from __future__ import annotations

"""Selected channel/category permission repair for Dank Shield.

The selected-target repair is intentionally narrow:
- the admin chooses one exact target;
- only Dank Shield's own missing overwrite bits are changed;
- unrelated role/member visibility is preserved;
- explicit denies require a separate confirmation;
- category children are opt-in;
- every applied change records an undo snapshot;
- reauthorization requests the approved non-Administrator permission set.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import discord

from .operation_queue import run_interaction_exclusive, with_retry

try:
    from .globals import get_supabase
except Exception:
    get_supabase = None  # type: ignore


_APPROVED_PUBLIC_GUILD_PERMISSIONS = (
    "kick_members",
    "ban_members",
    "manage_channels",
    "manage_roles",
    "view_audit_log",
    "view_channel",
    "send_messages",
    "send_messages_in_threads",
    "embed_links",
    "attach_files",
    "read_message_history",
    "manage_threads",
    "manage_messages",
    "moderate_members",
    "move_members",
)

_FEATURE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "general": ("view_channel", "send_messages", "embed_links", "attach_files", "read_message_history"),
    "tickets": (
        "view_channel",
        "send_messages",
        "send_messages_in_threads",
        "embed_links",
        "attach_files",
        "read_message_history",
        "manage_channels",
        "manage_messages",
        "manage_threads",
    ),
    "moderation": ("view_channel", "send_messages", "embed_links", "read_message_history", "manage_messages"),
    "logs": ("view_channel", "send_messages", "embed_links", "attach_files", "read_message_history"),
    "welcome": ("view_channel", "send_messages", "embed_links", "attach_files", "read_message_history"),
    "activity": ("view_channel", "read_message_history", "manage_threads"),
    "community": (
        "view_channel",
        "send_messages",
        "send_messages_in_threads",
        "embed_links",
        "attach_files",
        "read_message_history",
        "manage_messages",
        "manage_threads",
    ),
}

_FEATURE_LABELS = {
    "general": "General access",
    "tickets": "Tickets",
    "moderation": "Moderation",
    "logs": "Logs",
    "welcome": "Welcome",
    "activity": "Activity",
    "community": "Community Tools",
}

_FULL_CHANNEL_PERMISSIONS = (
    "view_channel",
    "send_messages",
    "send_messages_in_threads",
    "embed_links",
    "attach_files",
    "read_message_history",
    "manage_channels",
    "manage_messages",
    "manage_threads",
    "move_members",
)

_UNDO_MEMORY: dict[str, dict[str, Any]] = {}
_UNDO_TTL_SECONDS = 24 * 60 * 60


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text or default
    except Exception:
        return default


def _feature_label(feature: str) -> str:
    return _FEATURE_LABELS.get(_safe_str(feature, "general").lower(), "General access")


def _mode_label(mode: str) -> str:
    return "Full Dank Shield control" if _safe_str(mode).lower() == "full" else "Recommended minimum"


def _permission_names(perms: discord.Permissions) -> list[str]:
    out: list[str] = []
    try:
        for name, enabled in perms:
            if enabled:
                out.append(str(name))
    except Exception:
        pass
    return sorted(set(out))


def _overwrite_snapshot(overwrite: discord.PermissionOverwrite) -> dict[str, list[str]]:
    try:
        allow, deny = overwrite.pair()
        return {"allow": _permission_names(allow), "deny": _permission_names(deny)}
    except Exception:
        return {"allow": [], "deny": []}


def _overwrite_from_snapshot(data: dict[str, Any]) -> discord.PermissionOverwrite:
    allow = discord.Permissions.none()
    deny = discord.Permissions.none()
    for name in list(data.get("allow") or []):
        if hasattr(allow, name):
            try:
                setattr(allow, name, True)
            except Exception:
                pass
    for name in list(data.get("deny") or []):
        if hasattr(deny, name):
            try:
                setattr(deny, name, True)
            except Exception:
                pass
    return discord.PermissionOverwrite.from_pair(allow, deny)


def _required_permissions(
    feature: str,
    mode: str,
    target: discord.abc.GuildChannel,
) -> tuple[str, ...]:
    clean_mode = "full" if _safe_str(mode).lower() == "full" else "minimum"
    clean_feature = _safe_str(feature, "general").lower()
    names = list(
        _FULL_CHANNEL_PERMISSIONS
        if clean_mode == "full"
        else _FEATURE_PERMISSIONS.get(clean_feature, _FEATURE_PERMISSIONS["general"])
    )

    voice_types: tuple[type, ...] = (discord.VoiceChannel,)
    stage = getattr(discord, "StageChannel", None)
    if isinstance(stage, type):
        voice_types += (stage,)

    if isinstance(target, voice_types):
        names = ["view_channel", "manage_channels", "move_members"] if clean_mode == "full" else ["view_channel"]
        if clean_feature in {"moderation", "general"}:
            names.append("move_members")
    elif isinstance(target, discord.CategoryChannel):
        names = [name for name in names if name != "move_members" or clean_mode == "full"]

    return tuple(dict.fromkeys(name for name in names if name and name != "administrator"))


def approved_public_permissions() -> discord.Permissions:
    perms = discord.Permissions.none()
    for name in _APPROVED_PUBLIC_GUILD_PERMISSIONS:
        if hasattr(perms, name):
            try:
                setattr(perms, name, True)
            except Exception:
                pass
    try:
        perms.administrator = False
    except Exception:
        pass
    return perms


def reauthorize_url(guild: discord.Guild) -> str:
    try:
        client_id = int(getattr(getattr(guild, "me", None), "id", 0) or 0)
        if client_id <= 0:
            return ""
        return discord.utils.oauth_url(
            client_id,
            permissions=approved_public_permissions(),
            guild=guild,
            disable_guild_select=True,
            scopes=("bot", "applications.commands"),
        )
    except Exception:
        return ""


def _target_label(target: Any) -> str:
    try:
        mention = getattr(target, "mention", None)
        return str(mention or f"#{getattr(target, 'name', 'unknown')}")
    except Exception:
        return "unknown target"


def _target_name(target: Any) -> str:
    return _safe_str(getattr(target, "name", ""), "selected target")


def _bot_member(guild: discord.Guild) -> Optional[discord.Member]:
    try:
        if isinstance(guild.me, discord.Member):
            return guild.me
    except Exception:
        pass
    return None


def _target_supported(target: Any) -> bool:
    supported: tuple[type, ...] = (discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel)
    forum = getattr(discord, "ForumChannel", None)
    stage = getattr(discord, "StageChannel", None)
    if isinstance(forum, type):
        supported += (forum,)
    if isinstance(stage, type):
        supported += (stage,)
    return isinstance(target, supported)


@dataclass
class TargetPermissionAudit:
    guild_id: int
    target_id: int
    target_name: str
    feature: str
    mode: str
    required: list[str] = field(default_factory=list)
    effective_ok: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    explicit_denies: list[str] = field(default_factory=list)
    repairable_missing: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    child_count: int = 0

    @property
    def healthy(self) -> bool:
        return not self.missing

    @property
    def can_apply(self) -> bool:
        return bool(self.repairable_missing) and not self.blockers


@dataclass
class TargetRepairResult:
    ok: bool
    token: str
    changed_targets: list[str] = field(default_factory=list)
    failed_targets: list[str] = field(default_factory=list)
    skipped_conflicts: list[str] = field(default_factory=list)
    before: list[TargetPermissionAudit] = field(default_factory=list)
    after: list[TargetPermissionAudit] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class PermissionRepairState:
    guild: discord.Guild
    actor_id: int
    target: Optional[discord.abc.GuildChannel] = None
    feature: str = "general"
    mode: str = "minimum"
    include_children: bool = False
    last_token: str = ""


def audit_target(
    guild: discord.Guild,
    target: discord.abc.GuildChannel,
    *,
    feature: str = "general",
    mode: str = "minimum",
) -> TargetPermissionAudit:
    clean_feature = _safe_str(feature, "general").lower()
    clean_mode = "full" if _safe_str(mode).lower() == "full" else "minimum"
    required = list(_required_permissions(clean_feature, clean_mode, target))
    report = TargetPermissionAudit(
        guild_id=int(guild.id),
        target_id=int(target.id),
        target_name=_safe_str(getattr(target, "name", ""), str(target.id)),
        feature=clean_feature,
        mode=clean_mode,
        required=required,
        child_count=(
            len(list(getattr(target, "channels", []) or []))
            if isinstance(target, discord.CategoryChannel)
            else 0
        ),
    )
    if not _target_supported(target):
        report.blockers.append("This Discord channel type is not supported by Fix Access.")
        return report

    me = _bot_member(guild)
    if me is None:
        report.blockers.append("Dank Shield could not resolve its member record in this server.")
        return report

    try:
        effective = target.permissions_for(me)
    except Exception:
        report.blockers.append("Dank Shield could not evaluate effective permissions for this target.")
        return report

    try:
        member_overwrite = target.overwrites_for(me)
        _allow, explicit_deny = member_overwrite.pair()
    except Exception:
        explicit_deny = discord.Permissions.none()

    for name in required:
        if bool(getattr(effective, name, False)):
            report.effective_ok.append(name)
            continue
        report.missing.append(name)
        if bool(getattr(explicit_deny, name, False)):
            report.explicit_denies.append(name)
        else:
            report.repairable_missing.append(name)

    if report.missing and not bool(getattr(effective, "manage_channels", False)):
        report.blockers.append(
            "Dank Shield does not have Manage Channels in this target, so Discord will not let the bot repair its own overwrite here. "
            "Fix the bot role/channel deny or reauthorize Dank Shield first."
        )

    try:
        if getattr(me.top_role, "managed", False):
            report.warnings.append(
                "Dank Shield's integration role is managed. If role hierarchy is blocking another action, move the managed bot role in Server Settings → Roles."
            )
        if me.top_role <= guild.default_role:
            report.warnings.append("Dank Shield's top role is not above @everyone.")
    except Exception:
        pass

    if report.explicit_denies:
        report.warnings.append(
            "Dank Shield has an explicit member-level deny for: "
            + ", ".join(report.explicit_denies)
            + ". Safe Fix Access preserves it until you explicitly confirm clearing denies."
        )
    return report


def audit_targets(
    guild: discord.Guild,
    target: discord.abc.GuildChannel,
    *,
    feature: str,
    mode: str,
    include_children: bool,
) -> list[TargetPermissionAudit]:
    out = [audit_target(guild, target, feature=feature, mode=mode)]
    if include_children and isinstance(target, discord.CategoryChannel):
        for child in list(getattr(target, "channels", []) or []):
            if _target_supported(child):
                out.append(audit_target(guild, child, feature=feature, mode=mode))
    return out


def _apply_missing_to_overwrite(
    current: discord.PermissionOverwrite,
    names: Iterable[str],
    *,
    clear_explicit_denies: bool,
) -> tuple[discord.PermissionOverwrite, list[str], list[str]]:
    changed: list[str] = []
    preserved_denies: list[str] = []
    try:
        _allow, deny = current.pair()
    except Exception:
        deny = discord.Permissions.none()

    for name in names:
        if not hasattr(current, name):
            continue
        denied = bool(getattr(deny, name, False))
        if denied and not clear_explicit_denies:
            preserved_denies.append(name)
            continue
        try:
            setattr(current, name, True)
            changed.append(name)
        except Exception:
            continue
    return current, changed, preserved_denies


def _prune_undo_memory() -> None:
    cutoff = time.time() - _UNDO_TTL_SECONDS
    for key in list(_UNDO_MEMORY):
        if float(_UNDO_MEMORY[key].get("saved_epoch", 0.0) or 0.0) < cutoff:
            _UNDO_MEMORY.pop(key, None)


def _remember_snapshot(snapshot: dict[str, Any]) -> None:
    _prune_undo_memory()
    token = _safe_str(snapshot.get("token"))
    if token:
        _UNDO_MEMORY[token] = {**snapshot, "saved_epoch": time.time()}


def _insert_event_sync(payload: dict[str, Any]) -> bool:
    if get_supabase is None:
        return False
    try:
        sb = get_supabase()
        if sb is None:
            return False
        sb.table("activity_feed_events").insert(payload).execute()
        return True
    except Exception:
        return False


async def _record_repair_event(
    *,
    guild_id: int,
    actor_id: int,
    event_type: str,
    message: str,
    metadata: dict[str, Any],
) -> bool:
    payload = {
        "guild_id": str(guild_id),
        "event_type": event_type,
        "actor_id": str(actor_id),
        "target_id": _safe_str(metadata.get("target_id")) or None,
        "message": _safe_str(message)[:1000],
        "metadata": metadata,
        "meta": metadata,
        "created_at": _utc_now_iso(),
    }
    return await asyncio.to_thread(_insert_event_sync, payload)


def _load_snapshot_sync(guild_id: int, token: str) -> dict[str, Any] | None:
    cached = _UNDO_MEMORY.get(token)
    if cached and _safe_int(cached.get("guild_id"), 0) == int(guild_id):
        return dict(cached)
    if get_supabase is None:
        return None
    try:
        sb = get_supabase()
        if sb is None:
            return None
        response = (
            sb.table("activity_feed_events")
            .select("metadata,meta,created_at")
            .eq("guild_id", str(guild_id))
            .eq("event_type", "permission_repair")
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        for row in getattr(response, "data", None) or []:
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else row.get("meta")
            if isinstance(meta, dict) and _safe_str(meta.get("token")) == token:
                _remember_snapshot(meta)
                return dict(meta)
    except Exception:
        return None
    return None


async def apply_target_repair(
    guild: discord.Guild,
    target: discord.abc.GuildChannel,
    *,
    actor_id: int,
    feature: str,
    mode: str,
    include_children: bool,
    clear_explicit_denies: bool = False,
) -> TargetRepairResult:
    me = _bot_member(guild)
    if me is None:
        return TargetRepairResult(False, "", notes=["Dank Shield member record is unavailable."])

    before = audit_targets(
        guild,
        target,
        feature=feature,
        mode=mode,
        include_children=include_children,
    )
    token = uuid.uuid4().hex[:16]
    snapshot: dict[str, Any] = {
        "token": token,
        "guild_id": str(guild.id),
        "actor_id": str(actor_id),
        "target_id": str(target.id),
        "feature": feature,
        "mode": mode,
        "include_children": bool(include_children),
        "clear_explicit_denies": bool(clear_explicit_denies),
        "created_at": _utc_now_iso(),
        "targets": [],
    }
    result = TargetRepairResult(ok=True, token=token, before=before)

    targets: list[discord.abc.GuildChannel] = [target]
    if include_children and isinstance(target, discord.CategoryChannel):
        targets.extend(child for child in list(target.channels) if _target_supported(child))

    by_id = {item.target_id: item for item in before}
    for current_target in targets:
        report = by_id.get(int(current_target.id))
        if report is None:
            continue
        if report.blockers:
            result.failed_targets.append(
                f"{_target_label(current_target)} — {' '.join(report.blockers)}"
            )
            result.ok = False
            continue
        if not report.missing:
            continue

        try:
            current = current_target.overwrites_for(me)
            snapshot["targets"].append(
                {
                    "channel_id": str(current_target.id),
                    "channel_name": _safe_str(getattr(current_target, "name", "")),
                    "before": _overwrite_snapshot(current),
                }
            )
            new_overwrite, changed, preserved = _apply_missing_to_overwrite(
                current,
                report.missing,
                clear_explicit_denies=clear_explicit_denies,
            )
            if preserved:
                result.skipped_conflicts.append(
                    f"{_target_label(current_target)} — preserved explicit deny: {', '.join(preserved)}"
                )
            if not changed:
                continue

            await with_retry(
                lambda t=current_target, ow=new_overwrite: t.set_permissions(
                    me,
                    overwrite=ow,
                    reason=f"Dank Shield Fix Access by {actor_id} token={token}",
                ),
                attempts=3,
                concurrency_key=f"permission-repair:{guild.id}",
            )
            result.changed_targets.append(
                f"{_target_label(current_target)} — {', '.join(changed)}"
            )
        except discord.Forbidden:
            result.failed_targets.append(
                f"{_target_label(current_target)} — Discord denied Manage Channels."
            )
            result.ok = False
        except Exception as exc:
            result.failed_targets.append(
                f"{_target_label(current_target)} — {type(exc).__name__}: {str(exc)[:140]}"
            )
            result.ok = False

    result.after = audit_targets(
        guild,
        target,
        feature=feature,
        mode=mode,
        include_children=include_children,
    )
    _remember_snapshot(snapshot)
    persisted = await _record_repair_event(
        guild_id=int(guild.id),
        actor_id=int(actor_id),
        event_type="permission_repair",
        message=(
            f"Fix Access target={target.id} changed={len(result.changed_targets)} "
            f"failed={len(result.failed_targets)}"
        ),
        metadata={
            **snapshot,
            "changed_targets": result.changed_targets,
            "failed_targets": result.failed_targets,
            "skipped_conflicts": result.skipped_conflicts,
        },
    )
    result.notes.append(
        "Undo snapshot saved to activity history."
        if persisted
        else "Undo snapshot is available in this bot process; activity-history persistence was unavailable."
    )
    return result


async def undo_target_repair(
    guild: discord.Guild,
    *,
    actor_id: int,
    token: str,
) -> TargetRepairResult:
    me = _bot_member(guild)
    if me is None:
        return TargetRepairResult(False, token, notes=["Dank Shield member record is unavailable."])

    snapshot = await asyncio.to_thread(_load_snapshot_sync, int(guild.id), token)
    if not snapshot:
        return TargetRepairResult(False, token, notes=["That undo token was not found for this server."])

    result = TargetRepairResult(True, token)
    for row in list(snapshot.get("targets") or []):
        channel_id = _safe_int(row.get("channel_id"), 0)
        channel = guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.abc.GuildChannel):
            result.failed_targets.append(f"Channel `{channel_id}` no longer exists.")
            result.ok = False
            continue
        try:
            overwrite = _overwrite_from_snapshot(dict(row.get("before") or {}))
            await with_retry(
                lambda ch=channel, ow=overwrite: ch.set_permissions(
                    me,
                    overwrite=ow,
                    reason=f"Dank Shield Fix Access undo by {actor_id} token={token}",
                ),
                attempts=3,
                concurrency_key=f"permission-repair:{guild.id}",
            )
            result.changed_targets.append(f"Restored {_target_label(channel)}")
        except Exception as exc:
            result.failed_targets.append(
                f"{_target_label(channel)} — {type(exc).__name__}: {str(exc)[:140]}"
            )
            result.ok = False

    await _record_repair_event(
        guild_id=int(guild.id),
        actor_id=int(actor_id),
        event_type="permission_repair_undo",
        message=(
            f"Fix Access undo token={token} restored={len(result.changed_targets)} "
            f"failed={len(result.failed_targets)}"
        ),
        metadata={
            "token": token,
            "target_id": snapshot.get("target_id"),
            "restored": result.changed_targets,
            "failed": result.failed_targets,
        },
    )
    return result


def _audit_lines(audits: list[TargetPermissionAudit], limit: int = 8) -> str:
    lines: list[str] = []
    for audit in audits[:limit]:
        if audit.healthy:
            lines.append(f"✅ **#{audit.target_name}** — required access is already available")
            continue
        parts: list[str] = []
        if audit.repairable_missing:
            parts.append("missing: " + ", ".join(audit.repairable_missing))
        if audit.explicit_denies:
            parts.append("explicit deny: " + ", ".join(audit.explicit_denies))
        if audit.blockers:
            parts.append("blocked by Discord: " + " ".join(audit.blockers))
        lines.append(f"⚠️ **#{audit.target_name}** — " + " • ".join(parts))
    if len(audits) > limit:
        lines.append(f"…and {len(audits) - limit} more child target(s)")
    return "\n".join(lines) or "No targets."


def build_preview_embed(state: PermissionRepairState) -> discord.Embed:
    target_text = _target_label(state.target) if state.target is not None else "`Not selected`"
    is_category = isinstance(state.target, discord.CategoryChannel)
    children_text = "ON" if is_category and state.include_children else ("OFF" if is_category else "N/A")

    embed = discord.Embed(
        title="🛠️ Fix Access — Selected Target",
        description=(
            f"**Target:** {target_text}\n"
            f"**Feature:** {_feature_label(state.feature)}\n"
            f"**Repair mode:** {_mode_label(state.mode)}\n"
            f"**Include category children:** {children_text}\n\n"
            "Choose or change any option below. The selections shown here are the state that will be used. "
            "Nothing changes until you press **Fix Missing Access**."
        ),
        color=discord.Color.blurple(),
    )
    if state.target is None:
        embed.add_field(
            name="Next step",
            value="Choose the exact channel or category. Feature and repair-mode choices can be made before or after the target.",
            inline=False,
        )
        return embed

    audits = audit_targets(
        state.guild,
        state.target,
        feature=state.feature,
        mode=state.mode,
        include_children=state.include_children,
    )
    embed.add_field(name="Access check", value=_audit_lines(audits), inline=False)

    blockers = list(dict.fromkeys(item for audit in audits for item in audit.blockers))
    warnings = list(dict.fromkeys(item for audit in audits for item in audit.warnings))
    if blockers:
        embed.add_field(
            name="Cannot self-repair yet",
            value="\n".join(f"• {item}" for item in blockers)[:1024],
            inline=False,
        )
    if warnings:
        embed.add_field(
            name="Safety notes",
            value="\n".join(f"• {item}" for item in warnings[:4])[:1024],
            inline=False,
        )
    embed.add_field(
        name="What this changes",
        value=(
            "Only missing permissions on **Dank Shield's own member overwrite** for the selected scope. "
            "Existing member/staff visibility and unrelated allow/deny rules stay untouched."
        ),
        inline=False,
    )
    return embed


async def _safe_defer_update(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(thinking=False)
    except Exception:
        pass


async def _edit_original_or_followup(
    interaction: discord.Interaction,
    *,
    embed: discord.Embed,
    view: discord.ui.View | None = None,
) -> None:
    try:
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.response.edit_message(embed=embed, view=view)
        return
    except Exception:
        pass
    try:
        await interaction.followup.send(
            embed=embed,
            view=view,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception:
        pass


def _actor_can_manage(interaction: discord.Interaction) -> bool:
    try:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        if int(interaction.user.id) == int(interaction.guild.owner_id):
            return True
        perms = interaction.user.guild_permissions
        return bool(perms.administrator or perms.manage_guild or perms.manage_channels)
    except Exception:
        return False


async def _run_repair(
    interaction: discord.Interaction,
    state: PermissionRepairState,
    *,
    clear_explicit_denies: bool,
) -> TargetRepairResult | None:
    if interaction.guild is None or state.target is None:
        return None

    async def job() -> TargetRepairResult:
        return await apply_target_repair(
            interaction.guild,
            state.target,
            actor_id=int(interaction.user.id),
            feature=state.feature,
            mode=state.mode,
            include_children=state.include_children,
            clear_explicit_denies=clear_explicit_denies,
        )

    result = await run_interaction_exclusive(
        interaction=interaction,
        operation_type="permission_repair_target",
        action_label="Fix Access",
        factory=job,
        fingerprint={
            "guild_id": interaction.guild.id,
            "target_id": state.target.id,
            "feature": state.feature,
            "mode": state.mode,
            "children": state.include_children,
            "clear_denies": clear_explicit_denies,
        },
        risk_level="dangerous",
        concurrency_class="channel_mutation",
        concurrency_key="permission_repair",
        timeout_seconds=180.0,
    )
    return result if isinstance(result, TargetRepairResult) else None


def _result_embed(result: TargetRepairResult, *, undo: bool = False) -> discord.Embed:
    if undo:
        title = "↩️ Fix Access Restored"
    elif result.ok:
        title = "✅ Fix Access Finished"
    else:
        title = "⚠️ Fix Access Partially Finished"

    embed = discord.Embed(
        title=title,
        color=discord.Color.green() if result.ok else discord.Color.orange(),
    )
    parts = [
        f"**Changed/restored:** {len(result.changed_targets)}",
        f"**Failed:** {len(result.failed_targets)}",
    ]
    if result.skipped_conflicts:
        parts.append(f"**Explicit denies preserved:** {len(result.skipped_conflicts)}")
    if result.token and not undo:
        parts.append(f"**Undo token:** `{result.token}`")
    embed.description = "\n".join(parts)

    if result.changed_targets:
        embed.add_field(
            name="Changed",
            value="\n".join(f"• {item}" for item in result.changed_targets[:8])[:1024],
            inline=False,
        )
    if result.skipped_conflicts:
        embed.add_field(
            name="Preserved explicit denies",
            value="\n".join(f"• {item}" for item in result.skipped_conflicts[:6])[:1024],
            inline=False,
        )
    if result.failed_targets:
        embed.add_field(
            name="Still blocked",
            value="\n".join(f"• {item}" for item in result.failed_targets[:6])[:1024],
            inline=False,
        )
    if result.notes:
        embed.add_field(
            name="Undo / audit",
            value="\n".join(f"• {item}" for item in result.notes[:3])[:1024],
            inline=False,
        )
    return embed


class _TargetChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, state: PermissionRepairState) -> None:
        self.state = state
        selected = _target_name(state.target) if state.target is not None else "Choose a channel or category…"
        super().__init__(
            placeholder=(f"Target: {selected}" if state.target is not None else selected)[:100],
            min_values=1,
            max_values=1,
            channel_types=[
                discord.ChannelType.text,
                discord.ChannelType.news,
                discord.ChannelType.voice,
                discord.ChannelType.category,
                discord.ChannelType.stage_voice,
                discord.ChannelType.forum,
            ],
            custom_id="dank_permission_repair:target",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.state.actor_id:
            return await interaction.response.send_message(
                "❌ This Fix Access screen belongs to another admin.",
                ephemeral=True,
            )
        target = self.values[0] if self.values else None
        if not isinstance(target, discord.abc.GuildChannel):
            return await interaction.response.send_message(
                "❌ That channel could not be resolved.",
                ephemeral=True,
            )
        self.state.target = target
        if not isinstance(target, discord.CategoryChannel):
            self.state.include_children = False
        await interaction.response.edit_message(
            embed=build_preview_embed(self.state),
            view=TargetPermissionRepairView(self.state),
        )


class _FeatureSelect(discord.ui.Select):
    def __init__(self, state: PermissionRepairState) -> None:
        self.state = state
        rows = (
            ("General", "general", "Basic bot replies, embeds, files and history"),
            ("Tickets", "tickets", "Ticket channels, threads, cleanup and management"),
            ("Moderation", "moderation", "Message moderation and cleanup"),
            ("Logs", "logs", "Modlog, join/leave and security output"),
            ("Welcome", "welcome", "Welcome/exit cards and onboarding output"),
            ("Activity", "activity", "History/thread access for inactivity truth"),
            ("Community Tools", "community", "Stickies, polls and embeds"),
        )
        options = [
            discord.SelectOption(
                label=label,
                value=value,
                description=description,
                default=value == state.feature,
            )
            for label, value, description in rows
        ]
        super().__init__(
            placeholder=f"Feature: {_feature_label(state.feature)}"[:100],
            options=options,
            min_values=1,
            max_values=1,
            custom_id="dank_permission_repair:feature",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.state.actor_id:
            return await interaction.response.send_message(
                "❌ This Fix Access screen belongs to another admin.",
                ephemeral=True,
            )
        self.state.feature = self.values[0]
        await interaction.response.edit_message(
            embed=build_preview_embed(self.state),
            view=TargetPermissionRepairView(self.state),
        )


class _ModeSelect(discord.ui.Select):
    def __init__(self, state: PermissionRepairState) -> None:
        self.state = state
        options = [
            discord.SelectOption(
                label="Recommended minimum",
                value="minimum",
                description="Only permissions required by the selected feature",
                default=state.mode != "full",
            ),
            discord.SelectOption(
                label="Full Dank Shield control",
                value="full",
                description="Broad non-Administrator channel control",
                default=state.mode == "full",
            ),
        ]
        super().__init__(
            placeholder=f"Repair mode: {_mode_label(state.mode)}"[:100],
            options=options,
            min_values=1,
            max_values=1,
            custom_id="dank_permission_repair:mode",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.state.actor_id:
            return await interaction.response.send_message(
                "❌ This Fix Access screen belongs to another admin.",
                ephemeral=True,
            )
        self.state.mode = self.values[0]
        await interaction.response.edit_message(
            embed=build_preview_embed(self.state),
            view=TargetPermissionRepairView(self.state),
        )


class ExplicitDenyConfirmView(discord.ui.View):
    def __init__(self, state: PermissionRepairState) -> None:
        super().__init__(timeout=180)
        self.state = state

    @discord.ui.button(
        label="Clear Dank Shield Denies + Fix",
        emoji="⚠️",
        style=discord.ButtonStyle.danger,
    )
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if interaction.guild is None or interaction.user.id != self.state.actor_id or self.state.target is None:
            return await interaction.response.send_message(
                "❌ This confirmation belongs to the admin who opened it.",
                ephemeral=True,
            )
        if not _actor_can_manage(interaction):
            return await interaction.response.send_message(
                "❌ Manage Server or Administrator is required.",
                ephemeral=True,
            )
        await _safe_defer_update(interaction)
        result = await _run_repair(interaction, self.state, clear_explicit_denies=True)
        if result is None:
            return
        self.state.last_token = result.token
        await _edit_original_or_followup(
            interaction,
            embed=_result_embed(result),
            view=TargetPermissionRepairView(self.state),
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(
            content="Cancelled. No explicit deny was changed.",
            embed=None,
            view=None,
        )


class UndoTokenModal(discord.ui.Modal, title="Undo Fix Access"):
    token = discord.ui.TextInput(label="Undo token", placeholder="Shown after Fix Access", max_length=32)

    def __init__(self, state: PermissionRepairState) -> None:
        super().__init__()
        self.state = state
        if state.last_token:
            self.token.default = state.last_token

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.user.id != self.state.actor_id or not _actor_can_manage(interaction):
            return await interaction.response.send_message(
                "❌ You cannot restore this repair.",
                ephemeral=True,
            )

        # Modals need a deferred response, but every outcome below resolves that
        # original response so Discord cannot leave a permanent thinking card.
        await interaction.response.defer(ephemeral=True, thinking=True)

        async def job() -> TargetRepairResult:
            return await undo_target_repair(
                interaction.guild,
                actor_id=int(interaction.user.id),
                token=str(self.token.value).strip(),
            )

        result = await run_interaction_exclusive(
            interaction=interaction,
            operation_type="permission_repair_undo",
            action_label="permission repair undo",
            factory=job,
            fingerprint={
                "guild_id": interaction.guild.id,
                "token": str(self.token.value).strip(),
            },
            risk_level="dangerous",
            concurrency_class="channel_mutation",
            concurrency_key="permission_repair",
            timeout_seconds=180.0,
        )
        if not isinstance(result, TargetRepairResult):
            try:
                await interaction.edit_original_response(
                    content="⚠️ Undo did not complete. See the status message below for the reason.",
                    embed=None,
                    view=None,
                )
            except Exception:
                pass
            return

        await _edit_original_or_followup(
            interaction,
            embed=_result_embed(result, undo=True),
            view=TargetPermissionRepairView(self.state),
        )


class TargetPermissionRepairView(discord.ui.View):
    def __init__(self, state: PermissionRepairState) -> None:
        super().__init__(timeout=900)
        self.state = state
        self.add_item(_TargetChannelSelect(state))
        self.add_item(_FeatureSelect(state))
        self.add_item(_ModeSelect(state))

        is_category = isinstance(state.target, discord.CategoryChannel)
        self.toggle_children.disabled = not is_category
        self.toggle_children.label = (
            f"Include Category Children: {'ON' if state.include_children and is_category else 'OFF'}"
        )
        self.toggle_children.style = (
            discord.ButtonStyle.success
            if state.include_children and is_category
            else discord.ButtonStyle.secondary
        )
        self.fix_missing.disabled = state.target is None
        self.resolve_denies.disabled = state.target is None

        url = reauthorize_url(state.guild)
        if url:
            self.add_item(
                discord.ui.Button(
                    label="Reauthorize Dank Shield",
                    emoji="🔐",
                    style=discord.ButtonStyle.link,
                    url=url,
                    row=4,
                )
            )

    @discord.ui.button(
        label="Include Category Children: OFF",
        emoji="🗂️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_permission_repair:children",
        row=3,
    )
    async def toggle_children(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if interaction.user.id != self.state.actor_id:
            return await interaction.response.send_message(
                "❌ This Fix Access screen belongs to another admin.",
                ephemeral=True,
            )
        if not isinstance(self.state.target, discord.CategoryChannel):
            return await interaction.response.send_message(
                "Choose a category first. Child-channel repair only applies to categories.",
                ephemeral=True,
            )
        self.state.include_children = not self.state.include_children
        await interaction.response.edit_message(
            embed=build_preview_embed(self.state),
            view=TargetPermissionRepairView(self.state),
        )

    @discord.ui.button(
        label="Fix Missing Access",
        emoji="🛠️",
        style=discord.ButtonStyle.success,
        custom_id="dank_permission_repair:fix",
        row=3,
    )
    async def fix_missing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _actor_can_manage(interaction) or interaction.user.id != self.state.actor_id:
            return await interaction.response.send_message(
                "❌ Manage Server, Manage Channels, or Administrator is required.",
                ephemeral=True,
            )
        if self.state.target is None:
            return await interaction.response.send_message(
                "Choose a channel/category first.",
                ephemeral=True,
            )

        await _safe_defer_update(interaction)
        result = await _run_repair(interaction, self.state, clear_explicit_denies=False)
        if result is None:
            return
        self.state.last_token = result.token
        await _edit_original_or_followup(
            interaction,
            embed=_result_embed(result),
            view=TargetPermissionRepairView(self.state),
        )

    @discord.ui.button(
        label="Resolve Explicit Denies",
        emoji="⚠️",
        style=discord.ButtonStyle.danger,
        custom_id="dank_permission_repair:denies",
        row=3,
    )
    async def resolve_denies(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _actor_can_manage(interaction) or interaction.user.id != self.state.actor_id:
            return await interaction.response.send_message(
                "❌ Manage Server, Manage Channels, or Administrator is required.",
                ephemeral=True,
            )
        if self.state.target is None:
            return await interaction.response.send_message(
                "Choose a channel/category first.",
                ephemeral=True,
            )

        audits = audit_targets(
            self.state.guild,
            self.state.target,
            feature=self.state.feature,
            mode=self.state.mode,
            include_children=self.state.include_children,
        )
        conflicts = [
            (audit.target_name, audit.explicit_denies)
            for audit in audits
            if audit.explicit_denies
        ]
        if not conflicts:
            return await interaction.response.send_message(
                "✅ There are no explicit Dank Shield denies to resolve for this selection.",
                ephemeral=True,
            )

        text = "\n".join(
            f"• **#{name}**: {', '.join(denies)}"
            for name, denies in conflicts[:8]
        )
        await interaction.response.send_message(
            "⚠️ **This is the only path that clears explicit denies on Dank Shield's own overwrite.**\n"
            "It does not change other roles or members. Confirm only if these denies are accidental.\n\n"
            + text,
            view=ExplicitDenyConfirmView(self.state),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Undo Repair",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_permission_repair:undo",
        row=4,
    )
    async def undo(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _actor_can_manage(interaction) or interaction.user.id != self.state.actor_id:
            return await interaction.response.send_message(
                "❌ You cannot restore this repair.",
                ephemeral=True,
            )
        await interaction.response.send_modal(UndoTokenModal(self.state))


async def open_target_permission_repair(interaction: discord.Interaction) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message(
            "❌ This must be used inside a server.",
            ephemeral=True,
        )
    if not _actor_can_manage(interaction):
        return await interaction.response.send_message(
            "❌ Manage Server, Manage Channels, or Administrator is required.",
            ephemeral=True,
        )

    state = PermissionRepairState(
        guild=interaction.guild,
        actor_id=int(interaction.user.id),
    )
    embed = build_preview_embed(state)
    view = TargetPermissionRepairView(state)

    # Component navigation replaces the previous repair card instead of spawning
    # another ephemeral stack. Slash/command entry still sends a fresh ephemeral.
    try:
        if getattr(interaction, "message", None) is not None and not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=view)
            return
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    except Exception:
        try:
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        except Exception:
            pass


__all__ = [
    "TargetPermissionAudit",
    "TargetRepairResult",
    "PermissionRepairState",
    "TargetPermissionRepairView",
    "approved_public_permissions",
    "apply_target_repair",
    "audit_target",
    "audit_targets",
    "build_preview_embed",
    "open_target_permission_repair",
    "reauthorize_url",
    "undo_target_repair",
]
