from __future__ import annotations

"""Neutral game-services ticket category support.

Dank Shield should support server-owner-defined game service categories without
shipping public default wording that promotes cheating, hacks, anti-cheat bypass,
or other risky services.  This guard adds neutral routing language and lets owners
rename/configure their own categories through the normal category system.
"""

from typing import Any, Dict, List

_GAME_SERVICES_KEY = "game_services"
_GAME_SERVICES_LABEL = "Game Services"
_GAME_SERVICES_DESCRIPTION = "Route game-related service questions to the right staff. Server owners control the exact services and rules."

_GAME_SERVICES_CATEGORY: Dict[str, Any] = {
    "slug": _GAME_SERVICES_KEY,
    "name": _GAME_SERVICES_LABEL,
    "description": _GAME_SERVICES_DESCRIPTION,
    "intake_type": _GAME_SERVICES_KEY,
    "match_keywords": [
        "game services",
        "game help",
        "account help",
        "lobby help",
        "unlock question",
        "platform support",
        "cod",
        "warzone",
        "fortnite",
        "apex",
        "valorant",
        "minecraft",
        "gta",
    ],
    "is_default": False,
    "sort_order": 27,
}

_GAME_SERVICE_INTAKE_TYPES = {
    "game_services",
    "game_service",
    "game_support",
    "custom_game_services",
    "service_question",
}

_GAME_SERVICE_SIGNALS = (
    "game service",
    "game services",
    "game support",
    "gaming service",
    "gaming services",
    "account help",
    "lobby help",
    "unlock question",
    "platform support",
    "custom service",
    "service question",
)


def _log(message: str) -> None:
    try:
        print(f"✅ ticket_category_game_services_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_category_game_services_guard: {message}")
    except Exception:
        pass


def _safe_str(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _row_text(row: Any) -> str:
    if not isinstance(row, dict):
        return ""
    return " ".join(
        _safe_str(row.get(key)).lower().replace("_", "-")
        for key in ("slug", "name", "description", "intake_type", "button_label")
    )


def _is_game_services_row(row: Any) -> bool:
    text = _row_text(row)
    return any(signal.replace("_", "-") in text for signal in _GAME_SERVICE_SIGNALS)


def _install_public_menu_support() -> bool:
    try:
        from . import public_ticket_panel_clean_hardening as panel_guard
    except Exception as exc:
        _warn(f"could not import public_ticket_panel_clean_hardening: {exc!r}")
        return False

    try:
        allowed = getattr(panel_guard, "_ALLOWED_MENU_KEYS", None)
        if isinstance(allowed, set):
            allowed.add(_GAME_SERVICES_KEY)

        priority = getattr(panel_guard, "_MENU_PRIORITY", None)
        if isinstance(priority, dict):
            priority.setdefault(_GAME_SERVICES_KEY, 3)
            # Keep the old moderation keys after neutral game services.
            for key, value in {"report": 4, "appeal": 5, "bug": 6, "question": 7}.items():
                if key in priority:
                    priority[key] = max(int(priority.get(key, value) or value), value)

        labels = getattr(panel_guard, "_MENU_LABELS", None)
        if isinstance(labels, dict):
            labels.setdefault(_GAME_SERVICES_KEY, _GAME_SERVICES_LABEL)

        descriptions = getattr(panel_guard, "_MENU_DESCRIPTIONS", None)
        if isinstance(descriptions, dict):
            descriptions.setdefault(_GAME_SERVICES_KEY, _GAME_SERVICES_DESCRIPTION)

        rows = tuple(getattr(panel_guard, "_DEFAULT_PUBLIC_ROWS", ()) or ())
        if not any(isinstance(row, dict) and _safe_str(row.get("slug")).lower() == _GAME_SERVICES_KEY for row in rows):
            next_rows: List[Dict[str, Any]] = []
            inserted = False
            for row in rows:
                next_rows.append(row)
                slug = _safe_str(row.get("slug") if isinstance(row, dict) else "").lower().replace("-", "_")
                if not inserted and slug == "cod_services":
                    next_rows.append(dict(_GAME_SERVICES_CATEGORY))
                    inserted = True
            if not inserted:
                next_rows.append(dict(_GAME_SERVICES_CATEGORY))
            panel_guard._DEFAULT_PUBLIC_ROWS = tuple(next_rows)

        original = getattr(panel_guard, "_canonical_menu_key", None)
        if callable(original) and not getattr(panel_guard, "_GAME_SERVICES_CANON_PATCHED", False):
            def canonical_menu_key(panel_mod: Any, row: Dict[str, Any]) -> str:
                if _is_game_services_row(row):
                    return _GAME_SERVICES_KEY
                return original(panel_mod, row)

            panel_guard._canonical_menu_key = canonical_menu_key
            setattr(panel_guard, "_GAME_SERVICES_CANON_PATCHED", True)

        _log("patched neutral Game Services public menu support")
        return True
    except Exception as exc:
        _warn(f"public menu support patch failed: {exc!r}")
        return False


def _install_setup_category() -> bool:
    try:
        from ..commands_ext import public_setup_solid as setup_mod
    except Exception as exc:
        _warn(f"could not import public_setup_solid: {exc!r}")
        return False

    try:
        changed = False
        rows = tuple(getattr(setup_mod, "RECOMMENDED_CATEGORIES", ()) or ())
        if not any(_is_game_services_row(row) for row in rows):
            next_rows: List[Dict[str, Any]] = []
            inserted = False
            for row in rows:
                next_rows.append(row)
                slug = _safe_str(row.get("slug") if isinstance(row, dict) else "").lower().replace("-", "_")
                if not inserted and slug == "cod_services":
                    next_rows.append(dict(_GAME_SERVICES_CATEGORY))
                    inserted = True
            if not inserted:
                next_rows.append(dict(_GAME_SERVICES_CATEGORY))
            setup_mod.RECOMMENDED_CATEGORIES = tuple(next_rows)
            changed = True

        options = tuple(getattr(setup_mod, "INTAKE_TYPE_OPTIONS", ()) or ())
        if _GAME_SERVICES_KEY not in options:
            setup_mod.INTAKE_TYPE_OPTIONS = tuple(dict.fromkeys((*options, _GAME_SERVICES_KEY)))
            changed = True

        if changed:
            _log("patched setup recommended categories with neutral Game Services")
        return True
    except Exception as exc:
        _warn(f"setup category patch failed: {exc!r}")
        return False


def _install_category_admin_type() -> bool:
    try:
        from ..commands_ext import ticket_category_admin as admin_mod
    except Exception as exc:
        _warn(f"could not import ticket_category_admin: {exc!r}")
        return False

    allowed = getattr(admin_mod, "_ALLOWED_INTAKE_TYPES", None)
    if isinstance(allowed, set):
        before = set(allowed)
        allowed.update(_GAME_SERVICE_INTAKE_TYPES)
        if allowed != before:
            _log("patched ticket category admin allowed intake types for neutral Game Services")
    return True


def _game_service_questions(intake_mod: Any):
    maker = getattr(intake_mod, "_make_question")
    return [
        maker(
            key="game_title",
            label="Which game is this for?",
            placeholder="Example: COD, Warzone, Fortnite, Apex, Valorant, Minecraft, GTA, etc.",
            style="short",
            max_length=160,
            row=0,
        ),
        maker(
            key="service_question",
            label="What service or question do you need help with?",
            placeholder="Describe what you need in this server's own terms. Do not include passwords or private account credentials.",
            style="paragraph",
            max_length=1000,
            row=1,
        ),
        maker(
            key="platform_or_account_type",
            label="Platform / account type",
            placeholder="Xbox, PlayStation, PC, mobile, Java/Bedrock, Steam, Epic, Battle.net, etc.",
            style="short",
            required=False,
            max_length=180,
            row=2,
        ),
        maker(
            key="server_rules_acknowledgement",
            label="Anything staff should know about rules or allowed services?",
            placeholder="Optional context. Server owners are responsible for their own rules and services.",
            style="paragraph",
            required=False,
            max_length=700,
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
        valid.update(_GAME_SERVICE_INTAKE_TYPES)
        if valid != before:
            _log("patched intake service valid types for neutral Game Services")

    original = getattr(intake_mod, "_default_questions_for_intake_type", None)
    if not callable(original):
        _warn("intake_service._default_questions_for_intake_type is not callable")
        return False

    if not getattr(intake_mod, "_GAME_SERVICES_DEFAULT_TEMPLATE_APPLIED", False):
        def default_questions_for_intake_type(intake_type: str):
            kind = _safe_str(intake_type).lower().replace("-", "_")
            if kind in _GAME_SERVICE_INTAKE_TYPES:
                return _game_service_questions(intake_mod)
            return original(intake_type)

        intake_mod._default_questions_for_intake_type = default_questions_for_intake_type
        setattr(intake_mod, "_GAME_SERVICES_DEFAULT_TEMPLATE_APPLIED", True)
        _log("patched intake service default neutral Game Services questions")

    return True


def _install_form_template_support() -> bool:
    try:
        from . import ticket_form_default_templates_guard as forms_guard
    except Exception as exc:
        _warn(f"could not import ticket_form_default_templates_guard: {exc!r}")
        return False

    try:
        templates = getattr(forms_guard, "DEFAULT_TEMPLATES", None)
        if isinstance(templates, dict) and _GAME_SERVICES_KEY not in templates:
            templates[_GAME_SERVICES_KEY] = [
                {
                    "key": "game_title",
                    "label": "Which game is this for?",
                    "placeholder": "Example: COD, Warzone, Fortnite, Apex, Valorant, Minecraft, GTA, etc.",
                    "required": True,
                    "style": "short",
                    "max_length": 160,
                },
                {
                    "key": "service_question",
                    "label": "What service or question do you need help with?",
                    "placeholder": "Describe what you need in this server's own terms.",
                    "required": True,
                    "style": "paragraph",
                    "max_length": 1000,
                },
                {
                    "key": "platform_or_account_type",
                    "label": "Platform / account type",
                    "placeholder": "Xbox, PlayStation, PC, mobile, Java/Bedrock, Steam, Epic, Battle.net, etc.",
                    "required": False,
                    "style": "short",
                    "max_length": 180,
                },
            ]

        original = getattr(forms_guard, "_template_key", None)
        if callable(original) and not getattr(forms_guard, "_GAME_SERVICES_TEMPLATE_KEY_PATCHED", False):
            def template_key(row: Dict[str, Any]) -> str:
                if _is_game_services_row(row):
                    return _GAME_SERVICES_KEY
                return original(row)

            forms_guard._template_key = template_key
            setattr(forms_guard, "_GAME_SERVICES_TEMPLATE_KEY_PATCHED", True)

        _log("patched default form templates with neutral Game Services")
        return True
    except Exception as exc:
        _warn(f"form template patch failed: {exc!r}")
        return False


def apply() -> bool:
    ok = True
    ok = _install_public_menu_support() and ok
    ok = _install_setup_category() and ok
    ok = _install_category_admin_type() and ok
    ok = _install_intake_service_type() and ok
    ok = _install_form_template_support() and ok
    return bool(ok)


apply()

__all__ = ["apply"]
