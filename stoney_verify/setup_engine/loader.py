from __future__ import annotations

from typing import Any, Mapping

from .models import SetupConfigSnapshot


def cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
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


def cfg_int(cfg: Any, *keys: str) -> int:
    for key in keys:
        raw = cfg_value(cfg, key, None)
        try:
            if raw is None or isinstance(raw, bool):
                continue
            text = str(raw).strip()
            if not text or text.lower() in {"none", "null"}:
                continue
            value = int(text)
            if value > 0:
                return value
        except Exception:
            continue
    return 0


def cfg_str(cfg: Any, *keys: str) -> str:
    for key in keys:
        try:
            text = str(cfg_value(cfg, key, "") or "").strip()
            if text:
                return text
        except Exception:
            continue
    return ""


def flatten_raw(cfg: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    try:
        if hasattr(cfg, "items"):
            raw.update({str(k): v for k, v in cfg.items()})
    except Exception:
        pass
    try:
        for key, value in vars(cfg).items():
            raw.setdefault(str(key), value)
    except Exception:
        pass
    for bucket in ("settings", "config", "metadata", "meta"):
        for value in (raw.get(bucket), getattr(cfg, bucket, None)):
            try:
                if isinstance(value, Mapping):
                    raw.update({str(k): v for k, v in value.items()})
            except Exception:
                pass
    return raw


def snapshot_from_config(guild_id: int, cfg: Any) -> SetupConfigSnapshot:
    return SetupConfigSnapshot(
        guild_id=int(guild_id),
        setup_type=cfg_str(cfg, "setup_choice", "setup_type", "setup_mode"),
        server_control_role_id=cfg_int(cfg, "server_control_role_id", "control_role_id", "bot_manager_role_id"),
        staff_role_id=cfg_int(cfg, "staff_role_id", "ticket_staff_role_id", "support_role_id"),
        vc_staff_role_id=cfg_int(cfg, "vc_staff_role_id", "vc_verify_staff_role_id"),
        unverified_role_id=cfg_int(cfg, "unverified_role_id", "pending_role_id", "waiting_role_id"),
        verified_role_id=cfg_int(cfg, "verified_role_id", "approved_role_id"),
        resident_role_id=cfg_int(cfg, "resident_role_id"),
        member_role_id=cfg_int(cfg, "member_role_id"),
        onboarding_category_id=cfg_int(cfg, "start_category_id", "welcome_category_id", "onboarding_category_id"),
        welcome_channel_id=cfg_int(cfg, "welcome_channel_id"),
        rules_channel_id=cfg_int(cfg, "rules_channel_id", "rule_channel_id", "rules_text_channel_id"),
        announcements_channel_id=cfg_int(cfg, "announcements_channel_id", "announcement_channel_id"),
        verify_channel_id=cfg_int(cfg, "verify_channel_id", "verification_channel_id"),
        vc_verify_channel_id=cfg_int(cfg, "vc_verify_channel_id", "voice_verify_channel_id"),
        vc_queue_channel_id=cfg_int(cfg, "vc_verify_queue_channel_id", "vc_queue_channel_id", "vc_request_channel_id", "vc_verify_requests_channel_id"),
        ticket_panel_channel_id=cfg_int(cfg, "ticket_panel_channel_id", "support_channel_id", "panel_channel_id"),
        ticket_category_id=cfg_int(cfg, "ticket_category_id", "active_ticket_category_id", "open_ticket_category_id"),
        archive_category_id=cfg_int(cfg, "ticket_archive_category_id", "archive_category_id", "closed_ticket_category_id"),
        staff_tools_category_id=cfg_int(cfg, "management_category_id", "staff_tools_category_id"),
        transcript_channel_id=cfg_int(cfg, "transcripts_channel_id", "transcript_channel_id"),
        modlog_channel_id=cfg_int(cfg, "modlog_channel_id", "mod_log_channel_id"),
        join_leave_log_channel_id=cfg_int(cfg, "join_log_channel_id", "join_leave_log_channel_id", "joinlog_channel_id"),
        bot_status_channel_id=cfg_int(cfg, "status_channel_id", "bot_status_channel_id"),
        raw=flatten_raw(cfg),
    )
