from __future__ import annotations

import re
import unicodedata
from typing import Any

import discord
from discord import app_commands

from ..guild_config import get_guild_config, invalidate_guild_config, upsert_guild_config
from .public_setup_group import _require_setup_permission, _upsert_config, dank_group

_ATTACHED = False

ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
SPACE_RE = re.compile(r"\s+")

AUTOMOD_PRESETS: dict[str, dict[str, Any]] = {
    "off": {
        "automod_enabled": False,
        "automod_block_invites": False,
        "automod_block_links": False,
        "automod_max_mentions": 0,
        "automod_caps_ratio": 0,
        "automod_max_custom_emojis": 0,
    },
    "safe": {
        "automod_enabled": True,
        "automod_block_invites": True,
        "automod_block_links": False,
        "automod_max_mentions": 8,
        "automod_caps_ratio": 0.85,
        "automod_max_custom_emojis": 14,
    },
    "strict": {
        "automod_enabled": True,
        "automod_block_invites": True,
        "automod_block_links": True,
        "automod_max_mentions": 5,
        "automod_caps_ratio": 0.75,
        "automod_max_custom_emojis": 10,
    },
}


def _csv_items(value: Any) -> list[str]:
    raw = str(value or "")
    parts: list[str] = []
    for chunk in raw.replace("\n", ",").split(","):
        item = _clean_filter_item(chunk)
        if item and item not in parts:
            parts.append(item)
    return parts


def _clean_filter_item(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = ZERO_WIDTH_RE.sub("", text)
    text = text.replace(",", " ")
    text = SPACE_RE.sub(" ", text).strip().casefold()
    return text


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
    return default


def _cfg_bool(cfg: Any, key: str, default: bool = False) -> bool:
    try:
        raw = _cfg_value(cfg, key, default)
        if isinstance(raw, bool):
            return raw
        return str(raw or "").strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}
    except Exception:
        return bool(default)


def _cfg_int(cfg: Any, key: str, default: int = 0) -> int:
    try:
        raw = _cfg_value(cfg, key, default)
        if raw is None or isinstance(raw, bool):
            return int(default)
        return int(float(str(raw).strip()))
    except Exception:
        return int(default)


def _cfg_float(cfg: Any, key: str, default: float = 0.0) -> float:
    try:
        raw = _cfg_value(cfg, key, default)
        if raw is None or isinstance(raw, bool):
            return float(default)
        return float(str(raw).strip())
    except Exception:
        return float(default)


async def _save_config(guild_id: int, updates: dict[str, Any]) -> Any:
    """Save via the robust guild_config writer, then fall back to setup writer.

    The older setup writer is still kept as a fallback because some deployments
    may have a different guild config table shape during migration.
    """
    try:
        saved = await upsert_guild_config(int(guild_id), dict(updates))
        invalidate_guild_config(int(guild_id))
        return saved
    except Exception:
        saved = await _upsert_config(int(guild_id), dict(updates))
        invalidate_guild_config(int(guild_id))
        return saved


async def _send(interaction: discord.Interaction, content: str = "", **kwargs: Any) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(content, ephemeral=True, **kwargs)
        else:
            await interaction.followup.send(content, ephemeral=True, **kwargs)
    except Exception:
        pass


async def _defer(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass


automod_group = app_commands.Group(
    name="automod",
    description="Configure basic public-server automod filters.",
)


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
        "а": "a",  # Cyrillic lookalikes
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


def _would_block(cfg: Any, content: str) -> str | None:
    bad_words = _csv_items(_cfg_value(cfg, "automod_bad_words", ""))
    hit = _bad_word_hit(content, bad_words)
    if hit:
        return f"blocked word/phrase: {hit}"
    return None


async def _add_bad_word(interaction: discord.Interaction, word: str) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send(interaction, "❌ This command must be used inside a server.")
    cleaned = _clean_filter_item(word)
    if len(cleaned) < 2 or len(cleaned) > 80:
        return await _send(interaction, "❌ Use a 2-80 character word or phrase. Commas and zero-width characters are cleaned automatically.")
    await _defer(interaction)
    try:
        cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
        items = _csv_items(_cfg_value(cfg, "automod_bad_words", ""))
        existed = cleaned in [x.casefold() for x in items]
        if not existed:
            items.append(cleaned)
        updates = {
            "automod_bad_words": ",".join(items),
            "automod_enabled": True,
            "automod_updated_by_id": str(int(interaction.user.id)),
        }
        await _save_config(int(interaction.guild.id), updates)
        verify_cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
        verify_items = _csv_items(_cfg_value(verify_cfg, "automod_bad_words", ""))
        if cleaned not in [x.casefold() for x in verify_items]:
            return await interaction.followup.send(
                "❌ I tried to save that filter, but the saved config did not include it after refresh. Check the guild config table/schema before relying on automod.",
                ephemeral=True,
            )
        status = "already existed" if existed else "added"
        await interaction.followup.send(
            f"✅ `{cleaned}` {status} in automod filters. Automod is enabled. Obfuscated versions are checked too.",
            ephemeral=True,
        )
    except Exception as exc:
        await interaction.followup.send(f"❌ Could not save automod bad-word filter: `{type(exc).__name__}: {exc}`", ephemeral=True)


async def _remove_bad_word(interaction: discord.Interaction, word: str) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send(interaction, "❌ This command must be used inside a server.")
    cleaned = _clean_filter_item(word)
    await _defer(interaction)
    try:
        cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
        items = [x for x in _csv_items(_cfg_value(cfg, "automod_bad_words", "")) if x.casefold() != cleaned]
        await _save_config(int(interaction.guild.id), {"automod_bad_words": ",".join(items), "automod_updated_by_id": str(int(interaction.user.id))})
        await interaction.followup.send(f"✅ Removed `{cleaned}` from automod bad-word filters.", ephemeral=True)
    except Exception as exc:
        await interaction.followup.send(f"❌ Could not remove automod bad-word filter: `{type(exc).__name__}: {exc}`", ephemeral=True)


@automod_group.command(name="preset", description="Apply an automod preset without touching Spam Guard or Raid Guard.")
@app_commands.describe(preset="off, safe, or strict")
@app_commands.choices(
    preset=[
        app_commands.Choice(name="Off", value="off"),
        app_commands.Choice(name="Safe", value="safe"),
        app_commands.Choice(name="Strict", value="strict"),
    ]
)
async def automod_preset(interaction: discord.Interaction, preset: app_commands.Choice[str]) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send(interaction, "❌ This command must be used inside a server.")
    await _defer(interaction)
    updates = dict(AUTOMOD_PRESETS.get(str(preset.value), AUTOMOD_PRESETS["safe"]))
    updates["automod_updated_by_id"] = str(int(interaction.user.id))
    updates["automod_preset"] = str(preset.value)
    try:
        await _save_config(int(interaction.guild.id), updates)
        await interaction.followup.send(f"✅ Automod preset set to **{preset.name}**. Spam Guard/Raid Guard settings were not changed.", ephemeral=True)
    except Exception as exc:
        await interaction.followup.send(f"❌ Could not save automod preset: `{type(exc).__name__}: {exc}`", ephemeral=True)


@automod_group.command(name="enable", description="Enable automod with the current saved settings.")
async def automod_enable(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send(interaction, "❌ This command must be used inside a server.")
    await _defer(interaction)
    await _save_config(int(interaction.guild.id), {"automod_enabled": True, "automod_updated_by_id": str(int(interaction.user.id))})
    await interaction.followup.send("✅ Automod enabled.", ephemeral=True)


@automod_group.command(name="disable", description="Disable automod without changing saved filters.")
async def automod_disable(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send(interaction, "❌ This command must be used inside a server.")
    await _defer(interaction)
    await _save_config(int(interaction.guild.id), {"automod_enabled": False, "automod_updated_by_id": str(int(interaction.user.id))})
    await interaction.followup.send("✅ Automod disabled. Spam Guard/Raid Guard were not changed.", ephemeral=True)


@automod_group.command(name="badword-add", description="Add a word or phrase to this server's automod filter.")
@app_commands.describe(word="Word or phrase to block.")
async def automod_badword_add(interaction: discord.Interaction, word: str) -> None:
    await _add_bad_word(interaction, word)


@automod_group.command(name="filter-add", description="Alias: add a word or phrase to the automod filter.")
@app_commands.describe(word="Word or phrase to block.")
async def automod_filter_add(interaction: discord.Interaction, word: str) -> None:
    await _add_bad_word(interaction, word)


@automod_group.command(name="badword-remove", description="Remove a word or phrase from the automod filter.")
@app_commands.describe(word="Word or phrase to remove.")
async def automod_badword_remove(interaction: discord.Interaction, word: str) -> None:
    await _remove_bad_word(interaction, word)


@automod_group.command(name="filter-remove", description="Alias: remove a word or phrase from the automod filter.")
@app_commands.describe(word="Word or phrase to remove.")
async def automod_filter_remove(interaction: discord.Interaction, word: str) -> None:
    await _remove_bad_word(interaction, word)


@automod_group.command(name="ignore-channel", description="Toggle a channel ignored by automod.")
@app_commands.describe(channel="Channel to toggle as ignored.")
async def automod_ignore_channel(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send(interaction, "❌ This command must be used inside a server.")
    await _defer(interaction)
    cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
    ids = _csv_items(_cfg_value(cfg, "automod_ignored_channel_ids", ""))
    cid = str(int(channel.id))
    if cid in ids:
        ids.remove(cid)
        action = "no longer ignored"
    else:
        ids.append(cid)
        action = "ignored"
    await _save_config(int(interaction.guild.id), {"automod_ignored_channel_ids": ",".join(ids), "automod_updated_by_id": str(int(interaction.user.id))})
    await interaction.followup.send(f"✅ {channel.mention} is now **{action}** by automod.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@automod_group.command(name="test", description="Privately test whether saved bad-word filters would block text.")
@app_commands.describe(text="Text to test privately.")
async def automod_test(interaction: discord.Interaction, text: str) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send(interaction, "❌ This command must be used inside a server.")
    cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
    reason = _would_block(cfg, text)
    normalized = _normalize_for_filter(text, compact=True)
    if reason:
        return await _send(interaction, f"✅ Would block: **{reason}**\nNormalized check: `{normalized[:300]}`")
    return await _send(interaction, f"⚪ Would not block with current bad-word filters.\nNormalized check: `{normalized[:300]}`")


@automod_group.command(name="health", description="Show current automod settings and safety notes.")
async def automod_health(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _send(interaction, "❌ This command must be used inside a server.")
    await _defer(interaction)
    cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
    bad_words = _csv_items(_cfg_value(cfg, "automod_bad_words", ""))
    ignored = _csv_items(_cfg_value(cfg, "automod_ignored_channel_ids", ""))
    embed = discord.Embed(title="🛡️ Automod Health", color=discord.Color.green() if _cfg_bool(cfg, "automod_enabled") else discord.Color.dark_grey(), timestamp=discord.utils.utcnow())
    embed.add_field(name="Enabled", value="✅ Yes" if _cfg_bool(cfg, "automod_enabled") else "⚪ No", inline=True)
    embed.add_field(name="Preset", value=str(_cfg_value(cfg, "automod_preset", "custom") or "custom"), inline=True)
    embed.add_field(name="Bad-word filters", value=str(len(bad_words)), inline=True)
    embed.add_field(name="Invite links", value="Blocked" if _cfg_bool(cfg, "automod_block_invites") else "Allowed", inline=True)
    embed.add_field(name="External links", value="Blocked" if _cfg_bool(cfg, "automod_block_links") else "Allowed", inline=True)
    embed.add_field(name="Max mentions", value=str(_cfg_int(cfg, "automod_max_mentions", 0) or "off"), inline=True)
    embed.add_field(name="Caps ratio", value=str(_cfg_float(cfg, "automod_caps_ratio", 0.0) or "off"), inline=True)
    embed.add_field(name="Custom emoji limit", value=str(_cfg_int(cfg, "automod_max_custom_emojis", 0) or "off"), inline=True)
    embed.add_field(name="Ignored channels", value=str(len(ignored)), inline=True)
    embed.add_field(name="Anti-bypass", value="Bad-word checks normalize case, zero-width characters, separators, common leetspeak, and common lookalike characters.", inline=False)
    embed.add_field(name="Does not overlap", value="Automod only handles simple message filters. Spam Guard, Raid Guard, verification, tickets, and modlog keep their own settings.", inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


def _attach() -> bool:
    global _ATTACHED
    if _ATTACHED:
        return True
    try:
        if dank_group.get_command("automod") is not None:
            _ATTACHED = True
            return True
    except Exception:
        pass
    try:
        dank_group.add_command(automod_group)
        _ATTACHED = True
        return True
    except Exception as exc:
        try:
            print(f"⚠️ public_automod_group failed attaching /dank automod: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


def register_public_automod_group_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    if _attach():
        try:
            print("✅ public_automod_group: attached /dank automod commands")
        except Exception:
            pass


_attach()

__all__ = ["register_public_automod_group_commands", "automod_group", "_csv_items", "_cfg_bool", "_cfg_int", "_cfg_float", "_cfg_value", "_bad_word_hit"]
