from __future__ import annotations

from pathlib import Path

recommend = Path("stoney_verify/commands_ext/public_setup_recommend.py").read_text()
fresh = Path("stoney_verify/commands_ext/public_setup_fresh_choice.py").read_text()
recovery = Path("stoney_verify/commands_ext/public_setup_recovery.py").read_text()

failures: list[str] = []

for marker in (
    "Start / Continue Setup",
    "Setup Check",
    "Test / Launch",
    "Manage Setup",
):
    if marker not in recommend:
        failures.append(
            f"canonical product home missing {marker}"
        )

for retired_owner in (
    "_plain_choice_main_payload",
    "PlainSetupHomeView",
    "PlainContinueSetupView",
    "PlainLaunchView",
    "AfterChoiceView",
    "CreateMissingItemsView",
):
    if retired_owner in fresh:
        failures.append(
            f"retired fresh-choice owner remains: "
            f"{retired_owner}"
        )

if 'custom_id="dank_setup:custom_editor"' in recommend:
    failures.append("duplicate Custom Setup shortcut still exists on product home")

if "Need to undo setup?" in recommend or "Need to undo setup?" in fresh:
    failures.append("Recovery warning still appears on the setup home")

if "_add_recovery_button(view)" in recovery or 'name="Need to undo setup?"' in recovery:
    failures.append("Recovery still injects top-level home button/field")

if "open_recovery_center" not in recovery:
    failures.append("Recovery center helper is missing for Manage Setup")

if "custom_id=\"dank_setup_choice:custom\"" not in fresh:
    failures.append("Custom setup choice is missing from Choose Setup Type")

choice_start = fresh.find("class SetupTypeChoiceView(")
choice_end = fresh.find("def register_public_setup_fresh_choice_commands(", choice_start)
choice_block = (
    fresh[choice_start:choice_end]
    if choice_start >= 0 and choice_end > choice_start
    else ""
)

if (
    'choice.key == "custom_setup"' not in choice_block
    or "_open_custom_service_picker(" not in choice_block
):
    failures.append(
        "Custom setup choice does not open service switches"
    )

if failures:
    print("FAIL setup home final architecture")
    for item in failures:
        print(" -", item)
    raise SystemExit(1)

print("PASS setup home final architecture")
