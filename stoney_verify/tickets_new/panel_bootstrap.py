from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import discord

from ..globals import now_utc
from ..guild_config import (
    clear_guild_config_cache,
    save_runtime_discovered_config,
)
from .panel_repository import (
    DEFAULT_PANEL_RULES,
    ensure_ticket_panel_exists,
    get_ticket_panel_preset,
    get_ticket_panel_rules,
    list_ticket_panels,
    upsert_ticket_panel_preset,
    upsert_ticket_panel_rules,
)


# ============================================================
# tickets_new/panel_bootstrap.py
# ------------------------------------------------------------
# Safe bootstrap/self-heal worker for the DB-backed panel system.
#
# Goals:
# - keep public server setup from stalling when config is missing
# - never require per-server .env IDs
# - keep .env as fallback only through guild_config.py
# - do not create Discord channels/roles without explicit owner action
# - seed safe DB defaults only
# - isolate guild failures so one bad server cannot block others
# - keep work bounded so command storms do not starve the bot
#
# Legal/privacy posture:
# - does not collect extra data
# - does not auto-enable invasive moderation actions
# - does not send hidden messages
# - default panel copy includes a ticket logging/transcript notice
# ============================================================


PANEL_BOOTSTRAP_INTERVAL_SECONDS = 30 * 60
PANEL_BOOTSTRAP_GUILD_CONCURRENCY = 4
PANEL_BOOTSTRAP_STARTUP_JITTER_SECONDS = 8

DEFAULT_SUPPORT_PANEL_KEY = "support"
DEFAULT_SUPPORT_PRESET_KEY = "default-support"

DEFAULT_SUPPORT_PROMPT_TITLE = "Need help?"
DEFAULT_SUPPORT_PROMPT_DESCRIPTION = (
    "Open a ticket and staff will help you as soon as possible.\n\n"
    "By opening a ticket, you understand server staff may review ticket messages, "
    "staff notes, actions, and transcripts for support/moderation purposes."
)

_PANEL_BOOTSTRAP_TASK: Optional[asyncio.Task] = None
_PANEL_BOOTSTRAP_STOP_EVENT: Optional[asyncio.Event] = None
_PANEL_BOOTSTRAP_LOCK = asyncio.Lock()
_PANEL_BOOTSTRAP_LAST_RUN: Dict[str, str] = {}
_PANEL_BOOTSTRAP_LAST_ERROR: Dict[str, str] = {}


# ============================================================
# Small helpers
# ============================================================

def _debug(message: str) -> None:
    try:
        print(f"🧩 panel_bootstrap {message}")
    except Exception:
        pass


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        raw = str(value or "").strip().lower()
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
        return default
    except Exception:
        return default


def _now() -> datetime:
    try:
        return now_utc()
    except Exception:
        return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _guild_key(guild: discord.Guild | int | str) -> str:
    try:
        if isinstance(guild, discord.Guild):
            return str(int(guild.id))
        return str(int(str(guild).strip()))
    except Exception:
        return _safe_str(guild, "unknown")


def _default_rules() -> Dict[str, Any]:
    rules = dict(DEFAULT_PANEL_RULES)

    # Public-safe defaults:
    # - no auto-close until server owner explicitly configures it
    # - reminders are allowed, but non-destructive
    # - unknown members can open tickets so new servers do not break
    # - ghost tickets are disabled by default
    rules.update(
        {
            "cooldown_seconds": 0,
            "max_tickets_per_window": 0,
            "window_minutes": 0,
            "auto_close_enabled": False,
            "auto_close_minutes": 1440,
            "inactivity_reminders_enabled": True,
            "inactivity_reminder_minutes": 240,
            "staff_alert_channel_id": None,
            "allow_unverified": True,
            "allow_verified": True,
            "allow_resident": True,
            "allow_staff": True,
            "allow_unknown_members": True,
            "ghost_allowed": False,
            "transcript_mode": "on_close",
            "close_confirmation_required": True,
            "per_owner_open_limit": 1,
        }
    )

    return rules


def _rules_look_unpersisted(rules: Dict[str, Any]) -> bool:
    """
    panel_repository returns normalized default rules even when no DB row exists.
    A real DB row usually has id/raw/created_at.
    """
    try:
        if not isinstance(rules, dict):
            return True

        if rules.get("id"):
            return False

        raw = rules.get("raw")
        if isinstance(raw, dict) and raw:
            return False

        if rules.get("created_at") or rules.get("updated_at"):
            return False

        return True
    except Exception:
        return True


def _panel_rows_look_empty(rows: Any) -> bool:
    return not isinstance(rows, list) or len(rows) <= 0


# ============================================================
# Default preset / panel seeds
# ============================================================

async def ensure_default_support_preset(guild: discord.Guild) -> Optional[Dict[str, Any]]:
    try:
        existing = await get_ticket_panel_preset(guild.id, DEFAULT_SUPPORT_PRESET_KEY)
        if existing:
            return existing

        payload = {
            "guild_id": str(int(guild.id)),
            "preset_key": DEFAULT_SUPPORT_PRESET_KEY,
            "preset_name": "Default Support",
            "panel_style": "buttons",
            "default_prompt_title": DEFAULT_SUPPORT_PROMPT_TITLE,
            "default_prompt_description": DEFAULT_SUPPORT_PROMPT_DESCRIPTION,
            "default_embed_title": DEFAULT_SUPPORT_PROMPT_TITLE,
            "default_embed_description": DEFAULT_SUPPORT_PROMPT_DESCRIPTION,
            "default_button_label": "Open Ticket",
            "default_menu_placeholder": "Choose a ticket type",
            "default_rules_json": _default_rules(),
        }

        return await upsert_ticket_panel_preset(payload)
    except Exception as e:
        _debug(f"default preset failed guild={guild.id}: {repr(e)}")
        return None


async def ensure_default_support_panel(guild: discord.Guild) -> Optional[Dict[str, Any]]:
    try:
        panels = await list_ticket_panels(guild.id)

        # If the server already has any panels, do not force-create another.
        # This worker is a safety net, not an opinionated takeover.
        if not _panel_rows_look_empty(panels):
            return None

        await ensure_default_support_preset(guild)

        panel = await ensure_ticket_panel_exists(
            guild_id=guild.id,
            panel_key=DEFAULT_SUPPORT_PANEL_KEY,
            panel_name="Support",
            panel_style="buttons",
            prompt_title=DEFAULT_SUPPORT_PROMPT_TITLE,
            prompt_description=DEFAULT_SUPPORT_PROMPT_DESCRIPTION,
            button_label="Open Ticket",
            menu_placeholder="Choose a ticket type",
            preset_key=DEFAULT_SUPPORT_PRESET_KEY,
            sort_order=1,
        )

        rules = await get_ticket_panel_rules(guild.id, DEFAULT_SUPPORT_PANEL_KEY)
        if _rules_look_unpersisted(rules):
            payload = {
                "guild_id": str(int(guild.id)),
                "panel_key": DEFAULT_SUPPORT_PANEL_KEY,
                **_default_rules(),
            }
            await upsert_ticket_panel_rules(payload)

        return panel
    except Exception as e:
        _debug(f"default support panel failed guild={guild.id}: {repr(e)}")
        return None


async def ensure_default_panel_rules_for_existing_panels(guild: discord.Guild) -> int:
    repaired = 0

    try:
        panels = await list_ticket_panels(guild.id)
    except Exception as e:
        _debug(f"panel list failed guild={guild.id}: {repr(e)}")
        return 0

    for panel in panels or []:
        try:
            panel_key = _safe_str(panel.get("panel_key"))
            if not panel_key:
                continue

            rules = await get_ticket_panel_rules(guild.id, panel_key)
            if not _rules_look_unpersisted(rules):
                continue

            payload = {
                "guild_id": str(int(guild.id)),
                "panel_key": panel_key,
                **_default_rules(),
            }
            await upsert_ticket_panel_rules(payload)
            repaired += 1
        except Exception as e:
            _debug(
                f"panel rules repair failed guild={guild.id} "
                f"panel={_safe_str(panel.get('panel_key'))} error={repr(e)}"
            )

    return repaired


# ============================================================
# Guild bootstrap
# ============================================================

async def bootstrap_panel_system_for_guild(
    guild: discord.Guild,
    *,
    save_discovery: bool = True,
    seed_default_panel: bool = True,
) -> Dict[str, Any]:
    """
    Runs safe DB bootstrap for one guild.

    This does not:
    - create Discord channels
    - create roles
    - post panels
    - modify permissions
    - force enable destructive automation
    """
    gid = _guild_key(guild)
    started = _now_iso()

    result: Dict[str, Any] = {
        "guild_id": gid,
        "guild_name": _safe_str(getattr(guild, "name", "")),
        "started_at": started,
        "ok": True,
        "saved_discovery": False,
        "default_preset_ready": False,
        "default_panel_created": False,
        "rules_repaired": 0,
        "error": "",
    }

    try:
        clear_guild_config_cache(guild.id)

        if save_discovery:
            try:
                await save_runtime_discovered_config(guild)
                result["saved_discovery"] = True
            except Exception as e:
                result["saved_discovery"] = False
                _debug(f"runtime discovery save skipped guild={guild.id}: {repr(e)}")

        preset = await ensure_default_support_preset(guild)
        result["default_preset_ready"] = bool(preset)

        if seed_default_panel:
            created = await ensure_default_support_panel(guild)
            result["default_panel_created"] = bool(created)

        result["rules_repaired"] = await ensure_default_panel_rules_for_existing_panels(guild)

        result["finished_at"] = _now_iso()
        _PANEL_BOOTSTRAP_LAST_RUN[gid] = result["finished_at"]

        return result
    except Exception as e:
        result["ok"] = False
        result["error"] = repr(e)
        result["finished_at"] = _now_iso()
        _PANEL_BOOTSTRAP_LAST_ERROR[gid] = repr(e)
        _debug(f"guild bootstrap failed guild={gid}: {repr(e)}")
        return result


async def bootstrap_panel_system_for_bot(
    bot: discord.Client,
    *,
    save_discovery: bool = True,
    seed_default_panel: bool = True,
    concurrency: int = PANEL_BOOTSTRAP_GUILD_CONCURRENCY,
) -> Dict[str, Any]:
    guilds = list(getattr(bot, "guilds", []) or [])
    sem = asyncio.Semaphore(max(1, int(concurrency or 1)))

    results: List[Dict[str, Any]] = []

    async def _run_one(guild: discord.Guild) -> None:
        async with sem:
            try:
                result = await bootstrap_panel_system_for_guild(
                    guild,
                    save_discovery=save_discovery,
                    seed_default_panel=seed_default_panel,
                )
                results.append(result)
            except Exception as e:
                gid = _guild_key(guild)
                results.append(
                    {
                        "guild_id": gid,
                        "guild_name": _safe_str(getattr(guild, "name", "")),
                        "ok": False,
                        "error": repr(e),
                    }
                )

    await asyncio.gather(*[_run_one(guild) for guild in guilds], return_exceptions=True)

    ok_count = sum(1 for row in results if _safe_bool(row.get("ok"), False))
    failed_count = len(results) - ok_count
    rules_repaired = sum(_safe_int(row.get("rules_repaired"), 0) for row in results)
    default_created = sum(1 for row in results if _safe_bool(row.get("default_panel_created"), False))

    summary = {
        "ok": failed_count == 0,
        "guilds_seen": len(guilds),
        "guilds_ok": ok_count,
        "guilds_failed": failed_count,
        "rules_repaired": rules_repaired,
        "default_panels_created": default_created,
        "ran_at": _now_iso(),
        "results": results,
    }

    _debug(
        "bot bootstrap complete "
        f"guilds={len(guilds)} ok={ok_count} failed={failed_count} "
        f"rules_repaired={rules_repaired} default_created={default_created}"
    )

    return summary


# ============================================================
# Worker lifecycle
# ============================================================

async def _wait_until_ready(bot: discord.Client, timeout_seconds: int = 90) -> None:
    try:
        wait_until_ready = getattr(bot, "wait_until_ready", None)
        if callable(wait_until_ready):
            await asyncio.wait_for(wait_until_ready(), timeout=max(1, timeout_seconds))
    except asyncio.TimeoutError:
        _debug("wait_until_ready timed out; continuing bootstrap cautiously")
    except Exception as e:
        _debug(f"wait_until_ready skipped: {repr(e)}")


async def _panel_bootstrap_loop(
    bot: discord.Client,
    *,
    interval_seconds: int = PANEL_BOOTSTRAP_INTERVAL_SECONDS,
    save_discovery: bool = True,
    seed_default_panel: bool = True,
    once: bool = False,
) -> None:
    global _PANEL_BOOTSTRAP_STOP_EVENT

    if _PANEL_BOOTSTRAP_STOP_EVENT is None:
        _PANEL_BOOTSTRAP_STOP_EVENT = asyncio.Event()

    await _wait_until_ready(bot)

    try:
        jitter = random.uniform(0.5, float(max(1, PANEL_BOOTSTRAP_STARTUP_JITTER_SECONDS)))
        await asyncio.sleep(jitter)
    except Exception:
        pass

    while True:
        try:
            async with _PANEL_BOOTSTRAP_LOCK:
                await bootstrap_panel_system_for_bot(
                    bot,
                    save_discovery=save_discovery,
                    seed_default_panel=seed_default_panel,
                    concurrency=PANEL_BOOTSTRAP_GUILD_CONCURRENCY,
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _debug(f"bootstrap loop iteration failed: {repr(e)}")

        if once:
            return

        try:
            assert _PANEL_BOOTSTRAP_STOP_EVENT is not None
            await asyncio.wait_for(
                _PANEL_BOOTSTRAP_STOP_EVENT.wait(),
                timeout=max(60, int(interval_seconds)),
            )
            return
        except asyncio.TimeoutError:
            continue


def start_panel_bootstrap_worker(
    bot: discord.Client,
    *,
    interval_seconds: int = PANEL_BOOTSTRAP_INTERVAL_SECONDS,
    save_discovery: bool = True,
    seed_default_panel: bool = True,
) -> Optional[asyncio.Task]:
    """
    Start the recurring bootstrap worker.

    Safe to call more than once.
    """
    global _PANEL_BOOTSTRAP_TASK
    global _PANEL_BOOTSTRAP_STOP_EVENT

    try:
        if _PANEL_BOOTSTRAP_TASK and not _PANEL_BOOTSTRAP_TASK.done():
            _debug("worker already running; skipping duplicate start")
            return _PANEL_BOOTSTRAP_TASK

        _PANEL_BOOTSTRAP_STOP_EVENT = asyncio.Event()

        loop = asyncio.get_running_loop()
        _PANEL_BOOTSTRAP_TASK = loop.create_task(
            _panel_bootstrap_loop(
                bot,
                interval_seconds=interval_seconds,
                save_discovery=save_discovery,
                seed_default_panel=seed_default_panel,
                once=False,
            )
        )

        _debug("worker started")
        return _PANEL_BOOTSTRAP_TASK
    except Exception as e:
        _debug(f"worker start failed: {repr(e)}")
        return None


def start_panel_bootstrap_once(
    bot: discord.Client,
    *,
    save_discovery: bool = True,
    seed_default_panel: bool = True,
) -> Optional[asyncio.Task]:
    """
    Schedule one bootstrap pass without starting the recurring worker.
    """
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(
            _panel_bootstrap_loop(
                bot,
                interval_seconds=PANEL_BOOTSTRAP_INTERVAL_SECONDS,
                save_discovery=save_discovery,
                seed_default_panel=seed_default_panel,
                once=True,
            )
        )
        _debug("one-shot bootstrap scheduled")
        return task
    except Exception as e:
        _debug(f"one-shot bootstrap failed: {repr(e)}")
        return None


async def stop_panel_bootstrap_worker() -> None:
    global _PANEL_BOOTSTRAP_TASK
    global _PANEL_BOOTSTRAP_STOP_EVENT

    try:
        if _PANEL_BOOTSTRAP_STOP_EVENT is not None:
            _PANEL_BOOTSTRAP_STOP_EVENT.set()

        task = _PANEL_BOOTSTRAP_TASK
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        _PANEL_BOOTSTRAP_TASK = None
        _PANEL_BOOTSTRAP_STOP_EVENT = None
        _debug("worker stopped")
    except Exception as e:
        _debug(f"worker stop failed: {repr(e)}")


def panel_bootstrap_status() -> Dict[str, Any]:
    task_state = "not_started"

    try:
        if _PANEL_BOOTSTRAP_TASK is not None:
            if _PANEL_BOOTSTRAP_TASK.cancelled():
                task_state = "cancelled"
            elif _PANEL_BOOTSTRAP_TASK.done():
                task_state = "stopped"
            else:
                task_state = "running"
    except Exception:
        task_state = "unknown"

    return {
        "task_state": task_state,
        "last_run": dict(_PANEL_BOOTSTRAP_LAST_RUN),
        "last_error": dict(_PANEL_BOOTSTRAP_LAST_ERROR),
        "interval_seconds": PANEL_BOOTSTRAP_INTERVAL_SECONDS,
        "guild_concurrency": PANEL_BOOTSTRAP_GUILD_CONCURRENCY,
    }
