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
    assert {"Find Feature", "Directory", "Help", "Close"} <= home_labels

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
        "AI Voice Safety",
        "Share Router Downloads",
        "Toke Sesh Settings",
        "Verification Reminders",
        "Help",
    } <= labels


def test_registry_promotes_previously_buried_member_and_manager_destinations() -> None:
    by_key = {item.key: item for item in registry.FEATURES}

    assert by_key["action_reminder_center"].category == "actions"
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
    assert registry.search_features("voice patrol")[0].key == "voice_safety"
    assert registry.search_features("voice moderation")[0].key == "voice_safety"
    assert registry.search_features("download channel")[0].key == "share_downloads"
    assert registry.search_features("facebook download")[0].key == "share_downloads"
    assert registry.search_features("sesh")[0].key == "toke_settings"
    assert registry.search_features("unverified reminders")[0].key == "verification_reminders"
    assert registry.search_features("subtitles")[0].key == "movie_night"


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
    community_pings_source = (
        __import__("pathlib").Path(__import__(
            "stoney_verify.commands_ext.public_community_pings",
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

    assert "replace_message: bool = False" in toke_source
    assert "await open_community_ping_setup(interaction, replace_message=replace_message)" in toke_source
    assert "await open_generic_manager(interaction, replace_message=replace_message)" in toke_source
    assert "await open_generic_member(interaction, replace_message=replace_message)" in toke_source

    preset_start = toke_source.index("async def open_toke_preset_setup")
    preset_end = toke_source.index("def _target_channel(", preset_start)
    preset_block = toke_source[preset_start:preset_end]
    assert "await _defer_update(interaction)" not in preset_block
    assert "await _defer_private(interaction)" not in preset_block

    command_start = toke_source.index("async def open_toke_command")
    command_block = toke_source[command_start:]
    assert "await _defer_private(interaction)" in command_block

    assert community_pings_source.count("replace_message: bool = False") >= 2
    assert community_pings_source.count("if replace_message and not interaction.response.is_done():") >= 2
    assert "open_community_ping_setup" in community_pings_source
    assert "open_member_community_pings" in community_pings_source

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
    assert len(view.children) == 6

    section_select = next(
        item
        for item in view.children
        if isinstance(item, discord.ui.Select)
        and str(getattr(item, "custom_id", "")) == "dank:home:sections:v1"
    )
    assert int(getattr(section_select, "row", 0) or 0) == 0
    assert str(section_select.placeholder) == "🧭 Choose a section…"
    assert [str(option.label) for option in section_select.options] == [
        category.label for category in registry.CATEGORIES
    ]

    button_labels = [
        str(getattr(item, "label", "") or "")
        for item in view.children
        if isinstance(item, discord.ui.Button)
    ]
    assert button_labels == ["Find Feature", "Directory", "Help", "Close", "New & Updated"]
    assert all(int(getattr(item, "row", 0) or 0) <= 2 for item in view.children if isinstance(item, discord.ui.Button))


def test_home_embed_does_not_repeat_the_full_category_directory() -> None:
    embed = surface._home_embed()
    assert str(embed.title) == "🛡️ DANK SHIELD"
    assert len(embed.fields) == 0
    assert "CONTROL CENTER" in str(embed.description)
    assert f"{len(registry.FEATURES)} destinations" in str(embed.description)
    assert f"{len(registry.CATEGORIES)} sections" in str(embed.description)
    assert "Setup & Server Settings" not in str(embed.description)
    assert "Members, Roles & Profiles" not in str(embed.description)


def test_new_and_updated_shortcuts_route_to_live_owner_without_duplicate_logic() -> None:
    recent = registry.recent_features()
    assert tuple(item.key for item in recent) == registry.RECENT_FEATURE_KEYS
    assert len(recent) == len(set(item.key for item in recent))
    view = surface.RecentFeaturesView(123)
    buttons = [item for item in view.children if isinstance(item, discord.ui.Button)]
    labels = {item.label for item in buttons}
    assert {item.label for item in recent} <= labels
    assert {"Home", "Close"} <= labels
    assert len(view.children) <= 25
    assert max(int(getattr(item, "row", 0) or 0) for item in view.children) <= 4
    embed = surface._recent_embed()
    assert all(item.label in "\n".join(str(field.value) for field in embed.fields) for item in recent)

    from pathlib import Path
    source = Path(surface.__file__).read_text(encoding="utf-8")
    assert 'from .public_voice_safety import open_voice_safety_center' in source
    assert 'from .public_share_router import open_share_router_download_settings' in source
    assert 'from stoney_verify.toke_live_ui import open_toke_settings' in source
    assert 'from .public_verify_reminder_ui import open_reminder_settings' in source


def test_releases_show_real_merged_updates_and_exclude_unmerged_work() -> None:
    updates = registry.RECENT_UPDATES
    assert [item.key for item in updates][:6] == [
        "verification_reminders", "verification", "embed_studio",
        "share_router", "share_downloads", "action_safety_schedule",
    ]
    assert updates[0].headline == "Full-server verification reminders by channel or DM"
    assert "automatic schedules remain separate" in updates[0].detail
    assert registry.search_features("verification dm reminders")[0].key == "verification_reminders"
    assert len(updates) == len(set(item.key for item in updates))
    assert all(registry.feature_by_key(item.key) for item in updates)
    assert any("post builder" in item.headline.lower() for item in updates)
    assert all(item.merged_date and item.detail for item in updates)
    assert any("JSON" in item.detail for item in updates)

    content = surface._recent_embed()
    joined = "\n".join(str(field.value) for field in content.fields)
    assert "Post Builder is easier for moderators" in joined
    assert "Share videos from regular channels" in joined
    assert "Member Setup" in joined
    assert "Only merged updates" in str(content.footer.text)
    assert len(content.fields) <= 25
    assert all(len(str(field.value)) <= 1024 for field in content.fields)


def test_design_has_direct_embed_studio_without_second_editor() -> None:
    design = registry.features_for_category("design")
    assert "embed_studio" in {item.key for item in design}
    assert "Post Builder" in _labels(surface.FeatureCategoryView(100, "design"))
    assert registry.search_features("embed studio")[0].key == "embed_studio"
    assert registry.search_features("embed builder")[0].key == "embed_studio"
    assert registry.search_features("create announcement")[0].key == "embed_studio"
    assert registry.search_features("json embed")[0].key == "embed_studio"
    from pathlib import Path
    source = Path(surface.__file__).read_text(encoding="utf-8")
    assert 'if key == "embed_studio":' in source
    assert "from ..embed_studio_ui import open_embed_studio" in source
    assert "return await open_embed_studio(interaction)" in source
    assert len(surface.FeatureCategoryView(100, "design").children) <= 25


def test_category_groups_have_readable_tasks_and_fit_mobile() -> None:
    action_groups = registry.grouped_features("actions")
    assert [name for name, _ in action_groups] == [
        "Your reminders", "For server moderators",
    ]
    community_groups = registry.grouped_features("community")
    assert {name for name, _ in community_groups} >= {
        "Share photos & videos", "Events & meetups", "Roles & notifications",
    }
    design_groups = registry.grouped_features("design")
    assert [name for name, _ in design_groups] == [
        "Create messages", "Server appearance",
    ]
    for category in registry.CATEGORIES:
        keys = [f.key for _group, features in registry.grouped_features(category.key) for f in features]
        assert sorted(keys) == sorted(f.key for f in registry.features_for_category(category.key))
        embed = surface._category_embed(category)
        assert all(len(str(field.value)) <= 1024 for field in embed.fields)
        assert "manager-only" not in str(embed.description)
        view = surface.FeatureCategoryView(100, category.key)
        assert max(int(getattr(item, "row", 0) or 0) for item in view.children) <= 4


def test_buttons_use_action_meaning_not_moderator_role_color() -> None:
    blue = discord.ButtonStyle.primary
    neutral = discord.ButtonStyle.secondary
    design = surface.FeatureCategoryView(100, "design")
    embed = next(item for item in design.children if getattr(item, "label", "") == "Post Builder")
    card = next(item for item in design.children if getattr(item, "label", "") == "Card Assets")
    assert embed.style is blue
    assert card.style is neutral
    assert registry.feature_by_key("card_assets").manager_only
    home = surface.CompactDankHomeView(100)
    close = next(item for item in home.children if getattr(item, "label", "") == "Close")
    find = next(item for item in home.children if getattr(item, "label", "") == "Find Feature")
    assert close.style is neutral
    assert find.style is blue
    assert all(
        item.style is not discord.ButtonStyle.danger
        for item in surface.RecentFeaturesView(100).children
        if getattr(item, "label", "") == "Close"
    )


def test_navigation_home_category_descriptions_represent_recent_features() -> None:
    categories = {category.key: category for category in registry.CATEGORIES}
    assert "voice" in categories["safety"].description.lower()
    assert "voice safety" in categories["voice"].description.lower()
    assert "toke" in categories["community"].description.lower()
    assert "download" in categories["community"].description.lower()
    assert registry.feature_by_key("voice_safety").category == "voice"
    for key in ("share_downloads", "toke_settings"):
        assert registry.feature_by_key(key).category == "community"
    assert registry.feature_by_key("verification_reminders").category == "access"
    assert registry.feature_by_key("action_reminder_center").category == "actions"


def test_merged_voice_safety_navigation_contract_from_pr_522() -> None:
    import inspect

    cats = {category.key: category for category in registry.CATEGORIES}
    assert "AI Voice Safety" in cats["voice"].description
    assert "AI Voice Safety" in cats["safety"].description
    assert "Share Router" in cats["community"].description
    assert "Movie Night" in cats["community"].description
    assert len(registry.CATEGORIES) <= 25
    assert all(len(c.description) <= 100 for c in registry.CATEGORIES)
    assert {f.key for f in registry.features_for_category("voice")} == {"live_captions", "voice_safety"}
    entry = registry.feature_by_key("voice_safety")
    assert entry is not None and entry.manager_only is True
    assert "AI Voice Safety" in _labels(surface.FeatureCategoryView(100, "voice"))
    assert registry.search_features("AI Voice Moderation")[0].key == "voice_safety"
    assert registry.search_features("voice harassment")[0].key == "voice_safety"
    assert registry.search_features("voicepatrol")[0].key == "voice_safety"
    # New direct Download Settings replaces the earlier broader router match.
    assert registry.search_features("download channel")[0].key == "share_downloads"
    code = inspect.getsource(surface._route_feature)
    assert 'if key == "voice_safety":' in code
    assert "from .public_voice_safety import open_voice_safety_center" in code
    assert "return await open_voice_safety_center(interaction)" in code


def test_actions_and_notifications_is_a_distinct_mobile_category_with_live_owner_shortcuts() -> None:
    from stoney_verify.commands_ext import (
        public_action_builder,
        public_action_notification_directory,
        public_action_preferences,
        public_action_schedule,
    )
    category = registry.category_by_key("actions")
    assert category is not None
    assert category.label == "Actions & Notifications"
    assert category.emoji == "📋"
    assert len(registry.CATEGORIES) <= 25
    assert len(category.description) <= 100
    expected = {
        "action_reminder_center", "action_notification_systems", "action_builder",
        "action_safety_schedule", "action_my_preferences",
    }
    assert {feature.key for feature in registry.features_for_category("actions")} == expected
    labels = _labels(surface.FeatureCategoryView(42, "actions"))
    assert {
        "Action & Reminder Center", "Other Notification Settings", "Reminder Rules (Preview)",
        "Reminder Times & Limits", "My Reminder Preferences",
        "Back", "Home", "Refresh", "Close",
    } <= labels
    assert "Action & Reminder Center" not in _labels(surface.FeatureCategoryView(42, "access"))
    assert {"Verification", "Member Setup Manager", "Verification Reminders"} <= _labels(
        surface.FeatureCategoryView(42, "access")
    )
    assert registry.search_features("actions and notifications")[0].key == "action_reminder_center"
    assert registry.search_features("actions & notifications")[0].key == "action_reminder_center"
    assert registry.search_features("notification systems")[0].key == "action_notification_systems"
    assert registry.search_features("reminder center")[0].key == "action_reminder_center"
    assert registry.search_features("my reminder preferences")[0].key == "action_my_preferences"
    assert registry.search_features("delivery readiness")[0].key == "action_safety_schedule"
    assert registry.feature_by_key("action_notification_systems").manager_only
    assert registry.feature_by_key("action_builder").manager_only
    assert registry.feature_by_key("action_safety_schedule").manager_only
    assert not registry.feature_by_key("action_my_preferences").manager_only

    source = __import__("inspect").getsource(surface._route_feature)
    for feature_key, owner in (
        ("action_notification_systems", "open_notification_owner_directory"),
        ("action_builder", "open_action_builder"),
        ("action_safety_schedule", "open_schedule_panel"),
        ("action_my_preferences", "open_member_action_preferences"),
    ):
        assert f'if key == "{feature_key}":' in source
        assert owner in source
    for func in (
        public_action_builder.open_action_builder,
        public_action_notification_directory.open_notification_owner_directory,
        public_action_schedule.open_schedule_panel,
    ):
        assert "scoped_interaction_is_staff" in __import__("inspect").getsource(func)
    assert "read_member_consent" in __import__("inspect").getsource(
        public_action_preferences.open_member_action_preferences
    )
