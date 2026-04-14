from __future__ import annotations

import os
import asyncio
import traceback
import discord
from discord.ext import commands as ext_commands

from .globals import bot, DISCORD_TOKEN, GUILD_ID, get_supabase, claim_startup_flag

# ============================================================
# IMPORT ORDER MATTERS
# ------------------------------------------------------------
# commands.py defines a no-op @bot.event on_ready
# events.py defines the real @bot.event on_ready that starts
# vc sweeper + initial member sync protections.
#
# Therefore:
#   commands MUST be imported BEFORE events
# so that events.py is the final on_ready owner.
# ============================================================

# Normalize a few compatibility globals before importing modules that depend on them.
try:
    from . import globals as _g  # type: ignore

    if not hasattr(_g, "SUPABASE_ENABLED"):
        _g.SUPABASE_ENABLED = bool(get_supabase())
    if not hasattr(_g, "VC_SESSIONS_TABLE"):
        _g.VC_SESSIONS_TABLE = "vc_verify_sessions"
    if not hasattr(_g, "VC_VERIFY_ACCESS_MINUTES"):
        _g.VC_VERIFY_ACCESS_MINUTES = 30
    if not hasattr(_g, "TICKET_LAST_ACTIVITY"):
        _g.TICKET_LAST_ACTIVITY = {}
except Exception:
    pass

# EXISTING MODULES / REAL OWNERS
from . import tickets  # noqa: F401
from . import vc_verify  # noqa: F401
from . import modlog  # noqa: F401
from . import verify_ui  # noqa: F401
from . import transcripts  # noqa: F401
from . import timers  # noqa: F401
from . import commands  # noqa: F401
from . import events  # noqa: F401

# Ticket event bridge (thin listener layer)
try:
    from . import ticket_events  # noqa: F401
except Exception as e:
    ticket_events = None  # type: ignore
    print("⚠️ ticket_events import failed:", repr(e))

# SUPPORTING SERVICES ONLY
from .members_new import service as _members_service  # noqa: F401
from .tickets_new import service as _tickets_service  # noqa: F401
from .tickets_new import transcript_service as _transcript_service  # noqa: F401
from .tickets_new import panel as _tickets_panel  # noqa: F401
from .tickets_new import sync_service as _tickets_sync_service  # noqa: F401

# API SERVERS
from .api_new.server import start_api

try:
    from .bot_actions_api import start_bot_actions_server
except Exception:
    start_bot_actions_server = None

# WORKERS
from .workers.bot_command_worker import start_worker
from .workers.metrics_sync_worker import start_metrics_worker
from .workers.ticket_automation_worker import start_ticket_automation_worker


_STARTED_LEGACY_ACTIONS_API = False
_STARTED_NEW_ACTIONS_API = False
_STARTED_WORKERS = False
_DID_SLASH_MAINTENANCE = False
_DID_GLOBAL_COMMAND_CLEANUP = False
_DID_KICK_TIMER_RESUME = False
_DID_DEPARTED_RECONCILE = False
_DID_TICKET_SYNC = False
_DID_TICKET_EVENTS_SETUP = False
_DID_PERMISSION_SELF_CHECK = False

_STARTUP_BACKGROUND_TASK: asyncio.Task | None = None

# Foreign prefix commands owned by other bots in the same server.
# We ignore them here so this verify bot does not spam CommandNotFound.
_IGNORED_FOREIGN_PREFIX_COMMANDS = {
    "ask",
}


def _env_true(name: str, default: bool = False) -> bool:
    try:
        raw = os.getenv(name, "")
        if not raw:
            return bool(default)
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}
    except Exception:
        return bool(default)


def _track_background_task(task: asyncio.Task, *, label: str = "") -> None:
    try:
        def _done_callback(t: asyncio.Task) -> None:
            try:
                exc = t.exception()
                if exc is not None:
                    if label:
                        print(f"⚠️ Background task failed [{label}]:", repr(exc))
                    else:
                        print("⚠️ Background task failed:", repr(exc))
            except asyncio.CancelledError:
                if label:
                    print(f"ℹ️ Background task cancelled [{label}]")
            except Exception:
                pass

        task.add_done_callback(_done_callback)
    except Exception:
        pass


def _setup_ticket_events_once() -> None:
    global _DID_TICKET_EVENTS_SETUP

    if _DID_TICKET_EVENTS_SETUP:
        return

    _DID_TICKET_EVENTS_SETUP = True

    if ticket_events is None:
        print("⚠️ ticket_events unavailable; listener registration skipped.")
        return

    try:
        if hasattr(ticket_events, "setup"):
            ticket_events.setup(bot)
            print("✅ ticket_events.setup(bot) registered.")
        else:
            print("⚠️ ticket_events.setup not found; listener registration skipped.")
    except Exception as e:
        print("❌ ticket_events.setup(bot) failed:", repr(e))


async def _resolve_runtime_guild() -> discord.Guild | None:
    try:
        guild_id_int = int(str(GUILD_ID or "0") or 0)
    except Exception:
        guild_id_int = 0

    if not guild_id_int:
        print("⚠️ GUILD_ID missing or invalid.")
        return None

    guild = bot.get_guild(guild_id_int)
    if guild is not None:
        return guild

    try:
        await bot.fetch_guild(guild_id_int)
        guild = bot.get_guild(guild_id_int)
        if guild is not None:
            return guild

        print(
            "⚠️ fetch_guild succeeded but guild is still not in cache; "
            "startup tasks that require a cached Guild object will be skipped."
        )
        return None
    except Exception as e:
        print("⚠️ Could not resolve guild:", repr(e))
        return None


def _role_check_line(
    *,
    role_obj: discord.Role | None,
    bot_top_role: discord.Role | None,
    label: str,
) -> tuple[str, bool]:
    try:
        if role_obj is None:
            return (f"{label}: MISSING", False)

        if bot_top_role is None:
            return (f"{label}: UNKNOWN (bot top role missing)", False)

        ok = int(bot_top_role.position) > int(role_obj.position)
        if ok:
            return (f"{label}: OK ({role_obj.name})", True)

        return (
            f"{label}: FAIL ({role_obj.name} is at/above bot top role {bot_top_role.name})",
            False,
        )
    except Exception as e:
        return (f"{label}: ERROR ({repr(e)})", False)


def _perm_check_line(
    *,
    perms: discord.Permissions,
    perm_name: str,
    label: str,
) -> tuple[str, bool]:
    try:
        ok = bool(getattr(perms, perm_name, False))
        return (f"{label}: {'OK' if ok else 'MISSING'}", ok)
    except Exception as e:
        return (f"{label}: ERROR ({repr(e)})", False)


async def _run_permission_self_check_once() -> None:
    global _DID_PERMISSION_SELF_CHECK

    if _DID_PERMISSION_SELF_CHECK:
        return

    if not claim_startup_flag("permission_self_check"):
        _DID_PERMISSION_SELF_CHECK = True
        print("ℹ️ Startup permission self-check already claimed elsewhere; skipping here.")
        return

    _DID_PERMISSION_SELF_CHECK = True

    guild = await _resolve_runtime_guild()
    if guild is None:
        print("⚠️ Permission self-check skipped: guild could not be resolved.")
        return

    try:
        me = guild.me
        if me is None and getattr(bot, "user", None):
            try:
                me = await guild.fetch_member(bot.user.id)  # type: ignore[arg-type]
            except Exception:
                me = None

        if me is None:
            print("⚠️ Permission self-check skipped: bot member could not be resolved.")
            return

        perms = me.guild_permissions
        bot_top_role = getattr(me, "top_role", None)

        try:
            from .globals import (
                UNVERIFIED_ROLE_ID,
                VERIFIED_ROLE_ID,
                RESIDENT_ROLE_ID,
                STAFF_ROLE_ID,
                MODLOG_CHANNEL_ID,
            )
        except Exception:
            UNVERIFIED_ROLE_ID = 0  # type: ignore[assignment]
            VERIFIED_ROLE_ID = 0  # type: ignore[assignment]
            RESIDENT_ROLE_ID = 0  # type: ignore[assignment]
            STAFF_ROLE_ID = 0  # type: ignore[assignment]
            MODLOG_CHANNEL_ID = 0  # type: ignore[assignment]

        required_perm_lines: list[tuple[str, bool]] = [
            _perm_check_line(perms=perms, perm_name="view_audit_log", label="View Audit Log"),
            _perm_check_line(perms=perms, perm_name="manage_roles", label="Manage Roles"),
            _perm_check_line(perms=perms, perm_name="kick_members", label="Kick Members"),
            _perm_check_line(perms=perms, perm_name="ban_members", label="Ban Members"),
            _perm_check_line(perms=perms, perm_name="moderate_members", label="Moderate Members"),
            _perm_check_line(perms=perms, perm_name="manage_channels", label="Manage Channels"),
            _perm_check_line(perms=perms, perm_name="send_messages", label="Send Messages"),
            _perm_check_line(perms=perms, perm_name="read_message_history", label="Read Message History"),
            _perm_check_line(perms=perms, perm_name="attach_files", label="Attach Files"),
        ]

        hierarchy_lines: list[tuple[str, bool]] = [
            _role_check_line(
                role_obj=guild.get_role(int(UNVERIFIED_ROLE_ID or 0)) if int(UNVERIFIED_ROLE_ID or 0) > 0 else None,
                bot_top_role=bot_top_role,
                label="Hierarchy > Unverified",
            ),
            _role_check_line(
                role_obj=guild.get_role(int(VERIFIED_ROLE_ID or 0)) if int(VERIFIED_ROLE_ID or 0) > 0 else None,
                bot_top_role=bot_top_role,
                label="Hierarchy > Verified",
            ),
            _role_check_line(
                role_obj=guild.get_role(int(RESIDENT_ROLE_ID or 0)) if int(RESIDENT_ROLE_ID or 0) > 0 else None,
                bot_top_role=bot_top_role,
                label="Hierarchy > Resident",
            ),
            _role_check_line(
                role_obj=guild.get_role(int(STAFF_ROLE_ID or 0)) if int(STAFF_ROLE_ID or 0) > 0 else None,
                bot_top_role=bot_top_role,
                label="Hierarchy > Staff",
            ),
        ]

        modlog_channel_line = "Modlog Channel: UNKNOWN"
        modlog_channel_ok = False
        try:
            modlog_channel = None
            if int(MODLOG_CHANNEL_ID or 0) > 0:
                modlog_channel = guild.get_channel(int(MODLOG_CHANNEL_ID))
                if modlog_channel is None:
                    try:
                        modlog_channel = await guild.fetch_channel(int(MODLOG_CHANNEL_ID))
                    except Exception:
                        modlog_channel = None

            if isinstance(modlog_channel, discord.TextChannel):
                ch_perms = modlog_channel.permissions_for(me)
                modlog_channel_ok = bool(
                    ch_perms.view_channel
                    and ch_perms.send_messages
                    and ch_perms.embed_links
                    and ch_perms.read_message_history
                )
                if modlog_channel_ok:
                    modlog_channel_line = f"Modlog Channel: OK (#{modlog_channel.name})"
                else:
                    modlog_channel_line = (
                        f"Modlog Channel: FAIL (#{modlog_channel.name} missing one of: "
                        f"View Channel / Send Messages / Embed Links / Read Message History)"
                    )
            else:
                modlog_channel_line = "Modlog Channel: MISSING OR NOT TEXT CHANNEL"
        except Exception as e:
            modlog_channel_line = f"Modlog Channel: ERROR ({repr(e)})"
            modlog_channel_ok = False

        all_lines = [line for line, _ in required_perm_lines] + [line for line, _ in hierarchy_lines] + [modlog_channel_line]
        all_ok = all(ok for _, ok in required_perm_lines) and all(ok for _, ok in hierarchy_lines) and modlog_channel_ok

        print("🔎 Startup permission self-check:")
        for line in all_lines:
            print(f"   - {line}")

        embed = discord.Embed(
            title="🩺 Bot Permission / Hierarchy Self-Check",
            color=discord.Color.green() if all_ok else discord.Color.red(),
            timestamp=discord.utils.utcnow(),
            description=(
                "Startup self-check for moderation attribution, role actions, and logging."
                if all_ok
                else "One or more startup checks failed. Staff action attribution or moderation flows may be unreliable."
            ),
        )

        try:
            embed.add_field(
                name="Bot",
                value=(
                    f"{me.mention}\n"
                    f"Top role: `{getattr(bot_top_role, 'name', 'Unknown')}` "
                    f"(`{getattr(bot_top_role, 'id', '0')}`)"
                ),
                inline=False,
            )
        except Exception:
            pass

        embed.add_field(
            name="Required Permissions",
            value="\n".join([line for line, _ in required_perm_lines])[:1024],
            inline=False,
        )
        embed.add_field(
            name="Role Hierarchy",
            value="\n".join([line for line, _ in hierarchy_lines])[:1024],
            inline=False,
        )
        embed.add_field(
            name="Logging Channel",
            value=modlog_channel_line[:1024],
            inline=False,
        )

        if not all_ok:
            missing_items = [line for line, ok in required_perm_lines if not ok]
            bad_hierarchy = [line for line, ok in hierarchy_lines if not ok]
            notes = missing_items + bad_hierarchy
            if not modlog_channel_ok:
                notes.append(modlog_channel_line)
            embed.add_field(
                name="What Needs Fixing",
                value="\n".join(notes)[:1024] if notes else "Unknown startup check failure.",
                inline=False,
            )

        try:
            ch = modlog._get_modlog_channel(guild)
            if ch is not None:
                await ch.send(embed=embed)
        except Exception as e:
            print("⚠️ Failed posting startup permission self-check to modlog:", repr(e))

    except Exception as e:
        print("⚠️ Permission self-check failed:", repr(e))
        try:
            traceback.print_exc()
        except Exception:
            pass


async def _maybe_resume_kick_timers_once() -> None:
    global _DID_KICK_TIMER_RESUME

    if _DID_KICK_TIMER_RESUME:
        return

    _DID_KICK_TIMER_RESUME = True

    try:
        if hasattr(commands, "kick_timer_resume_all"):
            await commands.kick_timer_resume_all()
            print("✅ Resumed persisted kick timers.")
        else:
            print("ℹ️ commands.kick_timer_resume_all not found; skipping kick timer resume.")
    except Exception as e:
        print("⚠️ Kick timer resume failed:", repr(e))


async def _maybe_run_departed_reconcile_once() -> None:
    global _DID_DEPARTED_RECONCILE

    if _DID_DEPARTED_RECONCILE:
        return

    _DID_DEPARTED_RECONCILE = True

    try:
        from .events_new.members import (
            run_full_member_sync_for_guild,
            run_departed_reconciliation_for_guild,
        )
    except Exception as e:
        print("⚠️ Failed importing reconcile helpers:", repr(e))
        return

    guild = await _resolve_runtime_guild()
    if guild is None:
        print("⚠️ Skipping departed reconcile: guild could not be resolved from cache.")
        return

    initial_sync_started = False
    initial_sync_done = False

    try:
        initial_sync_started = bool(getattr(bot, "_initial_member_sync_started", False))
    except Exception:
        initial_sync_started = False

    try:
        initial_sync_done = bool(getattr(bot, "_initial_member_sync_done", False))
    except Exception:
        initial_sync_done = False

    if initial_sync_done:
        print("✅ events.py already completed initial sync; fallback full member sync not needed.")
    elif initial_sync_started:
        print("ℹ️ events.py initial sync already started; skipping fallback full member sync.")
    else:
        try:
            print("🧩 events.py did not start initial sync; running fallback full member sync...")
            summary_full = await run_full_member_sync_for_guild(guild)
            print("✅ Fallback startup member sync complete:", summary_full)

            try:
                bot._initial_member_sync_started = True  # type: ignore[attr-defined]
            except Exception:
                pass

            try:
                bot._initial_member_sync_done = True  # type: ignore[attr-defined]
            except Exception:
                pass
        except Exception as e:
            print("❌ Fallback member sync failed:", repr(e))

    try:
        print("🧹 Running departed-member reconciliation...")
        summary_departed = await run_departed_reconciliation_for_guild(guild)
        print("✅ Departed reconciliation complete:", summary_departed)
    except Exception as e:
        print("❌ Departed reconcile failed:", repr(e))


async def _maybe_run_ticket_sync_once() -> None:
    global _DID_TICKET_SYNC

    if _DID_TICKET_SYNC:
        return

    _DID_TICKET_SYNC = True

    try:
        from .tickets_new.sync_service import sync_active_ticket_channels_for_guild
    except Exception as e:
        print("⚠️ Failed importing ticket sync helper:", repr(e))
        return

    guild = await _resolve_runtime_guild()
    if guild is None:
        print("⚠️ Skipping startup ticket sync: guild could not be resolved.")
        return

    try:
        print("🎫 Running startup ticket sync/backfill...")
        summary = await sync_active_ticket_channels_for_guild(
            guild,
            source="startup_ticket_sync",
            include_closed_visible_channels=True,
            dry_run=False,
        )
        print("✅ Startup ticket sync complete:", summary)
    except Exception as e:
        print("❌ Startup ticket sync failed:", repr(e))


async def _run_slash_maintenance_once() -> None:
    global _DID_SLASH_MAINTENANCE
    global _DID_GLOBAL_COMMAND_CLEANUP

    if _DID_SLASH_MAINTENANCE:
        return

    _DID_SLASH_MAINTENANCE = True

    try:
        try:
            print("🧩 local global commands:", [c.name for c in bot.tree.get_commands()])
        except Exception:
            pass

        if GUILD_ID:
            try:
                guild_id_int = int(str(GUILD_ID))
                guild_obj = discord.Object(id=guild_id_int)

                try:
                    bot.tree.copy_global_to(guild=guild_obj)
                    print("✅ Copied global commands to guild tree.")
                except Exception as e:
                    print("⚠️ copy_global_to failed:", repr(e))

                if _env_true("CLEAR_GLOBAL_COMMANDS_ON_BOOT", default=False) and not _DID_GLOBAL_COMMAND_CLEANUP:
                    _DID_GLOBAL_COMMAND_CLEANUP = True
                    try:
                        bot.tree.clear_commands(guild=None)
                        await bot.tree.sync()
                        print("🧹 Cleared old global Discord application commands.")
                    except Exception as e:
                        print("⚠️ Global command cleanup failed:", repr(e))

                synced_guild = await bot.tree.sync(guild=guild_obj)
                print(
                    f"✅ Guild slash sync complete: {len(synced_guild)} "
                    f"command(s) for guild {guild_id_int}."
                )

                try:
                    guild_commands = bot.tree.get_commands(guild=guild_obj)
                    print("🧩 local guild commands:", [c.name for c in guild_commands])
                except Exception:
                    pass

            except Exception as e:
                print("⚠️ Guild slash sync failed:", repr(e))
        else:
            try:
                synced_global = await bot.tree.sync()
                print(f"✅ Global slash sync complete: {len(synced_global)} command(s).")
            except Exception as e:
                print("⚠️ Global slash sync failed:", repr(e))

    except Exception as e:
        print("❌ Slash maintenance failed:", repr(e))


async def _start_legacy_actions_api_once() -> None:
    global _STARTED_LEGACY_ACTIONS_API

    if _STARTED_LEGACY_ACTIONS_API:
        return

    if not claim_startup_flag("legacy_actions_api"):
        _STARTED_LEGACY_ACTIONS_API = True
        print("ℹ️ Legacy Bot Actions API startup already claimed elsewhere; skipping here.")
        return

    _STARTED_LEGACY_ACTIONS_API = True

    if start_bot_actions_server is None:
        print("⚠️ Legacy Bot Actions API unavailable")
        return

    try:
        await start_bot_actions_server(bot)
        print("🌐 Legacy Bot Actions API started")
    except Exception as e:
        print("⚠️ Legacy API failed:", repr(e))


async def _start_new_api_once() -> None:
    global _STARTED_NEW_ACTIONS_API

    if _STARTED_NEW_ACTIONS_API:
        return

    if not claim_startup_flag("structured_bot_api"):
        _STARTED_NEW_ACTIONS_API = True
        print("ℹ️ New Bot API startup already claimed elsewhere; skipping here.")
        return

    _STARTED_NEW_ACTIONS_API = True

    try:
        await start_api(bot)
        print("🌐 New Bot API started")
    except Exception as e:
        print("❌ New API failed:", repr(e))


async def _start_workers_once() -> None:
    global _STARTED_WORKERS

    if _STARTED_WORKERS:
        return

    if not claim_startup_flag("background_workers"):
        _STARTED_WORKERS = True
        print("ℹ️ Background worker startup already claimed elsewhere; skipping here.")
        return

    _STARTED_WORKERS = True

    try:
        worker_task = start_worker()
        if worker_task is not None:
            print("🤖 Bot command worker startup requested")
        else:
            print("ℹ️ Bot command worker was not started")
    except Exception as e:
        print("⚠️ Worker start failed:", repr(e))

    try:
        metrics_task = start_metrics_worker()
        if metrics_task is not None:
            print("📡 Metrics sync worker startup requested")
        else:
            print("ℹ️ Metrics sync worker was not started")
    except Exception as e:
        print("⚠️ Metrics worker failed:", repr(e))

    try:
        automation_task = start_ticket_automation_worker()
        if automation_task is not None:
            print("🤖 Ticket automation worker startup requested")
        else:
            print("ℹ️ Ticket automation worker was not started")
    except Exception as e:
        print("⚠️ Ticket automation worker failed:", repr(e))


async def _startup_background_runner() -> None:
    try:
        await asyncio.sleep(5.0)

        try:
            await _maybe_run_departed_reconcile_once()
        except Exception as e:
            print("⚠️ Background departed reconcile failed:", repr(e))

        try:
            await asyncio.sleep(2.0)
        except Exception:
            pass

        try:
            await _maybe_run_ticket_sync_once()
        except Exception as e:
            print("⚠️ Background ticket sync failed:", repr(e))

    except asyncio.CancelledError:
        return
    except Exception as e:
        print("⚠️ Startup background runner failed:", repr(e))


def _ensure_startup_background_runner() -> None:
    global _STARTUP_BACKGROUND_TASK

    try:
        if _STARTUP_BACKGROUND_TASK and not _STARTUP_BACKGROUND_TASK.done():
            return
    except Exception:
        pass

    try:
        task = asyncio.create_task(_startup_background_runner(), name="startup_background_runner")
        _STARTUP_BACKGROUND_TASK = task
        _track_background_task(task, label="startup_background_runner")
        print("🧩 Startup background runner scheduled.")
    except Exception as e:
        print("⚠️ Failed to schedule startup background runner:", repr(e))


def _should_ignore_foreign_prefix_command(
    ctx: ext_commands.Context,
    error: BaseException,
) -> bool:
    try:
        if not isinstance(error, ext_commands.CommandNotFound):
            return False

        msg = getattr(ctx, "message", None)
        if msg is None:
            return False

        if getattr(msg.author, "bot", False):
            return True

        content = str(getattr(msg, "content", "") or "").strip()
        lowered = content.lower()

        invoked = str(getattr(ctx, "invoked_with", "") or "").strip().lower()
        prefix = str(getattr(ctx, "prefix", "") or "")

        if prefix == "!" and invoked in _IGNORED_FOREIGN_PREFIX_COMMANDS:
            return True

        for name in _IGNORED_FOREIGN_PREFIX_COMMANDS:
            trigger = f"!{name}"
            if lowered == trigger or lowered.startswith(trigger + " "):
                return True

        return False
    except Exception:
        return False


# Register ticket event listeners once at import/startup time.
_setup_ticket_events_once()


@bot.listen("on_ready")
async def on_ready() -> None:
    try:
        print(f"🤖 Bot ready: {bot.user}")

        await _run_slash_maintenance_once()
        await _maybe_resume_kick_timers_once()
        await _start_legacy_actions_api_once()
        await _start_new_api_once()
        await _start_workers_once()
        await _run_permission_self_check_once()

        # Heavy work belongs in the background so the gateway heartbeat stays healthy.
        _ensure_startup_background_runner()

    except Exception as e:
        print("❌ app.py on_ready listener failed:", repr(e))


@bot.event
async def on_command_error(ctx: ext_commands.Context, error: Exception) -> None:
    """
    Silence foreign prefix-command noise from other bots in the same server.

    Example:
      - game bot owns !ask
      - verify bot also sees !ask
      - verify bot should ignore it instead of spamming CommandNotFound
    """
    try:
        base_error = getattr(error, "original", error)

        if _should_ignore_foreign_prefix_command(ctx, base_error):
            return

        # Also suppress ugly stack spam for any other unknown prefix command.
        # This bot is primarily slash-command driven now.
        if isinstance(base_error, ext_commands.CommandNotFound):
            try:
                raw = str(getattr(getattr(ctx, "message", None), "content", "") or "").strip()
                if raw:
                    print(f"ℹ️ Ignored unknown prefix command: {raw}")
            except Exception:
                pass
            return

        # Respect command/cog-local handlers if they exist.
        try:
            if ctx.command and hasattr(ctx.command, "on_error"):
                return
        except Exception:
            pass

        try:
            cog = getattr(ctx.command, "cog", None) if getattr(ctx, "command", None) else None
            if cog is not None:
                cog_handler_name = f"{cog.__class__.__name__}_error"
                if hasattr(cog, cog_handler_name):
                    return
        except Exception:
            pass

        print("⚠️ Prefix command error:", repr(base_error))
        try:
            traceback.print_exception(type(base_error), base_error, base_error.__traceback__)
        except Exception:
            pass

    except Exception as e:
        print("⚠️ on_command_error handler failed:", repr(e))


def run() -> None:
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing.")
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    run()
