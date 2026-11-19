"""Activity & Cleanup panel: plain-language copy + one-member activity lookup.

Felix's complaint (2026-11-18): the panel was "a hot mess filled with a
bunch of words that don't explain shit", and its whole point — "find any
activity made by any user" — was impossible because the panel only ever
listed members who went quiet. These tests pin the rewrite:

* the embed and buttons say what they do in plain words (kick is called
  kick; the old jargon is gone);
* the new "Look up one member" picker routes ANY picked member — active
  included — into the same stored-history read the Intelligence panel
  uses (``_show_stored_member_history``), with a result view that leads
  back to Activity & Cleanup;
* the cleanup picker and scan buttons keep their original routing.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord

from stoney_verify.commands_ext import member_command_center as center

ROOT = Path(__file__).resolve().parents[1]
CENTER_SRC = (
    ROOT / "stoney_verify" / "commands_ext" / "member_command_center.py"
).read_text(encoding="utf-8")
SETUP_CENTER_SRC = (
    ROOT / "stoney_verify" / "members_cleanup_center_services.py"
).read_text(encoding="utf-8")

OLD_JARGON = (
    "server evidence, not presence",
    "preview-first",
    "confirmation gates",
    "validated cleanup",
    "strictly eligible",
    "custom thresholds",
    "Validate cleanup for one current member",
    "Purge Eligible",
    "Evidence Coverage",
    "Include low confidence",
    "30d Review",
    "90d Review",
    "180d Review",
    "Custom Review",
)


def _embed_text(embed: discord.Embed) -> str:
    parts = [embed.title or "", embed.description or ""]
    for field in embed.fields:
        parts.append(field.name)
        parts.append(field.value)
    return "\n".join(parts)


def test_activity_embed_says_what_it_does_in_plain_words() -> None:
    embed = center._activity_embed()
    text = _embed_text(embed)
    assert [field.name for field in embed.fields] == [
        "Look up one member",
        "Find inactive members",
        "Kick members",
    ]
    # The two promises Felix asked for, in words a first-time admin gets.
    assert "not just someone being online" in text
    assert "nobody is kicked until you confirm it" in text
    # Kick is the only cleanup action; the panel must finally say so.
    assert "kick" in text.lower()
    for jargon in OLD_JARGON:
        assert jargon not in text


def test_activity_view_buttons_and_pickers() -> None:
    view = center.ActivityCleanupMenuView(123)
    buttons = [
        item for item in view.children if isinstance(item, discord.ui.Button)
    ]
    selects = [
        item for item in view.children if isinstance(item, discord.ui.UserSelect)
    ]
    assert {button.label for button in buttons} == {
        "30d Inactive",
        "90d Inactive",
        "180d Inactive",
        "Custom Days",
        "Last Review",
        "Cleanup Queue",
        "Kick Everyone Eligible",
        "Tracking Check",
        "Command Center",
    }
    # Still exactly 9 buttons: the lookup is a picker, not a 10th button.
    assert len(buttons) == 9
    assert {select.placeholder for select in selects} == {
        "Look up one member's activity…",
        "Check one member for kicking…",
    }
    assert {select.custom_id for select in selects} == {
        "dank_members_center:activity_lookup_member",
        "dank_members_center:cleanup_member",
    }
    assert all(0 <= (item.row or 0) <= 4 for item in view.children)


class _FakeResponse:
    def __init__(self) -> None:
        self.done = False
        self.defer_calls: list[dict] = []

    def is_done(self) -> bool:
        return self.done

    async def defer(self, **kwargs) -> None:
        self.defer_calls.append(kwargs)
        self.done = True


def _fake_interaction() -> SimpleNamespace:
    return SimpleNamespace(
        guild=SimpleNamespace(id=777),
        user=SimpleNamespace(id=123),
        response=_FakeResponse(),
    )


def _lookup_select(view: center.ActivityCleanupMenuView) -> center.ActivityLookupMemberSelect:
    return next(
        item for item in view.children
        if isinstance(item, center.ActivityLookupMemberSelect)
    )


def test_lookup_picker_opens_stored_history_for_any_member(monkeypatch) -> None:
    """The picked member reaches the Intelligence panel's stored-history
    read unchanged — there is no inactivity filter on this path, so an
    active member's activity is shown too."""
    resolved_member = SimpleNamespace(id=4242)
    calls: list[dict] = []

    async def fake_resolve(guild, user_id):
        assert guild is not None and user_id == 4242
        return resolved_member, "verified"

    async def fake_history(interaction, user_id, **kwargs):
        calls.append({"user_id": user_id, **kwargs})

    monkeypatch.setattr(center, "_resolve_intelligence_member", fake_resolve)
    monkeypatch.setattr(center, "_show_stored_member_history", fake_history)

    view = center.ActivityCleanupMenuView(123)
    select = _lookup_select(view)
    select._values = [SimpleNamespace(id=4242)]
    interaction = _fake_interaction()
    asyncio.run(select.callback(interaction))

    assert interaction.response.defer_calls == [{"ephemeral": True}]
    assert len(calls) == 1
    call = calls[0]
    assert call["user_id"] == 4242
    assert call["presence"] == "verified"
    assert call["live_member"] is resolved_member
    result_view = call["view"]
    assert isinstance(result_view, center.ActivityLookupResultView)
    assert result_view.member_id == 4242
    assert result_view.owner_id == 123


def test_lookup_picker_still_opens_history_when_member_unresolved(monkeypatch) -> None:
    """A member Discord cannot resolve still gets their stored history
    (same contract as the Intelligence panel), never a silent dead end."""
    calls: list[dict] = []

    async def fake_resolve(guild, user_id):
        return None, "absent"

    async def fake_history(interaction, user_id, **kwargs):
        calls.append({"user_id": user_id, **kwargs})

    monkeypatch.setattr(center, "_resolve_intelligence_member", fake_resolve)
    monkeypatch.setattr(center, "_show_stored_member_history", fake_history)

    view = center.ActivityCleanupMenuView(123)
    select = _lookup_select(view)
    select._values = [SimpleNamespace(id=9999)]
    asyncio.run(select.callback(_fake_interaction()))

    assert len(calls) == 1
    assert calls[0]["presence"] == "absent"
    assert calls[0]["live_member"] is None


def test_lookup_result_view_refresh_forces_a_fresh_read(monkeypatch) -> None:
    calls: list[dict] = []

    async def fake_resolve(guild, user_id):
        return SimpleNamespace(id=user_id), "verified"

    async def fake_history(interaction, user_id, **kwargs):
        calls.append({"user_id": user_id, **kwargs})

    monkeypatch.setattr(center, "_resolve_intelligence_member", fake_resolve)
    monkeypatch.setattr(center, "_show_stored_member_history", fake_history)

    view = center.ActivityLookupResultView(123, member_id=4242)
    labels = {
        item.label for item in view.children if isinstance(item, discord.ui.Button)
    }
    assert labels == {"Refresh Activity", "Activity & Cleanup", "Command Center"}
    interaction = _fake_interaction()
    interaction.response.done = True  # refresh must not defer a second time
    asyncio.run(view.refresh.callback(interaction))

    assert len(calls) == 1
    assert calls[0]["user_id"] == 4242
    assert calls[0]["force_refresh"] is True
    assert calls[0]["view"] is view


def test_scan_buttons_still_route_to_the_activity_scan(monkeypatch) -> None:
    """Renamed buttons, same behaviour: presets still run the shared scan
    with the same day counts, and the kick picker still validates kicks."""
    import stoney_verify.commands_ext.public_members_group as members_group

    scanned: list[int] = []

    async def fake_scan(interaction, *, inactive_days):
        scanned.append(inactive_days)

    monkeypatch.setattr(members_group, "_run_activity_scan", fake_scan)
    view = center.ActivityCleanupMenuView(123)
    interaction = _fake_interaction()
    asyncio.run(view.scan_30.callback(interaction))
    asyncio.run(view.scan_90.callback(interaction))
    asyncio.run(view.scan_180.callback(interaction))
    assert scanned == [30, 90, 180]


def test_custom_scan_modal_uses_plain_words() -> None:
    modal = center.CustomActivityScanModal()
    assert modal.title == "Find inactive members"
    assert modal.low_confidence.label == "Include less-sure members? yes/no"


def test_setup_side_duplicate_uses_the_same_plain_words() -> None:
    """The /dank setup Cleanup + Members Center runs the same scans; its
    copy must not keep the old jargon the main panel dropped."""
    assert 'label="Run 90d Inactive"' in SETUP_CENTER_SRC
    assert 'label="Run 30d Inactive"' in SETUP_CENTER_SRC
    for jargon in (
        "preview-first",
        "confirmation gates",
        "server activity evidence, not presence",
        "Run 90d Review",
        "Run 30d Review",
    ):
        assert jargon not in SETUP_CENTER_SRC


def test_panel_source_has_no_leftover_jargon() -> None:
    # Scoped to the Activity & Cleanup panel family: other panels in the
    # same file (e.g. Operations & Safety) keep their own wording.
    start = CENTER_SRC.index("def _activity_embed")
    end = CENTER_SRC.index("async def _resolve_intelligence_member")
    panel_src = CENTER_SRC[start:end]
    for jargon in OLD_JARGON:
        assert jargon not in panel_src
