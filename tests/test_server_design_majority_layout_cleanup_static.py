from __future__ import annotations

from pathlib import Path

SOURCE = Path("stoney_verify/services/server_design_majority_layout.py").read_text(encoding="utf-8")


def test_majority_repair_safety_helpers_are_defined_once() -> None:
    for function_name in (
        "_fail_repair_item",
        "_expected_separator",
        "_separator_matches_expected",
        "_expected_frame",
        "_frame_matches_expected",
        "_font_matches_expected",
        "validate_majority_repair_items",
    ):
        assert SOURCE.count(f"def {function_name}(") == 1, function_name


def test_category_aware_auto_detect_outputs_are_deterministically_ordered() -> None:
    assert "top_values = [value for value, value_count in counter.items() if value_count == count]" in SOURCE
    assert "value = sorted(top_values, key=repr)[0]" in SOURCE
    assert "for category_id, rows in sorted(groups.items(), key=lambda item: item[0])" in SOURCE
    assert '"category_names": dict(sorted(category_names.items(), key=lambda item: item[0]))' in SOURCE
    assert 'out["__auto_detect_ephemeral_channel_ids"] = sorted(set(ephemeral_ids))' in SOURCE
    assert 'out["__auto_detect_preserve_ids"] = sorted(set(preserve_ids))' in SOURCE
    assert 'dict(sorted(analyses.items(), key=lambda item: str(item[0])))' in SOURCE


def test_separator_example_choice_is_deterministic() -> None:
    assert "current_example = separator_examples.get(key)" in SOURCE
    assert "repr(sorted(candidate.items())) < repr(sorted(current_example.items()))" in SOURCE
