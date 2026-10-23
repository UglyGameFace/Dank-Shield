from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from stoney_verify import action_canary_delivery as canary
from stoney_verify.action_builder_policy import ActionBuilderDraft, edit_rule
from stoney_verify.action_delivery_preflight import DeliveryPreflight
from stoney_verify.action_delivery_readiness import ActionDeliveryReadiness
from stoney_verify.action_schedule_policy import ActionSchedule

AT = datetime(2026, 10, 23, 16, tzinfo=timezone.utc)


def fake_member(monkeypatch):
    class Member:
        id = 601
        bot = False
        def __init__(self):
            self.guild = SimpleNamespace(
                id=501, get_member=lambda user_id: self if user_id == self.id else None,
            )
            self.sent = []
        async def send(self, text, *, allowed_mentions):
            self.sent.append((text, allowed_mentions))
    monkeypatch.setattr(canary.discord, "Member", Member)
    return Member()


def setup(monkeypatch, member, *, flags=True, readiness="preview_due", reserved=True,
          final="candidate_preview", send_fails=False, schedule_change=False,
          revoke_during="none", day_jump=False):
    if flags:
        monkeypatch.setenv(canary.ENABLE_ENV, "1")
        monkeypatch.setenv(canary.GUILD_ENV, "501")
        monkeypatch.setenv(canary.USER_ENV, "601")
        monkeypatch.setenv(canary.REVISION_ENV, "3")
    else:
        for env in (canary.ENABLE_ENV, canary.GUILD_ENV, canary.USER_ENV, canary.REVISION_ENV):
            monkeypatch.delenv(env, raising=False)

    events = []
    clock = [AT]
    monkeypatch.setattr(canary, "_utcnow", lambda: clock[0])
    first = ActionSchedule(timezone="UTC", quiet_start=0, quiet_end=0)
    changed = ActionSchedule(timezone="UTC", quiet_start=22, quiet_end=8)
    state = iter([first, changed if schedule_change else first,
                  changed if schedule_change else first])
    async def load(gid, *, refresh=True):
        events.append("policy")
        assert gid == 501 and refresh
        return {"source": "test"}, next(state)
    draft = edit_rule(ActionBuilderDraft(), condition="member_setup_revision",
                      included=True, severity="recommended", note="")
    async def inspect(_member, *, expected_revision, now):
        events.append("readiness")
        assert expected_revision == 3 and now == AT
        return ActionDeliveryReadiness(
            readiness, "canary test", revision=3, ledger_checked=readiness == "preview_due",
        )
    async def reserve(gid, uid, revision, **kwargs):
        events.append("claim")
        assert (gid, uid, revision) == (501, 601, 3)
        assert kwargs["max_attempts"] == 1
        assert kwargs["max_guild_per_day"] == 1
        assert kwargs["local_day"].isoformat() == "2026-10-23"
        if revoke_during == "claim":
            monkeypatch.setenv(canary.ENABLE_ENV, "0")
        if day_jump:
            clock[0] = AT + timedelta(days=1)
        return reserved
    async def final_check(_member, *, expected_revision, now):
        events.append("final")
        assert expected_revision == 3
        if revoke_during == "final":
            monkeypatch.setenv(canary.ENABLE_ENV, "0")
        return DeliveryPreflight(final, "canonical state", revision=3)
    monkeypatch.setattr(canary, "load_schedule", load)
    monkeypatch.setattr(canary, "parse_action_builder", lambda cfg: draft)
    monkeypatch.setattr(canary, "inspect_member_setup_readiness", inspect)
    monkeypatch.setattr(canary, "inspect_member_setup_candidate", final_check)
    monkeypatch.setattr(canary, "reserve_member_setup_sync", reserve)
    # The real async worker offloads a *sync* function. Avoid patching
    # asyncio.to_thread globally so this test stays deterministic.
    async def fake_to_thread(func, *args, **kwargs):
        return await func(*args, **kwargs)
    monkeypatch.setattr(canary.asyncio, "to_thread", fake_to_thread)
    if send_fails:
        async def failing_send(text, *, allowed_mentions):
            events.append("DM_error")
            raise RuntimeError("Discord delivery unavailable")
        member.send = failing_send
    return events


def deliver(member):
    return asyncio.run(canary.dispatch_single_member_setup_canary(member, expected_revision=3))


def test_unconfigured_or_wrong_target_blocks_without_db_or_send(monkeypatch):
    member = fake_member(monkeypatch)
    events = setup(monkeypatch, member, flags=False)
    result = deliver(member)
    assert result.status == "disabled"
    assert events == [] and member.sent == []
    setup(monkeypatch, member)
    monkeypatch.setenv(canary.USER_ENV, "602")
    assert deliver(member).status == "disabled"
    assert member.sent == []


def test_sends_exactly_one_member_setup_canary_after_atomic_reservation(monkeypatch):
    member = fake_member(monkeypatch)
    events = setup(monkeypatch, member)
    result = deliver(member)
    assert result.status == "sent"
    assert result.sent and result.claimed
    assert events == ["policy", "readiness", "policy", "claim", "policy", "final"]
    assert len(member.sent) == 1
    text, allowed = member.sent[0]
    assert "/dank" in text
    assert "@everyone" not in text and "@here" not in text
    assert allowed.everyone is False and allowed.roles is False
    assert not hasattr(canary, "start_worker")


def test_not_due_or_unmigrated_ledger_never_attempts_atomic_claim(monkeypatch):
    member = fake_member(monkeypatch)
    for state in ("waiting", "capped", "exhausted", "ledger_unavailable", "opted_out"):
        events = setup(monkeypatch, member, readiness=state)
        result = deliver(member)
        assert result.status == "blocked"
        assert events == ["policy", "readiness"]
    assert member.sent == []


def test_claim_denied_cannot_send(monkeypatch):
    member = fake_member(monkeypatch)
    events = setup(monkeypatch, member, reserved=False)
    result = deliver(member)
    assert result.status == "claim_denied"
    assert not result.claimed and not result.sent
    assert "final" not in events and not member.sent


def test_post_claim_optout_or_completion_burns_attempt_without_dm(monkeypatch):
    member = fake_member(monkeypatch)
    for state in ("opted_out", "complete", "stale", "quiet", "unavailable"):
        events = setup(monkeypatch, member, final=state)
        result = deliver(member)
        assert result.status == "cancelled" and result.claimed and not result.sent
        assert "claim" in events and "final" in events
    assert member.sent == []


def test_canary_revocation_during_claim_and_just_before_send_blocks(monkeypatch):
    member = fake_member(monkeypatch)
    for phase in ("claim", "final"):
        events = setup(monkeypatch, member, revoke_during=phase)
        result = deliver(member)
        assert result.status == "cancelled" and result.claimed and not result.sent
        assert "claim" in events
    assert member.sent == []


def test_failed_dm_consumes_attempt_no_retry(monkeypatch):
    member = fake_member(monkeypatch)
    events = setup(monkeypatch, member, send_fails=True)
    result = deliver(member)
    assert result.status == "send_failed"
    assert result.claimed and not result.sent
    assert events.count("claim") == 1 and events.count("DM_error") == 1


def test_cached_membership_not_proven_blocks_without_network_scan(monkeypatch):
    member = fake_member(monkeypatch)
    events = setup(monkeypatch, member)
    member.guild.get_member = lambda _id: None
    result = deliver(member)
    assert result.status == "blocked"
    assert events == [] and not member.sent


def test_policy_change_and_day_boundary_block_canary(monkeypatch):
    member = fake_member(monkeypatch)
    events = setup(monkeypatch, member, schedule_change=True)
    result = deliver(member)
    assert result.status == "stale"
    assert "claim" not in events
    events = setup(monkeypatch, member, day_jump=True)
    result = deliver(member)
    assert result.status == "cancelled" and result.claimed and not result.sent
    assert member.sent == []
    # A race after claim does not authorize retries or a second claim.
    assert events.count("claim") == 1


def test_no_scheduled_caller_or_broad_scan_and_single_target_gates():
    root = Path(__file__).resolve().parents[1]
    code = Path(canary.__file__).read_text(encoding="utf-8")
    assert "create_task(" not in code
    assert "bot.guilds" not in code
    assert "fetch_members(" not in code
    assert "app_commands" not in code
    assert "max_attempts=1" in code and "max_guild_per_day=1" in code
    assert 'os.getenv(ENABLE_ENV, "") == "1"' in code
    assert 'os.getenv(REVISION_ENV, "") == str(revision)' in code
    # No startup, command, or worker imports canary dispatch until a separate
    # explicit production rollout. This PR cannot independently activate it.
    for path in (
        root / "main.py", root / "stoney_verify/verification_reminders.py",
        root / "stoney_verify/commands_ext/public_action_center.py",
        root / "stoney_verify/commands_ext/public_action_schedule.py",
    ):
        assert "action_canary_delivery" not in path.read_text(encoding="utf-8")
