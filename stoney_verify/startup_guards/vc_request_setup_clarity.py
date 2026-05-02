from __future__ import annotations

"""Make VC setup failures readable for real users.

The raw VC flow can fail with a technically true but unhelpful message like:
"VC request was not accepted because the staff panel could not be posted."

That leaves the ticket owner stuck and does not tell staff what setup screen to
open. This startup guard keeps the behavior safe, but rewrites that failure into
a direct setup-health instruction with the exact area to inspect.
"""

from typing import Any, Awaitable, Callable, Dict

_PATCHED = False


def _looks_like_staff_panel_post_failure(message: Any) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    return (
        "staff panel" in text
        and (
            "could not be posted" in text
            or "couldn't be posted" in text
            or "post" in text
            or "routing failed" in text
        )
    )


def _clear_vc_setup_message(original: Any = "") -> str:
    original_text = str(original or "").strip()
    details = (
        "VC verification is not ready yet because Stoney could not post the **staff VC request panel**.\n\n"
        "Staff should run `/stoney setup` → **Run Health Check** and fix the VC/log channel setup.\n\n"
        "Check these first:\n"
        "• The **VC queue/status text channel** is saved and still exists.\n"
        "• Stoney can **View Channel**, **Send Messages**, **Embed Links**, and **Read Message History** in that queue channel.\n"
        "• If no queue channel is configured, the **modlog** or **transcripts** fallback channel must be writable.\n"
        "• The configured **VC verification voice channel** must exist and Stoney must be able to manage it."
    )
    if original_text:
        details += f"\n\nOriginal error: `{original_text[:500]}`"
    return details


def patch_vc_request_setup_clarity() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    try:
        from stoney_verify.commands_ext import vc_flow
    except Exception as e:
        try:
            print(f"⚠️ vc_request_setup_clarity: vc_flow import failed: {e!r}")
        except Exception:
            pass
        return False

    original: Callable[..., Awaitable[Dict[str, Any]]] | None = getattr(
        vc_flow,
        "create_vc_request_for_ticket",
        None,
    )
    if original is None or not callable(original):
        try:
            print("⚠️ vc_request_setup_clarity: create_vc_request_for_ticket not found")
        except Exception:
            pass
        return False

    if getattr(original, "_stoney_setup_clarity_wrapped", False):
        _PATCHED = True
        return True

    async def wrapped_create_vc_request_for_ticket(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        result = await original(*args, **kwargs)
        try:
            if not isinstance(result, dict):
                return result
            if bool(result.get("ok")):
                return result
            if _looks_like_staff_panel_post_failure(result.get("message")):
                patched = dict(result)
                patched["message"] = _clear_vc_setup_message(result.get("message"))
                patched["setup_hint"] = "Run /stoney setup -> Run Health Check. Fix VC queue/status, modlog/transcripts fallback, and VC channel permissions."
                return patched
        except Exception:
            return result
        return result

    try:
        setattr(wrapped_create_vc_request_for_ticket, "_stoney_setup_clarity_wrapped", True)
    except Exception:
        pass

    setattr(vc_flow, "create_vc_request_for_ticket", wrapped_create_vc_request_for_ticket)
    _PATCHED = True

    try:
        print("✅ vc_request_setup_clarity: clearer VC setup failure messages active")
    except Exception:
        pass
    return True


patch_vc_request_setup_clarity()


__all__ = ["patch_vc_request_setup_clarity"]
