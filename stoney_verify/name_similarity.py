from __future__ import annotations

"""Guild-wide username/display-name similarity for alt review evidence.

Pure helpers: no Discord or database access. The join risk builder in
``raidguard`` owns collecting candidate accounts (current guild members,
recently departed members, and banned users); this module only decides
whether a joining account's name is a *strong* match for a candidate.

A strong match is review evidence, never identity proof: it can raise a
join to "possible alt — review" but must never be treated as a confirmed
link. False-positive discipline is therefore part of the contract:

- names whose alphabetic core is shorter than 5 characters need an exact
  (post-normalization) equality, not a fuzzy score;
- sharing only a common fragment or prefix (``stoney…``, ``420…``) never
  reaches the strong bar;
- containment only counts when the shorter name is a substantial part of
  the longer one.
"""

import re
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

_TOKEN_RE = re.compile(r"[a-z]+|\d+")
_NGRAM_SIZE = 5

# Trailing tokens people commonly append to an alt of the same name.
_ALT_SUFFIXES = (
    "phone",
    "mobile",
    "backup",
    "second",
    "extra",
    "main",
    "real",
    "alt",
    "alts",
    "new",
    "two",
    "2",
    "ii",
)

_LEET_TABLE = str.maketrans(
    {
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        "8": "b",
        "@": "a",
        "$": "s",
    }
)

STRONG_SCORE = 0.90
_MAX_MATCHES = 8


def normalize_name_key(value: Any) -> str:
    """Lowercase alphanumeric-only key; emoji/decoration/punctuation drop out."""
    try:
        text = str(value or "").strip().lower()
    except Exception:
        return ""
    return "".join(ch for ch in text if ch.isalnum())


def _leet_fold(text: str) -> str:
    return text.translate(_LEET_TABLE)


def _strip_alt_suffixes(text: str) -> str:
    out = text
    for _ in range(3):
        stripped = out
        for suffix in _ALT_SUFFIXES:
            if out.endswith(suffix) and len(out) - len(suffix) >= 4:
                stripped = out[: -len(suffix)]
                break
        if stripped == out:
            break
        out = stripped
    return out


def _strip_trailing_digits(text: str) -> str:
    return text.rstrip("0123456789")


def _variants(normalized: str) -> Dict[str, float]:
    """Map comparison form -> trust weight for an exact-equality hit."""
    if not normalized:
        return {}
    forms: Dict[str, float] = {}

    def add(form: str, weight: float) -> None:
        if form and (form not in forms or forms[form] < weight):
            forms[form] = weight

    base = normalized
    stripped = _strip_alt_suffixes(base)
    add(base, 1.0)
    add(_leet_fold(base), 1.0)
    add(stripped, 1.0)
    add(_leet_fold(stripped), 1.0)
    # Dropping a trailing digit run ("name64" vs "name99") is weaker: the
    # alphabetic core must carry the match on its own.
    add(_strip_trailing_digits(stripped), 0.94)
    add(_leet_fold(_strip_trailing_digits(stripped)), 0.94)
    return forms


def _tokens(form: str) -> Set[str]:
    return set(_TOKEN_RE.findall(form))


def _alpha_core(form: str) -> str:
    return "".join(ch for ch in form if ch.isalpha())


def _pair_score(forms_a: Mapping[str, float], forms_b: Mapping[str, float]) -> float:
    best = 0.0
    for form_a, weight_a in forms_a.items():
        for form_b, weight_b in forms_b.items():
            weight = min(weight_a, weight_b)
            if form_a == form_b:
                best = max(best, weight)
                continue
            shorter, longer = (form_a, form_b) if len(form_a) <= len(form_b) else (form_b, form_a)
            if len(shorter) >= 6 and shorter in longer:
                ratio = len(shorter) / max(1, len(longer))
                if ratio >= 0.55:
                    best = max(best, weight * (0.88 + 0.12 * ratio))
            try:
                best = max(best, weight * SequenceMatcher(None, form_a, form_b).ratio())
            except Exception:
                pass
            tokens_a = _tokens(form_a)
            tokens_b = _tokens(form_b)
            if tokens_a and tokens_b and (tokens_a <= tokens_b or tokens_b <= tokens_a):
                smaller = tokens_a if len(tokens_a) <= len(tokens_b) else tokens_b
                if len(smaller) >= 2 and any(len(t) >= 5 and t.isalpha() for t in smaller):
                    best = max(best, weight * 0.92)
    return best


def score_name_pair(name_a: Any, name_b: Any) -> float:
    """Best similarity score (0..1) between two raw names."""
    forms_a = _variants(normalize_name_key(name_a))
    forms_b = _variants(normalize_name_key(name_b))
    if not forms_a or not forms_b:
        return 0.0
    return _pair_score(forms_a, forms_b)


def _min_alpha_core(name_a: Any, name_b: Any) -> int:
    cores = []
    for raw in (name_a, name_b):
        normalized = normalize_name_key(raw)
        cores.append(len(_alpha_core(_strip_alt_suffixes(normalized))))
    return min(cores) if cores else 0


def is_strong_match(score: float, name_a: Any, name_b: Any) -> bool:
    """Apply the false-positive bar on top of a raw pair score."""
    core = _min_alpha_core(name_a, name_b)
    if core >= 5:
        return score >= STRONG_SCORE
    if core >= 4:
        # Short names: only a (post-normalization) exact hit counts.
        return score >= 0.999
    return False


def _ngrams(form: str) -> Set[str]:
    if len(form) < _NGRAM_SIZE:
        return {form} if form else set()
    return {form[i : i + _NGRAM_SIZE] for i in range(len(form) - _NGRAM_SIZE + 1)}


def _prefilter_grams(forms: Mapping[str, float]) -> Set[str]:
    grams: Set[str] = set()
    for form in forms:
        grams |= _ngrams(form)
        grams |= _ngrams(_alpha_core(form))
    return grams


def _shares_prefilter(
    candidate_forms: Mapping[str, float],
    joiner_grams: Set[str],
    joiner_tokens: Set[str],
) -> bool:
    for form in candidate_forms:
        if _ngrams(form) & joiner_grams:
            return True
        if _ngrams(_alpha_core(form)) & joiner_grams:
            return True
        for token in _tokens(form):
            if len(token) >= 5 and token.isalpha() and token in joiner_tokens:
                return True
    return False


def find_name_matches(
    joiner_names: Sequence[Any],
    candidates: Iterable[Mapping[str, Any]],
    *,
    exclude_user_ids: Optional[Iterable[int]] = None,
    limit: int = _MAX_MATCHES,
) -> List[Dict[str, Any]]:
    """Return strong name matches for a joiner against candidate accounts.

    ``candidates`` rows carry ``user_id``, ``username``, ``display_name``
    and ``source`` ("member" | "departed" | "banned"). Each returned row is
    the candidate plus a ``similarity`` score, sorted strongest first.
    """
    excluded = set()
    for raw in exclude_user_ids or ():
        try:
            excluded.add(int(raw))
        except Exception:
            continue

    joiner_forms: List[Mapping[str, float]] = []
    joiner_grams: Set[str] = set()
    joiner_tokens: Set[str] = set()
    for raw in joiner_names:
        forms = _variants(normalize_name_key(raw))
        if forms:
            joiner_forms.append(forms)
            joiner_grams |= _prefilter_grams(forms)
            for form in forms:
                joiner_tokens |= _tokens(form)
    if not joiner_forms or not joiner_grams:
        return []

    best_by_user: Dict[int, Dict[str, Any]] = {}
    for candidate in candidates:
        try:
            user_id = int(candidate.get("user_id") or 0)
        except Exception:
            continue
        if user_id <= 0 or user_id in excluded:
            continue
        if bool(candidate.get("bot") or candidate.get("is_bot")):
            continue

        candidate_names = [
            candidate.get("username"),
            candidate.get("display_name"),
            candidate.get("name"),
            candidate.get("global_name"),
        ]
        candidate_forms: List[Mapping[str, float]] = []
        grams_hit = False
        for raw in candidate_names:
            forms = _variants(normalize_name_key(raw))
            if not forms:
                continue
            candidate_forms.append(forms)
            if not grams_hit and _shares_prefilter(forms, joiner_grams, joiner_tokens):
                grams_hit = True
        if not candidate_forms or not grams_hit:
            continue

        best = 0.0
        best_pair: tuple[Any, Any] = (None, None)
        for joiner_raw, joiner_formset in zip(
            [n for n in joiner_names if _variants(normalize_name_key(n))],
            joiner_forms,
        ):
            for candidate_raw in candidate_names:
                if not normalize_name_key(candidate_raw):
                    continue
                score = _pair_score(joiner_formset, _variants(normalize_name_key(candidate_raw)))
                if score > best:
                    best = score
                    best_pair = (joiner_raw, candidate_raw)

        if best <= 0.0:
            continue
        if not is_strong_match(best, best_pair[0], best_pair[1]):
            continue

        row = dict(candidate)
        row["similarity"] = round(float(best), 3)
        existing = best_by_user.get(user_id)
        if existing is None or float(existing.get("similarity") or 0.0) < best:
            best_by_user[user_id] = row

    matches = sorted(
        best_by_user.values(),
        key=lambda row: float(row.get("similarity") or 0.0),
        reverse=True,
    )
    return matches[: max(1, int(limit))]


__all__ = [
    "STRONG_SCORE",
    "find_name_matches",
    "is_strong_match",
    "normalize_name_key",
    "score_name_pair",
]
