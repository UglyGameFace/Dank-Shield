from __future__ import annotations

from pathlib import Path
import runpy

source = Path('.github/workflows/ds-design-030-second-audit.yml').read_text(encoding='utf-8')
marker = "          python - <<'PY'\n"
start = source.index(marker) + len(marker)
end = source.index("\n          PY\n", start)
raw = source[start:end]
script = '\n'.join(line[10:] if line.startswith('          ') else line for line in raw.splitlines()) + '\n'

a = script.index('# Count only effective exact-protection records')
b = script.index('# Make Rules / Locks explain', a)
corrected = (
    "# Count only effective exact-protection records, not corrupt raw keys.\n"
    "needle = '    protection_items = _mapping_dict(options.get(\"protection_item_rules\"))\\n'\n"
    "section = p.find('def _lock_count(options: Mapping[str, Any])')\n"
    "pos = p.find(needle, section)\n"
    "section_end = p.find('def _effective_format_options(', section)\n"
    "if section < 0 or pos < 0 or pos >= section_end:\n"
    "    raise SystemExit(f'effective protection count target invalid: section={section} pos={pos} end={section_end}')\n"
    "p = p[:pos] + '    protection_items = _protection_item_rules(options) if \"_protection_item_rules\" in globals() else _mapping_dict(options.get(\"protection_item_rules\"))\\n' + p[pos + len(needle):]\n\n"
)
script = script[:a] + corrected + script[b:]
path = Path('/tmp/ds_design_030_second_audit.py')
path.write_text(script, encoding='utf-8')
runpy.run_path(str(path), run_name='__main__')
