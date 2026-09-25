from __future__ import annotations

"""Live Discord channel counters for auditable Dank Shield server statistics.

The display intentionally uses only durable, auditable actions or authoritative
current state that Dank Shield can prove. It does not invent estimates such as
"users protected" or "raids prevented".

The public display is opt-in per guild. When enabled, Dank Shield creates a visible
category containing locked voice channels, matching the common Discord server-stats
pattern. Members can see the counters but cannot connect to them.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

import discord
from discord.ext import tasks

from .globals import bot, get_supabase
from .guild_config import (
    GUILD_CONFIG_TABLE,
    clear_guild_config_keys,
    get_guild_config,
    upsert_guild_config,
)

SECURITY_STATS_CATEGORY_NAME = "🛡️ DANK SHIELD STATS"
SECURITY_STATS_ENABLED_KEY = "security_stats_display_enabled"
SECURITY_STATS_CATEGORY_ID_KEY = "security_stats_category_id"
SECURITY_STATS_CHANNEL_IDS_KEY = "security_stats_channel_ids"
SECURITY_STATS_COUNTS_KEY = "security_stats_counts"
SECURITY_STATS_CATEGORY_NAME_KEY = "security_stats_category_name"
SECURITY_STATS_VISIBLE_KEYS_KEY = "security_stats_visible_keys"
SECURITY_STATS_CUSTOM_LABELS_KEY = "security_stats_custom_labels"
SECURITY_STATS_FORMAT_OVERRIDES_KEY = "security_stats_format_overrides"
SECURITY_STATS_INHERIT_DESIGN_KEY = "security_stats_inherit_server_design"
SECURITY_STATS_NUMBER_STYLE_KEY = "security_stats_number_style"
SECURITY_STATS_PLACEMENT_KEY = "security_stats_category_placement"

SECURITY_STATS_REFRESH_MIN_SECONDS = 9 * 60

DEFAULT_SECURITY_STATS: Dict[str, int] = {
    "spam_blocked": 0,
    "invites_blocked": 0,
    "timeouts_issued": 0,
    "quarantines": 0,
}

DEFAULT_TICKET_STATUS_COUNTS: Dict[str, int] = {
    "open_tickets": 0,
    "claimed_tickets": 0,
    "closed_tickets": 0,
}

@dataclass(frozen=True)
class SecurityStatMetric:
    key: str
    title: str
    icon: str
    label: str
    provider: str
    source: str
    required_intents: tuple[str, ...] = ()
    privileged_intents: tuple[str, ...] = ()
    required_permissions: tuple[str, ...] = ()
    value_kind: str = "count"


# The public picker is generated from this registry. Adding a row here without
# wiring a real provider into _metric_values() is a regression, not a fake zero.
SECURITY_STATS_METRICS: Dict[str, SecurityStatMetric] = {
    "status": SecurityStatMetric("status", "SpamGuard status", "🛡️", "SpamGuard", "spamguard_status", "Dank Shield SpamGuard settings", value_kind="status"),
    "members": SecurityStatMetric("members", "Member count", "👥", "Members", "guild_member_count", "Discord GUILD_CREATE member_count", required_intents=("guilds",)),
    "spam_blocked": SecurityStatMetric("spam_blocked", "Spam blocked", "🚫", "Spam Blocked", "durable_counter", "Dank Shield audited SpamGuard actions"),
    "invites_blocked": SecurityStatMetric("invites_blocked", "Invites blocked", "🔗", "Invites Blocked", "invite_counter", "Dank Shield durable invite decisions"),
    "timeouts_issued": SecurityStatMetric("timeouts_issued", "Timeouts issued", "⏱️", "Timeouts Issued", "durable_counter", "Dank Shield audited moderation actions"),
    "quarantines": SecurityStatMetric("quarantines", "Quarantined", "☣️", "Quarantined", "durable_counter", "Dank Shield audited quarantine actions"),
    "open_tickets": SecurityStatMetric("open_tickets", "Open tickets", "🎫", "Open Tickets", "ticket_status", "Dank Shield ticket records + live-channel safety floor"),
    "claimed_tickets": SecurityStatMetric("claimed_tickets", "Claimed tickets", "🙋", "Claimed Tickets", "ticket_status", "Dank Shield ticket records"),
    "closed_tickets": SecurityStatMetric("closed_tickets", "Closed tickets", "✅", "Closed Tickets", "ticket_status", "Dank Shield ticket records"),
}

_SUPPORTED_METRIC_PROVIDERS = {"spamguard_status", "guild_member_count", "durable_counter", "invite_counter", "ticket_status"}
if any(metric.provider not in _SUPPORTED_METRIC_PROVIDERS for metric in SECURITY_STATS_METRICS.values()):
    raise RuntimeError("Server Stats metric registry contains an unimplemented provider.")

STAT_CHANNEL_PREFIXES: Dict[str, str] = {
    key: f"{metric.icon} {metric.label}:"
    for key, metric in SECURITY_STATS_METRICS.items()
}
DEFAULT_SECURITY_STATS_VISIBLE_KEYS = tuple(SECURITY_STATS_METRICS)
DEFAULT_SECURITY_STATS_LABELS: Dict[str, str] = {
    key: f"{metric.icon} {metric.label}"
    for key, metric in SECURITY_STATS_METRICS.items()
}
SECURITY_STATS_NUMBER_STYLES = {"compact", "exact"}
SECURITY_STATS_PLACEMENTS = {"top", "keep", "bottom"}
SECURITY_STATS_VALUE_TOKEN = "{value}"

_STATS_LOCKS: Dict[int, asyncio.Lock] = {}
_DISPLAY_LOCKS: Dict[int, asyncio.Lock] = {}
_LAST_REFRESH_AT: Dict[int, float] = {}
_ACTIVE_DISPLAY_GUILDS: set[int] = set()
_EVENT_REFRESH_TASKS: Dict[int, asyncio.Task] = {}
_LAST_EVENT_REFRESH_AT: Dict[int, float] = {}
_EVENT_REFRESH_MIN_SECONDS = 15.0
_STATS_DISCOVERY_BATCH_SIZE = 200
_TICKET_STATS_PAGE_SIZE = 500
_TICKET_STATS_SELECT_COLUMNS: Optional[str] = None
_LAST_SPAM_GUARD_ENABLED: Dict[int, bool] = {}
_LAST_TICKET_QUERY_ERROR: Dict[int, str] = {}


def _lock_for(store: Dict[int, asyncio.Lock], guild_id: int) -> asyncio.Lock:
    gid = int(guild_id)
    found = store.get(gid)
    if found is None:
        found = asyncio.Lock()
        store[gid] = found
    return found


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disabled"}:
        return False
    return bool(default)


def _mapping(value: Any) -> Dict[str, Any]:
    try:
        if isinstance(value, Mapping):
            return dict(value)
    except Exception:
        pass
    return {}


def security_stat_metric(key: str) -> SecurityStatMetric:
    clean = str(key or "").strip()
    metric = SECURITY_STATS_METRICS.get(clean)
    if metric is None:
        raise KeyError(f"Unknown Server Stats metric: {clean}")
    return metric


def security_stat_metric_capability(key: str, *, guild: Optional[discord.Guild] = None) -> Dict[str, Any]:
    metric = security_stat_metric(key)
    missing_intents: list[str] = []
    missing_permissions: list[str] = []

    intents = getattr(bot, "intents", None)
    for intent in (*metric.required_intents, *metric.privileged_intents):
        if intents is not None and not bool(getattr(intents, intent, False)):
            missing_intents.append(intent)

    if guild is not None and metric.required_permissions:
        me = getattr(guild, "me", None)
        perms = getattr(me, "guild_permissions", None)
        for permission in metric.required_permissions:
            if perms is None or not bool(getattr(perms, permission, False)):
                missing_permissions.append(permission)

    return {
        "key": metric.key,
        "title": metric.title,
        "provider": metric.provider,
        "source": metric.source,
        "required_intents": metric.required_intents,
        "privileged_intents": metric.privileged_intents,
        "required_permissions": metric.required_permissions,
        "missing_intents": tuple(missing_intents),
        "missing_permissions": tuple(missing_permissions),
        "available": not missing_intents and not missing_permissions,
    }


def security_stat_metric_description(key: str) -> str:
    metric = security_stat_metric(key)
    requirement = ""
    if metric.privileged_intents:
        requirement = " • privileged intent: " + ", ".join(metric.privileged_intents)
    elif metric.required_intents:
        requirement = " • intent: " + ", ".join(metric.required_intents)
    elif metric.required_permissions:
        requirement = " • permission: " + ", ".join(metric.required_permissions)
    return f"{metric.source}{requirement}"[:100]


def _clean_format_piece(value: Any, *, fallback: str = "", limit: int = 72) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if not text:
        text = str(fallback)
    return text[: max(0, int(limit))]


def _normalize_value_template(value: Any, *, fallback: str = SECURITY_STATS_VALUE_TOKEN) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if not text:
        text = fallback
    if text.count(SECURITY_STATS_VALUE_TOKEN) != 1:
        return fallback
    return text[:32]


def _raw_stat_format_overrides(cfg: Any) -> Dict[str, Dict[str, str]]:
    try:
        raw = _mapping(cfg.get(SECURITY_STATS_FORMAT_OVERRIDES_KEY, {}))
    except Exception:
        raw = {}
    out: Dict[str, Dict[str, str]] = {}
    for key, metric in SECURITY_STATS_METRICS.items():
        row = _mapping(raw.get(key))
        if not row:
            continue
        separator = str(row.get("separator") if row.get("separator") is not None else ": ")
        separator = separator.replace("\r", " ").replace("\n", " ")[:16]
        out[key] = {
            "icon": _clean_format_piece(row.get("icon"), fallback=metric.icon, limit=24),
            "label": _clean_format_piece(row.get("label"), fallback=metric.label, limit=72),
            "separator": separator,
            "value_template": _normalize_value_template(row.get("value_template")),
        }
    return out

def normalize_security_stats(value: Any) -> Dict[str, int]:
    raw = _mapping(value)
    normalized = dict(DEFAULT_SECURITY_STATS)
    for key in normalized:
        normalized[key] = max(0, _safe_int(raw.get(key), 0))
    return normalized


def format_security_stat_count(value: Any) -> str:
    """Compact a non-negative counter while keeping small values exact."""
    number = max(0, _safe_int(value, 0))
    if number < 1_000:
        return str(number)

    units = ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K"))
    for divisor, suffix in units:
        if number < divisor:
            continue
        scaled = number / divisor
        if scaled < 10:
            text = f"{scaled:.2f}"
        elif scaled < 100:
            text = f"{scaled:.1f}"
        else:
            text = f"{scaled:.0f}"
        return f"{text.rstrip('0').rstrip('.')}{suffix}"
    return str(number)


def _clean_channel_text(value: Any, *, fallback: str, limit: int = 100) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split()).strip()
    if not text:
        text = str(fallback)
    return text[: max(1, int(limit))]


def security_stats_preferences(cfg: Any) -> Dict[str, Any]:
    try:
        raw_category = cfg.get(SECURITY_STATS_CATEGORY_NAME_KEY, SECURITY_STATS_CATEGORY_NAME)
    except Exception:
        raw_category = SECURITY_STATS_CATEGORY_NAME
    category_name = _clean_channel_text(
        raw_category,
        fallback=SECURITY_STATS_CATEGORY_NAME,
        limit=100,
    )

    try:
        raw_visible = cfg.get(SECURITY_STATS_VISIBLE_KEYS_KEY, DEFAULT_SECURITY_STATS_VISIBLE_KEYS)
    except Exception:
        raw_visible = DEFAULT_SECURITY_STATS_VISIBLE_KEYS
    if isinstance(raw_visible, str):
        raw_visible = [item.strip() for item in raw_visible.split(",")]
    try:
        selected = {str(item).strip() for item in list(raw_visible or [])}
    except Exception:
        selected = set(DEFAULT_SECURITY_STATS_VISIBLE_KEYS)
    visible_keys = tuple(key for key in DEFAULT_SECURITY_STATS_VISIBLE_KEYS if key in selected)
    if not visible_keys:
        visible_keys = DEFAULT_SECURITY_STATS_VISIBLE_KEYS

    try:
        raw_labels = _mapping(cfg.get(SECURITY_STATS_CUSTOM_LABELS_KEY, {}))
    except Exception:
        raw_labels = {}
    labels: Dict[str, str] = {}
    for key in DEFAULT_SECURITY_STATS_VISIBLE_KEYS:
        if key not in raw_labels:
            continue
        cleaned = _clean_channel_text(
            raw_labels.get(key),
            fallback=DEFAULT_SECURITY_STATS_LABELS[key],
            limit=72,
        ).rstrip(":").strip()
        if cleaned and cleaned != DEFAULT_SECURITY_STATS_LABELS[key]:
            labels[key] = cleaned

    formats = _raw_stat_format_overrides(cfg)

    try:
        inherit_design = _safe_bool(cfg.get(SECURITY_STATS_INHERIT_DESIGN_KEY), False)
    except Exception:
        inherit_design = False

    try:
        design_options = _mapping(cfg.get("server_design_studio_options", {}))
    except Exception:
        design_options = {}

    try:
        number_style = str(cfg.get(SECURITY_STATS_NUMBER_STYLE_KEY, "compact") or "compact").strip().lower()
    except Exception:
        number_style = "compact"
    if number_style not in SECURITY_STATS_NUMBER_STYLES:
        number_style = "compact"

    try:
        placement = str(cfg.get(SECURITY_STATS_PLACEMENT_KEY, "top") or "top").strip().lower()
    except Exception:
        placement = "top"
    if placement not in SECURITY_STATS_PLACEMENTS:
        placement = "top"

    return {
        "category_name": category_name,
        "visible_keys": visible_keys,
        "labels": labels,
        "formats": formats,
        "inherit_design": inherit_design,
        "design_options": design_options,
        "number_style": number_style,
        "placement": placement,
    }


def _stat_label(preferences: Mapping[str, Any], key: str) -> str:
    metric = security_stat_metric(key)
    formats = _mapping(preferences.get("formats", {}))
    row = _mapping(formats.get(key))
    if row.get("label"):
        return _clean_format_piece(row.get("label"), fallback=metric.label, limit=72)
    labels = _mapping(preferences.get("labels", {}))
    if key in labels:
        legacy = _clean_channel_text(
            labels.get(key),
            fallback=DEFAULT_SECURITY_STATS_LABELS[key],
            limit=72,
        ).rstrip(":").strip()
        return legacy or metric.label
    return metric.label


def _design_context(preferences: Mapping[str, Any]) -> Dict[str, Any]:
    if not bool(preferences.get("inherit_design")):
        return {
            "enabled": False,
            "font": "normal",
            "separator": "",
            "strength": 0,
            "options": {},
        }
    options = _mapping(preferences.get("design_options", {}))
    try:
        from stoney_verify.services import server_design_plan_service as design_plan
        from stoney_verify.services import server_design_studio as design_studio

        theme_id = str(options.get("theme_id") or "gothic_clean")
        theme = design_studio.THEMES_BY_ID.get(
            theme_id,
            design_studio.THEMES_BY_ID["gothic_clean"],
        )
        try:
            strength = max(1, min(5, int(options.get("strength", 4) or 4)))
        except Exception:
            strength = 4
        font = str(options.get("font") or getattr(theme, "font", "normal") or "normal")
        font = font.lower().replace("-", "_")
        if font not in design_studio.DESIGN_FONT_STYLES:
            font = str(getattr(theme, "font", "normal") or "normal").lower().replace("-", "_")
        separator = ""
        if strength >= 2:
            separator_id = design_plan.effective_server_separator_id(options)
            separator_spec = design_studio.SEPARATORS_BY_ID.get(separator_id)
            separator = str(getattr(separator_spec, "value", "") or "")
        return {
            "enabled": True,
            "font": font if strength >= 3 else "normal",
            "separator": separator,
            "strength": strength,
            "options": options,
        }
    except Exception:
        return {
            "enabled": False,
            "font": "normal",
            "separator": "",
            "strength": 0,
            "options": options,
        }


def security_stats_category_display_name(preferences: Mapping[str, Any]) -> str:
    base = _clean_channel_text(
        preferences.get("category_name"),
        fallback=SECURITY_STATS_CATEGORY_NAME,
        limit=100,
    )
    context = _design_context(preferences)
    if not context["enabled"]:
        return base
    try:
        from stoney_verify.services import server_design_plan_service as design_plan
        from stoney_verify.services import server_design_studio as design_studio

        options = _mapping(context.get("options", {}))
        result = design_studio.build_styled_name(
            base,
            kind="category",
            theme_id=str(options.get("theme_id") or "gothic_clean"),
            strength=int(context.get("strength") or 4),
            icon_mode="keep_existing",
            separator_id=design_plan.effective_server_separator_id(options),
            category_frame_id=design_plan.effective_server_category_frame_id(options),
            font=str(context.get("font") or "normal"),
            exact_match=True,
        )
        if not result.blockers and result.after:
            return str(result.after)[:100]
    except Exception:
        pass
    return base


def _metric_format(preferences: Mapping[str, Any], key: str) -> Dict[str, str]:
    metric = security_stat_metric(key)
    formats = _mapping(preferences.get("formats", {}))
    row = _mapping(formats.get(key))
    labels = _mapping(preferences.get("labels", {}))

    if row:
        separator = str(row.get("separator") if row.get("separator") is not None else ": ")
        separator = separator.replace("\r", " ").replace("\n", " ")[:16]
        return {
            "icon": _clean_format_piece(row.get("icon"), fallback=metric.icon, limit=24),
            "label": _clean_format_piece(row.get("label"), fallback=metric.label, limit=72),
            "separator": separator,
            "value_template": _normalize_value_template(row.get("value_template")),
        }

    if key in labels:
        legacy = _clean_channel_text(
            labels.get(key),
            fallback=DEFAULT_SECURITY_STATS_LABELS[key],
            limit=72,
        ).rstrip(":").strip()
        return {
            "icon": "",
            "label": legacy,
            "separator": ": ",
            "value_template": SECURITY_STATS_VALUE_TOKEN,
        }

    return {
        "icon": metric.icon,
        "label": metric.label,
        "separator": ": ",
        "value_template": SECURITY_STATS_VALUE_TOKEN,
    }


def _styled_metric_label(preferences: Mapping[str, Any], label: str) -> str:
    context = _design_context(preferences)
    font = str(context.get("font") or "normal")
    if not context.get("enabled") or font == "normal":
        return label
    try:
        from stoney_verify.services import server_design_studio as design_studio

        styled, _subs = design_studio.transform_text_safe(
            label,
            font,
            fallback_order=design_studio.fallback_ladder(font),
        )
        return styled or label
    except Exception:
        return label


def _metric_name_head(preferences: Mapping[str, Any], key: str) -> str:
    row = _metric_format(preferences, key)
    context = _design_context(preferences)

    icon = row["icon"]
    label = _styled_metric_label(preferences, row["label"])
    explicit_format = bool(_mapping(preferences.get("formats", {})).get(key))
    if context.get("enabled") and not explicit_format:
        icon_mode = str(_mapping(context.get("options", {})).get("icon_mode") or "replace_missing")
        if icon_mode == "clear":
            icon = ""

    design_separator = str(context.get("separator") or "") if context.get("enabled") else ""
    if icon and design_separator and not explicit_format:
        return f"{icon} {design_separator} {label}".strip()
    if icon:
        return f"{icon} {label}".strip()
    return label.strip()


def security_stat_name_prefix(preferences: Mapping[str, Any], key: str) -> str:
    """Return the stable live-channel prefix before the changing value."""

    row = _metric_format(preferences, key)
    head = _metric_name_head(preferences, key)
    before_value = row["value_template"].split(SECURITY_STATS_VALUE_TOKEN, 1)[0]
    return f"{head}{row['separator']}{before_value}".strip()


def render_security_stat_name(
    preferences: Mapping[str, Any],
    key: str,
    value: Any,
) -> str:
    metric = security_stat_metric(key)
    row = _metric_format(preferences, key)
    context = _design_context(preferences)
    head = _metric_name_head(preferences, key)
    explicit_format = bool(_mapping(preferences.get("formats", {})).get(key))
    design_separator = str(context.get("separator") or "") if context.get("enabled") else ""

    rendered_value = row["value_template"].replace(SECURITY_STATS_VALUE_TOKEN, str(value), 1)
    name = f"{head}{row['separator']}{rendered_value}".strip()
    name = name.replace("\r", " ").replace("\n", " ").strip()

    if 1 <= len(name) <= 100:
        return name

    for cut in range(min(len(label), 72), 0, -1):
        short_label = label[:cut].rstrip()
        if icon and design_separator and not explicit_format:
            short_head = f"{icon} {design_separator} {short_label}".strip()
        elif icon:
            short_head = f"{icon} {short_label}".strip()
        else:
            short_head = short_label
        candidate = f"{short_head}{row['separator']}{rendered_value}".strip()
        if 1 <= len(candidate) <= 100:
            return candidate

    fallback = f"{metric.icon} {metric.label}: {value}".strip()
    return fallback[:100] or metric.label[:100]


def security_stat_format_state(
    preferences: Mapping[str, Any],
    key: str,
) -> Dict[str, str]:
    """Return the effective editable shell for one metric."""

    return dict(_metric_format(preferences, key))


def security_stat_format_preview(preferences: Mapping[str, Any], key: str) -> str:
    metric = security_stat_metric(key)
    sample = "ONLINE" if metric.value_kind == "status" else "0"
    return render_security_stat_name(preferences, key, sample)


def validate_security_stat_format(
    key: str,
    raw: Mapping[str, Any],
    *,
    preferences: Optional[Mapping[str, Any]] = None,
) -> Tuple[bool, str, Dict[str, str]]:
    metric = security_stat_metric(key)
    icon = _clean_format_piece(raw.get("icon"), fallback=metric.icon, limit=24)
    label = _clean_format_piece(raw.get("label"), fallback=metric.label, limit=72)
    separator = str(raw.get("separator") if raw.get("separator") is not None else ": ")
    separator = separator.replace("\r", " ").replace("\n", " ")[:16]
    value_template = str(raw.get("value_template") or SECURITY_STATS_VALUE_TOKEN).strip()
    if value_template.count(SECURITY_STATS_VALUE_TOKEN) != 1:
        return False, "Value format must contain exactly one `{value}` token.", {}
    if len(value_template) > 32:
        return False, "Value format is too long. Keep it at 32 characters or fewer.", {}

    cleaned = {
        "icon": icon,
        "label": label,
        "separator": separator,
        "value_template": value_template,
    }
    test_preferences = dict(preferences or {})
    formats = _mapping(test_preferences.get("formats", {}))
    formats[key] = cleaned
    test_preferences["formats"] = formats
    preview = security_stat_format_preview(test_preferences, key)
    if not (1 <= len(preview) <= 100):
        return False, "The rendered Discord channel name must be 1–100 characters.", {}
    return True, preview, cleaned

def _format_stat_count(value: Any, number_style: str) -> str:
    if str(number_style or "").strip().lower() == "exact":
        return str(max(0, _safe_int(value, 0)))
    return format_security_stat_count(value)


def _format_live_count(value: Optional[int], number_style: str = "compact") -> str:
    if value is None:
        return "N/A"
    return _format_stat_count(value, number_style)


def _guild_member_count(guild: discord.Guild) -> Optional[int]:
    """Return Discord's guild member total without trusting a partial member cache."""
    try:
        raw = getattr(guild, "member_count", None)
        if raw is not None and not isinstance(raw, bool):
            count = int(raw)
            if count >= 0:
                return count
    except Exception:
        pass

    try:
        if bool(getattr(guild, "chunked", False)):
            return max(0, len(list(getattr(guild, "members", []) or [])))
    except Exception:
        pass
    return None


def _normalize_ticket_status_counts(value: Any) -> Optional[Dict[str, int]]:
    if value is None:
        return None
    raw = _mapping(value)
    normalized = {
        key: max(0, _safe_int(raw.get(key), 0))
        for key in DEFAULT_TICKET_STATUS_COUNTS
    }
    # A claimed ticket is still active. Never publish the impossible state where
    # the claimed subset is larger than the active/open total.
    normalized["open_tickets"] = max(
        normalized["open_tickets"],
        normalized["claimed_tickets"],
    )
    return normalized


def _ticket_has_assignee(row: Mapping[str, Any]) -> bool:
    for key in ("claimed_by", "assigned_to"):
        text = str(row.get(key) or "").strip().lower()
        if text and text not in {"0", "none", "null"}:
            return True
    return False


def _ticket_status_counts_from_rows(rows: Any) -> Dict[str, int]:
    """Count active tickets and the claimed subset from stored lifecycle rows."""
    counts = dict(DEFAULT_TICKET_STATUS_COUNTS)
    try:
        iterable = list(rows or [])
    except Exception:
        return counts

    for row in iterable:
        if not isinstance(row, Mapping):
            continue

        status = str(row.get("status") or "").strip().lower()
        if status in {"active", "reopened"}:
            status = "open"

        if status in {"open", "claimed"}:
            # Claimed tickets remain open; Claimed is a subset, not a separate
            # lifecycle bucket that should disappear from the active total.
            counts["open_tickets"] += 1
            if status == "claimed" or _ticket_has_assignee(row):
                counts["claimed_tickets"] += 1
        elif status == "closed":
            counts["closed_tickets"] += 1

    return counts


def _ticket_stats_schema_error(exc: BaseException) -> bool:
    text = repr(exc or "").lower()
    return any(
        marker in text
        for marker in (
            "pgrst204",
            "schema cache",
            "does not exist",
            "undefined column",
            "column",
        )
    ) and any(name in text for name in ("claimed_by", "assigned_to"))


def _ticket_stats_select_candidates() -> tuple[str, ...]:
    preferred = (
        "status,claimed_by,assigned_to",
        "status,claimed_by",
        "status,assigned_to",
        "status",
    )
    cached = str(_TICKET_STATS_SELECT_COLUMNS or "").strip()
    if not cached or cached not in preferred:
        return preferred
    return (cached, *tuple(item for item in preferred if item != cached))


def _query_ticket_status_counts_sync(guild_id: int) -> Optional[Dict[str, int]]:
    """Read every ticket lifecycle row with schema-compatible pagination."""
    global _TICKET_STATS_SELECT_COLUMNS

    sb = get_supabase()
    if sb is None:
        return None

    gid = str(int(guild_id))
    page_size = max(1, int(_TICKET_STATS_PAGE_SIZE))
    last_schema_error: Optional[BaseException] = None

    for columns in _ticket_stats_select_candidates():
        rows: list[Dict[str, Any]] = []
        try:
            for page_index in range(200):
                start = page_index * page_size
                end = start + page_size - 1
                query = (
                    sb.table("tickets")
                    .select(columns)
                    .eq("guild_id", gid)
                )
                range_method = getattr(query, "range", None)
                if callable(range_method):
                    response = range_method(start, end).execute()
                else:
                    # Compatibility with older/minimal PostgREST clients. These
                    # clients can still provide an authoritative single response,
                    # but cannot be paged by the caller.
                    if page_index > 0:
                        break
                    response = query.execute()
                raw_page = getattr(response, "data", None)
                if raw_page is None:
                    return None
                page = [dict(row) for row in list(raw_page or []) if isinstance(row, Mapping)]
                rows.extend(page)
                if len(page) < page_size:
                    break
            else:
                print(
                    f"⚠️ security_stats ticket query page cap reached guild={gid} "
                    f"rows={len(rows)}"
                )

            previous = _TICKET_STATS_SELECT_COLUMNS
            _TICKET_STATS_SELECT_COLUMNS = columns
            if previous != columns and columns != "status,claimed_by,assigned_to":
                print(
                    f"⚠️ security_stats ticket query compatibility mode "
                    f"columns={columns}"
                )
            return _ticket_status_counts_from_rows(rows)
        except Exception as exc:
            if _ticket_stats_schema_error(exc):
                last_schema_error = exc
                if _TICKET_STATS_SELECT_COLUMNS == columns:
                    _TICKET_STATS_SELECT_COLUMNS = None
                continue
            raise

    if last_schema_error is not None:
        raise last_schema_error
    return None


async def _ticket_status_counts(guild_id: int) -> Optional[Dict[str, int]]:
    gid = int(guild_id)
    try:
        counts = await asyncio.to_thread(_query_ticket_status_counts_sync, gid)
        _LAST_TICKET_QUERY_ERROR.pop(gid, None)
        return _normalize_ticket_status_counts(counts)
    except Exception as exc:
        marker = f"{type(exc).__name__}: {str(exc)[:240]}"
        if _LAST_TICKET_QUERY_ERROR.get(gid) != marker:
            _LAST_TICKET_QUERY_ERROR[gid] = marker
            print(f"⚠️ security_stats ticket query failed guild={gid} error={marker}")
        return None


async def _spam_guard_enabled(guild_id: int) -> Optional[bool]:
    gid = int(guild_id)
    try:
        from .spam_guard import get_spam_settings

        spam_settings = await get_spam_settings(gid)
        enabled = bool(spam_settings.get("enabled"))
        _LAST_SPAM_GUARD_ENABLED[gid] = enabled
        return enabled
    except Exception as exc:
        cached = _LAST_SPAM_GUARD_ENABLED.get(gid)
        print(
            f"⚠️ security_stats SpamGuard state read failed guild={gid} "
            f"using={'cached' if cached is not None else 'unknown'} "
            f"error={type(exc).__name__}"
        )
        return cached


def _metric_values(
    *,
    spam_guard_enabled: Optional[bool],
    counts: Mapping[str, int],
    member_count: Optional[int] = None,
    ticket_counts: Optional[Mapping[str, int]] = None,
    number_style: str = "compact",
) -> Dict[str, str]:
    normalized = normalize_security_stats(counts)
    tickets = _normalize_ticket_status_counts(ticket_counts)
    spam_status = (
        "ONLINE" if spam_guard_enabled is True
        else "OFFLINE" if spam_guard_enabled is False
        else "UNKNOWN"
    )
    values = {
        "status": spam_status,
        "members": _format_live_count(member_count, number_style),
        "spam_blocked": _format_stat_count(normalized["spam_blocked"], number_style),
        "invites_blocked": _format_stat_count(normalized["invites_blocked"], number_style),
        "timeouts_issued": _format_stat_count(normalized["timeouts_issued"], number_style),
        "quarantines": _format_stat_count(normalized["quarantines"], number_style),
        "open_tickets": _format_live_count(None if tickets is None else tickets["open_tickets"], number_style),
        "claimed_tickets": _format_live_count(None if tickets is None else tickets["claimed_tickets"], number_style),
        "closed_tickets": _format_live_count(None if tickets is None else tickets["closed_tickets"], number_style),
    }
    missing = set(SECURITY_STATS_METRICS) - set(values)
    extra = set(values) - set(SECURITY_STATS_METRICS)
    if missing or extra:
        raise RuntimeError(f"Server Stats provider registry mismatch missing={sorted(missing)} extra={sorted(extra)}")
    return values


def _display_names(
    *,
    spam_guard_enabled: Optional[bool],
    counts: Mapping[str, int],
    member_count: Optional[int] = None,
    ticket_counts: Optional[Mapping[str, int]] = None,
    preferences: Optional[Mapping[str, Any]] = None,
) -> Dict[str, str]:
    prefs = dict(preferences or {})
    number_style = str(prefs.get("number_style") or "compact")
    values = _metric_values(
        spam_guard_enabled=spam_guard_enabled,
        counts=counts,
        member_count=member_count,
        ticket_counts=ticket_counts,
        number_style=number_style,
    )
    return {
        key: render_security_stat_name(prefs, key, values[key])
        for key in SECURITY_STATS_METRICS
    }


def _live_open_ticket_count(guild: discord.Guild) -> Optional[int]:
    """Count visible active ticket channels as a floor against false DB zeroes."""
    try:
        channels = list(getattr(guild, "text_channels", []) or [])
    except Exception:
        return None

    active = 0
    for channel in channels:
        try:
            name = str(getattr(channel, "name", "") or "").strip().lower()
            topic = str(getattr(channel, "topic", "") or "").strip().lower()
            category_name = str(
                getattr(getattr(channel, "category", None), "name", "") or ""
            ).strip().lower()

            ticketish = (
                name.startswith("ticket-")
                or name.startswith("closed-")
                or (
                    "ticket_number=" in topic
                    and any(owner_key in topic for owner_key in ("owner_id=", "user_id=", "requester_id="))
                )
            )
            if not ticketish:
                continue

            closed = (
                name.startswith("closed-")
                or "archive" in category_name
                or "archived" in category_name
                or "closed ticket" in category_name
            )
            if not closed:
                active += 1
        except Exception:
            continue
    return active


async def _display_names_for_guild(
    guild: discord.Guild,
    *,
    counts: Mapping[str, int],
    preferences: Optional[Mapping[str, Any]] = None,
) -> Dict[str, str]:
    gid = int(guild.id)
    spam_enabled, ticket_counts = await asyncio.gather(
        _spam_guard_enabled(gid),
        _ticket_status_counts(gid),
    )

    tickets = _normalize_ticket_status_counts(ticket_counts)
    live_open = _live_open_ticket_count(guild)
    if tickets is not None:
        db_open = int(tickets["open_tickets"])
        tickets["open_tickets"] = max(
            db_open,
            int(tickets["claimed_tickets"]),
            int(live_open or 0),
        )
        if live_open is not None and live_open > db_open:
            print(
                f"⚠️ security_stats active ticket mismatch guild={gid} "
                f"db_open={db_open} live_channels={live_open}; using live floor"
            )

    display_counts = normalize_security_stats(counts)
    try:
        from .durable_invite_stats import read_invites_blocked

        durable_invites = await read_invites_blocked(gid)
        if durable_invites is not None:
            display_counts["invites_blocked"] = max(
                int(display_counts["invites_blocked"]),
                int(durable_invites),
            )
    except Exception as exc:
        print(
            f"⚠️ security_stats durable invite count read failed guild={gid} "
            f"error={type(exc).__name__}"
        )

    return _display_names(
        spam_guard_enabled=spam_enabled,
        counts=display_counts,
        member_count=_guild_member_count(guild),
        ticket_counts=tickets,
        preferences=preferences,
    )


def _saved_channel_ids(cfg: Any) -> Dict[str, int]:
    raw = _mapping(getattr(cfg, "get", lambda *_args, **_kwargs: {}) (SECURITY_STATS_CHANNEL_IDS_KEY, {}))
    return {
        key: _safe_int(raw.get(key), 0)
        for key in STAT_CHANNEL_PREFIXES
    }


def _stats_enabled(cfg: Any) -> bool:
    try:
        return _safe_bool(cfg.get(SECURITY_STATS_ENABLED_KEY), False)
    except Exception:
        return False


def _stats_counts(cfg: Any) -> Dict[str, int]:
    try:
        return normalize_security_stats(cfg.get(SECURITY_STATS_COUNTS_KEY, {}))
    except Exception:
        return dict(DEFAULT_SECURITY_STATS)


def _category_has_stats_evidence(
    category: discord.CategoryChannel,
    preferences: Mapping[str, Any],
) -> bool:
    try:
        for channel in list(getattr(category, "voice_channels", []) or []):
            name = str(getattr(channel, "name", "") or "")
            for key, default_prefix in STAT_CHANNEL_PREFIXES.items():
                prefixes = [
                    default_prefix,
                    f"{_stat_label(preferences, key)}:",
                    security_stat_name_prefix(preferences, key),
                ]
                if any(name.startswith(prefix) for prefix in prefixes):
                    return True
    except Exception:
        return False
    return False


def _find_owned_category(guild: discord.Guild, cfg: Any) -> Optional[discord.CategoryChannel]:
    try:
        category_id = _safe_int(cfg.get(SECURITY_STATS_CATEGORY_ID_KEY), 0)
    except Exception:
        category_id = 0

    if category_id > 0:
        found = guild.get_channel(category_id)
        if isinstance(found, discord.CategoryChannel):
            return found

    # A saved stats-channel ID is stronger ownership evidence than a category
    # name and survives a category rename.
    for saved_id in _saved_channel_ids(cfg).values():
        if saved_id <= 0:
            continue
        channel = guild.get_channel(saved_id)
        if not isinstance(channel, discord.VoiceChannel):
            continue
        parent = getattr(channel, "category", None)
        if isinstance(parent, discord.CategoryChannel):
            return parent
        parent_id = _safe_int(getattr(channel, "category_id", 0), 0)
        if parent_id > 0:
            parent = guild.get_channel(parent_id)
            if isinstance(parent, discord.CategoryChannel):
                return parent

    preferences = security_stats_preferences(cfg)
    desired_name = str(preferences["category_name"])
    styled_name = security_stats_category_display_name(preferences)
    accepted_names = {SECURITY_STATS_CATEGORY_NAME, desired_name, styled_name}
    for category in list(getattr(guild, "categories", []) or []):
        name = str(getattr(category, "name", "") or "")
        if (
            name in accepted_names
            and _category_has_stats_evidence(category, preferences)
        ):
            return category
    return None


def _find_existing_stat_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    *,
    key: str,
    saved_id: int,
    preferences: Optional[Mapping[str, Any]] = None,
) -> Optional[discord.VoiceChannel]:
    if saved_id > 0:
        found = guild.get_channel(saved_id)
        if isinstance(found, discord.VoiceChannel) and int(getattr(found, "category_id", 0) or 0) == int(category.id):
            return found

    prefixes = [STAT_CHANNEL_PREFIXES[key]]
    if preferences is not None:
        custom_prefix = f"{_stat_label(preferences, key)}:"
        rendered_prefix = security_stat_name_prefix(preferences, key)
        for candidate in (custom_prefix, rendered_prefix):
            if candidate and candidate not in prefixes:
                prefixes.append(candidate)
    for channel in list(getattr(category, "voice_channels", []) or []):
        name = str(getattr(channel, "name", "") or "")
        if any(name.startswith(prefix) for prefix in prefixes):
            return channel
    return None


async def _apply_category_preferences(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    preferences: Mapping[str, Any],
) -> None:
    desired_name = security_stats_category_display_name(preferences)
    if str(getattr(category, "name", "") or "") != desired_name:
        await category.edit(name=desired_name, reason="Apply Dank Shield server stats category name")

    placement = str(preferences.get("placement") or "top")
    current_position = _safe_int(getattr(category, "position", -1), -1)
    if placement == "top":
        if current_position != 0:
            await category.edit(position=0, reason="Place Dank Shield server stats at the top")
    elif placement == "bottom":
        categories = list(getattr(guild, "categories", []) or [])
        target = max(0, len(categories) - 1)
        if current_position != target:
            await category.edit(position=target, reason="Place Dank Shield server stats at the bottom")


async def _remove_hidden_stat_channel(
    channel: Optional[discord.VoiceChannel],
    *,
    key: str,
) -> bool:
    if channel is None:
        return False
    try:
        await channel.delete(reason=f"Hide Dank Shield server stat: {key}")
        return True
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(
            f"⚠️ security_stats hide channel failed key={key} "
            f"error={type(exc).__name__}"
        )
        return False


async def _run_coalesced_security_stats_refresh(guild_id: int) -> None:
    gid = int(guild_id)
    try:
        now = time.monotonic()
        remaining = _EVENT_REFRESH_MIN_SECONDS - (
            now - float(_LAST_EVENT_REFRESH_AT.get(gid, 0.0))
        )
        if remaining > 0:
            await asyncio.sleep(remaining)

        try:
            guild = bot.get_guild(gid)
        except Exception:
            guild = None
        if guild is None or gid not in _ACTIVE_DISPLAY_GUILDS:
            return

        await refresh_security_stats_display(guild, force=True)
        _LAST_EVENT_REFRESH_AT[gid] = time.monotonic()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(
            f"⚠️ security_stats event refresh failed guild={gid} "
            f"error={type(exc).__name__}"
        )
    finally:
        current = _EVENT_REFRESH_TASKS.get(gid)
        if current is asyncio.current_task():
            _EVENT_REFRESH_TASKS.pop(gid, None)


def _schedule_security_stats_refresh(guild_id: int) -> None:
    gid = int(guild_id)
    if gid <= 0 or gid not in _ACTIVE_DISPLAY_GUILDS:
        return
    current = _EVENT_REFRESH_TASKS.get(gid)
    if current is not None and not current.done():
        return
    try:
        task = asyncio.create_task(
            _run_coalesced_security_stats_refresh(gid),
            name=f"security-stats-refresh-{gid}",
        )
    except RuntimeError:
        return
    _EVENT_REFRESH_TASKS[gid] = task


async def record_security_event(
    guild_id: int,
    *,
    spam_blocked: int = 0,
    invites_blocked: int = 0,
    timeouts_issued: int = 0,
    quarantines: int = 0,
) -> Dict[str, int]:
    """Persist actual protection actions for one guild.

    Counters are updated under a per-guild lock so concurrent moderation events do
    not race each other inside this process. Channel names are refreshed separately
    to avoid renaming Discord channels for every blocked message.
    """

    gid = int(guild_id)
    deltas = {
        "spam_blocked": max(0, _safe_int(spam_blocked, 0)),
        "invites_blocked": max(0, _safe_int(invites_blocked, 0)),
        "timeouts_issued": max(0, _safe_int(timeouts_issued, 0)),
        "quarantines": max(0, _safe_int(quarantines, 0)),
    }
    if gid <= 0 or not any(deltas.values()):
        return dict(DEFAULT_SECURITY_STATS)

    async with _lock_for(_STATS_LOCKS, gid):
        cfg = await get_guild_config(gid, refresh=True)
        if _stats_enabled(cfg):
            _ACTIVE_DISPLAY_GUILDS.add(gid)
        else:
            _ACTIVE_DISPLAY_GUILDS.discard(gid)
        counts = _stats_counts(cfg)
        for key, delta in deltas.items():
            counts[key] = max(0, int(counts.get(key, 0))) + int(delta)
        await upsert_guild_config(gid, {SECURITY_STATS_COUNTS_KEY: counts})
        _schedule_security_stats_refresh(gid)
        return counts


async def record_spam_guard_action(
    guild_id: int,
    *,
    deleted_messages: int,
    action_taken: str,
    quarantine_case: Optional[Mapping[str, Any]] = None,
) -> Dict[str, int]:
    """Translate a completed Spam Guard action into durable counters."""

    action = str(action_taken or "").strip().lower()
    case = _mapping(quarantine_case)
    timeout_count = 1 if action.startswith("timeout:") else 0
    quarantine_count = 1 if action.startswith("quarantine:") else 0
    if quarantine_count and _safe_bool(case.get("timeout_applied"), False):
        timeout_count = 1

    return await record_security_event(
        int(guild_id),
        spam_blocked=max(0, _safe_int(deleted_messages, 0)),
        timeouts_issued=timeout_count,
        quarantines=quarantine_count,
    )


async def ensure_security_stats_display(guild: discord.Guild) -> Tuple[bool, str]:
    """Create, repair, and apply the saved per-guild Server Stats display."""

    gid = int(guild.id)
    async with _lock_for(_DISPLAY_LOCKS, gid):
        me = guild.me
        if me is None:
            return False, "❌ Dank Shield could not resolve its server member permissions."

        perms = me.guild_permissions
        if not bool(getattr(perms, "manage_channels", False)):
            return False, "❌ Dank Shield needs **Manage Channels** to create and update Server Stats."
        if not bool(getattr(perms, "manage_roles", False)) and not bool(getattr(perms, "administrator", False)):
            return False, "❌ Dank Shield needs **Manage Roles** to keep Server Stats visible but non-joinable."

        cfg = await get_guild_config(gid, refresh=True)

        # Existing enabled installations remain visually unchanged unless the
        # owner opts into Design Sync. A newly enabled display inherits the
        # current saved Server Design by default.
        try:
            has_design_sync_choice = (
                isinstance(cfg, Mapping)
                and SECURITY_STATS_INHERIT_DESIGN_KEY in cfg
            )
        except Exception:
            has_design_sync_choice = False
        if not _stats_enabled(cfg) and not has_design_sync_choice:
            await upsert_guild_config(
                gid,
                {SECURITY_STATS_INHERIT_DESIGN_KEY: True},
            )
            if isinstance(cfg, Mapping):
                cfg = {
                    **dict(cfg),
                    SECURITY_STATS_INHERIT_DESIGN_KEY: True,
                }
            else:
                cfg = await get_guild_config(gid, refresh=True)

        preferences = security_stats_preferences(cfg)
        counts = _stats_counts(cfg)
        names = await _display_names_for_guild(
            guild,
            counts=counts,
            preferences=preferences,
        )
        category = _find_owned_category(guild, cfg)

        try:
            if category is None:
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=False),
                }
                category = await guild.create_category(
                    security_stats_category_display_name(preferences),
                    overwrites=overwrites,
                    reason="Dank Shield Server Stats display",
                )
            else:
                await category.set_permissions(
                    guild.default_role,
                    view_channel=True,
                    connect=False,
                    reason="Keep Dank Shield Server Stats visible but non-joinable",
                )
            await _apply_category_preferences(guild, category, preferences)
        except discord.Forbidden:
            return False, "❌ Discord denied permission to create, rename, move, or lock Server Stats. Check **Manage Channels** and **Manage Roles**."
        except discord.HTTPException as exc:
            return False, f"❌ Discord could not prepare the Server Stats category: `{type(exc).__name__}`."

        saved_ids = _saved_channel_ids(cfg)
        resolved_ids: Dict[str, str] = {}
        visible_keys = set(preferences["visible_keys"])
        hidden_cleanup_failed: list[str] = []

        for key in STAT_CHANNEL_PREFIXES:
            channel = _find_existing_stat_channel(
                guild,
                category,
                key=key,
                saved_id=saved_ids.get(key, 0),
                preferences=preferences,
            )
            if key not in visible_keys:
                removed = await _remove_hidden_stat_channel(channel, key=key)
                if not removed and channel is not None:
                    resolved_ids[key] = str(int(channel.id))
                    hidden_cleanup_failed.append(key)
                continue
            try:
                if channel is None:
                    channel = await guild.create_voice_channel(
                        names[key],
                        category=category,
                        reason="Dank Shield Server Stats display",
                    )
                elif channel.name != names[key]:
                    await channel.edit(name=names[key], reason="Refresh Dank Shield Server Stats")
                resolved_ids[key] = str(int(channel.id))
            except discord.Forbidden:
                return False, f"❌ Discord denied permission while creating **{names[key]}**. Check channel permission overrides."
            except discord.HTTPException as exc:
                return False, f"❌ Discord could not create or update **{names[key]}**: `{type(exc).__name__}`."

        await upsert_guild_config(
            gid,
            {
                SECURITY_STATS_ENABLED_KEY: True,
                SECURITY_STATS_CATEGORY_ID_KEY: str(int(category.id)),
                SECURITY_STATS_CHANNEL_IDS_KEY: resolved_ids,
                SECURITY_STATS_COUNTS_KEY: counts,
            },
        )
        _ACTIVE_DISPLAY_GUILDS.add(gid)
        _LAST_REFRESH_AT[gid] = time.monotonic()

        if hidden_cleanup_failed:
            labels = ", ".join(
                DEFAULT_SECURITY_STATS_LABELS[key]
                for key in hidden_cleanup_failed
            )
            return (
                False,
                "⚠️ Server Stats remain active, but Discord blocked removal of hidden counter channel(s): "
                f"**{labels}**. They remain tracked so **Repair Display** can retry safely.",
            )

        return (
            True,
            f"✅ Server Stats are active in **{security_stats_category_display_name(preferences)}** "
            f"with `{len(visible_keys)}` visible counters.",
        )


async def disable_security_stats_display(
    guild: discord.Guild,
    *,
    remove_channels: bool = True,
) -> Tuple[bool, str]:
    """Disable future refreshes and optionally remove only the tracked stats display."""

    gid = int(guild.id)
    async with _lock_for(_DISPLAY_LOCKS, gid):
        cfg = await get_guild_config(gid, refresh=True)
        category = _find_owned_category(guild, cfg)
        saved_ids = _saved_channel_ids(cfg)
        preferences = security_stats_preferences(cfg)

        remaining_ids: Dict[str, str] = (
            {
                key: str(value)
                for key, value in saved_ids.items()
                if int(value) > 0
            }
            if not remove_channels
            else {}
        )
        cleanup_complete = True
        keep_category_id = (
            str(int(category.id))
            if not remove_channels and category is not None
            else ""
        )

        if remove_channels and category is not None:
            owned_channels: list[tuple[str, discord.VoiceChannel]] = []
            owned_ids: set[int] = set()
            for key in STAT_CHANNEL_PREFIXES:
                channel = _find_existing_stat_channel(
                    guild,
                    category,
                    key=key,
                    saved_id=saved_ids.get(key, 0),
                    preferences=preferences,
                )
                if channel is None:
                    continue
                channel_id = _safe_int(getattr(channel, "id", 0), 0)
                if channel_id > 0 and channel_id in owned_ids:
                    continue
                if channel_id > 0:
                    owned_ids.add(channel_id)
                owned_channels.append((key, channel))

            try:
                existing_category_channels = list(getattr(category, "channels", []) or [])
            except Exception:
                existing_category_channels = []
            has_unowned_channels = any(
                _safe_int(getattr(channel, "id", 0), 0) not in owned_ids
                for channel in existing_category_channels
            )

            for key, channel in owned_channels:
                removed = await _remove_hidden_stat_channel(channel, key=key)
                if not removed:
                    cleanup_complete = False
                    channel_id = _safe_int(getattr(channel, "id", 0), 0)
                    if channel_id > 0:
                        remaining_ids[key] = str(channel_id)

            if not has_unowned_channels and cleanup_complete:
                try:
                    await category.delete(reason="Disable Dank Shield Server Stats")
                except (discord.Forbidden, discord.HTTPException):
                    cleanup_complete = False
                    keep_category_id = str(int(category.id))
            elif remaining_ids:
                keep_category_id = str(int(category.id))

        updates: Dict[str, Any] = {
            SECURITY_STATS_ENABLED_KEY: False,
            SECURITY_STATS_CHANNEL_IDS_KEY: remaining_ids,
        }
        if keep_category_id:
            updates[SECURITY_STATS_CATEGORY_ID_KEY] = keep_category_id
        await upsert_guild_config(gid, updates)
        if not keep_category_id:
            await clear_guild_config_keys(
                gid,
                (SECURITY_STATS_CATEGORY_ID_KEY,),
                source="server_stats.disable",
            )
        _ACTIVE_DISPLAY_GUILDS.discard(gid)
        _LAST_REFRESH_AT.pop(gid, None)
        _LAST_EVENT_REFRESH_AT.pop(gid, None)
        task = _EVENT_REFRESH_TASKS.pop(gid, None)
        if task is not None and not task.done():
            task.cancel()
        if not remove_channels:
            return (
                True,
                "✅ Server Stats are disabled. Existing display channels were left in place and remain tracked.",
            )
        if cleanup_complete:
            return True, "✅ Server Stats are disabled and their tracked display channels were removed."
        return (
            False,
            "⚠️ Server Stats are disabled, but Discord blocked removal of one or more tracked channels/category. "
            "Their IDs were kept so **Disable & Remove** can safely retry later.",
        )


async def refresh_security_stats_display(
    guild: discord.Guild,
    *,
    force: bool = False,
) -> bool:
    """Refresh and self-heal an enabled per-guild Server Stats display."""

    gid = int(guild.id)
    now = time.monotonic()
    if not force and (now - float(_LAST_REFRESH_AT.get(gid, 0.0))) < SECURITY_STATS_REFRESH_MIN_SECONDS:
        return False

    cfg = await get_guild_config(gid, refresh=True)
    if not _stats_enabled(cfg):
        _ACTIVE_DISPLAY_GUILDS.discard(gid)
        return False
    _ACTIVE_DISPLAY_GUILDS.add(gid)

    category = _find_owned_category(guild, cfg)
    if category is None:
        ok, _note = await ensure_security_stats_display(guild)
        return bool(ok)

    preferences = security_stats_preferences(cfg)
    names = await _display_names_for_guild(
        guild,
        counts=_stats_counts(cfg),
        preferences=preferences,
    )
    saved_ids = _saved_channel_ids(cfg)
    previous_ids = {
        key: str(value)
        for key, value in saved_ids.items()
        if int(value) > 0
    }
    resolved_ids: Dict[str, str] = {}
    visible_keys = set(preferences["visible_keys"])

    async with _lock_for(_DISPLAY_LOCKS, gid):
        try:
            await _apply_category_preferences(guild, category, preferences)
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(
                f"⚠️ security_stats category refresh failed guild={gid} "
                f"error={type(exc).__name__}"
            )

        for key in STAT_CHANNEL_PREFIXES:
            channel = _find_existing_stat_channel(
                guild,
                category,
                key=key,
                saved_id=saved_ids.get(key, 0),
                preferences=preferences,
            )
            if key not in visible_keys:
                removed = await _remove_hidden_stat_channel(channel, key=key)
                if not removed and channel is not None:
                    resolved_ids[key] = str(int(channel.id))
                continue
            try:
                if channel is None:
                    channel = await guild.create_voice_channel(
                        names[key],
                        category=category,
                        reason="Repair Dank Shield Server Stats display",
                    )
                elif channel.name != names[key]:
                    await channel.edit(name=names[key], reason="Refresh Dank Shield Server Stats")
                resolved_ids[key] = str(int(channel.id))
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(
                    f"⚠️ security_stats channel refresh failed guild={gid} "
                    f"key={key} error={type(exc).__name__}"
                )
                continue

        if resolved_ids != previous_ids:
            try:
                await upsert_guild_config(
                    gid,
                    {SECURITY_STATS_CHANNEL_IDS_KEY: resolved_ids},
                )
            except Exception:
                pass

        _LAST_REFRESH_AT[gid] = time.monotonic()
    return True


async def refresh_ticket_stats_for_guild_id(guild_id: int) -> bool:
    """Force the enabled live stats display to reflect a ticket transition."""
    gid = _safe_int(guild_id, 0)
    if gid <= 0:
        return False

    try:
        guild = bot.get_guild(gid)
    except Exception:
        guild = None
    if guild is None:
        return False

    try:
        return await refresh_security_stats_display(guild, force=True)
    except Exception as exc:
        try:
            print(
                f"⚠️ security_stats ticket refresh failed guild={gid} "
                f"error={type(exc).__name__}"
            )
        except Exception:
            pass
        return False


def _looks_like_cached_stats_category(category: Any) -> bool:
    try:
        if str(getattr(category, "name", "") or "") == SECURITY_STATS_CATEGORY_NAME:
            return True
        for channel in list(getattr(category, "voice_channels", []) or []):
            name = str(getattr(channel, "name", "") or "")
            if any(name.startswith(prefix) for prefix in STAT_CHANNEL_PREFIXES.values()):
                return True
    except Exception:
        return False
    return False


def _discover_cached_stats_guilds() -> None:
    """Seed obvious default-looking displays from Discord's in-memory cache."""
    for guild in list(getattr(bot, "guilds", []) or []):
        gid = _safe_int(getattr(guild, "id", 0), 0)
        if gid <= 0:
            continue
        try:
            categories = list(getattr(guild, "categories", []) or [])
        except Exception:
            categories = []
        if any(_looks_like_cached_stats_category(category) for category in categories):
            _ACTIVE_DISPLAY_GUILDS.add(gid)


async def _discover_persisted_stats_guilds() -> None:
    """Recover enabled displays after restart without one DB read per guild.

    A fully customized category/label set may contain none of the default names,
    so Discord's cache alone cannot identify it. Resolve the enabled flag for the
    guilds owned by this process in bounded Supabase batches instead of issuing a
    config read for every guild.
    """

    guild_ids = sorted(
        {
            _safe_int(getattr(guild, "id", 0), 0)
            for guild in list(getattr(bot, "guilds", []) or [])
            if _safe_int(getattr(guild, "id", 0), 0) > 0
        }
    )
    if not guild_ids:
        return

    sb = get_supabase()
    if sb is None:
        return

    def _read_enabled() -> set[int]:
        enabled: set[int] = set()
        for start in range(0, len(guild_ids), _STATS_DISCOVERY_BATCH_SIZE):
            batch = guild_ids[start : start + _STATS_DISCOVERY_BATCH_SIZE]
            response = (
                sb.table(GUILD_CONFIG_TABLE)
                .select("guild_id,settings")
                .in_("guild_id", [str(gid) for gid in batch])
                .execute()
            )
            for row in list(getattr(response, "data", None) or []):
                if not isinstance(row, Mapping):
                    continue
                settings = _mapping(row.get("settings"))
                if not _safe_bool(settings.get(SECURITY_STATS_ENABLED_KEY), False):
                    continue
                gid = _safe_int(row.get("guild_id"), 0)
                if gid > 0:
                    enabled.add(gid)
        return enabled

    try:
        _ACTIVE_DISPLAY_GUILDS.update(await asyncio.to_thread(_read_enabled))
    except Exception as exc:
        print(
            "⚠️ security_stats persisted display discovery failed "
            f"error={type(exc).__name__}"
        )


@tasks.loop(minutes=10)
async def refresh_all_security_stats_displays() -> None:
    # Never fan a forced config read across every guild. Only displays observed
    # in-process are revisited; relevant events and the UI add guilds lazily.
    for gid in tuple(_ACTIVE_DISPLAY_GUILDS):
        try:
            guild = bot.get_guild(int(gid))
        except Exception:
            guild = None
        if guild is None:
            _ACTIVE_DISPLAY_GUILDS.discard(int(gid))
            _LAST_REFRESH_AT.pop(int(gid), None)
            continue
        try:
            await refresh_security_stats_display(guild)
        except Exception as exc:
            try:
                print(f"⚠️ security_stats refresh failed guild={gid} error={type(exc).__name__}")
            except Exception:
                pass


@refresh_all_security_stats_displays.before_loop
async def _before_security_stats_refresh() -> None:
    await bot.wait_until_ready()


@bot.listen("on_member_join")
async def _refresh_member_join_stats(member: discord.Member) -> None:
    gid = _safe_int(getattr(getattr(member, "guild", None), "id", 0), 0)
    if gid <= 0 or gid not in _ACTIVE_DISPLAY_GUILDS:
        return
    try:
        await refresh_security_stats_display(member.guild)
    except Exception as exc:
        print(
            f"⚠️ security_stats member-join refresh failed guild="
            f"{getattr(getattr(member, 'guild', None), 'id', 0)} error={type(exc).__name__}"
        )


@bot.listen("on_member_remove")
async def _refresh_member_remove_stats(member: discord.Member) -> None:
    gid = _safe_int(getattr(getattr(member, "guild", None), "id", 0), 0)
    if gid <= 0 or gid not in _ACTIVE_DISPLAY_GUILDS:
        return
    try:
        await refresh_security_stats_display(member.guild)
    except Exception as exc:
        print(
            f"⚠️ security_stats member-remove refresh failed guild="
            f"{getattr(getattr(member, 'guild', None), 'id', 0)} error={type(exc).__name__}"
        )


@bot.listen("on_ready")
async def _start_security_stats_refresh_loop() -> None:
    if refresh_all_security_stats_displays.is_running():
        return
    _discover_cached_stats_guilds()
    await _discover_persisted_stats_guilds()
    try:
        refresh_all_security_stats_displays.start()
        print(
            "✅ security_stats: bounded live Discord stats refresh loop started "
            f"active={len(_ACTIVE_DISPLAY_GUILDS)}"
        )
    except RuntimeError:
        pass


__all__ = [
    "DEFAULT_SECURITY_STATS",
    "DEFAULT_TICKET_STATUS_COUNTS",
    "SECURITY_STATS_CATEGORY_NAME",
    "SECURITY_STATS_CATEGORY_NAME_KEY",
    "SECURITY_STATS_CHANNEL_IDS_KEY",
    "SECURITY_STATS_COUNTS_KEY",
    "SECURITY_STATS_CUSTOM_LABELS_KEY",
    "SECURITY_STATS_ENABLED_KEY",
    "SECURITY_STATS_NUMBER_STYLE_KEY",
    "SECURITY_STATS_PLACEMENT_KEY",
    "SECURITY_STATS_VISIBLE_KEYS_KEY",
    "DEFAULT_SECURITY_STATS_LABELS",
    "DEFAULT_SECURITY_STATS_VISIBLE_KEYS",
    "disable_security_stats_display",
    "ensure_security_stats_display",
    "format_security_stat_count",
    "normalize_security_stats",
    "security_stats_preferences",
    "record_security_event",
    "record_spam_guard_action",
    "refresh_security_stats_display",
    "refresh_ticket_stats_for_guild_id",
]
