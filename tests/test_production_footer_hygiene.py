from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "stoney_verify"

BANNED_LITERAL_MARKERS = (
    "dank_shield:",
    "stoney_verify:",
    "canonical live runtime",
    "config source:",
    "per-process, per-guild",
)
BANNED_ID_EXPRESSIONS = (
    "guild.id",
    "guild_id",
    "member.guild.id",
    "member.id",
    "user.id",
    "channel.id",
    "message.id",
    "report.guild_id",
)


def _direct_footer_text_expressions() -> list[tuple[Path, int, str]]:
    rows: list[tuple[Path, int, str]] = []
    for path in PACKAGE.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "set_footer":
                continue
            for keyword in node.keywords:
                if keyword.arg != "text":
                    continue
                segment = ast.get_source_segment(source, keyword.value) or ""
                rows.append((path, int(getattr(node, "lineno", 0) or 0), segment))
    return rows


def test_direct_production_footers_do_not_embed_debug_tokens_or_raw_ids() -> None:
    failures: list[str] = []
    for path, line, expression in _direct_footer_text_expressions():
        lowered = expression.casefold()
        for marker in BANNED_LITERAL_MARKERS:
            if marker.casefold() in lowered:
                failures.append(f"{path.relative_to(ROOT)}:{line}: {expression}")
        for marker in BANNED_ID_EXPRESSIONS:
            if marker.casefold() in lowered:
                failures.append(f"{path.relative_to(ROOT)}:{line}: {expression}")

    assert failures == []


def test_welcome_and_verification_emit_human_footers_with_legacy_compatibility() -> None:
    welcome = (PACKAGE / "welcome_message.py").read_text(encoding="utf-8")
    verify = (PACKAGE / "verify_ui.py").read_text(encoding="utf-8")
    basic_modes = (PACKAGE / "setup_engine/verification_modes.py").read_text(encoding="utf-8")
    basic_verify = (PACKAGE / "verification_new/basic_verify.py").read_text(encoding="utf-8")

    assert 'WELCOME_FOOTER = "Welcome • start here"' in welcome
    assert 'LEGACY_WELCOME_FOOTERS = ("dank_shield:welcome_message:v1",)' in welcome
    assert "any(marker in footer for marker in LEGACY_WELCOME_FOOTERS)" in welcome

    assert 'VERIFY_UI_FOOTER = "Verification • secure access"' in verify
    assert 'LEGACY_VERIFY_UI_FOOTERS = ("stoney_verify:verify_ui:v9",)' in verify
    assert "any(marker in footer_text for marker in LEGACY_VERIFY_UI_FOOTERS)" in verify

    assert 'BASIC_VERIFY_FOOTER = "Dank Shield Basic Verify"' in basic_modes
    assert 'LEGACY_BASIC_VERIFY_FOOTERS = ("dank_shield:basic_verify:v1",)' in basic_modes
    assert "LEGACY_BASIC_VERIFY_FOOTERS" in basic_verify


def test_spamguard_incident_footer_uses_durable_message_ownership_not_visible_ids() -> None:
    source = (PACKAGE / "spam_guard.py").read_text(encoding="utf-8")

    incident_block = source.split("def _incident_footer", 1)[1].split(
        "def _parse_incident_footer", 1
    )[0]
    assert '"Spam Guard incident • restore available"' in incident_block
    assert "|case=" not in incident_block
    assert "get_quarantine_case_by_modlog_message(" in source

    callback = source.split("class SpamIncidentRestoreButton", 1)[1].split(
        "class SpamIncidentRestoreView", 1
    )[0]
    durable = callback.index("get_quarantine_case_by_modlog_message(")
    legacy = callback.index("_parse_incident_footer(")
    assert durable < legacy


def test_live_profile_footer_hides_ids_but_keeps_legacy_parser() -> None:
    source = (PACKAGE / "profile_card_runtime_core.py").read_text(encoding="utf-8")

    footer_block = source.split("def live_card_footer", 1)[1].split(
        "def _live_card_footer_text", 1
    )[0]
    assert "return LIVE_CARD_FOOTER_PREFIX" in footer_block
    assert "user:" not in footer_block
    assert "trigger:" not in footer_block
    assert "_LEGACY_LIVE_CARD_FOOTER_RE" in source


def test_ticket_runtime_markers_are_human_readable_with_legacy_aliases() -> None:
    source = (PACKAGE / "transcripts.py").read_text(encoding="utf-8")
    new_service = (PACKAGE / "tickets_new/transcript_service.py").read_text(encoding="utf-8")

    assert '_OPEN_CONTROLS_MARKER = "Ticket controls"' in source
    assert '_STAFF_CLOSED_MARKER = "Ticket closed by staff"' in source
    assert '_STAFF_REVIEW_PANEL_MARKER = "Staff ticket review"' in source
    assert '_TRANSCRIPT_POSTED_MARKER = "Transcript posted"' in source
    assert "_LEGACY_MARKERS" in source
    assert '"stoney_verify:open_controls:v5"' in source
    assert '_TRANSCRIPT_MARKER = "Transcript posted"' in new_service



def test_public_control_center_and_db_check_footers_hide_operator_metadata() -> None:
    control_surface = (PACKAGE / "commands_ext/public_command_surface_v2.py").read_text(encoding="utf-8")
    setup_review = (PACKAGE / "commands_ext/public_setup_review.py").read_text(encoding="utf-8")

    assert 'embed.set_footer(text="Dank Shield • Home › choose an area")' in control_surface
    assert "runtime_release_label" not in control_surface
    assert 'embed.set_footer(text="Read-only check • no settings were changed.")' in setup_review
    assert "refresh Supabase REST schema cache" not in setup_review
    assert "service-role env vars" not in setup_review


def test_known_developer_footer_phrases_are_removed() -> None:
    forbidden = {
        "welcome_card_studio_ui.py": (
            "canonical live runtime",
            "Preview fallback • dank_shield:welcome_card_runtime:v1",
        ),
        "exit_card_studio_ui.py": ("dank_shield:exit_card_runtime:v1",),
        "commands_ext/public_diagnostics_group.py": (
            "diagnostics are per-process, per-guild",
        ),
        "commands_ext/public_modlog_group.py": ("Uses existing modlog_channel_id.",),
        "commands_ext/public_protection_center.py": ("overlapping config bucket",),
        "commands_ext/public_setup_group.py": ("config source:",),
    }

    failures: list[str] = []
    for relative, phrases in forbidden.items():
        source = (PACKAGE / relative).read_text(encoding="utf-8")
        for phrase in phrases:
            if phrase in source:
                failures.append(f"{relative}: {phrase}")

    assert failures == []
