from __future__ import annotations

"""Unified public protection center.

This is the production-facing safety surface. It intentionally keeps Automod
(content filtering) and Spam Guard (behavior/rate protection) as separate engines
under the hood, while exposing one simple /dank protection command to guild
owners.
"""

import re
import unicodedata
from typing import Any, Mapping

import discord
from discord import app_commands

from ..guild_config import get_guild_config, invalidate_guild_config, upsert_guild_config
from .public_setup_group import _require_setup_permission, stoney_group

_ATTACHED = False

ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
SPACE_RE = re.compile(r"\s+")

LEET_MAP = str.maketrans(
    {
        "@": "a",
        "4": "a",
        "0": "o",
        "1": "i",
        "!": "i",
        "|": "i",
        "3": "e",
        "5": "s",
        "$": "s",
        "7": "t",
        "+": "t",
        "8": "b",
        "9": "g",
        "6": "g",
        "а": "a",
        "е": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "х": "x",
        "у": "y",
        "к": "k",
        "м": "m",
        "н": "h",
        "т": "t",
    }
)

AUTOMOD_PRESETS: dict[str, dict[str, Any]] = {
    "off": {
        "automod_enabled": False,
        "automod_block_invites": False,
        "automod_block_links": False,
        "automod_max_mentions": 0,
        "automod_caps_ratio": 0,
        "automod_max_custom_emojis": 0,
        "automod_link_policy": "allow_links",
        "automod_preset": "off",
    },
    "safe": {
        "automod_enabled": True,
        "automod_block_invites": True,
        "automod_block_links": False,
        "automod_max_mentions": 8,
        "automod_caps_ratio": 0.85,
        "automod_max_custom_emojis": 14,
        "automod_link_policy": "invite_shield",
        "automod_preset": "safe",
    },
    "strict": {
        "automod_enabled": True,
        "automod_block_invites": True,
        "automod_block_links": True,
        "automod_max_mentions": 5,
        "automod_caps_ratio": 0.75,
        "automod_max_custom_emojis": 10,
        "automod_link_policy": "link_lockdown",
        "automod_preset": "strict",
    },
}

SPAM_PRESETS: dict[str, dict[str, Any]] = {
    "off": {"enabled": False},
    "safe": {
        "enabled": True,
        "mode": "timeout",
        "apply_to_verified_users": True,
        "block_external_invites_only": True,
        "allow_server_invites": True,
        "window_seconds": 12,
        "message_threshold": 5,
        "duplicate_threshold": 3,
        "invite_threshold": 2,
        "multi_invite_immediate": 2,
        "delete_history": 8,
        "timeout_minutes": 30,
        "cooldown_seconds": 20,
    },
    "strict": {
        "enabled": True,
        "mode": "timeout",
        "apply_to_verified_users": True,
        "block_external_invites_only": True,
        "allow_server_invites": True,
        "window_seconds": 10,
        "message_threshold": 4,
        "duplicate_threshold": 2,
        "invite_threshold": 1,
        "multi_invite_immediate": 2,
        "delete_history": 12,
        "timeout_minutes": 60,
        "cooldown_seconds": 30,
    },
}


def _clean_filter_item(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = ZERO_WIDTH_RE.sub("", text)
    text = text.replace(",", " ")
    text = SPACE_RE.sub(" ", text).strip().casefold()
    return text


def _csv_items(value: Any) -> list[str]:
    raw = str(value or "")
    out: list[str] = []
    for chunk in raw.replace("\n", ",").split(","):
        item = _clean_filter_item(chunk)
        if item and item not in out:
            out.append(item)
    return out


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    try:
        for bucket in ("settings", "config", "metadata", "meta"):
            nested = getattr(cfg, bucket, None)
            if isinstance(nested, Mapping) and nested.get(key) is not None:
                return nested.get(key)
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            for bucket in ("settings", "config", "metadata", "meta"):
                nested = cfg.get(bucket)
                if isinstance(nested, Mapping) and nested.get(key) is not None:
                    return nested.get(key)
    except Exception:
        pass
    return default


def _cfg_bool(cfg: Any, key: str, default: bool = False) -> bool:
    raw = _cfg_value(cfg, key, default)
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}


def _cfg_int(cfg: Any, key: str, default: int = 0) -> int:
    try:
        raw = _cfg_value(cfg, key, default)
        if raw is None or isinstance(raw, bool):
            return int(default)
        return int(float(str(raw).strip()))
    except Exception:
        return int(default)


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(float(str(value).strip()))
    except Exception:
        parsed = int(default)
    return max(int(minimum), min(int(maximum), parsed))


def _normalize_for_filter(value: Any, *, compact: bool) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = ZERO_WIDTH_RE.sub("", text).translate(LEET_MAP)
    out: list[str] = []
    last_space = False
    for ch in text:
        if ch.isalnum():
            out.append(ch)
            last_space = False
        elif not compact and not last_space:
            out.append(" ")
            last_space = True
    normalized = "".join(out).strip()
    return normalized if not compact else normalized.replace(" ", "")


def _bad_word_hit(content: str, bad_words: list[str]) -> str | None:
    raw_lower = str(content or "").casefold()
    normalized_spaced = _normalize_for_filter(content, compact=False)
    normalized_compact = _normalize_for_filter(content, compact=True)
    for word in bad_words:
        token = _clean_filter_item(word)
        if len(token) < 2:
            continue
        token_spaced = _normalize_for_filter(token, compact=False)
        token_compact = _normalize_for_filter(token, compact=True)
        if not token_compact:
            continue
        if " " in token:
            if token in raw_lower or token_spaced in normalized_spaced or token_compact in normalized_compact:
                return token
            continue
        if len(token_compact) <= 2:
            try:
                if re.search(r"(?<!\w)" + re.escape(token_compact) + r"(?!\w)", normalized_spaced):
                    return token
            except Exception:
                pass
            continue
        if token_compact in normalized_compact:
            return token
    return None


async def _load_spam_settings(guild_id: int) -> tuple[dict[str, Any], str]:
    try:
        from stoney_verify.spam_guard import get_spam_settings

        settings = await get_spam_settings(int(guild_id))
        return dict(settings or {}), "loaded"
    except Exception as exc:
        return {"enabled": False, "mode": "unknown"}, f"unavailable:{type(exc).__name__}"


async def _save_spam_settings(guild_id: int, patch: dict[str, Any], member: discord.Member | None) -> tuple[dict[str, Any], bool]:
    try:
        from stoney_verify.spam_guard import save_spam_settings

        settings, persisted = await save_spam_settings(int(guild_id), dict(patch), updated_by=member)
        return dict(settings or {}), bool(persisted)
    except Exception:
        return dict(patch), False


async def _save_automod(guild_id: int, updates: dict[str, Any]) -> Any:
    saved = await upsert_guild_config(int(guild_id), dict(updates))
    invalidate_guild_config(int(guild_id))
    return saved


async def _send_ephemeral(interaction: discord.Interaction, content: str = "", **kwargs: Any) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(content, ephemeral=True, **kwargs)
        else:
            await interaction.followup.send(content, ephemeral=True, **kwargs)
    except Exception:
        pass


def _link_policy_label(cfg: Any) -> str:
    block_links = _cfg_bool(cfg, "automod_block_links", False)
    block_invites = _cfg_bool(cfg, "automod_block_invites", False)
    if block_links:
        return "🔒 Link Lockdown — blocks all external links and Discord invites"
    if block_invites:
        return "🛡️ Invite Shield — blocks Discord invite links to other/bad servers"
    return "⚪ Links allowed — no invite/link blocking"


def _protection_embed(guild: discord.Guild, cfg: Any, spam: dict[str, Any], spam_source: str) -> discord.Embed:
    bad_words = _csv_items(_cfg_value(cfg, "automod_bad_words", ""))
    automod_on = _cfg_bool(cfg, "automod_enabled", False)
    spam_on = bool(spam.get("enabled"))
    both_on = automod_on and spam_on
    embed = discord.Embed(
        title="🛡️ Dank Shield Protection Center",
        description=(
            "One product surface. Two engines underneath:\n"
            "**Automod** filters message content and links. **Spam Guard** watches behavior, rate, duplicate messages, and invite floods."
        ),
        color=discord.Color.green() if both_on else discord.Color.gold() if (automod_on or spam_on) else discord.Color.dark_grey(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Quick recommendation",
        value=(
            "Use **Safe** for normal public servers. It blocks Discord invite spam while allowing normal links.\n"
            "Use **Block All Links** during raids, raids-after-ping, or if members should not post URLs at all."
        ),
        inline=False,
    )
    embed.add_field(
        name="Link Shield — bad server spam",
        value=(
            f"**Policy:** {_link_policy_label(cfg)}\n"
            f"**Discord invites:** {'blocked' if _cfg_bool(cfg, 'automod_block_invites', False) else 'allowed'}\n"
            f"**All external links:** {'blocked' if _cfg_bool(cfg, 'automod_block_links', False) else 'allowed'}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Automod — content filters",
        value=(
            f"**Enabled:** {'✅ Yes' if automod_on else '⚪ No'}\n"
            f"**Preset:** `{_cfg_value(cfg, 'automod_preset', 'custom') or 'custom'}`\n"
            f"**Bad-word filters:** `{len(bad_words)}`\n"
            f"**Max mentions:** `{_cfg_int(cfg, 'automod_max_mentions', 0) or 'off'}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Spam Guard — behavior protection",
        value=(
            f"**Enabled:** {'✅ Yes' if spam_on else '⚪ No'}\n"
            f"**Mode:** `{spam.get('mode', 'unknown')}`\n"
            f"**Window:** `{spam.get('window_seconds', '—')}s` • **Messages:** `{spam.get('message_threshold', '—')}` • "
            f"**Duplicates:** `{spam.get('duplicate_threshold', '—')}`\n"
            f"**Invite threshold:** `{spam.get('invite_threshold', '—')}` • **Timeout:** `{spam.get('timeout_minutes', '—')}m` • **Saving:** `{spam_source}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="What buttons do",
        value=(
            "**Edit Spam Guard** = message speed, duplicate messages, invite-flood threshold, timeout length.\n"
            "**Block Invites** = stop Discord server invite links.\n"
            "**Block All Links** = stop every URL.\n"
            "**Add Filter/Test** = banned words and bypass tests."
        ),
        inline=False,
    )
    embed.set_footer(text="Protection Center uses existing Automod + Spam Guard settings; no new overlapping config bucket.")
    return embed


async def _refresh_panel(interaction: discord.Interaction, *, content: str | None = None) -> None:
    guild = interaction.guild
    if guild is None:
        return await _send_ephemeral(interaction, "❌ This must be used inside a server.")
    cfg = await get_guild_config(int(guild.id), refresh=True)
    spam, spam_source = await _load_spam_settings(int(guild.id))
    embed = _protection_embed(guild, cfg, spam, spam_source)
    view = ProtectionCenterView(author_id=int(interaction.user.id))
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content or "Updated Protection Center.", embed=embed, view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.edit_message(content=content or "Protection Center refreshed.", embed=embed, view=view)
    except Exception:
        await _send_ephemeral(interaction, content or "Protection Center refreshed.", embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())


async def _apply_protection_preset(interaction: discord.Interaction, preset: str) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send_ephemeral(interaction, "❌ This must be used inside a server.")
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    automod_updates = dict(AUTOMOD_PRESETS[preset])
    automod_updates["automod_updated_by_id"] = str(int(interaction.user.id))
    await _save_automod(int(guild.id), automod_updates)
    spam_settings, persisted = await _save_spam_settings(int(guild.id), dict(SPAM_PRESETS[preset]), member)
    label = "Off" if preset == "off" else preset.title()
    note = f"✅ Protection preset set to **{label}**. Spam Guard saving: {'DB-backed' if persisted else 'runtime/fallback'}; mode `{spam_settings.get('mode', 'unknown')}`."
    await _refresh_panel(interaction, content=note)


async def _set_link_policy(interaction: discord.Interaction, policy: str) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send_ephemeral(interaction, "❌ This must be used inside a server.")
    if policy == "invite_shield":
        updates = {"automod_enabled": True, "automod_block_invites": True, "automod_block_links": False, "automod_link_policy": "invite_shield", "automod_updated_by_id": str(int(interaction.user.id))}
        label = "Invite Shield enabled — Discord server invite links are blocked, normal links are allowed."
    elif policy == "link_lockdown":
        updates = {"automod_enabled": True, "automod_block_invites": True, "automod_block_links": True, "automod_link_policy": "link_lockdown", "automod_updated_by_id": str(int(interaction.user.id))}
        label = "Link Lockdown enabled — all external links and Discord invites are blocked."
    else:
        updates = {"automod_block_invites": False, "automod_block_links": False, "automod_link_policy": "allow_links", "automod_updated_by_id": str(int(interaction.user.id))}
        label = "Links allowed — invite/link blocking is off."
    await _save_automod(int(guild.id), updates)
    await _refresh_panel(interaction, content=f"✅ {label}")


async def _add_bad_word(interaction: discord.Interaction, word: str) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send_ephemeral(interaction, "❌ This must be used inside a server.")
    cleaned = _clean_filter_item(word)
    if len(cleaned) < 2 or len(cleaned) > 80:
        return await _send_ephemeral(interaction, "❌ Use a 2-80 character word or phrase. Commas and invisible characters are cleaned automatically.")
    cfg = await get_guild_config(int(guild.id), refresh=True)
    items = _csv_items(_cfg_value(cfg, "automod_bad_words", ""))
    existed = cleaned in [x.casefold() for x in items]
    if not existed:
        items.append(cleaned)
    updates = {"automod_enabled": True, "automod_bad_words": ",".join(items), "automod_updated_by_id": str(int(interaction.user.id))}
    await _save_automod(int(guild.id), updates)
    verify_cfg = await get_guild_config(int(guild.id), refresh=True)
    saved_items = _csv_items(_cfg_value(verify_cfg, "automod_bad_words", ""))
    if cleaned not in [x.casefold() for x in saved_items]:
        return await _send_ephemeral(interaction, "❌ I tried to save that filter, but it did not survive read-back. Do not rely on it yet; check the guild config table/schema.")
    await _refresh_panel(interaction, content=f"✅ `{cleaned}` {'already existed' if existed else 'added'}. Obfuscated variants are checked too.")


async def _test_text(interaction: discord.Interaction, text: str) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send_ephemeral(interaction, "❌ This must be used inside a server.")
    cfg = await get_guild_config(int(guild.id), refresh=True)
    bad_words = _csv_items(_cfg_value(cfg, "automod_bad_words", ""))
    hit = _bad_word_hit(text, bad_words)
    normalized = _normalize_for_filter(text, compact=True)
    if hit:
        return await _send_ephemeral(interaction, f"✅ Would block: **blocked word/phrase `{hit}`**\nNormalized check: `{normalized[:300]}`")
    await _send_ephemeral(interaction, f"⚪ Would not block with current bad-word filters.\nNormalized check: `{normalized[:300]}`")


class SpamGuardSettingsModal(discord.ui.Modal):
    def __init__(self, spam: dict[str, Any]) -> None:
        super().__init__(title="Edit Spam Guard")
        self.window_seconds = discord.ui.TextInput(label="Watch window seconds", default=str(spam.get("window_seconds", 12)), min_length=1, max_length=4, required=True)
        self.message_threshold = discord.ui.TextInput(label="Messages allowed in window", default=str(spam.get("message_threshold", 5)), min_length=1, max_length=3, required=True)
        self.duplicate_threshold = discord.ui.TextInput(label="Duplicate messages allowed", default=str(spam.get("duplicate_threshold", 3)), min_length=1, max_length=3, required=True)
        self.invite_threshold = discord.ui.TextInput(label="Invite links allowed in window", default=str(spam.get("invite_threshold", 2)), min_length=1, max_length=3, required=True)
        self.timeout_minutes = discord.ui.TextInput(label="Timeout minutes when triggered", default=str(spam.get("timeout_minutes", 30)), min_length=1, max_length=4, required=True)
        for item in (self.window_seconds, self.message_threshold, self.duplicate_threshold, self.invite_threshold, self.timeout_minutes):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _send_ephemeral(interaction, "❌ This must be used inside a server.")
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        patch = {
            "enabled": True,
            "mode": "timeout",
            "apply_to_verified_users": True,
            "block_external_invites_only": True,
            "allow_server_invites": True,
            "window_seconds": _bounded_int(self.window_seconds.value, default=12, minimum=5, maximum=60),
            "message_threshold": _bounded_int(self.message_threshold.value, default=5, minimum=2, maximum=20),
            "duplicate_threshold": _bounded_int(self.duplicate_threshold.value, default=3, minimum=2, maximum=10),
            "invite_threshold": _bounded_int(self.invite_threshold.value, default=2, minimum=1, maximum=10),
            "multi_invite_immediate": 2,
            "delete_history": 8,
            "timeout_minutes": _bounded_int(self.timeout_minutes.value, default=30, minimum=1, maximum=10080),
            "cooldown_seconds": 20,
        }
        spam_settings, persisted = await _save_spam_settings(int(guild.id), patch, member)
        await _refresh_panel(
            interaction,
            content=(
                "✅ Spam Guard updated. "
                f"Window `{spam_settings.get('window_seconds')}`s, messages `{spam_settings.get('message_threshold')}`, duplicates `{spam_settings.get('duplicate_threshold')}`, "
                f"invite threshold `{spam_settings.get('invite_threshold')}`, timeout `{spam_settings.get('timeout_minutes')}`m. Saving: {'DB-backed' if persisted else 'runtime/fallback'}."
            ),
        )


async def _open_spamguard_editor(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send_ephemeral(interaction, "❌ This must be used inside a server.")
    spam, _source = await _load_spam_settings(int(guild.id))
    await interaction.response.send_modal(SpamGuardSettingsModal(spam))


class AddFilterModal(discord.ui.Modal, title="Add Automod Filter"):
    word = discord.ui.TextInput(label="Word or phrase to block", max_length=80, required=True, placeholder="Example: scam phrase")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _add_bad_word(interaction, str(self.word.value))


class TestFilterModal(discord.ui.Modal, title="Test Protection Filter"):
    text = discord.ui.TextInput(label="Text to test privately", style=discord.TextStyle.paragraph, max_length=700, required=True)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _test_text(interaction, str(self.text.value))


class ProtectionCenterView(discord.ui.View):
    def __init__(self, *, author_id: int) -> None:
        super().__init__(timeout=900)
        self.author_id = int(author_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.author_id:
            await interaction.response.send_message("Open your own Protection Center with `/dank protection` so settings stay clear.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Safe", emoji="🟢", style=discord.ButtonStyle.success, custom_id="dank_protection:safe")
    async def safe_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _apply_protection_preset(interaction, "safe")

    @discord.ui.button(label="Strict", emoji="🔒", style=discord.ButtonStyle.primary, custom_id="dank_protection:strict")
    async def strict_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _apply_protection_preset(interaction, "strict")

    @discord.ui.button(label="Off", emoji="⏸️", style=discord.ButtonStyle.secondary, custom_id="dank_protection:off")
    async def off_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _apply_protection_preset(interaction, "off")

    @discord.ui.button(label="Edit Spam Guard", emoji="🛠️", style=discord.ButtonStyle.secondary, custom_id="dank_protection:edit_spamguard", row=1)
    async def edit_spamguard_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_spamguard_editor(interaction)

    @discord.ui.button(label="Block Invites", emoji="🛡️", style=discord.ButtonStyle.secondary, custom_id="dank_protection:block_invites", row=1)
    async def block_invites_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _set_link_policy(interaction, "invite_shield")

    @discord.ui.button(label="Block All Links", emoji="🚫", style=discord.ButtonStyle.secondary, custom_id="dank_protection:block_links", row=1)
    async def block_links_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _set_link_policy(interaction, "link_lockdown")

    @discord.ui.button(label="Add Filter", emoji="➕", style=discord.ButtonStyle.secondary, custom_id="dank_protection:add_filter", row=2)
    async def add_filter_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        await interaction.response.send_modal(AddFilterModal())

    @discord.ui.button(label="Test", emoji="🧪", style=discord.ButtonStyle.secondary, custom_id="dank_protection:test", row=2)
    async def test_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        await interaction.response.send_modal(TestFilterModal())

    @discord.ui.button(label="Allow Links", emoji="🔓", style=discord.ButtonStyle.secondary, custom_id="dank_protection:allow_links", row=2)
    async def allow_links_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _set_link_policy(interaction, "allow_links")

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="dank_protection:refresh", row=3)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        await _refresh_panel(interaction)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="dank_protection:close", row=3)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        for child in self.children:
            try:
                child.disabled = True
            except Exception:
                pass
        await interaction.response.edit_message(content="Closed Protection Center. Reopen it with `/dank protection`.", view=self)


@stoney_group.command(name="protection", description="Open the unified Automod + Spam Guard protection center.")
async def protection_center(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return
    cfg = await get_guild_config(int(guild.id), refresh=True)
    spam, spam_source = await _load_spam_settings(int(guild.id))
    embed = _protection_embed(guild, cfg, spam, spam_source)
    view = ProtectionCenterView(author_id=int(interaction.user.id))
    await _send_ephemeral(interaction, embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())


def register_public_protection_center_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    global _ATTACHED
    if _ATTACHED:
        return
    _ATTACHED = True
    try:
        print("✅ public_protection_center: attached /dank protection")
    except Exception:
        pass


__all__ = ["register_public_protection_center_commands"]
