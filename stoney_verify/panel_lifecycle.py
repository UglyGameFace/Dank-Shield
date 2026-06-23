from __future__ import annotations

PUBLIC_PANEL_LIFECYCLE_TEXT = (
    "Public panel: **persistent** — buttons stay usable across time and bot restarts.\n"
    "Private menus/dropdowns/builders: **temporary by design** — reopen them from the public panel if they expire.\n"
    "Health checks setup, permissions, roles, and boot registration. It cannot inspect old dismissed/expired private menus."
)


def public_panel_lifecycle_text(public_name: str = "Public panel", private_name: str = "Private menus/dropdowns") -> str:
    public = str(public_name or "Public panel").strip()
    private = str(private_name or "Private menus/dropdowns").strip()
    return (
        f"{public}: **persistent** — buttons stay usable across time and bot restarts.\n"
        f"{private}: **temporary by design** — reopen them from the public panel if they expire.\n"
        "Health checks setup, permissions, roles, and boot registration. It cannot inspect old dismissed/expired private menus."
    )
