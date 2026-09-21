from __future__ import annotations

from pathlib import Path

DIAGNOSTICS = Path("stoney_verify/commands_ext/public_diagnostics_group.py").read_text(encoding="utf-8")


def _failure_helper_region() -> str:
    start = DIAGNOSTICS.index("def _safe_failure_token")
    end = DIAGNOSTICS.index("def _yes_no", start)
    return DIAGNOSTICS[start:end]


def test_diagnostics_reads_native_failure_ring_and_filters_by_current_guild() -> None:
    helper = _failure_helper_region()

    assert "recent_interaction_failures(limit=250)" in helper
    assert 'getattr(getattr(record, "context", None), "guild_id", 0)' in helper
    assert "== current_guild_id" in helper
    assert "latest = list(reversed(matched[-5:]))" in helper
    assert "Newest first • current server • current process" in helper


def test_diagnostics_failure_summary_renders_only_sanitized_safe_fields() -> None:
    helper = _failure_helper_region()

    for allowed in (
        '"error_id"',
        '"action_name"',
        '"stage"',
        '"error_type"',
        '"sent_to_user"',
    ):
        assert allowed in helper

    for forbidden in (
        "error_message",
        "fix_hint",
        "traceback_text",
        '"extra"',
        '"user_id"',
        '"channel_id"',
        '"message_id"',
    ):
        assert forbidden not in helper

    assert '.replace("\\r", " ")' in helper
    assert '.replace("\\n", " ")' in helper
    assert 'replace("`", "\'")' in helper


def test_diagnostics_failure_summary_stays_within_discord_field_limit() -> None:
    helper = _failure_helper_region()

    assert "limit=850" in helper
    assert "[:1000]" in helper
    assert "✅ None recorded for this server in this process." in helper


def test_diagnostics_embed_wires_current_guild_failure_summary_read_only() -> None:
    assert "Recent Native Interaction Failures" in DIAGNOSTICS
    assert "_native_interaction_failure_field(int(interaction.guild.id))" in DIAGNOSTICS
    assert "interaction_failure_summary=interaction_failure_summary" in DIAGNOSTICS
    assert "recent_interaction_failures" in DIAGNOSTICS

    # The diagnostics surface remains read-only: it reads the ring but has no
    # failure-clear or mutation call.
    assert "clear_recent_interaction_failures" not in DIAGNOSTICS
