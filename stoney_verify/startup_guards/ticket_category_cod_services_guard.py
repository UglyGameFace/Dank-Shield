from __future__ import annotations

"""Keep COD Services as a first-class ticket category option.

This guard centralizes neutral Call of Duty category compatibility that servers
expect from the public ticket menu and setup flow.  It supports legacy and modern
COD/Warzone/Zombies routing while avoiding risky public-default wording that
promotes cheats, hacks, anti-cheat bypasses, or similar services.
"""

from typing import Any, Dict, List

_COD_DESCRIPTION = "Call of Duty, Warzone, Zombies, legacy lobby, account, unlock, or service questions. Server owners control exact rules."
_COD_GAME_PLACEHOLDER = "Example: BO2, BO3, MW2, MW3, MW2019, MWII, MWIII, BO6, BO7, Warzone, Zombies, etc."
_COD_SERVICE_PLACEHOLDER = "Describe the COD/Warzone/Zombies question in this server's own terms. Do not include passwords or private account credentials."

_COD_KEYWORDS = [
    "cod",
    "call of duty",
    "call-of-duty",
    "black ops",
    "black ops 1",
    "black ops 2",
    "black ops 3",
    "black ops 4",
    "black ops cold war",
    "black ops 6",
    "black ops 7",
    "bo1",
    "bo2",
    "bo3",
    "bo4",
    "bocw",
    "bo6",
    "bo7",
    "modern warfare",
    "modern warfare 2",
    "modern warfare 3",
    "modern warfare ii",
    "modern warfare iii",
    "mw",
    "mw2",
    "mw3",
    "mw2019",
    "mwii",
    "mwiii",
    "warzone",
    "wz",
    "wz2",
    "world at war",
    "waw",
    "ghosts",
    "advanced warfare",
    "infinite warfare",
    "wwii",
    "vanguard",
    "zombies",
    "dmz",
    "ranked play",
    "camo",
    "camos",
    "unlock",
    "unlocks",
    "unlock all",
    "account",
    "platform",
    "lobby",
    "lobbies",
    "private lobby",
    "custom lobby",
    "modded lobby",
    "challenge lobby",
    "recovery",
    "rgh",
    "jtag",
]

_COD_CATEGORY: Dict[str, Any] = {
    "slug": "cod_services",
    "name": "Call of Duty Services",
    "description": _COD_DESCRIPTION,
    "intake_type": "cod_services",
    "match_keywords": list(_COD_KEYWORDS),
    "is_default": False,
    "sort_order": 25,
}

_COD_INTAKE_TYPES = {"cod", "cod_services", "call_of_duty", "modern_cod", "warzone", "game_services"}


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
        for key in ("slug", "name", "description", "intake_type", "button_label")
    )
    return any(token in text for token in _COD_KEYWORDS)


def _install_public_panel_wording() -> bool:
    try:
        from . import public_ticket_panel_clean_hardening as panel_guard
    except Exception as exc:
        _warn(f"could not import public_ticket_panel_clean_hardening: {exc!r}")
        return False

    try:
        allowed = getattr(panel_guard, "_ALLOWED_MENU_KEYS", None)
        if isinstance(allowed, set):
            allowed.add("cod_services")

        labels = getattr(panel_guard, "_MENU_LABELS", None)
        if isinstance(labels, dict):
            labels["cod_services"] = "Call of Duty Services"

        descriptions = getattr(panel_guard, "_MENU_DESCRIPTIONS", None)
        if isinstance(descriptions, dict):
            descriptions["cod_services"] = _COD_DESCRIPTION

        defaults = tuple(getattr(panel_guard, "_DEFAULT_PUBLIC_ROWS", ()) or ())
        next_rows: List[Dict[str, Any]] = []
        saw_cod = False
        for row in defaults:
            if isinstance(row, dict) and _safe_str(row.get("slug")).lower().replace("-", "_") == "cod_services":
                patched = dict(row)
                patched["name"] = "Call of Duty Services"
                patched["button_label"] = "Call of Duty Services"
                patched["description"] = _COD_DESCRIPTION
                patched.setdefault("intake_type", "cod_services")
                next_rows.append(patched)
                saw_cod = True
            else:
                next_rows.append(row)
        if not saw_cod:
            next_rows.append(dict(_COD_CATEGORY))
        panel_guard._DEFAULT_PUBLIC_ROWS = tuple(next_rows)
        _log("patched public panel COD wording for legacy and modern COD support")
        return True
    except Exception as exc:
        _warn(f"public panel COD wording patch failed: {exc!r}")
        return False


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
    else:
        patched_rows: List[Dict[str, Any]] = []
        for row in rows:
            if isinstance(row, dict) and _is_cod_row(row):
                patched = dict(row)
                patched.setdefault("slug", "cod_services")
                patched.setdefault("name", "Call of Duty Services")
                patched["description"] = _COD_DESCRIPTION
                patched.setdefault("intake_type", "cod_services")
                keywords = list(patched.get("match_keywords") or [])
                patched["match_keywords"] = list(dict.fromkeys([*keywords, *_COD_KEYWORDS]))
                patched_rows.append(patched)
                changed = True
            else:
                patched_rows.append(row)
        setup_mod.RECOMMENDED_CATEGORIES = tuple(patched_rows)

    options = tuple(getattr(setup_mod, "INTAKE_TYPE_OPTIONS", ()) or ())
    wanted = ("cod_services", "modern_cod", "warzone")
    missing = [item for item in wanted if item not in options]
    if missing:
        setup_mod.INTAKE_TYPE_OPTIONS = tuple(dict.fromkeys((*options, *missing)))
        changed = True

    if changed:
        _log("patched setup recommended categories with modern COD Services coverage")
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
            placeholder=_COD_GAME_PLACEHOLDER,
            style="short",
            max_length=180,
            row=0,
        ),
        maker(
            key="cod_service",
            label="What COD/Warzone/Zombies service or question do you need help with?",
            placeholder=_COD_SERVICE_PLACEHOLDER,
            style="paragraph",
            max_length=1000,
            row=1,
        ),
        maker(
            key="cod_platform",
            label="Platform / account type",
            placeholder="Xbox, PlayStation, PC, Steam, Battle.net, Activision account, etc.",
            style="short",
            max_length=180,
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
    ok = _install_public_panel_wording() and ok
    ok = _install_setup_category() and ok
    ok = _install_category_admin_type() and ok
    ok = _install_intake_service_type() and ok
    return bool(ok)


apply()

__all__ = ["apply"]
