from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import discord
from discord import app_commands

from stoney_verify.command_surface_contract import (
    PUBLIC_GLOBAL_COMMAND_COUNT,
    PUBLIC_GLOBAL_COMMAND_NAMES,
)
from stoney_verify.commands_ext import public_movie_night as movie_ui
from stoney_verify.commands_ext.public_command_surface_v2 import _standalone
from stoney_verify.media_source_registry import CustomMediaSource, MediaSourceRegistry
from stoney_verify.media_source_resolver import (
    MediaSourceSearchOutcome,
    ResolvedMediaVariant,
)
from stoney_verify.movie_night import MovieNightManager
from stoney_verify.navigation_registry import feature_by_key, search_features


def _labels(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(item, "label", "") or "")
        for item in view.children
        if str(getattr(item, "label", "") or "")
    }


def test_movie_is_one_compact_public_doorway() -> None:
    assert PUBLIC_GLOBAL_COMMAND_COUNT == 10
    assert PUBLIC_GLOBAL_COMMAND_NAMES == (
        "dank",
        "captions",
        "mod",
        "movie",
        "role",
        "ticket",
        "tickets",
        "toke",
        "verify",
        "View Dank Profile",
    )

    command = _standalone(
        "movie",
        "Open Movie Night.",
        movie_ui.open_movie_night_command,
    )
    assert isinstance(command, app_commands.Command)
    params = getattr(command, "_params", {})
    assert set(params) == {"magnet", "torrent"}
    assert params["magnet"].type is discord.AppCommandOptionType.string
    assert params["torrent"].type is discord.AppCommandOptionType.attachment
    assert not bool(getattr(params["magnet"], "required", True))
    assert not bool(getattr(params["torrent"], "required", True))


def test_movie_night_hub_and_admin_surfaces_are_progressively_disclosed() -> None:
    hub = movie_ui.MovieNightHubView(1)
    more = movie_ui.MovieNightMoreView(1, None, staff=False)
    staff_more = movie_ui.MovieNightMoreView(1, None, staff=True)
    settings = movie_ui.MovieNightSettingsView(1, adult_content_enabled=False)
    setup = movie_ui.MovieNightSetupView(1)
    sources = movie_ui.MovieNightSourcesView(1)

    assert _labels(hub) == {
        "Start Watch Party",
        "Watch Alone",
        "More",
    }
    more_button = next(
        item
        for item in hub.children
        if isinstance(item, discord.ui.Button) and item.label == "More"
    )
    # Discord rejects punctuation masquerading as a component emoji. Keep More
    # text-only unless it uses a real Unicode/custom emoji accepted by Discord.
    assert more_button.emoji is None
    assert "Cinema Settings" not in _labels(more)
    assert {
        "Notifications",
        "Refresh Cinema",
        "Back to Cinema",
        "Close",
    } <= _labels(more)
    assert "Cinema Settings" in _labels(staff_more)
    assert {
        "Provider Deck",
        "Setup & Diagnostics",
        "Notifications",
        "Adult Content: Off",
        "Session & Lifecycle",
        "Back to Cinema",
        "Close",
    } <= _labels(settings)
    assert {
        "Create / Repair Role",
        "Provider Deck",
        "Test Media Endpoint",
        "Community & Pings",
        "Refresh",
        "Back to Settings",
        "Close",
    } <= _labels(setup)
    assert {
        "Add In-App Provider",
        "Add External-Only Link",
        "Manage Providers",
        "Back to Settings",
        "Close",
    } <= _labels(sources)

    assert len(hub.children) <= 5
    assert len(more.children) <= 7
    assert len(settings.children) <= 7



def test_pass_host_is_only_visible_to_current_public_host(monkeypatch) -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
    )
    manager.join_room(room.room_id, user_id=20)
    monkeypatch.setattr(movie_ui, "get_movie_night_manager", lambda: manager)

    host_labels = _labels(movie_ui.MovieNightMoreView(10, room, staff=False))
    viewer_labels = _labels(movie_ui.MovieNightMoreView(20, room, staff=False))

    assert "Pass Host" in host_labels
    assert "Pass Host" not in viewer_labels

    room.ended = True
    private_room = manager.create_room(
        guild_id=1,
        channel_id=3,
        host_id=10,
        stream_token="",
        mode="private",
        now=102.0,
    )
    private_labels = _labels(movie_ui.MovieNightMoreView(10, private_room, staff=False))
    assert "Pass Host" not in private_labels


def test_host_handoff_choices_only_include_active_non_host_viewers(monkeypatch) -> None:
    manager = MovieNightManager(viewer_ttl_seconds=40)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
    )
    manager.join_room(room.room_id, user_id=20)
    manager.join_room(room.room_id, user_id=30)
    room.viewers[30].last_seen = -1_000_000.0
    monkeypatch.setattr(movie_ui, "get_movie_night_manager", lambda: manager)

    guild = SimpleNamespace(
        get_member=lambda uid: SimpleNamespace(
            display_name={20: "Alex", 30: "Stale Viewer"}.get(uid, str(uid)),
            name={20: "Alex", 30: "Stale Viewer"}.get(uid, str(uid)),
        )
    )
    interaction = SimpleNamespace(guild=guild)
    choices = movie_ui._host_handoff_choices(interaction, room)

    assert [choice.value for choice in choices] == ["20"]
    assert choices[0].label == "Alex"
    assert choices[0].default is False


def test_movie_night_hub_changes_controls_by_room_mode_and_vote_context(monkeypatch) -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
    public_room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
    )
    monkeypatch.setattr(movie_ui, "get_movie_night_manager", lambda: manager)

    public_labels = _labels(movie_ui.MovieNightHubView(10, public_room))
    assert {"Find Movie", "Movie Picks", "Queue", "More"} <= public_labels
    assert "Start Watch Party" not in public_labels
    assert "Watch Alone" not in public_labels
    assert "Yes" not in public_labels
    assert "No" not in public_labels

    manager.join_room(public_room.room_id, user_id=20)
    manager.propose_vote(
        public_room.room_id,
        proposer_id=10,
        action="search",
        payload={"query": "Blade Runner"},
    )
    voting_labels = _labels(movie_ui.MovieNightHubView(10, public_room))
    assert {"Yes", "No"} <= voting_labels

    public_room.ended = True
    private_room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
        mode="private",
        now=102.0,
    )
    private_labels = _labels(movie_ui.MovieNightHubView(10, private_room))
    assert {"Find Movie", "Movie Picks", "Queue", "More"} <= private_labels
    assert "Yes" not in private_labels
    assert "No" not in private_labels


def test_stale_watch_party_member_gets_rejoin_control_and_clear_status(monkeypatch) -> None:
    manager = MovieNightManager(viewer_ttl_seconds=35)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
    )
    room.viewers[10].last_seen = -1_000_000.0
    room.host_last_seen = -1_000_000.0
    monkeypatch.setattr(movie_ui, "get_movie_night_manager", lambda: manager)

    labels = _labels(movie_ui.MovieNightHubView(10, room))
    assert "Rejoin Movie Night" in labels
    assert "Start Watch Party" not in labels

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=10),
        guild=SimpleNamespace(get_member=lambda _uid: None),
    )
    embed = movie_ui._room_embed(interaction, room)
    text = "\n".join(
        [str(embed.description or "")]
        + [f"{field.name}\n{field.value}" for field in embed.fields]
    )
    assert "Active now: **0**" in text
    assert "heartbeat expired after about **35 seconds**" in text
    assert "room, queue, and movie picks were not deleted" in text


def test_private_room_candidate_and_release_controls_drop_voting(monkeypatch) -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
        mode="private",
    )
    candidate = manager.nominate(
        room.room_id,
        user_id=10,
        title="Private Movie",
        auto_vote=False,
    )
    variant = manager.add_variant(
        room.room_id,
        candidate.candidate_id,
        user_id=10,
        source_ref="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
        source_id="source",
        source_label="Source",
        file_size=1000,
        seeds=10,
        leechers=2,
        peers=12,
        auto_vote=False,
    )
    monkeypatch.setattr(movie_ui, "get_movie_night_manager", lambda: manager)

    candidate_labels = _labels(
        movie_ui.MovieCandidateView(10, room.room_id, candidate.candidate_id)
    )
    assert "Vote / Unvote Movie" not in candidate_labels
    assert "Add to Queue" in candidate_labels

    release_labels = _labels(
        movie_ui.MovieReleaseView(
            10,
            room.room_id,
            candidate.candidate_id,
            variant.variant_id,
        )
    )
    assert "Vote / Unvote Release" not in release_labels
    assert "Play This Release" in release_labels
    assert "Add to Queue" in release_labels


def test_private_room_announcement_is_suppressed() -> None:
    manager = MovieNightManager()
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
        mode="private",
    )

    class _Channel:
        async def send(self, **kwargs):
            raise AssertionError(f"private viewing unexpectedly announced: {kwargs}")

    interaction = SimpleNamespace(
        channel=_Channel(),
        user=SimpleNamespace(mention="<@10>"),
    )

    asyncio.run(
        movie_ui._announce_room(
            interaction,
            room,
            role=SimpleNamespace(mention="<@&99>"),
        )
    )


def test_search_vote_pending_response_keeps_dank_cinema_hub(monkeypatch) -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
    )
    manager.join_room(room.room_id, user_id=20)
    room.viewers[10].last_seen = -1_000_000.0
    room.host_last_seen = -1_000_000.0
    assert 10 not in manager.active_viewers(room)
    captured: list[dict] = []

    async def fake_replace(interaction, **kwargs):
        _ = interaction
        captured.append(kwargs)

    monkeypatch.setattr(movie_ui, "get_movie_night_manager", lambda: manager)
    monkeypatch.setattr(movie_ui, "_replace", fake_replace)

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=10),
        guild=None,
        channel=None,
    )
    asyncio.run(
        movie_ui._propose_movie_search_vote(
            interaction,
            room_id=room.room_id,
            query="Blade Runner",
        )
    )

    assert 10 in manager.active_viewers(room)
    assert captured
    payload = captured[-1]
    assert "Search vote opened" in payload["content"]
    assert isinstance(payload["view"], movie_ui.MovieNightHubView)


def test_movie_source_modal_hides_internal_id_and_prefills_edits() -> None:
    add_modal = movie_ui.CustomSourceModal(owner_id=1, baseline={})
    assert len(add_modal.children) == 2
    assert [item.label for item in add_modal.children] == [
        "In-app provider name",
        "HTTPS search API / feed",
    ]

    source = CustomMediaSource(
        source_id="family-library",
        label="Family Library",
        endpoint_url="https://library.example.org/search?q={query}",
        enabled=True,
        added_by=1,
    )
    edit_modal = movie_ui.CustomSourceModal(
        owner_id=1,
        baseline={},
        source=source,
    )
    assert edit_modal.source_id == "family-library"
    assert edit_modal.label_input.default == "Family Library"
    assert edit_modal.endpoint_input.default == source.endpoint_url

    actions = movie_ui.SourceActionView(1, "family-library")
    assert {
        "Edit Provider",
        "Enable Provider",
        "Pause Provider",
        "Remove Provider",
        "Back",
    } <= _labels(actions)


def test_external_only_provider_stays_admin_only() -> None:
    modal = movie_ui.ExternalSearchProviderModal(owner_id=1, baseline={})
    assert [item.label for item in modal.children] == [
        "Provider name (optional)",
        "External browser search URL",
    ]

    candidate = movie_ui.MovieCandidateView(1, "room", "candidate")
    assert "Search Elsewhere" not in _labels(candidate)
    assert not hasattr(movie_ui, "ExternalSearchResultsView")
    assert not hasattr(movie_ui, "_open_external_search_results")


def test_external_search_provider_modal_defers_before_persistence() -> None:
    source = inspect.getsource(movie_ui.ExternalSearchProviderModal.on_submit)
    assert "await interaction.response.defer" in source
    assert source.index("await interaction.response.defer") < source.index(
        "await save_media_source_registry"
    )
    assert "return await _replace(" in source


def test_provider_deck_custom_provider_field_stays_within_discord_limit() -> None:
    registry = MediaSourceRegistry(
        revision=20,
        sources=tuple(
            CustomMediaSource(
                source_id=f"provider-{index}",
                label=("Provider " + str(index) + " " + ("x" * 60))[:80],
                endpoint_url=(
                    "https://catalog.example/search?q={query}&provider="
                    + str(index)
                    + "&padding="
                    + ("x" * 180)
                ),
                provider_type=(
                    movie_ui.PROVIDER_TYPE_EXTERNAL
                    if index % 2
                    else movie_ui.PROVIDER_TYPE_JSON
                ),
            )
            for index in range(20)
        ),
    )
    embed = movie_ui._sources_embed(registry)
    custom = next(
        field for field in embed.fields if str(field.name).startswith("📚 Custom Providers")
    )
    assert len(str(custom.value)) <= 1024
    assert "more provider(s)" in str(custom.value)


def test_cinema_settings_show_adult_content_state() -> None:
    off = movie_ui._settings_embed(adult_content_enabled=False)
    on = movie_ui._settings_embed(adult_content_enabled=True)

    off_text = "\n".join(
        f"{field.name}\n{field.value}" for field in off.fields
    )
    on_text = "\n".join(
        f"{field.name}\n{field.value}" for field in on.fields
    )

    assert "Adult Content:** Off" in off_text
    assert "Adult Content:** On" in on_text
    assert "Direct magnets/.torrent files are not content-classified" in off_text
    assert "Adult Content: Off" in _labels(
        movie_ui.MovieNightSettingsView(1, adult_content_enabled=False)
    )
    assert "Adult Content: On" in _labels(
        movie_ui.MovieNightSettingsView(1, adult_content_enabled=True)
    )


def test_adult_provider_filter_is_default_deny_for_explicit_labels() -> None:
    safe = ResolvedMediaVariant(
        title="Public Domain Movie",
        source_id="safe",
        source_label="Safe",
        source_ref="magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        file_size=1,
        seeds=1,
        leechers=0,
        peers=1,
        metadata={"source_reported": {"category": "Movies"}},
    )
    adult = ResolvedMediaVariant(
        title="Example XXX Release",
        source_id="adult",
        source_label="Adult",
        source_ref="magnet:?xt=urn:btih:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        file_size=1,
        seeds=1,
        leechers=0,
        peers=1,
        metadata={"source_reported": {"category": "XXX"}},
    )
    adult_category = ResolvedMediaVariant(
        title="Opaque Provider Release",
        source_id="adult-category",
        source_label="Adult Category",
        source_ref="magnet:?xt=urn:btih:cccccccccccccccccccccccccccccccccccccccc",
        file_size=1,
        seeds=1,
        leechers=0,
        peers=1,
        metadata={"source_reported": {"category": "Adult"}},
    )
    outcome = MediaSourceSearchOutcome(variants=(safe, adult, adult_category))

    filtered = movie_ui._filter_adult_provider_results(outcome, enabled=False)
    assert filtered.variants == (safe,)
    assert "Filtered 2 explicit adult provider release" in filtered.errors[-1]

    unfiltered = movie_ui._filter_adult_provider_results(outcome, enabled=True)
    assert unfiltered is outcome


def test_movie_provider_page_keeps_search_and_direct_media_simple(monkeypatch) -> None:
    monkeypatch.delenv("DANK_TMDB_READ_TOKEN", raising=False)
    embed = movie_ui._sources_embed(MediaSourceRegistry())
    rendered = "\n".join(
        [str(embed.description or "")]
        + [
            f"{field.name}\n{field.value}"
            for field in embed.fields
        ]
    )

    assert "Regular members only use **Find Movie**" in rendered
    assert "Dank Catalog" in rendered
    assert "Powered by TMDB" in rendered
    assert "Dank Watch" in rendered
    assert "JustWatch via TMDB" in rendered
    assert "Dank Archive" in rendered
    assert "Internet Archive Feature Films" in rendered
    assert "Dank Direct" in rendered
    assert "Magnet links" in rendered
    assert ".torrent files" in rendered
    assert "Dank Provider Lab" in rendered
    assert "Dank Engine" in rendered
    assert "**Add In-App Provider**" in rendered
    assert "**Add External-Only Link**" in rendered
    assert "not** part of normal Find Movie results" in rendered
    assert "same Dank Engine adapter" in rendered
    assert "In-App Provider Contract" in rendered
    assert "RSS/Atom/Torznab" in rendered
    assert "v1/v2 info-hashes" in rendered
    assert "camelCase" in rendered
    assert "Add In-App Provider" in _labels(movie_ui.MovieNightSourcesView(1))
    assert "Add External-Only Link" in _labels(movie_ui.MovieNightSourcesView(1))


def test_candidate_embed_shows_tmdb_watch_availability(monkeypatch) -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
    )
    monkeypatch.setattr(movie_ui, "get_movie_night_manager", lambda: manager)
    candidate = manager.nominate(
        room.room_id,
        user_id=10,
        title="Example Movie",
        metadata={
            "catalog": {
                "catalog_id": "123",
                "title": "Example Movie",
                "year": 2026,
                "overview": "Example overview.",
                "poster_url": "https://image.tmdb.org/t/p/w342/example.jpg",
                "watch": {
                    "region": "US",
                    "free": ["Tubi"],
                    "ads": ["Pluto TV"],
                    "flatrate": ["Plex"],
                    "rent": [],
                    "buy": [],
                    "link": "https://www.themoviedb.org/movie/123/watch",
                    "attribution": "JustWatch via TMDB",
                },
            }
        },
        auto_vote=False,
    )

    embed = movie_ui._candidate_embed(room, candidate)
    fields = {str(field.name): str(field.value) for field in embed.fields}
    where = next(value for name, value in fields.items() if name.startswith("📡 Other legal availability"))
    assert "Tubi" in where
    assert "Pluto TV" in where
    assert "Plex" in where
    assert "JustWatch via TMDB" in where


def test_dank_cinema_branding_is_consistent_across_core_surfaces(monkeypatch) -> None:
    monkeypatch.delenv("DANK_TMDB_READ_TOKEN", raising=False)
    providers = movie_ui._sources_embed(MediaSourceRegistry())
    assert str(providers.title) == "🎞️ Dank Cinema • Provider Deck"
    assert "Dank Cinema • powered by Dank Shield" in str(providers.footer.text)

    empty_room = movie_ui._room_embed(
        SimpleNamespace(guild=None, channel=None),
        None,
    )
    assert str(empty_room.title) == "🍿 Dank Cinema"

    search_modal = movie_ui.MovieSearchModal(owner_id=1, room_id="room")
    assert str(search_modal.title) == "1/3 • Find Movie"


def test_cinema_home_embed_is_simple_and_status_details_are_separate(monkeypatch) -> None:
    empty = movie_ui._room_embed(
        SimpleNamespace(guild=None),
        None,
    )
    rendered_empty = "\n".join(
        [str(empty.description or "")]
        + [f"{field.name}\n{field.value}" for field in empty.fields]
    )
    assert "Choose how you want to watch" in rendered_empty
    assert "Session timing" not in rendered_empty

    manager = MovieNightManager(viewer_ttl_seconds=120)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
    )
    monkeypatch.setattr(movie_ui, "get_movie_night_manager", lambda: manager)
    monkeypatch.setattr(
        movie_ui,
        "get_torrent_manager",
        lambda: SimpleNamespace(idle_ttl_seconds=1800.0),
    )
    interaction = SimpleNamespace(guild=None)
    home = movie_ui._room_embed(interaction, room)
    status = movie_ui._session_status_embed(interaction, room)

    assert "Session timing" not in {str(field.name) for field in home.fields}
    assert "⏱️ Session timing" in {str(field.name) for field in status.fields}


def test_search_release_surfaces_show_find_choose_watch_steps(monkeypatch) -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
    )
    monkeypatch.setattr(movie_ui, "get_movie_night_manager", lambda: manager)
    candidate = manager.nominate(
        room.room_id,
        user_id=10,
        title="Example Movie",
        auto_vote=False,
    )
    variant = manager.add_variant(
        room.room_id,
        candidate.candidate_id,
        user_id=10,
        source_ref="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
        source_id="source",
        source_label="Source",
        file_size=2_000_000_000,
        seeds=100,
        leechers=4,
        peers=104,
        metadata={
            "release_name": {
                "source": "WEB-DL",
                "resolution": "1080p",
                "video_codec": "x265",
            }
        },
        auto_vote=False,
    )

    candidate_embed = movie_ui._candidate_embed(room, candidate)
    release_embed = movie_ui._release_embed(room, candidate, variant)
    choices = movie_ui._release_picker_choices([variant])

    assert str(candidate_embed.title) == "2/3 • Choose Release"
    assert str(release_embed.title) == "2/3 • Release Details"
    assert choices[0].emoji == "⭐"
    assert "Recommended" in choices[0].description
    assert "1080p" in choices[0].label


def test_movie_night_lifecycle_text_explains_distinct_timeouts(monkeypatch) -> None:
    monkeypatch.setattr(
        movie_ui,
        "get_torrent_manager",
        lambda: SimpleNamespace(idle_ttl_seconds=1800.0),
    )

    rendered = movie_ui._movie_night_lifecycle_text()

    assert "15 minutes" in rendered
    assert "6 hours" in rendered
    assert "no inactivity timeout" in rendered
    assert "30 minutes" in rendered
    assert "room stays active" in rendered
    assert "choose the release again" in rendered


def test_movie_night_hub_adds_signed_watch_link_when_media_is_active(monkeypatch) -> None:
    monkeypatch.setenv("DANK_MEDIA_PUBLIC_BASE_URL", "https://media.example.com")
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "movie-secret")
    room = SimpleNamespace(room_id="room-123", stream_token="torrent-token")

    view = movie_ui.MovieNightHubView(123, room)
    links = [
        item
        for item in view.children
        if getattr(item, "style", None) is discord.ButtonStyle.link
    ]
    assert len(links) == 1
    assert links[0].label == "Watch Movie"
    assert str(links[0].url).startswith(
        "https://media.example.com/movie/room-123/watch?"
    )


def test_movie_night_core_buttons_use_movie_component_namespace() -> None:
    views = [
        movie_ui.MovieNightHubView(1),
        movie_ui.MovieCandidateView(1, "room", "candidate"),
        movie_ui.MovieReleaseView(1, "room", "candidate", "variant"),
    ]
    for view in views:
        buttons = [
            item
            for item in view.children
            if isinstance(item, discord.ui.Button)
            and item.style is not discord.ButtonStyle.link
        ]
        assert buttons
        assert all(
            str(button.custom_id or "").startswith("dank:movie:")
            for button in buttons
        )


def test_release_picker_does_not_preselect_first_ranked_release() -> None:
    variants = [
        SimpleNamespace(
            variant_id="first",
            metadata={"release_name": {"source": "BluRay"}},
            source_label="ApiBay",
            source_id="apibay",
            file_size=1_840_000_000,
            swarm_health={
                "seeds": 153,
                "leechers": 6,
                "peers": 159,
                "seed_leech_ratio": 25.5,
                "label": "strong",
            },
        ),
        SimpleNamespace(
            variant_id="second",
            metadata={"release_name": {"source": "BluRay"}},
            source_label="ApiBay",
            source_id="apibay",
            file_size=8_130_000_000,
            swarm_health={
                "seeds": 47,
                "leechers": 4,
                "peers": 51,
                "seed_leech_ratio": 11.75,
                "label": "strong",
            },
        ),
    ]

    choices = movie_ui._release_picker_choices(variants)

    assert [choice.value for choice in choices] == ["first", "second"]
    assert all(choice.default is False for choice in choices)

    picker = movie_ui.DankPickerView(
        author_id=1,
        choices=choices,
        on_pick=lambda interaction, value: None,
        custom_id="dank:test:release-picker",
    )
    select = next(
        item for item in picker.children
        if isinstance(item, discord.ui.Select)
    )
    assert all(option.default is False for option in select.options)


def test_release_picker_first_ranked_value_dispatches_pick_action() -> None:
    picked: list[str] = []

    async def on_pick(interaction, value: str) -> None:
        _ = interaction
        picked.append(value)

    choices = [
        movie_ui.DankChoice(
            label="BluRay • 1.84 GiB",
            value="first",
            description="153 seeds",
            emoji="🎞️",
            default=False,
        ),
        movie_ui.DankChoice(
            label="BluRay • 8.13 GiB",
            value="second",
            description="47 seeds",
            emoji="🎞️",
            default=False,
        ),
    ]
    picker = movie_ui.DankPickerView(
        author_id=1,
        choices=choices,
        on_pick=on_pick,
        custom_id="dank:test:release-picker-dispatch",
    )

    asyncio.run(picker.handle_pick(SimpleNamespace(), "first"))

    assert picked == ["first"]


def test_search_results_group_releases_without_automatic_votes(monkeypatch) -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
    )
    monkeypatch.setattr(movie_ui, "get_movie_night_manager", lambda: manager)

    outcome = MediaSourceSearchOutcome(
        variants=(
            ResolvedMediaVariant(
                title="Example Movie",
                source_id="source-a",
                source_label="Source A",
                source_ref="magnet:?xt=urn:btih:aaa",
                file_size=4_000_000_000,
                seeds=100,
                leechers=10,
                peers=110,
                metadata={"release_name": {"source": "WEB-DL"}},
            ),
            ResolvedMediaVariant(
                title="Example Movie",
                source_id="source-b",
                source_label="Source B",
                source_ref="magnet:?xt=urn:btih:bbb",
                file_size=8_000_000_000,
                seeds=50,
                leechers=5,
                peers=55,
                metadata={"release_name": {"source": "BluRay"}},
            ),
            ResolvedMediaVariant(
                title="Other Movie",
                source_id="source-a",
                source_label="Source A",
                source_ref="magnet:?xt=urn:btih:ccc",
                file_size=3_000_000_000,
                seeds=20,
                leechers=4,
                peers=24,
                metadata={"release_name": {"source": "WEBRip"}},
            ),
        ),
    )

    movies, releases = movie_ui._materialize_search_results(
        room,
        outcome,
        proposer_id=10,
        query="example",
    )
    assert movies == 2
    assert releases == 3
    assert len(room.candidates) == 2

    example = manager.find_candidate_by_title(room.room_id, "Example Movie")
    assert example is not None
    assert example.votes == set()
    assert len(example.variants) == 2
    assert all(variant.votes == set() for variant in example.variants.values())
    ranked = manager.ranked_variants(room.room_id, example.candidate_id)
    assert ranked[0].seeds == 100


def test_catalog_selected_movie_groups_release_style_titles(monkeypatch) -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="",
    )
    monkeypatch.setattr(movie_ui, "get_movie_night_manager", lambda: manager)

    outcome = MediaSourceSearchOutcome(
        variants=(
            ResolvedMediaVariant(
                title="The End of Oak Street 2026 1080p WEB-DL x265",
                source_id="source-a",
                source_label="Source A",
                source_ref="magnet:?xt=urn:btih:aaa",
                file_size=4_000_000_000,
                seeds=100,
                leechers=10,
                peers=110,
                metadata={"release_name": {"source": "WEB-DL"}},
            ),
            ResolvedMediaVariant(
                title="The End of Oak Street 2026 2160p BluRay",
                source_id="source-b",
                source_label="Source B",
                source_ref="magnet:?xt=urn:btih:bbb",
                file_size=8_000_000_000,
                seeds=50,
                leechers=5,
                peers=55,
                metadata={"release_name": {"source": "BluRay"}},
            ),
        ),
    )
    catalog = {
        "catalog_id": "1101383",
        "title": "The End of Oak Street",
        "year": 2026,
    }

    movies, releases = movie_ui._materialize_search_results(
        room,
        outcome,
        proposer_id=10,
        query="The End of Oak Street",
        catalog_metadata=catalog,
    )

    assert movies == 1
    assert releases == 2
    candidate = manager.find_candidate_by_title(
        room.room_id,
        "The End of Oak Street",
    )
    assert candidate is not None
    assert candidate.metadata["catalog"]["catalog_id"] == "1101383"
    assert len(candidate.variants) == 2


def test_catalog_release_match_rejects_conflicting_year() -> None:
    catalog = {"title": "Example Movie", "year": 2026}
    assert movie_ui._release_matches_catalog(
        "Example Movie 2026 1080p WEB-DL",
        catalog,
    )
    assert not movie_ui._release_matches_catalog(
        "Example Movie 2019 1080p WEB-DL",
        catalog,
    )



def test_catalog_release_match_prefers_explicit_tmdb_identity() -> None:
    catalog = {
        "catalog_id": "1101383",
        "title": "The End of Oak Street",
        "year": 2026,
    }
    assert movie_ui._release_matches_catalog(
        "Provider Alternate Title 1080p",
        catalog,
        {"source_reported": {"tmdbId": "1101383"}},
    )
    assert not movie_ui._release_matches_catalog(
        "The End of Oak Street 2026 1080p",
        catalog,
        {"source_reported": {"tmdb_id": "999999"}},
    )



def test_catalog_filter_drops_unrelated_provider_releases() -> None:
    catalog = {
        "catalog_id": "1101383",
        "title": "The End of Oak Street",
        "year": 2026,
    }
    matching = ResolvedMediaVariant(
        title="The End of Oak Street 2026 1080p",
        source_id="a",
        source_label="A",
        source_ref="magnet:?xt=urn:btih:aaa",
        file_size=1,
        seeds=1,
        leechers=0,
        peers=1,
        metadata={},
    )
    unrelated = ResolvedMediaVariant(
        title="Completely Different Movie 2026",
        source_id="b",
        source_label="B",
        source_ref="magnet:?xt=urn:btih:bbb",
        file_size=1,
        seeds=1,
        leechers=0,
        peers=1,
        metadata={},
    )

    filtered = movie_ui._filter_outcome_for_catalog(
        MediaSourceSearchOutcome(variants=(matching, unrelated)),
        catalog,
    )
    assert filtered.variants == (matching,)
    assert "Ignored 1 provider release(s)" in filtered.errors[-1]


def test_catalog_filter_reports_when_provider_results_do_not_match() -> None:
    catalog = {
        "catalog_id": "1101383",
        "title": "The End of Oak Street",
        "year": 2026,
    }
    unrelated = ResolvedMediaVariant(
        title="Completely Different Movie",
        source_id="b",
        source_label="B",
        source_ref="magnet:?xt=urn:btih:bbb",
        file_size=1,
        seeds=1,
        leechers=0,
        peers=1,
        metadata={},
    )

    filtered = movie_ui._filter_outcome_for_catalog(
        MediaSourceSearchOutcome(variants=(unrelated,)),
        catalog,
    )
    assert filtered.variants == ()
    assert "none matched the selected catalog movie" in filtered.errors[-1]


def test_movie_night_is_reachable_from_home_registry_and_normal_search_words() -> None:
    feature = feature_by_key("movie_night")
    assert feature is not None
    assert feature.category == "community"
    assert feature.label == "Movie Night"

    for query in ("movie night", "watch party", "group streaming", "torrent streaming"):
        matches = search_features(query)
        assert matches
        assert matches[0].key == "movie_night"


def test_setup_readiness_blocks_public_url_bound_only_to_loopback(monkeypatch) -> None:
    role = SimpleNamespace(mentionable=True)
    perms = SimpleNamespace(
        view_channel=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        mention_everyone=False,
        administrator=False,
    )
    guild = SimpleNamespace(
        me=SimpleNamespace(
            guild_permissions=SimpleNamespace(
                administrator=False,
                manage_roles=True,
            )
        )
    )

    monkeypatch.setattr(movie_ui, "_movie_role", lambda guild, raw: role)
    monkeypatch.setattr(movie_ui, "_channel_permissions", lambda guild, channel: perms)
    monkeypatch.setattr(movie_ui, "media_public_base_url", lambda: "https://media.example.com")
    monkeypatch.setattr(movie_ui, "media_bind_host", lambda: "127.0.0.1")
    monkeypatch.setattr(movie_ui, "media_bind_port", lambda: 8080)
    monkeypatch.setattr(movie_ui, "media_server_ready", lambda: True)
    monkeypatch.setattr(
        movie_ui.importlib.util,
        "find_spec",
        lambda name: object() if name in {"libtorrent", "av"} else None,
    )
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "test-secret")

    result = movie_ui._setup_readiness(
        guild,
        object(),
        {},
        MediaSourceRegistry(),
    )
    assert not result["launch_ready"]
    assert not result["externally_bound"]
    assert any("DANK_MEDIA_BIND_HOST" in item for item in result["blockers"])


def test_setup_readiness_accepts_complete_public_runtime(monkeypatch) -> None:
    role = SimpleNamespace(mentionable=True)
    perms = SimpleNamespace(
        view_channel=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        mention_everyone=False,
        administrator=False,
    )
    guild = SimpleNamespace(
        me=SimpleNamespace(
            guild_permissions=SimpleNamespace(
                administrator=False,
                manage_roles=True,
            )
        )
    )

    monkeypatch.setattr(movie_ui, "_movie_role", lambda guild, raw: role)
    monkeypatch.setattr(movie_ui, "_channel_permissions", lambda guild, channel: perms)
    monkeypatch.setattr(movie_ui, "media_public_base_url", lambda: "https://media.example.com")
    monkeypatch.setattr(movie_ui, "media_bind_host", lambda: "0.0.0.0")
    monkeypatch.setattr(movie_ui, "media_bind_port", lambda: 8080)
    monkeypatch.setattr(movie_ui, "media_server_ready", lambda: True)
    monkeypatch.setattr(
        movie_ui.importlib.util,
        "find_spec",
        lambda name: object() if name in {"libtorrent", "av"} else None,
    )
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "test-secret")

    result = movie_ui._setup_readiness(
        guild,
        object(),
        {},
        MediaSourceRegistry(),
    )
    assert result["launch_ready"]
    assert result["blockers"] == []
    assert result["externally_bound"]
    assert result["runtime_ready"]
    assert not any("No custom media sources" in item for item in result["warnings"])


def test_private_viewing_does_not_require_notification_role(monkeypatch) -> None:
    perms = SimpleNamespace(
        view_channel=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        mention_everyone=False,
        administrator=False,
    )
    guild = SimpleNamespace(
        me=SimpleNamespace(
            guild_permissions=SimpleNamespace(
                administrator=False,
                manage_roles=False,
            )
        )
    )

    monkeypatch.setattr(movie_ui, "_movie_role", lambda guild, raw: None)
    monkeypatch.setattr(movie_ui, "_channel_permissions", lambda guild, channel: perms)
    monkeypatch.setattr(movie_ui, "media_public_base_url", lambda: "https://media.example.com")
    monkeypatch.setattr(movie_ui, "media_bind_host", lambda: "0.0.0.0")
    monkeypatch.setattr(movie_ui, "media_bind_port", lambda: 8080)
    monkeypatch.setattr(movie_ui, "media_server_ready", lambda: True)
    monkeypatch.setattr(
        movie_ui.importlib.util,
        "find_spec",
        lambda name: object() if name in {"libtorrent", "av"} else None,
    )
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "test-secret")

    public_ready = movie_ui._setup_readiness(
        guild,
        object(),
        {},
        MediaSourceRegistry(),
    )
    private_ready = movie_ui._setup_readiness(
        guild,
        object(),
        {},
        MediaSourceRegistry(),
        require_notification_role=False,
    )

    assert not public_ready["launch_ready"]
    assert private_ready["launch_ready"]
    assert not any("notification role" in item.lower() for item in private_ready["blockers"])
    assert not any("manage roles" in item.lower() for item in private_ready["blockers"])


def test_media_endpoint_check_acknowledges_before_network(monkeypatch) -> None:
    events: list[str] = []

    class FakeResponseState:
        def __init__(self) -> None:
            self.done = False

        def is_done(self) -> bool:
            return self.done

        async def defer(self, *, ephemeral: bool, thinking: bool) -> None:
            assert ephemeral and thinking
            self.done = True
            events.append("defer")

    class FakeHTTPResponse:
        status = 200

        async def __aenter__(self):
            events.append("http-enter")
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self, *, content_type=None):
            _ = content_type
            return {"ok": True, "service": "dank_torrent_media"}

    class FakeSession:
        def __init__(self, *, timeout) -> None:
            _ = timeout
            assert interaction.response.done
            events.append("session")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url: str, *, allow_redirects: bool):
            assert url == "https://media.example.com/health"
            assert not allow_redirects
            return FakeHTTPResponse()

    class FakeInteraction:
        def __init__(self) -> None:
            self.user = SimpleNamespace(id=123)
            self.response = FakeResponseState()
            self.message = None
            self.edits: list[dict] = []

        async def edit_original_response(self, **kwargs):
            self.edits.append(kwargs)

    interaction = FakeInteraction()
    monkeypatch.setattr(movie_ui, "media_public_base_url", lambda: "https://media.example.com")
    monkeypatch.setattr(movie_ui.aiohttp, "ClientSession", FakeSession)

    asyncio.run(movie_ui._test_public_media(interaction))

    assert events[:2] == ["defer", "session"]
    assert "http-enter" in events
    assert interaction.edits
    assert "reachable" in str(interaction.edits[-1].get("content", "")).lower()



def test_passed_end_vote_runs_canonical_session_cleanup(monkeypatch) -> None:
    manager = MovieNightManager(viewer_ttl_seconds=120)
    room = manager.create_room(
        guild_id=1,
        channel_id=2,
        host_id=10,
        stream_token="torrent-token",
        now=100.0,
    )
    manager.join_room(room.room_id, user_id=20, now=100.0)
    manager.join_room(room.room_id, user_id=30, now=100.0)

    vote = manager.propose_vote(
        room.room_id,
        proposer_id=20,
        action="end",
        now=101.0,
    )
    vote = manager.cast_vote(
        room.room_id,
        vote.vote_id,
        user_id=30,
        approve=True,
        now=102.0,
    )
    assert vote.resolved and vote.passed and room.ended

    cleanup_calls: list[str] = []
    replacements: list[dict] = []

    async def fake_cleanup(target):
        cleanup_calls.append(target.room_id)
        target.stream_token = ""
        return SimpleNamespace(cleanup_error="")

    async def fake_replace(interaction, **kwargs):
        _ = interaction
        replacements.append(kwargs)

    monkeypatch.setattr(movie_ui, "get_movie_night_manager", lambda: manager)
    monkeypatch.setattr(movie_ui, "terminate_movie_night_room", fake_cleanup)
    monkeypatch.setattr(movie_ui, "_replace", fake_replace)

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=20),
        guild=None,
        channel=None,
    )
    asyncio.run(movie_ui._execute_passed_vote(interaction, room, vote))

    assert cleanup_calls == [room.room_id]
    assert replacements
    assert "ended" in replacements[-1]["content"].lower()
    assert manager.claim_vote_execution(room.room_id, vote.vote_id) is False



def test_provider_deck_labels_capabilities_not_implementation_jargon() -> None:
    registry = MediaSourceRegistry(
        revision=3,
        sources=(
            CustomMediaSource(
                source_id="torrent-api",
                label="Torrent API",
                endpoint_url="https://api.example.com/search?q={query}",
                provider_type=movie_ui.PROVIDER_TYPE_JSON,
            ),
            CustomMediaSource(
                source_id="browser-only",
                label="Browser Only",
                endpoint_url="https://search.example.com/?q={query}",
                provider_type=movie_ui.PROVIDER_TYPE_EXTERNAL,
            ),
        ),
    )
    embed = movie_ui._sources_embed(registry)
    rendered = "\n".join(
        [str(embed.description or "")]
        + [f"{field.name}\n{field.value}" for field in embed.fields]
    )

    assert "Torrent API** • In-App • structured" in rendered
    assert "Browser Only** • External-only • browser link" in rendered
    assert "Structured JSON" not in rendered
    assert "Search Elsewhere" not in rendered


def test_primary_candidate_controls_never_open_provider_websites() -> None:
    labels = _labels(movie_ui.MovieCandidateView(1, "room", "candidate"))
    assert labels == {
        "Vote / Unvote Movie",
        "Choose Release",
        "Vote to Queue",
        "Back to Results",
    }


def test_search_vote_never_falls_back_to_external_browser_providers() -> None:
    source = inspect.getsource(movie_ui._execute_search_vote)
    assert "_external_provider_links" not in source
    assert "ExternalSearchResultsView" not in source
    assert "external_provider_count" not in source
    assert "External Search Available" not in source