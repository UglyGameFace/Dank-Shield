from __future__ import annotations

"""Disable the legacy TicketPanelView patch side effect from unverified flow.

`unverified_ticket_panel_flow.py` still exposes useful helpers used by the clean
public ticket panel, especially:
- _is_unverified_only_member
- _get_guild_config_safe
- _ensure_configured_vc_verify_locked
- _post_verify_ui

But its old import-time `patch_ticket_panel_view()` targets the legacy
`tickets_new.panel.TicketPanelView` path. That path is now disabled so stale
public panels cannot create duplicate/wrong tickets.

This guard keeps the helper module available while preventing future refreshes
or imports from re-wrapping the legacy public panel.
"""


def _log(message: str) -> None:
    try:
        print(f"✅ unverified_legacy_panel_patch_disable: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ unverified_legacy_panel_patch_disable: {message}")
    except Exception:
        pass


def apply() -> bool:
    try:
        from . import unverified_ticket_panel_flow as flow
    except Exception as e:
        _warn(f"could not import unverified_ticket_panel_flow: {e!r}")
        return False

    if getattr(flow, "_LEGACY_TICKET_PANEL_PATCH_DISABLED", False):
        return True

    def disabled_patch_ticket_panel_view() -> bool:
        try:
            flow._PATCHED = True
        except Exception:
            pass
        return True

    try:
        setattr(disabled_patch_ticket_panel_view, "_legacy_patch_disabled", True)
        flow.patch_ticket_panel_view = disabled_patch_ticket_panel_view
        flow._PATCHED = True
        setattr(flow, "_LEGACY_TICKET_PANEL_PATCH_DISABLED", True)
        _log("disabled legacy TicketPanelView auto-route patch; helper functions remain available")
        return True
    except Exception as e:
        _warn(f"patch failed: {e!r}")
        return False


apply()

__all__ = ["apply"]
