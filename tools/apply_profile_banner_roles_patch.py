from __future__ import annotations

import base64
import lzma
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PART_DIR = ROOT / "tools" / "profile_banner_roles_payload"
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

jobs:
  focused-profile-tests:
    runs-on: ubuntu-24.04
    timeout-minutes: 15
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
      - name: Run focused profile runtime tests
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
            tests/test_profile_card_setup_picker_ux.py \
            tests/test_profile_card_migration_safety.py \
            tests/test_profile_card_service.py \
            tests/test_profile_platform_privacy_preview_ux.py \
            tests/test_profile_platform_display_modes.py \
            tests/test_profile_role_display_separation.py
'''


def main() -> None:
    parts = sorted(PART_DIR.glob("*.part"))
    if not parts:
        raise RuntimeError("Profile banner patch payload is missing.")
    encoded = "".join(path.read_text(encoding="ascii").strip() for path in parts)
    patch_bytes = lzma.decompress(base64.b64decode(encoded))
    patch_path = ROOT / ".profile-banner-roles.patch"
    patch_path.write_bytes(patch_bytes)
    try:
        subprocess.run(
            ["git", "apply", "--whitespace=nowarn", str(patch_path)],
            cwd=ROOT,
            check=True,
        )
    except subprocess.CalledProcessError:
        subprocess.run(
            ["git", "apply", "--check", "--verbose", str(patch_path)],
            cwd=ROOT,
            check=False,
        )
        raise
    finally:
        patch_path.unlink(missing_ok=True)

    workflow = ROOT / ".github" / "workflows" / "profile-runtime-diagnostics.yml"
    workflow.write_text(FINAL_WORKFLOW, encoding="utf-8")
    for relative in (
        ".github/workflows/apply-profile-platform-modes.yml",
        "tools/apply_profile_platform_modes_patch.py",
        "tools/apply_profile_platform_modes_v2.py",
        "tools/build_profile_platform_logos.mjs",
    ):
        target = ROOT / relative
        target.unlink(missing_ok=True)
    shutil.rmtree(PART_DIR)
    self_path = Path(__file__).resolve()
    print("Applied wide 420 profile banner, real application-logo controls, and separated role menus.")
    self_path.unlink()


if __name__ == "__main__":
    main()
