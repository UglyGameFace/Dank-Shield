from __future__ import annotations

"""Shared Discord component lifecycle policy for Dank Shield.

There are two intentionally different UI classes:

* durable public panels are persistent and must survive process restarts;
* private/ephemeral menus are user sessions and may expire, but a stale click
  must recover cleanly instead of falling through to Discord's red
  "This interaction failed" banner.

Keep these values centralized. Public product surfaces should not invent their
own normal navigation lifetime.
"""

PRIVATE_MENU_TTL_SECONDS = 15 * 60
PRIVATE_MENU_RECOVERY_GRACE_SECONDS = 0.75

PUBLIC_PANEL_LIFECYCLE_TEXT = (
    "Public panel: **persistent** — buttons stay usable across time and bot restarts.\n"
    "Private menus/dropdowns/builders: **temporary by design** — reopen them from the public panel if they expire.\n"
    "Stale private controls self-recover to a fresh Dank Shield Control Center instead of silently failing.\n"
    "Health checks setup, permissions, roles, and boot registration. It cannot inspect old dismissed/expired private menus."
)


def public_panel_lifecycle_text(
    public_name: str = "Public panel",
    private_name: str = "Private menus/dropdowns",
) -> str:
    public = str(public_name or "Public panel").strip()
    private = str(private_name or "Private menus/dropdowns").strip()
    return (
        f"{public}: **persistent** — buttons stay usable across time and bot restarts.\n"
        f"{private}: **temporary by design** — reopen them from the public panel if they expire.\n"
        "Stale private controls self-recover to a fresh Dank Shield Control Center instead of silently failing.\n"
        "Health checks setup, permissions, roles, and boot registration. It cannot inspect old dismissed/expired private menus."
    )


def private_menu_lifecycle_text() -> str:
    minutes = max(1, int(PRIVATE_MENU_TTL_SECONDS // 60))
    return (
        f"Private control session: active for about **{minutes} minutes**. "
        "If it expires or Dank Shield redeploys, pressing an old control opens a fresh Control Center safely."
    )


__all__ = [
    "PRIVATE_MENU_RECOVERY_GRACE_SECONDS",
    "PRIVATE_MENU_TTL_SECONDS",
    "PUBLIC_PANEL_LIFECYCLE_TEXT",
    "private_menu_lifecycle_text",
    "public_panel_lifecycle_text",
]
