from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import discord

from stoney_verify.command_surface_contract import (
    PUBLIC_GLOBAL_COMMAND_COUNT,
    PUBLIC_GLOBAL_COMMAND_NAMES,
)
from stoney_verify.commands_ext.public_role_center import RolesProfilesView
from stoney_verify.commands_ext.public_self_roles_group import (
    ProfileBuilderView,
    ProfilePanelView,
)
from stoney_verify.commands_ext import public_toke
from stoney_verify.commands_ext import public_community_pings as community_ui
from stoney_verify.commands_ext.public_community_pings import CommunityPingsManagerView
from stoney_verify.community_pings_service import (
    CAP_TOKE_NOTIFY,
    CAP_TOKE_START,
    COMMUNITY_PINGS_KEY,
    CommunityPingGroup,
    CommunityPingOption,
    CommunityPingsConfig,
)
from stoney_verify.profile_card_runtime import _compact_profile_tag_labels


ROOT = Path(__file__).resolve().parents[1]


def _labels(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(item, "label", "") or "")
        for item in view.children
        if str(getattr(item, "label", "") or "")
    }


def test_public_surface_intentionally_exposes_toke() -> None:
    assert PUBLIC_GLOBAL_COMMAND_COUNT == 9
    assert PUBLIC_GLOBAL_COMMAND_NAMES == (
        "dank",
        "captions",
        "mod",
        "role",
        "ticket",
        "tickets",
        "toke",
        "verify",
        "View Dank Profile",
    )


def test_profile_surfaces_expose_community_and_pings() -> None:
    assert "Community & Pings" in _labels(ProfilePanelView())
    assert "Community & Pings" in _labels(
        ProfileBuilderView(author_id=1, ready=True, fixable=False, title="Profile Panel")
    )
    assert "My Community & Pings" in _labels(
        RolesProfilesView(
            1,
            staff=False,
            role_manager=False,
            setup_manager=False,
        )
    )


def test_configured_ids_fail_closed_on_bad_values() -> None:
    assert public_toke._configured_ids(
        {
            public_toke.STONER_ROLE_KEY: "123",
            public_toke.SESH_PING_ROLE_KEY: 456,
            public_toke.TOKE_CHANNEL_KEY: "789",
        }
    ) == (123, 456, 789)
    assert public_toke._configured_ids(
        {
            public_toke.STONER_ROLE_KEY: "not-an-id",
            public_toke.SESH_PING_ROLE_KEY: None,
            public_toke.TOKE_CHANNEL_KEY: False,
        }
    ) == (0, 0, 0)


def test_member_role_authority_uses_exact_role_id() -> None:
    member = SimpleNamespace(
        roles=[
            SimpleNamespace(id=111),
            SimpleNamespace(id=222),
        ]
    )
    assert public_toke._member_has_role_id(member, 222)
    assert not public_toke._member_has_role_id(member, 333)
    assert not public_toke._member_has_role_id(member, 0)


def test_member_message_cannot_smuggle_mass_mentions() -> None:
    clean = public_toke._clean_member_message(
        "  @everyone    pull up\n@here   "
    )
    assert clean == "@\u200beveryone pull up @\u200bhere"
    assert len(public_toke._clean_member_message("x" * 500)) == 180


def test_toke_allowed_mentions_only_serializes_selected_role() -> None:
    role = discord.Object(id=123456789012345678)
    payload = public_toke._toke_allowed_mentions(role).to_dict()

    assert payload.get("roles") == [123456789012345678]
    assert "everyone" not in payload.get("parse", [])
    assert "users" not in payload.get("parse", [])
    assert payload.get("replied_user") is not True


def test_ping_readiness_accepts_mentionable_role_or_bot_permission() -> None:
    mentionable = SimpleNamespace(mentionable=True)
    locked = SimpleNamespace(mentionable=False)

    assert public_toke._ping_permission_ready(
        mentionable,
        discord.Permissions.none(),
    )

    can_mass_mention = discord.Permissions.none()
    can_mass_mention.update(mention_everyone=True)
    assert public_toke._ping_permission_ready(locked, can_mass_mention)
    assert not public_toke._ping_permission_ready(
        locked,
        discord.Permissions.none(),
    )


def test_cooldowns_have_member_and_guild_boundaries() -> None:
    public_toke._TOKE_USER_LAST.clear()
    public_toke._TOKE_GUILD_LAST.clear()

    now = 10_000.0
    public_toke._TOKE_USER_LAST[(50, 10)] = now - 60
    public_toke._TOKE_GUILD_LAST[50] = now - 60

    remaining, label = public_toke._cooldown_remaining(
        now=now,
        guild_id=50,
        user_id=10,
    )
    assert 13 * 60 <= remaining <= 15 * 60
    assert label == "your /toke cooldown"

    remaining, label = public_toke._cooldown_remaining(
        now=now,
        guild_id=50,
        user_id=20,
    )
    assert 3 * 60 <= remaining <= 5 * 60
    assert label == "this server's /toke cooldown"

    remaining, label = public_toke._cooldown_remaining(
        now=now,
        guild_id=99,
        user_id=20,
    )
    assert remaining == 0
    assert label == ""

    public_toke._TOKE_USER_LAST.clear()
    public_toke._TOKE_GUILD_LAST.clear()


def test_stoner_is_profile_identity_but_sesh_subscription_is_not() -> None:
    stoner_id = 123456789012345678
    sesh_id = 223456789012345678

    stoner_member = SimpleNamespace(
        roles=[SimpleNamespace(id=stoner_id, name="Stoner")]
    )
    labels = _compact_profile_tag_labels(
        stoner_member,
        {
            public_toke.STONER_ROLE_KEY: str(stoner_id),
            public_toke.SESH_PING_ROLE_KEY: str(sesh_id),
        },
    )
    assert "Community: Stoner" in labels

    sesh_only_member = SimpleNamespace(
        roles=[SimpleNamespace(id=sesh_id, name="Sesh Pings")]
    )
    labels = _compact_profile_tag_labels(
        sesh_only_member,
        {
            public_toke.STONER_ROLE_KEY: str(stoner_id),
            public_toke.SESH_PING_ROLE_KEY: str(sesh_id),
        },
    )
    assert all("Sesh Pings" not in label for label in labels)


def test_generic_community_identity_is_not_duplicated_as_cosmetic_tag() -> None:
    community_id = 323456789012345678
    member = SimpleNamespace(
        roles=[SimpleNamespace(id=community_id, name="Gaming Crew")]
    )
    labels = _compact_profile_tag_labels(
        member,
        {
            COMMUNITY_PINGS_KEY: {
                "version": 2,
                "revision": 3,
                "groups": [{"key": "community", "label": "Community"}],
                "options": [
                    {
                        "key": "gaming-crew",
                        "role_id": str(community_id),
                        "label": "Gaming Crew",
                        "kind": "community",
                        "group_key": "community",
                    }
                ],
            },
            "profile_cosmetic_role_ids": [str(community_id)],
        },
    )
    assert "Community: Gaming Crew" in labels
    assert all(not label.startswith("Tags:") for label in labels)


def test_malformed_v2_does_not_restore_legacy_profile_identity() -> None:
    legacy_id = 423456789012345678
    member = SimpleNamespace(
        roles=[SimpleNamespace(id=legacy_id, name="Stoner")]
    )
    labels = _compact_profile_tag_labels(
        member,
        {
            COMMUNITY_PINGS_KEY: "corrupt",
            public_toke.STONER_ROLE_KEY: str(legacy_id),
        },
    )
    assert all("Community: Stoner" not in label for label in labels)


def test_toke_setup_uses_only_shared_resource_browser_for_channel_discovery() -> None:
    toke_source = Path(public_toke.__file__).read_text(encoding="utf-8")
    manager_source = (
        ROOT / "stoney_verify/commands_ext/public_community_pings.py"
    ).read_text(encoding="utf-8")

    assert "CommunityPingSetupView" not in toke_source
    assert "DankChannelSelect" not in toke_source
    assert "DankRoleSelect" not in toke_source
    assert "await open_community_ping_setup(interaction, replace_message=replace_message)" in toke_source

    start = manager_source.index(
        '@discord.ui.button(label="Toke Channel"'
    )
    end = manager_source.index(
        '@discord.ui.button(label="Clear Toke Channel"',
        start,
    )
    block = manager_source[start:end]
    assert "DankGuildResourceBrowserView(" in block
    assert 'resource_kinds=("text",)' in block
    assert 'placeholder="Choose a text channel…"' in block
    assert "discord.ui.ChannelSelect" not in block


def test_generic_manager_exposes_add_edit_and_safe_remove_controls() -> None:
    labels = _labels(CommunityPingsManagerView(1))
    assert {
        "Add Option",
        "Edit Option",
        "Add Group",
        "Edit Group",
        "Delete Group",
        "Member Preview",
        "Toke Starter",
        "Toke Notify",
        "Toke Channel",
        "Clear Toke Channel",
        "Refresh",
        "Home",
        "Close",
    } <= labels
    assert len(CommunityPingsManagerView(1).children) <= 25


def test_direct_toke_mapping_moves_one_capability_without_losing_the_other() -> None:
    model = CommunityPingsConfig(
        revision=4,
        groups=(
            CommunityPingGroup(
                key="community",
                label="Community",
            ),
        ),
        options=(
            CommunityPingOption(
                key="stoner",
                role_id=101,
                label="Stoner",
                group_key="community",
                capabilities=(CAP_TOKE_START,),
            ),
            CommunityPingOption(
                key="sesh-pings",
                role_id=202,
                label="Sesh Pings",
                kind="notification",
                group_key="community",
                capabilities=(CAP_TOKE_NOTIFY,),
            ),
        ),
    )

    moved_start = community_ui._assign_toke_capability(
        model,
        option_key="sesh-pings",
        capability=CAP_TOKE_START,
    )
    by_key = {item.key: item for item in moved_start.options}

    assert CAP_TOKE_START not in by_key["stoner"].capabilities
    assert CAP_TOKE_START in by_key["sesh-pings"].capabilities
    assert CAP_TOKE_NOTIFY in by_key["sesh-pings"].capabilities
    assert moved_start.revision > model.revision

    moved_notify = community_ui._assign_toke_capability(
        moved_start,
        option_key="stoner",
        capability=CAP_TOKE_NOTIFY,
    )
    by_key = {item.key: item for item in moved_notify.options}

    assert CAP_TOKE_NOTIFY in by_key["stoner"].capabilities
    assert CAP_TOKE_START in by_key["sesh-pings"].capabilities
    assert CAP_TOKE_NOTIFY not in by_key["sesh-pings"].capabilities


def test_toke_manager_has_direct_mapping_instructions() -> None:
    source = (
        ROOT / "stoney_verify/commands_ext/public_community_pings.py"
    ).read_text(encoding="utf-8")

    assert 'label="Toke Starter"' in source
    assert 'label="Toke Notify"' in source
    assert "Use **Toke Starter**, **Toke Notify**, and **Toke Channel** below." in source
    assert "_open_toke_capability_picker" in source
    assert "_assign_toke_capability" in source


def test_member_picker_rejects_stale_configuration_before_mutation() -> None:
    source = (ROOT / "stoney_verify/commands_ext/public_community_pings.py").read_text(encoding="utf-8")
    start = source.index("async def _handle_member_pick")
    end = source.index("async def open_community_ping_setup", start)
    block = source[start:end]

    assert "expected_model: Optional[CommunityPingsConfig]" in block
    assert "model != expected_model" in block
    assert "Community & Pings changed since this panel opened" in block
    assert "community_member_lock(guild.id, member.id)" in block

    member_open_start = source.index("async def open_member_community_pings")
    member_block = source[member_open_start:]
    assert "expected_model=model" in member_block
    assert "on_pick=apply_selection" in member_block


def test_cheers_card_is_response_only_and_has_one_button() -> None:
    view = public_toke.TokeCheersView(starter_id=123, stoner_role_id=456)
    assert _labels(view) == {"Cheers"}
    assert len(view.children) == 1
