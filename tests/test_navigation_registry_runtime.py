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
    home_labels = _labels(surface.CompactDankHomeView(100))
    assert {category.label for category in registry.CATEGORIES} <= home_labels
    assert {"Find a Feature", "All Features", "Close"} <= home_labels

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
