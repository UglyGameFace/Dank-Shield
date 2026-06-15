from __future__ import annotations

"""Route /dank setup Safety & Repair through the owned repair service.

The setup hub previously called the older guard helper directly. That helper and
the owned service drifted on the return shape of `_build_targets`, which caused
`ValueError: too many values to unpack (expected 2)` when Preview/Fix Permissions
was pressed. This guard keeps the centralized setup button pointed at the owned
service entrypoint.
"""

_PATCHED = False


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.startup_guards import setup_smart_home_menu_guard as hub
        from stoney_verify import setup_permission_repair_services as service

        async def open_permission_repair(interaction):
            return await service.open_permission_repair(interaction)

        hub._open_permission_repair = open_permission_repair  # type: ignore[attr-defined]
        _PATCHED = True
        print("✅ setup_safety_repair_service_guard active; Safety & Repair uses owned permission repair service")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ setup_safety_repair_service_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
