from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import action_member_setup_pipeline as pipeline
from stoney_verify.action_member_consent import MemberSetupOptInPage
from stoney_verify.commands_ext import public_action_member_setup_preview as ui
from stoney_verify.commands_ext import public_action_schedule as schedule_ui


class FakeMember:
    def __init__(self, guild, user_id, *, bot=False):
        self.guild = guild
        self.id = user_id
        self.bot = bot


class FakeGuild:
    id = 321

    def __init__(self, members=None):
        self.members = members or {}

    def get_member(self, uid):
        return self.members.get(uid)


def _owners(monkeypatch, *, ids=(101, 102), cursor=None, revision=7, enabled=True):
    async def published(guild_id, *, refresh):
        assert guild_id == 321 and refresh
        return {
            "enabled": enabled,
            "current_revision": revision,
            "history": [{
                "revision": revision, "published_at": "2026-10-22T11:00:00+00:00",
            }] if revision > 0 else [],
        }

    async def page(guild_id, *, after_user_id, limit):
        assert guild_id == 321
        assert limit == pipeline.PAGE_SIZE
        assert after_user_id >= 0
        return MemberSetupOptInPage(tuple(ids), cursor)

    def latest(state):
        entries = state.get("history", [])
        return entries[-1] if entries else None

    monkeypatch.setattr(pipeline, "load_guild_setup_state", published)
    monkeypatch.setattr(pipeline, "latest_revision", latest)
    monkeypatch.setattr(pipeline, "read_member_setup_opt_in_page", page)


def test_integrates_saved_opt_in_owner_revision_and_real_readiness_without_sends(monkeypatch):
    _owners(monkeypatch, ids=(101, 102, 103, 104, 105), cursor=105)
    guild = FakeGuild()
    guild.members = {
        101: FakeMember(guild, 101),
        102: FakeMember(guild, 102),
        104: FakeMember(guild, 104, bot=True),
        105: FakeMember(guild, 105),
    }
    inspected = []

    async def owner(member, *, expected_revision):
        assert expected_revision == 7
        inspected.append(member.id)
        if member.id == 101:
            return SimpleNamespace(status="preview_due", delivery_authorized=False)
        if member.id == 102:
            return SimpleNamespace(status="waiting", delivery_authorized=False)
        return SimpleNamespace(status="ledger_unavailable", delivery_authorized=False)

    monkeypatch.setattr(pipeline.discord, "Member", FakeMember)
    monkeypatch.setattr(pipeline, "inspect_member_setup_readiness", owner)
    report = asyncio.run(pipeline.preview_member_setup_opt_in_page(guild))
    assert set(inspected) == {101, 102, 105}
    assert report.scanned == 5
    assert report.revision == 7
    assert report.potentially_due == 1
    assert report.next_after_user_id == 105
    assert dict(report.counts) == {
        "preview_due": 1, "waiting": 1, "not_cached": 1,
        "bot": 1, "ledger_unavailable": 1,
    }
    text = str(ui._preview_embed(report).to_dict())
    assert "ledger" in text.lower()
    assert "No delivery enabled" in text
    for uid in (101, 102, 103, 104, 105):
        # None of the private recipient IDs appears in the view.
        assert str(uid) not in text
    view = ui.CandidatePreviewView(99, guild.id, report)
    buttons = {x.label: x for x in view.children if isinstance(x, discord.ui.Button)}
    assert set(buttons) == {"Next opt-in page", "Restart preview", "Back to Schedule"}
    assert not buttons["Next opt-in page"].disabled


def test_inactive_or_unpublished_setup_never_queries_consent_source(monkeypatch):
    _owners(monkeypatch, revision=0)
    async def forbid(*_args, **_kwargs):
        raise AssertionError("No opt-in reader should run without published setup")
    monkeypatch.setattr(pipeline, "read_member_setup_opt_in_page", forbid)
    report = asyncio.run(pipeline.preview_member_setup_opt_in_page(FakeGuild()))
    assert report.source_status == "no_published_revision"
    assert report.scanned == 0
    assert report.next_after_user_id is None
    assert "No active published Member Setup revision" in str(ui._preview_embed(report).to_dict())


def test_one_member_read_failure_cannot_make_it_due(monkeypatch):
    _owners(monkeypatch, ids=(101, 102))
    guild = FakeGuild()
    guild.members = {uid: FakeMember(guild, uid) for uid in (101, 102)}
    monkeypatch.setattr(pipeline.discord, "Member", FakeMember)

    async def inspect(member, *, expected_revision):
        if member.id == 101:
            raise RuntimeError("sensitive://service-token")
        return SimpleNamespace(status="preview_due", delivery_authorized=True)

    monkeypatch.setattr(pipeline, "inspect_member_setup_readiness", inspect)
    report = asyncio.run(pipeline.preview_member_setup_opt_in_page(guild))
    assert report.potentially_due == 0
    assert dict(report.counts) == {"unavailable": 2}
    assert "service-token" not in str(ui._preview_embed(report).to_dict())
    assert ui.CandidatePreviewView(55, 321, report).next_page.disabled


def test_source_failure_timeout_and_cross_guild_input_fail_closed(monkeypatch):
    _owners(monkeypatch)
    async def fail(_gid, *, refresh):
        raise RuntimeError("postgres://private-password")
    monkeypatch.setattr(pipeline, "load_guild_setup_state", fail)
    with pytest.raises(pipeline.CandidatePreviewUnavailable, match="Candidate source"):
        asyncio.run(pipeline.preview_member_setup_opt_in_page(FakeGuild()))

    guild = FakeGuild()
    guild.id = 0
    with pytest.raises(ValueError):
        asyncio.run(pipeline.preview_member_setup_opt_in_page(guild))
    guild.id = 321
    with pytest.raises(ValueError):
        asyncio.run(pipeline.preview_member_setup_opt_in_page(guild, after_user_id=-1))
    with pytest.raises(ValueError):
        pipeline.CandidatePreviewPage(321, 7, 6, (("waiting", 6),))
    with pytest.raises(ValueError):
        pipeline.CandidatePreviewPage(321, 7, 1, (("invented_status", 1),))


def test_outage_in_opt_in_feed_is_error_not_empty_page(monkeypatch):
    _owners(monkeypatch)
    async def outage(_gid, *, after_user_id, limit):
        raise RuntimeError("backend secret")
    monkeypatch.setattr(pipeline, "read_member_setup_opt_in_page", outage)
    with pytest.raises(pipeline.CandidatePreviewUnavailable):
        asyncio.run(pipeline.preview_member_setup_opt_in_page(FakeGuild()))


def test_staff_only_schedule_entrypoint_and_safety_contract():
    draft = schedule_ui.ScheduleView(
        12, 321,
        __import__("stoney_verify.action_schedule_policy", fromlist=["ActionSchedule"]).ActionSchedule(),
    )
    buttons = {b.label for b in draft.children if isinstance(b, discord.ui.Button)}
    assert "Preview Opted-in Members" in buttons
    assert len(draft.children) <= 25
    assert all((getattr(b, "row", 0) or 0) <= 4 for b in draft.children)

    preview = ui.CandidatePreviewView(
        12, 321,
        pipeline.CandidatePreviewPage(321, 7, 0, ()),
    )
    assert preview.next_page.disabled is True
    assert "scoped_interaction_is_staff(interaction)" in Path(ui.__file__).read_text()

    service_source = Path(pipeline.__file__).read_text()
    ui_source = Path(ui.__file__).read_text()
    for forbidden in (
        "create_task(", "member.send(", "channel.send(", "reserve_member_setup_sync(",
        ".upsert(", ".insert(", ".rpc(", "fetch_members(", "fetch_member(",
    ):
        assert forbidden not in service_source + ui_source
    assert "inspect_member_setup_readiness" in service_source
    assert "read_member_setup_opt_in_page" in service_source
    assert "MAX_PARALLEL_READS = 2" in service_source
    assert "PAGE_SIZE = 5" in service_source
