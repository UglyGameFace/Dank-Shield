"""Member Activity Review: dead-button regression coverage.

Felix's report (2026-11-19): the Member Activity Review screen "isn't
navigatable and the buttons don't work when pressed."

Root cause, proven against production logs and discord.py 2.7.1's
ViewStore: the review family (MemberActivityReviewView,
LockedUsersReviewView, UserDetailLockView) built every button and select
without a custom_id, so discord.py minted a random per-instance id and the
view existed only as a process-local ViewStore registration with a
15-minute timeout. The Discord message keeps rendering its buttons after
that registration dies — on timeout, and on every bot restart/redeploy —
so each press went nowhere, and the component runtime's stale-menu
recovery could not identify the feature from a random id and fell back to
the generic Control Center, discarding the scan. Production proof: the
reported screen was created 2026-11-19T03:53:03Z, the PR #873 deploy
restarted the bot at 04:13:53Z, and Felix's 04:50:20Z press was recovered
as an unknown private menu (custom_id '207c3a9bdf920931b373ff11249ab5dd').

The fix pinned here:

* every item in the review family carries a stable semantic custom_id in
  the ``dank:members_review:`` namespace;
* the component runtime recognises that namespace and reopens the feature
  (``reopen_member_activity_review``) instead of the Control Center —
  fresh review on the guild's last scan when this process still holds it,
  otherwise the Activity & Cleanup entry panel;
* while a view is alive, every button still dispatches through the real
  ViewStore and produces a response (the healthy path is pinned too).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import interaction_guard as guard
from stoney_verify.commands_ext import member_command_center as center
from stoney_verify.commands_ext import public_members_group as pmg
from stoney_verify.members_new.activity_service import (
    InactiveMemberCandidate,
    InactiveScanOptions,
    InactiveScanReport,
    ScanLockRecord,
)

GUILD_ID = 1514374173517152418
CHANNEL_ID = 1560134803494281246
BOT_ID = 1458458091468226630
USER_ID = 629459300854661120
MSG_ID = 1572822000000000001
# getattr fallback so this module still collects (and fails on assertions)
# against the pre-fix code, where the namespace constant does not exist.
PREFIX = getattr(pmg, "MEMBER_REVIEW_CUSTOM_ID_PREFIX", "dank:members_review:")


# ---------------------------------------------------------------------------
# Fixtures / harness
# ---------------------------------------------------------------------------


def _report(n_candidates: int = 30) -> InactiveScanReport:
    now = datetime(2026, 11, 19, 4, 0, tzinfo=timezone.utc)
    candidates = [
        InactiveMemberCandidate(
            user_id=1000 + i,
            display_name=f"user{i}",
            mention=f"<@{1000 + i}>",
            joined_at=now,
            last_seen_at=now,
            inactivity_days=120,
            activity_score=0,
            confidence="high",
            status="Purge-safe" if i % 2 == 0 else "Needs review",
            removable=(i % 2 == 0),
            protected=False,
            cannot_remove=False,
            reasons=["No tracked activity"],
        )
        for i in range(n_candidates)
    ]
    return InactiveScanReport(
        guild_id=GUILD_ID,
        scanned_at=now,
        options=InactiveScanOptions(),
        total_members_seen=100,
        candidates=candidates,
        protected=[],
        cannot_remove=[],
        data_warnings=[],
    )


class _Harness:
    """A real discord.py ConnectionState/ViewStore with only the network
    edge stubbed. The stubs store views exactly like the real send/edit
    implementations do, so dispatch and ownership behave as in production.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.responses: list[tuple[str, str]] = []
        self.edits: list[dict] = []
        self.current: dict = {}
        self._counter = 0
        self._install_stubs(monkeypatch)

    # -- network edge stubs -------------------------------------------------
    def _install_stubs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        harness = self

        async def fake_defer(self, *, ephemeral=False, thinking=False):
            harness.responses.append((str(self._parent.id), "defer"))
            self._response_type = 5

        async def fake_send_message(self, **kwargs):
            harness.responses.append((str(self._parent.id), "response.send_message"))
            self._response_type = 4
            view = kwargs.get("view")
            if view is not None:
                self._parent._state.store_view(view, MSG_ID + 5000)

        async def fake_edit_message(self, **kwargs):
            harness.responses.append((str(self._parent.id), "response.edit_message"))
            harness.edits.append(kwargs)
            self._response_type = 7
            view = kwargs.get("view")
            if view is not None and view is not discord.utils.MISSING:
                msg = self._parent.message
                self._parent._state.store_view(view, msg.id if msg else None)

        async def fake_send_modal(self, modal, **kwargs):
            harness.responses.append((str(self._parent.id), "response.send_modal"))
            self._response_type = 9
            self._parent._state.store_view(modal)

        async def fake_followup_send(self, **kwargs):
            harness.responses.append((str(harness.current["interaction"].id), "followup.send"))
            view = kwargs.get("view")
            msg = SimpleNamespace(id=MSG_ID + 9000)
            if view is not None:
                harness.current["state"].store_view(view, msg.id)
            return msg

        async def fake_edit_original(self, **kwargs):
            harness.responses.append((str(harness.current["interaction"].id), "edit_original_response"))
            harness.edits.append(kwargs)
            view = kwargs.get("view")
            if view is not None and view is not discord.utils.MISSING:
                harness.current["state"].store_view(view, harness.current["message_id"])
            return SimpleNamespace(id=harness.current["message_id"])

        monkeypatch.setattr(discord.InteractionResponse, "defer", fake_defer)
        monkeypatch.setattr(discord.InteractionResponse, "send_message", fake_send_message)
        monkeypatch.setattr(discord.InteractionResponse, "edit_message", fake_edit_message)
        monkeypatch.setattr(discord.InteractionResponse, "send_modal", fake_send_modal)
        monkeypatch.setattr(discord.Webhook, "send", fake_followup_send)
        monkeypatch.setattr(discord.Interaction, "edit_original_response", fake_edit_original)

    # -- state ---------------------------------------------------------------
    def build_state(self):
        client = discord.Client(intents=discord.Intents.default())
        state = client._connection
        guild = discord.Guild(
            data={
                "id": str(GUILD_ID),
                "name": "The 420 Lobby",
                "owner_id": str(USER_ID),
                "roles": [],
                "channels": [],
                "emojis": [],
                "stickers": [],
                "members": [
                    {
                        "user": {
                            "id": str(BOT_ID),
                            "username": "Dank Shield",
                            "discriminator": "0000",
                            "avatar": None,
                            "bot": True,
                        },
                        "roles": [],
                        "joined_at": "2025-01-01T00:00:00.000000+00:00",
                        "deaf": False,
                        "mute": False,
                        "flags": 0,
                    }
                ],
                "member_count": 174,
                "verification_level": 0,
                "default_message_notifications": 0,
                "explicit_content_filter": 0,
                "mfa_level": 0,
                "nsfw_level": 0,
                "premium_tier": 3,
                "features": [],
                "system_channel_id": None,
                "rules_channel_id": None,
                "public_updates_channel_id": None,
                "afk_channel_id": None,
                "afk_timeout": 60,
                "icon": None,
                "splash": None,
                "banner": None,
                "description": None,
                "vanity_url_code": None,
                "preferred_locale": "en-US",
            },
            state=state,
        )
        state._add_guild(guild)
        state.user = discord.ClientUser(
            data={
                "id": str(BOT_ID),
                "username": "Dank Shield",
                "discriminator": "0000",
                "avatar": None,
                "bot": True,
                "verified": True,
                "email": None,
                "locale": "en-US",
                "mfa_enabled": False,
                "flags": 0,
            },
            state=state,
        )
        self.current["state"] = state
        return state

    # -- interactions ----------------------------------------------------------
    def interaction(self, state, *, custom_id: str, component_type: int, message_id: int, values=None):
        self._counter += 1
        data = {
            "id": str(1700000000000000000 + self._counter),
            "application_id": str(BOT_ID),
            "type": 3,
            "token": "fake-token",
            "version": 1,
            "channel_id": str(CHANNEL_ID),
            "guild_id": str(GUILD_ID),
            "locale": "en-US",
            "guild_locale": "en-US",
            "attachment_size_limit": 26214400,
            "entitlements": [],
            "member": {
                "user": {
                    "id": str(USER_ID),
                    "username": "UglyGameFace",
                    "discriminator": "0",
                    "avatar": None,
                    "global_name": "Felix",
                },
                "roles": [],
                "permissions": "8",
                "joined_at": "2025-01-01T00:00:00.000000+00:00",
                "deaf": False,
                "mute": False,
                "flags": 0,
            },
            "message": {
                "id": str(message_id),
                "channel_id": str(CHANNEL_ID),
                "author": {
                    "id": str(BOT_ID),
                    "username": "Dank Shield",
                    "discriminator": "0000",
                    "avatar": None,
                    "bot": True,
                },
                "content": "",
                "timestamp": "2026-11-19T04:38:00.000000+00:00",
                "edited_timestamp": None,
                "type": 0,
                "flags": 64,
                "tts": False,
                "mention_everyone": False,
                "mentions": [],
                "mention_roles": [],
                "attachments": [],
                "embeds": [],
                "components": [],
                "pinned": False,
            },
            "data": {"component_type": component_type, "custom_id": custom_id},
        }
        if values is not None:
            data["data"]["values"] = values
        inter = discord.Interaction(data=data, state=state)
        self.current["interaction"] = inter
        self.current["message_id"] = message_id
        return inter

    async def press(self, state, label: str, *, values=None) -> bool:
        """Press the currently rendered control with this label, exactly as
        a user does: read the live view registered for the message, take
        the item's current custom_id, dispatch through the real store."""
        store = state._view_store
        view = store._synced_message_views.get(MSG_ID)
        assert view is not None, "no live view registered for the message"
        item = None
        for child in view.children:
            child_label = getattr(child, "label", None) or getattr(child, "placeholder", None)
            if child_label == label:
                item = child
                break
        assert item is not None, f"control {label!r} not on the current view"
        assert not getattr(item, "disabled", False), f"control {label!r} is disabled"
        before = len(self.responses)
        inter = self.interaction(
            state,
            custom_id=item.custom_id,
            component_type=item.type.value,
            message_id=MSG_ID,
            values=values if values is not None else (["1000"] if item.type.value == 3 else None),
        )
        store.dispatch_view(item.type.value, item.custom_id, inter)
        for _ in range(60):
            await asyncio.sleep(0.02)
            if not getattr(store, "_ViewStore__tasks", set()):
                break
        return len(self.responses) > before


def _stub_data_layer(monkeypatch: pytest.MonkeyPatch, report: InactiveScanReport) -> None:
    async def fake_scan(guild, options):
        return report

    async def fake_locks(guild_id):
        return [], "test"

    async def fake_is_locked(guild_id, user_id):
        return False

    monkeypatch.setattr(pmg, "scan_inactive_members", fake_scan)
    monkeypatch.setattr(pmg, "get_scan_lock_records", fake_locks)
    monkeypatch.setattr(pmg, "is_scan_user_locked", fake_is_locked)


# ---------------------------------------------------------------------------
# Stable, identifiable component ids
# ---------------------------------------------------------------------------


def test_review_family_views_use_stable_identifiable_custom_ids() -> None:
    report = _report()
    review = pmg.MemberActivityReviewView(report, page=0)
    ids = [str(getattr(item, "custom_id", "") or "") for item in review.children]
    assert all(cid.startswith(PREFIX) for cid in ids), ids
    assert len(ids) == len(set(ids)), ids
    assert set(ids) == {
        f"{PREFIX}select",
        f"{PREFIX}prev",
        f"{PREFIX}next",
        f"{PREFIX}refresh",
        f"{PREFIX}data",
        f"{PREFIX}safety",
        f"{PREFIX}ids",
        f"{PREFIX}locked",
        f"{PREFIX}p30",
        f"{PREFIX}p90",
        f"{PREFIX}p180",
        f"{PREFIX}notice_review",
        f"{PREFIX}notice_purge",
        f"{PREFIX}notice_manual",
        f"{PREFIX}notice_results",
    }

    candidate = report.candidates[0]
    detail = pmg.UserDetailLockView(candidate, locked=False)
    detail_ids = [str(getattr(item, "custom_id", "") or "") for item in detail.children]
    assert detail_ids == [f"{PREFIX}detail_lock", f"{PREFIX}detail_notice"]

    locked = pmg.LockedUsersReviewView(
        [ScanLockRecord(guild_id=GUILD_ID, user_id=1000)],
        "test",
        guild=None,
        scan_report=report,
        page=0,
    )
    locked_ids = [str(getattr(item, "custom_id", "") or "") for item in locked.children]
    assert all(cid.startswith(PREFIX) for cid in locked_ids), locked_ids
    assert f"{PREFIX}locked_select" in locked_ids
    assert f"{PREFIX}locked_back" in locked_ids


def test_review_ids_survive_item_rebuilds() -> None:
    """Pagination/rescan rebuilds the items in place; the ids a user sees
    must stay the ids the store holds (random ids broke exactly here)."""
    view = pmg.MemberActivityReviewView(_report(), page=0)
    before = [item.custom_id for item in view.children]
    view.page = 1
    view._rebuild_items()
    after = [item.custom_id for item in view.children]
    assert before == after


# ---------------------------------------------------------------------------
# Plain-language copy (same standard as the PR #873 panel rewrite)
# ---------------------------------------------------------------------------


def test_report_embed_says_what_it_does_in_plain_words() -> None:
    embed = pmg._build_report_embed(_report(), page=0)
    text = "\n".join(
        [embed.title or "", embed.description or ""]
        + [f"{field.name}\n{field.value}" for field in embed.fields]
        + [(embed.footer.text or "")]
    )
    for jargon in (
        "Reviews all current member types",
        "explicit safety and evidence checks",
        "Post-verification activity only",
        "lock/skip after review, manage locked users, or rescan",
    ):
        assert jargon not in text
    assert "nobody is kicked or messaged from this screen" in text
    assert "not just someone being online" in text
    assert "gone quiet" in text


# ---------------------------------------------------------------------------
# Live dispatch: every control answers while the view is registered
# ---------------------------------------------------------------------------


def test_every_review_control_dispatches_and_responds(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _report()
    _stub_data_layer(monkeypatch, report)
    harness = _Harness(monkeypatch)

    async def run() -> None:
        state = harness.build_state()
        view = pmg.MemberActivityReviewView(report, page=0)
        # Exactly what the followup send does with the view in production.
        state.store_view(view, MSG_ID)

        for label in (
            "Select a user to inspect manually",
            "Data Notes",
            "Safety",
            "Page IDs",
            "Next",
            "Previous",
            "Notice Review",
            "Notice Purge-Safe",
            "Notice Manual",
            "Notice Results",
            "Locked Users",
            # Now on the locked-users screen:
            "Refresh",
            "Back to Scan",
            # Back on the review screen; rescan paths swap in a fresh view:
            "Refresh",
            "30d",
            "90d",
            "180d",
        ):
            assert await harness.press(state, label), f"press on {label!r} produced no response"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# The reported failure: the view dies, a press must reopen the feature
# ---------------------------------------------------------------------------


def _arm_recovery(monkeypatch: pytest.MonkeyPatch, harness: _Harness, report):
    """Patch the generic fallbacks so a test fails if recovery uses them,
    and control whether this process still holds the guild's last scan."""
    fallback_calls: list[str] = []

    async def fake_home(interaction, **kwargs):
        fallback_calls.append("home")

    monkeypatch.setattr(
        "stoney_verify.commands_ext.public_command_surface_v2.replace_with_compact_dank_home",
        fake_home,
    )
    return fallback_calls


def test_stale_press_reopens_fresh_review_when_last_scan_survives(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _report()
    _stub_data_layer(monkeypatch, report)
    monkeypatch.setattr(pmg, "get_last_scan", lambda guild_id: report)
    harness = _Harness(monkeypatch)
    fallback_calls = _arm_recovery(monkeypatch, harness, report)

    async def run() -> None:
        state = harness.build_state()
        view = pmg.MemberActivityReviewView(report, page=0)
        state.store_view(view, MSG_ID)
        # The reported failure: timeout/restart drops the registration while
        # Discord keeps rendering the buttons.
        state._view_store.remove_view(view)

        fake_bot = SimpleNamespace(_connection=state)
        inter = harness.interaction(
            state,
            custom_id=f"{PREFIX}refresh",
            component_type=2,
            message_id=MSG_ID,
        )
        assert guard._view_store_component_owner_state(fake_bot, inter) is False
        recovered = await guard._recover_unowned_private_component(fake_bot, inter)
        assert recovered is True
        assert fallback_calls == [], "recovery fell back to the generic Control Center"

        edits = [e for e in harness.edits if e.get("view") is not None]
        assert edits, "recovery did not replace the stale message"
        new_view = edits[-1]["view"]
        assert isinstance(new_view, pmg.MemberActivityReviewView)
        assert "stale action was not executed" in str(edits[-1].get("content") or "")

        # The reopened screen is alive: its buttons answer presses.
        assert await harness.press(state, "Data Notes")

    asyncio.run(run())


def test_stale_press_reopens_activity_panel_when_scan_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _report()
    _stub_data_layer(monkeypatch, report)
    monkeypatch.setattr(pmg, "get_last_scan", lambda guild_id: None)
    harness = _Harness(monkeypatch)
    fallback_calls = _arm_recovery(monkeypatch, harness, report)

    async def run() -> None:
        state = harness.build_state()
        view = pmg.MemberActivityReviewView(report, page=0)
        state.store_view(view, MSG_ID)
        state._view_store.remove_view(view)

        fake_bot = SimpleNamespace(_connection=state)
        inter = harness.interaction(
            state,
            custom_id=f"{PREFIX}data",
            component_type=2,
            message_id=MSG_ID,
        )
        recovered = await guard._recover_unowned_private_component(fake_bot, inter)
        assert recovered is True
        assert fallback_calls == [], "recovery fell back to the generic Control Center"

        edits = [e for e in harness.edits if e.get("view") is not None]
        assert edits, "recovery did not replace the stale message"
        assert isinstance(edits[-1]["view"], center.ActivityCleanupMenuView)
        assert "stale action was not executed" in str(edits[-1].get("content") or "")

    asyncio.run(run())
