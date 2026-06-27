from pathlib import Path

PYTEST_INI = Path(__file__).resolve().parents[1] / "pytest.ini"


def test_pytest_collects_only_real_tests_directory():
    text = PYTEST_INI.read_text(encoding="utf-8")

    assert "testpaths = tests" in text
    assert "python_files = test_*.py" in text
    assert "tools" not in text.lower()


def test_tools_test_named_scripts_are_not_pytest_collected():
    script = Path(__file__).resolve().parents[1] / "tools/test_custom_setup_clear_toggles.py"
    assert script.exists()
    assert "raise SystemExit" in script.read_text(encoding="utf-8")
    assert "testpaths = tests" in PYTEST_INI.read_text(encoding="utf-8")
