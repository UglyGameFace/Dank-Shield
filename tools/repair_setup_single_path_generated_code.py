from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRESH = ROOT / "stoney_verify/commands_ext/public_setup_fresh_choice.py"
RECOMMEND = ROOT / "stoney_verify/commands_ext/public_setup_recommend.py"
TEST = ROOT / "tests/test_setup_single_path_front_door_v2_static.py"

fresh = FRESH.read_text(encoding="utf-8")

bad = '''embed.add_field(name="Your Setup", value=f"**{preset_label}**
{_custom_mix_label(payload)}", inline=False)'''
good = 'embed.add_field(name="Your Setup", value=f"**{preset_label}**\\n{_custom_mix_label(payload)}", inline=False)'

if bad in fresh:
    fresh = fresh.replace(bad, good, 1)

FRESH.write_text(fresh, encoding="utf-8")

for path in (RECOMMEND, FRESH, TEST):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")

print("PASS: repaired generated setup UX code")
