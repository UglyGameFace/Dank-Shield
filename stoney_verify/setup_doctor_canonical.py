from __future__ import annotations

"""Canonical Setup Doctor severity engine."""

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class SetupDoctorResult:
    blockers: list[str]
    warnings: list[str]
    ok: list[str]
    features: dict[str, bool]


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text or default
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _cfg_sources(cfg: Any) -> list[Any]:
    sources: list[Any] = []
    if cfg is not None:
        sources.append(cfg)
    for key in ("settings", "config", "metadata", "meta"):
        try:
            value = cfg.get(key) if hasattr(cfg, "get") else getattr(cfg, key, None)
            if isinstance(value, dict):
                sources.append(value)
        except Exception:
            continue
    return sources


def cfg_value(cfg: Any, *names: str) -> Any:
    for source in _cfg_sources(cfg):
        for name in names:
            try:
                if hasattr(source, "get"):
                    value = source.get(name)
                    if value not in (None, "", 0, "0"):
                        return value
            except Exception:
                pass
            try:
                value = getattr(source, name, None)
                if value not in (None, "", 0, "0"):
                    return value
            except Exception:
                pass
    return None


def cfg_bool(cfg: Any, *names: str, default: bool = False) -> bool:
    raw = cfg_value(cfg, *names)
    try:
        if isinstance(raw, bool):
            return raw
        if raw is None:
            return bool(default)
        text = str(raw).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled"}:
            return False
    except Exception:
        pass
    return bool(default)


def cfg_has_id(cfg: Any, *names: str) -> bool:
    return any(_safe_int(cfg_value(cfg, name), 0) > 0 for name in names)


def detect_features(cfg: Any) -> dict[str, bool]:
    mode = _safe_str(cfg_value(cfg, "verification_mode", "setup_type", "setup_mode"), "").lower()

    tickets = (
        cfg_bool(cfg, "tickets_enabled", "ticketing_enabled", default=False)
        or "ticket" in mode
        or "help desk" in mode
        or "helpdesk" in mode
        or cfg_has_id(cfg, "ticket_category_id", "open_ticket_category_id")
        or cfg_has_id(cfg, "ticket_archive_category_id", "archive_category_id", "closed_ticket_category_id")
        or cfg_has_id(cfg, "ticket_panel_channel_id", "support_channel_id", "panel_channel_id")
        or cfg_has_id(cfg, "staff_role_id", "ticket_staff_role_id", "support_role_id")
    )

    basic_verify = (
        cfg_bool(cfg, "basic_verify_enabled", "basic_button_verify_enabled", "verify_enabled", "verification_enabled", default=False)
        or "verify" in mode
        or cfg_has_id(cfg, "verify_channel_id", "verification_channel_id")
        or cfg_has_id(cfg, "unverified_role_id", "waiting_role_id")
        or cfg_has_id(cfg, "verified_role_id", "approved_role_id")
    )

    vc_verify = (
        cfg_bool(cfg, "vc_verify_enabled", "voice_verify_enabled", "enable_vc_verify", default=False)
        or "voice" in mode
        or "vc" in mode
        or cfg_has_id(cfg, "vc_verify_channel_id", "voice_verify_channel_id")
        or cfg_has_id(cfg, "vc_verify_queue_channel_id", "vc_queue_channel_id", "vc_verify_requests_channel_id")
    )

    logs = (
        cfg_bool(cfg, "logs_enabled", "modlog_enabled", default=False)
        or cfg_has_id(cfg, "modlog_channel_id", "raidlog_channel_id", "security_log_channel_id")
        or cfg_has_id(cfg, "transcripts_channel_id", "transcript_channel_id")
        or cfg_has_id(cfg, "join_log_channel_id", "join_leave_log_channel_id", "join_exit_log_channel_id")
        or cfg_has_id(cfg, "status_channel_id", "bot_status_channel_id")
    )

    return {
        "tickets": bool(tickets),
        "basic_verify": bool(basic_verify),
        "vc_verify": bool(vc_verify),
        "logs": bool(logs),
    }


_LAYOUT_ONLY = (
    "wrong category",
    "expected it under",
    "expected under",
    "not grouped with",
    "split across categories",
    "in different categories",
    "category name looks unusual",
    "name looks unusual",
    "separate channels are cleaner",
    "same category",
    "category order",
    "cleaner layout",
    "between active tickets and archive",
    "public/start",
    "staff/tools",
)

_OPTIONAL_PERMISSION = (
    "view audit log",
    "manage messages",
    "kick members",
    "moderate members",
    "ban members",
)

_REQUIRED_PERMISSION = (
    "manage channels",
    "manage roles",
    "view channel",
    "send messages",
    "read message history",
    "embed links",
)


def _is_layout_only(line: str) -> bool:
    low = str(line or "").lower()
    return any(phrase in low for phrase in _LAYOUT_ONLY)


def _is_optional_control(line: str) -> bool:
    low = str(line or "").lower()
    return "server-control role" in low or "server control role" in low


def _is_optional_permission_only(line: str) -> bool:
    low = str(line or "").lower()
    if "permission" not in low:
        return False
    has_optional = any(phrase in low for phrase in _OPTIONAL_PERMISSION)
    has_required = any(phrase in low for phrase in _REQUIRED_PERMISSION)
    return bool(has_optional and not has_required)


def _is_vc_only(line: str) -> bool:
    low = str(line or "").lower()
    return "vc verify" in low or "vc verification" in low or "voice verification" in low or "vc queue" in low


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def feature_scope_text(features: dict[str, bool]) -> str:
    return (
        "Feature scope: "
        f"tickets={'on' if features.get('tickets') else 'off'}, "
        f"basic verify={'on' if features.get('basic_verify') else 'off'}, "
        f"vc verify={'on' if features.get('vc_verify') else 'off'}, "
        f"logs={'on' if features.get('logs') else 'off'}."
    )


def truth_rules_text() -> str:
    return (
        "❌ **Blocker** = enabled feature cannot run, saved ID is missing/deleted/wrong type, or required permission is missing.\n"
        "⚠️ **Warning** = optional feature incomplete, useful permission missing, or layout/privacy/style cleanup.\n"
        "✅ **Passing** = saved item exists and is usable for the detected feature scope."
    )


def normalize_setup_health(
    *,
    cfg: Any,
    blockers: list[str],
    warnings: list[str],
    ok: list[str],
) -> SetupDoctorResult:
    features = detect_features(cfg)

    clean_blockers: list[str] = []
    clean_warnings: list[str] = list(warnings)
    clean_ok: list[str] = list(ok)

    for raw in blockers:
        line = str(raw or "").strip()
        if not line:
            continue

        if _is_optional_control(line):
            clean_warnings.append(
                "Optional setup control role is not saved. Admin/Manage Server users can still configure setup."
            )
            continue

        if _is_layout_only(line):
            clean_warnings.append("Layout/style cleanup: " + line)
            continue

        if _is_optional_permission_only(line):
            clean_warnings.append("Useful optional permission: " + line)
            continue

        if _is_vc_only(line) and not features.get("vc_verify", False):
            clean_ok.append("VC Verify is disabled/not configured, so VC-only missing items are not blockers.")
            continue

        clean_blockers.append(line)

    clean_ok.append(feature_scope_text(features))

    return SetupDoctorResult(
        blockers=_dedupe(clean_blockers),
        warnings=_dedupe(clean_warnings),
        ok=_dedupe(clean_ok),
        features=features,
    )
