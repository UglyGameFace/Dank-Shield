from __future__ import annotations

"""Make Protection Center invite scope easier to read.

This guard sits on top of the existing invite controls and adds a plain-language
snapshot showing which bots/users and channels are currently covered. It also
renames the buttons so staff know which one edits scope vs allow-list behavior.
"""

from typing import Any

import discord

_PATCHED = False
_ORIGINAL_EMBED: Any = None
_ORIGINAL_VIEW_INIT: Any = None


def _short(value: Any, limit: int = 80) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _helpers() -> tuple[Any, Any, Any]:
    from stoney_verify import spam_guard
    from stoney_verify.commands_ext import public_protection_center as center
    from stoney_verify.startup_guards import spam_guard_invite_override_options as policy

    try:
        policy.apply()
    except Exception:
        pass
    return center, spam_guard, policy


def _bot_label(guild: discord.Guild, raw_id: str) -> str:
    try:
        member = guild.get_member(int(raw_id))
        if isinstance(member, discord.Member):
            prefix = "🤖" if getattr(member, "bot", False) else "👤"
            return f"{prefix} {_short(member.display_name, 40)} (`{member.id}`)"
    except Exception:
        pass
    return f"`{raw_id}`"


def _channel_label(guild: discord.Guild, raw_id: str) -> str:
    try:
        channel = guild.get_channel(int(raw_id))
        if channel is None:
            getter = getattr(guild, "get_thread", None)
            if callable(getter):
                channel = getter(int(raw_id))
        if channel is not None:
            mention = getattr(channel, "mention", None)
            name = str(mention) if mention else f"#{getattr(channel, 'name', raw_id)}"
            return f"{name} (`{getattr(channel, 'id', raw_id)}`)"
    except Exception:
        pass
    return f"`{raw_id}`"


def _lines_with_more(lines: list[str], *, limit: int = 6) -> str:
    if not lines:
        return "—"
    shown = lines[:limit]
    if len(lines) > limit:
        shown.append(f"…and `{len(lines) - limit}` more")
    return "\n".join(shown)


def _snapshot(policy: Any, guild: discord.Guild, spam: dict[str, Any]) -> str:
    all_bots = policy._safe_bool(spam.get("invite_hard_block_target_all_bots"), False)
    bot_ids = policy._parse_ids(spam.get("invite_hard_block_target_bot_ids"))
    channel_ids = policy._parse_ids(spam.get("invite_hard_block_target_channel_ids"))

    if all_bots:
        bot_text = "✅ **All bots are covered.**"
        if bot_ids:
            bot_text += "\nAlso listed manually:\n" + _lines_with_more([_bot_label(guild, x) for x in bot_ids])
    elif bot_ids:
        bot_text = "✅ **Only these bots/users are covered:**\n" + _lines_with_more([_bot_label(guild, x) for x in bot_ids])
    else:
        bot_text = "⚠️ **Bots are not covered yet.** Human posts are watched; bot posts are ignored unless you enable All Bots or add bot IDs."

    if channel_ids:
        channel_text = "✅ **Only these channels are covered:**\n" + _lines_with_more([_channel_label(guild, x) for x in channel_ids])
    else:
        channel_text = "✅ **All message channels Dank Shield can monitor are covered.**"

    return (
        "**Bots / users**\n"
        + bot_text
        + "\n\n**Channels**\n"
        + channel_text
        + "\n\nUse **Protected Bots/Channels** to edit this. Use **Override Allow-Lists** when a normal allow-list should be ignored."
    )[:1024]


def _replace_invite_fields(embed: discord.Embed, *, value: str) -> discord.Embed:
    try:
        fields = list(embed.fields)
        embed.clear_fields()
        inserted = False
        for field in fields:
            name = str(getattr(field, "name", "") or "")
            if name.lower() in {"invite & link controls", "invite protection", "invite protection snapshot"}:
                if not inserted:
                    embed.add_field(name="Invite Protection Snapshot", value=value, inline=False)
                    inserted = True
                continue
            embed.add_field(name=field.name, value=field.value, inline=field.inline)
        if not inserted:
            embed.add_field(name="Invite Protection Snapshot", value=value, inline=False)
    except Exception:
        try:
            embed.add_field(name="Invite Protection Snapshot", value=value, inline=False)
        except Exception:
            pass
    return embed


def apply() -> bool:
    global _PATCHED, _ORIGINAL_EMBED, _ORIGINAL_VIEW_INIT
    if _PATCHED:
        return True
    try:
        center, _spam_guard, policy = _helpers()
        _ORIGINAL_EMBED = center._protection_embed
        _ORIGINAL_VIEW_INIT = center.ProtectionCenterView.__init__

        def patched_embed(guild: discord.Guild, cfg: Any, spam: dict[str, Any], spam_source: str) -> discord.Embed:
            embed = _ORIGINAL_EMBED(guild, cfg, spam, spam_source)
            return _replace_invite_fields(embed, value=_snapshot(policy, guild, spam))

        def patched_init(self, *, author_id: int) -> None:
            _ORIGINAL_VIEW_INIT(self, author_id=author_id)
            for child in list(getattr(self, "children", []) or []):
                custom_id = str(getattr(child, "custom_id", "") or "")
                if custom_id == "dank_protection:invite_scope":
                    child.label = "Protected Bots/Channels"
                    child.emoji = "🛡️"
                    child.style = discord.ButtonStyle.primary
                elif custom_id == "dank_protection:invite_override":
                    child.label = "Override Allow-Lists"
                    child.emoji = "⚙️"
                    child.style = discord.ButtonStyle.secondary

        center._protection_embed = patched_embed
        center.ProtectionCenterView.__init__ = patched_init
        _PATCHED = True
        print("✅ protection_center_invite_status_guard active; invite protection status is easier to read")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ protection_center_invite_status_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]