"""Shared picker labels for Post Builder published posts.

Felix's 2026-11-19 screenshot: the Sticky Posts picker offered
"Not sticky • #1534800308876742698 • me…" twice, pixel-identical, with
no title, no channel name, and no date — he could not tell which post
was his own announcement. These tests pin the shared builder that now
labels every published-post picker (Sticky, Edit Posted, Polls, Live
Text): identity first, channel name + posted time in the description,
and no two options in one picker ever rendering identically.
"""
from __future__ import annotations

from datetime import datetime, timezone

from stoney_verify.embed_studio_post_labels import (
    FetchedPostInfo,
    build_published_post_choices,
    format_posted_at,
    post_identity,
    snowflake_posted_at,
)

# Felix's three real tracked posts (The 420 Lobby, read live 2026-11-19).
GHOST_KEY = "1534800308876742698:1572858778448298115"      # message deleted
ANNOUNCE_KEY = "1534800308876742698:1572875731057385554"  # the announcement
PORTS_KEY = "1540598389001953340:1563335607403675669"     # sticky ports post
CHANNEL_NAMES = {1534800308876742698: "minecraft-chat", 1540598389001953340: "server-ports"}


def _record(message_id, channel_id=99, **extra):
    row = {
        "channel_id": str(channel_id), "message_id": str(message_id),
        "by": "10", "role_token": "", "role_mode": "buttons",
        "role_actions": [], "form": None, "form_token": "",
    }
    row.update(extra)
    return row


def _snowflake(year, month, day, hour, minute, second=0, seq=0):
    ts_ms = int(datetime(year, month, day, hour, minute, second,
                         tzinfo=timezone.utc).timestamp() * 1000)
    return ((ts_ms - 1420070400000) << 22) | seq


def test_snowflake_time_and_format():
    posted = snowflake_posted_at(1572875731057385554)
    assert posted == datetime(2026, 11, 19, 7, 26, 36, 902000, tzinfo=timezone.utc)
    assert format_posted_at(posted) == "Nov 19, 7:26 AM UTC"
    assert snowflake_posted_at("junk") is None
    assert format_posted_at(None) == "unknown time"


def test_identity_from_embed_title_and_plain_first_line():
    item = _record(100)
    assert post_identity(item, fetched=FetchedPostInfo(
        exists=True, title="⛏️ THE 420 LOBBY IS BACK!", first_line="ignored")) \
        == "⛏️ THE 420 LOBBY IS BACK!"
    assert post_identity(item, fetched=FetchedPostInfo(
        exists=True, first_line="📢 IT'S OFFICIAL: WE'RE BACK!")) \
        == "📢 IT'S OFFICIAL: WE'RE BACK!"


def test_identity_from_stored_source_and_poll_question():
    project = {"content": "", "embeds": [{"title": "Stored Title"}], "links": []}
    assert post_identity(_record(100, repost_source=project)) == "Stored Title"
    poll_item = _record(100, poll={
        "question": "What should we play?", "options": ["A", "B"],
        "votes": {}, "state": "active",
    })
    assert post_identity(poll_item) == "What should we play?"


def test_identity_empty_post_fallback():
    assert post_identity(_record(100)) == "Untitled post"
    choices = build_published_post_choices([("99:100", _record(100))])
    assert choices[0].label == "Untitled post"


def test_unresolved_channel_falls_back_to_id_tail():
    choices = build_published_post_choices(
        [("99:100", _record(100))], channel_names={})
    assert choices[0].description.startswith("#channel-2698") or \
        "#channel-" in choices[0].description


def test_duplicate_titles_get_time_in_label_and_unique_pairs():
    first = _record(_snowflake(2026, 11, 19, 7, 26), channel_id=1534800308876742698)
    second = _record(_snowflake(2026, 11, 19, 9, 41), channel_id=1534800308876742698)
    fetched = {
        "a": FetchedPostInfo(exists=True, title="Server Night"),
        "b": FetchedPostInfo(exists=True, title="Server Night"),
    }
    choices = build_published_post_choices(
        [("a", first), ("b", second)],
        channel_names=CHANNEL_NAMES, fetched=fetched)
    pairs = {(c.label, c.description) for c in choices}
    assert len(pairs) == 2
    assert all("Server Night" in c.label for c in choices)
    assert any("7:26 AM" in c.label for c in choices)
    assert any("9:41 AM" in c.label for c in choices)


def test_duplicate_titles_same_minute_get_message_tail():
    first = _record(_snowflake(2026, 11, 19, 7, 26, 5, seq=1), channel_id=1534800308876742698)
    second = _record(_snowflake(2026, 11, 19, 7, 26, 45, seq=2), channel_id=1534800308876742698)
    fetched = {
        "a": FetchedPostInfo(exists=True, title="Same Title"),
        "b": FetchedPostInfo(exists=True, title="Same Title"),
    }
    choices = build_published_post_choices(
        [("a", first), ("b", second)],
        channel_names=CHANNEL_NAMES, fetched=fetched)
    pairs = {(c.label, c.description) for c in choices}
    assert len(pairs) == 2


def test_sticky_prefix_preserved_and_not_doubled():
    item = _record(100, sticky={"enabled": True, "last_sent_at": None})
    choices = build_published_post_choices(
        [("k", item)], prefix_for=lambda _k, row: "📌")
    assert choices[0].label.startswith("📌")
    titled = _record(100, sticky={"enabled": True, "last_sent_at": None})
    choices = build_published_post_choices(
        [("k", titled)], prefix_for=lambda _k, row: "📌",
        fetched={"k": FetchedPostInfo(exists=True, title="📌 BEDROCK/JAVA PORTS")})
    assert choices[0].label == "📌 BEDROCK/JAVA PORTS"


def test_label_and_description_caps():
    item = _record(100)
    choices = build_published_post_choices(
        [("k", item)], channel_names={100: "x" * 200},
        fetched={"k": FetchedPostInfo(exists=True, title="T" * 300)})
    assert len(choices[0].label) <= 100
    assert len(choices[0].description or "") <= 100


def test_sticky_and_edit_posted_pickers_carry_title_channel_and_time():
    """Picker-level: the real views Felix opens show readable options."""
    from stoney_verify.embed_studio_sticky import _sticky_post_picker_view
    from stoney_verify.embed_studio_ui import StudioSession, _tracked_post_picker_view

    announce_id = _snowflake(2026, 11, 19, 7, 26)
    key = f"1534800308876742698:{announce_id}"
    lib = {"published": {key: _record(
        announce_id, channel_id=1534800308876742698,
        sticky={"enabled": True, "last_sent_at": None})}}
    fetched = {key: FetchedPostInfo(exists=True, title="⛏️ THE 420 LOBBY IS BACK! 🌍🔥")}
    session = StudioSession(guild_id=1514374173517152418, owner_id=10)
    sticky_option = _sticky_post_picker_view(
        session, lib, channel_names=CHANNEL_NAMES, fetched=fetched).options[0]
    assert sticky_option.label.startswith("📌 ⛏️ THE 420 LOBBY IS BACK!")
    tracked_option = _tracked_post_picker_view(
        session, lib, channel_names=CHANNEL_NAMES, fetched=fetched).options[0]
    assert tracked_option.label.startswith("⛏️ THE 420 LOBBY IS BACK!")
    for option in (sticky_option, tracked_option):
        assert "#minecraft-chat" in (option.description or "")
        assert "Nov 19, 7:26 AM UTC" in (option.description or "")
        assert option.value == key


def test_missing_flag_labels_message_deleted_without_fetch():
    item = _record(100, message_missing=True)
    choices = build_published_post_choices([("k", item)])
    assert "message deleted" in choices[0].label
    assert "message deleted" in (choices[0].description or "")


def test_felixs_three_real_records_render_readable():
    """The exact before/after proof for the screenshotted picker."""
    records = [
        (GHOST_KEY, _record(1572858778448298115, channel_id=1534800308876742698)),
        (ANNOUNCE_KEY, _record(1572875731057385554, channel_id=1534800308876742698)),
        (PORTS_KEY, _record(
            1563335607403675669, channel_id=1540598389001953340,
            sticky={"enabled": True, "last_sent_at": None})),
    ]
    fetched = {
        GHOST_KEY: FetchedPostInfo(exists=False),
        ANNOUNCE_KEY: FetchedPostInfo(
            exists=True, title="⛏️ THE 420 LOBBY IS BACK! 🌍🔥",
            first_line="📢 IT'S OFFICIAL: WE'RE BACK! ⛏️🔥"),
        PORTS_KEY: FetchedPostInfo(exists=True, title="📌 BEDROCK/JAVA PORTS"),
    }
    choices = build_published_post_choices(
        records, channel_names=CHANNEL_NAMES, fetched=fetched,
        prefix_for=lambda _k, row: "📌" if (row.get("sticky") or {}).get("enabled") else "",
        extra_description_for=lambda _k, row: (
            "Sticky on" if (row.get("sticky") or {}).get("enabled") else ""),
    )
    by_key = {c.value: c for c in choices}
    ghost = by_key[GHOST_KEY]
    assert ghost.label == "Deleted post — message deleted"
    assert ghost.description == \
        "#minecraft-chat • posted Nov 19, 6:19 AM UTC • message deleted • msg …298115"
    announce = by_key[ANNOUNCE_KEY]
    assert announce.label == "⛏️ THE 420 LOBBY IS BACK! 🌍🔥"
    assert announce.description == \
        "#minecraft-chat • posted Nov 19, 7:26 AM UTC • msg …385554"
    ports = by_key[PORTS_KEY]
    assert ports.label == "📌 BEDROCK/JAVA PORTS"
    assert ports.description == \
        "#server-ports • posted Oct 23, 11:37 PM UTC • Sticky on • msg …675669"
    pairs = {(c.label, c.description) for c in choices}
    assert len(pairs) == 3
