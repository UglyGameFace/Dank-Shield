from __future__ import annotations

"""Immediate external Discord invite deletion for SpamGuard."""

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
        if text in {"1", "true", "yes", "y", "on", "enabled", "all"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled", "none"}:
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


def _component_text(component: Any) -> list[str]:
    parts: list[str] = []
    try:
        for attr in ("url", "label", "custom_id"):
            value = getattr(component, attr, None)
            if value:
                parts.append(str(value))
    except Exception:
        pass
    try:
        for child in list(getattr(component, "children", []) or []):
            parts.extend(_component_text(child))
    except Exception:
        pass
    return parts


def _message_text(message: discord.Message) -> str:
    parts: list[str] = [str(getattr(message, "content", "") or "")]

    try:
        for embed in list(getattr(message, "embeds", []) or []):
            for attr in ("title", "description", "url"):
                value = getattr(embed, attr, None)
                if value:
                    parts.append(str(value))

            for field in list(getattr(embed, "fields", []) or []):
                parts.append(str(getattr(field, "name", "") or ""))
                parts.append(str(getattr(field, "value", "") or ""))

            footer = getattr(embed, "footer", None)
            if getattr(footer, "text", None):
                parts.append(str(footer.text))

            author = getattr(embed, "author", None)
            if getattr(author, "name", None):
                parts.append(str(author.name))
            if getattr(author, "url", None):
                parts.append(str(author.url))
    except Exception:
        pass

    try:
        for row in list(getattr(message, "components", []) or []):
            parts.extend(_component_text(row))
    except Exception:
        pass

    try:
        for attachment in list(getattr(message, "attachments", []) or []):
            for attr in ("url", "proxy_url", "filename", "description"):
                value = getattr(attachment, attr, None)
                if value:
                    parts.append(str(value))
    except Exception:
        pass

    return "\n".join(_clean_invite_text(part) for part in parts if part)


def _extract_codes_from_message(message: discord.Message) -> list[str]:
    """Extract invite codes from content, embeds, components, and attachments."""

    text = _message_text(message)
    compact = re.sub(r"\s+", "", text)

    codes: list[str] = []
    for source in (text, compact):
        try:
            for code in INVITE_HARD_RE.findall(source or ""):
                clean = str(code or "").strip().lower()
                if clean and clean not in codes:
                    codes.append(clean)
        except Exception:
            continue

    return codes


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
    codes: Set[str] = set()

    try:
        from stoney_verify import spam_guard

        getter = getattr(spam_guard, "_fetch_guild_invite_codes", None)
        if callable(getter):
            codes.update(str(code).lower() for code in await getter(guild) if str(code or "").strip())
    except Exception:
        pass

    try:
        codes.update(str(inv.code).lower() for inv in await guild.invites() if getattr(inv, "code", None))
    except Exception:
        pass

    try:
        vanity_code = getattr(guild, "vanity_url_code", None)
        if vanity_code:
            codes.add(str(vanity_code).lower())
    except Exception:
        pass

    try:
        vanity_invite = await guild.vanity_invite()
        vanity_code = getattr(vanity_invite, "code", None)
        if vanity_code:
            codes.add(str(vanity_code).lower())
    except Exception:
        pass

    return codes

async def _invite_runtime_state(
    guild: discord.Guild,
    settings: dict[str, Any],
) -> tuple[bool, bool]:
    """Return (runtime_enabled, invite_shield_enabled).

    Spam Guard's old master toggle and Protection Center's Invite Shield toggle
    live in different config stores. Invite Shield ON must enforce invites for
    every sender, including bots. Legacy Spam Guard-only mode keeps its bot
    targeting behavior.
    """

    try:
        spam_guard_enabled = _safe_bool(settings.get("enabled"), False)
        invite_shield_enabled = False

        for key in (
            "invite_shield_enabled",
            "invite_hard_block_enabled",
            "automod_block_invites",
            "block_invites",
        ):
            if key in settings and _safe_bool(settings.get(key), False):
                invite_shield_enabled = True
            spam_key = f"spam_{key}"
            if spam_key in settings and _safe_bool(settings.get(spam_key), False):
                invite_shield_enabled = True

        try:
            from stoney_verify.commands_ext import public_protection_center as center

            cfg = await center.get_guild_config(int(guild.id), refresh=False)
            if center._cfg_bool(cfg, "automod_block_invites", False):
                invite_shield_enabled = True
        except TypeError:
            try:
                from stoney_verify.commands_ext import public_protection_center as center

                cfg = await center.get_guild_config(int(guild.id))
                if center._cfg_bool(cfg, "automod_block_invites", False):
                    invite_shield_enabled = True
            except Exception as exc:
                _log(
                    "invite shield guild-config check failed "
                    f"guild={getattr(guild, 'id', 'unknown')} error={type(exc).__name__}"
                )
        except Exception as exc:
            _log(
                "invite shield guild-config check failed "
                f"guild={getattr(guild, 'id', 'unknown')} error={type(exc).__name__}"
            )

        return bool(spam_guard_enabled or invite_shield_enabled), bool(invite_shield_enabled)
    except Exception:
        return False, False



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

        embed = discord.Embed(title="🛡️ Invite Link Blocked", description=f"Deleted Discord invite link from {message.author.mention} in {message.channel.mention}.", color=discord.Color.red())
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
        runtime_enabled, invite_shield_enabled = await _invite_runtime_state(guild, settings)
        if not runtime_enabled:
            return

        include_all_bots = _safe_bool(_first_setting(settings, "invite_hard_block_target_all_bots", "invite_target_all_bots"), False)
        include_ids = _normalize_id_list(_first_setting(settings, "invite_hard_block_target_bot_ids", "invite_target_bot_ids"))
        channel_ids = _normalize_id_list(_first_setting(settings, "invite_hard_block_target_channel_ids", "invite_target_channel_ids"))

        if (
            getattr(message.author, "bot", False)
            and not invite_shield_enabled
            and not include_all_bots
            and str(message.author.id) not in include_ids
        ):
            return
        if channel_ids and str(message.channel.id) not in channel_ids:
            return

        codes = _extract_codes_from_message(message)
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

        notes = []
        if override_exempt:
            notes.append("exempt users/roles ignored")
        if override_allowed_roles:
            notes.append("invite-allowed roles ignored")
        if override_allowed_channels:
            notes.append("allowed channels ignored")
        if override_allowed_codes:
            notes.append("allowed invite codes ignored")
        if override_own_codes:
            notes.append("this-server invite codes ignored")
        if include_all_bots:
            notes.append("all bots included")
        elif include_ids:
            notes.append(f"listed bot/user ids={len(include_ids)}")
        if channel_ids:
            notes.append(f"listed channel ids={len(channel_ids)}")
        reason = "external Discord invite link"
        if notes:
            reason += "; policy: " + ", ".join(notes)

        try:
            await message.delete(reason="Dank Shield Invite Shield: external Discord invite link")
            await _modlog(guild, message, blocked, reason)
            _log(
                "deleted external invite "
                f"guild={guild.id} channel={message.channel.id} author={message.author.id} codes={','.join(blocked[:5])}"
            )
        except discord.Forbidden:
            _log(
                "delete forbidden "
                f"guild={guild.id} channel={message.channel.id} author={message.author.id}"
            )
            await _modlog(guild, message, blocked, "bot lacks Manage Messages in that channel")
        except Exception as exc:
            _log(
                "delete failed "
                f"guild={guild.id} channel={message.channel.id} author={message.author.id} error={type(exc).__name__}"
            )
            await _modlog(guild, message, blocked, f"delete failed: {type(exc).__name__}")
    except Exception as exc:
        _log(f"handler error: {type(exc).__name__}: {exc}")


async def _hard_block_invite_message_edit(before: discord.Message, after: discord.Message) -> None:
    try:
        await _hard_block_invite_message(after)
    except Exception as exc:
        _log(f"edit handler error: {type(exc).__name__}: {exc}")


def install() -> bool:
    global _INSTALLED
    if _INSTALLED:
        return True
    if bot is None:
        _log("bot unavailable; listener not installed")
        return False
    try:
        bot.add_listener(_hard_block_invite_message, "on_message")
        bot.add_listener(_hard_block_invite_message_edit, "on_message_edit")
        _INSTALLED = True
        _log("active; rich external Discord invite links delete on create/edit when SpamGuard or Invite Shield is enabled")
        return True
    except Exception as exc:
        _log(f"install failed: {type(exc).__name__}: {exc}")
        return False


install()

try:
    from stoney_verify.startup_guards import spam_guard_invite_scope_pagination_guard as _scope_pagination_guard
    _scope_pagination_guard.apply()
except Exception as exc:
    _log(f"scope pagination guard not applied yet: {type(exc).__name__}: {exc}")

__all__ = ["install"]
