from pathlib import Path

import pytest

from stoney_verify.profile_card_service import (
    InvalidPlatformProfile,
    clean_profile_username,
    display_profile_username,
    effective_preferences,
    normalize_platform_entry,
    normalize_platform_url,
    visible_platform_entries,
)


ROOT = Path(__file__).resolve().parents[1]


def test_profile_username_sanitization_prevents_discord_mentions_and_markdown_links():
    assert clean_profile_username(" @everyone  test ") == "everyone test"
    assert display_profile_username("player`name") == "playerʼname"
    with pytest.raises(InvalidPlatformProfile):
        clean_profile_username("https://example.com/profile")


def test_supported_platform_urls_are_canonical_and_tracking_free():
    assert normalize_platform_url(
        "steam",
        "https://steamcommunity.com/id/Player_Name",
    ) == "https://steamcommunity.com/id/Player_Name"
    assert normalize_platform_url(
        "roblox",
        "https://www.roblox.com/users/12345/profile",
    ) == "https://roblox.com/users/12345/profile"
    assert normalize_platform_url(
        "youtube",
        "https://www.youtube.com/@Player_Name",
    ) == "https://youtube.com/@Player_Name"

    for url in (
        "http://steamcommunity.com/id/player",
        "https://evil.example/steamcommunity.com/id/player",
        "https://steamcommunity.com/id/player?tracking=1",
        "https://steamcommunity.com/profiles/not-a-number",
    ):
        with pytest.raises(InvalidPlatformProfile):
            normalize_platform_url("steam", url)


def test_username_only_platforms_reject_invented_links():
    for platform in (
        "epic",
        "xbox",
        "playstation",
        "nintendo",
        "riot",
        "battle_net",
        "custom",
    ):
        with pytest.raises(InvalidPlatformProfile):
            normalize_platform_url(platform, "https://example.com/member")


def test_platform_identity_is_private_until_explicitly_shared():
    private = normalize_platform_entry("steam", username="Player", shared=False)
    public = normalize_platform_entry("twitch", username="Streamer", shared=True)
    entries = visible_platform_entries(
        {"steam": private, "twitch": public},
        allowed=True,
    )
    assert [entry["platform"] for entry in entries] == ["twitch"]
    assert visible_platform_entries({"twitch": public}, allowed=False) == []


def test_global_defaults_and_deny_only_server_overrides_are_exposed():
    service = (ROOT / "stoney_verify/profile_card_service.py").read_text(encoding="utf-8")
    commands = (ROOT / "stoney_verify/commands_ext/public_profile_cards.py").read_text(encoding="utf-8")
    commands_core = (ROOT / "stoney_verify/commands_ext/public_profile_cards_core.py").read_text(encoding="utf-8")
    assert "async def upsert_profile_user_preferences" in service
    assert "settings.pop(key, None)" in service
    assert "settings[key] = False" in service
    assert "class _GlobalPrivacyToggleButton" in commands_core
    assert "class _GuildPrivacyToggleButton" in commands_core
    assert "class ProfileSettingsView" in commands
    assert "Everywhere" in commands_core
    assert "In This Server" in commands_core
    assert "Use Default" in commands_core
    assert "Every Server" not in commands_core
    assert "Inherit" not in commands_core


def test_per_server_user_privacy_can_only_be_stricter_than_global_defaults():
    effective = effective_preferences(
        {
            "live_cards_enabled": False,
            "show_roles": True,
            "show_account_dates": False,
            "show_platforms": True,
        },
        {
            "live_cards_enabled": True,
            "show_roles": False,
            "show_account_dates": True,
            "show_platforms": False,
        },
    )
    assert effective == {
        "live_cards_enabled": False,
        "show_roles": False,
        "show_account_dates": False,
        "show_platforms": False,
    }
