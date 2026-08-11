from __future__ import annotations

"""Selected channel/category permission repair for Dank Shield.

This is deliberately narrower and safer than the older whole-setup repair pass:
- admins select the exact target;
- only Dank Shield's own missing overwrite bits are changed;
- unrelated allows/denies and member/staff visibility are preserved;
- an explicit deny on Dank Shield itself is never cleared without a second,
  explicit confirmation path;
- category children are opt-in and previewed;
- every applied repair stores a before snapshot that can be restored;
- the reauthorization link uses the approved non-Administrator public set.
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
        "view_channel", "send_messages", "send_messages_in_threads", "embed_links", "attach_files",
        "read_message_history", "manage_channels", "manage_messages", "manage_threads",
    ),
    "moderation": ("view_channel", "send_messages", "embed_links", "read_message_history", "manage_messages"),
    "logs": ("view_channel", "send_messages", "embed_links", "attach_files", "read_message_history"),
    "welcome": ("view_channel", "send_messages", "embed_links", "attach_files", "read_message_history"),
    "activity": ("view_channel", "read_message_history", "manage_threads"),
    "community": (
        "view_channel", "send_messages", "send_messages_in_threads", "embed_links", "attach_files",
        "read_message_history", "manage_messages", "manage_threads",
    ),
}

_FULL_CHANNEL_PERMISSIONS = (
    "view_channel", "send_messages", "send_messages_in_threads", "embed_links", "attach_files",
    "read_message_history", "manage_channels", "manage_messages", "manage_threads", "move_members",
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


def _required_permissions(feature: str, mode: str, target: discord.abc.GuildChannel) -> tuple[str, ...]:
    clean_mode = _safe_str(mode, "minimum").lower()
    clean_feature = _safe_str(feature, "general").lower()
    names = list(_FULL_CHANNEL_PERMISSIONS if clean_mode == "full" else _FEATURE_PERMISSIONS.get(clean_feature, _FEATURE_PERMISSIONS["general"]))

    # Voice/stage channels do not need message-only bits unless they also expose
    # a text surface. Move Members is meaningful for voice moderation.
    if isinstance(target, (discord.VoiceChannel, discord.StageChannel)):
        voice = ["view_channel", "manage_channels", "move_members"] if clean_mode == "full" else ["view_channel"]
        if clean_feature in {"moderation", "general"}:
            voice.append("move_members")
        names = voice
    elif isinstance(target, discord.CategoryChannel):
        # Categories are templates. Keep message/history bits because children
        # inherit them, but do not add voice-only Move Members in minimum mode.
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
        guild_id=int(guild.id), target_id=int(target.id), target_name=_safe_str(getattr(target, "name", ""), str(target.id)),
        feature=clean_feature, mode=clean_mode, required=required,
        child_count=len(list(getattr(target, "channels", []) or [])) if isinstance(target, discord.CategoryChannel) else 0,
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
        member_overwrite = discord.PermissionOverwrite()
        explicit_deny = discord.Permissions.none()

    for name in required:
        enabled = bool(getattr(effective, name, False))
        denied = bool(getattr(explicit_deny, name, False))
        if enabled:
            report.effective_ok.append(name)
            continue
        report.missing.append(name)
        if denied:
            report.explicit_denies.append(name)
        else:
            report.repairable_missing.append(name)

    # Manage Channels must be effective before the bot can edit this target.
    if not bool(getattr(effective, "manage_channels", False)):
        # The target may be missing Manage Channels specifically. A bot cannot
        # use the permission it is missing to grant it to itself here.
        report.blockers.append(
            "Dank Shield does not currently have Manage Channels in this target, so Discord will not let it self-repair this overwrite. Reauthorize the bot and/or fix the bot role/channel deny first."
        )

    try:
        if getattr(me.top_role, "managed", False):
            report.warnings.append("Dank Shield's integration role is managed. The bot cannot move that role itself; a server admin must fix role order in Server Settings → Roles if hierarchy is part of the blocker.")
        if me.top_role <= guild.default_role:
            report.warnings.append("Dank Shield's top role is not above @everyone. Move the managed bot role higher before role-sensitive actions.")
    except Exception:
        pass

    if report.explicit_denies:
        report.warnings.append(
            "Dank Shield has an explicit member-level deny for: " + ", ".join(report.explicit_denies) + ". Safe Fix Access will preserve those denies until you explicitly confirm clearing them."
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
        explicit_deny = bool(getattr(deny, name, False))
        if explicit_deny and not clear_explicit_denies:
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
    *, guild_id: int, actor_id: int, event_type: str, message: str, metadata: dict[str, Any]
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

    before = audit_targets(guild, target, feature=feature, mode=mode, include_children=include_children)
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
        targets.extend([child for child in list(target.channels) if _target_supported(child)])

    by_id = {item.target_id: item for item in before}
    for current_target in targets:
        report = by_id.get(int(current_target.id))
        if report is None:
            continue
        if report.blockers:
            result.failed_targets.append(f"{_target_label(current_target)} — {' '.join(report.blockers)}")
            result.ok = False
            continue
        required_missing = list(report.missing)
        if not required_missing:
            continue
        try:
            current = current_target.overwrites_for(me)
            snapshot["targets"].append({
                "channel_id": str(current_target.id),
                "channel_name": _safe_str(getattr(current_target, "name", "")),
                "before": _overwrite_snapshot(current),
            })
            new_overwrite, changed, preserved = _apply_missing_to_overwrite(
                current,
                required_missing,
                clear_explicit_denies=clear_explicit_denies,
            )
            if preserved:
                result.skipped_conflicts.append(f"{_target_label(current_target)} — preserved explicit deny: {', '.join(preserved)}")
            if not changed:
                continue
            await with_retry(
                lambda t=current_target, ow=new_overwrite: t.set_permissions(
                    me, overwrite=ow, reason=f"Dank Shield Fix Access by {actor_id} token={token}"
                ),
                attempts=3,
                concurrency_key=f"permission-repair:{guild.id}",
            )
            result.changed_targets.append(f"{_target_label(current_target)} — {', '.join(changed)}")
        except discord.Forbidden:
            result.failed_targets.append(f"{_target_label(current_target)} — Discord denied Manage Channels.")
            result.ok = False
        except Exception as exc:
            result.failed_targets.append(f"{_target_label(current_target)} — {type(exc).__name__}: {str(exc)[:140]}")
            result.ok = False

    result.after = audit_targets(guild, target, feature=feature, mode=mode, include_children=include_children)
    _remember_snapshot(snapshot)
    persisted = await _record_repair_event(
        guild_id=int(guild.id),
        actor_id=int(actor_id),
        event_type="permission_repair",
        message=f"Fix Access target={target.id} changed={len(result.changed_targets)} failed={len(result.failed_targets)}",
        metadata={
            **snapshot,
            "changed_targets": result.changed_targets,
            "failed_targets": result.failed_targets,
            "skipped_conflicts": result.skipped_conflicts,
        },
    )
    result.notes.append("Undo snapshot saved to activity history." if persisted else "Undo snapshot is available in this bot process; activity-history persistence was unavailable.")
    return result


async def undo_target_repair(guild: discord.Guild, *, actor_id: int, token: str) -> TargetRepairResult:
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
                    me, overwrite=ow, reason=f"Dank Shield Fix Access undo by {actor_id} token={token}"
                ),
                attempts=3,
                concurrency_key=f"permission-repair:{guild.id}",
            )
            result.changed_targets.append(f"Restored {_target_label(channel)}")
        except Exception as exc:
            result.failed_targets.append(f"{_target_label(channel)} — {type(exc).__name__}: {str(exc)[:140]}")
            result.ok = False

    await _record_repair_event(
        guild_id=int(guild.id), actor_id=int(actor_id), event_type="permission_repair_undo",
        message=f"Fix Access undo token={token} restored={len(result.changed_targets)} failed={len(result.failed_targets)}",
        metadata={"token": token, "target_id": snapshot.get("target_id"), "restored": result.changed_targets, "failed": result.failed_targets},
    )
    return result


def _audit_lines(audits: list[TargetPermissionAudit], limit: int = 10) -> str:
    lines: list[str] = []
    for audit in audits[:limit]:
        if audit.healthy:
            lines.append(f"✅ **#{audit.target_name}** — all required permissions are effective")
            continue
        parts: list[str] = []
        if audit.repairable_missing:
            parts.append("fixable: " + ", ".join(audit.repairable_missing))
        if audit.explicit_denies:
            parts.append("explicit deny: " + ", ".join(audit.explicit_denies))
        if audit.blockers:
            parts.append("blocked: " + " ".join(audit.blockers))
        lines.append(f"⚠️ **#{audit.target_name}** — " + " • ".join(parts))
    if len(audits) > limit:
        lines.append(f"…and {len(audits) - limit} more child target(s)")
    return "\n".join(lines) or "No targets."


def build_preview_embed(state: "PermissionRepairState") -> discord.Embed:
    embed = discord.Embed(title="🛠️ Fix Access — Selected Target", color=discord.Color.blurple())
    if state.target is None:
        embed.description = (
            "Choose the exact channel or category below. Then pick the Dank Shield feature and repair mode. "
            "Nothing changes until you preview and press **Fix Missing Access**."
        )
        return embed
    audits = audit_targets(
        state.guild, state.target, feature=state.feature, mode=state.mode,
        include_children=state.include_children,
    )
    embed.description = (
        f"**Target:** {_target_label(state.target)}\n"
        f"**Feature:** {state.feature.title()} • **Mode:** {'Full Dank Shield control' if state.mode == 'full' else 'Recommended minimum'}\n"
        f"**Category children:** {'Included' if state.include_children and isinstance(state.target, discord.CategoryChannel) else 'Not included'}\n\n"
        f"{_audit_lines(audits)}"
    )
    blockers = [item for audit in audits for item in audit.blockers]
    warnings = [item for audit in audits for item in audit.warnings]
    if blockers:
        embed.add_field(name="Discord blockers", value="\n".join(f"• {item}" for item in dict.fromkeys(blockers))[:1024], inline=False)
    if warnings:
        embed.add_field(name="Safety notes", value="\n".join(f"• {item}" for item in dict.fromkeys(warnings))[:1024], inline=False)
    embed.add_field(
        name="What Safe Fix changes",
        value=(
            "Only missing permissions on **Dank Shield's own member overwrite**. Existing member/staff visibility and unrelated allow/deny rules stay untouched. "
            "An explicit deny on Dank Shield itself requires the separate **Resolve Explicit Denies** confirmation."
        ),
        inline=False,
    )
    return embed


@dataclass
class PermissionRepairState:
    guild: discord.Guild
    actor_id: int
    target: Optional[discord.abc.GuildChannel] = None
    feature: str = "general"
    mode: str = "minimum"
    include_children: bool = False
    last_token: str = ""


class ExplicitDenyConfirmView(discord.ui.View):
    def __init__(self, state: PermissionRepairState) -> None:
        super().__init__(timeout=180)
        self.state = state

    @discord.ui.button(label="Clear Dank Shield Denies + Fix", emoji="⚠️", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if interaction.guild is None or interaction.user.id != self.state.actor_id or self.state.target is None:
            return await interaction.response.send_message("❌ This confirmation belongs to the admin who opened it.", ephemeral=True)
        if not _actor_can_manage(interaction):
            return await interaction.response.send_message("❌ Manage Server or Administrator is required.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await _run_repair(interaction, self.state, clear_explicit_denies=True)
        if result is None:
            return
        self.state.last_token = result.token
        await interaction.followup.send(embed=_result_embed(result), view=TargetPermissionRepairView(self.state), ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(content="Cancelled. No explicit deny was changed.", embed=None, view=None)


class UndoTokenModal(discord.ui.Modal, title="Undo Fix Access"):
    token = discord.ui.TextInput(label="Undo token", placeholder="Shown after Fix Access", max_length=32)

    def __init__(self, state: PermissionRepairState) -> None:
        super().__init__()
        self.state = state
        if state.last_token:
            self.token.default = state.last_token

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.user.id != self.state.actor_id or not _actor_can_manage(interaction):
            return await interaction.response.send_message("❌ You cannot restore this repair.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)

        async def job() -> TargetRepairResult:
            return await undo_target_repair(interaction.guild, actor_id=int(interaction.user.id), token=str(self.token.value).strip())

        result = await run_interaction_exclusive(
            interaction=interaction,
            operation_type="permission_repair_undo",
            action_label="permission repair undo",
            factory=job,
            fingerprint={"guild_id": interaction.guild.id, "token": str(self.token.value).strip()},
            risk_level="dangerous",
            concurrency_class="channel_mutation",
            concurrency_key="permission_repair",
            timeout_seconds=180.0,
        )
        if isinstance(result, TargetRepairResult):
            await interaction.followup.send(embed=_result_embed(result, undo=True), ephemeral=True)


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
            "guild_id": interaction.guild.id, "target_id": state.target.id,
            "feature": state.feature, "mode": state.mode,
            "children": state.include_children, "clear_denies": clear_explicit_denies,
        },
        risk_level="dangerous",
        concurrency_class="channel_mutation",
        concurrency_key="permission_repair",
        timeout_seconds=180.0,
    )
    return result if isinstance(result, TargetRepairResult) else None


def _result_embed(result: TargetRepairResult, *, undo: bool = False) -> discord.Embed:
    embed = discord.Embed(
        title="↩️ Fix Access Restored" if undo else ("✅ Fix Access Complete" if result.ok else "⚠️ Fix Access Partial"),
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
        embed.add_field(name="Changed", value="\n".join(f"• {item}" for item in result.changed_targets[:12])[:1024], inline=False)
    if result.skipped_conflicts:
        embed.add_field(name="Preserved conflicts", value="\n".join(f"• {item}" for item in result.skipped_conflicts[:8])[:1024], inline=False)
    if result.failed_targets:
        embed.add_field(name="Still blocked", value="\n".join(f"• {item}" for item in result.failed_targets[:8])[:1024], inline=False)
    if result.notes:
        embed.add_field(name="Audit / restore", value="\n".join(f"• {item}" for item in result.notes[:5])[:1024], inline=False)
    return embed


class TargetPermissionRepairView(discord.ui.View):
    def __init__(self, state: PermissionRepairState) -> None:
        super().__init__(timeout=900)
        self.state = state
        url = reauthorize_url(state.guild)
        if url:
            self.add_item(discord.ui.Button(label="Reauthorize Dank Shield", emoji="🔐", style=discord.ButtonStyle.link, url=url, row=4))

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="Choose a channel or category…",
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
        row=0,
    )
    async def target_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect) -> None:
        if interaction.user.id != self.state.actor_id:
            return await interaction.response.send_message("❌ This Fix Access screen belongs to another admin.", ephemeral=True)
        target = select.values[0] if select.values else None
        if not isinstance(target, discord.abc.GuildChannel):
            return await interaction.response.send_message("❌ That channel could not be resolved.", ephemeral=True)
        self.state.target = target
        if not isinstance(target, discord.CategoryChannel):
            self.state.include_children = False
        await interaction.response.edit_message(embed=build_preview_embed(self.state), view=TargetPermissionRepairView(self.state))

    @discord.ui.select(
        placeholder="Feature: General access",
        options=[
            discord.SelectOption(label="General", value="general", description="Basic bot replies, embeds, files, history"),
            discord.SelectOption(label="Tickets", value="tickets", description="Ticket channels, threads, cleanup and management"),
            discord.SelectOption(label="Moderation", value="moderation", description="Message moderation and cleanup"),
            discord.SelectOption(label="Logs", value="logs", description="Modlog, join/leave and security output"),
            discord.SelectOption(label="Welcome", value="welcome", description="Welcome/exit cards and onboarding output"),
            discord.SelectOption(label="Activity", value="activity", description="History/thread access for inactivity truth"),
            discord.SelectOption(label="Community Tools", value="community", description="Stickies, polls and embeds"),
        ],
        row=1,
    )
    async def feature_select(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        if interaction.user.id != self.state.actor_id:
            return await interaction.response.send_message("❌ This Fix Access screen belongs to another admin.", ephemeral=True)
        self.state.feature = select.values[0]
        await interaction.response.edit_message(embed=build_preview_embed(self.state), view=TargetPermissionRepairView(self.state))

    @discord.ui.select(
        placeholder="Repair mode: Recommended minimum",
        options=[
            discord.SelectOption(label="Recommended minimum", value="minimum", description="Only permissions required by the selected feature"),
            discord.SelectOption(label="Full Dank Shield control", value="full", description="Broad non-Administrator channel control"),
        ],
        row=2,
    )
    async def mode_select(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        if interaction.user.id != self.state.actor_id:
            return await interaction.response.send_message("❌ This Fix Access screen belongs to another admin.", ephemeral=True)
        self.state.mode = select.values[0]
        await interaction.response.edit_message(embed=build_preview_embed(self.state), view=TargetPermissionRepairView(self.state))

    @discord.ui.button(label="Include Category Children", emoji="🗂️", style=discord.ButtonStyle.secondary, row=3)
    async def toggle_children(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if interaction.user.id != self.state.actor_id:
            return await interaction.response.send_message("❌ This Fix Access screen belongs to another admin.", ephemeral=True)
        if not isinstance(self.state.target, discord.CategoryChannel):
            return await interaction.response.send_message("Choose a category first. Child-channel repair only applies to categories.", ephemeral=True)
        self.state.include_children = not self.state.include_children
        await interaction.response.edit_message(embed=build_preview_embed(self.state), view=TargetPermissionRepairView(self.state))

    @discord.ui.button(label="Fix Missing Access", emoji="🛠️", style=discord.ButtonStyle.success, row=3)
    async def fix_missing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _actor_can_manage(interaction) or interaction.user.id != self.state.actor_id:
            return await interaction.response.send_message("❌ Manage Server, Manage Channels, or Administrator is required.", ephemeral=True)
        if self.state.target is None:
            return await interaction.response.send_message("Choose a channel/category first.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await _run_repair(interaction, self.state, clear_explicit_denies=False)
        if result is None:
            return
        self.state.last_token = result.token
        await interaction.followup.send(embed=_result_embed(result), view=TargetPermissionRepairView(self.state), ephemeral=True)

    @discord.ui.button(label="Resolve Explicit Denies", emoji="⚠️", style=discord.ButtonStyle.danger, row=3)
    async def resolve_denies(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _actor_can_manage(interaction) or interaction.user.id != self.state.actor_id:
            return await interaction.response.send_message("❌ Manage Server, Manage Channels, or Administrator is required.", ephemeral=True)
        if self.state.target is None:
            return await interaction.response.send_message("Choose a channel/category first.", ephemeral=True)
        audits = audit_targets(self.state.guild, self.state.target, feature=self.state.feature, mode=self.state.mode, include_children=self.state.include_children)
        conflicts = [(audit.target_name, audit.explicit_denies) for audit in audits if audit.explicit_denies]
        if not conflicts:
            return await interaction.response.send_message("✅ There are no explicit Dank Shield denies to resolve for this selection.", ephemeral=True)
        text = "\n".join(f"• **#{name}**: {', '.join(denies)}" for name, denies in conflicts[:10])
        await interaction.response.send_message(
            "⚠️ **This is the only path that clears explicit denies on Dank Shield's own overwrite.**\n"
            "It does not change other roles or members. Confirm only if these denies are accidental.\n\n" + text,
            view=ExplicitDenyConfirmView(self.state), ephemeral=True,
        )

    @discord.ui.button(label="Undo Repair", emoji="↩️", style=discord.ButtonStyle.secondary, row=4)
    async def undo(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _actor_can_manage(interaction) or interaction.user.id != self.state.actor_id:
            return await interaction.response.send_message("❌ You cannot restore this repair.", ephemeral=True)
        await interaction.response.send_modal(UndoTokenModal(self.state))


async def open_target_permission_repair(interaction: discord.Interaction) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    if not _actor_can_manage(interaction):
        return await interaction.response.send_message("❌ Manage Server, Manage Channels, or Administrator is required.", ephemeral=True)
    state = PermissionRepairState(guild=interaction.guild, actor_id=int(interaction.user.id))
    embed = build_preview_embed(state)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=TargetPermissionRepairView(state), ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=TargetPermissionRepairView(state), ephemeral=True)
    except Exception:
        await interaction.followup.send(embed=embed, view=TargetPermissionRepairView(state), ephemeral=True)


__all__ = [
    "TargetPermissionAudit", "TargetRepairResult", "PermissionRepairState",
    "TargetPermissionRepairView", "approved_public_permissions", "apply_target_repair",
    "audit_target", "audit_targets", "build_preview_embed", "open_target_permission_repair",
    "reauthorize_url", "undo_target_repair",
]
