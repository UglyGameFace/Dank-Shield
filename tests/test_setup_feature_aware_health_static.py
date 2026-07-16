from pathlib import Path


SOURCE = Path(
    "stoney_verify/commands_ext/public_setup_recommend.py"
).read_text(encoding="utf-8")


def block(start: str, end: str) -> str:
    left = SOURCE.index(start)
    right = SOURCE.index(end, left)
    return SOURCE[left:right]


def test_one_service_truth_helper_exists():
    helper = block(
        "def _selected_setup_services(",
        "def _missing_setup_permissions(",
    )

    assert '"tickets_enabled"' in helper
    assert '"ticket_service_enabled"' in helper
    assert '"basic_verify_enabled"' in helper
    assert '"verification_enabled"' in helper
    assert '"voice_verification_enabled"' in helper
    assert '"spam_guard_enabled"' in helper
    assert '"moderation_enabled"' in helper
    assert 'choice == "basic_verify"' in helper


def test_permissions_follow_enabled_features():
    helper = block(
        "def _missing_setup_permissions(",
        "async def _build_plain_setup_health_embed(",
    )

    assert 'if services["tickets"]:' in helper
    assert 'if services["verify"]:' in helper
    assert 'if services["voice"]:' in helper
    assert 'if services["spam_guard"]:' in helper


def test_health_does_not_require_disabled_tickets():
    health = block(
        "async def _build_plain_setup_health_embed(",
        "def _build_setup_help_embed()",
    )

    ticket_gate = health.index(
        'if services["tickets"]:'
    )
    staff_check = health.index(
        '"staff_role_id"',
        ticket_gate,
    )
    category_check = health.index(
        '"ticket_category_id"',
        ticket_gate,
    )
    ticket_menu = health.index(
        "category_load = await solid._category_load",
        ticket_gate,
    )

    assert ticket_gate < staff_check
    assert ticket_gate < category_check
    assert ticket_gate < ticket_menu
    assert "Tickets are OFF" in health


def test_basic_verify_is_checked_without_id_or_voice():
    health = block(
        "async def _build_plain_setup_health_embed(",
        "def _build_setup_help_embed()",
    )

    assert 'if services["verify"]:' in health
    assert '"verify_channel_id"' in health
    assert '"verified_role_id"' in health
    assert "Verification is OFF" in health


def test_id_web_verify_remains_guild_gated():
    health = block(
        "async def _build_plain_setup_health_embed(",
        "def _build_setup_help_embed()",
    )

    assert 'services["id"]' in health
    assert "id_verify_allowed_for_guild(guild)" in health
    assert "Choose Basic Verify or Voice Verify" in health


def test_progress_matches_health_service_gates():
    progress = block(
        "async def _setup_progress(",
        "async def _product_main_setup_payload(",
    )

    assert 'if services["tickets"]:' in progress
    assert 'if services["verify"]:' in progress
    assert 'if services["voice"]:' in progress
    assert 'if services["id"]:' in progress
    assert 'if services["logs"]:' in progress
    assert "_missing_setup_permissions(" in progress


def test_no_feature_choice_cannot_claim_ready():
    health = block(
        "async def _build_plain_setup_health_embed(",
        "def _build_setup_help_embed()",
    )

    progress = block(
        "async def _setup_progress(",
        "async def _product_main_setup_payload(",
    )

    assert "if setup_choice and not any_service:" in health
    assert '"At least one feature"' in progress
