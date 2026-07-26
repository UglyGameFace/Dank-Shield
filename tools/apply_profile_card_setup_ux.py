from pathlib import Path
import runpy


ROOT = Path(__file__).resolve().parents[1]
IMPL = ROOT / "tools" / "apply_profile_card_setup_ux_impl.py"
runpy.run_path(str(IMPL), run_name="__main__")
IMPL.unlink(missing_ok=True)


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    source = target.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"expected one contract anchor in {path}: {old!r}; found {count}")
    target.write_text(source.replace(old, new, 1), encoding="utf-8")


replace_once(
    "tests/test_setup_advanced_options_behavior.py",
    '        "Server Design",\n        "Backups & History",\n',
    '        "Server Design",\n        "Member Profiles & Live Cards",\n        "Backups & History",\n',
)
replace_once(
    "tests/test_setup_aio_navigation_behavior.py",
    '        "Server Design",\n        "Backups & History",\n',
    '        "Server Design",\n        "Member Profiles & Live Cards",\n        "Backups & History",\n',
)
replace_once(
    "tests/test_welcome_join_channel_routing_static.py",
    '    assert "join welcomes pause instead of posting somewhere else" in SETUP\n    assert "Join welcomes will pause instead of posting to another channel." in SETUP\n',
    '    assert "join announcements pause instead of posting somewhere else" in SETUP\n    assert "Join announcements will pause instead of posting to another channel." in SETUP\n',
)
