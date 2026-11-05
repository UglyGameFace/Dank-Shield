from pathlib import Path


def test_birthday_settings_acknowledged_before_database_io() -> None:
    source = (Path(__file__).resolve().parents[1] / "stoney_verify/commands_ext/public_birthdays.py").read_text()
    save = source.split("async def _save_staff(", 1)[1].split("\n\nclass BirthdayTimezoneModal", 1)[0]
    assert save.index("await _defer(interaction)") < save.index("await get_guild(")
    assert save.index("await _defer(interaction)") < save.index("await save_guild(")
    assert "await open_staff_birthday(interaction)" in save
