from __future__ import annotations

from dataclasses import replace

from stoney_verify.verification_flow_service import (
    CONTEXT_NEW_MEMBER,
    PRESET_APPLICATION,
    PRESET_APPROVAL,
    PRESET_GUARDED,
    PRESET_LEGACY,
    PRESET_SIMPLE,
    PRESET_STANDARD,
    STEP_ACCOUNT_AGE,
    STEP_APPLICATION,
    STEP_MEMBER_SETUP,
    STEP_RULES_ACK,
    STEP_SIMPLE_VERIFY,
    STEP_STAFF_APPROVAL,
    STEP_VERIFICATION_TICKET,
    STEP_VOICE_VERIFY,
    VERIFICATION_FLOW_KEY,
    VerificationFlowConfig,
    VerificationStep,
    activation_blockers,
    parse_verification_flow,
    preset_flow,
    replace_with_preset,
    validate_flow,
)


def _types(config: VerificationFlowConfig) -> list[str]:
    return [step.step_type for step in config.steps]


def test_product_presets_have_stable_ordered_steps() -> None:
    assert _types(preset_flow(PRESET_SIMPLE)) == [STEP_SIMPLE_VERIFY]
    assert _types(preset_flow(PRESET_STANDARD)) == [
        STEP_RULES_ACK,
        STEP_SIMPLE_VERIFY,
    ]
    assert _types(preset_flow(PRESET_GUARDED)) == [
        STEP_RULES_ACK,
        STEP_ACCOUNT_AGE,
        STEP_MEMBER_SETUP,
        STEP_SIMPLE_VERIFY,
    ]
    assert _types(preset_flow(PRESET_APPROVAL)) == [
        STEP_RULES_ACK,
        STEP_MEMBER_SETUP,
        STEP_STAFF_APPROVAL,
    ]
    assert _types(preset_flow(PRESET_APPLICATION)) == [
        STEP_APPLICATION,
        STEP_VERIFICATION_TICKET,
        STEP_STAFF_APPROVAL,
    ]


def test_presets_are_drafts_by_default_and_do_not_invent_thresholds() -> None:
    guarded = preset_flow(PRESET_GUARDED)
    assert guarded.enabled is False
    account_age = next(step for step in guarded.steps if step.step_type == STEP_ACCOUNT_AGE)
    assert dict(account_age.settings) == {}


def test_legacy_simple_voice_and_id_config_is_described_without_v2() -> None:
    simple = parse_verification_flow(
        {
            "setup_choice": "custom_setup",
            "verification_enabled": True,
            "basic_verify_enabled": True,
        }
    )
    assert simple.source == "legacy"
    assert simple.preset == PRESET_LEGACY
    assert STEP_SIMPLE_VERIFY in _types(simple)

    voice = parse_verification_flow(
        {
            "setup_choice": "custom_setup",
            "voice_verification_enabled": True,
        }
    )
    assert voice.source == "legacy"
    assert STEP_VOICE_VERIFY in _types(voice)

    id_flow = parse_verification_flow(
        {
            "setup_choice": "custom_setup",
            "id_verify_enabled": True,
        }
    )
    assert id_flow.source == "legacy"
    assert _types(id_flow) == [STEP_VERIFICATION_TICKET, STEP_STAFF_APPROVAL]


def test_v2_presence_is_authoritative_even_when_payload_is_malformed() -> None:
    config = parse_verification_flow(
        {
            VERIFICATION_FLOW_KEY: "corrupt",
            "basic_verify_enabled": True,
            "verification_enabled": True,
        }
    )
    assert config.source == "v2"
    assert config.enabled is False
    assert config.steps == ()


def test_v2_parser_bounds_dedupes_and_normalizes_steps() -> None:
    raw_steps = [
        {
            "key": "Rules",
            "type": STEP_RULES_ACK,
            "label": "Rules",
            "order": 10,
        },
        {
            "key": "Rules",
            "type": STEP_SIMPLE_VERIFY,
            "label": "Duplicate key",
            "order": 0,
        },
        {
            "key": "Verify",
            "type": STEP_SIMPLE_VERIFY,
            "label": "Verify",
            "order": 20,
        },
    ]
    config = parse_verification_flow(
        {
            VERIFICATION_FLOW_KEY: {
                "version": 2,
                "revision": 4,
                "preset": PRESET_STANDARD,
                "enabled": False,
                "contexts": [CONTEXT_NEW_MEMBER],
                "steps": raw_steps,
            }
        }
    )
    assert config.revision == 4
    assert [step.key for step in config.steps] == ["rules", "verify"]
    assert [step.order for step in config.steps] == [0, 1]


def test_active_flow_requires_real_required_steps() -> None:
    config = VerificationFlowConfig(
        revision=1,
        preset=PRESET_SIMPLE,
        enabled=True,
        contexts=(CONTEXT_NEW_MEMBER,),
        steps=(),
    )
    assert "active flow has no required steps" in validate_flow(config)


def test_activation_blockers_are_explicit_about_unwired_steps_and_thresholds() -> None:
    guarded = preset_flow(PRESET_GUARDED)
    blockers = activation_blockers(
        guarded,
        supported_step_types={
            STEP_RULES_ACK,
            STEP_ACCOUNT_AGE,
            STEP_MEMBER_SETUP,
            STEP_SIMPLE_VERIFY,
        },
    )
    assert "Account Age: minimum_days must be configured" in blockers

    unsupported = activation_blockers(
        preset_flow(PRESET_STANDARD),
        supported_step_types={STEP_SIMPLE_VERIFY},
    )
    assert "Acknowledge Rules: runtime integration is not available" in unsupported


def test_replace_with_preset_bumps_revision_and_never_activates_implicitly() -> None:
    current = replace(preset_flow(PRESET_SIMPLE), revision=7, enabled=True)
    updated = replace_with_preset(current, PRESET_APPROVAL)
    assert updated.revision == 8
    assert updated.preset == PRESET_APPROVAL
    assert updated.enabled is False


def test_payload_round_trip_preserves_policy_shape() -> None:
    original = replace(preset_flow(PRESET_APPROVAL), revision=3)
    parsed = parse_verification_flow({VERIFICATION_FLOW_KEY: original.to_payload()})
    assert parsed.revision == 3
    assert parsed.preset == PRESET_APPROVAL
    assert parsed.enabled is False
    assert parsed.contexts == (CONTEXT_NEW_MEMBER,)
    assert _types(parsed) == _types(original)


def test_validation_rejects_unknown_types_in_programmatic_configs() -> None:
    config = VerificationFlowConfig(
        revision=1,
        preset=PRESET_SIMPLE,
        enabled=False,
        contexts=(CONTEXT_NEW_MEMBER,),
        steps=(
            VerificationStep(
                key="mystery",
                step_type="mystery_step",
                label="Mystery",
            ),
        ),
    )
    assert "mystery: unknown step type" in validate_flow(config)
