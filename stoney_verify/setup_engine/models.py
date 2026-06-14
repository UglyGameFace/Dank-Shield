from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional


class FindingSeverity(str, Enum):
    BLOCKER = "blocker"
    WARNING = "warning"
    INFO = "info"
    PASS = "pass"


class RecommendedAction(str, Enum):
    NONE = "none"
    FIX_PERMISSIONS = "fix_permissions"
    PICK_EXISTING = "pick_existing"
    MOVE_CHANNEL = "move_channel"
    MOVE_ROLE = "move_role"
    CREATE_MISSING = "create_missing"
    MANUAL_REVIEW = "manual_review"


@dataclass(frozen=True)
class SetupConfigSnapshot:
    guild_id: int
    setup_type: str = ""

    server_control_role_id: int = 0
    staff_role_id: int = 0
    vc_staff_role_id: int = 0
    unverified_role_id: int = 0
    verified_role_id: int = 0
    resident_role_id: int = 0
    member_role_id: int = 0

    onboarding_category_id: int = 0
    welcome_channel_id: int = 0
    rules_channel_id: int = 0
    announcements_channel_id: int = 0
    verify_channel_id: int = 0
    vc_verify_channel_id: int = 0
    vc_queue_channel_id: int = 0
    ticket_panel_channel_id: int = 0
    ticket_category_id: int = 0
    archive_category_id: int = 0
    staff_tools_category_id: int = 0
    transcript_channel_id: int = 0
    modlog_channel_id: int = 0
    join_leave_log_channel_id: int = 0
    bot_status_channel_id: int = 0

    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def effective_staff_role_id(self) -> int:
        return self.staff_role_id or self.vc_staff_role_id

    @property
    def effective_member_role_id(self) -> int:
        return self.resident_role_id or self.member_role_id or self.verified_role_id

    @property
    def uses_verified_as_member(self) -> bool:
        return bool(self.verified_role_id and self.effective_member_role_id == self.verified_role_id)

    @property
    def saved_onboarding_channel_ids(self) -> set[int]:
        return {x for x in {self.welcome_channel_id, self.rules_channel_id, self.announcements_channel_id, self.verify_channel_id, self.vc_verify_channel_id, self.ticket_panel_channel_id} if x > 0}

    @property
    def saved_private_channel_ids(self) -> set[int]:
        return {x for x in {self.ticket_category_id, self.archive_category_id, self.staff_tools_category_id, self.transcript_channel_id, self.modlog_channel_id, self.join_leave_log_channel_id, self.bot_status_channel_id, self.vc_queue_channel_id} if x > 0}


@dataclass(frozen=True)
class LiveTarget:
    id: int
    name: str
    kind: str
    mention: str
    parent_id: int = 0
    parent_name: str = ""
    is_category: bool = False
    is_text: bool = False
    is_voice: bool = False


@dataclass(frozen=True)
class RepairMutation:
    target_id: int
    target_label: str
    subject_id: int
    subject_label: str
    action: str
    reason: str
    overwrite: Mapping[str, Optional[bool]] = field(default_factory=dict)


@dataclass(frozen=True)
class RepairPlan:
    id: str
    fixes_finding_ids: tuple[str, ...]
    label: str
    mutations: tuple[RepairMutation, ...] = ()
    manual_steps: tuple[str, ...] = ()
    risk: str = "low"
    can_apply: bool = True
    cannot_apply_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class HealthFinding:
    id: str
    severity: FindingSeverity
    title: str
    observed: str
    expected: str
    affected_target_ids: tuple[int, ...] = ()
    recommended_action: RecommendedAction = RecommendedAction.NONE
    repairable: bool = False
    repair_plan_id: str = ""
    manual_steps: tuple[str, ...] = ()
    proof_after_repair: str = ""

    def legacy_line(self) -> str:
        action = ""
        if self.repairable and self.recommended_action == RecommendedAction.FIX_PERMISSIONS:
            action = " Smart fix: **Safety & Repair → Fix Permissions**."
        elif self.manual_steps:
            action = " Manual fix: " + " ".join(self.manual_steps[:2])
        return f"{self.title}: {self.observed} Expected: {self.expected}.{action}"


@dataclass(frozen=True)
class SetupHealthReport:
    guild_id: int
    engine_version: str
    config: SetupConfigSnapshot
    findings: tuple[HealthFinding, ...]
    repair_plans: tuple[RepairPlan, ...] = ()
    ok_lines: tuple[str, ...] = ()

    @property
    def blockers(self) -> list[HealthFinding]:
        return [f for f in self.findings if f.severity == FindingSeverity.BLOCKER]

    @property
    def warnings(self) -> list[HealthFinding]:
        return [f for f in self.findings if f.severity == FindingSeverity.WARNING]

    @property
    def passes(self) -> list[HealthFinding]:
        return [f for f in self.findings if f.severity == FindingSeverity.PASS]
