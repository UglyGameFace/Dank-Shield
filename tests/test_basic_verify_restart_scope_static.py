from pathlib import Path


GUARD = Path(
    "stoney_verify/startup_guards/"
    "basic_verify_panel_auto_refresh_guard.py"
).read_text(encoding="utf-8")

MODES = Path(
    "stoney_verify/setup_engine/verification_modes.py"
).read_text(encoding="utf-8")


def test_startup_refresh_checks_effective_guild_mode():
    assert "effective_verification_mode" in GUARD
    assert 'if mode != "basic_button":' in GUARD
    assert "skipped disabled Basic Verify refresh" in GUARD


def test_startup_refresh_never_guesses_channel_by_name():
    start = GUARD.index("def _verify_channel(")
    end = GUARD.index("def _can_scan", start)
    block = GUARD[start:end]

    assert "_channel_by_name(" not in block
    assert '"verify_channel_id"' in block
    assert '"verification_channel_id"' in block
    assert "if cid <= 0:" in block
    assert "return None" in block


def test_id_upload_requires_both_configuration_and_allowlist():
    assert "config_requests_id_verify(cfg)" in MODES
    assert "id_verify_allowed_for_guild(guild, cfg)" in MODES
    assert 'return "id_verify"' in MODES
    assert 'return "basic_button"' in MODES


def test_disabled_or_unconfigured_guild_is_not_modified_on_restart():
    load = GUARD.index("cfg = await _load_cfg(guild)")
    scan = GUARD.index("if not _can_scan(channel):", load)
    block = GUARD[load:scan]

    mode_check = block.index('if mode != "basic_button":')
    channel_check = block.index(
        "if not isinstance(channel, discord.TextChannel):"
    )

    assert mode_check < channel_check
    assert "return False" in block[mode_check:channel_check]
