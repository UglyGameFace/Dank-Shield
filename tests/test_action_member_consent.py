from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import action_member_consent as prefs
from stoney_verify import action_delivery_preflight as gate
from stoney_verify.action_builder_policy import ActionBuilderDraft, edit_rule
from stoney_verify.action_schedule_policy import ActionSchedule
from stoney_verify.commands_ext import public_action_preferences as ui


def test_consent_starts_off_and_enforces_versioned_explicit_bool():
    assert not prefs.parse_consent({}).member_setup_dm
    known = prefs.MemberReminderConsent(True, "2026-10-23T12:00:00+00:00")
    assert prefs.parse_consent({prefs.KEY: known.to_payload()}) == known
    for raw in (
        {"version": 2, "member_setup_dm": True, "updated_at": known.updated_at},
        {"version": 1, "member_setup_dm": 1, "updated_at": known.updated_at},
        {"version": 1, "member_setup_dm": "false", "updated_at": known.updated_at},
        {"version": 1, "member_setup_dm": True},
        {"version": 1, "member_setup_dm": True, "updated_at": "invalid"},
    ):
        with pytest.raises(ValueError):
            prefs.parse_consent({prefs.KEY: raw})


def test_verified_per_guild_save_and_stop_does_not_change_other_settings(monkeypatch):
    from stoney_verify import profile_card_service as profiles
    records = {(100, 200): {"other_feature": {"keep": True}}}
    async def get(gid, uid, *, refresh=False):
        assert refresh
        return {"settings": dict(records.get((gid, uid), {}))}
    async def put(gid, uid, key, value):
        records.setdefault((gid, uid), {})[key] = value
        return {"settings": dict(records[(gid, uid)])}
    monkeypatch.setattr(profiles, "get_profile_guild_settings", get)
    monkeypatch.setattr(profiles, "upsert_profile_guild_namespace", put)
    enabled = asyncio.run(prefs.save_member_consent(100, 200, enabled=True))
    assert enabled.member_setup_dm
    assert not asyncio.run(prefs.read_member_consent(100, 201)).member_setup_dm
    assert not asyncio.run(prefs.read_member_consent(101, 200)).member_setup_dm
    stopped = asyncio.run(prefs.save_member_consent(100, 200, enabled=False))
    assert not stopped.member_setup_dm
    assert records[(100, 200)]["other_feature"] == {"keep": True}
    with pytest.raises(ValueError):
        asyncio.run(prefs.save_member_consent(100, 200, enabled="yes"))


def test_failed_private_write_and_storage_outage_never_implies_consent(monkeypatch):
    from stoney_verify import profile_card_service as profiles
    async def blank(gid, uid, *, refresh=False):
        return {"settings": {}}
    async def missing(gid, uid, key, value):
        return {"settings": {}}
    monkeypatch.setattr(profiles, "get_profile_guild_settings", blank)
    monkeypatch.setattr(profiles, "upsert_profile_guild_namespace", missing)
    with pytest.raises(RuntimeError, match="not confirmed"):
        asyncio.run(prefs.save_member_consent(100, 200, enabled=True))
    async def down(gid, uid, *, refresh=False):
        raise RuntimeError("Supabase error with private details")
    monkeypatch.setattr(profiles, "get_profile_guild_settings", down)
    with pytest.raises(RuntimeError):
        asyncio.run(prefs.read_member_consent(100, 200))
    with pytest.raises(RuntimeError):
        asyncio.run(prefs.save_member_consent(100, 200, enabled=True))


def test_member_private_view_does_not_expose_another_members_settings():
    off = ui.MemberActionPreferencesView(42, 987, prefs.MemberReminderConsent())
    on = ui.MemberActionPreferencesView(
        42, 987, prefs.MemberReminderConsent(True, "2026-10-23T12:00:00+00:00")
    )
    assert off.enable.disabled is False
    assert off.stop.disabled is True
    assert on.enable.disabled is True
    assert on.stop.disabled is False
    labels = {v.label for v in on.children if isinstance(v, discord.ui.Button)}
    assert labels == {"Enable Future Setup DMs", "Stop Setup DMs", "Check Setup DM Status", "Back to Action Inbox"}
    assert "not live" in ui._embed(prefs.MemberReminderConsent()).description
    assert "Verification reminders have separate controls" in ui._embed(prefs.MemberReminderConsent()).description


def test_failed_stop_explicitly_warns_that_prior_opt_in_may_remain(monkeypatch):
    messages = []
    async def deferred(_interaction):
        return None
    async def failed(*args, **kwargs):
        raise RuntimeError("Storage offline")
    async def capture(_interaction, content):
        messages.append(content)
    monkeypatch.setattr(ui, "_defer", deferred)
    monkeypatch.setattr(ui, "save_member_consent", failed)
    monkeypatch.setattr(ui, "_msg", capture)
    view = ui.MemberActionPreferencesView(
        42, 123, prefs.MemberReminderConsent(True, "2026-10-23T12:00:00+00:00")
    )
    asyncio.run(view._save(SimpleNamespace(), enabled=False))
    assert len(messages) == 1
    assert "previous opt-in may still be stored" in messages[0]
    assert "not currently active" in messages[0]


def test_static_no_background_worker_new_slash_or_auto_dm():
    source = Path(prefs.__file__).read_text()
    guard = Path(gate.__file__).read_text()
    ui_source = Path(ui.__file__).read_text()
    for code in (source, guard, ui_source):
        assert "create_task(" not in code
        assert "channel.send(" not in code
        assert "member.send(" not in code
        assert "reserve_member_setup_sync(" not in code
        assert "discord.app_commands" not in code
    assert "get_profile_guild_settings" in source
    assert "upsert_profile_guild_namespace" in source
    assert "read_member_consent" in guard
    assert "_member_payload" in guard
    assert "delivery_authorized: bool = False" in guard
    with pytest.raises(ValueError):
        gate.DeliveryPreflight("fake", "bad", delivery_authorized=True)


def _member(monkeypatch):
    class FakeMember:
        id = 200
        bot = False
        joined_at = datetime(2026, 10, 20, tzinfo=timezone.utc)
        guild = SimpleNamespace(id=100, owner_id=999)
    monkeypatch.setattr(gate.discord, "Member", FakeMember)
    return FakeMember()


def _patch_preflight(monkeypatch, *, consent=True, current=3, complete=False,
                     included=True, published="2026-10-21T00:00:00+00:00"):
    from stoney_verify.commands_ext import public_member_setup
    async def load(gid, *, refresh=True):
        return {"source": "test"}, ActionSchedule(timezone="UTC", quiet_start=0, quiet_end=0)
    async def consent_get(gid, uid):
        return prefs.MemberReminderConsent(consent, "2026-10-22T00:00:00+00:00")
    async def member_truth(member):
        state = {"current_revision": current, "history": [
            {"revision": current, "severity": "required", "published_at": published,
             "changed_sections": ["community"]},
        ]}
        status = {"current_revision": current, "enabled": True,
                  "is_current": complete,
                  "pending_sections": [] if complete else ["community"]}
        return state, {}, status
    draft = edit_rule(ActionBuilderDraft(), condition="member_setup_revision",
                      included=included, severity="recommended", note="")
    monkeypatch.setattr(gate, "load_schedule", load)
    monkeypatch.setattr(gate, "read_member_consent", consent_get)
    monkeypatch.setattr(gate, "parse_action_builder", lambda _cfg: draft)
    monkeypatch.setattr(public_member_setup, "_member_payload", member_truth)
    monkeypatch.setattr(public_member_setup, "_member_exempt", lambda _member: False)


def test_preflight_never_authorizes_sends_even_when_due(monkeypatch):
    member = _member(monkeypatch)
    _patch_preflight(monkeypatch)
    result = asyncio.run(gate.inspect_member_setup_candidate(
        member, expected_revision=3,
        now=datetime(2026, 10, 26, tzinfo=timezone.utc),
    ))
    assert result.status == "candidate_preview"
    assert result.revision == 3
    assert result.delivery_authorized is False
    assert "no real delivery" in result.reason


def test_preflight_respects_opt_out_and_stale_completion(monkeypatch):
    member = _member(monkeypatch)
    _patch_preflight(monkeypatch, consent=False)
    result = asyncio.run(gate.inspect_member_setup_candidate(member, expected_revision=3))
    assert result.status == "opted_out"
    _patch_preflight(monkeypatch, consent=True, current=4)
    result = asyncio.run(gate.inspect_member_setup_candidate(member, expected_revision=3))
    assert result.status == "stale"
    _patch_preflight(monkeypatch, consent=True, complete=True)
    result = asyncio.run(gate.inspect_member_setup_candidate(member, expected_revision=3))
    assert result.status == "complete"
    _patch_preflight(monkeypatch, consent=True, included=False)
    result = asyncio.run(gate.inspect_member_setup_candidate(member, expected_revision=3))
    assert result.status == "excluded"


def test_preflight_unknown_publication_and_outage_fail_closed(monkeypatch):
    member = _member(monkeypatch)
    _patch_preflight(monkeypatch, published="")
    decision = asyncio.run(gate.inspect_member_setup_candidate(
        member, expected_revision=3, now=datetime(2026, 10, 26, tzinfo=timezone.utc),
    ))
    assert decision.status == "blocked"
    _patch_preflight(monkeypatch)
    async def unavailable(gid, uid):
        raise RuntimeError("No DB")
    monkeypatch.setattr(gate, "read_member_consent", unavailable)
    decision = asyncio.run(gate.inspect_member_setup_candidate(member, expected_revision=3))
    assert decision.status == "unavailable"
    assert not decision.delivery_authorized


def test_member_setup_status_is_read_only_and_uses_authoritative_adapter(monkeypatch):
    from stoney_verify import member_setup_service
    from stoney_verify import action_delivery_readiness
    member = SimpleNamespace(guild=SimpleNamespace(id=901), id=902)
    seen = []
    async def load(guild_id, *, refresh):
        seen.append(("setup", guild_id, refresh))
        return {"current_revision": 8}
    async def inspect(target, *, expected_revision):
        seen.append(("eligibility", target.id, expected_revision))
        return SimpleNamespace(status="exempt", next_at=None)
    monkeypatch.setattr(member_setup_service, "load_guild_setup_state", load)
    monkeypatch.setattr(action_delivery_readiness, "inspect_member_setup_readiness", inspect)
    status = asyncio.run(ui._read_my_setup_dm_status(member))
    assert seen == [("setup", 901, True), ("eligibility", 902, 8)]
    assert "owners" in status and "exempt" in status.lower()
    assert "No Action Center DM is scheduled or authorized" in status
    assert "🛡️ Current Member Setup DM status" in {
        field.name for field in ui._embed(prefs.MemberReminderConsent(), status=status).fields
    }


def test_member_setup_status_without_published_revision_never_inspects_claims(monkeypatch):
    from stoney_verify import member_setup_service
    from stoney_verify import action_delivery_readiness
    async def no_revision(guild_id, *, refresh):
        return {"current_revision": 0}
    async def forbidden(*args, **kwargs):
        raise AssertionError("No private DB or readiness call without a revision")
    monkeypatch.setattr(member_setup_service, "load_guild_setup_state", no_revision)
    monkeypatch.setattr(action_delivery_readiness, "inspect_member_setup_readiness", forbidden)
    status = asyncio.run(ui._read_my_setup_dm_status(
        SimpleNamespace(guild=SimpleNamespace(id=901), id=902),
    ))
    assert "No published Member Setup revision" in status
    assert "Delivery remains disabled" in status


def test_member_setup_status_unknown_result_fails_closed_without_exposing_reason(monkeypatch):
    from stoney_verify import member_setup_service
    from stoney_verify import action_delivery_readiness
    async def load(guild_id, *, refresh):
        return {"current_revision": 1}
    async def inspect(member, *, expected_revision):
        return SimpleNamespace(status="raw-db-error", reason="private password", next_at=None)
    monkeypatch.setattr(member_setup_service, "load_guild_setup_state", load)
    monkeypatch.setattr(action_delivery_readiness, "inspect_member_setup_readiness", inspect)
    status = asyncio.run(ui._read_my_setup_dm_status(
        SimpleNamespace(guild=SimpleNamespace(id=901), id=902),
    ))
    assert "Eligibility could not be verified" in status
    assert "private password" not in status
    assert "Delivery remains disabled" in status
