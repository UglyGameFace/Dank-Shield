from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional, Tuple

from ..globals import get_supabase, now_utc


# ============================================================
# tickets_new/intake_service.py
# ------------------------------------------------------------
# Purpose:
# - centralize ticket category loading from Supabase
# - normalize dashboard-driven ticket category rows
# - infer best category from a freeform reason
# - build structured intake questions per category/intake_type
# - normalize submitted intake answers
# - generate dashboard-safe summary/search payloads
#
# Current schema compatibility:
#   public.ticket_categories
#     - id
#     - guild_id
#     - name
#     - slug
#     - color
#     - staff_role_ids
#     - staff_role_names
#     - description
#     - intake_type
#     - match_keywords
#     - button_label
#     - sort_order
#     - is_default
#
# Future-safe compatibility:
#   If later you add any of these optional fields to ticket_categories,
#   this file will automatically use them when present:
#     - intake_questions
#     - form_schema
#     - form_title
#     - dashboard_tags
#     - auto_priority
#     - default_priority
# ============================================================

TICKET_CATEGORIES_TABLE = "ticket_categories"

FALLBACK_SUPPORT_CATEGORY = "support"
FALLBACK_VERIFICATION_CATEGORY = "verification_issue"
FALLBACK_GHOST_CATEGORY = "ghost"

_VALID_INTAKE_TYPES = {
    "general",
    "verification",
    "appeal",
    "report",
    "partnership",
    "question",
    "custom",
}

_VALID_PRIORITY_VALUES = {"low", "medium", "high", "urgent"}


# ============================================================
# Small helpers
# ============================================================

def _debug(msg: str) -> None:
    try:
        print(f"🧩 intake_service {msg}")
    except Exception:
        pass


def _sb():
    try:
        return get_supabase()
    except Exception:
        return None


def _safe_str(value: Any) -> str:
    try:
        return str(value)
    except Exception:
        return ""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = _safe_str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _clean_text(value: Any, limit: int = 4000) -> str:
    try:
        return re.sub(r"\s+", " ", _safe_str(value)).strip()[:limit]
    except Exception:
        return ""


def _clean_multiline_text(value: Any, limit: int = 4000) -> str:
    try:
        text = _safe_str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
        return text[:limit]
    except Exception:
        return ""


def _slugify(value: str) -> str:
    try:
        text = _safe_str(value).strip().lower()
        text = text.replace("&", " and ")
        text = re.sub(r"[^a-z0-9_\-\s]+", "", text)
        text = re.sub(r"[\s\-]+", "_", text).strip("_")
        return text
    except Exception:
        return ""


def _tokenize_text(text: str) -> List[str]:
    cleaned = _slugify(text).replace("_", " ")
    return [part for part in cleaned.split() if part]


def _truncate(text: str, limit: int = 280) -> str:
    raw = _safe_str(text).strip()
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 1)] + "…"


def _normalize_string_list(value: Any, *, item_limit: int = 120) -> List[str]:
    out: List[str] = []

    try:
        if isinstance(value, list):
            raw_items = value
        elif value is None:
            raw_items = []
        else:
            raw_items = str(value).split(",")

        for item in raw_items:
            cleaned = _clean_text(item, limit=item_limit)
            if cleaned and cleaned not in out:
                out.append(cleaned)
    except Exception:
        pass

    return out


def _normalize_role_ids(value: Any) -> List[int]:
    out: List[int] = []
    seen: set[int] = set()

    try:
        items = value if isinstance(value, list) else []
        for item in items:
            rid = _safe_int(item, 0)
            if rid <= 0 or rid in seen:
                continue
            seen.add(rid)
            out.append(rid)
    except Exception:
        pass

    return out


def _safe_json_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else []


def _safe_json_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


# ============================================================
# Category normalization
# ============================================================

def normalize_category_row(row: Dict[str, Any]) -> Dict[str, Any]:
    slug = _slugify(_safe_str(row.get("slug") or row.get("name") or "")) or FALLBACK_SUPPORT_CATEGORY
    name = _clean_text(row.get("name") or slug.title(), limit=200)
    description = _clean_text(row.get("description"), limit=700)
    intake_type = _slugify(_safe_str(row.get("intake_type") or "general")) or "general"

    if intake_type not in _VALID_INTAKE_TYPES:
        intake_type = "general"

    default_priority = _slugify(_safe_str(row.get("default_priority") or row.get("auto_priority") or "medium"))
    if default_priority not in _VALID_PRIORITY_VALUES:
        default_priority = "medium"

    normalized: Dict[str, Any] = {
        "id": row.get("id"),
        "guild_id": _safe_str(row.get("guild_id")),
        "name": name or "Support",
        "slug": slug,
        "description": description,
        "color": _clean_text(row.get("color"), limit=30) or "#45d483",
        "intake_type": intake_type,
        "match_keywords": _normalize_string_list(row.get("match_keywords")),
        "button_label": _clean_text(row.get("button_label"), limit=120) or name or "Open Ticket",
        "sort_order": row.get("sort_order"),
        "is_default": _safe_bool(row.get("is_default"), False),
        "staff_role_ids": _normalize_role_ids(row.get("staff_role_ids")),
        "staff_role_names": _normalize_string_list(row.get("staff_role_names"), item_limit=120),
        "default_priority": default_priority,
        "dashboard_tags": _normalize_string_list(row.get("dashboard_tags"), item_limit=120),
        "intake_questions": _safe_json_list(row.get("intake_questions")),
        "form_schema": _safe_json_dict(row.get("form_schema")),
        "form_title": _clean_text(row.get("form_title"), limit=120),
        "raw": dict(row),
    }

    return normalized


def _sort_categories(categories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    try:
        return sorted(
            categories,
            key=lambda c: (
                c.get("sort_order") is None,
                c.get("sort_order") if c.get("sort_order") is not None else 10_000,
                _safe_str(c.get("name")).lower(),
            ),
        )
    except Exception:
        return categories


# ============================================================
# Category loading
# ============================================================

def _fetch_ticket_categories_sync(guild_id: int | str) -> List[Dict[str, Any]]:
    sb = _sb()
    if sb is None:
        _debug(f"fetch categories skipped no-supabase guild={guild_id}")
        return []

    try:
        resp = (
            sb.table(TICKET_CATEGORIES_TABLE)
            .select("*")
            .eq("guild_id", str(guild_id))
            .execute()
        )
        rows = getattr(resp, "data", None) or []
    except Exception as e:
        print(f"⚠️ intake_service category fetch failed guild={guild_id}: {repr(e)}")
        return []

    normalized: List[Dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            normalized.append(normalize_category_row(row))

    normalized = _sort_categories(normalized)
    _debug(f"fetch categories guild={guild_id} count={len(normalized)}")
    return normalized


async def fetch_ticket_categories(guild_id: int | str) -> List[Dict[str, Any]]:
    try:
        return await asyncio.to_thread(_fetch_ticket_categories_sync, guild_id)
    except Exception as e:
        print(f"⚠️ intake_service async category fetch failed guild={guild_id}: {repr(e)}")
        return []


async def get_category_by_slug(
    *,
    guild_id: int | str,
    slug: str,
) -> Optional[Dict[str, Any]]:
    slug_clean = _slugify(slug)
    if not slug_clean:
        return None

    categories = await fetch_ticket_categories(guild_id)
    for cat in categories:
        if _safe_str(cat.get("slug")).lower() == slug_clean:
            return cat
    return None


async def get_default_category(
    *,
    guild_id: int | str,
) -> Dict[str, Any]:
    categories = await fetch_ticket_categories(guild_id)

    for cat in categories:
        if _safe_bool(cat.get("is_default"), False):
            return cat

    for cat in categories:
        slug = _safe_str(cat.get("slug")).lower()
        if slug in {"support", "general_support", "general_support_ticket", "general"}:
            return cat

    return {
        "id": None,
        "guild_id": _safe_str(guild_id),
        "name": "Support",
        "slug": FALLBACK_SUPPORT_CATEGORY,
        "description": "",
        "color": "#45d483",
        "intake_type": "general",
        "match_keywords": [],
        "button_label": "Create Ticket",
        "sort_order": None,
        "is_default": True,
        "staff_role_ids": [],
        "staff_role_names": [],
        "default_priority": "medium",
        "dashboard_tags": [],
        "intake_questions": [],
        "form_schema": {},
        "form_title": "",
        "raw": {},
    }


# ============================================================
# Category inference
# ============================================================

def _reason_has_cod_legacy_signals(reason: str) -> bool:
    text = f" {_clean_text(reason, limit=1200).lower()} "
    signals = (
        " mw2 ", " mw3 ", " bo1 ", " bo2 ", " bo3 ",
        " world at war ", " waw ", " ghosts ", " cod ghosts ",
        " advanced warfare ", " aw ", " infinite warfare ", " iw ",
        " recovery ", " recoveries ", " challenge lobby ", " challenge lobbies ",
        " unlock all ", " mod menu ", " rgh ", " jtag ",
        " old cod ", " older cod ", " legacy cod ",
    )
    return any(s in text for s in signals)


def score_reason_against_category(reason: str, category: Dict[str, Any]) -> int:
    reason_norm = _clean_text(reason, limit=2000).lower()
    reason_tokens = set(_tokenize_text(reason_norm))

    slug = _safe_str(category.get("slug")).lower()
    name = _safe_str(category.get("name")).lower()
    desc = _safe_str(category.get("description")).lower()
    keywords = [str(x).lower() for x in (category.get("match_keywords") or [])]

    score = 0

    for kw in keywords:
        kw_clean = _clean_text(kw, limit=120).lower()
        if not kw_clean:
            continue
        if kw_clean in reason_norm:
            score += 25
            if len(kw_clean.split()) > 1:
                score += 10

    slug_words = [w for w in re.split(r"[-_\s]+", slug) if w]
    name_words = _tokenize_text(name)
    desc_words = _tokenize_text(desc)

    for word in slug_words:
        if len(word) >= 3 and word in reason_tokens:
            score += 6

    for word in name_words:
        if len(word) >= 3 and word in reason_tokens:
            score += 5

    for word in desc_words[:25]:
        if len(word) >= 4 and word in reason_tokens:
            score += 2

    intake_type = _safe_str(category.get("intake_type")).lower()

    if intake_type == "appeal" and any(x in reason_norm for x in ["appeal", "unban", "timeout", "ban", "muted", "banned"]):
        score += 6
    elif intake_type == "report" and any(x in reason_norm for x in ["report", "scam", "abuse", "harassment", "threat"]):
        score += 6
    elif intake_type == "partnership" and any(x in reason_norm for x in ["partner", "partnership", "collab", "promo", "sponsor"]):
        score += 6
    elif intake_type == "question" and any(x in reason_norm for x in ["question", "help", "how do i", "how to"]):
        score += 4
    elif intake_type == "verification" and any(x in reason_norm for x in ["verify", "verification", "id", "secure upload", "vc verify"]):
        score += 8

    if _reason_has_cod_legacy_signals(reason_norm):
        haystack = f"{slug} {name} {desc} {' '.join(keywords)}"
        if any(x in haystack for x in [
            "cod", "call of duty", "legacy", "older", "old school", "recovery",
            "recoveries", "challenge lobby", "challenge lobbies", "unlock all",
            "mod menu", "mw2", "mw3", "bo2", "bo3", "ghosts", "waw", "rgh", "jtag"
        ]):
            score += 40

    return score


async def infer_category_from_reason(
    *,
    guild_id: int | str,
    reason: str,
) -> Tuple[Dict[str, Any], int, List[Dict[str, Any]]]:
    categories = await fetch_ticket_categories(guild_id)

    if not categories:
        fallback = await get_default_category(guild_id=guild_id)
        _debug(f"infer category fallback=no-categories guild={guild_id}")
        return fallback, 0, []

    best: Optional[Dict[str, Any]] = None
    best_score = 0

    for cat in categories:
        score = score_reason_against_category(reason, cat)
        if score > best_score:
            best_score = score
            best = cat

    if best is not None and best_score > 0:
        _debug(
            f"infer category matched guild={guild_id} "
            f"slug={best.get('slug')} score={best_score}"
        )
        return best, best_score, categories

    default_cat = await get_default_category(guild_id=guild_id)
    _debug(
        f"infer category default guild={guild_id} "
        f"slug={default_cat.get('slug')} score=0"
    )
    return default_cat, 0, categories


# ============================================================
# Intake question builders
# ============================================================

def _make_question(
    *,
    key: str,
    label: str,
    placeholder: str,
    style: str = "paragraph",
    required: bool = True,
    max_length: int = 600,
    row: int = 0,
    help_text: str = "",
) -> Dict[str, Any]:
    return {
        "key": _slugify(key) or "field",
        "label": _clean_text(label, limit=80) or "Question",
        "placeholder": _clean_text(placeholder, limit=200) or "",
        "style": "short" if str(style).lower() == "short" else "paragraph",
        "required": bool(required),
        "max_length": max(1, min(int(max_length or 600), 4000)),
        "row": max(0, int(row or 0)),
        "help_text": _clean_text(help_text, limit=200),
    }


def _default_questions_for_intake_type(intake_type: str) -> List[Dict[str, Any]]:
    kind = _slugify(intake_type) or "general"

    if kind == "verification":
        return [
            _make_question(
                key="issue_summary",
                label="What do you need help with?",
                placeholder="Example: my secure upload expired, VC verify issue, wrong role after approval",
                style="paragraph",
                max_length=600,
                row=0,
            ),
            _make_question(
                key="current_problem",
                label="What happened?",
                placeholder="Tell staff what is failing or what you already tried.",
                style="paragraph",
                max_length=800,
                row=1,
            ),
        ]

    if kind == "appeal":
        return [
            _make_question(
                key="appeal_reason",
                label="Why are you appealing?",
                placeholder="Explain what happened and why staff should review this.",
                style="paragraph",
                max_length=800,
                row=0,
            ),
            _make_question(
                key="additional_context",
                label="Anything else staff should know?",
                placeholder="Optional extra context, proof, timing, misunderstandings, etc.",
                style="paragraph",
                required=False,
                max_length=800,
                row=1,
            ),
        ]

    if kind == "report":
        return [
            _make_question(
                key="report_subject",
                label="Who or what are you reporting?",
                placeholder="User, channel, staff member, scam, abuse, etc.",
                style="short",
                max_length=120,
                row=0,
            ),
            _make_question(
                key="report_details",
                label="What happened?",
                placeholder="Give a clear explanation with as much detail as possible.",
                style="paragraph",
                max_length=1000,
                row=1,
            ),
        ]

    if kind == "partnership":
        return [
            _make_question(
                key="partner_name",
                label="Who are you / what community is this for?",
                placeholder="Brand, server, creator, org, or community name",
                style="short",
                max_length=120,
                row=0,
            ),
            _make_question(
                key="partnership_pitch",
                label="What partnership are you proposing?",
                placeholder="Explain the collab, promo, sponsor, cross-post, event, etc.",
                style="paragraph",
                max_length=900,
                row=1,
            ),
        ]

    if kind == "question":
        return [
            _make_question(
                key="question_topic",
                label="What do you need help with?",
                placeholder="Summarize your question",
                style="short",
                max_length=150,
                row=0,
            ),
            _make_question(
                key="question_details",
                label="Question details",
                placeholder="Add details so staff can help faster.",
                style="paragraph",
                max_length=900,
                row=1,
            ),
        ]

    if kind == "custom":
        return [
            _make_question(
                key="reason",
                label="Tell us what you need",
                placeholder="Describe the issue clearly.",
                style="paragraph",
                max_length=1000,
                row=0,
            ),
        ]

    return [
        _make_question(
            key="reason",
            label="What do you need help with?",
            placeholder="Describe your issue clearly so the correct staff can help.",
            style="paragraph",
            max_length=1000,
            row=0,
        ),
    ]


def _normalize_custom_question(row: Dict[str, Any], index: int) -> Optional[Dict[str, Any]]:
    if not isinstance(row, dict):
        return None

    key = _slugify(_safe_str(row.get("key") or row.get("id") or f"field_{index+1}"))
    label = _clean_text(row.get("label") or row.get("title") or f"Question {index + 1}", limit=80)
    placeholder = _clean_text(row.get("placeholder") or row.get("hint") or "", limit=200)
    style = _safe_str(row.get("style") or row.get("type") or "paragraph").lower()
    required = _safe_bool(row.get("required"), True)
    max_length = _safe_int(row.get("max_length") or row.get("maxlength") or 600, 600)
    help_text = _clean_text(row.get("help_text") or row.get("description") or "", limit=200)

    if not key or not label:
        return None

    return _make_question(
        key=key,
        label=label,
        placeholder=placeholder,
        style="short" if style in {"short", "singleline", "input"} else "paragraph",
        required=required,
        max_length=max_length,
        row=index,
        help_text=help_text,
    )


def build_intake_questions_for_category(category: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(category, dict):
        return _default_questions_for_intake_type("general")

    raw_custom_questions = category.get("intake_questions")
    if isinstance(raw_custom_questions, list) and raw_custom_questions:
        out: List[Dict[str, Any]] = []
        for index, item in enumerate(raw_custom_questions):
            if isinstance(item, dict):
                norm = _normalize_custom_question(item, index)
                if norm is not None:
                    out.append(norm)
        if out:
            return out

    form_schema = category.get("form_schema")
    if isinstance(form_schema, dict):
        questions = form_schema.get("questions")
        if isinstance(questions, list) and questions:
            out: List[Dict[str, Any]] = []
            for index, item in enumerate(questions):
                if isinstance(item, dict):
                    norm = _normalize_custom_question(item, index)
                    if norm is not None:
                        out.append(norm)
            if out:
                return out

    return _default_questions_for_intake_type(_safe_str(category.get("intake_type") or "general"))


def build_intake_modal_title(category: Dict[str, Any]) -> str:
    form_title = _clean_text(category.get("form_title"), limit=45)
    if form_title:
        return form_title

    name = _clean_text(category.get("name"), limit=45)
    if name:
        return f"{name} Intake"[:45]

    return "Create Ticket"


# ============================================================
# Intake answer normalization
# ============================================================

def normalize_intake_answers(
    *,
    questions: List[Dict[str, Any]],
    answers: Dict[str, Any],
) -> Dict[str, str]:
    out: Dict[str, str] = {}
    raw = dict(answers or {})

    for question in questions:
        if not isinstance(question, dict):
            continue

        key = _slugify(_safe_str(question.get("key")))
        if not key:
            continue

        style = _safe_str(question.get("style") or "paragraph").lower()
        max_length = _safe_int(question.get("max_length") or 600, 600)

        value = raw.get(key)
        if style == "short":
            cleaned = _clean_text(value, limit=max_length)
        else:
            cleaned = _clean_multiline_text(value, limit=max_length)

        if cleaned:
            out[key] = cleaned
        else:
            out[key] = ""

    return out


def build_answer_preview_lines(
    *,
    questions: List[Dict[str, Any]],
    answers: Dict[str, str],
    include_empty: bool = False,
) -> List[str]:
    lines: List[str] = []

    for question in questions:
        if not isinstance(question, dict):
            continue

        key = _slugify(_safe_str(question.get("key")))
        label = _clean_text(question.get("label") or key.title(), limit=80)
        value = _safe_str((answers or {}).get(key)).strip()

        if not value and not include_empty:
            continue

        lines.append(f"{label}: {value or '—'}")

    return lines


def build_intake_summary(
    *,
    category: Dict[str, Any],
    questions: List[Dict[str, Any]],
    answers: Dict[str, str],
) -> str:
    lines = build_answer_preview_lines(
        questions=questions,
        answers=answers,
        include_empty=False,
    )

    if not lines:
        category_name = _safe_str(category.get("name") or "Support").strip()
        return f"{category_name} ticket created."

    first = lines[0]
    second = lines[1] if len(lines) > 1 else ""
    summary = first if not second else f"{first} | {second}"
    return _truncate(summary, limit=300)


def build_intake_search_text(
    *,
    category: Dict[str, Any],
    answers: Dict[str, str],
) -> str:
    parts: List[str] = [
        _safe_str(category.get("slug")),
        _safe_str(category.get("name")),
        _safe_str(category.get("intake_type")),
    ]

    for value in (answers or {}).values():
        text = _safe_str(value).strip()
        if text:
            parts.append(text)

    search_text = " ".join(part for part in parts if part).strip()
    return _truncate(search_text, limit=2500)


def build_dashboard_intake_payload(
    *,
    guild_id: int | str,
    category: Dict[str, Any],
    questions: List[Dict[str, Any]],
    answers: Dict[str, str],
    source: str,
    requester_id: Optional[int | str] = None,
) -> Dict[str, Any]:
    summary = build_intake_summary(
        category=category,
        questions=questions,
        answers=answers,
    )

    payload: Dict[str, Any] = {
        "guild_id": _safe_str(guild_id),
        "category_id": category.get("id"),
        "category_slug": _safe_str(category.get("slug")),
        "category_name": _safe_str(category.get("name")),
        "intake_type": _safe_str(category.get("intake_type")),
        "default_priority": _safe_str(category.get("default_priority") or "medium"),
        "staff_role_ids": list(category.get("staff_role_ids") or []),
        "staff_role_names": list(category.get("staff_role_names") or []),
        "dashboard_tags": list(category.get("dashboard_tags") or []),
        "summary": summary,
        "search_text": build_intake_search_text(category=category, answers=answers),
        "answers": dict(answers or {}),
        "questions": [
            {
                "key": _safe_str(q.get("key")),
                "label": _safe_str(q.get("label")),
                "style": _safe_str(q.get("style")),
                "required": _safe_bool(q.get("required"), True),
            }
            for q in (questions or [])
            if isinstance(q, dict)
        ],
        "source": _safe_str(source),
        "requester_id": _safe_str(requester_id),
        "captured_at": now_utc().isoformat(),
    }

    return payload


# ============================================================
# Public orchestration helpers
# ============================================================

async def resolve_intake_for_reason(
    *,
    guild_id: int | str,
    reason: str,
    source: str = "ticket_reason_modal",
    requester_id: Optional[int | str] = None,
) -> Dict[str, Any]:
    category, score, categories = await infer_category_from_reason(
        guild_id=guild_id,
        reason=reason,
    )

    questions = build_intake_questions_for_category(category)
    answers = normalize_intake_answers(
        questions=questions,
        answers={"reason": reason, "issue_summary": reason, "question_topic": reason},
    )

    payload = build_dashboard_intake_payload(
        guild_id=guild_id,
        category=category,
        questions=questions,
        answers=answers,
        source=source,
        requester_id=requester_id,
    )

    return {
        "ok": True,
        "category": category,
        "category_score": int(score),
        "questions": questions,
        "answers": answers,
        "payload": payload,
        "categories": categories,
    }


async def resolve_intake_for_category(
    *,
    guild_id: int | str,
    category_slug: str,
    answers: Optional[Dict[str, Any]] = None,
    source: str = "ticket_category_submit",
    requester_id: Optional[int | str] = None,
) -> Dict[str, Any]:
    category = await get_category_by_slug(
        guild_id=guild_id,
        slug=category_slug,
    )

    if category is None:
        category = await get_default_category(guild_id=guild_id)

    questions = build_intake_questions_for_category(category)
    normalized_answers = normalize_intake_answers(
        questions=questions,
        answers=dict(answers or {}),
    )

    payload = build_dashboard_intake_payload(
        guild_id=guild_id,
        category=category,
        questions=questions,
        answers=normalized_answers,
        source=source,
        requester_id=requester_id,
    )

    return {
        "ok": True,
        "category": category,
        "questions": questions,
        "answers": normalized_answers,
        "payload": payload,
    }


async def intake_healthcheck(guild_id: int | str) -> Dict[str, Any]:
    categories = await fetch_ticket_categories(guild_id)

    return {
        "ok": True,
        "guild_id": _safe_str(guild_id),
        "category_count": len(categories),
        "category_slugs": [_safe_str(c.get("slug")) for c in categories],
        "has_default": any(_safe_bool(c.get("is_default"), False) for c in categories),
        "checked_at": now_utc().isoformat(),
    }


__all__ = [
    "TICKET_CATEGORIES_TABLE",
    "FALLBACK_SUPPORT_CATEGORY",
    "FALLBACK_VERIFICATION_CATEGORY",
    "FALLBACK_GHOST_CATEGORY",
    "normalize_category_row",
    "fetch_ticket_categories",
    "get_category_by_slug",
    "get_default_category",
    "score_reason_against_category",
    "infer_category_from_reason",
    "build_intake_questions_for_category",
    "build_intake_modal_title",
    "normalize_intake_answers",
    "build_answer_preview_lines",
    "build_intake_summary",
    "build_intake_search_text",
    "build_dashboard_intake_payload",
    "resolve_intake_for_reason",
    "resolve_intake_for_category",
    "intake_healthcheck",
]