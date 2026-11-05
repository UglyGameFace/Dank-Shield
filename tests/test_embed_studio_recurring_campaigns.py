"""Recurring Studio campaigns: one shared safe sender, bounded claims, civil time."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from stoney_verify import embed_studio_campaigns as campaigns
from stoney_verify import embed_studio_schedules as schedules
from stoney_verify import embed_studio_schedule_runtime as runtime
from stoney_verify.embed_studio_core import new_project
from stoney_verify.embed_studio_ui import StudioAdvancedTools, StudioReviewView, StudioSession


MIGRATION = Path(__file__).resolve().parents[1] / (
    "supabase_fresh/migrations/"
    "202611050001_dankshield_embed_studio_recurring_campaigns.sql"
)


def test_disabled_without_both_flags(monkeypatch):
    monkeypatch.setattr(campaigns, "schedules_enabled",
                        lambda env: env.get("SCHEDULING") == "on")
    assert not campaigns.campaigns_enabled({"SCHEDULING": "on"})
    assert not campaigns.campaigns_enabled({campaigns.FLAG: "on"})
    assert campaigns.campaigns_enabled(
        {"SCHEDULING": "on", campaigns.FLAG: "on"}
    )


def test_daily_and_weekly_have_explicit_timezone_and_future_occurrence():
    now = datetime(2026, 3, 7, 12, tzinfo=timezone.utc)
    daily = campaigns.parse_campaign("daily", "UTC", "16:20", 3, now=now)
    assert daily["frequency"] == "daily"
    assert daily["weekday"] is None
    assert datetime.fromisoformat(daily["next_fire_at"]) > now
    # Mar 8 02:30 is nonexistent. Weekly Sun must skip to Mar 15.
    weekly = campaigns.parse_campaign(
        "weekly Sunday", "America/New_York", "02:30", 2, now=now
    )
    assert weekly["weekday"] == 7
    assert weekly["next_fire_at"] == "2026-03-15T06:30:00+00:00"


@pytest.mark.parametrize(("freq", "zone", "clock", "count"), [
    ("sometimes", "UTC", "12:00", 2),
    ("weekly", "UTC", "12:00", 2),
    ("daily", "Bad/Timezone", "12:00", 2),
    ("daily", "UTC", "24:20", 2),
    ("daily", "UTC", "12:00", 0),
    ("daily", "UTC", "12:00", 366),
    ("daily", "UTC", "12:00", True),
])
def test_invalid_recurrences_fail_closed(freq, zone, clock, count):
    with pytest.raises(schedules.StudioScheduleError):
        campaigns.parse_campaign(freq, zone, clock, count)


def test_campaign_ui_is_opt_in_and_reuses_preview_guard(monkeypatch):
    monkeypatch.setattr(schedules, "schedules_enabled", lambda: True)
    monkeypatch.setattr(campaigns, "campaigns_enabled", lambda: False)
    session = StudioSession(guild_id=123, owner_id=456)
    assert StudioReviewView(session).repeat_post.disabled
    monkeypatch.setattr(campaigns, "campaigns_enabled", lambda: True)
    assert not StudioReviewView(session).repeat_post.disabled
    assert "Recurring Campaigns" in {
        item.label for item in StudioAdvancedTools(session).children
        if hasattr(item, "label")
    }
    session.post_key = "post123"
    assert StudioReviewView(session).repeat_post.disabled
    session.post_key = ""
    session.project["form"] = {
        "title": "Form", "button_label": "Open",
        "questions": [{"label": "Reason", "style": "paragraph", "required": True}],
    }
    assert StudioReviewView(session).repeat_post.disabled


def test_insert_applies_safe_project_and_requires_persistence(monkeypatch):
    monkeypatch.setattr(campaigns, "campaigns_enabled", lambda: True)
    sent = []
    class Table:
        def insert(self, row):
            sent.append(row)
            return self
        def execute(self):
            return SimpleNamespace(data=[{"id": "a", **sent[-1]}])
    monkeypatch.setattr(campaigns, "_client",
                        lambda: SimpleNamespace(table=lambda name: Table()))
    schedule = campaigns.parse_campaign(
        "daily", "UTC", "12:30", 2,
        now=datetime(2026, 11, 5, 10, tzinfo=timezone.utc))
    result = campaigns._enqueue_sync(123, 456, 789, new_project(), schedule)
    assert result["id"] == "a"
    assert sent[0]["project"]["role_actions"] == []
    assert sent[0]["max_occurrences"] == 2
    assert sent[0]["guild_id"] == "123"


def test_claims_require_verified_owned_shards(monkeypatch):
    monkeypatch.setattr(campaigns, "campaigns_enabled", lambda: True)
    calls = []
    class Client:
        def rpc(self, name, args):
            calls.append((name, args))
            return SimpleNamespace(execute=lambda: SimpleNamespace(data=[]))
    monkeypatch.setattr(campaigns, "_client", Client)
    with pytest.raises(schedules.StudioScheduleError, match="shard"):
        asyncio.run(campaigns.claim_due(shard_count=2, owned_shards=(2,)))
    assert asyncio.run(campaigns.claim_due(
        shard_count=4, owned_shards=(1, 3))) == []
    assert calls == [(campaigns.CLAIM_RPC, {
        "p_limit": 4, "p_shard_count": 4, "p_owned_shards": [1, 3],
    })]


def test_claim_finalization_is_bound_to_exact_claim_uuid(monkeypatch):
    monkeypatch.setattr(campaigns, "campaigns_enabled", lambda: True)
    seen = []
    class Table:
        def update(self, value):
            seen.append(("update", value))
            return self
        def eq(self, key, value):
            seen.append((key, value))
            return self
        def execute(self):
            return SimpleNamespace(data=[{"id": "a"}])
    monkeypatch.setattr(campaigns, "_client",
                        lambda: SimpleNamespace(table=lambda name: Table()))
    assert asyncio.run(campaigns.finish("a", "claim-42", message_id=444))
    assert ("last_claim_id", "claim-42") in seen
    assert ("last_status", "claimed") in seen
    assert seen[0] == ("update", {"last_status": "sent", "last_message_id": "444"})


def test_recurring_sender_is_the_same_guarded_one_shot_dispatch():
    import inspect
    source = inspect.getsource(runtime._loop)
    assert "_dispatch(bot, job)" in source
    assert "campaigns.claim_due" in source
    assert "campaigns.finish" in source
    assert "last_claim_id" in source


def test_schema_is_unique_and_private():
    text = MIGRATION.read_text(encoding="utf-8").lower()
    assert "dank_embed_studio_claim_campaigns" in text
    assert "for update skip locked" in text
    assert "row level security" in text
    assert "revoke all on public.dank_embed_studio_campaigns from public,anon,authenticated" in text
    assert "to service_role" in text
    assert "dank_embed_studio_campaign_quota" in text
    assert "for offset_days in 0..15 loop" in text
    filenames = [p.name.split("_", 1)[0] for p in MIGRATION.parent.glob("*.sql")]
    assert len(filenames) == len(set(filenames))
