from __future__ import annotations

"""One staff notification for each newly created Dank Shield ticket.

Both the public ticket panel and the ticket service call this after successful
channel creation. Reused tickets, reopen actions, and ghost tests never ping.
Settings are one atomic, per-guild guild_config feature blob, not a second
ticket creator or a deployment-wide role ID.
"""

import re
from typing import Any, Mapping, Sequence

import discord

from ..guild_config import compare_and_swap_guild_config_key, get_guild_config

SETTINGS_KEY = "ticket_open_notifications_v1"
MAX_ROLES = 5
MAX_CATEGORY_RULES = 50


def category_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "support").lower()).strip("-")[:80] or "support"


def _role_ids(values: Any) -> list[int]:
    if not isinstance(values, (list, tuple)):
        return []
    ids: list[int] = []
    for value in values:
        try:
            role_id = int(value)
        except (ValueError, TypeError):
            continue
        if role_id > 0 and role_id not in ids:
            ids.append(role_id)
        if len(ids) >= MAX_ROLES:
            break
    return ids


def normalize_settings(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, Mapping) else {}
    rules = data.get("category_roles")
    category_roles: dict[str, list[int]] = {}
    if isinstance(rules, Mapping):
        for key, values in list(rules.items())[:MAX_CATEGORY_RULES]:
            if not isinstance(values, (list, tuple)):
                continue
            category_roles[category_key(key)] = _role_ids(values)
    return {
        # A correctly configured ticket staff role is enough for basic pings.
        # Explicit false is respected, including when the automation worker is off.
        "enabled": data.get("enabled") is not False,
        "role_ids": _role_ids(data.get("role_ids")),
        "category_roles": category_roles,
    }


async def load_settings(guild_id: int) -> tuple[dict[str, Any], bool]:
    cfg = await get_guild_config(int(guild_id), refresh=True)
    source = str(cfg.get("source") or "")
    # Never fall back to a different guild's environment roles when storage is down.
    return normalize_settings(cfg.get(SETTINGS_KEY)), source.startswith("supabase:")


async def save_settings(guild_id: int, patch: Mapping[str, Any]) -> dict[str, Any]:
    """CAS prevents simultaneous mobile managers overwriting each other's changes."""
    for _ in range(3):
        cfg = await get_guild_config(int(guild_id), refresh=True)
        if not str(cfg.get("source") or "").startswith("supabase:"):
            raise RuntimeError("Per-server ticket settings are unavailable; no changes saved.")
        expected = cfg.get(SETTINGS_KEY)
        next_value = normalize_settings(expected)
        if "enabled" in patch:
            next_value["enabled"] = bool(patch["enabled"])
        if "role_ids" in patch:
            next_value["role_ids"] = _role_ids(patch["role_ids"])
        if "category_key" in patch:
            key = category_key(patch["category_key"])
            rules = dict(next_value["category_roles"])
            if patch.get("remove_category"):
                rules.pop(key, None)
            elif "category_role_ids" in patch:
                rules[key] = _role_ids(patch["category_role_ids"])
            next_value["category_roles"] = rules
        applied, _ = await compare_and_swap_guild_config_key(
            int(guild_id), SETTINGS_KEY, expected=expected, value=next_value,
            source="ticket_open_notification_settings",
        )
        if applied:
            return next_value
    raise RuntimeError("Ticket settings changed concurrently. Reopen this panel and retry.")


def selected_role_ids(
    settings: Mapping[str, Any], category: str, default_staff_role_ids: Sequence[int],
) -> list[int]:
    rules = settings.get("category_roles") or {}
    key = category_key(category)
    if isinstance(rules, Mapping) and key in rules:
        return _role_ids(rules[key])
    preferred = _role_ids(settings.get("role_ids"))
    return preferred if preferred else _role_ids(list(default_staff_role_ids))


def _private_for_staff(channel: discord.TextChannel) -> bool:
    try:
        return not channel.permissions_for(channel.guild.default_role).view_channel
    except Exception:
        return False


async def alert_channel_for_guild(guild: discord.Guild) -> discord.TextChannel | None:
    # Reuse ticket automation's existing alert destination, without depending
    # on the automation worker being enabled.
    try:
        from ..workers.ticket_automation_worker import get_ticket_automation_settings
        saved = await get_ticket_automation_settings(guild.id)
        channel_id = int(saved.get("staff_alert_channel_id") or 0)
        channel = guild.get_channel(channel_id) if channel_id else None
        if isinstance(channel, discord.TextChannel) and _private_for_staff(channel):
            return channel
    except Exception as exc:
        print(f"⚠️ ticket new alert destination unavailable guild={guild.id}: {type(exc).__name__}")
    return None


async def notify_new_ticket(
    channel: discord.TextChannel,
    *,
    owner: discord.Member,
    category: str,
    default_staff_role_ids: Sequence[int] = (),
    is_ghost: bool = False,
) -> bool:
    """Best effort, one-shot ping; only call after a genuinely new channel exists."""
    if is_ghost:
        return False
    guild = channel.guild
    try:
        settings, available = await load_settings(guild.id)
        if not available or not settings["enabled"]:
            return False

        role_ids = selected_role_ids(settings, category, default_staff_role_ids)
        if not role_ids:
            # Canonical per-guild staff role is the default if the caller omitted one.
            cfg = await get_guild_config(guild.id, refresh=True)
            role_ids = _role_ids([cfg.get("staff_role_id") or cfg.get("ticket_staff_role_id")])
        if not role_ids:
            return False

        target = await alert_channel_for_guild(guild) or channel
        bot_member = guild.me
        if bot_member is None:
            return False
        perms = target.permissions_for(bot_member)
        if not (perms.view_channel and perms.send_messages):
            if target.id == channel.id:
                return False
            target = channel
            perms = target.permissions_for(bot_member)
            if not (perms.view_channel and perms.send_messages):
                return False

        roles: list[discord.Role] = []
        for role_id in role_ids:
            role = guild.get_role(role_id)
            if role is None or role.is_default():
                continue
            if role.mentionable or perms.mention_everyone:
                roles.append(role)
        if not roles:
            print(f"⚠️ ticket staff ping suppressed guild={guild.id}: roles missing or unmentionable")
            return False

        safe_category = discord.utils.escape_mentions(discord.utils.escape_markdown(str(category)[:80]))
        content = (
            f"{' '.join(role.mention for role in roles)} "
            f"🎫 New **{safe_category}** ticket from <@{owner.id}> in {channel.mention}."
        )
        await target.send(
            content,
            allowed_mentions=discord.AllowedMentions(
                everyone=False, users=False, roles=roles, replied_user=False,
            ),
        )
        return True
    except Exception as exc:
        print(f"⚠️ ticket staff ping failed guild={guild.id} channel={channel.id}: {type(exc).__name__}: {exc}")
        return False
