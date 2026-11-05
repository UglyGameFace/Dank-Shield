from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
health = (ROOT / "stoney_verify/startup_guards/process_health.py").read_text()


def test_ack_trace_only_when_ingress_trace_enabled() -> None:
    assert "if trace_enabled and not _INTERACTION_TRACE_LISTENERS_ATTACHED:" in health
    assert "asyncio.create_task(_trace_interaction_ack(interaction, label)" in health


def test_ack_trace_does_not_claim_or_retry_interactions() -> None:
    section = health.split("async def _trace_interaction_ack(", 1)[1].split("async def _process_health_on_interaction(", 1)[0]
    assert "interaction.response.is_done()" in section
    assert "INTERACTION_ACK_LATE" in section
    assert "interaction.response.defer(" not in section
    assert "interaction.response.send_message(" not in section
    assert "interaction.followup.send(" not in section


def test_ack_trace_has_pre_deadline_checkpoints() -> None:
    assert "for checkpoint in (1.5, 2.5):" in health
    assert "elapsed=" in health
