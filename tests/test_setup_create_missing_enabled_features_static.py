from pathlib import Path


DEFAULTS = Path(
    "stoney_verify/commands_ext/public_setup_defaults.py"
).read_text(encoding="utf-8")

RECOMMEND = Path(
    "stoney_verify/commands_ext/public_setup_recommend.py"
).read_text(encoding="utf-8")


def block(source: str, start: str, end: str) -> str:
    left = source.index(start)
    right = source.index(end, left)
    return source[left:right]


def test_defaults_has_authoritative_service_scope():
    scope = block(
        DEFAULTS,
        "def _service_scope_from_config(",
        "def _key(",
    )

    for key in (
        '"tickets_enabled"',
        '"verification_enabled"',
        '"basic_verify_enabled"',
        '"voice_verification_enabled"',
        '"spam_guard_enabled"',
        '"moderation_enabled"',
    ):
        assert key in scope

    assert 'choice == "basic_verify"' in scope
    assert "if voice or id_verify:" in scope
    assert "if spam_guard:" in scope


def test_callback_creates_each_feature_conditionally():
    callback = block(
        DEFAULTS,
        "async def _setup_defaults_callback(",
        "def _attach() -> None:",
    )

    assert 'if services["welcome"]:' in callback
    assert 'if services["verify"]:' in callback
    assert 'if services["tickets"]:' in callback
    assert 'if services["voice"]:' in callback
    assert 'if services["logs"]:' in callback

    assert "Nothing unrelated was created" in callback
    assert "Disabled features created nothing" in callback


def test_control_role_is_not_created_automatically():
    callback = block(
        DEFAULTS,
        "async def _setup_defaults_callback(",
        "def _attach() -> None:",
    )

    assert "DEFAULT_CONTROL_ROLE_NAME" not in callback
    assert "_resolve_existing_control_role" in callback


def test_required_items_are_feature_gated():
    callback = block(
        DEFAULTS,
        "async def _setup_defaults_callback(",
        "def _attach() -> None:",
    )

    required_start = callback.index(
        "required: list[tuple[str, Any]]"
    )

    required = callback[required_start:]

    assert 'if services["tickets"]:' in required
    assert 'if services["verify"]:' in required
    assert 'if services["voice"]:' in required
    assert 'if services["logs"]:' in required


def test_updates_do_not_write_disabled_feature_ids():
    callback = block(
        DEFAULTS,
        "async def _setup_defaults_callback(",
        "def _attach() -> None:",
    )

    updates_start = callback.index(
        "updates: dict[str, Any]"
    )

    updates = callback[updates_start:]

    assert 'if services["verify"]:' in updates
    assert 'if services["tickets"]:' in updates
    assert 'if services["voice"]:' in updates
    assert 'if services["logs"]:' in updates


def test_ticket_choices_are_seeded_only_for_tickets():
    create_missing = block(
        RECOMMEND,
        "async def _open_create_missing(",
        "async def _open_ticket_menu(",
    )

    ticket_gate = create_missing.index(
        'if not services["tickets"]:'
    )
    seed = create_missing.index(
        "solid._seed_recommended_categories"
    )

    assert ticket_gate < seed
    assert "handled is not True" in create_missing


def test_every_feature_is_still_supported():
    for marker in (
        "TICKET_CATEGORY_NAME",
        "VERIFY_CHANNEL_NAME",
        "VC_VERIFY_CHANNEL_NAME",
        "TRANSCRIPTS_CHANNEL_NAME",
        "MODLOG_CHANNEL_NAME",
        "JOIN_LEAVE_CHANNEL_NAME",
        "STATUS_CHANNEL_NAME",
    ):
        assert marker in DEFAULTS
