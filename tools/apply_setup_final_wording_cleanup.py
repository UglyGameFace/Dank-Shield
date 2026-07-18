from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECOMMEND = ROOT / "stoney_verify/commands_ext/public_setup_recommend.py"
FRESH = ROOT / "stoney_verify/commands_ext/public_setup_fresh_choice.py"
TEST = ROOT / "tests/test_setup_single_path_front_door_v2_static.py"

recommend = RECOMMEND.read_text(encoding="utf-8")

replacements = {
    '"Saved **Custom setup**. Now turn each service on/off below. "\n                        "This is the actual manual editor."':
        '"Saved **Choose My Own Features**. Choose which features are ON or OFF below."',
    'title="✅ Custom Setup Saved"': 'title="✅ Feature Choices Saved"',
    '"Saved **Custom setup**, but the manual service editor did not open.\\n\\n"':
        '"Saved your feature choices, but the feature screen did not open.\\n\\n"',
    '"ID/Web choices are hidden because this server "\n                "has not been specifically allowed to use them."':
        '"ID/Web Verify is only available for servers approved to use it, "\n                "so those options are hidden here."',
    'f"✅ Basic Verify: **{\'ON ✅\' if state.get(\'basic_verify\') else \'OFF ⬜\'}**\\n"':
        'f"✅ Simple Verify: **{\'ON ✅\' if state.get(\'basic_verify\') else \'OFF ⬜\'}**\\n"',
    'actions.append("2. Press **Post Basic Verify Panel**.")':
        'actions.append("2. Press **Post Simple Verify Panel**.")',
    'actions.append("3. Join the saved voice verify channel with an alt and request staff verification.")':
        'actions.append("3. Join the saved Voice Verify channel with a second test account and request a staff voice check.")',
    'actions.append("4. ID/Web verify is ON. Only use this for allowlisted/private servers.")':
        'actions.append("4. ID/Web Verify is ON. This option is only for servers approved to use it.")',
    'value="Ticket panel opens a ticket. Basic Verify grants the approved role. No ID/Voice flow appears unless those switches are ON."':
        'value="The ticket panel opens a ticket. Simple Verify gives the member role. ID or Voice Verify only appears when you turned it on."',
    '"This Custom Setup does not have any features "\n                "turned on yet."':
        '"You have not turned on any features yet."',
    '"ID/Web Verify is not available for this server. "\n                "Choose Basic Verify or Voice Verify."':
        '"ID/Web Verify is not available for this server. "\n                "Choose Simple Verify or Voice Verify."',
    'label="Post Basic Verify Panel"': 'label="Post Simple Verify Panel"',
    '"✅ Basic Verify is OFF in Custom Setup. Turn Basic Verify ON first."':
        '"✅ Simple Verify is OFF. Turn Simple Verify ON first."',
    'f"❌ Could not post Basic Verify panel: `{type(e).__name__}: {str(e)[:220]}`"':
        'f"❌ Could not post Simple Verify panel: `{type(e).__name__}: {str(e)[:220]}`"',
}
for old, new in replacements.items():
    recommend = recommend.replace(old, new)

RECOMMEND.write_text(recommend, encoding="utf-8")

fresh = FRESH.read_text(encoding="utf-8")
fresh_replacements = {
    '"Private ID upload verification for allowlisted servers only."':
        '"Private ID upload verification for servers approved to use this feature."',
    '"Private ID upload plus voice-check workflow for allowlisted servers only."':
        '"Private ID upload plus a staff voice check for servers approved to use this feature."',
    '"setup_choice_description": "Custom setup service switches."':
        '"setup_choice_description": "Custom feature choices."',
    'enabled.append("Ticket Basics")': 'enabled.append("Tickets")',
    'f"Detected existing server setup and pre-selected: **{label_text}**. Nothing was created."':
        'f"Found existing setup and turned on matching features: **{label_text}**. Nothing was created."',
}
for old, new in fresh_replacements.items():
    fresh = fresh.replace(old, new)

FRESH.write_text(fresh, encoding="utf-8")

test = TEST.read_text(encoding="utf-8")
if "test_launch_and_fallback_copy_use_plain_language" not in test:
    test += '''\n\ndef test_launch_and_fallback_copy_use_plain_language():\n    for stale in ("manual service editor", "each service on/off", "Post Basic Verify Panel", "with an alt", "those switches are ON", "allowlisted/private servers"):\n        assert stale not in RECOMMEND\n    for expected in ("Post Simple Verify Panel", "second test account", "Simple Verify gives the member role", "servers approved to use it"):\n        assert expected in RECOMMEND\n\n\ndef test_custom_saved_copy_uses_feature_language():\n    for stale in ("Custom setup service switches.", "allowlisted servers only", "pre-selected:"):\n        assert stale not in FRESH\n    for expected in ("Custom feature choices.", "servers approved to use this feature", "turned on matching features"):\n        assert expected in FRESH\n'''
TEST.write_text(test, encoding="utf-8")

for path in (RECOMMEND, FRESH, TEST):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")

print("PASS: applied final setup wording consistency pass")
