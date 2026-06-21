from __future__ import annotations

"""Central Discord invite enforcement policy for Dank Shield.

This module is intentionally the only place that is allowed to approve a
Discord invite-link delete.  Runtime listeners, scanners, Spam Guard bridge
code, Automod, and protected-bot rules must ask this engine first.

Default production posture:
- same-server invite links are allowed
- normal links are not touched here
- Spam Guard alone does not delete a single invite link
- unknown invite targets are allowed/logged unless strict unknown handling is on
- every decision is keyed by the current Discord guild id
"""

import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

import discord

INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord(?:app)?\.com\s*/\s*invite|discord\.gg)\s*/\s*([A-Za-z0-9-]+)",
    re.IGNORECASE,
)

_POLICY_CACHE: dict[int, tuple[float, Any, dict[str, Any]]] = {}
_POLICY_CACHE_TTL_SECONDS = 8.0
_DECISION_TRACE: dict[tuple[int, int, int], "InviteDecision"] = {}
_DECISION_TRACE_ORDER: list[tuple[int, int, int]] = []
_DECISION_TRACE_LIMIT = 750


@dataclass(slots=True)
class InviteDecision:
    action: str = "allow"  # allow, log_only, warn, delete
    feature_owner: str = "Invite Policy"
    rule_id: str = "no_rule"
    reason: str = "No invite enforcement rule matched."
    fix_hint: str = "No action needed."
    guild_id: int = 0
    config_guild_id: int = 0
    channel_id: int = 0
    author_id: int = 0
    source: str = "live"
    codes: list[str] = field(default_factory=list)
    blocked_codes: list[str] = field(default_factory=list)
    allowed_codes: list[str] = field(default_factory=list)
    internal_codes: list[str] = field(default_factory=list)
    external_codes: list[str] = field(default_factory=list)
    unknown_codes: list[str] = field(default_factory=list)
    target_guilds: dict[str, str] = field(default_factory=dict)
    settings_source: str = "guild"
    invite_shield_enabled: bool = False
    link_shield_enabled: bool = False
    spam_guard_enabled: bool = False
    spam_invite_burst_action: str = "observe"
    protected_poster_rule_enabled: bool = False
    protected_poster_matched: bool = False
    strict_unknown_invites: bool = False
    delete_attempted: bool = False
    delete_succeeded: bool = False
    delete_error: str = ""

    @property
    def should_delete(self) -> bool:
        return self.action == "delete"


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
        if text in {"1", "true", "yes", "y", "on", "enabled", "enable", "allow", "allowed", "all"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled", "disable", "block", "blocked", "none"}:
            return False
    except Exception:
        pass
    return bool(default)


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

    for bucket in ("settings", "config", "metadata", "meta"):
        try:
            nested = getattr(cfg, bucket, None)
            if isinstance(nested, Mapping) and nested.get(key) is not None:
                return nested.get(key)
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, Mapping) and nested.get(key) is not None:
                    return nested.get(key)
        except Exception:
            pass

    return default


def _cfg_bool(cfg: Any, key: str, default: bool = False) -> bool:
    return _safe_bool(_cfg_value(cfg, key, default), default)


def _setting(settings: Mapping[str, Any], key: str, default: Any = None) -> Any:
    if key in settings and settings.get(key) is not None:
        return settings.get(key)
    spam_key = f"spam_{key}"
    if spam_key in settings and settings.get(spam_key) is not None:
        return settings.get(spam_key)
    return default


def _setting_bool(settings: Mapping[str, Any], key: str, default: bool = False) -> bool:
    return _safe_bool(_setting(settings, key, default), default)


def _ids(values: Any) -> set[str]:
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


def _codes(values: Any) -> set[str]:
    out: set[str] = set()
    try:
        source = values if isinstance(values, Iterable) and not isinstance(values, (str, bytes, dict)) else [values]
        for raw in source:
            text = _safe_str(raw).lower().strip("/")
            text = text.replace("https://discord.gg/", "").replace("http://discord.gg/", "")
            text = text.replace("https://discord.com/invite/", "").replace("http://discord.com/invite/", "")
            text = text.replace("https://discordapp.com/invite/", "").replace("http://discordapp.com/invite/", "")
            text = text.strip()
            if text:
                out.add(text)
    except Exception:
        pass
    return out


def normalize_invite_code(value: Any) -> str:
    text = _safe_str(value).lower().strip().strip("/")
    text = text.replace("https://discord.gg/", "").replace("http://discord.gg/", "")
    text = text.replace("https://discord.com/invite/", "").replace("http://discord.com/invite/", "")
    text = text.replace("https://discordapp.com/invite/", "").replace("http://discordapp.com/invite/", "")
    text = re.sub(r"[^a-z0-9-]+", "", text, flags=re.IGNORECASE)
    return text[:80]


def clean_invite_text(value: Any) -> str:
    text = _safe_str(value)
    text = text.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "")
    text = text.replace("[.]", ".").replace("(.)", ".").replace(" . ", ".")
    text = re.sub(r"discord\s*\.\s*gg", "discord.gg", text, flags=re.IGNORECASE)
    text = re.sub(r"discord(?:app)?\s*\.\s*com\s*/\s*invite", "discord.com/invite", text, flags=re.IGNORECASE)
    return text


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


def message_text(message: discord.Message) -> str:
    parts: list[str] = [str(getattr(message, "content", "") or "")]
    try:
        for embed in list(getattr(message, "embeds", []) or []):
            for attr in ("title", "description", "url"):
                value = getattr(embed, attr, None)
                if value:
                    parts.append(str(value))
            for field_obj in list(getattr(embed, "fields", []) or []):
                parts.append(str(getattr(field_obj, "name", "") or ""))
                parts.append(str(getattr(field_obj, "value", "") or ""))
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
    return "\n".join(clean_invite_text(part) for part in parts if part)


def extract_invite_codes_from_text(value: Any) -> list[str]:
    text = clean_invite_text(value)
    compact = re.sub(r"\s+", "", text)
    found: list[str] = []
    for source in (text, compact):
        try:
            for raw in INVITE_RE.findall(source or ""):
                code = normalize_invite_code(raw)
                if code and code not in found:
                    found.append(code)
        except Exception:
            continue
    return found


def extract_invite_codes_from_message(message: discord.Message) -> list[str]:
    return extract_invite_codes_from_text(message_text(message))


def has_discord_invite(value: Any) -> bool:
    return bool(extract_invite_codes_from_text(value))


async def load_invite_policy(guild: discord.Guild, *, refresh: bool = False) -> tuple[Any, dict[str, Any]]:
    gid = int(guild.id)
    now = time.monotonic()
    if not refresh:
        cached = _POLICY_CACHE.get(gid)
        if cached is not None:
            saved_at, cfg, settings = cached
            if now - float(saved_at) <= _POLICY_CACHE_TTL_SECONDS:
                return cfg, dict(settings or {})

    cfg = None
    try:
        from stoney_verify.guild_config import get_guild_config

        cfg = await get_guild_config(gid, refresh=refresh)
    except TypeError:
        try:
            from stoney_verify.guild_config import get_guild_config

            cfg = await get_guild_config(gid)
        except Exception:
            cfg = None
    except Exception:
        cfg = None

    settings: dict[str, Any] = {}
    try:
        from stoney_verify import spam_guard

        settings = dict(await spam_guard.get_spam_settings(gid) or {})
    except Exception:
        settings = {}

    _POLICY_CACHE[gid] = (now, cfg, dict(settings))
    return cfg, dict(settings)


def invalidate_invite_policy(guild_id: int) -> None:
    try:
        _POLICY_CACHE.pop(int(guild_id), None)
    except Exception:
        pass


def _channel_ids(message: discord.Message) -> set[str]:
    out: set[str] = set()
    try:
        channel = getattr(message, "channel", None)
        for obj in (channel, getattr(channel, "parent", None), getattr(channel, "category", None)):
            cid = str(getattr(obj, "id", "") or "")
            if cid.isdigit():
                out.add(cid)
    except Exception:
        pass
    return out


def _member_has_any_role(member: Any, role_ids: set[str]) -> bool:
    try:
        wanted = {int(x) for x in role_ids if str(x).isdigit()}
        return bool(wanted) and any(int(getattr(role, "id", 0) or 0) in wanted for role in list(getattr(member, "roles", []) or []))
    except Exception:
        return False


def _protected_target_match(message: discord.Message, settings: Mapping[str, Any]) -> bool:
    author_id = str(getattr(getattr(message, "author", None), "id", "") or "")
    author_is_bot = bool(getattr(getattr(message, "author", None), "bot", False))
    all_bots = _setting_bool(settings, "invite_hard_block_target_all_bots", False) or _setting_bool(settings, "invite_target_all_bots", False)
    bot_ids = _ids(_setting(settings, "invite_hard_block_target_bot_ids", _setting(settings, "invite_target_bot_ids")))
    wanted_channels = _ids(_setting(settings, "invite_hard_block_target_channel_ids", _setting(settings, "invite_target_channel_ids")))
    author_match = author_id in bot_ids or (author_is_bot and all_bots)
    channel_match = bool(wanted_channels and (_channel_ids(message) & wanted_channels))
    return bool(author_match or channel_match)


def _protected_poster_rule_enabled(settings: Mapping[str, Any]) -> bool:
    # New explicit gate.  Old target IDs still work when Invite Shield itself is
    # enabled, but they do not override Invite Shield OFF unless this is true.
    return any(
        _setting_bool(settings, key, False)
        for key in (
            "invite_protected_poster_rule_enabled",
            "protected_poster_invite_rule_enabled",
            "invite_hard_block_protected_posters_enabled",
        )
    )


def _spam_invite_burst_action(settings: Mapping[str, Any]) -> str:
    raw = _safe_str(
        _setting(
            settings,
            "invite_burst_action",
            _setting(settings, "spam_invite_burst_action", "observe"),
        )
    ).lower()
    aliases = {
        "off": "disabled",
        "none": "disabled",
        "false": "disabled",
        "log": "observe",
        "log_only": "observe",
        "alert": "observe",
        "alert_only": "observe",
        "delete": "delete_on_burst",
        "delete_only": "delete_on_burst",
        "delete_on_burst": "delete_on_burst",
        "warn": "warn",
        "warning": "warn",
        "observe": "observe",
    }
    return aliases.get(raw, "observe")


async def _guild_invite_codes(guild: discord.Guild) -> set[str]:
    codes: set[str] = set()
    try:
        from stoney_verify import spam_guard

        getter = getattr(spam_guard, "_fetch_guild_invite_codes", None)
        if callable(getter):
            codes.update(str(code).lower() for code in await getter(guild) if _safe_str(code))
    except Exception:
        pass
    try:
        codes.update(str(inv.code).lower() for inv in await guild.invites() if getattr(inv, "code", None))
    except Exception:
        pass
    try:
        vanity = getattr(guild, "vanity_url_code", None)
        if vanity:
            codes.add(str(vanity).lower())
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


async def _invite_code_belongs_to_guild(guild: discord.Guild, code: str) -> tuple[str, str]:
    """Return (classification, target label): internal/external/unknown."""

    clean = normalize_invite_code(code)
    if not clean:
        return "unknown", ""

    current_guild_id = str(getattr(guild, "id", "") or "")

    # First prove same-server invites from the current guild's known invite list.
    # This is what prevents this-server invites from being deleted.
    try:
        own_codes = await _guild_invite_codes(guild)
        if clean in own_codes:
            return "internal", current_guild_id
    except Exception:
        pass

    try:
        from stoney_verify.startup_guards.invite_shield_sanitize_shared import invite_code_belongs_to_guild

        if await invite_code_belongs_to_guild(guild, clean):
            return "internal", current_guild_id
    except Exception:
        pass

    # Prefer discord.py's public client API for target lookup.
    try:
        state = getattr(guild, "_state", None)
        get_client = getattr(state, "_get_client", None)
        client = get_client() if callable(get_client) else None
        fetch_invite = getattr(client, "fetch_invite", None)

        if callable(fetch_invite):
            try:
                invite_obj = await fetch_invite(clean, with_counts=False, with_expiration=False)
            except TypeError:
                invite_obj = await fetch_invite(clean)

            target_guild = getattr(invite_obj, "guild", None)
            target_id = str(getattr(target_guild, "id", "") or "")
            target_name = str(getattr(target_guild, "name", "") or "")

            if target_id and target_id == current_guild_id:
                return "internal", target_name or target_id
            if target_id:
                return "external", target_name or target_id
    except Exception:
        pass

    # Low-level fallback if client.fetch_invite is unavailable.
    try:
        invite = await guild._state.http.get_invite(clean, with_counts=False, with_expiration=False)  # type: ignore[attr-defined]
        target = invite.get("guild") if isinstance(invite, dict) else None
        target_id = str((target or {}).get("id") or "")
        target_name = str((target or {}).get("name") or "")

        if target_id and target_id == current_guild_id:
            return "internal", target_name or target_id
        if target_id:
            return "external", target_name or target_id
    except Exception:
        pass

    return "unknown", ""


def _decision_key(message: discord.Message) -> tuple[int, int, int]:
    return (
        int(getattr(getattr(message, "guild", None), "id", 0) or 0),
        int(getattr(getattr(message, "channel", None), "id", 0) or 0),
        int(getattr(getattr(message, "author", None), "id", 0) or 0),
    )


def record_invite_decision(message: discord.Message, decision: InviteDecision) -> None:
    try:
        key = _decision_key(message)
        if key == (0, 0, 0):
            return
        _DECISION_TRACE[key] = decision
        _DECISION_TRACE_ORDER.append(key)
        while len(_DECISION_TRACE_ORDER) > _DECISION_TRACE_LIMIT:
            old = _DECISION_TRACE_ORDER.pop(0)
            _DECISION_TRACE.pop(old, None)
    except Exception:
        pass


def get_last_invite_decision(*, guild_id: int, channel_id: int = 0, user_id: int = 0) -> InviteDecision | None:
    try:
        gid = int(guild_id)
        cid = int(channel_id or 0)
        uid = int(user_id or 0)
        if cid and uid:
            found = _DECISION_TRACE.get((gid, cid, uid))
            if found:
                return found
        for key in reversed(_DECISION_TRACE_ORDER):
            kgid, kcid, kuid = key
            if kgid != gid:
                continue
            if cid and kcid != cid:
                continue
            if uid and kuid != uid:
                continue
            found = _DECISION_TRACE.get(key)
            if found:
                return found
    except Exception:
        pass
    return None


async def decide_invite_message(
    message: discord.Message,
    *,
    source: str = "live",
    spam_burst: bool = False,
    refresh_policy: bool = False,
) -> InviteDecision:
    guild = getattr(message, "guild", None)
    channel = getattr(message, "channel", None)
    author = getattr(message, "author", None)
    guild_id = int(getattr(guild, "id", 0) or 0)

    decision = InviteDecision(
        guild_id=guild_id,
        config_guild_id=guild_id,
        channel_id=int(getattr(channel, "id", 0) or 0),
        author_id=int(getattr(author, "id", 0) or 0),
        source=source,
    )

    if guild is None or guild_id <= 0:
        decision.reason = "Message is not in a server."
        return decision

    codes = extract_invite_codes_from_message(message)
    decision.codes = list(codes)
    if not codes:
        decision.reason = "No Discord invite link was found."
        record_invite_decision(message, decision)
        return decision

    cfg, settings = await load_invite_policy(guild, refresh=refresh_policy)
    decision.config_guild_id = guild_id

    invite_shield = (
        _cfg_bool(cfg, "automod_block_invites", False)
        or _setting_bool(settings, "invite_shield_enabled", False)
        or _setting_bool(settings, "invite_hard_block_enabled", False)
        or _setting_bool(settings, "automod_block_invites", False)
        or _setting_bool(settings, "block_invites", False)
    )
    link_shield = _cfg_bool(cfg, "automod_block_links", False) or _setting_bool(settings, "automod_block_links", False)
    spam_enabled = _safe_bool(settings.get("enabled", settings.get("spam_blocker_enabled")), False)
    protected_rule_enabled = _protected_poster_rule_enabled(settings)
    protected_match = _protected_target_match(message, settings)
    protected_active = bool(protected_rule_enabled and protected_match)
    strict_unknown = (
        _cfg_bool(cfg, "invite_strict_unknown_targets", False)
        or _setting_bool(settings, "invite_strict_unknown_targets", False)
        or _setting_bool(settings, "strict_unknown_invites", False)
    )

    # Unknown means the invite was detected, but Discord did not prove it is
    # this server's invite. While Invite Shield/Link Shield is protecting the
    # server, unknown invite targets must not fail-open.
    block_unknown_when_protected = bool(
        strict_unknown
        or invite_shield
        or link_shield
        or protected_active
    )
    block_internal = (
        _cfg_bool(cfg, "automod_block_internal_invites", False)
        or _setting_bool(settings, "block_internal_invites", False)
        or _setting_bool(settings, "invite_block_internal_invites", False)
    )

    allow_server_invites = _setting_bool(settings, "allow_server_invites", True)
    allowed_code_set = _codes(_setting(settings, "allowed_invite_codes", []))
    exempt_users = _ids(_setting(settings, "exempt_user_ids", []))
    exempt_roles = _ids(_setting(settings, "exempt_role_ids", []))
    allowed_roles = _ids(_setting(settings, "invite_allowed_role_ids", []))
    allowed_channels = _ids(_setting(settings, "allowed_invite_channel_ids", _setting(settings, "invite_allowed_channel_ids", [])))

    decision.invite_shield_enabled = bool(invite_shield)
    decision.link_shield_enabled = bool(link_shield)
    decision.spam_guard_enabled = bool(spam_enabled)
    decision.spam_invite_burst_action = _spam_invite_burst_action(settings)
    decision.protected_poster_rule_enabled = bool(protected_rule_enabled)
    decision.protected_poster_matched = bool(protected_match)
    decision.strict_unknown_invites = bool(block_unknown_when_protected)

    if str(decision.author_id) in exempt_users:
        decision.rule_id = "exempt_user"
        decision.reason = "Author is on the invite exempt-user list."
        decision.fix_hint = "Remove the user from exempt users if this should be enforced."
        decision.allowed_codes = list(codes)
        record_invite_decision(message, decision)
        return decision

    if isinstance(author, discord.Member) and _member_has_any_role(author, exempt_roles):
        decision.rule_id = "exempt_role"
        decision.reason = "Author has an exempt role."
        decision.fix_hint = "Remove the role from exempt roles if this should be enforced."
        decision.allowed_codes = list(codes)
        record_invite_decision(message, decision)
        return decision

    if isinstance(author, discord.Member) and _member_has_any_role(author, allowed_roles):
        decision.rule_id = "invite_allowed_role"
        decision.reason = "Author has an invite-allowed role."
        decision.fix_hint = "Remove the role from invite-allowed roles if this should be enforced."
        decision.allowed_codes = list(codes)
        record_invite_decision(message, decision)
        return decision

    if str(decision.channel_id) in allowed_channels:
        decision.rule_id = "invite_allowed_channel"
        decision.reason = "Channel is on the invite allowed-channel list."
        decision.fix_hint = "Remove the channel from invite allowed channels if this should be enforced."
        decision.allowed_codes = list(codes)
        record_invite_decision(message, decision)
        return decision

    blocked: list[str] = []
    unknown_blocked: list[str] = []
    for raw in codes:
        code = normalize_invite_code(raw)
        if not code:
            continue

        if code in allowed_code_set:
            decision.allowed_codes.append(code)
            continue

        classification, target_label = await _invite_code_belongs_to_guild(guild, code)
        if target_label:
            decision.target_guilds[code] = target_label

        if classification == "internal":
            decision.internal_codes.append(code)
            if allow_server_invites and not block_internal:
                decision.allowed_codes.append(code)
                continue
            blocked.append(code)
            continue

        if classification == "external":
            decision.external_codes.append(code)
            blocked.append(code)
            continue

        decision.unknown_codes.append(code)
        if block_unknown_when_protected:
            unknown_blocked.append(code)
            blocked.append(code)
        else:
            decision.allowed_codes.append(code)

    decision.blocked_codes = list(dict.fromkeys(blocked))

    if not decision.blocked_codes:
        decision.rule_id = "allowed_invite"
        if decision.internal_codes:
            decision.reason = "Invite link points to this server and same-server invites are allowed."
            decision.fix_hint = "Enable Block Internal Invites only if you intentionally want to remove same-server invites."
        elif decision.unknown_codes:
            decision.action = "log_only"
            decision.rule_id = "unknown_invite_target_allowed"
            decision.reason = "Invite target could not be verified, and no invite-blocking feature required deletion."
            decision.fix_hint = "Enable Invite Shield or strict unknown invite handling if you want unknown invite targets removed."
        else:
            decision.reason = "Invite code is allowed by policy."
            decision.fix_hint = "No action needed."
        record_invite_decision(message, decision)
        return decision

    if invite_shield:
        decision.action = "delete"
        decision.feature_owner = "Invite Shield"
        decision.rule_id = "invite_shield_external_or_blocked"
        decision.reason = "Invite Shield is enabled and this message contains a blocked Discord invite code."
        decision.fix_hint = "Turn Invite Shield off, allow the invite code, or allow this channel/role/user."
    elif link_shield:
        decision.action = "delete"
        decision.feature_owner = "Link Shield"
        decision.rule_id = "link_shield_discord_invite"
        decision.reason = "Link Shield is enabled and Discord invites are treated as blocked links unless allowed."
        decision.fix_hint = "Turn Link Shield off, allow the invite code, or allow this channel/role/user."
    elif protected_active:
        decision.action = "delete"
        decision.feature_owner = "Protected Bot/Channel Invite Rule"
        decision.rule_id = "protected_poster_invite_rule"
        decision.reason = "A protected bot/channel invite rule explicitly matched this poster or channel."
        decision.fix_hint = "Turn off Protected Poster Invite Rule or remove this bot/channel from the protected list."
    elif spam_enabled and spam_burst and decision.spam_invite_burst_action == "delete_on_burst":
        decision.action = "delete"
        decision.feature_owner = "Spam Guard Invite Burst Protection"
        decision.rule_id = "spam_invite_burst_delete"
        decision.reason = "Spam Guard detected a repeated invite burst and invite-burst action is set to delete."
        decision.fix_hint = "Set Spam Guard invite-burst action to observe/log-only or raise the burst threshold."
    elif spam_enabled and spam_burst and decision.spam_invite_burst_action == "warn":
        decision.action = "warn"
        decision.feature_owner = "Spam Guard Invite Burst Protection"
        decision.rule_id = "spam_invite_burst_warn"
        decision.reason = "Spam Guard detected a repeated invite burst and invite-burst action is set to warn."
        decision.fix_hint = "Set Spam Guard invite-burst action to observe/log-only or raise the burst threshold."
    else:
        decision.action = "log_only"
        decision.feature_owner = "Invite Policy"
        decision.rule_id = "no_delete_feature_enabled"
        decision.reason = (
            "Discord invite was detected, but no enabled feature is allowed to delete it. "
            "Spam Guard alone does not delete a single invite link."
        )
        decision.fix_hint = "Enable Invite Shield for invite blocking, or configure Spam Guard invite-burst deletion explicitly."

    record_invite_decision(message, decision)
    return decision


async def delete_message_if_allowed(message: discord.Message, decision: InviteDecision) -> bool:
    decision.delete_attempted = True
    if not decision.should_delete:
        decision.delete_succeeded = False
        decision.delete_error = "policy_not_delete"
        record_invite_decision(message, decision)
        return False
    try:
        await message.delete()
        decision.delete_succeeded = True
        decision.delete_error = ""
        record_invite_decision(message, decision)
        return True
    except discord.NotFound:
        decision.delete_succeeded = False
        decision.delete_error = "not_found"
    except discord.Forbidden:
        decision.delete_succeeded = False
        decision.delete_error = "missing_manage_messages"
    except Exception as exc:
        decision.delete_succeeded = False
        decision.delete_error = type(exc).__name__
    record_invite_decision(message, decision)
    return False


def decision_summary(decision: InviteDecision) -> str:
    target_bits = []
    if decision.internal_codes:
        target_bits.append(f"internal={','.join(decision.internal_codes[:5])}")
    if decision.external_codes:
        target_bits.append(f"external={','.join(decision.external_codes[:5])}")
    if decision.unknown_codes:
        target_bits.append(f"unknown={','.join(decision.unknown_codes[:5])}")
    target_text = " • ".join(target_bits) or "targets=none"

    return (
        f"feature={decision.feature_owner} • rule={decision.rule_id} • action={decision.action}\n"
        f"guild_id={decision.guild_id} • config_guild_id={decision.config_guild_id} • source={decision.source}\n"
        f"{target_text}\n"
        f"reason={decision.reason}\n"
        f"fix={decision.fix_hint}"
    )


async def send_invite_decision_modlog(message: discord.Message, decision: InviteDecision) -> None:
    guild = getattr(message, "guild", None)
    if guild is None:
        return

    try:
        from stoney_verify.modlog import send_mod_log  # type: ignore

        maybe = send_mod_log(
            guild,
            f"🛡️ Invite Policy: {decision.action.upper()}",
            decision_summary(decision),
        )
        if hasattr(maybe, "__await__"):
            await maybe
        return
    except Exception:
        pass

    try:
        from stoney_verify import spam_guard

        embed = discord.Embed(
            title=f"🛡️ Invite Policy: {decision.action.upper()}",
            description=decision.reason[:1024],
            color=discord.Color.red() if decision.should_delete else discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Feature / rule", value=f"`{decision.feature_owner}`\n`{decision.rule_id}`", inline=False)
        embed.add_field(name="Action", value=f"`{decision.action}`", inline=True)
        embed.add_field(name="Guild config used", value=f"`{decision.config_guild_id}`", inline=True)
        embed.add_field(name="Invite codes", value=", ".join(f"`{c}`" for c in decision.codes[:10]) or "—", inline=False)
        embed.add_field(name="Targets", value=(f"Internal `{len(decision.internal_codes)}` • External `{len(decision.external_codes)}` • Unknown `{len(decision.unknown_codes)}`"), inline=False)
        embed.add_field(name="Fix", value=decision.fix_hint[:1024], inline=False)
        sender = getattr(spam_guard, "_send_modlog_embed", None)
        if callable(sender):
            await sender(guild, embed)
    except Exception:
        pass


async def scan_channel_invites(channel: Any, *, limit: int = 100, repost_mixed: bool = False, source: str = "scanner") -> dict[str, Any]:
    result: dict[str, Any] = {
        "checked": 0,
        "matched": 0,
        "allowed": 0,
        "deleted": 0,
        "failed": 0,
        "warning": None,
    }

    if not isinstance(channel, discord.TextChannel):
        result["warning"] = "This scan can only run in a text channel."
        return result

    me = channel.guild.me
    if me is None:
        result["warning"] = "Dank Shield could not resolve its bot member for permission checks."
        return result

    perms = channel.permissions_for(me)
    if not perms.read_message_history:
        result["warning"] = f"Dank Shield needs Read Message History in {channel.mention} to scan existing messages."
        return result
    if not perms.manage_messages:
        result["warning"] = f"Dank Shield needs Manage Messages in {channel.mention} to remove messages that policy approves for deletion."
        return result

    try:
        async for message in channel.history(limit=max(1, min(int(limit or 100), 250))):
            result["checked"] += 1
            try:
                if message.author == me:
                    continue
                codes = extract_invite_codes_from_message(message)
                if not codes:
                    continue
                result["matched"] += 1
                decision = await decide_invite_message(message, source=source)
                if not decision.should_delete:
                    result["allowed"] += 1
                    continue
                ok = await delete_message_if_allowed(message, decision)
                if ok:
                    result["deleted"] += 1
                    await send_invite_decision_modlog(message, decision)
                    if repost_mixed and decision.internal_codes:
                        try:
                            from stoney_verify.startup_guards.invite_shield_sanitize_shared import send_mixed_invite_sanitized_notice

                            await send_mixed_invite_sanitized_notice(
                                message,
                                kept_codes=list(decision.internal_codes),
                                removed_count=len(decision.blocked_codes),
                                source=source,
                            )
                        except Exception:
                            pass
                else:
                    result["failed"] += 1
            except discord.Forbidden:
                result["failed"] += 1
                result["warning"] = f"Discord denied deletion in {channel.mention}. Check Manage Messages and channel permission overrides."
                break
            except discord.NotFound:
                continue
            except Exception as exc:
                result["failed"] += 1
                if result.get("warning") is None:
                    result["warning"] = f"Some matched messages could not be processed in {channel.mention}: {type(exc).__name__}: {str(exc)[:150]}"
    except discord.Forbidden:
        result["warning"] = f"Dank Shield cannot read message history in {channel.mention}."
    except Exception as exc:
        result["warning"] = f"Scan failed in {channel.mention}: {type(exc).__name__}: {str(exc)[:170]}"

    return result


def policy_snapshot_text(cfg: Any, spam: Mapping[str, Any]) -> str:
    invite_shield = _cfg_bool(cfg, "automod_block_invites", False) or _setting_bool(spam, "invite_shield_enabled", False)
    link_shield = _cfg_bool(cfg, "automod_block_links", False)
    spam_on = _safe_bool(spam.get("enabled", spam.get("spam_blocker_enabled")), False)
    burst_action = _spam_invite_burst_action(spam) if spam_on else "disabled"
    protected_enabled = _protected_poster_rule_enabled(spam)
    protected_targets = bool(
        _ids(_setting(spam, "invite_hard_block_target_bot_ids", []))
        or _ids(_setting(spam, "invite_hard_block_target_channel_ids", []))
        or _setting_bool(spam, "invite_hard_block_target_all_bots", False)
    )

    delete_paths = []
    if invite_shield:
        delete_paths.append("Invite Shield")
    if link_shield:
        delete_paths.append("Link Shield")
    if protected_enabled and protected_targets:
        delete_paths.append("Protected Bot/Channel Invite Rule")
    if spam_on and burst_action == "delete_on_burst":
        delete_paths.append("Spam Guard invite burst")

    if delete_paths:
        invite_line = "⚠️ Can delete invites via: " + ", ".join(delete_paths)
    else:
        invite_line = "✅ Discord invites are not deleted unless a clear burst/delete feature is enabled."

    warning = ""
    if not invite_shield and not link_shield and (protected_enabled or burst_action == "delete_on_burst"):
        warning = "\n⚠️ Consistency warning: Invite Shield is OFF, but another invite delete path is enabled."

    return (
        "**Invite Link Blocking**\n"
        f"{'✅ ON' if invite_shield else '⚪ OFF'} — external Discord invites {'blocked' if invite_shield else 'allowed'}; same-server invites allowed by default.\n\n"
        "**Spam Guard Invite Burst Protection**\n"
        f"{'✅ ON' if spam_on else '⚪ OFF'} — burst action `{burst_action}`. A single invite link is never deleted by Spam Guard alone.\n\n"
        "**Link Shield Normal Links**\n"
        f"{'✅ ON' if link_shield else '⚪ OFF'} — normal URLs {'blocked' if link_shield else 'allowed'}.\n\n"
        "**Protected Bot/Channel Invite Rules**\n"
        f"{'✅ ON' if protected_enabled else '⚪ OFF'} — targets {'configured' if protected_targets else 'not configured'}.\n\n"
        f"{invite_line}{warning}"
    )[:1024]


__all__ = [
    "InviteDecision",
    "INVITE_RE",
    "clean_invite_text",
    "decision_summary",
    "decide_invite_message",
    "delete_message_if_allowed",
    "extract_invite_codes_from_message",
    "extract_invite_codes_from_text",
    "get_last_invite_decision",
    "has_discord_invite",
    "invalidate_invite_policy",
    "load_invite_policy",
    "message_text",
    "normalize_invite_code",
    "policy_snapshot_text",
    "record_invite_decision",
    "scan_channel_invites",
    "send_invite_decision_modlog",
]
