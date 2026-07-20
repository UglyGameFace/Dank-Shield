from __future__ import annotations

"""Permanent source-contract audit for Dank Design Smart Auto-Detect."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAJORITY = (ROOT / "stoney_verify/services/server_design_majority_layout.py").read_text(encoding="utf-8")
STUDIO = (ROOT / "stoney_verify/services/server_design_studio.py").read_text(encoding="utf-8")
COMMANDS = (ROOT / "stoney_verify/commands_ext/public_design_commands.py").read_text(encoding="utf-8")
TESTS = (ROOT / "tests/test_server_design_category_aware_auto_detect.py").read_text(encoding="utf-8")


def main() -> int:
    failures: list[str] = []

    required_majority = (
        "def infer_category_local_layouts(",
        "def build_category_aware_options(",
        "def annotate_category_aware_plan_items(",
        "sorted(set(ephemeral_ids))",
        "sorted(set(preserve_ids))",
        "sorted(groups.items(), key=lambda item: item[0])",
        "value = sorted(top_values, key=repr)[0]",
        "current_example = separator_examples.get(key)",
    )
    for marker in required_majority:
        if marker not in MAJORITY:
            failures.append(f"majority layout missing contract marker: {marker}")

    # Separator identity must compare the raw stored separator value. Do not
    # normalize or strip whitespace here: "│" and " │ " are different styles.
    separator_helper_start = MAJORITY.find("def _separator_spec_exists(")
    separator_helper_end = MAJORITY.find("\ndef ensure_separator_spec(", separator_helper_start)
    if separator_helper_start < 0 or separator_helper_end < 0:
        failures.append("separator identity helper is missing")
    else:
        separator_helper = MAJORITY[separator_helper_start:separator_helper_end]
        if 'raw_value = str(getattr(spec, "value", "") or "")' not in separator_helper:
            failures.append("separator identity helper does not read the raw stored separator value")
        if "raw_value == value" not in separator_helper:
            failures.append("separator identity helper does not compare the raw stored separator value directly")
        if ".strip(" in separator_helper:
            failures.append("separator identity helper strips whitespace and would collapse compact/spaced separators")

    for function_name in (
        "_fail_repair_item",
        "_expected_separator",
        "_separator_matches_expected",
        "_expected_frame",
        "_frame_matches_expected",
        "_font_matches_expected",
        "validate_majority_repair_items",
    ):
        count = MAJORITY.count(f"def {function_name}(")
        if count != 1:
            failures.append(f"{function_name} definition count is {count}, expected 1")

    if 'if mode == "keep_existing":\n        return existing' not in STUDIO:
        failures.append("keep_existing icon mode does not return the exact existing icon value")

    required_tests = (
        "test_exact_font_detection_distinguishes_supported_lettering_families",
        "test_separator_identity_preserves_raw_spacing",
        "test_each_category_gets_its_own_font_and_separator_majority",
        "test_global_lock_prevents_smart_auto_detect_from_overwriting_any_unlocked_dimension",
        "test_uncertain_local_dimensions_preserve_each_channels_current_style",
        "test_category_local_detection_is_semantically_deterministic_when_scan_order_changes",
        "test_keep_existing_icon_mode_does_not_invent_placeholder_icon",
    )
    for marker in required_tests:
        if marker not in TESTS:
            failures.append(f"category-aware regression coverage missing: {marker}")

    if "build_category_aware_options" not in COMMANDS:
        failures.append("public design command flow does not use category-aware options")
    if "annotate_category_aware_plan_items" not in COMMANDS:
        failures.append("public design preview flow does not annotate category-aware decisions")

    if failures:
        print("DANK DESIGN SMART AUTO-DETECT AUDIT FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("DANK DESIGN SMART AUTO-DETECT AUDIT OK")
    print("category_local=yes raw_separator_identity=yes deterministic=yes keep_existing_exact=yes duplicate_helpers=no")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
