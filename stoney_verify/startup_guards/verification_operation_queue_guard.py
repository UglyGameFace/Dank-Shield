from __future__ import annotations

"""Route verification staff decisions through the shared operation queue.

This protects ID approval/deny/resubmit and VC verification controls from double
clicks, stale dashboard retries, and multiple staff racing the same token.
"""

import builtins
import sys
from typing import Any

_ORIGINAL_IMPORT = builtins.__import__


def _log(message: str) -> None:
    try:
        print(f"🧱 verification_operation_queue_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ verification_operation_queue_guard {message}")
    except Exception:
        pass


def _safe_str(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default
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


def _channel_id(interaction: Any, kwargs: dict[str, Any]) -> int:
    for source in (kwargs.get("channel"), kwargs.get("ticket_ch"), getattr(interaction, "channel", None)):
        cid = _safe_int(getattr(source, "id", 0), 0)
        if cid > 0:
            return cid
    return 0


def _standard_operation(action: str) -> str:
    action = _safe_str(action).lower()
    if action == "approve":
        return "verification_accept"
    if action == "denyclose":
        return "verification_deny"
    if action == "resubmit":
        return "verification_resubmit"
    return f"verification_{action or 'decision'}"


def _vc_operation(action: str) -> str:
    mapping = {
        "vc_accept": "vc_verification_accept",
        "vc_start": "vc_verification_start",
        "vc_approve": "vc_verification_approve",
        "vc_denyclose": "vc_verification_deny",
        "vc_upload": "vc_verification_upload_request",
        "vc_reissue": "vc_verification_reissue",
        "vc_end": "vc_verification_end",
        "vc_complete": "vc_verification_complete",
        "vc_cancel": "vc_verification_cancel",
    }
    return mapping.get(_safe_str(action).lower(), f"vc_verification_{_safe_str(action) or 'action'}")


def _wrap_standard(handler_mod: Any) -> bool:
    original = getattr(handler_mod, "_handle_standard_staff_decision", None)
    if not callable(original) or getattr(original, "_verification_operation_queue_wrapped", False):
        return False

    async def wrapped(interaction: Any, *args: Any, **kwargs: Any) -> Any:
        action = _safe_str(kwargs.get("action"))
        token = _safe_str(kwargs.get("token"))
        channel_id = _channel_id(interaction, kwargs)
        try:
            from ..operation_queue import run_interaction_exclusive

            return await run_interaction_exclusive(
                interaction=interaction,
                operation_type=_standard_operation(action),
                action_label=f"verification {action or 'decision'}",
                fingerprint={"token": token, "action": action, "channel_id": channel_id},
                risk_level="dangerous",
                source="discord_command",
                concurrency_class="verification_decision",
                concurrency_key=f"token:{token or channel_id or 'unknown'}",
                timeout_seconds=240.0,
                factory=lambda: original(interaction, *args, **kwargs),
            )
        except Exception as e:
            _warn(f"standard decision queue unavailable; running original: {e!r}")
            return await original(interaction, *args, **kwargs)

    setattr(wrapped, "_verification_operation_queue_wrapped", True)
    setattr(wrapped, "_verification_operation_queue_original", original)
    setattr(handler_mod, "_handle_standard_staff_decision", wrapped)
    return True


def _wrap_vc(handler_mod: Any) -> bool:
    original = getattr(handler_mod, "_handle_vc_staff_action", None)
    if not callable(original) or getattr(original, "_verification_operation_queue_wrapped", False):
        return False

    async def wrapped(interaction: Any, *args: Any, **kwargs: Any) -> Any:
        action = _safe_str(kwargs.get("action"))
        token = _safe_str(kwargs.get("token"))
        channel_id = _channel_id(interaction, kwargs)
        try:
            from ..operation_queue import run_interaction_exclusive

            return await run_interaction_exclusive(
                interaction=interaction,
                operation_type=_vc_operation(action),
                action_label=f"VC verification {action or 'action'}",
                fingerprint={"token": token, "action": action, "channel_id": channel_id},
                risk_level="dangerous",
                source="discord_command",
                concurrency_class="verification_decision",
                concurrency_key=f"token:{token or channel_id or 'unknown'}",
                timeout_seconds=300.0,
                factory=lambda: original(interaction, *args, **kwargs),
            )
        except Exception as e:
            _warn(f"VC decision queue unavailable; running original: {e!r}")
            return await original(interaction, *args, **kwargs)

    setattr(wrapped, "_verification_operation_queue_wrapped", True)
    setattr(wrapped, "_verification_operation_queue_original", original)
    setattr(handler_mod, "_handle_vc_staff_action", wrapped)
    return True


def _patch_handlers(handler_mod: Any) -> None:
    if getattr(handler_mod, "_VERIFICATION_OPERATION_QUEUE_GUARD_APPLIED", False):
        return
    wrapped = 0
    try:
        if _wrap_standard(handler_mod):
            wrapped += 1
    except Exception as e:
        _warn(f"failed wrapping standard decision handler: {e!r}")
    try:
        if _wrap_vc(handler_mod):
            wrapped += 1
    except Exception as e:
        _warn(f"failed wrapping VC decision handler: {e!r}")
    try:
        setattr(handler_mod, "_VERIFICATION_OPERATION_QUEUE_GUARD_APPLIED", True)
    except Exception:
        pass
    _log(f"patched verification decision handlers wrapped={wrapped}")


def _maybe_patch_loaded() -> None:
    try:
        mod = sys.modules.get("stoney_verify.interaction_handlers")
        if mod is not None:
            _patch_handlers(mod)
    except Exception as e:
        _warn(f"patch loaded interaction_handlers failed: {e!r}")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.interaction_handlers" or name.endswith(".interaction_handlers"):
            target = sys.modules.get("stoney_verify.interaction_handlers") or sys.modules.get(name)
            if target is not None:
                _patch_handlers(target)
        _maybe_patch_loaded()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")
    return module


def install() -> bool:
    if getattr(builtins, "_stoney_verification_operation_queue_import_hook", False):
        _maybe_patch_loaded()
        return True
    try:
        builtins.__import__ = _safe_import
        setattr(builtins, "_stoney_verification_operation_queue_import_hook", True)
        _maybe_patch_loaded()
        _log("loaded; verification operation queue guard active")
        return True
    except Exception as e:
        _warn(f"install failed: {e!r}")
        return False


install()

__all__ = ["install"]
