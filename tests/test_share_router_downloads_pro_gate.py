"""Phase 3 Slice 6: Share Router downloads Pro gates (spec §2.4).

Two gate points, both through services.membership_gate.premium_decision:

- configure-time, centrally in share_router_runtime.configure_guild_download
  (client injected by the interaction callers), with the
  downloads_tier_checked_at marker written for non-grandfathered setups
  saved while membership is configured;
- submission-time in share_router_download_queue.enqueue_download_request,
  where a markerless active setup is grandfathered (pre-arming) and a
  marker-carrying free guild is denied before any queue row is written.

The worker (_process_job) is deliberately not gated: queued jobs still
deliver after a coverage lapse.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

import stoney_verify.guild_context as guild_context
from stoney_verify import share_router_download_queue as queue
from stoney_verify import share_router_runtime as runtime
from stoney_verify.guild_config import GuildRuntimeConfig
from stoney_verify.guild_context import build_guild_context
from stoney_verify.services import membership

GUILD_ID = 400
CHANNEL_ID = 500
DEST_ID = 900
MEMBER_ID = 200
MESSAGE_ID = 600
CLIENT = object()
MARKER = "2026-11-18T12:00:00+00:00"
VALID_URL = "https://www.facebook.com/reel/1591316522498163"
PROBLEM_PRO = "Downloads are a Dank Shield Pro feature for this server."


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "DANK_SHARE_DOWNLOAD_QUEUE_DB", str(tmp_path / "private" / "queue.sqlite3"),
    )
    monkeypatch.setattr(runtime, "ROUTES_FILE", tmp_path / "routes.json")
    monkeypatch.setattr(runtime, "_ROUTE_STATE_CACHE", None)
    monkeypatch.setattr(queue, "_WORKER_BOT", None)
    monkeypatch.delenv(membership.PRO_MONTHLY_SKU_ENV, raising=False)
    monkeypatch.delenv(membership.PRO_YEARLY_SKU_ENV, raising=False)
    yield
    for task in queue._WORKERS:
        if not task.done():
            task.cancel()
    queue._WORKERS = []
    queue._WORKER_BOT = None


def _configure_membership(monkeypatch, status):
    """Configured membership with a stubbed status + ready guild context."""
    monkeypatch.setenv(membership.PRO_MONTHLY_SKU_ENV, "111222333")

    async def resolve(_guild_id, *, client=None):
        return status

    monkeypatch.setattr(membership, "guild_pro_status", resolve)

    async def fake_context(guild_id, *, refresh=False):
        config = {
            "guild_id": str(guild_id),
            "source": "supabase:guild_configs",
            "ticket_category_id": "111",
            "staff_role_id": "222",
            "verify_channel_id": "333",
            "unverified_role_id": "444",
            "verified_role_id": "555",
            "modlog_channel_id": "666",
            "use_env_fallbacks": False,
            "allow_runtime_discovery": True,
        }
        return build_guild_context(str(guild_id), GuildRuntimeConfig(config))

    monkeypatch.setattr(guild_context, "get_guild_context", fake_context)


def _write_routes(monkeypatch, data):
    runtime.ROUTES_FILE.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(runtime, "_ROUTE_STATE_CACHE", None)


def _seed_bucket(monkeypatch, bucket):
    _write_routes(monkeypatch, {str(GUILD_ID): bucket})


def _read_bucket():
    if not runtime.ROUTES_FILE.exists():
        return {}
    data = json.loads(runtime.ROUTES_FILE.read_text(encoding="utf-8"))
    return data.get(str(GUILD_ID), {})


def _active_bucket(**overrides):
    bucket = {
        "download_channel_id": str(CHANNEL_ID),
        "download_delivery_mode": "dm",
        "download_destination_channel_id": "",
    }
    bucket.update(overrides)
    return bucket


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Marker read surface
# ---------------------------------------------------------------------------


def test_tier_state_reads_activity_and_marker(monkeypatch):
    async def scenario():
        assert await runtime.guild_download_tier_state(GUILD_ID) == (False, "")
        _seed_bucket(monkeypatch, _active_bucket())
        assert await runtime.guild_download_tier_state(GUILD_ID) == (True, "")
        _seed_bucket(monkeypatch, _active_bucket(
            **{runtime.DOWNLOADS_TIER_MARKER_KEY: MARKER},
        ))
        assert await runtime.guild_download_tier_state(GUILD_ID) == (True, MARKER)
        # Paused setups are not "already downloading", marker still read.
        _seed_bucket(monkeypatch, _active_bucket(
            download_delivery_mode="off",
            **{runtime.DOWNLOADS_TIER_MARKER_KEY: MARKER},
        ))
        assert await runtime.guild_download_tier_state(GUILD_ID) == (False, MARKER)
        # Channel mode without a destination resolves to off, like delivery.
        _seed_bucket(monkeypatch, _active_bucket(
            download_delivery_mode="channel",
            **{runtime.DOWNLOADS_TIER_MARKER_KEY: MARKER},
        ))
        assert await runtime.guild_download_tier_state(GUILD_ID) == (False, MARKER)

    run(scenario())


# ---------------------------------------------------------------------------
# Configure-time gate
# ---------------------------------------------------------------------------


def test_configure_unconfigured_saves_without_marker():
    async def scenario():
        await runtime.configure_guild_download(
            GUILD_ID, channel_id=CHANNEL_ID, mode="dm", client=CLIENT,
        )
        assert await runtime.guild_download_delivery(GUILD_ID) == ("dm", 0)
        assert runtime.DOWNLOADS_TIER_MARKER_KEY not in _read_bucket()

    run(scenario())


def test_configure_pro_fresh_setup_saves_and_writes_marker(monkeypatch):
    _configure_membership(monkeypatch, membership.STATUS_PRO)

    async def scenario():
        await runtime.configure_guild_download(
            GUILD_ID, channel_id=CHANNEL_ID, mode="dm", client=CLIENT,
        )
        assert await runtime.guild_download_delivery(GUILD_ID) == ("dm", 0)
        marker = _read_bucket()[runtime.DOWNLOADS_TIER_MARKER_KEY]
        assert datetime.fromisoformat(marker) is not None
        active, read_marker = await runtime.guild_download_tier_state(GUILD_ID)
        assert active is True and read_marker == marker

    run(scenario())


def test_configure_free_fresh_setup_denied_and_writes_nothing(monkeypatch):
    _configure_membership(monkeypatch, membership.STATUS_FREE)

    async def scenario():
        with pytest.raises(runtime.DownloadsProRequiredError) as excinfo:
            await runtime.configure_guild_download(
                GUILD_ID, channel_id=CHANNEL_ID, mode="dm", client=CLIENT,
            )
        decision = excinfo.value.decision
        assert decision.denied is True
        assert decision.premium_feature == "share_router_downloads"
        # Denied before any write: no route file, no bucket, no marker.
        assert not runtime.ROUTES_FILE.exists()
        assert await runtime.guild_download_delivery(GUILD_ID) == ("off", 0)

    run(scenario())


def test_configure_free_grandfathered_setup_saves_and_stays_markerless(monkeypatch):
    _seed_bucket(monkeypatch, _active_bucket())
    _configure_membership(monkeypatch, membership.STATUS_FREE)

    async def scenario():
        await runtime.configure_guild_download(
            GUILD_ID, channel_id=CHANNEL_ID, mode="channel",
            destination_id=DEST_ID, client=CLIENT,
        )
        assert await runtime.guild_download_delivery(GUILD_ID) == ("channel", DEST_ID)
        # A pre-arming setup keeps its markerless (grandfathered) identity
        # even when re-saved under the tier regime.
        assert runtime.DOWNLOADS_TIER_MARKER_KEY not in _read_bucket()

    run(scenario())


def test_configure_free_marker_carrying_active_setup_may_resave(monkeypatch):
    # Configure-time grandfather evidence is the active setup itself
    # (spec §2.4); the marker is enforced at submission time instead.
    _seed_bucket(monkeypatch, _active_bucket(
        **{runtime.DOWNLOADS_TIER_MARKER_KEY: MARKER},
    ))
    _configure_membership(monkeypatch, membership.STATUS_FREE)

    async def scenario():
        await runtime.configure_guild_download(
            GUILD_ID, channel_id=CHANNEL_ID, mode="both",
            destination_id=DEST_ID, client=CLIENT,
        )
        assert await runtime.guild_download_delivery(GUILD_ID) == ("both", DEST_ID)
        assert _read_bucket()[runtime.DOWNLOADS_TIER_MARKER_KEY] == MARKER

    run(scenario())


def test_configure_pause_is_never_gated(monkeypatch):
    _configure_membership(monkeypatch, membership.STATUS_FREE)

    async def scenario():
        await runtime.configure_guild_download(
            GUILD_ID, channel_id=CHANNEL_ID, mode="off", client=CLIENT,
        )
        assert await runtime.guild_download_channel_id(GUILD_ID) == CHANNEL_ID
        assert await runtime.guild_download_delivery(GUILD_ID) == ("off", 0)
        assert runtime.DOWNLOADS_TIER_MARKER_KEY not in _read_bucket()

    run(scenario())


def test_configure_without_client_keeps_historical_behavior(monkeypatch):
    _configure_membership(monkeypatch, membership.STATUS_FREE)

    async def scenario():
        await runtime.configure_guild_download(
            GUILD_ID, channel_id=CHANNEL_ID, mode="dm",
        )
        assert await runtime.guild_download_delivery(GUILD_ID) == ("dm", 0)
        assert runtime.DOWNLOADS_TIER_MARKER_KEY not in _read_bucket()

    run(scenario())


# ---------------------------------------------------------------------------
# Submission-time gate
# ---------------------------------------------------------------------------


class _Member:
    bot = False
    id = MEMBER_ID


class _Channel:
    id = CHANNEL_ID

    def permissions_for(self, member):
        return SimpleNamespace(view_channel=True, send_messages=True)

    def is_nsfw(self):
        return False


def _message():
    return SimpleNamespace(
        guild=SimpleNamespace(id=GUILD_ID),
        author=_Member(), channel=_Channel(),
        id=MESSAGE_ID, content=VALID_URL, webhook_id=None,
    )


@pytest.fixture()
def _gateway(monkeypatch):
    monkeypatch.setattr(queue.discord, "Member", _Member)
    problems = []

    async def reply(message, content):
        problems.append(content)

    monkeypatch.setattr(queue, "_reply_problem", reply)
    return problems


def test_submission_unconfigured_queues(monkeypatch, _gateway):
    _seed_bucket(monkeypatch, _active_bucket())
    assert run(queue.enqueue_download_request(_message(), client=CLIENT)) is True
    assert queue._snapshot()["queued"] == 1
    assert _gateway == []


def test_submission_pro_marker_carrying_queues(monkeypatch, _gateway):
    _seed_bucket(monkeypatch, _active_bucket(
        **{runtime.DOWNLOADS_TIER_MARKER_KEY: MARKER},
    ))
    _configure_membership(monkeypatch, membership.STATUS_PRO)
    assert run(queue.enqueue_download_request(_message(), client=CLIENT)) is True
    assert queue._snapshot()["queued"] == 1
    assert _gateway == []


def test_submission_free_markerless_active_is_grandfathered(monkeypatch, _gateway):
    _seed_bucket(monkeypatch, _active_bucket())
    _configure_membership(monkeypatch, membership.STATUS_FREE)
    assert run(queue.enqueue_download_request(_message(), client=CLIENT)) is True
    assert queue._snapshot()["queued"] == 1
    assert _gateway == []


def test_submission_free_marker_carrying_denied_before_queue(monkeypatch, _gateway):
    _seed_bucket(monkeypatch, _active_bucket(
        **{runtime.DOWNLOADS_TIER_MARKER_KEY: MARKER},
    ))
    _configure_membership(monkeypatch, membership.STATUS_FREE)
    assert run(queue.enqueue_download_request(_message(), client=CLIENT)) is True
    assert _gateway == [PROBLEM_PRO]
    assert queue._snapshot()["queued"] == 0


def test_submission_without_client_keeps_historical_behavior(monkeypatch, _gateway):
    _seed_bucket(monkeypatch, _active_bucket(
        **{runtime.DOWNLOADS_TIER_MARKER_KEY: MARKER},
    ))
    _configure_membership(monkeypatch, membership.STATUS_FREE)
    # No client passed and no worker bot registered: entitlement cannot be
    # resolved, so the gate is skipped (production always passes one).
    assert run(queue.enqueue_download_request(_message())) is True
    assert queue._snapshot()["queued"] == 1
    assert _gateway == []


def test_queued_job_still_processed_after_coverage_lapses(monkeypatch):
    # The job was queued while the guild was covered; the worker must not
    # re-check the tier gate at delivery time.
    _seed_bucket(monkeypatch, _active_bucket(
        **{runtime.DOWNLOADS_TIER_MARKER_KEY: MARKER},
    ))
    _configure_membership(monkeypatch, membership.STATUS_FREE)
    assert queue._submit(GUILD_ID, CHANNEL_ID, MEMBER_ID, MESSAGE_ID) == "queued"

    member = _Member()
    channel = _Channel()

    async def fetch_message(message_id):
        return SimpleNamespace(
            guild=guild, channel=channel, author=member, id=message_id,
            content=VALID_URL,
        )

    channel.fetch_message = fetch_message
    guild = SimpleNamespace(
        id=GUILD_ID,
        get_channel=lambda channel_id: channel if channel_id == CHANNEL_ID else None,
    )
    bot = SimpleNamespace(
        get_guild=lambda guild_id: guild if guild_id == GUILD_ID else None,
    )
    monkeypatch.setattr(queue.discord, "Member", _Member)
    monkeypatch.setattr(queue.discord, "TextChannel", _Channel)

    delivered = []

    async def process(message):
        delivered.append(message.id)
        return True

    monkeypatch.setattr(queue, "handle_download_message", process)
    run(queue._process_job(bot, queue.DownloadJob(1, GUILD_ID, CHANNEL_ID, MEMBER_ID, MESSAGE_ID)))
    assert delivered == [MESSAGE_ID]
