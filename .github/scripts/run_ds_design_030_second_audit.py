from __future__ import annotations

from pathlib import Path
import runpy

source = Path('.github/workflows/ds-design-030-second-audit.yml').read_text(encoding='utf-8')
marker = "          python - <<'PY'\n"
start = source.index(marker) + len(marker)
end = source.index("\n          PY\n", start)
raw = source[start:end]
script = '\n'.join(line[10:] if line.startswith('          ') else line for line in raw.splitlines()) + '\n'

# Scope the protection-count edit to _lock_count. The original safety matcher
# saw the same raw storage line in stale-cleanup code and correctly refused to guess.
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

# Replace the brittle literal matcher for _live_majority_exact_lock with a
# function-scoped structural edit. Preserve icon/source metadata below the
# component fields while deriving the minimum strength that actually reproduces
# the detected separator/font/frame.
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

# Replace the brittle exact-strength-description literal with a function-scoped
# structural edit. The source indentation changed during earlier cleanup, which
# is precisely why large whitespace-dependent source transforms are a charming
# way for humans to manufacture their own problems.
a = script.index('# Exact editor descriptions must match')
b = script.index('# Current layout example must respect strength', a)
corrected_strengths = '''# Exact editor descriptions must match the engine's current five levels.
strength_fn = p.find('def _exact_strength_description(strength: int) -> str:')
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

path = Path('/tmp/ds_design_030_second_audit.py')
path.write_text(script, encoding='utf-8')
runpy.run_path(str(path), run_name='__main__')
