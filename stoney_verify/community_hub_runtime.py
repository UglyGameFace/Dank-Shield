from __future__ import annotations

"""Single-owner runtime for Dank Shield Community Hub.

This runtime owns Discord resources, startup reconciliation, lifecycle cleanup,
voice/thread activity observation, optional aggregate Presence analytics, and
persistent public session controls. Durable session truth remains in
community_hub_service; discord.py remains the route-aware HTTP rate-limit owner;
the existing AntiNuke HTTP wrapper remains the self-action authorization owner.
"""

import asyncio
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import discord

from . import community_hub_service as hub
from .operation_queue import operation_queue_health_summary, run_exclusive, with_retry
from .startup_guards.discord_api_safety import reserve_recovery_discord_rest_requests


_RUNTIME_ATTR = "_dank_community_hub_runtime"
_AUDIT_REASON_PREFIX = "Dank Community Hub"
_MAINTENANCE_SECONDS = 300
_ACTIVITY_TOUCH_SECONDS = 60
_PRESENCE_AGGREGATE_SECONDS = 60
_STARTUP_BATCH_DELAY_SECONDS = 0.15
_MAX_RECONCILE_PER_PASS = 1000
_CHANNEL_NAME_CLEAN = re.compile(r"[\x00-\x1f\x7f]")


def _log(message: str) -> None:
    try:
        print(f"community_hub_runtime: {message}", flush=True)
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text or default
    except Exception:
        return default


def _parse_dt(value: Any) -> Optional[datetime]:
    text = _safe_str(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _channel_name(value: Any, *, fallback: str = "Gaming Session") -> str:
    text = _CHANNEL_NAME_CLEAN.sub("", _safe_str(value, fallback))
    text = " ".join(text.split())
    return (text or fallback)[:100]


def _audit_reason(token: str, action: str) -> str:
    return f"{_AUDIT_REASON_PREFIX} {action} [{_safe_str(token)[:64]}]"[:512]


def _is_human(member: Any) -> bool:
    return bool(member is not None and not bool(getattr(member, "bot", False)))


def _can_manage_channels(guild: discord.Guild, category: Any = None) -> bool:
    me = getattr(guild, "me", None)
    if me is None:
        return False
    try:
        if bool(getattr(me.guild_permissions, "administrator", False)):
            return True
    except Exception:
        pass
    if category is not None:
        try:
            return bool(getattr(category.permissions_for(me), "manage_channels", False))
        except Exception:
            pass
    try:
        return bool(getattr(me.guild_permissions, "manage_channels", False))
    except Exception:
        return False


def _can_create_public_thread(channel: discord.TextChannel) -> bool:
    me = getattr(channel.guild, "me", None)
    if me is None:
        return False
    try:
        perms = channel.permissions_for(me)
        # Discord's Start Thread from Message route is governed by the ability
        # to send messages in the parent channel. CREATE_PUBLIC_THREADS is not
        # the permission gate for this route.
        return bool(
            getattr(perms, "view_channel", False)
            and getattr(perms, "send_messages", False)
        )
    except Exception:
        return False


class CommunityHubRuntime:
    def __init__(self, bot: Any) -> None:
        self.bot = bot
        self._ready_lock = asyncio.Lock()
        self._registered_public_view = False
        self._maintenance_task: Optional[asyncio.Task[Any]] = None
        self._cleanup_tasks: dict[str, asyncio.Task[Any]] = {}
        self._notification_tasks: set[asyncio.Task[Any]] = set()
        self._notification_semaphore = asyncio.Semaphore(2)
        self._touch_last: dict[str, float] = {}
        self._voice_sessions: dict[int, str] = {}
        self._thread_sessions: dict[int, str] = {}
        self._presence_dirty: set[int] = set()
        self._presence_last_aggregate: dict[int, float] = {}
        self._presence_enabled_guilds: set[int] = set()
        self._settings_cache: dict[int, tuple[float, dict[str, Any]]] = {}
        self._runtime_started_at = datetime.now(timezone.utc)
        self._startup_reconciled = False

    async def settings_for(self, guild_id: int, *, refresh: bool = False) -> dict[str, Any]:
        gid = int(guild_id)
        now = time.monotonic()
        cached = self._settings_cache.get(gid)
        if not refresh and cached is not None and now - cached[0] < 60.0:
            return dict(cached[1])
        settings = await hub.get_settings(gid)
        self._settings_cache[gid] = (now, dict(settings))
        if bool(settings.get("presence_analytics_enabled")):
            self._presence_enabled_guilds.add(gid)
        else:
            self._presence_enabled_guilds.discard(gid)
        return settings

    def invalidate_settings(self, guild_id: int) -> None:
        self._settings_cache.pop(int(guild_id), None)

    async def register_public_view(self) -> None:
        if self._registered_public_view:
            return
        try:
            from .commands_ext.public_community_hub import CommunitySessionPublicView
            self.bot.add_view(CommunitySessionPublicView())
            self._registered_public_view = True
        except Exception as exc:
            _log(f"persistent public view registration failed: {type(exc).__name__}: {exc}")

    async def on_ready(self) -> None:
        async with self._ready_lock:
            await self.register_public_view()
            if not self._startup_reconciled:
                self._startup_reconciled = True
                asyncio.create_task(
                    self._reconcile_startup(),
                    name="dank-community-hub-reconcile",
                )
            if self._maintenance_task is None or self._maintenance_task.done():
                self._maintenance_task = asyncio.create_task(
                    self._maintenance_loop(),
                    name="dank-community-hub-maintenance",
                )

    async def _reconcile_startup(self) -> None:
        try:
            resources = await hub.list_recovery_resources(limit=_MAX_RECONCILE_PER_PASS)
        except hub.CommunityHubError as exc:
            _log(f"startup reconcile skipped: {exc}")
            return

        recovered = missing = unresolved = 0
        for index, resource in enumerate(resources):
            try:
                outcome = await self._reconcile_resource(resource)
                if outcome == "recovered":
                    recovered += 1
                elif outcome == "missing":
                    missing += 1
                elif outcome == "unresolved":
                    unresolved += 1
            except Exception as exc:
                unresolved += 1
                try:
                    await hub.mark_resource_state(
                        _safe_str(resource.get("id")),
                        "unresolved",
                        error=f"{type(exc).__name__}: {str(exc)[:300]}",
                    )
                except Exception:
                    pass
            if index and index % 25 == 0:
                await asyncio.sleep(_STARTUP_BATCH_DELAY_SECONDS)

        if resources:
            _log(
                "startup reconcile "
                f"resources={len(resources)} recovered={recovered} missing={missing} unresolved={unresolved}"
            )

        # Sessions owned by the previous process must not remain zombies. An
        # ENDING session resumes cleanup. A CREATING session whose timestamp
        # predates this runtime is provably orphaned because the process that
        # owned its Discord publication step is gone.
        try:
            orphaned_creating = 0
            for guild in list(getattr(self.bot, "guilds", []) or []):
                guild_id = int(guild.id)
                bot_actor_id = int(getattr(getattr(guild, "me", None), "id", 0) or 0)
                sessions = await hub.list_active_sessions(guild_id, limit=100)
                for session in sessions:
                    sid = _safe_str(session.get("id"))
                    state = _safe_str(session.get("state"))
                    if not sid:
                        continue
                    if state == "ending":
                        self.schedule_cleanup(
                            sid,
                            guild_id,
                            actor_id=bot_actor_id,
                            delay_seconds=5,
                        )
                        continue
                    if state != "creating":
                        continue

                    created_at = _parse_dt(session.get("created_at"))
                    if created_at is None or created_at >= self._runtime_started_at:
                        continue

                    if _safe_str(session.get("idempotency_key")).startswith("quick:"):
                        try:
                            members = await hub.list_session_members(sid)
                            candidate = next(
                                (
                                    row
                                    for row in members
                                    if _safe_str(row.get("role")) != "host"
                                    and _safe_int(row.get("user_id"), 0) > 0
                                ),
                                None,
                            )
                            candidate_id = _safe_int((candidate or {}).get("user_id"), 0)
                            if candidate_id > 0:
                                await hub.set_availability_auto_match(
                                    guild_id,
                                    candidate_id,
                                    True,
                                    game_name=_safe_str(session.get("game_name")),
                                )
                        except hub.CommunityHubError:
                            pass

                    try:
                        ended = await hub.transition_session(
                            sid,
                            guild_id,
                            bot_actor_id,
                            "begin_end",
                            staff_override=True,
                            reason="Interrupted before Community Hub publication during a previous bot process",
                        )
                        await self.refresh_session_card(ended)
                        self.schedule_cleanup(
                            sid,
                            guild_id,
                            actor_id=bot_actor_id,
                            delay_seconds=5,
                        )
                        orphaned_creating += 1
                    except hub.CommunityHubError as exc:
                        _log(
                            "creating-session reconcile failed "
                            f"session={sid} error={type(exc).__name__}: {exc}"
                        )
            if orphaned_creating:
                _log(f"startup reconcile orphaned_creating={orphaned_creating}")
        except Exception as exc:
            _log(f"session-state reconcile degraded: {type(exc).__name__}: {exc}")

    async def _reconcile_resource(self, resource: dict[str, Any]) -> str:
        guild_id = _safe_int(resource.get("guild_id"), 0)
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return "unresolved"

        resource_id = _safe_str(resource.get("id"))
        resource_type = _safe_str(resource.get("resource_type"))
        discord_id = _safe_int(resource.get("discord_id"), 0)
        session_id = _safe_str(resource.get("session_id"))

        if discord_id > 0:
            if resource_type == "voice_channel":
                self._voice_sessions[discord_id] = session_id
            elif resource_type == "thread":
                self._thread_sessions[discord_id] = session_id

            # Cached existence is enough on startup. Do not spend REST on every
            # proven active resource just to admire it.
            if resource_type in {"voice_channel", "thread", "category"}:
                cached = (
                    guild.get_thread(discord_id)
                    if resource_type == "thread"
                    else guild.get_channel(discord_id)
                )
                if cached is not None:
                    if resource.get("state") != "active":
                        await hub.mark_resource_state(resource_id, "active")
                    return "recovered"

                if resource.get("state") == "active":
                    return "recovered"

                await reserve_recovery_discord_rest_requests(
                    1,
                    label=f"community-hub fetch {resource_type}",
                )
                try:
                    fetched = await guild.fetch_channel(discord_id)
                except discord.NotFound:
                    await hub.mark_resource_state(resource_id, "deleted")
                    self._voice_sessions.pop(discord_id, None)
                    self._thread_sessions.pop(discord_id, None)
                    return "missing"
                except discord.Forbidden as exc:
                    await hub.mark_resource_state(
                        resource_id,
                        "unresolved",
                        error=f"Forbidden: {str(exc)[:240]}",
                    )
                    return "unresolved"
                except discord.HTTPException as exc:
                    await hub.mark_resource_state(
                        resource_id,
                        "unresolved",
                        error=f"HTTPException: {str(exc)[:240]}",
                    )
                    return "unresolved"

                if resource_type == "voice_channel":
                    self._voice_sessions[int(fetched.id)] = session_id
                elif resource_type == "thread":
                    self._thread_sessions[int(fetched.id)] = session_id
                await hub.mark_resource_state(resource_id, "active")
                return "recovered"

            # Panel messages are intentionally not fetched during startup. The
            # durable message ID is enough for lazy refresh and no destructive
            # cleanup ever relies on message existence.
            return "recovered"

        if resource.get("state") not in {"planned", "unresolved", "failed"}:
            return "unresolved"
        if resource_type not in {"voice_channel", "thread", "category"}:
            await hub.mark_resource_state(
                resource_id,
                "unresolved",
                error="No Discord ID and this resource type has no administrative audit-log recovery proof.",
            )
            return "unresolved"

        target_id = await self._recover_planned_resource_from_audit(guild, resource)
        if target_id <= 0:
            await hub.mark_resource_state(
                resource_id,
                "unresolved",
                error="No matching bot-authored Discord audit-log entry was found; resource preserved.",
            )
            return "unresolved"

        await hub.finalize_resource(resource_id, discord_id=target_id)
        if resource_type == "voice_channel":
            self._voice_sessions[target_id] = session_id
        elif resource_type == "thread":
            self._thread_sessions[target_id] = session_id
        return "recovered"

    async def _recover_planned_resource_from_audit(
        self,
        guild: discord.Guild,
        resource: dict[str, Any],
    ) -> int:
        me = getattr(guild, "me", None)
        if me is None:
            return 0
        try:
            if not bool(getattr(me.guild_permissions, "view_audit_log", False)):
                return 0
        except Exception:
            return 0

        resource_type = _safe_str(resource.get("resource_type"))
        token = _safe_str(resource.get("ownership_token"))
        if not token:
            return 0
        if resource_type in {"voice_channel", "category"}:
            action = discord.AuditLogAction.channel_create
        else:
            action = getattr(discord.AuditLogAction, "thread_create", None)
            if action is None:
                return 0

        created = _parse_dt(resource.get("created_at"))
        kwargs: dict[str, Any] = {"limit": 25, "action": action}
        if created is not None:
            kwargs["after"] = created - timedelta(minutes=10)
            kwargs["before"] = created + timedelta(minutes=10)

        await reserve_recovery_discord_rest_requests(
            1,
            label=f"community-hub audit recover {resource_type}",
        )
        try:
            async for entry in guild.audit_logs(**kwargs):
                if _safe_int(getattr(getattr(entry, "user", None), "id", 0), 0) != int(me.id):
                    continue
                if token not in _safe_str(getattr(entry, "reason", "")):
                    continue
                target_id = _safe_int(getattr(getattr(entry, "target", None), "id", 0), 0)
                if target_id > 0:
                    return target_id
        except (discord.Forbidden, discord.HTTPException):
            return 0
        return 0

    async def provision_session(
        self,
        interaction: discord.Interaction,
        session: dict[str, Any],
    ) -> dict[str, Any]:
        guild = interaction.guild
        if guild is None:
            raise hub.CommunityHubError("Community Hub sessions can only be created inside a server.")
        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise hub.CommunityHubError("Open Community Hub in a text channel so I can publish the group.")

        guild_id = int(guild.id)
        session_id = _safe_str(session.get("id"))
        settings = await self.settings_for(guild_id, refresh=True)
        mode = _safe_str(settings.get("mode"), "minimal")
        result: dict[str, Any] = {"session": session, "warnings": []}

        from .commands_ext.public_community_hub import CommunitySessionPublicView, build_session_embed

        members = await hub.list_session_members(session_id)
        panel_plan = await hub.plan_resource(
            guild_id=guild_id,
            session_id=session_id,
            resource_type="panel_message",
            parent_discord_id=int(channel.id),
        )
        try:
            panel_message = await channel.send(
                embed=build_session_embed(session, members),
                view=CommunitySessionPublicView(),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception as exc:
            await hub.mark_resource_state(
                _safe_str(panel_plan.get("id")),
                "failed",
                error=f"{type(exc).__name__}: {str(exc)[:300]}",
            )
            result["warnings"].append("I could not publish the public session card.")
            panel_message = None

        if panel_message is None:
            try:
                await hub.transition_session(
                    session_id,
                    guild_id,
                    int(interaction.user.id),
                    "begin_end",
                    reason="Public session card could not be published",
                )
                await hub.finalize_end_session(
                    session_id,
                    guild_id,
                    int(interaction.user.id),
                    cleanup_state="failed",
                )
            except hub.CommunityHubError:
                pass
            raise hub.CommunityHubError(
                "I could not publish the Community Hub session card. Check View Channel, Send Messages, and Embed Links in this channel."
            )

        if panel_message is not None:
            try:
                await hub.finalize_resource(
                    _safe_str(panel_plan.get("id")),
                    discord_id=int(panel_message.id),
                    parent_discord_id=int(channel.id),
                )
                session = await hub.update_session_refs(
                    session_id,
                    guild_id,
                    panel_channel_id=int(channel.id),
                    panel_message_id=int(panel_message.id),
                )
                result["session"] = session
            except hub.CommunityHubError:
                # The Discord message exists, but ownership could not be durably
                # finalized. Never delete it automatically from an uncertain state.
                result["warnings"].append(
                    "The session card was posted, but durable ownership recording failed; it will be preserved for staff review."
                )

        if mode in {"smart", "managed"} and panel_message is not None:
            if bool(settings.get("auto_create_thread", True)) and session.get("privacy") == "public":
                try:
                    thread = await self._create_session_thread(guild, panel_message, session)
                    if thread is not None:
                        result["thread"] = thread
                        session = await hub.update_session_refs(
                            session_id,
                            guild_id,
                            thread_id=int(thread.id),
                        )
                        result["session"] = session
                except Exception as exc:
                    result["warnings"].append(f"Discussion thread unavailable: {type(exc).__name__}.")

            if bool(settings.get("auto_create_voice", True)):
                try:
                    voice = await self._create_session_voice(guild, channel, session, settings)
                    if voice is not None:
                        result["voice"] = voice
                        session = await hub.update_session_refs(
                            session_id,
                            guild_id,
                            voice_channel_id=int(voice.id),
                        )
                        result["session"] = session
                except Exception as exc:
                    result["warnings"].append(f"Temporary voice room unavailable: {type(exc).__name__}.")

        session = await hub.transition_session(
            session_id,
            guild_id,
            int(interaction.user.id),
            "publish",
        )
        # Only Quick Match can pre-form a session before the Discord card is
        # published. Ordinary/manual session creation must not depend on the
        # follow-up matchmaking RPC during a rolling schema deployment.
        if _safe_str(session.get("idempotency_key")).startswith("quick:"):
            session = await hub.normalize_session_formation(session_id, guild_id)
        result["session"] = session
        await hub.bump_hourly_metric(guild_id, "sessions_created", 1)
        await hub.bump_game_metric(guild_id, _safe_str(session.get("game_name"), "Game"), "sessions_created", 1)
        await self.refresh_session_card(session)
        self._schedule_open_session_notifications(session)
        return result

    def _track_notification_task(self, task: asyncio.Task[Any]) -> None:
        self._notification_tasks.add(task)
        task.add_done_callback(self._notification_tasks.discard)

    def _schedule_open_session_notifications(self, session: dict[str, Any]) -> None:
        task = asyncio.create_task(
            self.dispatch_open_session_notifications(session),
            name=f"dank-community-notify-session:{_safe_str(session.get('id'))}",
        )
        self._track_notification_task(task)

    async def dispatch_open_session_notifications(self, session: dict[str, Any]) -> None:
        guild_id = _safe_int(session.get("guild_id"), 0)
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        settings = await self.settings_for(guild_id)
        if not bool(settings.get("notifications_enabled", True)):
            return
        limit = max(
            0,
            min(250, _safe_int(settings.get("max_notification_targets_per_dispatch"), 50)),
        )
        if limit <= 0:
            return
        game = _safe_str(session.get("game_name"))
        if not game:
            return
        try:
            notification_targets = await hub.list_notification_targets(
                guild_id,
                game,
                for_event=False,
                limit=limit,
            )
            available = await hub.list_available_users(
                guild_id,
                game,
                limit=limit,
            )
            targets = list(
                dict.fromkeys(
                    [
                        *notification_targets,
                        *[
                            _safe_str(row.get("user_id"))
                            for row in available
                            if row.get("user_id")
                        ],
                    ]
                )
            )
            members = await hub.list_session_members(_safe_str(session.get("id")))
        except hub.CommunityHubError:
            return
        participating = {
            _safe_int(row.get("user_id"), 0)
            for row in members
            if _safe_int(row.get("user_id"), 0) > 0
        }
        panel_channel_id = _safe_int(session.get("panel_channel_id"), 0)
        panel_message_id = _safe_int(session.get("panel_message_id"), 0)
        jump_url = (
            f"https://discord.com/channels/{guild_id}/{panel_channel_id}/{panel_message_id}"
            if panel_channel_id > 0 and panel_message_id > 0
            else ""
        )
        content = (
            f"🎮 **{game}** has a new Community Hub group in **{guild.name}**.\n"
            f"{jump_url}\n"
            "You received this because you opted into this game's notifications or marked yourself Open to Play."
        ).strip()
        async with self._notification_semaphore:
            for raw_user_id in targets[:limit]:
                user_id = _safe_int(raw_user_id, 0)
                if user_id <= 0 or user_id in participating:
                    continue
                user = guild.get_member(user_id) or self.bot.get_user(user_id)
                if user is None:
                    continue
                reserved = await hub.reserve_notification(
                    guild_id,
                    user_id,
                    max_per_hour=_safe_int(settings.get("max_notifications_per_hour"), 5),
                    cooldown_seconds=_safe_int(settings.get("notification_cooldown_seconds"), 300),
                )
                if not reserved:
                    continue
                try:
                    await user.send(
                        content,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except (discord.Forbidden, discord.NotFound):
                    await hub.mark_notification_failure(
                        guild_id,
                        user_id,
                        blocked_until=datetime.now(timezone.utc) + timedelta(hours=24),
                    )
                except discord.HTTPException:
                    pass
                await asyncio.sleep(0.10)

    async def dispatch_event_notifications(self, event: dict[str, Any]) -> None:
        guild_id = _safe_int(event.get("guild_id"), 0)
        guild = self.bot.get_guild(guild_id)
        game = _safe_str(event.get("game_name"))
        if guild is None or not game:
            return
        settings = await self.settings_for(guild_id)
        if not bool(settings.get("notifications_enabled", True)):
            return
        limit = max(
            0,
            min(250, _safe_int(settings.get("max_notification_targets_per_dispatch"), 50)),
        )
        if limit <= 0:
            return
        try:
            targets = await hub.list_notification_targets(
                guild_id,
                game,
                for_event=True,
                limit=limit,
            )
        except hub.CommunityHubError:
            return
        starts = _parse_dt(event.get("starts_at"))
        when = f"<t:{int(starts.timestamp())}:F>" if starts is not None else "soon"
        content = (
            f"📅 **{_safe_str(event.get('title'), 'Community event')}** is scheduled in **{guild.name}** for {when}.\n"
            f"Game/activity: **{game}**\n"
            "Open Community Hub → Events to RSVP. You received this because you opted into this game's event notifications."
        )
        async with self._notification_semaphore:
            for raw_user_id in targets[:limit]:
                user_id = _safe_int(raw_user_id, 0)
                if user_id <= 0:
                    continue
                user = guild.get_member(user_id) or self.bot.get_user(user_id)
                if user is None:
                    continue
                reserved = await hub.reserve_notification(
                    guild_id,
                    user_id,
                    max_per_hour=_safe_int(settings.get("max_notifications_per_hour"), 5),
                    cooldown_seconds=_safe_int(settings.get("notification_cooldown_seconds"), 300),
                )
                if not reserved:
                    continue
                try:
                    await user.send(
                        content,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except (discord.Forbidden, discord.NotFound):
                    await hub.mark_notification_failure(
                        guild_id,
                        user_id,
                        blocked_until=datetime.now(timezone.utc) + timedelta(hours=24),
                    )
                except discord.HTTPException:
                    pass
                await asyncio.sleep(0.10)

    def schedule_event_notifications(self, event: dict[str, Any]) -> None:
        task = asyncio.create_task(
            self.dispatch_event_notifications(event),
            name=f"dank-community-notify-event:{_safe_str(event.get('id'))}",
        )
        self._track_notification_task(task)

    async def _create_session_thread(
        self,
        guild: discord.Guild,
        panel_message: discord.Message,
        session: dict[str, Any],
    ) -> Optional[discord.Thread]:
        channel = panel_message.channel
        if not isinstance(channel, discord.TextChannel):
            return None
        if not _can_create_public_thread(channel):
            return None

        resource = await hub.plan_resource(
            guild_id=int(guild.id),
            session_id=_safe_str(session.get("id")),
            resource_type="thread",
            parent_discord_id=int(channel.id),
        )
        token = _safe_str(resource.get("ownership_token"))
        try:
            thread = await panel_message.create_thread(
                name=_channel_name(f"{session.get('game_name', 'Game')} group"),
                auto_archive_duration=1440,
                reason=_audit_reason(token, "create discussion thread"),
            )
        except Exception as exc:
            await hub.mark_resource_state(
                _safe_str(resource.get("id")),
                "failed" if isinstance(exc, discord.Forbidden) else "unresolved",
                error=f"{type(exc).__name__}: {str(exc)[:300]}",
            )
            if isinstance(exc, discord.Forbidden):
                return None
            raise

        try:
            await hub.finalize_resource(
                _safe_str(resource.get("id")),
                discord_id=int(thread.id),
                parent_discord_id=int(channel.id),
            )
            self._thread_sessions[int(thread.id)] = _safe_str(session.get("id"))
        except hub.CommunityHubError:
            # Creation is proven by audit reason but DB finalization failed. Keep
            # the resource; startup audit reconciliation can recover the token.
            pass

        try:
            await thread.send(
                "Use this thread for the group. Leaving the group does not delete the session; "
                "ending the session starts the configured cleanup grace period.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            pass
        return thread

    async def _create_session_voice(
        self,
        guild: discord.Guild,
        source_channel: discord.abc.GuildChannel,
        session: dict[str, Any],
        settings: dict[str, Any],
    ) -> Optional[discord.VoiceChannel]:
        maximum = max(0, min(50, _safe_int(settings.get("max_temporary_voice_rooms"), 12)))
        if maximum <= 0:
            return None
        current = await hub.count_active_resources(int(guild.id), "voice_channel")
        if current >= maximum:
            return None

        category: Optional[discord.CategoryChannel] = None
        configured_id = _safe_int(settings.get("parent_category_id"), 0)
        if configured_id > 0:
            candidate = guild.get_channel(configured_id)
            if isinstance(candidate, discord.CategoryChannel):
                category = candidate
        if category is None:
            candidate = getattr(source_channel, "category", None)
            if isinstance(candidate, discord.CategoryChannel):
                category = candidate

        if not _can_manage_channels(guild, category):
            return None

        resource = await hub.plan_resource(
            guild_id=int(guild.id),
            session_id=_safe_str(session.get("id")),
            resource_type="voice_channel",
            parent_discord_id=int(category.id) if category else None,
        )
        token = _safe_str(resource.get("ownership_token"))
        try:
            voice = await guild.create_voice_channel(
                name=_channel_name(f"🎮 {session.get('game_name', 'Gaming')}"),
                category=category,
                user_limit=max(0, min(99, _safe_int(session.get("capacity"), 6))),
                reason=_audit_reason(token, "create temporary voice"),
            )
        except Exception as exc:
            await hub.mark_resource_state(
                _safe_str(resource.get("id")),
                "failed" if isinstance(exc, discord.Forbidden) else "unresolved",
                error=f"{type(exc).__name__}: {str(exc)[:300]}",
            )
            if isinstance(exc, discord.Forbidden):
                return None
            raise

        try:
            await hub.finalize_resource(
                _safe_str(resource.get("id")),
                discord_id=int(voice.id),
                parent_discord_id=int(category.id) if category else None,
            )
            self._voice_sessions[int(voice.id)] = _safe_str(session.get("id"))
        except hub.CommunityHubError:
            pass
        return voice

    async def refresh_session_card(
        self,
        session: dict[str, Any] | str,
        *,
        interaction: Optional[discord.Interaction] = None,
    ) -> None:
        if isinstance(session, str):
            session = await hub.get_session(session)
        session_id = _safe_str(session.get("id"))
        guild_id = _safe_int(session.get("guild_id"), 0)
        members = await hub.list_session_members(session_id)

        from .commands_ext.public_community_hub import CommunitySessionPublicView, build_session_embed

        embed = build_session_embed(session, members)
        ended = session.get("state") in {"ended", "archived", "cleaned", "failed", "abandoned"}
        view = CommunitySessionPublicView(ended=ended)

        if interaction is not None and getattr(interaction, "message", None) is not None:
            try:
                if not interaction.response.is_done():
                    await interaction.response.edit_message(
                        embed=embed,
                        view=view,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    return
            except discord.HTTPException:
                pass

        channel_id = _safe_int(session.get("panel_channel_id"), 0)
        message_id = _safe_int(session.get("panel_message_id"), 0)
        if guild_id <= 0 or channel_id <= 0 or message_id <= 0:
            return
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        channel = guild.get_channel(channel_id) or self.bot.get_channel(channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        try:
            message = channel.get_partial_message(message_id)
            await message.edit(
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

    async def begin_end(
        self,
        session_id: str,
        guild_id: int,
        actor_id: int,
        *,
        staff_override: bool = False,
        reason: str = "Ended by host",
    ) -> dict[str, Any]:
        session = await hub.transition_session(
            session_id,
            guild_id,
            actor_id,
            "begin_end",
            staff_override=staff_override,
            reason=reason,
        )
        await self.refresh_session_card(session)
        settings = await self.settings_for(guild_id)
        self.schedule_cleanup(
            session_id,
            guild_id,
            actor_id=actor_id,
            delay_seconds=max(0, _safe_int(settings.get("cleanup_grace_seconds"), 300)),
        )
        return session

    def schedule_cleanup(
        self,
        session_id: str,
        guild_id: int,
        *,
        actor_id: int,
        delay_seconds: int,
    ) -> None:
        sid = _safe_str(session_id)
        existing = self._cleanup_tasks.get(sid)
        if existing is not None and not existing.done():
            return

        async def _runner() -> None:
            try:
                if delay_seconds > 0:
                    await asyncio.sleep(float(delay_seconds))
                await self.cleanup_session_now(sid, guild_id, actor_id=actor_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log(f"cleanup failed session={sid} error={type(exc).__name__}: {exc}")
            finally:
                self._cleanup_tasks.pop(sid, None)

        self._cleanup_tasks[sid] = asyncio.create_task(
            _runner(),
            name=f"dank-community-cleanup:{sid}",
        )

    def cancel_scheduled_cleanup(self, session_id: str) -> bool:
        sid = _safe_str(session_id)
        task = self._cleanup_tasks.get(sid)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    def schedule_empty_cleanup(
        self,
        session_id: str,
        guild_id: int,
        *,
        actor_id: int,
        delay_seconds: int,
    ) -> None:
        sid = _safe_str(session_id)
        existing = self._cleanup_tasks.get(sid)
        if existing is not None and not existing.done():
            return

        async def _runner() -> None:
            try:
                if delay_seconds > 0:
                    await asyncio.sleep(float(delay_seconds))
                members = await hub.list_session_members(sid)
                active = [
                    row
                    for row in members
                    if _safe_str(row.get("role")) != "waitlist"
                ]
                if active:
                    return
                session = await hub.get_session(sid, guild_id=guild_id)
                if session.get("state") in {"ending", "ended", "archived", "cleaned"}:
                    if session.get("state") == "ending":
                        await self.cleanup_session_now(sid, guild_id, actor_id=actor_id)
                    return
                await hub.transition_session(
                    sid,
                    guild_id,
                    actor_id,
                    "begin_end",
                    staff_override=True,
                    reason="Empty session cleanup grace expired",
                )
                await self.cleanup_session_now(sid, guild_id, actor_id=actor_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log(f"empty cleanup failed session={sid} error={type(exc).__name__}: {exc}")
            finally:
                self._cleanup_tasks.pop(sid, None)

        self._cleanup_tasks[sid] = asyncio.create_task(
            _runner(),
            name=f"dank-community-empty-cleanup:{sid}",
        )

    async def cleanup_session_now(
        self,
        session_id: str,
        guild_id: int,
        *,
        actor_id: int,
    ) -> None:
        session_before = await hub.get_session(session_id, guild_id=guild_id)
        if session_before.get("state") == "cleaned":
            return
        if session_before.get("state") not in {"ending", "ended", "archived", "failed"}:
            return
        first_finalize = not bool(session_before.get("ended_at"))
        previously_cleaned = session_before.get("state") == "cleaned"
        resources = await hub.list_session_resources(session_id)
        settings = await self.settings_for(guild_id)
        guild = self.bot.get_guild(int(guild_id))
        failures = 0

        # Discussion goes quiet first, then voice disappears. The panel is kept
        # as an ended-session receipt and Play Again doorway.
        for resource in resources:
            if _safe_str(resource.get("state")) in {"deleted", "converted"}:
                continue
            resource_type = _safe_str(resource.get("resource_type"))
            resource_id = _safe_str(resource.get("id"))
            discord_id = _safe_int(resource.get("discord_id"), 0)
            if resource_type == "panel_message":
                try:
                    await hub.mark_resource_state(resource_id, "archived")
                except hub.CommunityHubError:
                    pass
                continue
            if discord_id <= 0:
                # No proven Discord resource ID means no destructive cleanup.
                try:
                    await hub.mark_resource_state(
                        resource_id,
                        "unresolved",
                        error="Cleanup skipped because durable Discord resource identity is missing.",
                    )
                except hub.CommunityHubError:
                    pass
                failures += 1
                continue
            if guild is None:
                failures += 1
                continue

            if resource_type == "thread":
                channel = guild.get_thread(discord_id) or self.bot.get_channel(discord_id)
            else:
                channel = guild.get_channel(discord_id) or self.bot.get_channel(discord_id)

            if channel is None:
                try:
                    channel = await with_retry(
                        lambda: guild.fetch_channel(discord_id),
                        attempts=2,
                        concurrency_key=f"community-hub:{guild_id}:cleanup-fetch",
                    )
                except discord.NotFound:
                    await hub.mark_resource_state(resource_id, "deleted")
                    self._voice_sessions.pop(discord_id, None)
                    self._thread_sessions.pop(discord_id, None)
                    continue
                except discord.Forbidden as exc:
                    failures += 1
                    await hub.mark_resource_state(
                        resource_id,
                        "failed",
                        error=f"Forbidden while resolving cleanup target: {str(exc)[:300]}",
                    )
                    await hub.bump_hourly_metric(guild_id, "cleanup_failures", 1)
                    continue
                except discord.HTTPException as exc:
                    failures += 1
                    await hub.mark_resource_state(
                        resource_id,
                        "failed",
                        error=f"HTTPException while resolving cleanup target: {str(exc)[:300]}",
                    )
                    await hub.bump_hourly_metric(guild_id, "cleanup_failures", 1)
                    continue

            try:
                if resource_type == "thread" and isinstance(channel, discord.Thread):
                    if bool(settings.get("archive_threads_on_end", True)):
                        await with_retry(
                            lambda channel=channel: channel.edit(
                                archived=True,
                                reason=_audit_reason(
                                    _safe_str(resource.get("ownership_token")),
                                    "archive ended discussion",
                                ),
                            ),
                            attempts=3,
                            concurrency_key=f"community-hub:{guild_id}:thread-cleanup",
                        )
                        await hub.mark_resource_state(resource_id, "archived")
                    else:
                        await with_retry(
                            lambda channel=channel: channel.delete(
                                reason=_audit_reason(
                                    _safe_str(resource.get("ownership_token")),
                                    "delete ended discussion",
                                )
                            ),
                            attempts=3,
                            concurrency_key=f"community-hub:{guild_id}:thread-cleanup",
                        )
                        await hub.mark_resource_state(resource_id, "deleted")
                    self._thread_sessions.pop(discord_id, None)
                elif resource_type in {"voice_channel", "category"}:
                    await with_retry(
                        lambda channel=channel: channel.delete(
                            reason=_audit_reason(
                                _safe_str(resource.get("ownership_token")),
                                "clean ended temporary resource",
                            )
                        ),
                        attempts=3,
                        concurrency_key=f"community-hub:{guild_id}:channel-cleanup",
                    )
                    await hub.mark_resource_state(resource_id, "deleted")
                    self._voice_sessions.pop(discord_id, None)
                else:
                    await hub.mark_resource_state(resource_id, "archived")
            except discord.NotFound:
                await hub.mark_resource_state(resource_id, "deleted")
                self._voice_sessions.pop(discord_id, None)
                self._thread_sessions.pop(discord_id, None)
            except discord.Forbidden as exc:
                failures += 1
                await hub.mark_resource_state(
                    resource_id,
                    "failed",
                    error=f"Forbidden: {str(exc)[:300]}",
                )
                await hub.bump_hourly_metric(guild_id, "cleanup_failures", 1)
            except discord.HTTPException as exc:
                failures += 1
                await hub.mark_resource_state(
                    resource_id,
                    "failed",
                    error=f"HTTPException: {str(exc)[:300]}",
                )
                await hub.bump_hourly_metric(guild_id, "cleanup_failures", 1)

        final_state = "ended" if failures else "cleaned"
        try:
            session = await hub.finalize_end_session(
                session_id,
                guild_id,
                actor_id,
                cleanup_state=final_state,
            )
        except hub.CommunityConflict:
            session = await hub.get_session(session_id, guild_id=guild_id)

        if first_finalize:
            await hub.bump_hourly_metric(guild_id, "sessions_ended", 1)
        if final_state == "cleaned" and not previously_cleaned:
            await hub.bump_hourly_metric(guild_id, "completed_sessions", 1)
        await self.refresh_session_card(session)

    async def restart_from_ended(
        self,
        interaction: discord.Interaction,
        session: dict[str, Any],
    ) -> dict[str, Any]:
        guild = interaction.guild
        if guild is None:
            raise hub.CommunityHubError("This session no longer belongs to an available server.")
        old_id = _safe_str(session.get("id"))
        key = f"play-again:{old_id}:{int(interaction.user.id)}:{int(time.time() // 10)}"
        new_session = await hub.restart_session(
            old_id,
            int(guild.id),
            int(interaction.user.id),
            idempotency_key=key,
        )
        return await self.provision_session(interaction, new_session)

    async def touch_session(self, session_id: str, guild_id: int) -> None:
        sid = _safe_str(session_id)
        now = time.monotonic()
        last = self._touch_last.get(sid, 0.0)
        if now - last < _ACTIVITY_TOUCH_SECONDS:
            return
        self._touch_last[sid] = now
        await hub.touch_session(sid, guild_id)

    async def on_message(self, message: discord.Message) -> None:
        if not _is_human(getattr(message, "author", None)):
            return
        guild = getattr(message, "guild", None)
        if guild is None:
            return
        sid = self._thread_sessions.get(int(getattr(message.channel, "id", 0) or 0))
        if sid:
            await self.touch_session(sid, int(guild.id))

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if not _is_human(member):
            return
        guild = member.guild
        touched: set[str] = set()
        for state in (before, after):
            channel = getattr(state, "channel", None)
            channel_id = _safe_int(getattr(channel, "id", 0), 0)
            sid = self._voice_sessions.get(channel_id)
            if sid and sid not in touched:
                touched.add(sid)
                await self.touch_session(sid, int(guild.id))

        try:
            voice_count = sum(
                1
                for voice in guild.voice_channels
                for voice_member in list(getattr(voice, "members", []) or [])
                if _is_human(voice_member)
            )
            await hub.bump_hourly_metric(
                int(guild.id),
                "voice_participants_peak",
                voice_count,
                peak=True,
            )
        except Exception:
            pass

        before_channel = getattr(before, "channel", None)
        if before_channel is not None:
            before_id = _safe_int(getattr(before_channel, "id", 0), 0)
            sid = self._voice_sessions.get(before_id)
            if sid and not list(getattr(before_channel, "members", []) or []):
                try:
                    session = await hub.get_session(sid, guild_id=int(guild.id))
                    if session.get("state") == "ending":
                        self.schedule_cleanup(
                            sid,
                            int(guild.id),
                            actor_id=int(getattr(getattr(guild, "me", None), "id", 0) or 0),
                            delay_seconds=5,
                        )
                except hub.CommunityHubError:
                    pass

    async def on_member_remove(self, member: discord.Member) -> None:
        if not _is_human(member):
            return
        try:
            await hub.clear_availability(int(member.guild.id), int(member.id))
        except hub.CommunityHubError:
            return


    async def on_presence_update(
        self,
        before: discord.Member,
        after: discord.Member,
    ) -> None:
        guild = getattr(after, "guild", None) or getattr(before, "guild", None)
        if guild is None:
            return
        gid = int(guild.id)
        if gid not in self._presence_enabled_guilds:
            return
        self._presence_dirty.add(gid)

    async def _aggregate_presence_if_due(self, guild: discord.Guild) -> None:
        gid = int(guild.id)
        if gid not in self._presence_enabled_guilds or gid not in self._presence_dirty:
            return
        now = time.monotonic()
        if now - self._presence_last_aggregate.get(gid, 0.0) < _PRESENCE_AGGREGATE_SECONDS:
            return
        self._presence_last_aggregate[gid] = now
        self._presence_dirty.discard(gid)

        online = 0
        playing: dict[str, int] = {}
        for member in list(getattr(guild, "members", []) or []):
            if not _is_human(member):
                continue
            status = getattr(member, "status", discord.Status.offline)
            if status != discord.Status.offline:
                online += 1
            for activity in list(getattr(member, "activities", []) or []):
                if getattr(activity, "type", None) != discord.ActivityType.playing:
                    continue
                name = _safe_str(getattr(activity, "name", ""))
                if name:
                    playing[name] = playing.get(name, 0) + 1
                    break

        await hub.bump_hourly_metric(gid, "online_presence_peak", online, peak=True)
        gaming_total = sum(playing.values())
        await hub.bump_hourly_metric(gid, "gaming_presence_peak", gaming_total, peak=True)
        for name, count in sorted(playing.items(), key=lambda item: item[1], reverse=True)[:50]:
            await hub.bump_game_metric(gid, name, "active_players_peak", count, peak=True)

    async def pulse_snapshot(self, guild: discord.Guild) -> dict[str, Any]:
        sessions = await hub.list_active_sessions(int(guild.id), limit=100)
        active_groups = [
            row
            for row in sessions
            if row.get("state") in {"open", "forming", "ready", "active", "paused"}
        ]
        voice_members: set[int] = set()
        for voice in guild.voice_channels:
            for member in list(getattr(voice, "members", []) or []):
                if _is_human(member):
                    voice_members.add(int(member.id))

        availability = await hub.availability_summary(int(guild.id), limit=1000)
        playing: dict[str, int] = {}
        presence_available = bool(
            getattr(getattr(self.bot, "intents", None), "presences", False)
            and int(guild.id) in self._presence_enabled_guilds
        )
        online = 0
        if presence_available:
            for member in list(getattr(guild, "members", []) or []):
                if not _is_human(member):
                    continue
                if getattr(member, "status", discord.Status.offline) != discord.Status.offline:
                    online += 1
                for activity in list(getattr(member, "activities", []) or []):
                    if getattr(activity, "type", None) == discord.ActivityType.playing:
                        name = _safe_str(getattr(activity, "name", ""))
                        if name:
                            playing[name] = playing.get(name, 0) + 1
                            break

        return {
            "active_groups": len(active_groups),
            "voice_members": len(voice_members),
            "open_to_play_members": _safe_int(availability.get("unique_users"), 0),
            "open_to_play_games": list(availability.get("top_games") or [])[:5],
            "presence_available": presence_available,
            "online_members": online if presence_available else None,
            "gaming_members": sum(playing.values()) if presence_available else None,
            "top_games": sorted(playing.items(), key=lambda item: item[1], reverse=True)[:5],
        }

    async def partner_activity_snapshots(
        self,
        guild_id: int,
        *,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        requester_settings = await self.settings_for(int(guild_id))
        if not bool(requester_settings.get("partner_discovery_enabled")):
            return []
        links = await hub.list_partner_links(int(guild_id), active_only=True)
        snapshots: list[dict[str, Any]] = []
        for link in links:
            if len(snapshots) >= max(1, min(25, int(limit))):
                break
            if not bool(link.get("aggregate_activity_shared")):
                continue
            a = _safe_int(link.get("guild_a_id"), 0)
            b = _safe_int(link.get("guild_b_id"), 0)
            target_id = b if a == int(guild_id) else a
            if target_id <= 0 or target_id == int(guild_id):
                continue
            target = self.bot.get_guild(target_id)
            if target is None:
                continue
            try:
                target_settings = await self.settings_for(target_id)
            except hub.CommunityHubError:
                continue
            if not bool(target_settings.get("partner_discovery_enabled")):
                continue
            try:
                pulse = await self.pulse_snapshot(target)
            except hub.CommunityHubError:
                continue
            snapshots.append(
                {
                    "guild_id": target_id,
                    "guild_name": _safe_str(getattr(target, "name", ""), f"Server {target_id}"),
                    "active_groups": _safe_int(pulse.get("active_groups"), 0),
                    "voice_members": _safe_int(pulse.get("voice_members"), 0),
                    "open_to_play_members": _safe_int(pulse.get("open_to_play_members"), 0),
                    "presence_available": bool(pulse.get("presence_available")),
                    "online_members": pulse.get("online_members"),
                    "gaming_members": pulse.get("gaming_members"),
                    "top_games": list(pulse.get("top_games") or [])[:3],
                }
            )
        return snapshots

    async def reliability_snapshot(self) -> dict[str, Any]:
        health = operation_queue_health_summary()
        try:
            from .startup_guards.discord_api_safety import recovery_discord_rest_budget_snapshot
            recovery = recovery_discord_rest_budget_snapshot()
        except Exception:
            recovery = {}
        return {
            "operation_queue": health,
            "recovery_rest": recovery,
            "cleanup_tasks": sum(1 for task in self._cleanup_tasks.values() if not task.done()),
            "notification_tasks": sum(1 for task in self._notification_tasks if not task.done()),
            "managed_voice_cached": len(self._voice_sessions),
            "managed_threads_cached": len(self._thread_sessions),
        }

    async def _maintenance_loop(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                for guild in list(getattr(self.bot, "guilds", []) or []):
                    try:
                        settings = await self.settings_for(int(guild.id))
                    except hub.CommunityHubError:
                        continue
                    if bool(settings.get("presence_analytics_enabled")):
                        await self._aggregate_presence_if_due(guild)
                    ready_timeout = max(60, _safe_int(settings.get("ready_timeout_seconds"), 300))
                    await hub.clear_stale_ready(
                        int(guild.id),
                        cutoff=datetime.now(timezone.utc) - timedelta(seconds=ready_timeout),
                    )
                    await self._expire_idle_sessions(guild, settings)
                await self._run_retention_pass()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log(f"maintenance pass degraded: {type(exc).__name__}: {exc}")
            await asyncio.sleep(float(_MAINTENANCE_SECONDS))

    async def _expire_idle_sessions(
        self,
        guild: discord.Guild,
        settings: dict[str, Any],
    ) -> None:
        if not bool(settings.get("enabled", True)):
            return
        sessions = await hub.list_active_sessions(int(guild.id), limit=100)
        now = datetime.now(timezone.utc)
        idle_timeout = max(300, _safe_int(settings.get("idle_timeout_seconds"), 1800))
        hard_lifetime = max(1800, _safe_int(settings.get("max_session_lifetime_seconds"), 43200))
        actor_id = int(getattr(getattr(guild, "me", None), "id", 0) or 0)

        for session in sessions:
            if session.get("state") in {"ending", "recovering"}:
                continue
            created = _parse_dt(session.get("created_at"))
            activity = _parse_dt(session.get("last_activity_at")) or created
            too_old = bool(created and (now - created).total_seconds() >= hard_lifetime)
            idle = bool(activity and (now - activity).total_seconds() >= idle_timeout)
            if not (too_old or idle):
                continue
            sid = _safe_str(session.get("id"))
            try:
                await hub.transition_session(
                    sid,
                    int(guild.id),
                    actor_id,
                    "begin_end",
                    staff_override=True,
                    reason="Maximum lifetime reached" if too_old else "Inactive session timed out",
                )
            except hub.CommunityHubError:
                continue
            self.schedule_cleanup(
                sid,
                int(guild.id),
                actor_id=actor_id,
                delay_seconds=max(60, _safe_int(settings.get("cleanup_grace_seconds"), 300)),
            )

    async def _run_retention_pass(self) -> None:
        # Five-minute loop, but each guild retention delete is bounded and cheap.
        # The timestamp guard prevents turning retention into a database metronome.
        marker = getattr(self, "_last_retention_monotonic", 0.0)
        now = time.monotonic()
        if now - marker < 6 * 60 * 60:
            return
        self._last_retention_monotonic = now
        for guild in list(getattr(self.bot, "guilds", []) or []):
            try:
                settings = await self.settings_for(int(guild.id))
                retention_days = _safe_int(settings.get("detailed_retention_days"), 30)
                await hub.delete_expired_detailed_events(
                    int(guild.id),
                    retention_days=retention_days,
                )
                await hub.prune_session_personal_data(
                    int(guild.id),
                    retention_days=retention_days,
                )
                await hub.delete_expired_availability(int(guild.id))
            except hub.CommunityHubError:
                continue


def ensure_community_hub_runtime(bot: Any) -> CommunityHubRuntime:
    existing = getattr(bot, _RUNTIME_ATTR, None)
    if isinstance(existing, CommunityHubRuntime):
        return existing

    runtime = CommunityHubRuntime(bot)
    setattr(bot, _RUNTIME_ATTR, runtime)
    bot.add_listener(runtime.on_ready, "on_ready")
    bot.add_listener(runtime.on_message, "on_message")
    bot.add_listener(runtime.on_voice_state_update, "on_voice_state_update")
    bot.add_listener(runtime.on_member_remove, "on_member_remove")
    bot.add_listener(runtime.on_presence_update, "on_presence_update")
    return runtime


def community_hub_runtime(bot: Any) -> Optional[CommunityHubRuntime]:
    runtime = getattr(bot, _RUNTIME_ATTR, None)
    return runtime if isinstance(runtime, CommunityHubRuntime) else None


__all__ = [
    "CommunityHubRuntime",
    "community_hub_runtime",
    "ensure_community_hub_runtime",
]
