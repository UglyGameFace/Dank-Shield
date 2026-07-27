from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

import discord


_BROWSER_TIMEOUT_SECONDS = 900
_ACTION_LOCKS: dict[str, asyncio.Lock] = {}

def _cfg_role_id(cfg: Any, key: str) -> int:
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return int(str(value))
    except Exception:
        pass

    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return int(str(value))
    except Exception:
        pass

    return 0


async def _can_review(interaction: discord.Interaction) -> bool:
    try:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False

        perms = interaction.user.guild_permissions
        if (
            perms.administrator
            or perms.manage_guild
            or perms.manage_roles
            or perms.moderate_members
            or perms.kick_members
            or perms.ban_members
        ):
            return True

        try:
            from stoney_verify.guild_config import get_guild_config

            cfg = await get_guild_config(interaction.guild.id)
            staff_ids = {
                role_id
                for role_id in (
                    _cfg_role_id(cfg, "staff_role_id"),
                    _cfg_role_id(cfg, "vc_staff_role_id"),
                    _cfg_role_id(cfg, "server_control_role_id"),
                )
                if role_id > 0
            }
            return any(int(role.id) in staff_ids for role in interaction.user.roles)
        except Exception:
            return False
    except Exception:
        return False


async def reply_ephemeral(
    interaction: discord.Interaction,
    content: str = "",
    **kwargs: Any,
) -> None:
    kwargs.setdefault("ephemeral", True)
    kwargs.setdefault("allowed_mentions", discord.AllowedMentions.none())
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(content, **kwargs)
        else:
            await interaction.followup.send(content, **kwargs)
    except Exception:
        pass


async def require_review(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        await reply_ephemeral(interaction, "❌ This must be used inside a server.")
        return False
    if not await _can_review(interaction):
        await reply_ephemeral(
            interaction,
            "❌ Member browsing requires a configured staff role or Administrator, "
            "Manage Server, Manage Roles, Moderate Members, Kick Members, or Ban Members.",
        )
        return False
    return True


def trim(value: Any, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def display_name(member: discord.Member) -> str:
    raw = str(
        getattr(member, "display_name", None)
        or getattr(member, "global_name", None)
        or getattr(member, "name", None)
        or member.id
    )
    return trim(discord.utils.escape_markdown(raw, as_needed=True), 80)


def timestamp(value: Optional[datetime], style: str = "R") -> str:
    if not isinstance(value, datetime):
        return "Unknown"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return f"<t:{int(value.timestamp())}:{style}>"


def action_lock(guild_id: int, target_id: int, action: str) -> asyncio.Lock:
    key = f"{int(guild_id)}:{int(target_id)}:{action}"
    lock = _ACTION_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _ACTION_LOCKS[key] = lock
    return lock


def _actor_permission(actor: discord.Member, action: str) -> bool:
    perms = actor.guild_permissions
    if perms.administrator:
        return True
    if action in {"add_role", "remove_role"}:
        return bool(perms.manage_roles or perms.manage_guild)
    if action == "verify":
        # The interaction-level staff gate already passed. Verification is
        # performed by Dank Shield's configured role service, not the actor.
        return True
    if action == "timeout":
        return bool(perms.moderate_members)
    if action == "kick":
        return bool(perms.kick_members)
    if action == "ban":
        return bool(perms.ban_members)
    if action in {"dm", "review"}:
        return True
    return False


def _bot_permission(me: discord.Member, action: str) -> bool:
    perms = me.guild_permissions
    if perms.administrator:
        return True
    if action in {"add_role", "remove_role", "verify"}:
        return bool(perms.manage_roles)
    if action == "timeout":
        return bool(perms.moderate_members)
    if action == "kick":
        return bool(perms.kick_members)
    if action == "ban":
        return bool(perms.ban_members)
    return True


def _target_is_staff_like(member: discord.Member) -> bool:
    perms = member.guild_permissions
    return bool(
        perms.administrator
        or perms.manage_guild
        or perms.manage_roles
        or perms.moderate_members
        or perms.kick_members
        or perms.ban_members
    )


def action_blockers(
    guild: discord.Guild,
    actor: discord.Member,
    target: discord.Member,
    action: str,
) -> list[str]:
    blockers: list[str] = []
    me = guild.me
    if not isinstance(me, discord.Member):
        return ["Dank Shield could not resolve its own server member."]
    if target.id == guild.owner_id:
        blockers.append("The server owner is protected.")
    if target.id == me.id:
        blockers.append("Dank Shield cannot act on itself.")
    if target.id == actor.id and action in {"timeout", "kick", "ban"}:
        blockers.append("You cannot use this panel to punish yourself.")
    if not _actor_permission(actor, action):
        blockers.append(f"You do not have the Discord permission required for {action.replace('_', ' ')}.")
    if not _bot_permission(me, action):
        blockers.append(f"Dank Shield is missing the Discord permission required for {action.replace('_', ' ')}.")

    if action not in {"dm", "review"}:
        try:
            if actor.id != guild.owner_id and target.top_role >= actor.top_role:
                blockers.append("The target is above or equal to your highest role.")
        except Exception:
            blockers.append("Your role hierarchy could not be verified.")
        try:
            if me.id != guild.owner_id and target.top_role >= me.top_role:
                blockers.append("The target is above or equal to Dank Shield's highest role.")
        except Exception:
            blockers.append("Dank Shield's role hierarchy could not be verified.")

    if action in {"timeout", "kick", "ban"} and _target_is_staff_like(target):
        if actor.id != guild.owner_id and not actor.guild_permissions.administrator:
            blockers.append("Staff-like targets require the server owner or an Administrator.")
    return blockers


def role_action_blockers(
    guild: discord.Guild,
    actor: discord.Member,
    target: discord.Member,
    role: discord.Role,
    action: str,
) -> list[str]:
    blockers = action_blockers(guild, actor, target, action)
    me = guild.me
    if role.is_default():
        blockers.append("The @everyone role cannot be changed.")
    if role.managed:
        blockers.append("Discord manages that role, so it cannot be changed manually.")
    if isinstance(me, discord.Member) and role >= me.top_role:
        blockers.append("Dank Shield's role must be above the selected role.")
    if actor.id != guild.owner_id and role >= actor.top_role:
        blockers.append("Your highest role must be above the selected role.")
    return blockers


async def record_member_action(
    *,
    guild_id: int,
    actor_id: int,
    target_id: int,
    action: str,
    reason: str,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    def _insert() -> None:
        try:
            from stoney_verify.globals import get_supabase

            sb = get_supabase()
            if not sb:
                return
            payload = {
                "guild_id": str(int(guild_id)),
                "event_type": "member_browser_action",
                "actor_id": str(int(actor_id)),
                "target_id": str(int(target_id)),
                "message": trim(f"{action}: {reason}", 1000),
                "metadata": {"action": action, **dict(metadata or {})},
                "meta": {"action": action, **dict(metadata or {})},
                "created_at": discord.utils.utcnow().isoformat(),
            }
            sb.table("activity_feed_events").insert(payload).execute()
        except Exception:
            return

    try:
        await asyncio.to_thread(_insert)
    except Exception:
        pass


async def apply_staff_basic_verification(
    guild: discord.Guild,
    target: discord.Member,
) -> tuple[bool, str]:
    """Use the canonical basic-verification path without bypassing ID verification."""
    try:
        from stoney_verify.guild_config import get_guild_config
        from stoney_verify.setup_engine.loader import snapshot_from_config
        from stoney_verify.setup_engine.verification_modes import effective_verification_mode
        from stoney_verify.verification_new.basic_verify import apply_basic_verification

        cfg = await get_guild_config(int(guild.id), refresh=True)
        mode = effective_verification_mode(guild, cfg)
        if mode != "basic_button":
            return (
                False,
                "This server uses the protected ID/ticket verification flow. "
                "Open the member's verification ticket and approve them there so token, "
                "ticket, transcript, and decision records stay consistent.",
            )

        snapshot = snapshot_from_config(int(guild.id), cfg)
        unverified_role = guild.get_role(int(snapshot.unverified_role_id or 0))
        if not isinstance(unverified_role, discord.Role):
            return False, "The configured Unverified role could not be resolved. Run `/dank setup` and repair verification roles."
        if unverified_role not in target.roles:
            return False, "That member no longer has the configured Unverified role. Refresh the roster before acting."

        ok, message = await apply_basic_verification(target)
        if ok:
            return True, f"{target.mention} was verified through the configured Basic Button role flow."
        return False, message
    except Exception as exc:
        return False, f"Verification service failed: {type(exc).__name__}."


class OwnedView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout: float = _BROWSER_TIMEOUT_SECONDS) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.owner_id:
            await reply_ephemeral(interaction, "❌ This private member browser belongs to another staff member.")
            return False
        if not await require_review(interaction):
            return False
        return True

    async def on_timeout(self) -> None:
        for item in self.children:
            try:
                item.disabled = True
            except Exception:
                pass



__all__ = [
    "OwnedView",
    "action_blockers",
    "action_lock",
    "apply_staff_basic_verification",
    "display_name",
    "record_member_action",
    "reply_ephemeral",
    "require_review",
    "role_action_blockers",
    "timestamp",
    "trim",
]
