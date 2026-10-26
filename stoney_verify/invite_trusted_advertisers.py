from __future__ import annotations

"""Per-guild, exact identity+channel exemptions. No vendor name trust.

Guild config is the canonical policy source; Invite Shield consumes the same
config snapshot as its normal allow/block decisions. No channel-wide bypass.
"""

from datetime import datetime, timezone, timedelta
from typing import Any, Mapping

CONFIG_KEY = "invite_trusted_advertisers_v1"
MAX_RULES = 10
RETAIN_CHOICES = (24, 48, 72)
# OneBump's published fair-play rules require ads stay up at least 24 hours.
# Allow extra time for Discord clocks, bot downtime and boundary races.
RETENTION_GRACE_MINUTES = 10


def _id(value: Any) -> int:
    try:
        raw = str(value or "").strip()
        return int(raw) if raw.isdecimal() and 0 < len(raw) <= 20 and int(raw) > 0 else 0
    except (TypeError, ValueError):
        return 0


def clean_rules(config: Mapping[str, Any] | Any) -> list[dict[str, Any]]:
    if not isinstance(config, Mapping):
        return []
    raw = config.get(CONFIG_KEY)
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    used: set[tuple[int, int]] = set()
    for item in raw[:MAX_RULES]:
        if not isinstance(item, dict):
            continue
        bot_id = _id(item.get("bot_id"))
        channel_id = _id(item.get("channel_id"))
        if not bot_id or not channel_id or (bot_id, channel_id) in used:
            continue
        try:
            retention = int(item.get("retention_hours", 24))
        except (TypeError, ValueError):
            retention = 24
        if retention not in RETAIN_CHOICES:
            retention = 24
        used.add((bot_id, channel_id))
        out.append({
            "bot_id": bot_id,
            "channel_id": channel_id,
            "cleanup_enabled": item.get("cleanup_enabled") is True,
            "retention_hours": retention,
            "suppress_embeds": item.get("suppress_embeds") is True,
        })
    return out


def trusted_rule(message: Any, cfg: Any) -> dict[str, Any] | None:
    """Both Discord IDs must match a saved server-owned rule.

    Webhooks, ordinary members, and app responses cannot impersonate bots.
    """
    author = getattr(message, "author", None)
    if not bool(getattr(author, "bot", False)):
        return None
    if _id(getattr(message, "webhook_id", 0)):
        return None
    if getattr(message, "interaction_metadata", None) is not None:
        return None
    if getattr(message, "interaction", None) is not None:
        return None
    bot_id = _id(getattr(author, "id", 0))
    channel_id = _id(getattr(getattr(message, "channel", None), "id", 0))
    app_id = _id(getattr(message, "application_id", 0))
    if not bot_id or not channel_id or (app_id and app_id != bot_id):
        return None
    for rule in clean_rules(cfg):
        if rule["bot_id"] == bot_id and rule["channel_id"] == channel_id:
            return rule
    return None


def is_advert_like(message: Any) -> bool:
    """Do not auto-delete bot greetings, commands or casual text."""
    if getattr(message, "interaction_metadata", None) is not None:
        return False
    if getattr(message, "interaction", None) is not None:
        return False
    try:
        from .invite_policy_engine import extract_invite_codes_from_message
        if extract_invite_codes_from_message(message):
            return True
    except Exception:
        pass
    # Bot-delivered partner ads are rich embeds/buttons; ordinary receipts
    # without invites cannot become cleanup targets.
    if list(getattr(message, "embeds", []) or []):
        return True
    return bool(list(getattr(message, "components", []) or []))


def expires_at(message: Any, rule: Mapping[str, Any]) -> datetime | None:
    posted = getattr(message, "created_at", None)
    if not isinstance(posted, datetime) or posted.tzinfo is None:
        return None
    try:
        hours = int(rule.get("retention_hours", 24))
    except (TypeError, ValueError):
        return None
    if hours not in RETAIN_CHOICES:
        return None
    return posted.astimezone(timezone.utc) + timedelta(
        hours=hours, minutes=RETENTION_GRACE_MINUTES,
    )


async def load_rules(guild_id: int, *, refresh: bool = False) -> list[dict[str, Any]]:
    from .guild_config import get_guild_config
    config = await get_guild_config(guild_id, refresh=refresh)
    if str(config.get("source", "")).startswith("unavailable:"):
        raise RuntimeError("Guild settings are unavailable; refusing to change trust.")
    return clean_rules(config)


async def save_rule(guild_id: int, *, bot_id: int, channel_id: int,
                    cleanup_enabled: bool = False, retention_hours: int = 24,
                    remove: bool = False,
                    suppress_embeds: bool | None = None) -> list[dict[str, Any]]:
    from .guild_config import get_guild_config, compare_and_swap_guild_config_key
    from .invite_policy_engine import invalidate_invite_policy
    gid, bot, chan = _id(guild_id), _id(bot_id), _id(channel_id)
    if not gid or not bot or not chan:
        raise ValueError("Select a valid bot and channel.")
    if retention_hours not in RETAIN_CHOICES:
        raise ValueError("Choose 24, 48 or 72 hours.")
    for _attempt in range(3):
        cfg = await get_guild_config(gid, refresh=True)
        if str(cfg.get("source", "")).startswith("unavailable:"):
            raise RuntimeError("Guild settings unavailable. Nothing was saved.")
        current_raw = cfg.get(CONFIG_KEY)
        current = clean_rules(cfg)
        old = next((v for v in current if (v["bot_id"], v["channel_id"]) == (bot, chan)), None)
        new = [dict(v) for v in current if (v["bot_id"], v["channel_id"]) != (bot, chan)]
        if not remove:
            if len(new) >= MAX_RULES:
                raise ValueError("This server has reached its ten trusted bot/channel pairs.")
            new.append({
                "bot_id": bot, "channel_id": chan,
                "cleanup_enabled": bool(cleanup_enabled),
                "retention_hours": retention_hours,
                "suppress_embeds": (old or {}).get("suppress_embeds", False)
                    if suppress_embeds is None else bool(suppress_embeds),
            })
        ok, _ = await compare_and_swap_guild_config_key(
            gid, CONFIG_KEY,
            expected=current_raw, value=new,
            source="invite_trusted_advertisers",
        )
        if ok:
            invalidate_invite_policy(gid)
            return new
    raise RuntimeError("Another moderator changed this setting. Reopen and retry.")
