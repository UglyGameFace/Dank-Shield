from __future__ import annotations

"""Action Center Slice 4B: per-guild DRAFT rules and truthful private previews.

Policy defines what staff would show when an authoritative owner reports a
pending action. It does not change verification/access rules, send reminders,
scan guild members, or install background workers. All drafts are disabled for
delivery until a future explicit, separately reviewed scheduler slice.
"""

from dataclasses import dataclass, replace
from typing import Any, Mapping

ACTION_BUILDER_KEY = "action_reminder_builder_v1"
ACTION_BUILDER_VERSION = 1

# These are existing completion-truth owners, NOT user-supplied expressions.
CONDITIONS: tuple[str, ...] = ("pending_verification", "member_setup_revision")
AUDIENCE = "pending_member"  # No unrestricted "all members" targeting.
DELIVERY = "preview_only"
SCHEDULE = "manual_preview"
MAX_NOTE_LENGTH = 160
MAX_RULES = 2
LEVELS = ("required", "recommended")


def _nonnegative(value: Any) -> int:
    try:
        if isinstance(value, bool):
            return 0
        return max(0, min(int(value), 2_147_483_647))
    except (ValueError, TypeError, OverflowError):
        return 0


def _note(value: Any) -> str:
    candidate = str(value or "").strip().replace("\n", " ").replace("\r", " ")
    if len(candidate) > MAX_NOTE_LENGTH:
        raise ValueError("Notes must be 160 characters or less.")
    # Embeds escape mentions at render time as well. Don't accept live role
    # mention syntax in staff-set informational notes.
    if "@everyone" in candidate.casefold() or "@here" in candidate.casefold():
        raise ValueError("Mass mentions are not allowed in action notes.")
    return candidate


@dataclass(frozen=True)
class DraftActionRule:
    condition: str
    included: bool = False
    severity: str = "recommended"
    note: str = ""
    audience: str = AUDIENCE
    schedule: str = SCHEDULE
    delivery: str = DELIVERY

    def __post_init__(self) -> None:
        if self.condition not in CONDITIONS:
            raise ValueError("Rules must use an existing authoritative action condition")
        if self.severity not in LEVELS:
            raise ValueError("Invalid action severity")
        if self.audience != AUDIENCE or self.schedule != SCHEDULE or self.delivery != DELIVERY:
            raise ValueError("Automated delivery and arbitrary audiences are unavailable in this slice")
        if _note(self.note) != self.note:
            raise ValueError("Action note must be normalized")
        if self.condition == "pending_verification" and self.severity != "required":
            raise ValueError("Verification requirements cannot be downgraded")

    def to_payload(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "included": self.included,
            "severity": self.severity,
            "note": self.note,
            "audience": self.audience,
            "schedule": self.schedule,
            "delivery": self.delivery,
        }


@dataclass(frozen=True)
class ActionBuilderDraft:
    revision: int = 0
    rules: tuple[DraftActionRule, ...] = ()

    def __post_init__(self) -> None:
        if len(self.rules) > MAX_RULES or len({r.condition for r in self.rules}) != len(self.rules):
            raise ValueError("Too many or duplicate rules")

    def rule_for(self, condition: str) -> DraftActionRule:
        if condition not in CONDITIONS:
            raise ValueError("Unrecognized rule condition")
        return next((r for r in self.rules if r.condition == condition), default_rule(condition))

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": ACTION_BUILDER_VERSION,
            "revision": int(self.revision),
            "delivery_enabled": False,  # Important: draft is NEVER a scheduler switch.
            "rules": [r.to_payload() for r in self.rules],
        }


def default_rule(condition: str) -> DraftActionRule:
    if condition not in CONDITIONS:
        raise ValueError("Unknown action condition")
    return DraftActionRule(
        condition=condition,
        severity="required" if condition == "pending_verification" else "recommended",
    )


def parse_action_builder(raw_config: Mapping[str, Any]) -> ActionBuilderDraft:
    blob = raw_config.get(ACTION_BUILDER_KEY)
    if blob is None:
        return ActionBuilderDraft()
    if not isinstance(blob, Mapping) or blob.get("version") != ACTION_BUILDER_VERSION:
        raise ValueError("Unknown draft-rule schema. Refusing to overwrite it.")
    found: dict[str, DraftActionRule] = {}
    raw_rules = blob.get("rules")
    if not isinstance(raw_rules, list) or len(raw_rules) > MAX_RULES:
        raise ValueError("Unrecognized action-rule configuration; refusing to overwrite it")
    if blob.get("delivery_enabled") is not False:
        raise ValueError("Unexpected live delivery flag: this is a draft-only rule format")
    for raw in raw_rules:
        if not isinstance(raw, Mapping):
            raise ValueError("Malformed action-rule definition")
        condition = str(raw.get("condition") or "")
        if condition in found or condition not in CONDITIONS:
            raise ValueError("Duplicate or unknown action condition")
        # Round-trip exact allowed types; fail closed on a future schema rather
        # than silently turning strange objects into True.
        if type(raw.get("included")) is not bool:
            raise ValueError("Invalid action inclusion flag")
        rule = DraftActionRule(
            condition=condition,
            included=raw["included"],
            severity=str(raw.get("severity") or ""),
            note=_note(raw.get("note")),
            audience=str(raw.get("audience") or ""),
            schedule=str(raw.get("schedule") or ""),
            delivery=str(raw.get("delivery") or ""),
        )
        found[condition] = rule
    return ActionBuilderDraft(
        revision=_nonnegative(blob.get("revision")),
        rules=tuple(found[key] for key in CONDITIONS if key in found),
    )


def edit_rule(
    draft: ActionBuilderDraft, *, condition: str, included: bool,
    severity: str, note: str,
) -> ActionBuilderDraft:
    if type(included) is not bool:
        raise ValueError("Included must be on or off")
    new = DraftActionRule(
        condition=condition,
        included=included,
        severity=str(severity),
        note=_note(note),
    )
    rules = {rule.condition: rule for rule in draft.rules}
    rules[condition] = new
    return replace(
        draft,
        revision=draft.revision + 1,
        rules=tuple(rules[key] for key in CONDITIONS if key in rules),
    )


async def load_action_builder(guild_id: int, *, refresh: bool = True) -> tuple[Mapping[str, Any], ActionBuilderDraft]:
    from .guild_config import get_guild_config
    if int(guild_id) <= 0:
        raise ValueError("Guild required")
    cfg = await get_guild_config(guild_id, refresh=refresh)
    if not isinstance(cfg, Mapping) or str(cfg.get("source") or "").startswith("unavailable:"):
        raise RuntimeError("Guild configuration is unavailable")
    return cfg, parse_action_builder(cfg)


async def save_action_builder(
    guild_id: int, *, expected_config: Mapping[str, Any], draft: ActionBuilderDraft,
) -> tuple[bool, Mapping[str, Any]]:
    from .guild_config import compare_and_swap_guild_config_key
    if int(guild_id) <= 0:
        raise ValueError("Guild required")
    current = parse_action_builder(expected_config)
    if draft.revision != current.revision + 1:
        raise ValueError("Rule revision conflict: reopen the builder")
    applied, saved = await compare_and_swap_guild_config_key(
        guild_id,
        ACTION_BUILDER_KEY,
        expected=expected_config.get(ACTION_BUILDER_KEY),
        value=draft.to_payload(),
        source="action_reminder_center_draft",
    )
    return bool(applied), saved


@dataclass(frozen=True)
class RulePreview:
    condition: str
    included: bool
    status: str
    note: str = ""
    severity: str = "recommended"


def preview_rules(draft: ActionBuilderDraft, snapshot: Any) -> tuple[RulePreview, ...]:
    """Preview the requesting staff member only; do not scan/sweep the guild.

    Unknown source truth is shown as unavailable even when a draft rule is off.
    Completion always comes from the original owner, not the draft policy.
    """
    route_by_condition = {
        "pending_verification": ("verification", "verification"),
        "member_setup_revision": ("member_setup", "member_setup"),
    }
    items = {item.route: item for item in snapshot.items}
    unknown = set(snapshot.unavailable_sources)
    results = []
    for condition in CONDITIONS:
        rule = draft.rule_for(condition)
        source, route = route_by_condition[condition]
        item = items.get(route)
        status = (
            "unavailable" if source in unknown
            else "pending" if item is not None
            else "complete"
        )
        effective = (
            "required" if item is not None and item.severity == "required"
            else rule.severity
        )
        results.append(RulePreview(
            condition=condition, included=rule.included,
            status=status, note=rule.note, severity=effective,
        ))
    return tuple(results)
