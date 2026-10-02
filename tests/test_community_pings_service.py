from __future__ import annotations

import pytest

from stoney_verify.community_pings_service import (
    CAP_MOVIE_NIGHT_NOTIFY,
    CAP_TOKE_NOTIFY,
    CAP_TOKE_START,
    COMMUNITY_PINGS_KEY,
    MAX_COMMUNITY_GROUPS,
    MAX_COMMUNITY_OPTIONS,
    CommunityPingGroup,
    CommunityPingOption,
    CommunityPingsConfig,
    community_member_lock,
    move_option,
    movie_night_registration_blocker,
    movie_night_role_id,
    parse_community_pings,
    register_movie_night_notification_role,
    role_dependency_labels,
    self_service_kind,
    toke_role_ids,
    upsert_group,
    validate_config,
    validate_member_selection,
    with_option,
    without_group,
    without_option,
)


def _base() -> CommunityPingsConfig:
    return CommunityPingsConfig(
        revision=2,
        groups=(
            CommunityPingGroup(
                key="games",
                label="Games",
                emoji="🎮",
                max_selections=2,
                order=0,
            ),
            CommunityPingGroup(
                key="alerts",
                label="Alerts",
                emoji="🔔",
                max_selections=0,
                order=1,
            ),
        ),
        options=(
            CommunityPingOption(
                key="minecraft",
                role_id=101,
                label="Minecraft",
                emoji="⛏️",
                group_key="games",
                kind="community",
                order=0,
            ),
            CommunityPingOption(
                key="fortnite",
                role_id=102,
                label="Fortnite",
                emoji="🎯",
                group_key="games",
                kind="community",
                order=1,
            ),
            CommunityPingOption(
                key="giveaways",
                role_id=201,
                label="Giveaway Pings",
                emoji="🎁",
                group_key="alerts",
                kind="notification",
                prerequisite_role_id=101,
                order=2,
            ),
        ),
        source="v2",
    )


def test_legacy_stoner_sesh_config_is_normalized_without_mutating_storage() -> None:
    parsed = parse_community_pings(
        {
            "stoner_role_id": "111",
            "sesh_ping_role_id": "222",
            "toke_channel_id": "333",
        }
    )
    assert parsed.source == "legacy"
    assert parsed.revision == 1
    assert [item.role_id for item in parsed.options] == [111, 222]
    assert CAP_TOKE_START in parsed.options[0].capabilities
    assert CAP_TOKE_NOTIFY in parsed.options[1].capabilities
    assert parsed.options[1].prerequisite_role_id == 111


def test_same_legacy_role_can_own_both_toke_capabilities() -> None:
    parsed = parse_community_pings(
        {
            "stoner_role_id": "111",
            "sesh_ping_role_id": "111",
        }
    )
    assert len(parsed.options) == 1
    assert set(parsed.options[0].capabilities) == {CAP_TOKE_START, CAP_TOKE_NOTIFY}
    assert toke_role_ids(parsed) == (111, 111)


def test_present_but_malformed_v2_fails_closed_instead_of_reactivating_legacy() -> None:
    parsed = parse_community_pings(
        {
            COMMUNITY_PINGS_KEY: "corrupt",
            "stoner_role_id": "111",
            "sesh_ping_role_id": "222",
        }
    )

    assert parsed.source == "v2"
    assert parsed.options == ()
    assert toke_role_ids(
        parsed,
        {"stoner_role_id": "111", "sesh_ping_role_id": "222"},
    ) == (0, 0)


def test_v2_parser_is_bounded_and_dedupes_duplicate_roles() -> None:
    raw_options = [
        {
            "key": f"option-{index}",
            "role_id": str(1000 + index),
            "label": f"Option {index}",
            "group_key": "all",
        }
        for index in range(MAX_COMMUNITY_OPTIONS + 10)
    ]
    raw_options.append(
        {
            "key": "duplicate-role",
            "role_id": "1000",
            "label": "Duplicate",
            "group_key": "all",
        }
    )
    parsed = parse_community_pings(
        {
            COMMUNITY_PINGS_KEY: {
                "version": 2,
                "revision": 9,
                "groups": [{"key": "all", "label": "All"}],
                "options": raw_options,
            }
        }
    )
    assert parsed.source == "v2"
    assert parsed.revision == 9
    assert len(parsed.options) == MAX_COMMUNITY_OPTIONS
    assert len({item.role_id for item in parsed.options}) == MAX_COMMUNITY_OPTIONS
    assert validate_config(parsed) == []


def test_adding_option_creates_group_and_bumps_revision() -> None:
    config = CommunityPingsConfig(revision=4, groups=(), options=(), source="v2")
    updated = with_option(
        config,
        CommunityPingOption(
            key="events",
            role_id=55,
            label="Events",
            group_key="events",
            kind="notification",
        ),
    )
    assert updated.revision == 5
    assert updated.options[0].role_id == 55
    assert updated.groups[0].key == "events"


def test_option_identity_conflicts_do_not_silently_replace_existing_mapping() -> None:
    config = _base()

    with pytest.raises(ValueError, match="already uses the key"):
        with_option(
            config,
            CommunityPingOption(
                key="minecraft",
                role_id=999,
                label="Different Role",
                group_key="games",
            ),
        )

    with pytest.raises(ValueError, match="already mapped"):
        with_option(
            config,
            CommunityPingOption(
                key="minecraft-alt",
                role_id=101,
                label="Minecraft Alt",
                group_key="games",
            ),
        )


def test_noop_option_and_group_updates_do_not_bump_revision() -> None:
    config = _base()
    assert with_option(config, config.options[0]) is config
    assert upsert_group(config, config.groups[0]) is config


def test_community_member_lock_is_shared_for_same_guild_member() -> None:
    first = community_member_lock(123, 456)
    second = community_member_lock(123, 456)
    other = community_member_lock(123, 789)

    assert first is second
    assert first is not other


def test_option_and_group_hard_limits_reject_overflow() -> None:
    groups = tuple(
        CommunityPingGroup(key=f"g-{index}", label=f"Group {index}", order=index)
        for index in range(MAX_COMMUNITY_GROUPS)
    )
    options = tuple(
        CommunityPingOption(
            key=f"o-{index}",
            role_id=10000 + index,
            label=f"Option {index}",
            group_key=groups[index % len(groups)].key,
            order=index,
        )
        for index in range(MAX_COMMUNITY_OPTIONS)
    )
    config = CommunityPingsConfig(revision=1, groups=groups, options=options)

    with pytest.raises(ValueError):
        with_option(
            config,
            CommunityPingOption(
                key="overflow",
                role_id=99999,
                label="Overflow",
                group_key=groups[0].key,
            ),
        )

    with pytest.raises(ValueError):
        upsert_group(
            config,
            CommunityPingGroup(key="overflow-group", label="Overflow Group"),
        )


def test_member_selection_requires_configured_prerequisite_option() -> None:
    config = _base()
    error = validate_member_selection(
        config,
        selected_role_ids={201},
        current_role_ids={101, 201},
    )
    assert "requires its configured prerequisite option" in error

    assert (
        validate_member_selection(
            config,
            selected_role_ids={101, 201},
            current_role_ids={101, 201},
        )
        == ""
    )


def test_external_prerequisite_must_exist_on_member() -> None:
    config = CommunityPingsConfig(
        revision=1,
        groups=(CommunityPingGroup(key="alerts", label="Alerts"),),
        options=(
            CommunityPingOption(
                key="vip-alerts",
                role_id=500,
                label="VIP Alerts",
                group_key="alerts",
                prerequisite_role_id=999,
                kind="notification",
            ),
        ),
    )
    assert "prerequisite role" in validate_member_selection(
        config,
        selected_role_ids={500},
        current_role_ids=set(),
    )
    assert validate_member_selection(
        config,
        selected_role_ids={500},
        current_role_ids={999},
    ) == ""


def test_mutual_exclusion_and_group_caps_are_enforced() -> None:
    config = CommunityPingsConfig(
        revision=1,
        groups=(CommunityPingGroup(key="region", label="Region", max_selections=2),),
        options=(
            CommunityPingOption(
                key="east",
                role_id=1,
                label="East",
                group_key="region",
                exclusive_key="coast",
            ),
            CommunityPingOption(
                key="west",
                role_id=2,
                label="West",
                group_key="region",
                exclusive_key="coast",
            ),
            CommunityPingOption(
                key="central",
                role_id=3,
                label="Central",
                group_key="region",
            ),
        ),
    )
    assert "mutually exclusive" in validate_member_selection(
        config,
        selected_role_ids={1, 2},
        current_role_ids=set(),
    )
    assert "at most 2" in validate_member_selection(
        replace_exclusive(config),
        selected_role_ids={1, 2, 3},
        current_role_ids=set(),
    )


def replace_exclusive(config: CommunityPingsConfig) -> CommunityPingsConfig:
    return CommunityPingsConfig(
        revision=config.revision,
        groups=config.groups,
        options=tuple(
            CommunityPingOption(
                **{
                    **item.__dict__,
                    "exclusive_key": "",
                }
            )
            for item in config.options
        ),
    )


def test_non_removable_option_cannot_be_deselected_by_member() -> None:
    config = CommunityPingsConfig(
        revision=1,
        groups=(CommunityPingGroup(key="identity", label="Identity"),),
        options=(
            CommunityPingOption(
                key="member-community",
                role_id=77,
                label="Member Community",
                group_key="identity",
                removable=False,
            ),
        ),
    )
    assert "cannot be removed" in validate_member_selection(
        config,
        selected_role_ids=set(),
        current_role_ids={77},
    )


def test_role_dependency_and_self_service_helpers_cover_generic_options() -> None:
    config = _base()
    assert role_dependency_labels(config, 101) == [
        "Community & Pings · Minecraft",
        "Community & Pings prerequisite · Giveaway Pings",
    ]
    assert self_service_kind(config, 101) == "Community"
    assert self_service_kind(config, 201) == "Notification"
    assert self_service_kind(config, 999) == ""


def test_toke_capabilities_survive_payload_parse_round_trip() -> None:
    config = CommunityPingsConfig(
        revision=5,
        groups=(CommunityPingGroup(key="toke", label="Toke"),),
        options=(
            CommunityPingOption(
                key="stoner",
                role_id=700,
                label="Stoner",
                group_key="toke",
                capabilities=(CAP_TOKE_START, CAP_TOKE_NOTIFY),
            ),
        ),
        source="v2",
    )

    payload = config.to_payload()
    assert payload["options"][0]["capabilities"] == [
        CAP_TOKE_START,
        CAP_TOKE_NOTIFY,
    ]

    reparsed = parse_community_pings({COMMUNITY_PINGS_KEY: payload})
    assert reparsed.options[0].capabilities == (
        CAP_TOKE_START,
        CAP_TOKE_NOTIFY,
    )
    assert toke_role_ids(reparsed) == (700, 700)


def test_toke_capability_parser_repairs_hyphenated_legacy_bug() -> None:
    reparsed = parse_community_pings(
        {
            COMMUNITY_PINGS_KEY: {
                "version": 2,
                "revision": 6,
                "groups": [{"key": "toke", "label": "Toke"}],
                "options": [
                    {
                        "key": "stoner",
                        "role_id": "700",
                        "label": "Stoner",
                        "group_key": "toke",
                        "capabilities": ["toke-start", "toke-notify"],
                    }
                ],
            }
        }
    )

    assert reparsed.options[0].capabilities == (
        CAP_TOKE_START,
        CAP_TOKE_NOTIFY,
    )
    assert toke_role_ids(reparsed) == (700, 700)


def test_toke_capabilities_prefer_v2_and_fall_back_to_legacy_ids() -> None:
    config = CommunityPingsConfig(
        revision=1,
        groups=(CommunityPingGroup(key="toke", label="Toke"),),
        options=(
            CommunityPingOption(
                key="starter",
                role_id=700,
                label="Starter",
                group_key="toke",
                capabilities=(CAP_TOKE_START,),
            ),
            CommunityPingOption(
                key="notify",
                role_id=701,
                label="Notify",
                kind="notification",
                group_key="toke",
                capabilities=(CAP_TOKE_NOTIFY,),
            ),
        ),
    )
    assert toke_role_ids(
        config,
        {"stoner_role_id": "111", "sesh_ping_role_id": "222"},
    ) == (700, 701)

    legacy = CommunityPingsConfig(revision=1, groups=(), options=(), source="legacy")
    assert toke_role_ids(
        legacy,
        {"stoner_role_id": "111", "sesh_ping_role_id": "222"},
    ) == (111, 222)

    migrated = CommunityPingsConfig(revision=3, groups=(), options=(), source="v2")
    assert toke_role_ids(
        migrated,
        {"stoner_role_id": "111", "sesh_ping_role_id": "222"},
    ) == (0, 0)


def test_movie_night_role_registration_is_notification_capability_and_round_trips() -> None:
    config = _base()
    updated = register_movie_night_notification_role(
        config,
        role_id=777,
        label="Movie Night",
    )

    option = next(item for item in updated.options if item.role_id == 777)
    assert option.kind == "notification"
    assert CAP_MOVIE_NIGHT_NOTIFY in option.capabilities
    assert movie_night_role_id(updated) == 777
    assert movie_night_registration_blocker(updated) == ""

    reparsed = parse_community_pings(
        {COMMUNITY_PINGS_KEY: updated.to_payload()}
    )
    assert movie_night_role_id(reparsed) == 777
    reparsed_option = next(item for item in reparsed.options if item.role_id == 777)
    assert CAP_MOVIE_NIGHT_NOTIFY in reparsed_option.capabilities


def test_movie_night_registration_preserves_existing_option_rules_and_toke_caps() -> None:
    config = CommunityPingsConfig(
        revision=3,
        groups=(CommunityPingGroup(key="alerts", label="Alerts"),),
        options=(
            CommunityPingOption(
                key="cinema",
                role_id=555,
                label="Cinema Crew",
                emoji="🍿",
                description="Existing member choice",
                kind="community",
                group_key="alerts",
                prerequisite_role_id=123,
                exclusive_key="events",
                removable=False,
                enabled=False,
                capabilities=(CAP_TOKE_NOTIFY,),
            ),
        ),
        source="v2",
    )

    updated = register_movie_night_notification_role(
        config,
        role_id=555,
    )
    option = updated.options[0]
    assert option.key == "cinema"
    assert option.label == "Cinema Crew"
    assert option.emoji == "🍿"
    assert option.description == "Existing member choice"
    assert option.group_key == "alerts"
    assert option.prerequisite_role_id == 123
    assert option.exclusive_key == "events"
    assert not option.removable
    assert option.enabled
    assert option.kind == "notification"
    assert CAP_TOKE_NOTIFY in option.capabilities
    assert CAP_MOVIE_NIGHT_NOTIFY in option.capabilities


def test_movie_night_registration_is_unique_without_disturbing_other_capabilities() -> None:
    config = CommunityPingsConfig(
        revision=2,
        groups=(CommunityPingGroup(key="alerts", label="Alerts"),),
        options=(
            CommunityPingOption(
                key="old-movie",
                role_id=500,
                label="Old Movie Pings",
                kind="notification",
                group_key="alerts",
                capabilities=(CAP_MOVIE_NIGHT_NOTIFY, CAP_TOKE_NOTIFY),
            ),
            CommunityPingOption(
                key="new-movie",
                role_id=501,
                label="New Movie Pings",
                kind="notification",
                group_key="alerts",
                capabilities=(CAP_TOKE_START,),
            ),
        ),
        source="v2",
    )

    updated = register_movie_night_notification_role(config, role_id=501)
    by_role = {item.role_id: item for item in updated.options}
    assert CAP_MOVIE_NIGHT_NOTIFY not in by_role[500].capabilities
    assert CAP_TOKE_NOTIFY in by_role[500].capabilities
    assert CAP_MOVIE_NIGHT_NOTIFY in by_role[501].capabilities
    assert CAP_TOKE_START in by_role[501].capabilities
    assert movie_night_role_id(updated) == 501


def test_movie_night_role_creation_preflight_blocks_only_when_new_option_cannot_fit() -> None:
    groups = (CommunityPingGroup(key="alerts", label="Alerts"),)
    full = CommunityPingsConfig(
        revision=1,
        groups=groups,
        options=tuple(
            CommunityPingOption(
                key=f"role-{index}",
                role_id=1000 + index,
                label=f"Role {index}",
                group_key="alerts",
            )
            for index in range(MAX_COMMUNITY_OPTIONS)
        ),
        source="v2",
    )
    assert "maximum" in movie_night_registration_blocker(full).lower()

    already_mapped = CommunityPingsConfig(
        revision=1,
        groups=groups,
        options=(
            CommunityPingOption(
                key="movie-night",
                role_id=999,
                label="Movie Night",
                kind="notification",
                group_key="alerts",
                capabilities=(CAP_MOVIE_NIGHT_NOTIFY,),
            ),
        ),
        source="v2",
    )
    assert movie_night_registration_blocker(already_mapped) == ""


def test_remove_and_reorder_bump_revision_only_when_changed() -> None:
    config = _base()
    moved = move_option(config, "fortnite", -1)
    assert moved.revision == config.revision + 1
    assert [item.key for item in moved.options][:2] == ["fortnite", "minecraft"]

    unchanged = move_option(moved, "fortnite", -1)
    assert unchanged is moved

    removed = without_option(moved, "fortnite")
    assert removed.revision == moved.revision + 1
    assert all(item.key != "fortnite" for item in removed.options)

    missing = without_option(removed, "does-not-exist")
    assert missing is removed


def test_empty_group_can_be_deleted_but_in_use_group_is_blocked() -> None:
    config = CommunityPingsConfig(
        revision=7,
        groups=(
            CommunityPingGroup(key="games", label="Games", order=0),
            CommunityPingGroup(key="empty", label="Empty", order=1),
        ),
        options=(
            CommunityPingOption(
                key="minecraft",
                role_id=101,
                label="Minecraft",
                group_key="games",
            ),
        ),
        source="v2",
    )

    with pytest.raises(ValueError, match="Move or delete the options"):
        without_group(config, "games")

    updated = without_group(config, "empty")
    assert updated.revision == 8
    assert [group.key for group in updated.groups] == ["games"]
    assert [group.order for group in updated.groups] == [0]

    unchanged = without_group(updated, "missing")
    assert unchanged is updated
