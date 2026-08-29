from __future__ import annotations

from pathlib import Path
import runpy

source = Path('.github/workflows/ds-design-030-second-audit.yml').read_text(encoding='utf-8')
marker = "          python - <<'PY'\n"
start = source.index(marker) + len(marker)
end = source.index("\n          PY\n", start)
raw = source[start:end]
script = '\n'.join(line[10:] if line.startswith('          ') else line for line in raw.splitlines()) + '\n'

# Scope the protection-count edit to _lock_count.
a = script.index('# Count only effective exact-protection records')
b = script.index('# Make Rules / Locks explain', a)
corrected_count = (
    "# Count only effective exact-protection records, not corrupt raw keys.\n"
    "needle = '    protection_items = _mapping_dict(options.get(\"protection_item_rules\"))\\n'\n"
    "section = p.find('def _lock_count(options: Mapping[str, Any])')\n"
    "pos = p.find(needle, section)\n"
    "section_end = p.find('def _effective_format_options(', section)\n"
    "if section < 0 or pos < 0 or pos >= section_end:\n"
    "    raise SystemExit(f'effective protection count target invalid: section={section} pos={pos} end={section_end}')\n"
    "p = p[:pos] + '    protection_items = _protection_item_rules(options) if \"_protection_item_rules\" in globals() else _mapping_dict(options.get(\"protection_item_rules\"))\\n' + p[pos + len(needle):]\n\n"
)
script = script[:a] + corrected_count + script[b:]

# Derive live-majority strength from detected components instead of inherited draft strength.
a = script.index('          old_return =', script.index('# Centralize the strength needed')) if '          old_return =' in script else script.index('old_return =', script.index('# Centralize the strength needed'))
b = script.index('# Live target strength must reproduce', a)
corrected_majority = '''# Derive live-majority strength from the components that were actually detected.
majority_start = p.find('def _live_majority_exact_lock(')
return_start = p.find('        return {\\n', majority_start)
icon_line = p.find('            "icon_mode":', return_start)
if majority_start < 0 or return_start < 0 or icon_line < 0:
    raise SystemExit(f'live majority structural markers invalid: fn={majority_start} return={return_start} icon={icon_line}')
detected_prefix = ''' + "'''" + '''        detected_font = _safe_str(inferred.get("font"), "normal").lower().replace("-", "_")
        detected_separator = _safe_str(inferred.get("separator_id"), "none")
        detected_frame = _safe_str(inferred.get("category_frame_id"), "plain")
        detected_strength = _required_strength_for_components(
            scope=scope,
            font=detected_font,
            separator_id=detected_separator,
            category_frame_id=detected_frame,
        )

        return {
            "scope": scope,
            "theme_id": _safe_str(inferred.get("theme_id"), _safe_str(options.get("theme_id"), "gothic_clean")),
            "strength": detected_strength,
            "font": detected_font,
            "separator_id": detected_separator,
            "category_frame_id": detected_frame,
''' + "'''" + '''
p = p[:return_start] + detected_prefix + p[icon_line:]

'''
script = script[:a] + corrected_majority + script[b:]

# Exact strength descriptions use the actual helper signature in source.
a = script.index('# Exact editor descriptions must match')
b = script.index('# Current layout example must respect strength', a)
corrected_strengths = '''# Exact editor descriptions must match the engine's current five levels.
strength_fn = p.find('def _exact_strength_description(value: int) -> str:')
map_start = p.find('    descriptions = {\\n', strength_fn)
return_start = p.find('    return descriptions.get(', map_start)
map_end = p.rfind('    }\\n', map_start, return_start)
if strength_fn < 0 or map_start < 0 or return_start < 0 or map_end < 0:
    raise SystemExit(f'exact strength structural markers invalid: fn={strength_fn} map={map_start} end={map_end} return={return_start}')
new_strengths = ''' + "'''" + '''    descriptions = {
        1: "Icons/base only; no separator, font, or frame.",
        2: "Layout: adds the selected channel separator.",
        3: "Font: layout plus the selected font.",
        4: "Recommended: adds category frames where applicable.",
        5: "Exact: strictly normalizes the full selected format.",
    }
''' + "'''" + '''
p = p[:map_start] + new_strengths + p[map_end + len('    }\\n'):]

'''
script = script[:a] + corrected_strengths + script[b:]

# Replace the YAML-sensitive semantic-doc matcher with a service-scoped edit.
service_read = script.index("s = SERVICE.read_text(encoding='utf-8')")
a = script.index('s = once(', service_read)
b = script.index('old_dupes =', a)
corrected_service_doc = '''old_semantic_doc = ''' + "'''" + '''    decoration. Full exact-layout enforcement can be added later as a separate
    explicit mode; the default design repair should avoid needless churn.
''' + "'''" + '''
new_semantic_doc = ''' + "'''" + '''    decoration. Strength 5 is the explicit exact-layout mode. Lower strengths may
    preserve harmless existing decoration while enforcing the components they enable.
''' + "'''" + '''
if old_semantic_doc not in s:
    raise SystemExit('semantic match doc structural target not found')
s = s.replace(old_semantic_doc, new_semantic_doc, 1)

'''
script = script[:a] + corrected_service_doc + script[b:]

# Replace the entire duplicate detector structurally.
a = script.index('old_dupes =', script.index("s = SERVICE.read_text(encoding='utf-8')"))
b = script.index("m = MAJORITY.read_text(encoding='utf-8')", a)
corrected_duplicates = '''dup_start = s.find('def detect_duplicate_outputs(items: list[dict[str, Any]]) -> list[str]:')
dup_end = s.find('\\ndef ', dup_start + 1)
if dup_start < 0 or dup_end < 0:
    raise SystemExit(f'duplicate detector structural markers invalid: start={dup_start} end={dup_end}')
new_duplicate_detector = ''' + "'''" + '''def detect_duplicate_outputs(items: list[dict[str, Any]]) -> list[str]:
    """Report exact visible collisions introduced by this plan.

    Existing duplicate names are legal in Discord and should not block an
    unrelated design pass. Different icons/separators are also distinct visible
    names, so compare the final rendered output instead of stripped base names.
    """

    buckets: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if item.get("status") == "failed" or item.get("protected"):
            continue
        key = strip_invisible(safe_str(item.get("after"))).strip()
        if not key:
            continue
        buckets.setdefault(key, []).append(item)

    duplicates: list[str] = []
    for final_name, rows in buckets.items():
        if len(rows) < 2 or not any(safe_str(row.get("status")) == "changed" for row in rows):
            continue
        first = rows[0]
        for other in rows[1:]:
            duplicates.append(
                f"`{safe_str(first.get('before'))}` and `{safe_str(other.get('before'))}` would both become `{final_name}`"
            )
    return duplicates
''' + "'''" + '''
s = s[:dup_start] + new_duplicate_detector.rstrip() + '\\n\\n' + s[dup_end + 1:]
SERVICE.write_text(s, encoding='utf-8')

'''
script = script[:a] + corrected_duplicates + script[b:]

# Keep the working majority count/copy edits from the original transform, but
# remove the duplicate Recommended field using the actual function structure.
majority_read = script.index("m = MAJORITY.read_text(encoding='utf-8')")
a = script.index('duplicate_recommended =', majority_read)
b = script.index("MAJORITY.write_text(m, encoding='utf-8')", a)
corrected_recommended = '''target_fn = m.find('    def _target_embed(')
first_recommended = m.find('        embed.add_field(\\n            name="Recommended",', target_fn)
second_recommended = m.find('        embed.add_field(name="Recommended",', first_recommended + 1)
if target_fn < 0 or first_recommended < 0 or second_recommended < 0:
    raise SystemExit(f'majority recommendation markers invalid: fn={target_fn} first={first_recommended} second={second_recommended}')
m = m[:first_recommended] + m[second_recommended:]
'''
script = script[:a] + corrected_recommended + script[b:]

path = Path('/tmp/ds_design_030_second_audit.py')
path.write_text(script, encoding='utf-8')
runpy.run_path(str(path), run_name='__main__')
