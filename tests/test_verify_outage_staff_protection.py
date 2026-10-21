"""Regression proofs for the October 21 verification-kick and trust-save incident.

Never launch a bot, send Discord requests, or touch Supabase in this suite.
"""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from stoney_verify.members_new import join_removal_safety as removal
from stoney_verify import anti_nuke
from stoney_verify.commands_ext import public_protection_center as protection


def test_automated_verification_failure_cannot_kick_even_after_grace_period(monkeypatch):
    triggered = []

    async def run():
        triggered.append("kick")

    guild = SimpleNamespace(id=123)
    member = SimpleNamespace(id=456, guild=guild)

    async def exercise(reason, action):
        return await removal.block_or_run_bot_removal(
            action=action,
            guild=guild,
            member=member,
            reason=reason,
            runner=run,
            staff_confirmed=False,
        )

    for reason in (
        "verification fail-closed: Join verification watchdog exhausted retries "
        "and member still has no safe verification role",
        "verification fail-closed: database schema cache unavailable",
    ):
        asyncio.run(exercise(reason, "kick"))
        asyncio.run(exercise(reason, "ban"))
    assert triggered == []


def test_staff_confirmed_removal_keeps_distinct_moderation_path(monkeypatch):
    triggered = []

    async def run():
        triggered.append("manual action")

    monkeypatch.setattr(
        removal, "removal_decision",
        lambda *args, **kwargs: SimpleNamespace(
            blocked=False, join_age_seconds=None
        ),
    )
    guild = SimpleNamespace(id=123)
    member = SimpleNamespace(id=456, guild=guild)
    asyncio.run(removal.block_or_run_bot_removal(
        action="kick", guild=guild, member=member,
        reason="staff confirmed moderation", runner=run, staff_confirmed=True,
    ))
    assert triggered == ["manual action"]


def test_watchdog_native_handler_does_not_include_kick_call():
    code = inspect.getsource(removal.handle_join_verification_failure)
    assert "automatic kick disabled" in code
    assert "post_fresh_join_recovery_log" in code
    assert "member_has_safe_verification_state" in code
    assert ".kick(" not in code
    assert "block_or_run_bot_removal(" not in code
    assert "no automatic kick or role" in code


def test_watchdog_logs_review_instead_of_downgrading_returning_member(monkeypatch):
    calls = []

    class FakeMember:
        def __init__(self):
            self.id = 456
            self.guild = SimpleNamespace(id=123)
            self.bot = False

    member = FakeMember()

    async def refetch(_guild, _id):
        return member

    async def review(*args, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(removal.discord, "Member", FakeMember)
    monkeypatch.setattr(removal, "refetch_live_member", refetch)
    monkeypatch.setattr(removal, "member_has_safe_verification_state", lambda _: False)
    monkeypatch.setattr(removal, "post_fresh_join_recovery_log", review)
    asyncio.run(removal.handle_join_verification_failure(
        member, "Join verification watchdog exhausted retries"
    ))
    assert len(calls) == 1
    assert calls[0]["success"] is False
    assert "no automatic kick" in calls[0]["recovery_detail"].lower()


def _mock_client(settings, *, fail=False):
    class Query:
        def select(self, _columns): return self
        def eq(self, _key, _value): return self
        def limit(self, _n): return self
        def execute(self):
            if fail:
                raise RuntimeError("sensitive Supabase secret")
            return SimpleNamespace(data=[{
                "settings": settings,
                "config": {},
                "metadata": {},
                "meta": {},
            }])
    class Client:
        def table(self, name):
            assert name == "guild_configs"
            return Query()
    return Client()


def test_antinuke_trust_save_requires_database_readback(monkeypatch):
    expected = {
        "antinuke_trusted_user_ids": [123456789012345678],
        "antinuke_trusted_role_ids": [234567890123456789],
        "antinuke_trusted_bot_ids": [],
    }
    monkeypatch.setattr(anti_nuke, "get_supabase", lambda: _mock_client(expected))
    asyncio.run(anti_nuke._verify_persisted_trust_lists(123, expected))

    monkeypatch.setattr(anti_nuke, "get_supabase", lambda: _mock_client({}))
    with pytest.raises(RuntimeError, match="did not survive database read-back"):
        asyncio.run(anti_nuke._verify_persisted_trust_lists(123, expected))


def test_antinuke_save_refuses_false_success_when_database_is_out(monkeypatch):
    async def returned_stale_cache(_guild_id, patch):
        return dict(patch)

    monkeypatch.setattr(anti_nuke, "upsert_guild_config", returned_stale_cache)
    monkeypatch.setattr(anti_nuke, "get_supabase", lambda: _mock_client({}, fail=True))
    with pytest.raises(RuntimeError, match="could not be verified") as exc:
        asyncio.run(anti_nuke.save_antinuke_settings(
            123, {"antinuke_trusted_user_ids": [123456789012345678]},
        ))
    assert "sensitive" not in str(exc.value)


def test_trust_input_invalid_does_not_receive_saved_reply(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("no database writes for invalid IDs")

    monkeypatch.setattr(protection.anti_nuke_service, "save_antinuke_settings", forbidden)
    with pytest.raises(ValueError, match="No valid Discord IDs"):
        asyncio.run(protection._save_antinuke_trust_lists(
            123, trusted_users="not-an-id", trusted_roles="", trusted_bots=""
        ))


def test_antinuke_trusted_modal_refreshes_current_settings():
    src = inspect.getsource(protection.AntiNukeTrustedIdsModal)
    assert 'get_antinuke_settings(int(guild.id), refresh=True)' in src
