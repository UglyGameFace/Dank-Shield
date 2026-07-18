from pathlib import Path


SOURCE = Path(
    "stoney_verify/commands_ext/"
    "public_setup_full_customization.py"
).read_text(encoding="utf-8")


def block(start: str, end: str) -> str:
    left = SOURCE.index(start)
    right = SOURCE.index(end, left)
    return SOURCE[left:right]


def test_main_role_page_uses_feature_names():
    roles = block(
        "class RoleCustomizationPageOne(",
        "class RoleCustomizationPageTwo(",
    )

    assert "Tickets: role that answers tickets" in roles
    assert "Verify: waiting role for new members" in roles
    assert "Verify: approved member role" in roles
    assert "Optional Roles" in roles


def test_optional_role_page_has_back_button():
    roles = block(
        "class RoleCustomizationPageTwo(",
        "class DiscordCategoryCustomizationView(",
    )

    assert "Back to Main Roles" in roles
    assert "RoleCustomizationPageOne()" in roles


def test_ticket_folders_are_plain_language():
    folders = block(
        "class DiscordCategoryCustomizationView(",
        "class ChannelCustomizationPageOne(",
    )

    assert "Tickets: folder for new tickets" in folders
    assert "Optional: folder for closed tickets" in folders
    assert "Discord category" not in folders


def test_main_channels_follow_enabled_features():
    channels = block(
        "class ChannelCustomizationPageOne(",
        "class ChannelCustomizationPageTwo(",
    )

    assert "Verify: channel with the Verify button" in channels
    assert "Tickets: channel with Create Ticket panel" in channels
    assert "Voice Verify: voice channel for the check" in channels
    assert "Optional Channels" in channels


def test_optional_channels_have_back_button():
    channels = block(
        "class ChannelCustomizationPageTwo(",
        "class LogStatusCustomizationView(",
    )

    assert "Back to Main Channels" in channels
    assert "ChannelCustomizationPageOne()" in channels
    assert "Join and leave: staff log channel" in channels


def test_log_choices_are_plain_language():
    logs = block(
        "class LogStatusCustomizationView(",
        "class BehaviorSettingsModal(",
    )

    assert "Tickets: saved transcript channel" in logs
    assert "Moderation and protection log channel" in logs
    assert "Join and leave log channel" in logs
    assert "Bot status and uptime channel" in logs


def test_all_underlying_settings_remain():
    required = {
        "server_control_role_id",
        "staff_role_id",
        "unverified_role_id",
        "verified_role_id",
        "resident_role_id",
        "vc_staff_role_id",
        "control_role_id",
        "start_category_id",
        "ticket_category_id",
        "ticket_archive_category_id",
        "management_category_id",
        "welcome_channel_id",
        "verify_channel_id",
        "ticket_panel_channel_id",
        "vc_verify_channel_id",
        "vc_verify_queue_channel_id",
        "join_leave_log_channel_id",
        "support_channel_id",
        "health_channel_id",
        "transcripts_channel_id",
        "modlog_channel_id",
        "status_channel_id",
    }

    missing = sorted(
        key
        for key in required
        if f'"{key}"' not in SOURCE
    )

    assert not missing, (
        "Missing setup settings: "
        + ", ".join(missing)
    )


def test_join_leave_aliases_remain_in_both_pages():
    assert (
        SOURCE.count(
            "also_same=JOIN_LEAVE_LOG_ALIASES"
        )
        >= 2
    )
