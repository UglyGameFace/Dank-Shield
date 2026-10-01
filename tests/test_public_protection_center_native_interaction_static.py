from __future__ import annotations

from pathlib import Path

SOURCE = Path("stoney_verify/commands_ext/public_protection_center.py").read_text(encoding="utf-8")


def test_protection_center_imports_native_guard() -> None:
    assert "run_guarded_interaction" in SOURCE
    assert "log_interaction_failure" in SOURCE
    assert "safe_send_interaction" in SOURCE


def test_protection_center_command_uses_native_guard() -> None:
    command_block = SOURCE[SOURCE.index("async def protection_center") : SOURCE.index("def register_public_protection_center_commands")]
    assert "await run_guarded_interaction(" in command_block
    assert "print(" not in command_block
    assert "except Exception" not in command_block


def test_protection_center_buttons_use_guarded_actions() -> None:
    required_actions = [
        "protection.safe",
        "protection.strict",
        "protection.off",
        "protection.open_spamguard_editor",
        "protection.invite_blocker",
        "protection.link_shield",
        "protection.open_add_filter_modal",
        "protection.open_import_filter_pack_modal",
        "protection.import_filter_pack_modal",
        "protection.open_test_filter_modal",
        "protection.allow_links",
        "protection.refresh",
        "protection.close",
        "protection.spam_response_mode",
        "protection.spam_detection_modal",
        "protection.spam_action_modal",
    ]
    for action in required_actions:
        assert action in SOURCE


def test_protection_center_removed_legacy_local_open_error_prints() -> None:
    assert "public_protection_center open failed" not in SOURCE
    assert "failed to send Protection Center" not in SOURCE
    assert "Protection Center could not open safely" not in SOURCE


def test_protection_center_owns_import_pack_without_startup_patch() -> None:
    assert 'label="Import Pack"' in SOURCE
    assert 'custom_id="dank_protection:import_pack"' in SOURCE
    assert "StarterPackImportModal" in SOURCE
    assert "_merge_imported_filter_terms" in SOURCE
    assert "protection_pack_manual_import_guard" not in SOURCE
    assert "protection_import_button_patch" not in SOURCE

def test_protection_center_reports_invite_delete_health_and_v2_coverage() -> None:
    assert "def _invite_delete_health(" in SOURCE
    assert "**Live delete access here:**" in SOURCE
    assert "**Modern app cards:** visible Components V2 text is checked" in SOURCE
    assert "manage_messages" in SOURCE


def test_protection_center_close_removes_panel_instead_of_greying_it_out() -> None:
    start = SOURCE.index("async def close_button")
    end = SOURCE.index("@dank_group.command", start)
    block = SOURCE[start:end]

    assert "embed=None" in block
    assert "view=None" in block
    assert "child.disabled = True" not in block


def test_protection_refresh_acknowledges_and_shows_loading_before_state_reads() -> None:
    ack_start = SOURCE.index("async def _ack_protection_entry")
    ack_end = SOURCE.index("async def _show_protection_loading", ack_start)
    ack_block = SOURCE[ack_start:ack_end]
    assert "interaction.response.is_done()" in ack_block
    assert "interaction.response.defer" in ack_block

    start = SOURCE.index("async def _refresh_panel")
    end = SOURCE.index("def _normalize_spam_mode_for_ui", start)
    block = SOURCE[start:end]

    assert "await _ack_protection_entry(interaction)" in block
    assert "await _show_protection_loading(interaction)" in block
    assert "await _load_protection_panel_state(" in block
    assert "await asyncio.wait_for(" in block
    assert "interaction.edit_original_response(" in block
    assert "await _replace_protection_loading_with_error(" in block
    assert "await _refresh_security_stats_after_panel(interaction, guild)" in block
    assert block.index("await _ack_protection_entry(interaction)") < block.index(
        "await _show_protection_loading(interaction)"
    )
    assert block.index("await _show_protection_loading(interaction)") < block.index(
        "await _load_protection_panel_state("
    )
    assert block.rindex("interaction.edit_original_response(") < block.index(
        "await _refresh_security_stats_after_panel(interaction, guild)"
    )


def test_protection_state_load_is_bounded_and_degrades_fail_closed() -> None:
    start = SOURCE.index("async def _load_protection_panel_state")
    end = SOURCE.index("async def _refresh_security_stats_after_panel", start)
    block = SOURCE[start:end]

    assert "asyncio.wait_for(" in block
    assert "get_guild_config(int(guild_id), refresh=True)" in block
    assert "get_guild_config(int(guild_id), refresh=False)" in block
    assert '"enabled": False, "mode": "unknown"' in block
    assert "Settings controls are locked until live state loads." in block


def test_degraded_protection_view_only_leaves_retry_and_close_enabled() -> None:
    start = SOURCE.index("class ProtectionCenterView")
    end = SOURCE.index("@dank_group.command", start)
    block = SOURCE[start:end]

    assert "degraded: bool = False" in block
    assert 'custom_id not in {"dank_protection:refresh", "dank_protection:close"}' in block
    assert 'child.label = "Retry Live State"' in block


def test_direct_protection_command_reuses_canonical_refresh_owner() -> None:
    start = SOURCE.index("async def protection_center")
    end = SOURCE.index("def register_public_protection_center_commands", start)
    block = SOURCE[start:end]

    assert "await _refresh_panel(" in block
    assert "await get_guild_config(" not in block
    assert "await _load_spam_settings(" not in block


def test_protection_center_exposes_owner_only_restore_member_control() -> None:
    assert "class RestoreMemberModal" in SOURCE
    assert 'label="Restore Member"' in SOURCE
    assert 'custom_id="dank_protection:antinuke_restore_member"' in SOURCE
    assert "physical server owner" in SOURCE.lower()
    assert "clear_hostile_reputation_for_owner_intent" in SOURCE
