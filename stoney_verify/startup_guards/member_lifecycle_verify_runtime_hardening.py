from __future__ import annotations

"""Runtime hardening for Basic Verify, setup visibility, schema lag, and modlog aliases.

Member join/leave routing now lives only in member_lifecycle_router_guard.
This file intentionally does not patch member lifecycle listeners anymore, because
that old runtime override could reintroduce join-card posts into the welcome
channel. The native router is the single source of truth.
"""

import re
from typing import Any, Optional

import discord

_INSTALLED = False
_BASIC_VERIFY_FALLBACK_INSTALLED = False


def _log(message: str) -> None:
    try:
        print(f"✅ member_lifecycle_verify_runtime_hardening {message}")
    except Exception:
        pass


def _missing_column(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc!r} {exc}"
    match = re.search(r"Could not find the '([^']+)' column", text)
    return str(match.group(1)) if match else ""


def _strip_none(payload: dict[str, Any]) -> dict[str, Any]:
    return {str(k): v for k, v in dict(payload or {}).items() if v is not None}


def _schema_safe_execute(label: str, payload: dict[str, Any], executor: Any) -> Any:
    clean = _strip_none(payload)
    stripped: list[str] = []
    for _ in range(20):
        try:
            result = executor(clean)
            if stripped:
                _log(f"{label} saved without missing schema columns: {', '.join(stripped)}")
            return result
        except Exception as exc:
            col = _missing_column(exc)
            if col and col in clean:
                clean.pop(col, None)
                stripped.append(col)
                continue
            raise
    return executor(clean)


def _install_basic_verify_fallback() -> None:
    global _BASIC_VERIFY_FALLBACK_INSTALLED
    if _BASIC_VERIFY_FALLBACK_INSTALLED:
        return
    try:
        from stoney_verify.globals import bot
        from stoney_verify.setup_engine.verification_modes import BASIC_VERIFY_CUSTOM_ID
        from stoney_verify.verification_new.basic_verify import maybe_handle_basic_verify_interaction
    except Exception as exc:
        _log(f"basic verify fallback unavailable: {type(exc).__name__}: {exc}")
        return
    if bot is None:
        return

    @bot.listen("on_interaction")
    async def _dank_basic_verify_fallback(interaction: discord.Interaction) -> None:
        try:
            if getattr(interaction, "type", None) is not discord.InteractionType.component:
                return
            data = getattr(interaction, "data", None) or {}
            if str(data.get("custom_id") or "") != BASIC_VERIFY_CUSTOM_ID:
                return
            if getattr(getattr(interaction, "response", None), "is_done", lambda: False)():
                return
            await maybe_handle_basic_verify_interaction(interaction)
        except Exception as exc:
            try:
                print(f"⚠️ basic_verify fallback failed: {type(exc).__name__}: {exc}")
            except Exception:
                pass

    _BASIC_VERIFY_FALLBACK_INSTALLED = True
    _log("basic verify component fallback active")


def _patch_ticket_panel_basic_verify_warning() -> None:
    try:
        from stoney_verify.commands_ext import public_ticket_panel_clean as panel
    except Exception:
        return
    original = getattr(panel, "_maybe_post_verification_panel", None)
    if not callable(original) or getattr(original, "_basic_mode_hardened", False):
        return

    async def _patched_maybe_post_verification_panel(channel: discord.TextChannel, owner: discord.Member, row: dict[str, Any]) -> str:
        try:
            if getattr(panel, "_canon")(row) != "verification":
                return ""
            from stoney_verify.startup_guards import unverified_ticket_panel_flow as verify_flow
            if not await verify_flow._is_unverified_only_member(owner):
                return ""
            cfg = await verify_flow._get_guild_config_safe(channel.guild.id)
            if not verify_flow._should_auto_route_unverified_ticket(cfg):
                return ""
        except Exception:
            pass
        return await original(channel, owner, row)

    try:
        setattr(_patched_maybe_post_verification_panel, "_basic_mode_hardened", True)
    except Exception:
        pass
    panel._maybe_post_verification_panel = _patched_maybe_post_verification_panel
    _log("ticket panel Basic Verify warning hardening active")


def _patch_setup_join_leave_alias_picker() -> None:
    try:
        from stoney_verify.commands_ext import public_setup_full_customization as full
    except Exception:
        return
    if getattr(full, "_JOIN_LEAVE_ALIAS_PICKER_PATCHED", False):
        return

    aliases = (
        "join_leave_channel_id",
        "member_join_leave_log_channel_id",
        "member_lifecycle_log_channel_id",
        "member_log_channel_id",
        "member_logs_channel_id",
        "join_log_channel_id",
        "join_exit_log_channel_id",
        "joinlog_channel_id",
        "joinleave_channel_id",
        "welcome_exit_channel_id",
        "welcome_exit_log_channel_id",
        "leave_log_channel_id",
        "welcome_leave_channel_id",
        "leave_channel_id",
    )

    class PatchedLogStatusCustomizationView(full.SetupBackView):  # type: ignore[name-defined]
        def __init__(self) -> None:
            super().__init__()
            self.add_item(full.SaveChannelSelect(placeholder="Ticket transcripts channel", columns=("transcripts_channel_id",), channel_types=[discord.ChannelType.text], row=0, need_files=True))
            self.add_item(full.SaveChannelSelect(placeholder="Moderation log channel", columns=("modlog_channel_id",), also_same=("raidlog_channel_id", "raid_log_channel_id", "force_verify_log_channel_id", "staff_join_audit_channel_id", "member_audit_log_channel_id", "staff_log_channel_id", "staff_logs_channel_id", "audit_log_channel_id"), channel_types=[discord.ChannelType.text], row=1))
            self.add_item(full.SaveChannelSelect(placeholder="Join / leave log channel — not welcome", columns=("join_leave_log_channel_id",), also_same=aliases, channel_types=[discord.ChannelType.text], row=2))
            self.add_item(full.SaveChannelSelect(placeholder="Bot status / uptime channel", columns=("status_channel_id",), also_same=("bot_status_channel_id", "uptime_channel_id"), channel_types=[discord.ChannelType.text], row=3))

    full.LogStatusCustomizationView = PatchedLogStatusCustomizationView
    full._JOIN_LEAVE_ALIAS_PICKER_PATCHED = True
    _log("setup Logs + Status join/leave alias picker active")


def _patch_member_lifecycle_router() -> None:
    """No-op by design.

    The native router now owns member lifecycle routing. Keeping this symbol helps
    older static checks/imports, but it must not register listeners or send join
    cards to the public welcome channel.
    """
    _log("member lifecycle router patch skipped; native router owns no-welcome-leak routing")


def _patch_join_context_schema_fallback() -> None:
    try:
        from stoney_verify.members_new import join_context_service as svc
    except Exception:
        return
    if getattr(svc, "_SCHEMA_SAFE_JOIN_CONTEXT", False):
        return

    def _guild_members_update_member_sync(sb: Any, guild_id: str, user_id: str, payload: dict[str, Any]) -> Any:
        def run(clean: dict[str, Any]) -> Any:
            return sb.table("guild_members").update(clean).eq("guild_id", str(guild_id)).eq("user_id", str(user_id)).execute()
        return _schema_safe_execute("guild_members join context", payload, run)

    def _member_joins_insert_sync(sb: Any, payload: dict[str, Any]) -> Any:
        def run(clean: dict[str, Any]) -> Any:
            return sb.table("member_joins").insert(clean).execute()
        return _schema_safe_execute("member_joins", payload, run)

    svc._guild_members_update_member_sync = _guild_members_update_member_sync
    svc._member_joins_insert_sync = _member_joins_insert_sync
    svc._SCHEMA_SAFE_JOIN_CONTEXT = True
    _log("join context schema fallback active")


def _patch_modlog_alias_resolution() -> None:
    try:
        from stoney_verify import modlog
        from stoney_verify.guild_config import get_guild_config
    except Exception:
        return
    if getattr(modlog, "_STAFF_AUDIT_ALIAS_LOOKUP", False):
        return
    original = getattr(modlog, "_get_modlog_channel_async", None)
    if not callable(original):
        return

    async def _patched_get_modlog_channel_async(guild: discord.Guild) -> Optional[discord.TextChannel]:
        try:
            cfg = await get_guild_config(int(guild.id), refresh=False)
            cid = modlog._cfg_id_value(
                cfg,
                "modlog_channel_id",
                "mod_log_channel_id",
                "logs_channel_id",
                "staff_join_audit_channel_id",
                "member_audit_log_channel_id",
                "staff_log_channel_id",
                "staff_logs_channel_id",
                "audit_log_channel_id",
                "raidlog_channel_id",
                "raid_log_channel_id",
                "force_verify_log_channel_id",
            )
            if cid > 0:
                channel = guild.get_channel(int(cid))
                if modlog._same_guild_text_channel(channel, guild):
                    return channel
                try:
                    fetched = await guild.fetch_channel(int(cid))
                    if modlog._same_guild_text_channel(fetched, guild):
                        return fetched
                except Exception:
                    pass
        except Exception:
            pass
        return await original(guild)

    modlog._get_modlog_channel_async = _patched_get_modlog_channel_async
    modlog._STAFF_AUDIT_ALIAS_LOOKUP = True
    _log("modlog staff-audit alias lookup active")


def install() -> bool:
    global _INSTALLED
    if _INSTALLED:
        return True
    _install_basic_verify_fallback()
    _patch_ticket_panel_basic_verify_warning()
    _patch_setup_join_leave_alias_picker()
    _patch_member_lifecycle_router()
    _patch_join_context_schema_fallback()
    _patch_modlog_alias_resolution()
    _INSTALLED = True
    _log("active")
    return True


install()

__all__ = ["install"]
