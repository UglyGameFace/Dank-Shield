from __future__ import annotations

from pathlib import Path

CENTER = Path("stoney_verify/commands_ext/public_verify_command_center.py").read_text(encoding="utf-8")
GROUP = Path("stoney_verify/commands_ext/public_verify_group.py").read_text(encoding="utf-8")


def _invoke_region() -> str:
    start = CENTER.index("async def _invoke(")
    end = CENTER.index("async def _require_staff", start)
    return CENTER[start:end]


def test_verify_center_dispatcher_uses_native_interaction_guard() -> None:
    region = _invoke_region()

    assert "from stoney_verify.interaction_guard import run_guarded_interaction" in CENTER
    assert "async def action() -> None:" in region
    assert "await run_guarded_interaction(" in region
    assert "defer=True" in region
    assert 'action_name=f"verify.center.{callback_name}"' in region
    assert "Verification action stopped unexpectedly" in region
    assert "inspect the member/server verification state before retrying" in region


def test_verify_center_dispatcher_preserves_callable_validation_and_arguments() -> None:
    region = _invoke_region()

    assert 'callback = getattr(command, "callback", command)' in region
    assert "if not callable(callback):" in region
    assert 'raise RuntimeError("Verification action is unavailable")' in region
    assert "result = await callback(interaction, *args, **kwargs)" in region


def test_verify_center_dispatcher_preserves_callback_result() -> None:
    region = _invoke_region()

    assert "result: Any = None" in region
    assert "nonlocal result" in region
    assert "result = await callback(interaction, *args, **kwargs)" in region
    assert "return result" in region


def test_verify_center_action_name_is_derived_from_canonical_callback() -> None:
    region = _invoke_region()

    assert 'getattr(callback, "__name__", None)' in region
    assert 'getattr(command, "name", None)' in region
    assert 'or "action"' in region
    assert '.strip().lower().replace(" ", "_")' in region


def test_canonical_verify_ack_tolerates_predeferred_interaction() -> None:
    start = GROUP.index("async def _ack")
    end = GROUP.index("async def _send", start)
    ack = GROUP[start:end]

    assert "if not interaction.response.is_done():" in ack
    assert "await interaction.response.defer(" in ack


def test_center_mutation_callers_still_route_through_shared_dispatcher() -> None:
    for command_name in (
        "verify_repair_unverified",
        "verify_grant_vr",
        "verify_fix_member",
        "verify_set_verified",
        "verify_set_resident",
    ):
        assert command_name in CENTER
    assert CENTER.count("await _invoke(") >= 7
