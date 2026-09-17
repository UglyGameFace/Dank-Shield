from __future__ import annotations

"""Same-screen permission repair for exact Protection Center resources.

This integration deliberately owns no Discord permission mutation. It maps the
persisted Live Stats category/counter IDs into the shared permission-repair core
and leaves AntiNuke/server-level readiness blockers manual.
"""

from dataclasses import dataclass, field
from typing import Any, Mapping

import discord

from .. import permission_repair_core as core
from ..globals import bot
from ..security_stats import (
    SECURITY_STATS_CATEGORY_ID_KEY,
    SECURITY_STATS_CHANNEL_IDS_KEY,
    SECURITY_STATS_ENABLED_KEY,
    STAT_CHANNEL_PREFIXES,
)
from . import public_protection_center as center

_PATCHED = False
_ORIGINAL_VIEW: type[discord.ui.View] | None = None
_VERIFIED_AUDIT: dict[int, "ProtectionStatsAudit"] = {}


@dataclass(frozen=True)
class ProtectionStatsTarget:
    channel_id: int
    label: str
    feature: str
    mode: str


@dataclass
class ProtectionStatsRow:
    spec: ProtectionStatsTarget
    channel: discord.abc.GuildChannel | None = None
    audit: core.TargetPermissionAudit | None = None
    missing_target: bool = False


@dataclass
class ProtectionStatsAudit:
    rows: list[ProtectionStatsRow] = field(default_factory=list)
    manual_issues: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        if not self.rows:
            return False
        return not self.manual_issues and all(
            row.audit is not None and not row.audit.missing and not row.missing_target
            for row in self.rows
        )

    @property
    def repairable_count(self) -> int:
        if self.manual_issues:
            # Server-level prerequisites can prevent every overwrite write. Keep
            # the button manual rather than advertising an autofix that cannot run.
            return 0
        return sum(
            1
            for row in self.rows
            if row.audit is not None and row.audit.can_apply and not row.missing_target
        )


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    return default


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
    if value is None:
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _guild_for_cfg(cfg: Any) -> discord.Guild | None:
    gid = _safe_int(_cfg_value(cfg, "guild_id", 0), 0)
    if gid <= 0:
        return None
    try:
        guild = bot.get_guild(gid)
    except Exception:
        guild = None
    return guild if isinstance(guild, discord.Guild) else None


def _stats_enabled(cfg: Any) -> bool:
    return _safe_bool(_cfg_value(cfg, SECURITY_STATS_ENABLED_KEY, False), False)


def _target_specs(cfg: Any) -> tuple[ProtectionStatsTarget, ...]:
    if not _stats_enabled(cfg):
        return ()

    out: list[ProtectionStatsTarget] = []
    seen: set[int] = set()
    category_id = _safe_int(_cfg_value(cfg, SECURITY_STATS_CATEGORY_ID_KEY, 0), 0)
    if category_id > 0:
        # The dedicated stats category needs channel-management capability so
        # the product can keep its owned counter channels grouped and updated.
        out.append(
            ProtectionStatsTarget(
                category_id,
                "Live Stats category",
                "tickets",
                "minimum",
            )
        )
        seen.add(category_id)

    saved = _mapping(_cfg_value(cfg, SECURITY_STATS_CHANNEL_IDS_KEY, {}))
    for key in STAT_CHANNEL_PREFIXES:
        channel_id = _safe_int(saved.get(key), 0)
        if channel_id <= 0 or channel_id in seen:
            continue
        seen.add(channel_id)
        label = str(key).replace("_", " ").title()
        # Voice-channel full mode is intentionally narrow in the shared core:
        # view_channel + manage_channels + move_members. It does not grant
        # Administrator or alter any member/@everyone overwrite.
        out.append(
            ProtectionStatsTarget(
                channel_id,
                f"Live Stats {label} counter",
                "general",
                "full",
            )
        )
    return tuple(out)


def _manual_prerequisites(guild: discord.Guild, cfg: Any) -> list[str]:
    if not _stats_enabled(cfg):
        return []
    out: list[str] = []
    me = getattr(guild, "me", None)
    if not isinstance(me, discord.Member):
        return ["Live Stats: Dank Shield could not resolve its server member record."]

    perms = getattr(me, "guild_permissions", None)
    if perms is None:
        return ["Live Stats: Dank Shield could not evaluate its server permissions."]
    if not bool(getattr(perms, "manage_channels", False)):
        out.append("Live Stats: server-level Manage Channels is required to create/rename the counter display.")
    if not (
        bool(getattr(perms, "manage_roles", False))
        or bool(getattr(perms, "administrator", False))
    ):
        out.append("Live Stats: server-level Manage Roles / Manage Permissions is required to maintain counter overwrites.")

    category_id = _safe_int(_cfg_value(cfg, SECURITY_STATS_CATEGORY_ID_KEY, 0), 0)
    if category_id <= 0:
        out.append("Live Stats: the saved stats category mapping is missing. Use Live Stats setup to rebuild it.")

    saved = _mapping(_cfg_value(cfg, SECURITY_STATS_CHANNEL_IDS_KEY, {}))
    missing_keys = [key for key in STAT_CHANNEL_PREFIXES if _safe_int(saved.get(key), 0) <= 0]
    if missing_keys:
        out.append(
            "Live Stats: one or more saved counter mappings are missing. Use Live Stats setup to rebuild the owned display."
        )
    return out


def audit_protection_stats(
    guild: discord.Guild,
    cfg: Any,
    *,
    resolved: Mapping[int, discord.abc.GuildChannel | None] | None = None,
) -> ProtectionStatsAudit:
    rows: list[ProtectionStatsRow] = []
    for spec in _target_specs(cfg):
        channel = (
            resolved.get(spec.channel_id)
            if resolved is not None
            else guild.get_channel(spec.channel_id)
        )
        if not isinstance(channel, discord.abc.GuildChannel):
            rows.append(
                ProtectionStatsRow(
                    spec=spec,
                    missing_target=True,
                )
            )
            continue
        rows.append(
            ProtectionStatsRow(
                spec=spec,
                channel=channel,
                audit=core.audit_target(
                    guild,
                    channel,
                    feature=spec.feature,
                    mode=spec.mode,
                ),
            )
        )

    manual = _manual_prerequisites(guild, cfg)
    for row in rows:
        if row.missing_target:
            manual.append(f"{row.spec.label}: saved Discord target is missing or unavailable.")
    return ProtectionStatsAudit(rows=rows, manual_issues=list(dict.fromkeys(manual)))


async def audit_protection_stats_fresh(guild: discord.Guild, cfg: Any) -> ProtectionStatsAudit:
    resolved: dict[int, discord.abc.GuildChannel | None] = {}
    fetch_channel = getattr(guild, "fetch_channel", None)
    for spec in _target_specs(cfg):
        fresh: Any = None
        if callable(fetch_channel):
            try:
                fresh = await fetch_channel(spec.channel_id)
            except Exception:
                fresh = None
        if not isinstance(fresh, discord.abc.GuildChannel):
            fresh = guild.get_channel(spec.channel_id)
        resolved[spec.channel_id] = fresh if isinstance(fresh, discord.abc.GuildChannel) else None
    return audit_protection_stats(guild, cfg, resolved=resolved)


def _remaining_lines(audit: ProtectionStatsAudit) -> list[str]:
    lines = list(audit.manual_issues)
    for row in audit.rows:
        if row.missing_target or row.audit is None or not row.audit.missing:
            continue
        if row.audit.blockers:
            lines.append(f"{row.spec.label}: " + " ".join(row.audit.blockers))
        elif row.audit.explicit_denies:
            lines.append(
                f"{row.spec.label}: explicit Dank Shield deny preserved for "
                + ", ".join(row.audit.explicit_denies)
                + "."
            )
        else:
            lines.append(f"{row.spec.label}: still missing " + ", ".join(row.audit.missing))
    return list(dict.fromkeys(lines))


def _button_state(audit: ProtectionStatsAudit) -> tuple[str, str, discord.ButtonStyle, bool]:
    if audit.healthy:
        return "Access Healthy", "✅", discord.ButtonStyle.secondary, True
    if audit.repairable_count > 0:
        return "Fix Issues", "🛠️", discord.ButtonStyle.danger, False
    return "Manual Fix Needed", "⚠️", discord.ButtonStyle.secondary, False


async def repair_protection_stats(
    guild: discord.Guild,
    cfg: Any,
    *,
    actor_id: int,
) -> tuple[ProtectionStatsAudit, list[str], list[str]]:
    before = await audit_protection_stats_fresh(guild, cfg)
    changed: list[str] = []
    failed: list[str] = []
    if before.manual_issues:
        return before, changed, failed

    for row in before.rows:
        if row.channel is None or row.audit is None or not row.audit.missing:
            continue
        if not row.audit.can_apply:
            failed.append(f"{row.spec.label}: " + " ".join(row.audit.blockers or row.audit.warnings))
            continue
        result = await core.apply_target_repair(
            guild,
            row.channel,
            actor_id=int(actor_id),
            feature=row.spec.feature,
            mode=row.spec.mode,
            include_children=False,
            clear_explicit_denies=False,
        )
        changed.extend(result.changed_targets)
        failed.extend(result.failed_targets)

    after = await audit_protection_stats_fresh(guild, cfg)
    return after, changed, failed


class ProtectionAccessButton(discord.ui.Button):
    def __init__(self, *, author_id: int, guild: discord.Guild, cfg: Any, audit: ProtectionStatsAudit) -> None:
        label, emoji, style, disabled = _button_state(audit)
        super().__init__(
            label=label,
            emoji=emoji,
            style=style,
            disabled=disabled,
            custom_id="dank_protection:fix_access",
            row=4,
        )
        self.author_id = int(author_id)
        self.guild = guild
        self.cfg = cfg
        self.audit = audit

    async def callback(self, interaction: discord.Interaction) -> None:
        if int(getattr(interaction.user, "id", 0) or 0) != self.author_id:
            await center._send_ephemeral(interaction, "❌ This Protection Center belongs to another admin.")
            return
        if not await center._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            await center._send_ephemeral(interaction, "❌ This must be used inside a server.")
            return

        cfg = await center.get_guild_config(int(guild.id), refresh=True)
        audit = await audit_protection_stats_fresh(guild, cfg)
        if audit.healthy:
            _VERIFIED_AUDIT[int(guild.id)] = audit
            await center._refresh_panel(interaction, content="✅ Protection Live Stats access is healthy.")
            return
        if audit.repairable_count <= 0:
            _VERIFIED_AUDIT[int(guild.id)] = audit
            text = "\n".join(f"• {line}" for line in _remaining_lines(audit)[:6])
            await center._refresh_panel(
                interaction,
                content=("⚠️ Manual fix needed for Protection Live Stats.\n" + text)[:1800],
            )
            return

        after, changed, failed = await repair_protection_stats(
            guild,
            cfg,
            actor_id=int(interaction.user.id),
        )
        _VERIFIED_AUDIT[int(guild.id)] = after
        remaining = _remaining_lines(after)
        parts: list[str] = []
        if changed:
            parts.append(f"✅ Repaired {len(changed)} Protection target(s).")
        if failed:
            parts.append(f"⚠️ {len(failed)} target(s) could not be repaired automatically.")
        if remaining:
            parts.append("Remaining: " + " • ".join(remaining[:4]))
        if after.healthy:
            parts.append("✅ Access re-check passed.")
        await center._refresh_panel(
            interaction,
            content="\n".join(parts)[:1800] or "Protection access re-check finished.",
        )


def apply_protection_contextual_permission_repair() -> bool:
    global _PATCHED, _ORIGINAL_VIEW
    if _PATCHED:
        return True

    BaseView = center.ProtectionCenterView
    if not isinstance(BaseView, type) or not issubclass(BaseView, discord.ui.View):
        return False
    _ORIGINAL_VIEW = BaseView

    class ContextualProtectionCenterView(BaseView):
        def __init__(self, *, author_id: int, cfg: Any | None = None, spam: dict[str, Any] | None = None) -> None:
            super().__init__(author_id=author_id, cfg=cfg, spam=spam)
            if cfg is None or not _stats_enabled(cfg):
                return
            guild = _guild_for_cfg(cfg)
            if guild is None:
                return
            gid = int(guild.id)
            audit = _VERIFIED_AUDIT.pop(gid, None) or audit_protection_stats(guild, cfg)
            self.add_item(
                ProtectionAccessButton(
                    author_id=int(author_id),
                    guild=guild,
                    cfg=cfg,
                    audit=audit,
                )
            )

    ContextualProtectionCenterView.__name__ = "ContextualProtectionCenterView"
    ContextualProtectionCenterView.__qualname__ = "ContextualProtectionCenterView"
    center.ProtectionCenterView = ContextualProtectionCenterView
    _PATCHED = True
    return True


__all__ = [
    "ProtectionAccessButton",
    "ProtectionStatsAudit",
    "ProtectionStatsTarget",
    "apply_protection_contextual_permission_repair",
    "audit_protection_stats",
    "audit_protection_stats_fresh",
    "repair_protection_stats",
]
