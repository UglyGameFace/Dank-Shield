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
from .public_setup_group import _require_setup_permission, dank_group
from ..interaction_guard import safe_defer_interaction, safe_send_interaction

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
    view = ProtectionCenterView(author_id=int(interaction.user.id), cfg=cfg, spam=spam)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content or "Updated Protection Center.", embed=embed, view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.edit_message(content=content or "Protection Center refreshed.", embed=embed, view=view)
    except Exception:
        await _send_ephemeral(interaction, content or "Protection Center refreshed.", embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())


SPAM_MODE_LABELS = {
    "log_only": "Alert Only",
    "delete_only": "Delete Messages",
    "timeout": "Timeout User",
    "quarantine": "Quarantine + Restore",
    "kick": "Kick User",
    "ban": "Ban User",
}


def _normalize_spam_mode_for_ui(value: Any) -> str:
    text = str(value or "timeout").strip().lower()
    return text if text in SPAM_MODE_LABELS else "timeout"


def _protection_state(cfg: Any, spam: dict[str, Any]) -> str:
    """Return off/safe/strict/custom for live button labels."""

    try:
        automod_on = _cfg_bool(cfg, "automod_enabled", False)
        invites_on = _cfg_bool(cfg, "automod_block_invites", False)
        links_on = _cfg_bool(cfg, "automod_block_links", False)
        spam_on = bool((spam or {}).get("enabled"))
        preset = str(_cfg_value(cfg, "automod_preset", "custom") or "custom").strip().lower()

        if not automod_on and not invites_on and not links_on and not spam_on:
            return "off"

        if spam_on and invites_on and links_on and preset == "strict":
            return "strict"

        if spam_on and invites_on and not links_on and preset == "safe":
            return "safe"

        # Backward-compatible detection if the saved preset label is stale.
        if spam_on and invites_on and links_on:
            try:
                if int((spam or {}).get("message_threshold", 99) or 99) <= int(SPAM_PRESETS["strict"]["message_threshold"]):
                    return "strict"
            except Exception:
                return "strict"

        if spam_on and invites_on and not links_on:
            return "safe"

        return "custom"
    except Exception:
        return "custom"


def _decorate_quick_mode_buttons(view: discord.ui.View, cfg: Any, spam: dict[str, Any]) -> None:
    state = _protection_state(cfg, spam or {})
    for child in list(getattr(view, "children", []) or []):
        custom_id = str(getattr(child, "custom_id", "") or "")

        if custom_id == "dank_protection:safe":
            child.label = f"Safe Defaults: {'ON' if state == 'safe' else 'OFF'}"
            child.style = discord.ButtonStyle.success if state == "safe" else discord.ButtonStyle.secondary
            child.emoji = "🟢"

        elif custom_id == "dank_protection:strict":
            child.label = f"Strict Mode: {'ON' if state == 'strict' else 'OFF'}"
            child.style = discord.ButtonStyle.success if state == "strict" else discord.ButtonStyle.secondary
            child.emoji = "🔒"

        elif custom_id == "dank_protection:off":
            child.label = "Protection: OFF" if state == "off" else "Turn Off"
            child.style = discord.ButtonStyle.success if state == "off" else discord.ButtonStyle.danger
            child.emoji = "⏸️"

        elif custom_id == "dank_protection:edit_spamguard":
            child.label = "Spam Guard Actions"
            child.emoji = "🛡️"




async def _apply_protection_preset(interaction: discord.Interaction, preset: str) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send_ephemeral(interaction, "❌ This must be used inside a server.")

    preset = str(preset or "safe").strip().lower()
    if preset not in AUTOMOD_PRESETS or preset not in SPAM_PRESETS:
        preset = "safe"

    member = interaction.user if isinstance(interaction.user, discord.Member) else None

    current_cfg = await get_guild_config(int(guild.id), refresh=True)
    current_spam, _ = await _load_spam_settings(int(guild.id))
    current_state = _protection_state(current_cfg, current_spam)

    # Make Safe / Strict behave like real live toggles.
    target = "off" if preset in {"safe", "strict"} and current_state == preset else preset

    automod_updates = dict(AUTOMOD_PRESETS[target])
    automod_updates["automod_updated_by_id"] = str(int(interaction.user.id))
    await _save_automod(int(guild.id), automod_updates)

    spam_settings, persisted = await _save_spam_settings(int(guild.id), dict(SPAM_PRESETS[target]), member)

    if target == "off":
        note = "✅ Protection turned **OFF**."
        if preset in {"safe", "strict"} and current_state == preset:
            note = f"✅ **{preset.title()}** was already ON, so I turned protection **OFF**."
    else:
        note = f"✅ Protection preset set to **{target.title()}**."

    note += f" Spam Guard saving: {'DB-backed' if persisted else 'runtime/fallback'}; mode `{spam_settings.get('mode', 'unknown')}`."
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


async def _toggle_link_shield(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send_ephemeral(interaction, "❌ This must be used inside a server.")

    cfg = await get_guild_config(int(guild.id), refresh=True)
    links_on = _cfg_bool(cfg, "automod_block_links", False)
    invites_on = _cfg_bool(cfg, "automod_block_invites", False)

    if links_on:
        updates = {
            "automod_enabled": bool(invites_on),
            "automod_block_invites": bool(invites_on),
            "automod_block_links": False,
            "automod_link_policy": "invite_shield" if invites_on else "allow_links",
            "automod_updated_by_id": str(int(interaction.user.id)),
        }
        label = "Link Shield disabled. Normal links are allowed again."
    else:
        updates = {
            "automod_enabled": True,
            "automod_block_invites": True,
            "automod_block_links": True,
            "automod_link_policy": "link_lockdown",
            "automod_updated_by_id": str(int(interaction.user.id)),
        }
        label = "Link Shield enabled. All external links and Discord invites are blocked."

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
        self.current_mode = _normalize_spam_mode_for_ui(spam.get("mode", "timeout"))
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
            "mode": self.current_mode,
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


async def _set_spam_response_mode(interaction: discord.Interaction, mode: str) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send_ephemeral(interaction, "❌ This must be used inside a server.")

    clean_mode = _normalize_spam_mode_for_ui(mode)
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    settings, persisted = await _save_spam_settings(
        int(guild.id),
        {"enabled": True, "mode": clean_mode},
        member,
    )

    await _refresh_panel(
        interaction,
        content=(
            f"✅ Spam Guard response set to **{SPAM_MODE_LABELS[clean_mode]}** "
            f"(`{settings.get('mode', clean_mode)}`). Saving: {'DB-backed' if persisted else 'runtime/fallback'}."
        ),
    )


class SpamResponseModeSelect(discord.ui.Select):
    def __init__(self, current_mode: str):
        clean = _normalize_spam_mode_for_ui(current_mode)
        options = [
            discord.SelectOption(label="Alert Only", value="log_only", description="Log the incident without punishment.", default=clean == "log_only"),
            discord.SelectOption(label="Delete Messages", value="delete_only", description="Delete matching spam messages only.", default=clean == "delete_only"),
            discord.SelectOption(label="Timeout User", value="timeout", description="Delete matching spam and timeout the user.", default=clean == "timeout"),
            discord.SelectOption(label="Quarantine + Restore", value="quarantine", description="Move to quarantine and show staff restore.", default=clean == "quarantine"),
            discord.SelectOption(label="Kick User", value="kick", description="Delete matching spam and kick the user.", default=clean == "kick"),
            discord.SelectOption(label="Ban User", value="ban", description="Delete matching spam and ban the user.", default=clean == "ban"),
        ]
        super().__init__(
            placeholder=f"Response action: {SPAM_MODE_LABELS.get(clean, 'Timeout User')}",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="dank_protection:spam_response_mode",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await _set_spam_response_mode(interaction, self.values[0] if self.values else "timeout")


class SpamGuardActionSettingsModal(discord.ui.Modal, title="Spam Guard Actions"):
    def __init__(self, spam: dict[str, Any]) -> None:
        super().__init__(timeout=300)

        self.timeout_minutes = discord.ui.TextInput(
            label="Timeout minutes",
            default=str(spam.get("timeout_minutes", 30)),
            min_length=1,
            max_length=5,
            required=True,
        )
        self.delete_history = discord.ui.TextInput(
            label="Matching messages to delete",
            default=str(spam.get("delete_history", 8)),
            min_length=1,
            max_length=3,
            required=True,
        )
        self.cooldown_seconds = discord.ui.TextInput(
            label="Repeat action cooldown seconds",
            default=str(spam.get("cooldown_seconds", 20)),
            min_length=1,
            max_length=4,
            required=True,
        )
        self.quarantine_role_id = discord.ui.TextInput(
            label="Quarantine role ID, optional",
            default=str(spam.get("quarantine_role_id", "")),
            required=False,
            max_length=25,
        )
        self.allowed_invite_codes = discord.ui.TextInput(
            label="Allowed invite codes, optional",
            default=", ".join(list(spam.get("allowed_invite_codes") or [])),
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )

        for item in (
            self.timeout_minutes,
            self.delete_history,
            self.cooldown_seconds,
            self.quarantine_role_id,
            self.allowed_invite_codes,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _send_ephemeral(interaction, "❌ This must be used inside a server.")

        role_id = str(self.quarantine_role_id.value or "").strip()
        if role_id and not role_id.isdigit():
            return await _send_ephemeral(interaction, "❌ Quarantine role ID must be blank or a numeric role ID.")

        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        patch = {
            "timeout_minutes": _bounded_int(self.timeout_minutes.value, default=30, minimum=1, maximum=10080),
            "delete_history": _bounded_int(self.delete_history.value, default=8, minimum=1, maximum=30),
            "cooldown_seconds": _bounded_int(self.cooldown_seconds.value, default=20, minimum=5, maximum=300),
            "quarantine_role_id": role_id,
            "allowed_invite_codes": _parse_csvish_codes(str(self.allowed_invite_codes.value or "")),
        }

        settings, persisted = await _save_spam_settings(int(guild.id), patch, member)
        await _refresh_panel(
            interaction,
            content=(
                "✅ Spam Guard action settings updated. "
                f"Timeout `{settings.get('timeout_minutes')}`m, delete `{settings.get('delete_history')}`, cooldown `{settings.get('cooldown_seconds')}`s. "
                f"Saving: {'DB-backed' if persisted else 'runtime/fallback'}."
            ),
        )


class SpamGuardDetectionButton(discord.ui.Button):
    def __init__(self, spam: dict[str, Any]):
        super().__init__(
            label="Edit Detection Numbers",
            emoji="📊",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_protection:spam_edit_detection",
            row=1,
        )
        self.spam = dict(spam or {})

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_setup_permission(interaction):
            return
        await interaction.response.send_modal(SpamGuardSettingsModal(self.spam))


class SpamGuardActionsButton(discord.ui.Button):
    def __init__(self, spam: dict[str, Any]):
        super().__init__(
            label="Edit Action Settings",
            emoji="⚖️",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_protection:spam_edit_actions",
            row=1,
        )
        self.spam = dict(spam or {})

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_setup_permission(interaction):
            return
        await interaction.response.send_modal(SpamGuardActionSettingsModal(self.spam))


class SpamGuardEditorView(discord.ui.View):
    def __init__(self, *, author_id: int, spam: dict[str, Any]):
        super().__init__(timeout=300)
        self.author_id = int(author_id)
        self.spam = dict(spam or {})
        self.add_item(SpamResponseModeSelect(_normalize_spam_mode_for_ui(self.spam.get("mode", "timeout"))))
        self.add_item(SpamGuardDetectionButton(self.spam))
        self.add_item(SpamGuardActionsButton(self.spam))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != int(self.author_id):
            await interaction.response.send_message("Open your own Protection Center so settings stay clear.", ephemeral=True)
            return False
        return True



async def _open_spamguard_editor(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send_ephemeral(interaction, "❌ This must be used inside a server.")

    spam, spam_source = await _load_spam_settings(int(guild.id))
    mode = _normalize_spam_mode_for_ui(spam.get("mode", "timeout"))

    embed = discord.Embed(
        title="🛡️ Spam Guard Actions",
        description=(
            "Choose what happens after Dank Shield detects behavior spam, invite floods, or likely hacked-account activity.\n\n"
            "**Detection Numbers** = message speed, duplicate threshold, invite threshold.\n"
            "**Action Settings** = timeout length, cleanup amount, quarantine role, allowed invite codes."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Current response",
        value=f"**Action:** {SPAM_MODE_LABELS.get(mode, mode)} (`{mode}`)\n**Saving:** `{spam_source}`",
        inline=False,
    )
    embed.add_field(
        name="Current numbers",
        value=(
            f"Window `{spam.get('window_seconds', '—')}s` • Messages `{spam.get('message_threshold', '—')}` • "
            f"Duplicates `{spam.get('duplicate_threshold', '—')}`\n"
            f"Invite threshold `{spam.get('invite_threshold', '—')}` • Timeout `{spam.get('timeout_minutes', '—')}m` • "
            f"Delete `{spam.get('delete_history', '—')}`"
        ),
        inline=False,
    )

    await _send_ephemeral(
        interaction,
        embed=embed,
        view=SpamGuardEditorView(author_id=int(interaction.user.id), spam=spam),
        allowed_mentions=discord.AllowedMentions.none(),
    )


class AddFilterModal(discord.ui.Modal, title="Add Automod Filter"):
    word = discord.ui.TextInput(label="Word or phrase to block", max_length=80, required=True, placeholder="Example: scam phrase")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _add_bad_word(interaction, str(self.word.value))


class TestFilterModal(discord.ui.Modal, title="Test Protection Filter"):
    text = discord.ui.TextInput(label="Text to test privately", style=discord.TextStyle.paragraph, max_length=700, required=True)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _test_text(interaction, str(self.text.value))


class ProtectionCenterView(discord.ui.View):
    def __init__(self, *, author_id: int, cfg: Any | None = None, spam: dict[str, Any] | None = None) -> None:
        super().__init__(timeout=900)
        self.author_id = int(author_id)
        self.cfg = cfg
        self.spam = dict(spam or {})
        _decorate_quick_mode_buttons(self, cfg, self.spam)

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

    @discord.ui.button(label="Link Shield", emoji="🔗", style=discord.ButtonStyle.secondary, custom_id="dank_protection:block_links", row=1)
    async def block_links_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _toggle_link_shield(interaction)

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


@dank_group.command(name="protection", description="Open the unified Automod + Spam Guard protection center.")
async def protection_center(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return

    await safe_defer_interaction(interaction, ephemeral=True)

    try:
        guild = interaction.guild
        if guild is None:
            await safe_send_interaction(
                interaction,
                content="❌ Protection Center must be opened inside a server.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        cfg = await get_guild_config(int(guild.id), refresh=True)
        spam, spam_source = await _load_spam_settings(int(guild.id))
        embed = _protection_embed(guild, cfg, spam, spam_source)
        view = ProtectionCenterView(author_id=int(interaction.user.id), cfg=cfg, spam=spam)

        sent = await safe_send_interaction(
            interaction,
            embed=embed,
            view=view,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        if not sent:
            try:
                print(f"⚠️ public_protection_center: failed to send Protection Center guild={getattr(guild, 'id', 'unknown')}")
            except Exception:
                pass

    except Exception as exc:
        try:
            print(
                "⚠️ public_protection_center open failed "
                f"guild={getattr(interaction.guild, 'id', 'unknown')}: {type(exc).__name__}: {exc}"
            )
        except Exception:
            pass

        await safe_send_interaction(
            interaction,
            content=(
                "❌ Protection Center could not open safely. Nothing was changed. "
                "Try again, then check the bot logs or run diagnostics if it keeps happening."
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


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
