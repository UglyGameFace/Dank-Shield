from __future__ import annotations

"""Improve setup permission repair reporting.

This keeps the existing repair engine, but makes the preview/apply result act
like a truth report: what was repaired, what is already clean, and what still
needs a human prerequisite such as bot permissions, role hierarchy, or missing
saved setup mappings.

Removal path: fold this reporting logic directly into setup_permission_repair_guard.py
when that file is next consolidated.
"""

from typing import Any

import discord

_PATCHED = False
_ORIGINAL_PREVIEW_OR_APPLY: Any = None
_ORIGINAL_RESULT_EMBED: Any = None


_ROLE_ATTRS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Staff role", ("staff_role_id", "ticket_staff_role_id", "support_role_id", "vc_staff_role_id")),
    ("Server-control role", ("server_control_role_id", "control_role_id", "perm_role_id", "bot_manager_role_id")),
    ("Unverified role", ("unverified_role_id", "pending_role_id", "waiting_role_id")),
    ("Verified role", ("verified_role_id", "approved_role_id")),
)

_CHANNEL_ATTRS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Start/public category", ("start_category_id", "welcome_category_id", "onboarding_category_id")),
    ("Active tickets category", ("ticket_category_id", "active_ticket_category_id", "open_ticket_category_id")),
    ("Ticket archive category", ("ticket_archive_category_id", "archive_category_id", "closed_ticket_category_id")),
    ("Staff tools category", ("management_category_id", "staff_tools_category_id")),
    ("Welcome channel", ("welcome_channel_id",)),
    ("Rules channel", ("rules_channel_id",)),
    ("Verification start channel", ("verify_channel_id", "verification_channel_id")),
    ("Ticket panel channel", ("ticket_panel_channel_id", "support_channel_id", "panel_channel_id")),
    ("VC verification queue channel", ("vc_verify_queue_channel_id", "vc_queue_channel_id", "vc_request_channel_id", "vc_verify_requests_channel_id")),
    ("Transcripts channel", ("transcripts_channel_id", "transcript_channel_id")),
    ("Modlog channel", ("modlog_channel_id", "mod_log_channel_id")),
    ("Raid/security log channel", ("raidlog_channel_id", "raid_log_channel_id", "security_log_channel_id")),
    ("Join/leave log channel", ("join_log_channel_id", "join_leave_log_channel_id", "joinlog_channel_id")),
    ("Bot status channel", ("status_channel_id", "bot_status_channel_id", "uptime_channel_id", "health_channel_id")),
    ("Voice verification channel", ("vc_verify_channel_id", "voice_verify_channel_id")),
)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        if not text or text.lower() in {"none", "null"}:
            return int(default)
        return int(text)
    except Exception:
        return int(default)


def _cfg_value(cfg: Any, attr: str, default: Any = 0) -> Any:
    try:
        value = getattr(cfg, attr, None)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(attr)
            if value is not None:
                return value
    except Exception:
        pass
    for bucket in ("settings", "config", "metadata", "meta"):
        try:
            nested = getattr(cfg, bucket, None)
            if isinstance(nested, dict) and nested.get(attr) is not None:
                return nested.get(attr)
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, dict) and nested.get(attr) is not None:
                    return nested.get(attr)
        except Exception:
            pass
    return default


def _cfg_int(cfg: Any, *attrs: str) -> int:
    for attr in attrs:
        value = _safe_int(_cfg_value(cfg, attr, 0), 0)
        if value > 0:
            return value
    return 0


def _clip(lines: list[str], *, limit: int = 950, empty: str = "None") -> str:
    if not lines:
        return empty
    out: list[str] = []
    total = 0
    for line in lines:
        text = str(line)
        if total + len(text) + 1 > limit:
            out.append(f"…and {len(lines) - len(out)} more")
            break
        out.append(text)
        total += len(text) + 1
    return "\n".join(out) or empty


async def _truth_breakdown(guild: discord.Guild) -> dict[str, list[str]]:
    from stoney_verify.guild_config import get_guild_config

    cfg = await get_guild_config(int(guild.id), refresh=True)
    manual: list[str] = []
    missing_mappings: list[str] = []
    prerequisites: list[str] = []

    me = getattr(guild, "me", None)
    if not isinstance(me, discord.Member):
        prerequisites.append("Bot member could not be resolved from Discord cache.")
    else:
        perms = me.guild_permissions
        if not perms.manage_channels:
            prerequisites.append("Bot is missing **Manage Channels**; permission repair cannot apply overwrites.")
        if not perms.view_audit_log:
            manual.append("Bot is missing **View Audit Log**; activity/verification fallback evidence may be weaker.")
        if not perms.manage_roles:
            manual.append("Bot is missing **Manage Roles**; role creation/self-role repair cannot be fixed by channel permission repair.")
        if not perms.kick_members:
            manual.append("Bot is missing **Kick Members**; member cleanup/purge actions will be blocked even if channels are repaired.")

    for label, attrs in _ROLE_ATTRS:
        rid = _cfg_int(cfg, *attrs)
        role = guild.get_role(rid) if rid > 0 else None
        if rid <= 0:
            missing_mappings.append(f"{label} is not saved in setup config.")
        elif not isinstance(role, discord.Role):
            missing_mappings.append(f"{label} id `{rid}` is saved but no longer exists.")
        elif isinstance(me, discord.Member) and not role.is_default() and role >= me.top_role:
            manual.append(f"Move the Dank Shield bot role above {role.mention}; role hierarchy blocks role-related repairs.")

    for label, attrs in _CHANNEL_ATTRS:
        cid = _cfg_int(cfg, *attrs)
        channel = guild.get_channel(cid) if cid > 0 else None
        if cid <= 0:
            missing_mappings.append(f"{label} is not saved in setup config.")
        elif channel is None:
            missing_mappings.append(f"{label} id `{cid}` is saved but the channel/category no longer exists.")

    if not missing_mappings:
        manual.append("All known setup mappings exist. Remaining failures are likely Discord permission/hierarchy/API errors, not missing config.")

    return {"manual": manual, "missing_mappings": missing_mappings, "prerequisites": prerequisites}


async def _truth_preview_or_apply(guild: discord.Guild, *, apply: bool) -> dict[str, Any]:
    result = await _ORIGINAL_PREVIEW_OR_APPLY(guild, apply=apply)
    truth = await _truth_breakdown(guild)
    notes = list(result.get("notes") or [])
    if truth["prerequisites"]:
        notes.insert(0, "Prerequisite blocker(s): " + "; ".join(truth["prerequisites"][:3]))
    result["notes"] = notes
    result["manual"] = truth["manual"]
    result["missing_mappings"] = truth["missing_mappings"]
    result["prerequisites"] = truth["prerequisites"]
    if truth["prerequisites"]:
        result["ok"] = False
        if not result.get("error"):
            result["error"] = "Discord prerequisites block complete automatic repair."
    return result


def _truth_result_embed(result: dict[str, Any]) -> discord.Embed:
    embed = _ORIGINAL_RESULT_EMBED(result)
    try:
        embed.description = (
            "Truth report for setup permission repair. It separates auto-repairable overwrite drift from missing mappings, "
            "bot prerequisites, role hierarchy, and manual Discord blockers."
        )
    except Exception:
        pass
    missing = list(result.get("missing_mappings") or [])
    prereq = list(result.get("prerequisites") or [])
    manual = list(result.get("manual") or [])
    if prereq:
        embed.add_field(name="❌ Must Fix First", value=_clip(prereq, empty="None"), inline=False)
    if missing:
        embed.add_field(name="⚠️ Missing / Deleted Setup Mappings", value=_clip(missing, empty="None"), inline=False)
    if manual:
        embed.add_field(name="⚠️ Manual / Not Channel-Repairable", value=_clip(manual, empty="None"), inline=False)
    try:
        embed.set_footer(text="Fix scope: saved setup channels/categories + ticket/archive/staff children. It does not blindly rewrite unrelated server channels.")
    except Exception:
        pass
    return embed


def apply() -> bool:
    global _PATCHED, _ORIGINAL_PREVIEW_OR_APPLY, _ORIGINAL_RESULT_EMBED
    if _PATCHED:
        return True
    try:
        from stoney_verify.startup_guards import setup_permission_repair_guard as repair

        original_preview = getattr(repair, "_preview_or_apply", None)
        original_embed = getattr(repair, "_result_embed", None)
        if not callable(original_preview) or not callable(original_embed):
            return False
        if getattr(original_preview, "_truth_wrapped", False):
            return True
        _ORIGINAL_PREVIEW_OR_APPLY = original_preview
        _ORIGINAL_RESULT_EMBED = original_embed
        setattr(_truth_preview_or_apply, "_truth_wrapped", True)
        repair._preview_or_apply = _truth_preview_or_apply
        repair._result_embed = _truth_result_embed
        _PATCHED = True
        print("🛠️ setup_permission_repair_truth_guard active; permission repair now reports prerequisites, missing mappings, and manual blockers")
        return True
    except Exception as exc:
        print(f"⚠️ setup_permission_repair_truth_guard failed: {exc!r}")
        return False


apply()

__all__ = ["apply"]
