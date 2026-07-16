from pathlib import Path


SOURCE = Path(
    "stoney_verify/commands_ext/"
    "public_setup_full_customization.py"
).read_text(encoding="utf-8")


def block(start: str, end: str) -> str:
    left = SOURCE.index(start)
    right = SOURCE.index(end, left)
    return SOURCE[left:right]


def test_existing_items_menu_has_plain_groups():
    menu = block(
        "class FullChooseExistingView(",
        "class SaveRoleSelect(",
    )

    for label in (
        "Access & Staff Roles",
        "Ticket Folders",
        "Member Channels",
        "Logs & Status",
        "Optional Settings",
    ):
        assert label in menu

    assert "Customize Discord Categories" not in menu
    assert "Behavior Settings" not in menu


def test_each_plain_group_still_opens_real_picker():
    menu = block(
        "class FullChooseExistingView(",
        "class SaveRoleSelect(",
    )

    for view_name in (
        "RoleCustomizationPageOne",
        "DiscordCategoryCustomizationView",
        "ChannelCustomizationPageOne",
        "LogStatusCustomizationView",
        "BehaviorSettingsModal",
    ):
        assert view_name in menu


def test_save_confirmations_hide_internal_columns():
    role = block(
        "class SaveRoleSelect(",
        "class SaveChannelSelect(",
    )

    channel = block(
        "class SaveChannelSelect(",
        "class RoleCustomizationPageOne(",
    )

    assert 'title="✅ Role Saved"' in role
    assert 'title="✅ Channel Saved"' in channel
    assert "join(self.columns" not in role
    assert "join(self.columns" not in channel
    assert "self.placeholder" in role
    assert "self.placeholder" in channel


def test_every_existing_primary_setting_remains():
    required_settings = {
        # Roles
        "server_control_role_id",
        "staff_role_id",
        "unverified_role_id",
        "verified_role_id",
        "resident_role_id",
        "vc_staff_role_id",
        "control_role_id",

        # Discord folders
        "start_category_id",
        "ticket_category_id",
        "ticket_archive_category_id",
        "management_category_id",

        # Member-facing channels
        "welcome_channel_id",
        "verify_channel_id",
        "ticket_panel_channel_id",
        "vc_verify_channel_id",
        "vc_verify_queue_channel_id",
        "join_leave_channel_id",
        "support_channel_id",
        "health_channel_id",

        # Logs and status
        "transcripts_channel_id",
        "modlog_channel_id",
        "status_channel_id",

        # Optional behavior
        "ticket_prefix",
        "verify_kick_hours",
        "setup_note",
    }

    missing = sorted(
        setting
        for setting in required_settings
        if f'"{setting}"' not in SOURCE
    )

    assert not missing, (
        "Setup settings disappeared: "
        + ", ".join(missing)
    )


def test_join_leave_and_staff_aliases_remain():
    assert "JOIN_LEAVE_LOG_ALIASES" in SOURCE
    assert "STAFF_LOG_ALIASES" in SOURCE
    assert "also_same=JOIN_LEAVE_LOG_ALIASES" in SOURCE
    assert "also_same=STAFF_LOG_ALIASES" in SOURCE


def test_saved_message_uses_plain_next_step():
    assert "Choose another item" in SOURCE
    assert "or press **Setup Check**" in SOURCE
    assert "Run Health Check" not in SOURCE
