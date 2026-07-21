from __future__ import annotations

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
HELPER = Path(__file__).resolve()
FRESH = ROOT / "stoney_verify/commands_ext/public_setup_fresh_choice.py"
TEST = ROOT / "tests/test_setup_close_button_style_behavior.py"

fresh = FRESH.read_text(encoding="utf-8")
test = TEST.read_text(encoding="utf-8")

old = '''    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_plans:close",
        row=1,
    )
'''
new = '''    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        custom_id="dank_setup_plans:close",
        row=1,
    )
'''
count = fresh.count(old)
if count != 1:
    raise RuntimeError(f"setup plan Close style: expected exactly 1 match, found {count}")
fresh = fresh.replace(old, new, 1)

anchor = '''        recommend.ProductSetupHomeView(),
'''
replacement = '''        recommend.ProductSetupHomeView(),
        __import__(
            "stoney_verify.commands_ext.public_setup_fresh_choice",
            fromlist=["SetupTypeChoiceView"],
        ).SetupTypeChoiceView(),
'''
if replacement not in test:
    count = test.count(anchor)
    if count != 1:
        raise RuntimeError(f"Close behavior test anchor: expected exactly 1 match, found {count}")
    test = test.replace(anchor, replacement, 1)

compile(fresh, str(FRESH), "exec")
compile(test, str(TEST), "exec")

FRESH.write_text(fresh, encoding="utf-8")
TEST.write_text(test, encoding="utf-8")
HELPER.unlink()
subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)

print("✅ Setup plan picker Close button is now red.")
print("✅ Setup plan picker is included in permanent Close-button behavior coverage.")
print("✅ Temporary helper removed from the working tree.")
print("✅ git diff --check passed.")
