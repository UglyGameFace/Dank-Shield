from __future__ import annotations

import pytest

from stoney_verify.community_pings_service import (
    CAP_TOKE_NOTIFY,
    CAP_TOKE_START,
    COMMUNITY_PINGS_KEY,
    MAX_COMMUNITY_GROUPS,
    MAX_COMMUNITY_OPTIONS,
    CommunityPingGroup,
    CommunityPingOption,
    CommunityPingsConfig,
    move_option,
    parse_community_pings,
    role_dependency_labels,
    self_service_kind,
    toke_role_ids,
    upsert_group,
    validate_config,
    validate_member_selection,
    with_option,
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
