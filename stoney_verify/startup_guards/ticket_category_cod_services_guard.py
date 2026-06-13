from __future__ import annotations

"""Keep COD Services as a first-class ticket category option.

This guard centralizes the Call of Duty / legacy lobby category compatibility
that servers expect from the public ticket menu and setup flow.  It is a small
runtime bridge until the constants are folded directly into the category/admin
modules after live verification.
"""

from typing import Any, Dict, List

_COD_CATEGORY: Dict[str, Any] = {
    "slug": "cod_services",
    "name": "Call of Duty Services",
    "description": "Older COD lobby, unlock, recovery, zombies, or service questions.",
    "intake_type": "cod_services",
    "match_keywords": [
        "cod",
        "call of duty",
        "bo1",
        "bo2",
        "bo3",
        "mw2",
        "mw3",
        "waw",
        "ghosts",
        "zombies",
        "modded lobby",
        "challenge lobby",
        "unlock all",
        "recovery",
        "rgh",
        "jtag",
    ],
    "is_default": False,
    "sort_order": 25,
}

_COD_INTAKE_TYPES = {"cod", "cod_services", "call_of_duty", "game_services"}


def _log(message: str) -> None:
    try:
        print(f"✅ ticket_category_cod_services_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_category_cod_services_guard: {message}")
    except Exception:
        pass


def _safe_str(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _is_cod_row(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    text = " ".join(
        _safe_str(row.get(key)).lower().replace("_", "-")
        for key in ("slug", "name", "description", "intake_type")
    )
    return any(
        token in text
        for token in (
            "cod",
            "call of duty",
            "call-of-duty",
            "black ops",
            "bo2",
            "bo3",
            "mw2",
            "mw3",
            "modded lobby",
            "challenge lobby",
        )
    )


def _install_setup_category() -> bool:
    try:
        from ..commands_ext import public_setup_solid as setup_mod
    except Exception as exc:
        _warn(f"could not import public_setup_solid: {exc!r}")
        return False

    changed = False
    rows = tuple(getattr(setup_mod, "RECOMMENDED_CATEGORIES", ()) or ())
    if not any(_is_cod_row(row) for row in rows):
        # Keep Support first, Verification second, then COD before moderation flows.
        next_rows: List[Dict[str, Any]] = []
        inserted = False
        for row in rows:
            next_rows.append(row)
            slug = _safe_str(row.get("slug") if isinstance(row, dict) else "").lower().replace("-", "_")
            if not inserted and slug in {"verification", "support"} and len(next_rows) >= 2:
                next_rows.append(dict(_COD_CATEGORY))
                inserted = True
        if not inserted:
            next_rows.append(dict(_COD_CATEGORY))
        setup_mod.RECOMMENDED_CATEGORIES = tuple(next_rows)
        changed = True

    options = tuple(getattr(setup_mod, "INTAKE_TYPE_OPTIONS", ()) or ())
    if "cod_services" not in options:
        setup_mod.INTAKE_TYPE_OPTIONS = tuple(dict.fromkeys((*options, "cod_services")))
        changed = True

    if changed:
        _log("patched setup recommended categories with COD Services")
    return True


def _install_category_admin_type() -> bool:
    try:
        from ..commands_ext import ticket_category_admin as admin_mod
    except Exception as exc:
        _warn(f"could not import ticket_category_admin: {exc!r}")
        return False

    allowed = getattr(admin_mod, "_ALLOWED_INTAKE_TYPES", None)
    if isinstance(allowed, set):
        before = set(allowed)
        allowed.update(_COD_INTAKE_TYPES)
        if allowed != before:
            _log("patched ticket category admin allowed intake types for COD Services")
    return True


def _cod_questions(intake_mod: Any) -> List[Dict[str, Any]]:
    maker = getattr(intake_mod, "_make_question")
    return [
        maker(
            key="cod_game",
            label="Which COD game?",
            placeholder="Example: BO2, BO3, MW2, MW3, Ghosts, WaW, zombies, etc.",
            style="short",
            max_length=160,
            row=0,
        ),
        maker(
            key="cod_service",
            label="What do you need done or answered?",
            placeholder="Rank, unlocks, modded lobby, recovery question, zombies, or other COD service details.",
            style="paragraph",
            max_length=1000,
            row=1,
        ),
        maker(
            key="cod_platform",
            label="Platform / console",
            placeholder="Xbox, PlayStation, PC, etc.",
            style="short",
            max_length=140,
            row=2,
        ),
        maker(
            key="cod_availability",
            label="Best time to reach you?",
            placeholder="Timezone and when you are usually available.",
            style="short",
            required=False,
            max_length=200,
            row=3,
        ),
    ]


def _install_intake_service_type() -> bool:
    try:
        from ..tickets_new import intake_service as intake_mod
    except Exception as exc:
        _warn(f"could not import intake_service: {exc!r}")
        return False

    valid = getattr(intake_mod, "_VALID_INTAKE_TYPES", None)
    if isinstance(valid, set):
        before = set(valid)
        valid.update(_COD_INTAKE_TYPES)
        if valid != before:
            _log("patched intake service valid types for COD Services")

    if getattr(intake_mod, "_COD_SERVICES_DEFAULT_TEMPLATE_APPLIED", False):
        return True

    original = getattr(intake_mod, "_default_questions_for_intake_type", None)
    if not callable(original):
        _warn("intake_service._default_questions_for_intake_type is not callable")
        return False

    def default_questions_for_intake_type(intake_type: str):
        kind = _safe_str(intake_type).lower().replace("-", "_")
        if kind in _COD_INTAKE_TYPES:
            return _cod_questions(intake_mod)
        return original(intake_type)

    intake_mod._default_questions_for_intake_type = default_questions_for_intake_type
    setattr(intake_mod, "_COD_SERVICES_DEFAULT_TEMPLATE_APPLIED", True)
    _log("patched intake service default COD Services questions")
    return True


def apply() -> bool:
    ok = True
    ok = _install_setup_category() and ok
    ok = _install_category_admin_type() and ok
    ok = _install_intake_service_type() and ok
    return bool(ok)


apply()

__all__ = ["apply"]
