from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _function_block(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_spam_guard_rejects_bot_messages_before_settings_or_counters():
    source = (ROOT / "stoney_verify/spam_guard.py").read_text(encoding="utf-8")
    block = _function_block(
        source,
        "async def handle_incoming_spam_message",
        "\n\nasync def ",
    )

    bot_guard = block.index('if getattr(message.author, "bot", False):')
    settings_read = block.index("settings = await get_spam_settings")
    recent_messages = block.index("recent_messages")
    assert bot_guard < settings_read < recent_messages


def test_spam_cleanup_sweep_rejects_bot_messages_before_burst_memory():
    source = (ROOT / "stoney_verify/commands_ext/public_spam_cleanup_hardening.py").read_text(
        encoding="utf-8"
    )
    predicate = _function_block(source, "def _should_track_message", "\n\nasync def ")
    listener = _function_block(source, "async def _post_enforcement_sweep_listener", "\n\ndef ")

    assert "if message.author.bot:" in predicate
    assert predicate.index("if message.author.bot:") < predicate.index("_spam_guard_enabled")
    assert listener.index("if not _should_track_message(message):") < listener.index("_BURST_WINDOWS")


def test_automod_and_activity_tracking_reject_bot_messages_before_work():
    automod = (ROOT / "stoney_verify/startup_guards/automod_public_guard.py").read_text(
        encoding="utf-8"
    )
    activity = (ROOT / "stoney_verify/members_new/activity_tracker.py").read_text(
        encoding="utf-8"
    )
    automod_block = _function_block(automod, "async def _automod_message_listener", "\n\ndef ")
    activity_block = _function_block(activity, "async def _on_message", "\n\nasync def ")

    assert automod_block.index('if getattr(message.author, "bot", False):') < automod_block.index(
        "get_guild_config"
    )
    assert activity_block.index('if bool(getattr(author, "bot", False)):') < activity_block.index(
        "record_direct_member_activity"
    )


def test_raidguard_has_no_message_listener_for_signature_output():
    source = (ROOT / "stoney_verify/raidguard.py").read_text(encoding="utf-8")

    assert '@bot.listen("on_message")' not in source
    assert 'add_listener(' not in source or '"on_message"' not in source
