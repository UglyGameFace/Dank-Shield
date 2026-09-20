from __future__ import annotations

from pathlib import Path

CENTER = Path("stoney_verify/commands_ext/public_ticket_command_center.py").read_text(encoding="utf-8")
COMMON = Path("stoney_verify/commands_ext/common.py").read_text(encoding="utf-8")


def _runner_region() -> str:
    start = CENTER.index("async def _run_ticket_command(")
    end = CENTER.index("class TicketChannelPicker", start)
    return CENTER[start:end]


def test_ticket_center_runner_uses_native_interaction_guard() -> None:
    region = _runner_region()

    assert "from stoney_verify.interaction_guard import run_guarded_interaction" in CENTER
    assert "async def action() -> None:" in region
    assert "await _run_ticket_command_action(" in region
    assert "await run_guarded_interaction(" in region
    assert "defer=True" in region
    assert 'action_name=f"ticket.center.{name}"' in region
    assert "Ticket action stopped unexpectedly" in region
    assert "inspect the ticket's current state" in region
    assert "before retrying. Use the Error ID" in region


def test_ticket_center_runner_preserves_authorization_and_canonical_dispatch() -> None:
    region = _runner_region()
    body_start = region.index("async def _run_ticket_command_action")
    body = region[body_start:]

    for required in (
        "await _require_staff(interaction)",
        "await _authorize_ticket_command(interaction, command_name=name, channel=channel)",
        "from .public_ticket_group import ticket_group",
        "ticket_group.get_command(name)",
        "await _invoke(command, interaction, channel=channel, **kwargs)",
    ):
        assert required in body

    assert body.index("_require_staff") < body.index("_authorize_ticket_command")
    assert body.index("_authorize_ticket_command") < body.index("ticket_group.get_command")
    assert body.index("ticket_group.get_command") < body.index("await _invoke")


def test_ticket_center_runner_keeps_dedicated_flow_typeerror_but_not_broad_exception_swallow() -> None:
    region = _runner_region()
    body = region[region.index("async def _run_ticket_command_action"):]

    assert "except TypeError:" in body
    assert "needs more information" in body
    assert "except Exception" not in body
    assert "Ticket action failed safely:" not in body


def test_canonical_ticket_response_helpers_tolerate_predeferred_interaction() -> None:
    defer_start = COMMON.index("async def safe_defer")
    defer_end = COMMON.index("async def safe_followup", defer_start)
    safe_defer = COMMON[defer_start:defer_end]

    reply_start = COMMON.index("async def reply_once")
    reply_end = COMMON.index("def ensure_runtime_stat_keys", reply_start)
    reply_once = COMMON[reply_start:reply_end]

    assert "if not interaction.response.is_done():" in safe_defer
    assert "await interaction.response.defer(" in safe_defer

    assert "if interaction.response.is_done():" in reply_once
    assert "await interaction.followup.send(**payload)" in reply_once
    assert "await interaction.response.send_message(**payload)" in reply_once
