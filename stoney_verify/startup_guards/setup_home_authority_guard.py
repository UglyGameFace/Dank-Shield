from __future__ import annotations

"""Final authority guard for /dank setup home.

Multiple legacy modules used to monkey-patch public_setup_solid._build_main_setup_payload.
This guard runs late and restores public_setup_solid as the single home owner.
"""

from typing import Any

_PATCHED = False


def _name(fn: Any) -> str:
    try:
        return f"{getattr(fn, '__module__', '?')}.{getattr(fn, '__name__', repr(fn))}"
    except Exception:
        return repr(fn)


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    try:
        from stoney_verify.commands_ext import public_setup_solid as solid

        owner = getattr(solid, "_DANK_SOLID_HOME_OWNER", None)
        if not callable(owner):
            print("⚠️ setup_home_authority_guard failed; solid home owner alias missing")
            return False

        current = getattr(solid, "_build_main_setup_payload", None)
        if current is not owner:
            print(f"🧭 setup_home_authority_guard restored /dank setup home owner old={_name(current)} new={_name(owner)}")
            solid._build_main_setup_payload = owner
        else:
            print("🧭 setup_home_authority_guard verified; public_setup_solid owns /dank setup home")

        try:
            from stoney_verify.commands_ext import public_setup_fresh_choice as fresh
            fresh._plain_choice_main_payload = owner
            fresh.FreshChoiceHomeView = solid.SolidSetupView
            fresh.FreshServerChoiceView = solid.SolidSetupView
        except Exception:
            pass

        try:
            from stoney_verify.commands_ext import public_setup_recovery as recovery
            recovery._ORIGINAL_BUILD_MAIN = owner
        except Exception:
            pass

        _PATCHED = True
        return True
    except Exception as exc:
        print(f"⚠️ setup_home_authority_guard failed: {exc!r}")
        return False


apply()

__all__ = ["apply"]
