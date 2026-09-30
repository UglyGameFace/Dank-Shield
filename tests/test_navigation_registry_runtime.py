from __future__ import annotations

import discord

from stoney_verify import navigation_registry as registry
from stoney_verify.commands_ext import public_command_surface_v2 as surface


def _labels(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(item, "label", "") or "")
        for item in view.children
        if str(getattr(item, "label", "") or "")
    }


def test_navigation_registry_is_complete_and_internally_valid() -> None:
    assert registry.REGISTRY_ERRORS == ()
    assert registry.validate_registry() == []

    category_keys = {item.key for item in registry.CATEGORIES}
    feature_keys = {item.key for item in registry.FEATURES}

    assert len(category_keys) == len(registry.CATEGORIES)
    assert len(feature_keys) == len(registry.FEATURES)
    assert feature_keys == set(surface._FEATURE_ROUTE_KEYS)

    for feature in registry.FEATURES:
        assert feature.category in category_keys
        assert feature.label
        assert feature.description


def test_every_registered_category_and_feature_is_reachable_from_navigation() -> None:
    home = surface.CompactDankHomeView(100)
    home_labels = _labels(home)
    assert {"Find", "Directory", "Close"} <= home_labels

    section_select = next(
        item
        for item in home.children
        if isinstance(item, discord.ui.Select)
        and str(getattr(item, "custom_id", "")) == "dank:home:sections:v1"
    )
    assert [str(option.label) for option in section_select.options] == [
        category.label for category in registry.CATEGORIES
    ]
    assert [str(option.value) for option in section_select.options] == [
        category.key for category in registry.CATEGORIES
    ]

    reached: set[str] = set()
    for category in registry.CATEGORIES:
        features = registry.features_for_category(category.key)
        assert features
        view = surface.FeatureCategoryView(100, category.key)
        labels = _labels(view)
        assert {"Back", "Home", "Refresh", "Close"} <= labels
        assert len(view.children) <= 25
        assert all(int(getattr(item, "row", 0) or 0) <= 4 for item in view.children)

        expected = {item.label for item in features}
        assert expected <= labels
        reached.update(item.key for item in features)

    assert reached == {item.key for item in registry.FEATURES}


def test_registry_preserves_all_previous_home_destinations() -> None:
    labels = {item.label for item in registry.FEATURES}
    assert {
        "Setup & Settings",
        "Protection",
        "Tickets",
        "Verification",
        "Welcome, Join & Exit",
        "Members & Moderation",
        "Server Design",
        "Roles & Profiles",
        "Logs & Activity",
        "My Profile",
        "Community Tools",
        "Community Hub",
        "Server Stats",
        "Status",
        "Diagnostics",
        "Card Assets",
        "Live Captions",
        "Help",
    } <= labels


def test_registry_promotes_previously_buried_member_and_manager_destinations() -> None:
    by_key = {item.key: item for item in registry.FEATURES}

    assert by_key["member_setup_manager"].category == "access"
    assert by_key["community_pings_manager"].category == "community"
    assert by_key["share_router"].category == "community"

    assert by_key["my_member_setup"].category == "my"
    assert by_key["profile_tags"].category == "my"
    assert by_key["my_community_pings"].category == "my"

    assert by_key["member_setup_manager"].manager_only is True
    assert by_key["community_pings_manager"].manager_only is True

    access_labels = _labels(surface.FeatureCategoryView(100, "access"))
    community_labels = _labels(surface.FeatureCategoryView(100, "community"))
    my_labels = _labels(surface.FeatureCategoryView(100, "my"))

    assert "Member Setup Manager" in access_labels
    assert "Community & Pings Manager" in community_labels
    assert "Share Router" in community_labels
    assert {"My Member Setup", "Profile Tags & Cosmetics", "My Community & Pings"} <= my_labels


def test_find_feature_understands_common_names_and_aliases() -> None:
    assert registry.search_features("invite shield")[0].key == "protection"
    assert registry.search_features("ping roles")[0].key == "community_pings_manager"
    assert registry.search_features("share router")[0].key == "share_router"
    assert registry.search_features("member setup manager")[0].key == "member_setup_manager"
    assert registry.search_features("captions")[0].key == "live_captions"
    assert registry.search_features("ticket forms")[0].key == "tickets"
    assert registry.search_features("channel fonts")[0].key == "server_design"


def test_manager_features_are_visible_instead_of_permission_hidden() -> None:
    manager_keys = {item.key for item in registry.FEATURES if item.manager_only}
    assert manager_keys

    visible_keys = {
        feature.key
        for category in registry.CATEGORIES
        for feature in registry.features_for_category(category.key)
    }
    assert manager_keys <= visible_keys


def test_directory_lists_every_registry_category() -> None:
    embed = surface._directory_embed()
    field_names = {str(field.name) for field in embed.fields}
    for category in registry.CATEGORIES:
        assert f"{category.emoji} {category.label}" in field_names

    assert len(surface.FeatureDirectoryView(100).children) <= 25


def test_promoted_shortcuts_use_opt_in_in_place_navigation() -> None:
    surface_source = (
        __import__("pathlib").Path(surface.__file__).read_text(encoding="utf-8")
    )
    member_setup_source = (
        __import__("pathlib").Path(__import__(
            "stoney_verify.commands_ext.public_member_setup",
            fromlist=["x"],
        ).__file__).read_text(encoding="utf-8")
    )
    toke_source = (
        __import__("pathlib").Path(__import__(
            "stoney_verify.commands_ext.public_toke",
            fromlist=["x"],
        ).__file__).read_text(encoding="utf-8")
    )
    profile_source = (
        __import__("pathlib").Path(__import__(
            "stoney_verify.commands_ext.public_self_roles_group",
            fromlist=["x"],
        ).__file__).read_text(encoding="utf-8")
    )

    assert "open_member_setup(interaction, replace_message=True)" in surface_source
    assert "open_community_ping_setup(interaction, replace_message=True)" in surface_source
    assert "open_member_community_pings(interaction, replace_message=True)" in surface_source
    assert "replace_message=True," in surface_source

    assert "replace_message: bool = False" in member_setup_source
    assert "if replace_message:" in member_setup_source
    assert "await _defer_panel_update(interaction)" in member_setup_source
    assert "await _defer(interaction)" in member_setup_source

    assert toke_source.count("replace_message: bool = False") >= 2
    assert toke_source.count("if replace_message:") >= 2
    assert "await _defer_update(interaction)" in toke_source
    assert "await _defer_private(interaction)" in toke_source

    assert profile_source.count("replace_message: bool = False") >= 2
    assert "interaction.response.edit_message(" in profile_source
    assert "interaction.response.send_message(" in profile_source

    builder_start = profile_source.index("async def _post_profile_builder")
    builder_end = profile_source.index("async def _handle_builder_action", builder_start)
    builder = profile_source[builder_start:builder_end]
    assert "await _require_setup_permission(interaction)" in builder


def test_every_registry_feature_has_an_explicit_dispatch_branch() -> None:
    from pathlib import Path

    source = Path(surface.__file__).read_text(encoding="utf-8")
    for feature in registry.FEATURES:
        assert f'if key == "{feature.key}":' in source


def test_home_is_mobile_compact_full_name_section_picker() -> None:
    view = surface.CompactDankHomeView(100)
    assert len(view.children) == 4

    section_select = next(
        item
        for item in view.children
        if isinstance(item, discord.ui.Select)
        and str(getattr(item, "custom_id", "")) == "dank:home:sections:v1"
    )
    assert int(getattr(section_select, "row", 0) or 0) == 0
    assert str(section_select.placeholder) == "Choose a section…"
    assert [str(option.label) for option in section_select.options] == [
        category.label for category in registry.CATEGORIES
    ]

    button_labels = [
        str(getattr(item, "label", "") or "")
        for item in view.children
        if isinstance(item, discord.ui.Button)
    ]
    assert button_labels == ["Find", "Directory", "Close"]
    assert all(int(getattr(item, "row", 0) or 0) == 1 for item in view.children if isinstance(item, discord.ui.Button))


def test_home_embed_does_not_repeat_the_full_category_directory() -> None:
    embed = surface._home_embed()
    assert str(embed.title) == "🛡️ Dank Shield"
    assert len(embed.fields) == 0
    assert "Control Center" in str(embed.description)
    assert f"{len(registry.FEATURES)} destinations" in str(embed.description)
    assert f"{len(registry.CATEGORIES)} sections" in str(embed.description)
    assert "Setup & Server Settings" not in str(embed.description)
    assert "Members, Roles & Profiles" not in str(embed.description)
