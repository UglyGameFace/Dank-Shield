from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT = (ROOT / "stoney_verify/ui/picker.py").read_text(encoding="utf-8")


def test_multi_picker_action_contract_exists() -> None:
    assert "MultiPickerAction" in TEXT
    assert "Callable[[discord.Interaction, list[str]], Awaitable[None]]" in TEXT


def test_dank_multi_picker_view_exists() -> None:
    assert "class DankMultiPickerView" in TEXT
    assert "class _DankMultiPickerSelect" in TEXT
    assert "min_values" in TEXT
    assert "max_values" in TEXT


def test_multi_picker_uses_shared_choice_contract() -> None:
    assert "[choice.to_option() for choice in owner.choices" in TEXT
    assert "Sequence[DankChoice]" in TEXT


def test_cancel_supports_single_and_multi_picker() -> None:
    assert "isinstance(owner, (DankPickerView, DankMultiPickerView))" in TEXT


def test_multi_picker_is_exported() -> None:
    assert '"DankMultiPickerView"' in TEXT
    assert '"MultiPickerAction"' in TEXT


if __name__ == "__main__":
    for test in (
        test_multi_picker_action_contract_exists,
        test_dank_multi_picker_view_exists,
        test_multi_picker_uses_shared_choice_contract,
        test_cancel_supports_single_and_multi_picker,
        test_multi_picker_is_exported,
    ):
        test()
        print(f"PASS {test.__name__}")
