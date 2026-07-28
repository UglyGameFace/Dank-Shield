from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "stoney_verify/commands_ext/public_profile_cards_core.py"
TEST_PATH = ROOT / "tests/test_profile_role_display_separation.py"
WORKFLOW_PATH = ROOT / ".github/workflows/profile-runtime-diagnostics.yml"

FINAL_WORKFLOW = '''name: Profile Runtime Diagnostics

on:
  pull_request:
    paths:
      - "stoney_verify/profile_card_runtime.py"
      - "stoney_verify/profile_card_runtime_core.py"
      - "stoney_verify/profile_card_service.py"
      - "stoney_verify/profile_signature_live_renderer.py"
      - "stoney_verify/profile_signature_studio.py"
      - "stoney_verify/profile_card_setup_ui.py"
      - "stoney_verify/profile_card_setup_ui_core.py"
      - "stoney_verify/commands_ext/public_profile_cards.py"
      - "stoney_verify/commands_ext/public_profile_cards_core.py"
      - "stoney_verify/commands_ext/public_self_roles_group.py"
      - "stoney_verify/startup_guards/profile_role_editor_guard.py"
      - "stoney_verify/assets/platform_logos/**"
      - "supabase/migrations/*live_profile*.sql"
      - "tests/test_live_profile*.py"
      - "tests/test_profile_card*.py"
      - "tests/test_profile_platform*.py"
      - "tests/test_profile_role*.py"
      - ".github/workflows/profile-runtime-diagnostics.yml"
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: profile-runtime-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true

jobs:
  focused-profile-tests:
    runs-on: ubuntu-24.04
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          python -m pip install -r requirements.txt
          python -m pip install pytest

      - name: Verify bundled platform artwork
        shell: bash
        run: |
          set -euo pipefail
          for key in steam epic xbox playstation nintendo riot battle_net roblox twitch youtube kick custom; do
            test -s "stoney_verify/assets/platform_logos/${key}.png"
          done
          test -s stoney_verify/assets/platform_logos/ATTRIBUTION.md

      - name: Compile profile implementation
        run: |
          python -m py_compile \
            stoney_verify/profile_card_service.py \
            stoney_verify/profile_card_runtime_core.py \
            stoney_verify/profile_card_runtime.py \
            stoney_verify/profile_signature_live_renderer.py \
            stoney_verify/profile_signature_studio.py \
            stoney_verify/profile_card_setup_ui.py \
            stoney_verify/profile_card_setup_ui_core.py \
            stoney_verify/commands_ext/public_profile_cards.py \
            stoney_verify/commands_ext/public_profile_cards_core.py \
            stoney_verify/commands_ext/public_self_roles_group.py \
            stoney_verify/startup_guards/profile_role_editor_guard.py

      - name: Run focused profile regression suite
        run: |
          python -m pytest -q \
            tests/test_live_profile_card_runtime.py \
            tests/test_live_profile_channel_spam_regression.py \
            tests/test_live_profile_runtime_delivery_regression.py \
            tests/test_live_profile_card_cleanup.py \
            tests/test_live_profile_card_final_safety.py \
            tests/test_live_profile_card_integration_contract.py \
            tests/test_live_profile_card_lifecycle.py \
            tests/test_live_profile_card_visual_links.py \
            tests/test_live_profile_runtime_responsive.py \
            tests/test_profile_card_service.py \
            tests/test_profile_card_setup_picker_ux.py \
            tests/test_profile_card_migration_safety.py \
            tests/test_profile_platform_display_modes.py \
            tests/test_profile_platform_privacy_preview_ux.py \
            tests/test_profile_role_display_separation.py

      - name: Run role-menu compatibility checks
        run: |
          python tools/test_profile_cosmetic_roles_static.py
          python tools/test_profile_role_editor_guard_static.py

      - name: Confirm temporary materializers are absent
        shell: bash
        run: |
          set -euo pipefail
          test ! -e tools/apply_profile_banner_roles_patch.py
          test ! -e tools/apply_profile_upgrade_regression_fixes.py
          test ! -d tools/profile_banner_roles_payload
          test ! -e .github/workflows/apply-profile-platform-modes.yml
          test ! -e tools/apply_profile_platform_modes_patch.py
          test ! -e tools/apply_profile_platform_modes_v2.py
          test ! -e tools/fix_profile_privacy_defaults.py
          test ! -e tools/apply_profile_spacing_guard.py
          test ! -e tools/apply_profile_role_menu_dedupe.py
          test ! -e tools/apply_profile_view_attachment_fix.py
          git diff --check
'''


def replace_once(path: Path, old: str, new: str, *, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one exact match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> None:
    replace_once(
        CORE_PATH,
        '''    await _send_private(
        interaction,
        embed=rendered_embed,
        view=view if view.children else None,
    )


''',
        '''    await _send_private(
        interaction,
        embed=rendered_embed,
        view=view if view.children else None,
        file=rendered.file if rendered is not None else None,
    )


''',
        label="member profile banner attachment",
    )

    test_text = TEST_PATH.read_text(encoding="utf-8")
    marker = "def test_member_profile_view_attaches_generated_wide_banner()"
    if marker in test_text:
        raise RuntimeError("member profile attachment regression already exists")
    test_text = test_text.rstrip() + '''


def test_member_profile_view_attaches_generated_wide_banner() -> None:
    profile_send = PRIVACY_CORE.split("async def send_privacy_aware_profile", 1)[1].split(
        "def _live_status_embed", 1
    )[0]
    assert "file=rendered.file if rendered is not None else None" in profile_send
    assert "render_live_profile_card(" in profile_send
''' + "\n"
    TEST_PATH.write_text(test_text, encoding="utf-8")

    WORKFLOW_PATH.write_text(FINAL_WORKFLOW, encoding="utf-8")
    Path(__file__).unlink()
    print("Attached the generated wide banner to member-profile responses and added regression coverage.")


if __name__ == "__main__":
    main()
