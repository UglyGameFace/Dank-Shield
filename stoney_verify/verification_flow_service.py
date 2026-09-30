from __future__ import annotations

"""Versioned verification-flow policy for issue #367 Slice 3.

This module owns only the flow definition and validation contract. Existing
verification runtimes continue to own execution and truth:
- role_truth owns verification/member-role truth;
- Basic/Voice/ID verification runtimes own their own Discord actions;
- Member Setup owns its own revision/completion truth;
- Access Gate owns effective channel/category access.

Keeping the policy model separate prevents the configurable framework from
becoming a second role/access authority.
"""

from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Sequence

VERIFICATION_FLOW_KEY = "verification_flow_v2"
VERIFICATION_FLOW_VERSION = 2
MAX_VERIFICATION_STEPS = 10
MAX_STEP_SETTINGS = 12

STEP_SIMPLE_VERIFY = "simple_verify"
STEP_RULES_ACK = "rules_ack"
STEP_MEMBER_SETUP = "member_setup"
STEP_ACCOUNT_AGE = "account_age"
STEP_MEMBERSHIP_DELAY = "membership_delay"
STEP_STAFF_APPROVAL = "staff_approval"
STEP_APPLICATION = "application"
STEP_VERIFICATION_TICKET = "verification_ticket"
STEP_VOICE_VERIFY = "voice_verify"
STEP_EXTERNAL_LINK = "external_link"

STEP_TYPES: frozenset[str] = frozenset(
    {
        STEP_SIMPLE_VERIFY,
        STEP_RULES_ACK,
        STEP_MEMBER_SETUP,
        STEP_ACCOUNT_AGE,
        STEP_MEMBERSHIP_DELAY,
        STEP_STAFF_APPROVAL,
        STEP_APPLICATION,
        STEP_VERIFICATION_TICKET,
        STEP_VOICE_VERIFY,
        STEP_EXTERNAL_LINK,
    }
)

PRESET_SIMPLE = "simple"
PRESET_STANDARD = "standard"
PRESET_GUARDED = "guarded"
PRESET_APPROVAL = "approval"
PRESET_APPLICATION = "application"
PRESET_CUSTOM = "custom"
PRESET_LEGACY = "legacy"

PRESETS: frozenset[str] = frozenset(
    {
        PRESET_SIMPLE,
        PRESET_STANDARD,
        PRESET_GUARDED,
        PRESET_APPROVAL,
        PRESET_APPLICATION,
        PRESET_CUSTOM,
        PRESET_LEGACY,
    }
)

CONTEXT_NEW_MEMBER = "new_member"
CONTEXT_RETURNING = "returning"
CONTEXT_TRUSTED = "trusted"
CONTEXT_MANUAL_REVIEW = "manual_review"
CONTEXTS: frozenset[str] = frozenset(
    {
        CONTEXT_NEW_MEMBER,
        CONTEXT_RETURNING,
        CONTEXT_TRUSTED,
        CONTEXT_MANUAL_REVIEW,
    }
)

FAIL_WAIT = "wait"
FAIL_LIMITED_ACCESS = "limited_access"
FAIL_STAFF_REVIEW = "staff_review"
FAIL_TICKET = "ticket"
FAIL_DENY = "deny"
FAILURE_ACTIONS: frozenset[str] = frozenset(
    {
        FAIL_WAIT,
        FAIL_LIMITED_ACCESS,
        FAIL_STAFF_REVIEW,
        FAIL_TICKET,
        FAIL_DENY,
    }
)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().casefold()
    if text in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disabled"}:
        return False
    return bool(default)


def _clean_key(value: Any, *, fallback: str = "") -> str:
    text = str(value or "").strip().lower().replace("_", "-").replace(" ", "-")
    text = "".join(ch for ch in text if ch.isalnum() or ch == "-")
    while "--" in text:
        text = text.replace("--", "-")
    return text.strip("-")[:64] or fallback


def _clean_label(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip()
    return text[:100] or fallback


def _normalize_settings(value: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Mapping):
        return ()
    items: list[tuple[str, str]] = []
    for raw_key, raw_value in value.items():
        key = _clean_key(raw_key)
        if not key:
            continue
        val = str(raw_value if raw_value is not None else "").strip()[:200]
        items.append((key, val))
        if len(items) >= MAX_STEP_SETTINGS:
            break
    return tuple(sorted(dict(items).items()))


def _settings_payload(settings: Iterable[tuple[str, str]]) -> dict[str, str]:
    return {str(key): str(value) for key, value in settings}


@dataclass(frozen=True)
class VerificationStep:
    key: str
    step_type: str
    label: str
    required: bool = True
    enabled: bool = True
    order: int = 0
    settings: tuple[tuple[str, str], ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "type": self.step_type,
            "label": self.label,
            "required": bool(self.required),
            "enabled": bool(self.enabled),
            "order": int(self.order),
            "settings": _settings_payload(self.settings),
        }


@dataclass(frozen=True)
class VerificationFlowConfig:
    revision: int = 1
    preset: str = PRESET_CUSTOM
    enabled: bool = False
    failure_action: str = FAIL_WAIT
    contexts: tuple[str, ...] = (CONTEXT_NEW_MEMBER,)
    steps: tuple[VerificationStep, ...] = ()
    source: str = "v2"

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": VERIFICATION_FLOW_VERSION,
            "revision": max(1, int(self.revision)),
            "preset": self.preset,
            "enabled": bool(self.enabled),
            "failure_action": self.failure_action,
            "contexts": list(self.contexts),
            "steps": [step.to_payload() for step in self.steps],
        }


def _step(
    key: str,
    step_type: str,
    label: str,
    order: int,
    *,
    settings: Mapping[str, Any] | None = None,
) -> VerificationStep:
    return VerificationStep(
        key=key,
        step_type=step_type,
        label=label,
        order=int(order),
        settings=_normalize_settings(settings or {}),
    )


def preset_flow(
    preset: str,
    *,
    revision: int = 1,
    enabled: bool = False,
) -> VerificationFlowConfig:
    """Return a bounded draft/active flow for a named product preset.

    Presets intentionally avoid inventing owner-specific thresholds. Steps such
    as account age remain structurally present with empty settings until the
    owner supplies a value; activation_blockers() catches that later.
    """
    wanted = str(preset or "").strip().lower()
    if wanted not in PRESETS - {PRESET_LEGACY}:
        raise ValueError(f"Unknown verification preset: {preset!r}")

    definitions: dict[str, tuple[VerificationStep, ...]] = {
        PRESET_SIMPLE: (
            _step("verify", STEP_SIMPLE_VERIFY, "Verify", 0),
        ),
        PRESET_STANDARD: (
            _step("rules", STEP_RULES_ACK, "Acknowledge Rules", 0),
            _step("verify", STEP_SIMPLE_VERIFY, "Verify", 1),
        ),
        PRESET_GUARDED: (
            _step("rules", STEP_RULES_ACK, "Acknowledge Rules", 0),
            _step("account-age", STEP_ACCOUNT_AGE, "Account Age", 1),
            _step("member-setup", STEP_MEMBER_SETUP, "Member Setup", 2),
            _step("verify", STEP_SIMPLE_VERIFY, "Verify", 3),
        ),
        PRESET_APPROVAL: (
            _step("rules", STEP_RULES_ACK, "Acknowledge Rules", 0),
            _step("member-setup", STEP_MEMBER_SETUP, "Member Setup", 1),
            _step("staff-approval", STEP_STAFF_APPROVAL, "Staff Approval", 2),
        ),
        PRESET_APPLICATION: (
            _step("application", STEP_APPLICATION, "Application", 0),
            _step("verification-ticket", STEP_VERIFICATION_TICKET, "Verification Ticket", 1),
            _step("staff-approval", STEP_STAFF_APPROVAL, "Staff Decision", 2),
        ),
        PRESET_CUSTOM: (),
    }
    failure = FAIL_STAFF_REVIEW if wanted in {PRESET_APPROVAL, PRESET_APPLICATION} else FAIL_WAIT
    return VerificationFlowConfig(
        revision=max(1, int(revision)),
        preset=wanted,
        enabled=bool(enabled),
        failure_action=failure,
        contexts=(CONTEXT_NEW_MEMBER,),
        steps=definitions[wanted],
        source="v2",
    )


def _step_from_raw(raw: Mapping[str, Any], index: int) -> VerificationStep | None:
    step_type = str(raw.get("type") or raw.get("step_type") or "").strip().lower()
    if step_type not in STEP_TYPES:
        return None
    key = _clean_key(raw.get("key"), fallback=f"step-{index + 1}")
    label = _clean_label(raw.get("label"), fallback=step_type.replace("_", " ").title())
    return VerificationStep(
        key=key,
        step_type=step_type,
        label=label,
        required=_safe_bool(raw.get("required"), True),
        enabled=_safe_bool(raw.get("enabled"), True),
        order=max(0, _safe_int(raw.get("order"), index)),
        settings=_normalize_settings(raw.get("settings")),
    )


def _dedupe_steps(steps: Iterable[VerificationStep]) -> tuple[VerificationStep, ...]:
    ordered = sorted(steps, key=lambda step: (int(step.order), step.key))
    out: list[VerificationStep] = []
    seen: set[str] = set()
    for step in ordered:
        if step.key in seen:
            continue
        seen.add(step.key)
        out.append(replace(step, order=len(out)))
        if len(out) >= MAX_VERIFICATION_STEPS:
            break
    return tuple(out)


def _legacy_flow(config: Mapping[str, Any]) -> VerificationFlowConfig:
    """Describe existing verification services without changing their authority."""
    try:
        from stoney_verify.setup_service_state import service_state_from_config

        state = service_state_from_config(config)
        simple = bool(getattr(state, "simple_verify", False))
        voice = bool(getattr(state, "voice_verify", False))
        id_verify = bool(getattr(state, "id_verify", False))
    except Exception:
        simple = _safe_bool(config.get("basic_verify_enabled") or config.get("verification_enabled"), False)
        voice = _safe_bool(config.get("voice_verification_enabled") or config.get("voice_verify_enabled"), False)
        id_verify = _safe_bool(config.get("id_verify_enabled") or config.get("id_web_verify_enabled"), False)

    steps: list[VerificationStep] = []
    if simple:
        steps.append(_step("legacy-simple", STEP_SIMPLE_VERIFY, "Simple Verify", len(steps)))
    if voice:
        steps.append(_step("legacy-voice", STEP_VOICE_VERIFY, "Voice Verify", len(steps)))
    if id_verify:
        steps.append(_step("legacy-ticket", STEP_VERIFICATION_TICKET, "Verification Ticket", len(steps)))
        steps.append(_step("legacy-approval", STEP_STAFF_APPROVAL, "Staff Approval", len(steps)))

    return VerificationFlowConfig(
        revision=1,
        preset=PRESET_LEGACY,
        enabled=bool(steps),
        failure_action=FAIL_WAIT,
        contexts=(CONTEXT_NEW_MEMBER,),
        steps=_dedupe_steps(steps),
        source="legacy" if steps else "empty",
    )


def parse_verification_flow(config: Mapping[str, Any]) -> VerificationFlowConfig:
    """Parse v2 when present; otherwise expose the current legacy configuration.

    Presence of verification_flow_v2 establishes v2 authority. Malformed v2
    therefore fails closed into a disabled v2 flow rather than silently
    resurrecting a legacy mode.
    """
    if VERIFICATION_FLOW_KEY not in config:
        return _legacy_flow(config)

    raw = config.get(VERIFICATION_FLOW_KEY)
    if not isinstance(raw, Mapping):
        return VerificationFlowConfig(source="v2")

    preset = str(raw.get("preset") or PRESET_CUSTOM).strip().lower()
    if preset not in PRESETS - {PRESET_LEGACY}:
        preset = PRESET_CUSTOM

    failure_action = str(raw.get("failure_action") or FAIL_WAIT).strip().lower()
    if failure_action not in FAILURE_ACTIONS:
        failure_action = FAIL_WAIT

    contexts_raw = raw.get("contexts")
    contexts: list[str] = []
    if isinstance(contexts_raw, Sequence) and not isinstance(contexts_raw, (str, bytes)):
        for value in contexts_raw:
            clean = str(value or "").strip().lower()
            if clean in CONTEXTS and clean not in contexts:
                contexts.append(clean)
    if not contexts:
        contexts = [CONTEXT_NEW_MEMBER]

    steps_raw = raw.get("steps")
    steps = _dedupe_steps(
        step
        for index, item in enumerate(
            steps_raw
            if isinstance(steps_raw, Sequence) and not isinstance(steps_raw, (str, bytes))
            else []
        )
        if isinstance(item, Mapping)
        for step in [_step_from_raw(item, index)]
        if step is not None
    )

    return VerificationFlowConfig(
        revision=max(1, _safe_int(raw.get("revision"), 1)),
        preset=preset,
        enabled=_safe_bool(raw.get("enabled"), False),
        failure_action=failure_action,
        contexts=tuple(contexts),
        steps=steps,
        source="v2",
    )


def next_revision(config: VerificationFlowConfig) -> int:
    return max(1, int(config.revision) + 1)


def replace_with_preset(
    config: VerificationFlowConfig,
    preset: str,
) -> VerificationFlowConfig:
    candidate = preset_flow(preset, revision=next_revision(config), enabled=False)
    equivalent = replace(candidate, revision=config.revision, source=config.source)
    if equivalent == config:
        return config
    return candidate


def validate_flow(config: VerificationFlowConfig) -> list[str]:
    errors: list[str] = []
    if int(config.revision) < 1:
        errors.append("revision must be positive")
    if config.preset not in PRESETS:
        errors.append("unknown preset")
    if config.failure_action not in FAILURE_ACTIONS:
        errors.append("unknown failure action")
    if not config.contexts:
        errors.append("at least one context is required")
    elif any(context not in CONTEXTS for context in config.contexts):
        errors.append("unknown context")
    if len(config.steps) > MAX_VERIFICATION_STEPS:
        errors.append("too many steps")

    keys = [step.key for step in config.steps]
    if len(keys) != len(set(keys)):
        errors.append("duplicate step key")

    for index, step in enumerate(config.steps):
        if step.step_type not in STEP_TYPES:
            errors.append(f"{step.key}: unknown step type")
        if not step.key:
            errors.append(f"step {index + 1}: missing key")
        if not step.label:
            errors.append(f"{step.key}: missing label")
        if len(step.settings) > MAX_STEP_SETTINGS:
            errors.append(f"{step.key}: too many settings")

    if config.enabled and not any(step.enabled and step.required for step in config.steps):
        errors.append("active flow has no required steps")

    return errors


def activation_blockers(
    config: VerificationFlowConfig,
    *,
    supported_step_types: Iterable[str],
) -> list[str]:
    """Return blockers before a saved draft may become the live policy.

    Runtime integrations explicitly pass the step types they support. This
    avoids claiming a preset is executable before its owning subsystems are
    wired into the framework.
    """
    blockers = list(validate_flow(config))
    supported = {str(value) for value in supported_step_types}

    required = [step for step in config.steps if step.enabled and step.required]
    if not required:
        blockers.append("flow has no enabled required steps")

    for step in required:
        if step.step_type not in supported:
            blockers.append(f"{step.label}: runtime integration is not available")
        if step.step_type == STEP_ACCOUNT_AGE:
            minimum_days = _safe_int(_settings_payload(step.settings).get("minimum_days"), 0)
            if minimum_days <= 0:
                blockers.append(f"{step.label}: minimum_days must be configured")
        if step.step_type == STEP_MEMBERSHIP_DELAY:
            minimum_minutes = _safe_int(_settings_payload(step.settings).get("minimum_minutes"), 0)
            if minimum_minutes <= 0:
                blockers.append(f"{step.label}: minimum_minutes must be configured")

    return list(dict.fromkeys(blockers))


__all__ = [
    "CONTEXTS",
    "CONTEXT_MANUAL_REVIEW",
    "CONTEXT_NEW_MEMBER",
    "CONTEXT_RETURNING",
    "CONTEXT_TRUSTED",
    "FAILURE_ACTIONS",
    "FAIL_DENY",
    "FAIL_LIMITED_ACCESS",
    "FAIL_STAFF_REVIEW",
    "FAIL_TICKET",
    "FAIL_WAIT",
    "MAX_VERIFICATION_STEPS",
    "PRESETS",
    "PRESET_APPLICATION",
    "PRESET_APPROVAL",
    "PRESET_CUSTOM",
    "PRESET_GUARDED",
    "PRESET_LEGACY",
    "PRESET_SIMPLE",
    "PRESET_STANDARD",
    "STEP_ACCOUNT_AGE",
    "STEP_APPLICATION",
    "STEP_EXTERNAL_LINK",
    "STEP_MEMBER_SETUP",
    "STEP_MEMBERSHIP_DELAY",
    "STEP_RULES_ACK",
    "STEP_SIMPLE_VERIFY",
    "STEP_STAFF_APPROVAL",
    "STEP_TYPES",
    "STEP_VERIFICATION_TICKET",
    "STEP_VOICE_VERIFY",
    "VERIFICATION_FLOW_KEY",
    "VERIFICATION_FLOW_VERSION",
    "VerificationFlowConfig",
    "VerificationStep",
    "activation_blockers",
    "next_revision",
    "parse_verification_flow",
    "preset_flow",
    "replace_with_preset",
    "validate_flow",
]
