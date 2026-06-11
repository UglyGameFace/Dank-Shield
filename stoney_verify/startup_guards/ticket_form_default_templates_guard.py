from __future__ import annotations

"""Default ticket form templates for the public issue menu.

The issue/category selection menu remains the first step. This guard makes forms
feel product-ready without requiring server owners to hand-edit Supabase rows:

- dashboard-provided questions always win
- recognized categories get sensible built-in form templates
- verification stays one-click after category confirmation by default
- dashboard can opt out with form_config.disable_default_template=true
- custom COD/modded-lobby categories are recognized by name/slug
"""

import json
import re
from typing import Any, Dict, List


def _log(message: str) -> None:
    try:
        print(f"✅ ticket_form_default_templates_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_form_default_templates_guard: {message}")
    except Exception:
        pass


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        text = str(value).strip().lower()
        if not text:
            return default
        return text in {"1", "true", "yes", "y", "on", "required"}
    except Exception:
        return default


def _jsonish(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    text = _safe_str(value)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _slug_text(row: Dict[str, Any]) -> str:
    parts = [
        row.get("slug"),
        row.get("category_slug"),
        row.get("name"),
        row.get("display_name"),
        row.get("title"),
        row.get("description"),
    ]
    return " ".join(_safe_str(p).lower() for p in parts if _safe_str(p))


def _template_key(row: Dict[str, Any]) -> str:
    text = _slug_text(row)
    compact = re.sub(r"[^a-z0-9]+", "-", text).strip("-")

    if any(token in compact for token in ("cod", "call-of-duty", "black-ops", "bo3", "bo2", "mw2", "mw3", "modded-lobby", "modded-lobbies", "lobby")):
        return "cod"
    if "verify" in compact or "verification" in compact:
        return "verification"
    if "appeal" in compact or "ban" in compact or "mute" in compact or "timeout" in compact:
        return "appeal"
    if "report" in compact or "scam" in compact or "abuse" in compact or "raid" in compact:
        return "report"
    if "bug" in compact or "technical" in compact or "issue" in compact:
        return "bug"
    if "question" in compact or "other" in compact:
        return "question"
    if "support" in compact or "help" in compact or "general" in compact:
        return "support"
    return "support"


DEFAULT_TEMPLATES: Dict[str, List[Dict[str, Any]]] = {
    "support": [
        {
            "key": "summary",
            "label": "What do you need help with?",
            "placeholder": "Explain the issue clearly.",
            "required": True,
            "style": "paragraph",
            "max_length": 1000,
        },
        {
            "key": "proof",
            "label": "Any screenshots or proof?",
            "placeholder": "Paste links or say none.",
            "required": False,
            "style": "paragraph",
            "max_length": 1000,
        },
    ],
    # Verification tickets intentionally have no default pre-ticket modal.
    # Members already confirm the category; the verification panel inside the
    # private ticket is the actual next action. This keeps verification from
    # becoming a category confirm -> intake form -> verify panel three-step flow.
    "verification": [],
    "report": [
        {
            "key": "reported_user",
            "label": "Who are you reporting?",
            "placeholder": "Mention, username, or user ID if you have it.",
            "required": True,
            "style": "short",
            "max_length": 200,
        },
        {
            "key": "what_happened",
            "label": "What happened?",
            "placeholder": "Explain the scam, abuse, spam, raid, or rule break.",
            "required": True,
            "style": "paragraph",
            "max_length": 1200,
        },
        {
            "key": "evidence",
            "label": "Evidence / message links",
            "placeholder": "Paste message links, screenshots, or say none.",
            "required": False,
            "style": "paragraph",
            "max_length": 1000,
        },
    ],
    "appeal": [
        {
            "key": "action_appealed",
            "label": "What are you appealing?",
            "placeholder": "Ban, timeout, mute, role removal, etc.",
            "required": True,
            "style": "short",
            "max_length": 200,
        },
        {
            "key": "why_review",
            "label": "Why should staff review it?",
            "placeholder": "Explain what happened and why you believe it should be changed.",
            "required": True,
            "style": "paragraph",
            "max_length": 1200,
        },
    ],
    "bug": [
        {
            "key": "bug_summary",
            "label": "What is broken?",
            "placeholder": "Name the command, panel, page, or workflow.",
            "required": True,
            "style": "paragraph",
            "max_length": 1000,
        },
        {
            "key": "steps",
            "label": "How can staff reproduce it?",
            "placeholder": "List what you clicked or typed before it happened.",
            "required": False,
            "style": "paragraph",
            "max_length": 1000,
        },
    ],
    "question": [
        {
            "key": "question",
            "label": "What is your question?",
            "placeholder": "Ask clearly so staff can answer faster.",
            "required": True,
            "style": "paragraph",
            "max_length": 1000,
        }
    ],
    "cod": [
        {
            "key": "game",
            "label": "Which COD game?",
            "placeholder": "Example: BO2, BO3, MW2, MW3, Ghosts, etc.",
            "required": True,
            "style": "short",
            "max_length": 120,
        },
        {
            "key": "service",
            "label": "What do you need done?",
            "placeholder": "Rank, unlocks, modded lobby info, recovery question, etc.",
            "required": True,
            "style": "paragraph",
            "max_length": 1000,
        },
        {
            "key": "platform",
            "label": "Platform / console",
            "placeholder": "Xbox, PlayStation, PC, etc.",
            "required": True,
            "style": "short",
            "max_length": 120,
        },
        {
            "key": "availability",
            "label": "Best time to reach you?",
            "placeholder": "Timezone and when you are usually available.",
            "required": False,
            "style": "short",
            "max_length": 200,
        },
    ],
}


def _extract_dashboard_questions(forms_mod: Any, row: Dict[str, Any]) -> List[Dict[str, Any]]:
    questions: List[Dict[str, Any]] = []
    for key in ("form_questions", "questions", "intake_questions", "fields"):
        questions.extend(forms_mod._as_question_list(row.get(key)))
    for key in ("form_config", "form_schema", "metadata", "config"):
        parsed = forms_mod._jsonish(row.get(key))
        if isinstance(parsed, dict):
            for nested_key in ("questions", "form_questions", "intake_questions", "fields", "items"):
                questions.extend(forms_mod._as_question_list(parsed.get(nested_key)))
    return questions


def _disabled_by_dashboard(row: Dict[str, Any]) -> bool:
    for key in ("form_config", "form_schema", "metadata", "config"):
        parsed = _jsonish(row.get(key))
        if isinstance(parsed, dict):
            if _safe_bool(parsed.get("disable_default_template"), False):
                return True
            if _safe_bool(parsed.get("forms_disabled"), False):
                return True
            if parsed.get("enabled") is False:
                return True
            if _safe_str(parsed.get("mode")).lower() in {"off", "disabled", "none"}:
                return True
    return False


def _dedupe(forms_mod: Any, questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        label = forms_mod._question_label(q)
        key = label.lower()
        if not label or key in seen:
            continue
        seen.add(key)
        out.append(dict(q))
        if len(out) >= 5:
            break
    return out


def apply() -> bool:
    try:
        from . import ticket_forms_foundation_guard as forms_mod
    except Exception as e:
        _warn(f"could not import ticket_forms_foundation_guard: {e!r}")
        return False

    if getattr(forms_mod, "_DEFAULT_TICKET_FORM_TEMPLATES_APPLIED", False):
        return True

    try:
        def category_questions(row: Dict[str, Any]) -> List[Dict[str, Any]]:
            row = row if isinstance(row, dict) else {}
            dashboard_questions = _dedupe(forms_mod, _extract_dashboard_questions(forms_mod, row))
            if dashboard_questions:
                return dashboard_questions
            if _disabled_by_dashboard(row):
                return []
            return _dedupe(forms_mod, list(DEFAULT_TEMPLATES.get(_template_key(row), DEFAULT_TEMPLATES["support"])))

        def form_enabled(row: Dict[str, Any]) -> bool:
            return bool(category_questions(row))

        forms_mod._category_questions = category_questions
        forms_mod._form_enabled = form_enabled
        setattr(forms_mod, "_DEFAULT_TICKET_FORM_TEMPLATES_APPLIED", True)
        _log("installed default ticket form templates with one-step verification default and dashboard override support")
        return True
    except Exception as e:
        _warn(f"patch failed: {e!r}")
        return False


apply()

__all__ = ["apply", "DEFAULT_TEMPLATES"]
