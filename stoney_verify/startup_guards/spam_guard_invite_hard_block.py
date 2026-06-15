from __future__ import annotations

"""Immediate external Discord invite deletion for SpamGuard.

Invite links should not sit in public chat waiting for a burst threshold. This
guard deletes blocked Discord invite links immediately when SpamGuard is enabled.

It also supports strict scopes:
- target bot/user IDs: include specific bot accounts instead of ignoring all bots
- target channel IDs: enforce only in selected channels when configured
- override flags: bypass normal allow/exempt buckets when the owner wants lockdown
"""

import re
from typing import Any, Iterable, Set

import discord

try:
    from stoney_verify.globals import bot
except Exception:  # pragma: no cover
    bot = None  # type: ignore

_INSTALLED = False

INVITE_HARD_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord(?:app)?\.com/invite|discord\.gg)\s*/\s*([A-Za-z0-9-]+)",
    re.IGNORECASE,
)


def _log(message: str) -> None:
    try:
        print(f"🛡️ spam_guard_invite_hard_block {message}")
    except Exception:
        pass


def _safe_str(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return bool(default)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled"}:
            return False
    except Exception:
        pass
    return bool(default)


def _override_enabled(settings: dict[str, Any], key: str) -> bool:
    return _safe_bool(settings.get(key, settings.get(f"spam_{key}")), False)


def _first_setting(settings: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in settings and settings.get(key) is not None:
            return settings.get(key)
        spam_key = f"spam_{key}"
        if spam_key in settings and settings.get(spam_key) is not None:
            return settings.get(spam_key)
    return None


def _clean_invite_text(content: str) -> str:
    text = _safe_str(content)
    text = text.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "")
    text = text.replace("[.]", ".").replace("(.)", ".").replace(" . ", ".")
    text = re.sub(r"discord\s*\.\s*gg", "discord.gg", text, flags=re.IGNORECASE)
    text = re.sub(r"discord(?:app)?\s*\.\s*com\s*/\s*invite", "discord.com/invite", text, flags=re.IGNORECASE)
    return text


def _extract_codes(content: str) -> list[str]:
    text = _clean_invite_text(content)
    return list(dict.fromkeys(code.strip().lower() for code in INVITE_HARD_RE.findall(text) if code.strip()))


def _normalize_id_list(values: Any) -> set[str]:
    out: set[str] = set()
    try:
        if isinstance(values, str):
            raw_items = re.split(r"[\s,;]+", values)
        elif isinstance(values, Iterable) and not isinstance(values, (bytes, dict)):
            raw_items = list(values)
        else:
            raw_items = [values]
        for raw in raw_items:
            text = _safe_str(raw).strip("<@#!&>")
            if text.isdigit():
                out.add(text)
    except Exception:
        pass
    return out


def _normalize_codes(values: Any) -> set[str]:
    out: set[str] = set()
    try:
        source = values if isinstance(values, Iterable) and not isinstance(values, (str, bytes, dict)) else [values]
        for raw in source:
            text = _safe_str(raw).lower().strip("/")
            text = text.replace("https://discord.gg/", "").replace("http://discord.gg/", "")
            text = text.replace("https://discord.com/invite/", "").replace("http://discord.com/invite/", "")
            if text:
                out.add(text)
    except Exception:
        pass
    return out


def _member_has_any_role(member: discord.Member, role_ids: set[str]) -> bool:
    try:
        wanted = {int(x) for x in role_ids if str(x).isdigit()}
        return bool(wanted) and any(int(role.id) in wanted for role in member.roles)
    except Exception:
        return False


async def _own_invite_codes(guild: discord.Guild) -> Set[str]:
    try:
        from stoney_verify import spam_guard

        getter = getattr(spam_guard, "_fetch_guild_invite_codes", None)
        if callable(getter):
            return set(await getter(guild))
    except Exception:
        pass
    try:
        return {str(inv.code).lower() for inv in await guild.invites() if getattr(inv, "code", None)}
    except Exception:
        return set()


async def _modlog(guild: discord.Guild, message: discord.Message, codes: list[str], reason: str) -> None:
    try:
        from stoney_verify.modlog import send_mod_log  # type: ignore

        maybe = send_mod_log(
            guild,
            "🛡️ Invite Link Blocked",
            f"Deleted Discord invite link from {message.author.mention} in {message.channel.mention}.\nCodes: `{', '.join(codes[:8])}`\nReason: {reason}",
        )
        if hasattr(maybe, "__await__"):
            await maybe
        return
    except Exception:
        pass
    try:
        from stoney_verify import spam_guard

        embed = discord.Embed(
            title="🛡️ Invite Link Blocked",
            description=f"Deleted Discord invite link from {message.author.mention} in {message.channel.mention}.",
            color=discord.Color.red(),
        )
        embed.add_field(name="Codes", value=", ".join(f"`{code}`" for code in codes[:8]) or "—", inline=False)
        embed.add_field(name="Reason", value=reason[:1024], inline=False)
        sender = getattr(spam_guard, "_send_modlog_embed", None)
        if callable(sender):
            await sender(guild, embed)
    except Exception:
        pass


async def _hard_block_invite_message(message: discord.Message) -> None:
    try:
        guild = message.guild
        if guild is None or not isinstance(message.author, discord.Member):
            return
        if not isinstance(message.channel, discord.TextChannel):
            return

        from stoney_verify import spam_guard

        settings = await spam_guard.get_spam_settings(guild.id)
        if not bool(settings.get("enabled")):
            return

        target_bot_ids = _normalize_id_list(_first_setting(settings, "invite_hard_block_target_bot_ids", "invite_target_bot_ids"))
        target_channel_ids = _normalize_id_list(_first_setting(settings, "invite_hard_block_target_channel_ids", "invite_target_channel_ids"))

        # Default: protect against humans everywhere. Bots are ignored unless the
        # owner explicitly targets that bot/user ID.
        if getattr(message.author, "bot", False) and str(message.author.id) not in target_bot_ids:
            return
        if target_channel_ids and str(message.channel.id) not in target_channel_ids:
            return

        codes = _extract_codes(message.content or "")
        if not codes:
            return

        override_exempt = _override_enabled(settings, "invite_override_exempt_users_roles")
        override_allowed_roles = _override_enabled(settings, "invite_override_allowed_roles")
        override_allowed_channels = _override_enabled(settings, "invite_override_allowed_channels")
        override_allowed_codes = _override_enabled(settings, "invite_override_allowed_codes")
        override_own_codes = _override_enabled(settings, "invite_override_own_server_invites")

        if not override_exempt:
            if str(message.author.id) in _normalize_id_list(settings.get("exempt_user_ids")):
                return
            if _member_has_any_role(message.author, _normalize_id_list(settings.get("exempt_role_ids"))):
                return
        if not override_allowed_roles and _member_has_any_role(message.author, _normalize_id_list(settings.get("invite_allowed_role_ids"))):
            return
        if not override_allowed_channels and str(message.channel.id) in _normalize_id_list(settings.get("allowed_channel_ids")):
            return

        allowed_codes = set() if override_allowed_codes else _normalize_codes(settings.get("allowed_invite_codes"))
        own_codes = set()
        if bool(settings.get("allow_server_invites", True)) and not override_own_codes:
            own_codes = await _own_invite_codes(guild)

        blocked = [code for code in codes if code not in allowed_codes and code not in own_codes]
        if not blocked:
            return

        override_notes = []
        if override_exempt:
            override_notes.append("exempt users/roles")
        if override_allowed_roles:
            override_notes.append("invite-allowed roles")
        if override_allowed_channels:
            override_notes.append("allowed channels")
        if override_allowed_codes:
            override_notes.append("allowed invite codes")
        if override_own_codes:
            override_notes.append("this-server invite codes")
        if target_bot_ids:
            override_notes.append(f"target bot/user scope={len(target_bot_ids)}")
        if target_channel_ids:
            override_notes.append(f"target channel scope={len(target_channel_ids)}")
        reason = "external Discord invite link"
        if override_notes:
            reason += "; policy: " + ", ".join(override_notes)

        try:
            await message.delete(reason="SpamGuard hard block: external Discord invite link")
            await _modlog(guild, message, blocked, reason)
        except discord.Forbidden:
            await _modlog(guild, message, blocked, "bot lacks Manage Messages in that channel")
        except Exception as exc:
            await _modlog(guild, message, blocked, f"delete failed: {type(exc).__name__}")
    except Exception as exc:
        _log(f"handler error: {type(exc).__name__}: {exc}")


def install() -> bool:
    global _INSTALLED
    if _INSTALLED:
        return True
    if bot is None:
        _log("bot unavailable; listener not installed")
        return False
    try:
        bot.add_listener(_hard_block_invite_message, "on_message")
        _INSTALLED = True
        _log("active; external Discord invite links delete immediately when SpamGuard is enabled")
        return True
    except Exception as exc:
        _log(f"install failed: {type(exc).__name__}: {exc}")
        return False


install()

__all__ = ["install"]
