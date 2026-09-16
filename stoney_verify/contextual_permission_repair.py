from __future__ import annotations

"""Shared same-screen permission repair contract for configuration menus.

Feature menus should diagnose access and repair it from the same surface instead
of making administrators leave the menu to hunt through Discord permissions.
This module intentionally delegates actual overwrite mutation, retry handling,
explicit-deny safety, post-repair auditing, and undo/event recording to the
canonical :mod:`stoney_verify.permission_repair_core` owner.
"""

from dataclasses import dataclass, field
from typing import Any, Iterable

import discord

from . import permission_repair_core as core


@dataclass(frozen=True)
class ContextualRepairTarget:
    channel_id: int
    feature: str = "general"
    label: str = "Configured channel"


@dataclass
class ContextualTargetAudit:
    target: ContextualRepairTarget
    audit: core.TargetPermissionAudit | None = None
    missing_target: bool = False
    note: str = ""

    @property
    def missing(self) -> list[str]:
        return list(getattr(self.audit, "missing", []) or []) if self.audit is not None else []

    @property
    def blockers(self) -> list[str]:
        return list(getattr(self.audit, "blockers", []) or []) if self.audit is not None else []

    @property
    def repairable(self) -> list[str]:
        return list(getattr(self.audit, "repairable_missing", []) or []) if self.audit is not None else []


@dataclass
class ContextualRepairAudit:
    targets: list[ContextualTargetAudit] = field(default_factory=list)
    manual_issues: list[str] = field(default_factory=list)

    @property
    def permission_issue_count(self) -> int:
        return sum(1 for item in self.targets if item.missing_target or bool(item.missing))

    @property
    def repairable_count(self) -> int:
        return sum(1 for item in self.targets if bool(item.repairable) and not item.blockers)

    @property
    def healthy(self) -> bool:
        return self.permission_issue_count == 0 and not self.manual_issues

    @property
    def needs_attention(self) -> bool:
        return not self.healthy


@dataclass
class ContextualRepairResult:
    ok: bool
    changed_targets: list[str] = field(default_factory=list)
    failed_targets: list[str] = field(default_factory=list)
    remaining_issues: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    after: ContextualRepairAudit | None = None

    def summary(self) -> str:
        lines: list[str] = []
        if self.changed_targets:
            lines.append(f"✅ Repaired {len(self.changed_targets)} configured target(s).")
        elif not self.failed_targets:
            lines.append("No safe permission changes were needed.")
        if self.failed_targets:
            lines.append(f"⚠️ {len(self.failed_targets)} target(s) could not be repaired automatically.")
        if self.remaining_issues:
            lines.append("Remaining: " + " • ".join(self.remaining_issues[:4]))
        if self.ok and not self.remaining_issues:
            lines.append("✅ Access re-check passed.")
        return "\n".join(lines)[:1000]


def normalize_targets(targets: Iterable[ContextualRepairTarget]) -> tuple[ContextualRepairTarget, ...]:
    out: list[ContextualRepairTarget] = []
    seen: set[tuple[int, str]] = set()
    for raw in targets:
        channel_id = int(getattr(raw, "channel_id", 0) or 0)
        feature = str(getattr(raw, "feature", "general") or "general").strip().lower()
        if channel_id <= 0:
            continue
        key = (channel_id, feature)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            ContextualRepairTarget(
                channel_id=channel_id,
                feature=feature,
                label=str(getattr(raw, "label", "Configured channel") or "Configured channel"),
            )
        )
    return tuple(out)


def audit_context(
    guild: discord.Guild,
    targets: Iterable[ContextualRepairTarget],
    *,
    manual_issues: Iterable[str] = (),
) -> ContextualRepairAudit:
    rows: list[ContextualTargetAudit] = []
    for target in normalize_targets(targets):
        channel = guild.get_channel(int(target.channel_id))
        if not isinstance(channel, discord.abc.GuildChannel):
            rows.append(
                ContextualTargetAudit(
                    target=target,
                    missing_target=True,
                    note=f"{target.label}: saved channel is missing or no longer visible to Dank Shield.",
                )
            )
            continue
        rows.append(
            ContextualTargetAudit(
                target=target,
                audit=core.audit_target(
                    guild,
                    channel,
                    feature=target.feature,
                    mode="minimum",
                ),
            )
        )
    return ContextualRepairAudit(
        targets=rows,
        manual_issues=[str(item).strip() for item in manual_issues if str(item).strip()],
    )


def repair_button_state(audit: ContextualRepairAudit) -> tuple[str, str, discord.ButtonStyle, bool]:
    if audit.healthy:
        return "Access Healthy", "✅", discord.ButtonStyle.secondary, True
    if audit.repairable_count > 0:
        return "Fix Issues", "🛠️", discord.ButtonStyle.danger, False
    return "Manual Fix Needed", "⚠️", discord.ButtonStyle.secondary, False


def remaining_issue_lines(audit: ContextualRepairAudit) -> list[str]:
    out: list[str] = []
    for item in audit.targets:
        if item.missing_target:
            out.append(item.note or f"{item.target.label}: configured target is missing.")
            continue
        if item.audit is None or not item.missing:
            continue
        if item.blockers:
            out.append(f"{item.target.label}: " + " ".join(item.blockers))
        else:
            out.append(f"{item.target.label}: still missing " + ", ".join(item.missing))
    out.extend(audit.manual_issues)
    return out


async def repair_context(
    guild: discord.Guild,
    targets: Iterable[ContextualRepairTarget],
    *,
    actor_id: int,
    manual_issues: Iterable[str] = (),
) -> ContextualRepairResult:
    normalized = normalize_targets(targets)
    before = audit_context(guild, normalized, manual_issues=manual_issues)
    result = ContextualRepairResult(ok=True)

    by_key = {
        (item.target.channel_id, item.target.feature): item
        for item in before.targets
    }
    for target in normalized:
        row = by_key.get((target.channel_id, target.feature))
        if row is None or row.missing_target or row.audit is None or not row.missing:
            continue
        if row.blockers:
            result.failed_targets.append(f"{target.label}: " + " ".join(row.blockers))
            result.ok = False
            continue
        if not row.repairable:
            # Most commonly an explicit deny. The canonical core intentionally
            # preserves those until an administrator explicitly confirms it.
            result.failed_targets.append(
                f"{target.label}: Discord has an explicit deny that safe repair will not clear automatically."
            )
            result.ok = False
            continue

        channel = guild.get_channel(int(target.channel_id))
        if not isinstance(channel, discord.abc.GuildChannel):
            result.failed_targets.append(f"{target.label}: configured channel disappeared before repair.")
            result.ok = False
            continue

        repaired = await core.apply_target_repair(
            guild,
            channel,
            actor_id=int(actor_id),
            feature=target.feature,
            mode="minimum",
            include_children=False,
            clear_explicit_denies=False,
        )
        result.changed_targets.extend(repaired.changed_targets)
        result.failed_targets.extend(repaired.failed_targets)
        result.notes.extend(repaired.notes)
        if not repaired.ok:
            result.ok = False

    after = audit_context(guild, normalized, manual_issues=manual_issues)
    result.after = after
    result.remaining_issues = remaining_issue_lines(after)
    if result.remaining_issues:
        result.ok = False
    return result


__all__ = [
    "ContextualRepairTarget",
    "ContextualTargetAudit",
    "ContextualRepairAudit",
    "ContextualRepairResult",
    "normalize_targets",
    "audit_context",
    "repair_button_state",
    "remaining_issue_lines",
    "repair_context",
]
