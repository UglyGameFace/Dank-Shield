from pathlib import Path

import pytest

from stoney_verify.profile_card_service import (
    InvalidPlatformProfile,
    effective_preferences,
    normalize_platform_entry,
    normalize_platform_url,
    normalize_server_allowed_fields,
    visible_platform_entries,
)


ROOT = Path(__file__).resolve().parents[1]


def test_official_profile_urls_are_canonicalized_without_tracking():
    assert normalize_platform_url(
        "steam",
        "https://steamcommunity.com/id/UglyGameFace",
    ) == "https://steamcommunity.com/id/UglyGameFace"
    assert normalize_platform_url(
        "roblox",
        "https://www.roblox.com/users/123456/profile",
    ) == "https://roblox.com/users/123456/profile"
    assert normalize_platform_url(
        "youtube",
        "https://www.youtube.com/@UglyGameFace",
    ) == "https://youtube.com/@UglyGameFace"
    assert normalize_platform_url(
        "twitch",
        "https://www.twitch.tv/uglygameface",
    ) == "https://twitch.tv/uglygameface"


@pytest.mark.parametrize(
    ("platform", "url"),
    [
        ("steam", "http://steamcommunity.com/id/example"),
        ("steam", "https://steamcommunity.com.evil.example/id/example"),
        ("steam", "https://evil.example/?next=steamcommunity.com/id/example"),
        ("roblox", "https://roblox.com.evil.example/users/123/profile"),
        ("roblox", "https://roblox.com/users/not-a-number/profile"),
        ("youtube", "https://youtube.com/watch?v=abc"),
        ("twitch", "https://twitch.tv/login"),
        ("kick", "https://kick.com/example?tracking=1"),
    ],
)
def test_phishing_lookalikes_and_non_profile_urls_are_rejected(platform, url):
    with pytest.raises(InvalidPlatformProfile):
        normalize_platform_url(platform, url)


@pytest.mark.parametrize(
    "platform",
    [
        "epic",
        "xbox",
        "playstation",
        "nintendo",
        "riot",
        "battle_net",
        "custom",
    ],
)
def test_username_only_platforms_never_invent_or_accept_profile_links(platform):
    assert normalize_platform_url(platform, "") == ""
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


def test_server_allowed_fields_are_allowlisted_and_default_safe():
    assert normalize_server_allowed_fields(None) == {"roles", "account_dates", "platforms"}
    assert normalize_server_allowed_fields(["roles", "platforms", "admin", "unknown"]) == {
        "roles",
        "platforms",
    }


def test_profile_tables_are_service_role_only_and_rls_enabled():
    migration = (ROOT / "supabase/migrations/20260725_live_profile_cards.sql").read_text(encoding="utf-8")
    for table in (
        "dank_profile_users",
        "dank_profile_guild_settings",
        "dank_live_profile_cards",
    ):
        assert f"alter table public.{table} enable row level security" in migration
        assert f"revoke all on table public.{table} from anon, authenticated" in migration
    assert "create policy" not in migration.lower()
