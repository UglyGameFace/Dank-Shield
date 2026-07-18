from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECOMMEND = ROOT / "stoney_verify/commands_ext/public_setup_recommend.py"
FRESH = ROOT / "stoney_verify/commands_ext/public_setup_fresh_choice.py"
TEST = ROOT / "tests/test_setup_single_path_front_door_v2_static.py"

recommend = RECOMMEND.read_text(encoding="utf-8")
replacements = {
    '"Turn on at least one feature under "\n            "**Other Settings → Features On / Off**."':
        '"Press **Continue Setup** and turn on at least one feature."',
    '"Ticket choices could not be checked. "\n                    "Open **Other Settings → Ticket Choices**."':
        '"Ticket choices could not be checked. Press **Continue Setup** to fix them."',
    'f"**{done}/{total} required checks complete**"':
        'f"**{done}/{total} required steps complete**"',
    'f"Guild {guild.id} • one guided setup route"':
        'f"Guild {guild.id} • guided setup"',
}
for old, new in replacements.items():
    recommend = recommend.replace(old, new)
RECOMMEND.write_text(recommend, encoding="utf-8")

fresh = FRESH.read_text(encoding="utf-8")
fresh = fresh.replace(
    '"🔒 ID/Web verification is not available for this server. Use **Basic Verify** instead."',
    '"🔒 ID/Web Verify is not available for this server. Use **Simple Verify** instead."',
)
fresh = fresh.replace(
    '"Saved **Custom setup**. Choose which features this server should use, "',
    '"Saved **Choose My Own Features**. Choose which features this server should use, "',
)
fresh = fresh.replace(
    '"❌ Unknown preset."',
    '"❌ That feature choice is no longer available. Choose another option."',
)
FRESH.write_text(fresh, encoding="utf-8")

test = TEST.read_text(encoding="utf-8")
if "test_final_setup_copy_never_sends_users_out_of_the_guided_path" not in test:
    test += '''\n\ndef test_final_setup_copy_never_sends_users_out_of_the_guided_path():\n    for stale in (\n        "Other Settings → Features On / Off",\n        "Other Settings → Ticket Choices",\n        "required checks complete",\n        "one guided setup route",\n    ):\n        assert stale not in RECOMMEND\n    assert "Press **Continue Setup** and turn on at least one feature." in RECOMMEND\n    assert "Ticket choices could not be checked. Press **Continue Setup** to fix them." in RECOMMEND\n    assert "required steps complete" in RECOMMEND\n\n\ndef test_final_setup_type_copy_uses_the_same_names_everywhere():\n    assert "Use **Basic Verify** instead." not in FRESH\n    assert "Saved **Custom setup**" not in FRESH\n    assert "Unknown preset" not in FRESH\n    assert "Use **Simple Verify** instead." in FRESH\n    assert "Saved **Choose My Own Features**" in FRESH\n'''
TEST.write_text(test, encoding="utf-8")

for path in (RECOMMEND, FRESH, TEST):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")

print("PASS: applied final setup wording consistency patch")
