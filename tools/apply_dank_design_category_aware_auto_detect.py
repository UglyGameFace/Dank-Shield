from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAJORITY = ROOT / "stoney_verify/services/server_design_majority_layout.py"
STUDIO = ROOT / "stoney_verify/services/server_design_studio.py"
GUARD = ROOT / "stoney_verify/startup_guards/server_design_majority_layout_guard.py"
PUBLIC = ROOT / "stoney_verify/commands_ext/public_design_studio.py"
TEST = ROOT / "tests/test_server_design_category_aware_auto_detect.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, found {count}")
    return text.replace(old, new, 1)


def insert_before(text: str, marker: str, addition: str, label: str) -> str:
    pos = text.find(marker)
    if pos < 0:
        raise SystemExit(f"{label}: marker not found")
    return text[:pos] + addition.rstrip() + "\n\n\n" + text[pos:]


# ---------------------------------------------------------------------------
# 1) Fix keep_existing so it actually keeps an empty/missing icon empty.
# ---------------------------------------------------------------------------
studio = STUDIO.read_text(encoding="utf-8")
studio = replace_once(
    studio,
    '''def suggested_icon(base_name: str, *, icon_pack: str = "420_lounge", existing: str = "", mode: str = "replace_missing") -> str:\n    if mode == "keep_existing" and existing:\n        return existing\n    if mode == "clear":\n        return ""''',
    '''def suggested_icon(base_name: str, *, icon_pack: str = "420_lounge", existing: str = "", mode: str = "replace_missing") -> str:\n    if mode == "keep_existing":\n        return existing\n    if mode == "clear":\n        return ""''',
    "suggested_icon keep_existing",
)
STUDIO.write_text(studio, encoding="utf-8")


# ---------------------------------------------------------------------------
# 2) Upgrade majority detection: exact font family + all supported separators.
# ---------------------------------------------------------------------------
majority = MAJORITY.read_text(encoding="utf-8")

old_detect_font = '''def detect_font_style(studio: Any, name: Any) -> str:\n    raw = _strip_invisible_with(studio, name)\n    plain = _strip_fonts_with(studio, raw)\n    return "styled" if _font_compare_text(raw) != _font_compare_text(plain) else "normal"'''
new_detect_font = '''def detect_font_id(studio: Any, name: Any) -> str:\n    """Detect the actual supported Unicode font family used by a visible name.\n\n    Older auto-detection collapsed every decorated alphabet into ``fraktur``.\n    That made Bold Sans, Monospace, Serif, Small Caps, Fullwidth, Script, and\n    other intentional category styles look like mistakes.  Score the real\n    runtime glyph maps instead and return the strongest exact family match.\n    """\n\n    raw = _strip_invisible_with(studio, name)\n    if not raw:\n        return "normal"\n\n    styles = tuple(getattr(studio, "FONT_STYLES", ("normal", "fraktur")) or ("normal", "fraktur"))\n    runtime_map = getattr(studio, "_runtime_unicode_map", None)\n    if not callable(runtime_map):\n        plain = _strip_fonts_with(studio, raw)\n        return "fraktur" if _font_compare_text(raw) != _font_compare_text(plain) else "normal"\n\n    plain = _strip_fonts_with(studio, raw)\n    plain_count = max(1, sum(1 for ch in plain if ch.isalnum()))\n    best_font = "normal"\n    best_hits = 0\n\n    for font_id in styles:\n        font_id = _text(font_id, "normal").lower().replace("-", "_")\n        if font_id in {"", "normal", "upside_down"}:\n            continue\n        try:\n            mapping = runtime_map(font_id)\n        except Exception:\n            continue\n        if not isinstance(mapping, Mapping):\n            continue\n        glyphs = {\n            str(glyph)\n            for plain_char, glyph in mapping.items()\n            if glyph and str(glyph) != str(plain_char) and not str(glyph).isascii()\n        }\n        if not glyphs:\n            continue\n        hits = sum(1 for ch in raw if ch in glyphs)\n        if hits > best_hits:\n            best_font = font_id\n            best_hits = hits\n\n    minimum_hits = 1 if plain_count <= 2 else 2\n    if best_hits >= minimum_hits and (best_hits / plain_count) >= 0.40:\n        return best_font\n    return "normal"\n\n\ndef detect_font_style(studio: Any, name: Any) -> str:\n    """Backward-compatible coarse label used by older callers."""\n\n    return "normal" if detect_font_id(studio, name) == "normal" else "styled"'''
majority = replace_once(majority, old_detect_font, new_detect_font, "exact font detector")

# Detect every simple separator in the studio library, not only vertical bars.
majority = replace_once(
    majority,
    '''    for token in VERTICAL_SEPARATOR_TOKENS:\n        if not scan.startswith(token):''',
    '''    tokens = tuple(sorted({value.strip() for value in _separator_values_for(studio) if value and value.strip()}, key=len, reverse=True))\n    for token in tokens:\n        if not scan.startswith(token):''',
    "all separator scan",
)

# Attach a stable separator id when possible, including bracket/wrapper layouts.
majority = replace_once(
    majority,
    '''    raw = _strip_invisible_with(studio, name)\n    emoji, remainder = _remove_leading_emoji(studio, raw, kind="text")\n    left_spaces = len(remainder) - len(remainder.lstrip(" "))''',
    '''    raw = _strip_invisible_with(studio, name)\n\n    wrapper_specs = (\n        ("「", "」", "bracket_corner"),\n        ("『", "』", "bracket_white_corner"),\n        ("〔", "〕", "bracket_tortoise"),\n        ("【", "】", "bracket_lenticular"),\n        ("〖", "〗", "bracket_white_lenticular"),\n        ("꒰", "꒱", "bracket_soft"),\n    )\n    for opener, closer, separator_id in wrapper_specs:\n        if raw.startswith(opener) and closer in raw:\n            close_at = raw.find(closer)\n            inside = raw[len(opener):close_at].strip()\n            remainder_after = raw[close_at + len(closer):].strip()\n            if inside and remainder_after:\n                return {\n                    "emoji": inside,\n                    "has_leading_emoji": True,\n                    "token": f"{opener}{closer}",\n                    "spacing": "wrapped",\n                    "label": separator_id.replace("_", " "),\n                    "separator_id": separator_id,\n                    "missing": False,\n                    "doubled": False,\n                    "separator_in_name_text": False,\n                }\n\n    emoji, remainder = _remove_leading_emoji(studio, raw, kind="text")\n    left_spaces = len(remainder) - len(remainder.lstrip(" "))''',
    "wrapper separator detection",
)

majority = majority.replace(
    '''        return {\n            "emoji": emoji,\n            "has_leading_emoji": bool(emoji),\n            "token": token,''',
    '''        separator_id = ""\n        for spec in tuple(getattr(studio, "SEPARATOR_LIBRARY", tuple()) or tuple()):\n            value = _text(getattr(spec, "value", "")).strip()\n            if value == token:\n                separator_id = _text(getattr(spec, "id", ""))\n                break\n        return {\n            "emoji": emoji,\n            "has_leading_emoji": bool(emoji),\n            "token": token,''',
    1,
)
majority = majority.replace(
    '''            "label": _separator_label(token, spacing),\n            "missing": False,''',
    '''            "label": _separator_label(token, spacing),\n            "separator_id": separator_id,\n            "missing": False,''',
    1,
)
majority = majority.replace(
    '''        "label": "no separator",\n        "missing": True,''',
    '''        "label": "no separator",\n        "separator_id": "none",\n        "missing": True,''',
    1,
)

# Majority key honors exact known separator IDs such as middle-dot/brackets.
majority = replace_once(
    majority,
    '''def _separator_key(parts: Mapping[str, Any]) -> tuple[str, str]:\n    spacing = _text(parts.get("spacing"), "none")\n    token = _text(parts.get("token"))''',
    '''def _separator_key(parts: Mapping[str, Any]) -> tuple[str, str]:\n    spacing = _text(parts.get("spacing"), "none")\n    separator_id = _text(parts.get("separator_id"))\n    if separator_id and separator_id != "none":\n        return (f"id:{separator_id}", spacing)\n    token = _text(parts.get("token"))''',
    "separator majority key",
)

# Infer the exact font id rather than reducing every styled font to fraktur.
majority = majority.replace('font_counts[detect_font_style(studio, name)] += 1', 'font_counts[detect_font_id(studio, name)] += 1')
old_font_payload = '''        "font": {\n            "id": "fraktur" if font_id == "styled" and not font_mixed else ("normal" if font_id == "normal" and not font_mixed else ""),\n            "label": "styled/fraktur" if font_id == "styled" and not font_mixed else ("normal text" if font_id == "normal" and not font_mixed else "mixed/unknown"),\n            "count": font_count,\n            "mixed": font_mixed,\n        },'''
new_font_payload = '''        "font": {\n            "id": _text(font_id) if font_id and not font_mixed else "",\n            "label": (_text(font_id).replace("_", " ") if font_id and not font_mixed else "mixed/unknown"),\n            "count": font_count,\n            "mixed": font_mixed,\n        },'''
majority = replace_once(majority, old_font_payload, new_font_payload, "font analysis payload")

# Apply exact separator id/font family when a clear majority exists.
majority = replace_once(
    majority,
    '''    separator = analysis.get("separator") if isinstance(analysis.get("separator"), Mapping) else {}\n    token = _text(separator.get("token"))\n    spacing = _text(separator.get("spacing"), "unknown")\n    if spacing in {"compact", "spaced", "none"}:\n        out["separator_id"] = ensure_separator_spec(studio, token, spacing)''',
    '''    separator = analysis.get("separator") if isinstance(analysis.get("separator"), Mapping) else {}\n    token = _text(separator.get("token"))\n    spacing = _text(separator.get("spacing"), "unknown")\n    separator_id = _text(separator.get("separator_id"))\n    if separator_id and separator_id in getattr(studio, "SEPARATORS_BY_ID", {}):\n        out["separator_id"] = separator_id\n    elif spacing in {"compact", "spaced", "none"}:\n        out["separator_id"] = ensure_separator_spec(studio, token, spacing)''',
    "apply separator id",
)
majority = replace_once(
    majority,
    '''    font = analysis.get("font") if isinstance(analysis.get("font"), Mapping) else {}\n    font_id = _text(font.get("id"))\n    if font_id in {"normal", "fraktur"}:\n        out["font"] = font_id''',
    '''    font = analysis.get("font") if isinstance(analysis.get("font"), Mapping) else {}\n    font_id = _text(font.get("id"))\n    if font_id in set(getattr(studio, "FONT_STYLES", ("normal", "fraktur"))):\n        out["font"] = font_id''',
    "apply exact font",
)

# All safety/classification checks compare the exact detected font family.
majority = majority.replace("before_font = detect_font_style(studio, before)", "before_font = detect_font_id(studio, before)")
majority = majority.replace("after_font = detect_font_style(studio, after)", "after_font = detect_font_id(studio, after)")
majority = majority.replace("detected = detect_font_style(studio, after)", "detected = detect_font_id(studio, after)")
majority = majority.replace(
    '''    if expected not in {"normal", "fraktur"}:\n        return True\n\n    detected = detect_font_id(studio, after)\n\n    if expected == "normal":\n        return detected == "normal"\n\n    return detected == "styled"''',
    '''    if expected not in set(getattr(studio, "FONT_STYLES", ("normal", "fraktur"))):\n        return True\n\n    detected = detect_font_id(studio, after)\n    return detected == expected''',
)

category_aware_helpers = r'''
def _record_id(record: Any) -> str:
    if isinstance(record, Mapping):
        return _text(record.get("id"))
    return _text(getattr(record, "id", ""))


def _record_category_id(record: Any) -> str:
    if isinstance(record, Mapping):
        return _text(record.get("category_id"))
    return _text(getattr(record, "category_id", ""))


def infer_category_local_layouts(studio: Any, records: Iterable[Any]) -> dict[str, Any]:
    """Infer independent channel styles for each category.

    Category A is never allowed to become the template for Category B.  The
    uncategorized channel bucket is treated as its own group.  Category headers
    are intentionally not used as channel-majority votes because a category
    title often uses a different font/frame from the channels beneath it.
    """

    groups: dict[str, list[Any]] = {}
    category_names: dict[str, str] = {}
    for record in records:
        kind = _record_kind(record)
        rid = _record_id(record)
        if kind == "category":
            if rid:
                category_names[rid] = _record_name(record)
            continue
        category_id = _record_category_id(record) or "__uncategorized__"
        groups.setdefault(category_id, []).append(record)

    analyses = {
        category_id: infer_live_majority_layout(studio, rows)
        for category_id, rows in groups.items()
        if rows
    }
    return {
        "channel_groups": analyses,
        "category_names": category_names,
        "summaries": {category_id: _summary_text(analysis) for category_id, analysis in analyses.items()},
    }


def infer_target_layout(
    studio: Any,
    records: Iterable[Any],
    *,
    scope: str,
    target_id: int,
) -> dict[str, Any]:
    """Return the local live layout relevant to one category/channel editor."""

    rows = list(records)
    target_key = str(int(target_id))
    target = next((row for row in rows if _record_id(row) == target_key), None)
    profiles = infer_category_local_layouts(studio, rows)
    groups = profiles.get("channel_groups") if isinstance(profiles.get("channel_groups"), Mapping) else {}

    if scope == "channel" and target is not None:
        category_id = _record_category_id(target) or "__uncategorized__"
        analysis = groups.get(category_id)
        if isinstance(analysis, Mapping):
            return dict(analysis)

    if scope == "category":
        analysis = groups.get(target_key)
        if isinstance(analysis, Mapping):
            return dict(analysis)

    return infer_live_majority_layout(studio, rows)


def _saved_lock_scope(options: Mapping[str, Any], *, channel_id: str, category_id: str) -> str:
    channel_locks = options.get("channel_format_locks") if isinstance(options.get("channel_format_locks"), Mapping) else {}
    category_locks = options.get("category_format_locks") if isinstance(options.get("category_format_locks"), Mapping) else {}
    global_lock = options.get("format_lock_global") if isinstance(options.get("format_lock_global"), Mapping) else {}
    if channel_id and isinstance(channel_locks.get(channel_id), Mapping):
        return "channel"
    if category_id and isinstance(category_locks.get(category_id), Mapping):
        return "category"
    if global_lock.get("enabled"):
        return "global"
    return ""


def _separator_id_from_parts(studio: Any, parts: Mapping[str, Any]) -> str:
    separator_id = _text(parts.get("separator_id"))
    if separator_id and separator_id in getattr(studio, "SEPARATORS_BY_ID", {}):
        return separator_id
    spacing = _text(parts.get("spacing"), "unknown")
    token = _text(parts.get("token"))
    if spacing in {"compact", "spaced", "none"}:
        return ensure_separator_spec(studio, token, spacing)
    return ""


def _auto_channel_lock(
    studio: Any,
    record: Mapping[str, Any],
    analysis: Mapping[str, Any],
    options: Mapping[str, Any],
) -> dict[str, Any] | None:
    current_name = _record_name(record)
    current_separator = detect_channel_separator(studio, current_name)
    majority_separator = analysis.get("separator") if isinstance(analysis.get("separator"), Mapping) else {}
    majority_spacing = _text(majority_separator.get("spacing"), "unknown")
    separator_id = ""
    if majority_spacing not in {"mixed/unknown", "unknown", "partial", "doubled"}:
        separator_id = _separator_id_from_parts(studio, majority_separator)
    if not separator_id:
        separator_id = _separator_id_from_parts(studio, current_separator)
    if not separator_id:
        return None

    majority_font = analysis.get("font") if isinstance(analysis.get("font"), Mapping) else {}
    font_id = _text(majority_font.get("id"))
    if font_id not in set(getattr(studio, "FONT_STYLES", ("normal", "fraktur"))):
        font_id = detect_font_id(studio, current_name)

    majority_emoji = analysis.get("leading_emoji") if isinstance(analysis.get("leading_emoji"), Mapping) else {}
    enabled = majority_emoji.get("enabled")
    current_has_emoji = bool(current_separator.get("has_leading_emoji"))
    if enabled is True:
        icon_mode = "replace_missing"
    elif enabled is False:
        icon_mode = "clear"
    else:
        icon_mode = "keep_existing" if current_has_emoji else "clear"

    strength = 4 if font_id != "normal" else 2
    return {
        "scope": "auto_category",
        "theme_id": _text(options.get("theme_id"), "gothic_clean"),
        "strength": strength,
        "font": font_id,
        "separator_id": separator_id,
        "category_frame_id": _text(options.get("category_frame_id"), "plain"),
        "icon_mode": icon_mode,
        "emoji_override": "",
        "exact_match": True,
        "__auto_detect": True,
    }


def build_category_aware_options(
    studio: Any,
    options: Mapping[str, Any],
    records: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create ephemeral per-channel rules from each category's own majority.

    Saved channel/category/global locks always win and are never overwritten.
    Category headers without an explicit saved rule are preserved as-is.  When a
    category is too mixed to infer safely, only dimensions with a clear majority
    are changed and uncertain dimensions keep each channel's current style.
    """

    rows = [dict(row) for row in records]
    profiles = infer_category_local_layouts(studio, rows)
    analyses = profiles.get("channel_groups") if isinstance(profiles.get("channel_groups"), Mapping) else {}

    out = dict(options)
    saved_channel_locks = dict(options.get("channel_format_locks")) if isinstance(options.get("channel_format_locks"), Mapping) else {}
    merged_channel_locks = dict(saved_channel_locks)
    ephemeral_ids: list[str] = []
    preserve_ids: list[str] = []

    for record in rows:
        channel_id = _record_id(record)
        kind = _record_kind(record)
        category_id = channel_id if kind == "category" else (_record_category_id(record) or "__uncategorized__")
        saved_scope = _saved_lock_scope(options, channel_id=channel_id, category_id=(channel_id if kind == "category" else _record_category_id(record)))
        if saved_scope:
            continue
        if kind == "category":
            if channel_id:
                preserve_ids.append(channel_id)
            continue
        analysis = analyses.get(category_id)
        if not isinstance(analysis, Mapping):
            if channel_id:
                preserve_ids.append(channel_id)
            continue
        lock = _auto_channel_lock(studio, record, analysis, options)
        if lock is None:
            if channel_id:
                preserve_ids.append(channel_id)
            continue
        merged_channel_locks[channel_id] = lock
        ephemeral_ids.append(channel_id)

    out["channel_format_locks"] = merged_channel_locks
    out["__category_aware_auto_detect"] = True
    out["__auto_detect_ephemeral_channel_ids"] = ephemeral_ids
    out["__auto_detect_preserve_ids"] = preserve_ids
    out["__auto_detect_category_analyses"] = dict(analyses)
    out["__auto_detect_category_profiles"] = dict(profiles.get("summaries") or {})
    out["__auto_detect_saved_rules_respected"] = _lock_counts(options)
    return out, profiles


def annotate_category_aware_plan_items(
    studio: Any,
    items: list[dict[str, Any]],
    options: Mapping[str, Any],
) -> list[dict[str, Any]]:
    analyses = options.get("__auto_detect_category_analyses") if isinstance(options.get("__auto_detect_category_analyses"), Mapping) else {}
    ephemeral = {str(value) for value in list(options.get("__auto_detect_ephemeral_channel_ids") or [])}
    preserve = {str(value) for value in list(options.get("__auto_detect_preserve_ids") or [])}
    saved_counts = options.get("__auto_detect_saved_rules_respected") if isinstance(options.get("__auto_detect_saved_rules_respected"), Mapping) else {}
    saved_total = sum(_safe_int(saved_counts.get(key), 0) for key in ("global", "categories", "channels"))

    for item in items:
        channel_id = _text(item.get("channel_id"))
        category_id = _text(item.get("category_id")) or "__uncategorized__"
        kind = _text(item.get("kind"), "text")

        if channel_id in preserve:
            item["after"] = item.get("before")
            item["status"] = "unchanged"
            item["blockers"] = []
            item["warnings"] = list(item.get("warnings") or []) + [
                "Smart Auto-Detect left this item unchanged because it has no safe local majority target."
            ]
            item["auto_detect_preserved"] = True
            continue

        if channel_id in ephemeral:
            analysis = analyses.get(category_id)
            if isinstance(analysis, Mapping):
                validate_majority_repair_items(studio, [item], analysis)
                item["majority_layout"] = _summary_text(analysis)
            item["format_lock_scope"] = "auto_category"
            item["auto_detect_category_id"] = category_id

        if saved_total:
            item["auto_detect_saved_rules_respected"] = saved_total

    return items
'''
majority = insert_before(majority, "def majority_summary_from_items", category_aware_helpers, "category-aware helpers")

# Export new public helpers.
majority = majority.replace(
    '    "detect_font_style",\n',
    '    "detect_font_id",\n    "detect_font_style",\n',
)
majority = majority.replace(
    '    "infer_live_majority_layout",\n',
    '    "infer_live_majority_layout",\n    "infer_category_local_layouts",\n    "infer_target_layout",\n    "build_category_aware_options",\n    "annotate_category_aware_plan_items",\n',
)
MAJORITY.write_text(majority, encoding="utf-8")


# ---------------------------------------------------------------------------
# 3) Make exact-format suggestions local to the selected category/channel.
# ---------------------------------------------------------------------------
public = PUBLIC.read_text(encoding="utf-8")
public = replace_once(
    public,
    '''            records.append({"name": name, "kind": "category"})''',
    '''            records.append({"id": str(getattr(category, "id", "")), "category_id": str(getattr(category, "id", "")), "name": name, "kind": "category"})''',
    "category exact-format record ids",
)
public = replace_once(
    public,
    '''                records.append({"name": name, "kind": "text"})''',
    '''                parent = getattr(channel, "category", None)\n                records.append({\n                    "id": str(getattr(channel, "id", "")),\n                    "category_id": str(getattr(parent, "id", "")),\n                    "name": name,\n                    "kind": "text",\n                })''',
    "channel exact-format record ids",
)
public = replace_once(
    public,
    '''        analysis = majority.infer_live_majority_layout(studio, records)\n        inferred = majority.apply_majority_to_options(studio, options, analysis, respect_locks=False)''',
    '''        analysis = majority.infer_target_layout(\n            studio,\n            records,\n            scope=scope,\n            target_id=int(target_id),\n        )\n        inferred = majority.apply_majority_to_options(studio, options, analysis, respect_locks=True)''',
    "target-local exact-format inference",
)
PUBLIC.write_text(public, encoding="utf-8")


# ---------------------------------------------------------------------------
# 4) Replace global Live Majority plan generation with category-aware detection.
# ---------------------------------------------------------------------------
guard = GUARD.read_text(encoding="utf-8")

# Recommendation text and user-facing naming.
guard = replace_once(
    guard,
    '''def _repair_mode_recommendation_text() -> str:\n    return (\n        "Start with **Fix Only Obvious Mistakes** for styled servers. "\n        "Use **Live Majority** only when the preview keeps the current server look. "\n        "Use **Saved Layout** when this server already has approved Dank Design rules."\n    )''',
    '''def _repair_mode_recommendation_text() -> str:\n    return (\n        "Use **Smart Auto-Detect** to learn each category separately. "\n        "Saved channel/category/global rules always win, and mixed categories are left alone instead of being flattened. "\n        "Use **Saved Layout** when you want to enforce your saved design everywhere."\n    )''',
    "repair recommendation",
)

# Category profile display helper.
profile_helper = r'''
def _category_profile_lines(guild: Any, options: Mapping[str, Any], *, limit: int = 8) -> list[str]:
    profiles = options.get("__auto_detect_category_profiles") if isinstance(options.get("__auto_detect_category_profiles"), Mapping) else {}
    lines: list[str] = []
    for category_id, summary in profiles.items():
        if not isinstance(summary, Mapping):
            continue
        if category_id == "__uncategorized__":
            label = "No Category"
        else:
            category = guild.get_channel(int(category_id)) if str(category_id).isdigit() else None
            label = _text(getattr(category, "name", ""), f"Category {category_id}")
        lines.append(
            f"• **{label}** — font: {_text(summary.get('font'), 'mixed/unknown')} • "
            f"separator: {_text(summary.get('separator'), 'mixed/unknown')} • "
            f"emoji: {_text(summary.get('leading_emoji'), 'mixed/unknown')}"
        )
        if len(lines) >= limit:
            break
    return lines or ["No category-local channel groups were available to detect."]
'''
guard = insert_before(guard, "def _patch_consistency_embed", profile_helper, "profile helper")

# Rewrite the plan wrapper itself.
old_wrapper = '''        async def _build_design_plan_with_majority(guild: Any, options: Mapping[str, Any]) -> list[dict[str, Any]]:\n            if not _is_consistency_repair(options):\n                return await original(guild, options)\n\n            records = _records_for_guild(command_guard, guild)\n            analysis = majority.infer_live_majority_layout(studio, records)\n            respect_saved_locks = bool(_saved_rule_count(options))\n            inferred = majority.apply_majority_to_options(studio, options, analysis, respect_locks=respect_saved_locks)\n            if respect_saved_locks:\n                inferred["__live_majority_apply_disabled_by_saved_rules"] = _saved_rule_count(options)\n            items = await original(guild, inferred)\n            return majority.annotate_plan_items(items, analysis, inferred, studio=studio)'''
new_wrapper = '''        async def _build_design_plan_with_majority(guild: Any, options: Mapping[str, Any]) -> list[dict[str, Any]]:\n            if not _is_consistency_repair(options):\n                return await original(guild, options)\n\n            records = _records_for_guild(command_guard, guild)\n            inferred, _profiles = majority.build_category_aware_options(studio, options, records)\n            items = await original(guild, inferred)\n            return majority.annotate_category_aware_plan_items(studio, items, inferred)'''
guard = replace_once(guard, old_wrapper, new_wrapper, "category-aware plan wrapper")

# Smart auto detect can apply to unlocked items even when saved rules exist.
guard = guard.replace('                and saved_rules == 0\n', '')
guard = guard.replace(
    '                requested["__live_majority_apply_disabled_by_saved_rules"] = saved_rules\n',
    '                requested["__auto_detect_saved_rules_respected_count"] = saved_rules\n',
)
guard = guard.replace(
    '                "mode": "consistency_live_majority_preview_only" if saved_rules else "consistency_live_majority",',
    '                "mode": "consistency_smart_auto_detect",',
)

# Target screen uses the requested category-aware metadata.
guard = guard.replace(
    '            items, _requested = await _majority_items(guild, options)\n            await interaction.edit_original_response(embed=_target_embed(guild, options, items), view=RepairTargetView())',
    '            items, requested = await _majority_items(guild, options)\n            await interaction.edit_original_response(embed=_target_embed(guild, requested, items), view=RepairTargetView())',
)

# Replace global-majority target summary with category-local profiles.
guard = guard.replace('title="🧭 Choose Repair Target"', 'title="🧭 Choose Smart Repair Target"')
guard = guard.replace(
    '''            name="Live majority detected",\n            value=(\n                f"Separator: **{detected.get('separator', 'mixed/unknown')}**\\n"\n                f"Category frame: **{detected.get('category_frame', 'mixed/unknown')}**\\n"\n                f"Font/style: **{detected.get('font', 'mixed/unknown')}**\\n"\n                f"Leading emoji: **{detected.get('leading_emoji', 'mixed/unknown')}**"\n            )[:1024],''',
    '''            name="Smart detection by category",\n            value="\\n".join(_category_profile_lines(guild, options, limit=8))[:1024],''',
)
guard = guard.replace('name="Using live majority would"', 'name="Smart Auto-Detect would"')
guard = guard.replace(
    'value=f"Saved rules found: **{_saved_rule_count(options)}**\\nSaved rules/locks are owner-approved. Use them unless you are only previewing Live Majority.",',
    'value=f"Saved rules found: **{_saved_rule_count(options)}**\\nChannel rules beat category rules, category rules beat global rules, and Smart Auto-Detect only fills unlocked areas.",',
)
guard = guard.replace(
    '"Use **Saved Layout** when saved rules exist. "\n                "Live Majority is only a preview unless there are no saved locks and confidence is high."',
    '"Use **Smart Auto-Detect** to repair unlocked outliers using each category\'s own channel style. "\n                "Use **Saved Layout** to enforce saved design rules instead."',
)

# Button/preview naming.
guard = guard.replace('label="Preview Live Majority"', 'label="Preview Smart Auto-Detect"')
guard = guard.replace('title="✅ Live Majority Repair Preview"', 'title="✅ Smart Auto-Detect Preview"')
guard = guard.replace('title="⚠️ Live Majority Needs Review"', 'title="⚠️ Smart Auto-Detect Needs Review"')
guard = guard.replace('"Target: the layout most channels/categories already use here.\\n\\n"', '"Target: each category follows its own local channel majority.\\n\\n"')
guard = guard.replace('context=\'live_majority\'', 'context=\'smart_category_auto_detect\'')
guard = guard.replace('"Use **Manual Editor** or **Saved Layout** instead."', '"Use **Manual Editor** or **Saved Layout** instead."')
guard = guard.replace('"Live Majority"', '"Smart Auto-Detect"')
guard = guard.replace('"live majority"', '"smart category auto-detect"')
guard = guard.replace('"Live majority"', '"Smart auto-detect"')

# Saved rules are respected, not grounds for disabling the whole apply operation.
guard = guard.replace(
    '''        if found:\n            embed.add_field(name="Saved rules found", value=f"{found} saved rule(s) exist. **Apply is disabled for Smart Auto-Detect** so saved rules cannot be bypassed here. Use **Saved Layout** or Manual Editor.", inline=False)''',
    '''        if found:\n            embed.add_field(name="Saved rules respected", value=f"{found} saved rule(s) are preserved. Smart Auto-Detect only repairs unlocked channels and leaves locked scopes alone.", inline=False)''',
)

guard = guard.replace(
    'print("✅ server_design_majority_layout_guard active; guided repair target choices use smart category auto-detect layout")',
    'print("✅ server_design_majority_layout_guard active; Smart Auto-Detect uses per-category layouts and respects saved locks")',
)
GUARD.write_text(guard, encoding="utf-8")


# ---------------------------------------------------------------------------
# 5) Regression tests for the user's exact complaints.
# ---------------------------------------------------------------------------
TEST.write_text(r'''from __future__ import annotations

from stoney_verify.services import server_design_majority_layout as majority
from stoney_verify.services import server_design_studio as studio


def _styled(text: str, font: str) -> str:
    output, _ = studio.transform_text_safe(text, font)
    return output


def _record(record_id: int, category_id: int | None, name: str, *, kind: str = "text") -> dict[str, str]:
    return {
        "id": str(record_id),
        "category_id": "" if category_id is None else str(category_id),
        "name": name,
        "kind": kind,
    }


def test_exact_font_detection_distinguishes_supported_lettering_families() -> None:
    for font in ("fraktur", "bold_sans", "monospace", "serif_bold", "fullwidth", "small_caps"):
        visible = f"🎮│{_styled('gaming-news', font)}"
        assert majority.detect_font_id(studio, visible) == font


def test_separator_detection_supports_non_vertical_spacers() -> None:
    middle = majority.detect_channel_separator(studio, "🎮・gaming")
    dot = majority.detect_channel_separator(studio, "🎮·gaming")
    triangle = majority.detect_channel_separator(studio, "🎮▸gaming")
    wrapped = majority.detect_channel_separator(studio, "「🎮」gaming")

    assert middle["separator_id"] == "katakana_dot"
    assert dot["separator_id"] == "middle_dot"
    assert triangle["separator_id"] == "tri_right"
    assert wrapped["separator_id"] == "bracket_corner"


def test_each_category_gets_its_own_font_and_separator_majority() -> None:
    records = [
        _record(10, None, "── lounge ──", kind="category"),
        _record(11, 10, f"💬│{_styled('general', 'fraktur')}"),
        _record(12, 10, f"🎮│{_styled('gaming', 'fraktur')}"),
        _record(13, 10, f"🎭┃{_styled('profile', 'bold_sans')}"),
        _record(20, None, "【 staff 】", kind="category"),
        _record(21, 20, f"🛡・{_styled('staff-chat', 'monospace')}"),
        _record(22, 20, f"📋・{_styled('mod-log', 'monospace')}"),
        _record(23, 20, f"🤖│{_styled('bot-room', 'fraktur')}"),
    ]

    profiles = majority.infer_category_local_layouts(studio, records)
    lounge = profiles["channel_groups"]["10"]
    staff = profiles["channel_groups"]["20"]

    assert lounge["font"]["id"] == "fraktur"
    assert lounge["separator"]["separator_id"] == "bar_thin"
    assert staff["font"]["id"] == "monospace"
    assert staff["separator"]["separator_id"] == "katakana_dot"


def test_smart_options_create_local_ephemeral_rules_without_overwriting_saved_locks() -> None:
    records = [
        _record(10, None, "── lounge ──", kind="category"),
        _record(11, 10, f"💬│{_styled('general', 'fraktur')}"),
        _record(12, 10, f"🎮│{_styled('gaming', 'fraktur')}"),
        _record(13, 10, f"🎭┃{_styled('profile', 'bold_sans')}"),
        _record(20, None, "【 staff 】", kind="category"),
        _record(21, 20, f"🛡・{_styled('staff-chat', 'monospace')}"),
        _record(22, 20, f"📋・{_styled('mod-log', 'monospace')}"),
        _record(23, 20, f"🤖│{_styled('bot-room', 'fraktur')}"),
    ]
    saved = {
        "theme_id": "gothic_clean",
        "channel_format_locks": {
            "13": {"font": "bold_sans", "separator_id": "bar_heavy", "strength": 4},
        },
        "category_format_locks": {
            "20": {"font": "monospace", "separator_id": "katakana_dot", "strength": 4},
        },
        "protection_rules": {"mod-log": "never"},
    }

    inferred, _profiles = majority.build_category_aware_options(studio, saved, records)
    locks = inferred["channel_format_locks"]

    # Explicit channel lock remains byte-for-byte the owner-approved rule.
    assert locks["13"] == saved["channel_format_locks"]["13"]
    # Category 20 is locked, so Smart Auto-Detect adds no fake channel overrides there.
    assert "21" not in locks
    assert "22" not in locks
    assert "23" not in locks
    # Unlocked category 10 gets local temporary rules using its own majority.
    assert locks["11"]["font"] == "fraktur"
    assert locks["11"]["separator_id"] == "bar_thin"
    assert locks["12"]["font"] == "fraktur"
    assert locks["12"]["separator_id"] == "bar_thin"
    assert locks["11"]["__auto_detect"] is True
    # Category headers are preserved unless an explicit saved rule applies.
    assert "10" in inferred["__auto_detect_preserve_ids"]


def test_keep_existing_icon_mode_does_not_invent_placeholder_icon() -> None:
    assert studio.suggested_icon("random-channel", existing="", mode="keep_existing") == ""
    assert studio.suggested_icon("random-channel", existing="🎯", mode="keep_existing") == "🎯"
''', encoding="utf-8")

# Compile touched Python files before CI.
for path in (MAJORITY, STUDIO, GUARD, PUBLIC, TEST):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")

print("PASS: applied category-aware Smart Auto-Detect for Dank Design")
