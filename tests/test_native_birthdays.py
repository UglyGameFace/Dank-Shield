from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from stoney_verify import birthday_service as service
from stoney_verify.commands_ext import public_birthdays as ui
from stoney_verify.commands_ext import public_self_roles_group as profiles


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_birthday_requires_real_past_year_and_correct_leap_births() -> None:
    assert service.valid_birth_date(2, 29, 2004, today=date(2026, 11, 4)) == (2, 29, 2004)
    assert service.valid_birth_date(6, 20, 2002, today=date(2026, 11, 4)) == (6, 20, 2002)
    for month, day, year in (
        (0, 1, 2000), (2, 30, 2000), (4, 31, 2000),
        (13, 1, 2000), (1, 0, 2000), (2, 29, 2003),
        (6, 20, 1899), (6, 20, 2027), (12, 31, 2026),
    ):
        with pytest.raises(ValueError):
            service.valid_birth_date(month, day, year, today=date(2026, 11, 4))
    assert service.valid_date(2, 29) == (2, 29)  # Celebration rule unchanged.


def test_feb_29_policy_only_changes_in_non_leap_years() -> None:
    assert service.celebrated_on(2, 29, date(2028, 2, 29), "feb28")
    assert not service.celebrated_on(2, 29, date(2028, 2, 28), "feb28")
    assert service.celebrated_on(2, 29, date(2027, 2, 28), "feb28")
    assert not service.celebrated_on(2, 29, date(2027, 3, 1), "feb28")
    assert service.celebrated_on(2, 29, date(2027, 3, 1), "mar1")
    assert not service.celebrated_on(2, 29, date(2027, 2, 28), "mar1")
    assert not service.celebrated_on(6, 20, date(2027, 6, 21), "feb28")


def test_announcement_and_role_expiry_obey_real_dst() -> None:
    assert service.next_daily_fire("America/New_York", now=utc("2026-01-01T12:00:00Z")) == utc("2026-01-01T14:00:00Z")
    assert service.next_daily_fire("America/New_York", now=utc("2026-07-01T12:00:00Z")) == utc("2026-07-01T13:00:00Z")
    assert service.next_daily_fire("America/New_York", now=utc("2026-03-07T15:00:00Z")) == utc("2026-03-08T13:00:00Z")
    assert service.birthday_role_expires("America/New_York", date(2026, 3, 7)) == utc("2026-03-08T05:00:00Z")
    assert service.birthday_role_expires("America/New_York", date(2026, 11, 1)) == utc("2026-11-02T05:00:00Z")
    with pytest.raises(ValueError):
        service.valid_zone("Planet/Mars")
    with pytest.raises(ValueError):
        service.next_daily_fire("UTC", hour=30)


def test_member_record_is_private_and_opt_in_by_default(monkeypatch) -> None:
    captured = []

    async def run(label, callback):
        class Table:
            def upsert(self, payload, on_conflict):
                assert on_conflict == "guild_id,user_id"
                captured.append(payload)
                return self

            def execute(self):
                return SimpleNamespace(data=[dict(captured[-1])])

        class FakeDb:
            def table(self, name):
                assert name == service.MEMBERS
                return Table()

        return callback(FakeDb())

    monkeypatch.setattr(service, "_execute", run)
    row = asyncio.run(service.save_member(123, 456, month=2, day=29, year=2004))
    assert row["guild_id"] == "123"
    assert row["user_id"] == "456"
    assert (row["birth_month"], row["birth_day"], row["birth_year"]) == (2, 29, 2004)
    assert not row["public_wishes"] and not row["birthday_role"] and not row["dm_wishes"]
    assert "age" not in row  # Self-reported dates are not proof of age.
    with pytest.raises(ValueError):
        asyncio.run(service.save_member(123, 456, month=2, day=30, year=2004))
    assert len(captured) == 1


def test_server_is_disabled_by_default_and_requires_a_channel(monkeypatch) -> None:
    values = []

    async def run(label, callback):
        class Table:
            def upsert(self, payload, on_conflict):
                assert on_conflict == "guild_id"
                values.append(payload)
                return self

            def execute(self):
                return SimpleNamespace(data=[dict(values[-1])])

        class FakeDb:
            def table(self, name):
                assert name == service.GUILDS
                return Table()
        return callback(FakeDb())

    monkeypatch.setattr(service, "_execute", run)
    row = asyncio.run(service.save_guild(555, enabled=False, zone_name="America/New_York"))
    assert row["enabled"] is False and row["channel_id"] is None
    with pytest.raises(ValueError):
        asyncio.run(service.save_guild(555, enabled=True, zone_name="UTC"))
    with pytest.raises(ValueError):
        asyncio.run(service.save_guild(555, enabled=False, zone_name="UTC", leap_policy="unexpected"))


def test_command_and_profile_builder_integration_is_discoverable() -> None:
    assert {command.name for command in ui.birthday_group.commands} == {"me", "set", "setup", "upcoming"}
    root = Path(__file__).resolve().parents[1]
    source = (root / "stoney_verify/commands_ext/public_command_surface_v2.py").read_text()
    assert "dank_group.add_command(birthday_group)" in source
    assert '"cheers", "birthdays"' in source
    assert "birthday" not in profiles.PROFILE_CATEGORIES  # Not a self-assignable cosmetic access role.
    assert "birthday" in profiles.PROFILE_COSMETIC_BLOCKED_NAME_PARTS


def test_migration_uses_service_role_and_atomic_claims() -> None:
    root = Path(__file__).resolve().parents[1]
    sql = (root / "supabase_fresh/migrations/202611040001_dankshield_native_birthdays.sql").read_text()
    assert "enable row level security" in sql.lower()
    assert "grant execute on function public.dank_birthdays_claim_due" in sql.lower()
    assert "for update skip locked" in sql.lower()
    assert "on conflict do nothing" in sql.lower()
    assert "birth_year" not in sql.lower()
    assert "dank_birthday_role_leases" in sql
    extra = (root / "supabase_fresh/migrations/202611040002_dankshield_birth_year.sql").read_text()
    assert "add column if not exists birth_year" in extra.lower()
    assert "dank_birthday_valid_year" in extra
    assert "not null" not in extra.lower()
    assert "grant " not in extra.lower().lower()
