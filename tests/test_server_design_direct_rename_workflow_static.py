from pathlib import Path


SOURCE = Path("stoney_verify/startup_guards/server_design_studio_command_guard.py").read_text()


def test_rename_copy_says_direct_and_no_apply():
    assert "Rename applies immediately. No Apply button appears after Rename." in SOURCE
    assert "Applied immediately. No Apply button is needed after Rename." in SOURCE
    assert "Discord result:" in SOURCE

    assert "Rename is instant • Preview/Change One Style/Custom Format use Apply later" in SOURCE
    assert "Rename is instant • Preview/Style Change/Exact Format use Apply later" not in SOURCE


def test_rename_has_refresh_buttons():
    assert 'custom_id="dank_design:category_action_refresh"' in SOURCE
    assert 'custom_id="dank_design:channel_action_refresh"' in SOURCE
    assert "async def refresh_category" in SOURCE
    assert "async def refresh_channel" in SOURCE


def test_rename_refetches_live_discord_result():
    assert "async def _direct_rename_fetch_target" in SOURCE
    assert "Discord result:" in SOURCE
    assert "Discord returned a different final name" in SOURCE


def test_hash_keycap_is_blocked_for_channel_rename_not_category_copy():
    assert "def _direct_rename_has_unsafe_channel_icon" in SOURCE
    assert "`#️⃣` and square placeholder icons can break into blocks" in SOURCE
    assert "Pick a real emoji/icon, or use it on a category only." in SOURCE
