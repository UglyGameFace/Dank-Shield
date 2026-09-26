from __future__ import annotations

"""Single-owner runtime for Dank Shield live voice captions.

Community Hub sessions and general server voice channels share this same
receiver owner. One caption receiver may own a guild voice connection at a
time. Speaker consent is memory-only and must be re-established after restart.
Audio is never persisted by Dank Shield.
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

import discord

from .community_voice_captions import CaptionEngine, openai_transcriber_from_env
from .community_voice_receive import (
    PerSpeakerFrameBridge,
    VoiceReceiveUnavailable,
    connect_receive_client,
    disconnect_receive_client,
    voice_receive_capability,
)


log = logging.getLogger(__name__)


@dataclass(slots=True)
class CaptionRuntimeState:
    session_id: str
    guild_id: int
    voice_channel_id: int
    destination_channel_id: int
    voice_client: Any
    bridge: PerSpeakerFrameBridge
    engine: CaptionEngine
    scope_kind: str = "community_hub"


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, str(default))).strip())
    except (TypeError, ValueError):
        value = int(default)
    return max(int(minimum), min(int(maximum), value))


SERVER_CAPTION_SCOPE_PREFIX = "server:"


def server_caption_scope_id(guild_id: int) -> str:
    gid = int(guild_id)
    if gid <= 0:
        raise ValueError("guild_id must be positive")
    return f"{SERVER_CAPTION_SCOPE_PREFIX}{gid}"


def live_captions_enabled() -> bool:
    return str(os.getenv("DANK_COMMUNITY_LIVE_CAPTIONS_ENABLED", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


class CommunityVoiceCaptionManager:
    def __init__(self, bot: Any) -> None:
        self.bot = bot
        self._sessions: dict[str, CaptionRuntimeState] = {}
        self._guild_owner: dict[int, str] = {}
        self._lock = asyncio.Lock()
        self.max_active_guilds = _env_int(
            "DANK_COMMUNITY_CAPTION_MAX_ACTIVE_GUILDS", 4, 1, 100
        )
        self.max_speakers_per_session = _env_int(
            "DANK_COMMUNITY_CAPTION_MAX_SPEAKERS", 8, 1, 25
        )
        self.max_concurrent_transcriptions = _env_int(
            "DANK_COMMUNITY_CAPTION_MAX_CONCURRENT_TRANSCRIPTIONS", 6, 1, 100
        )
        self._global_transcribe_semaphore = asyncio.Semaphore(
            self.max_concurrent_transcriptions
        )

    def status(self, session_id: str) -> dict[str, Any]:
        state = self._sessions.get(str(session_id))
        capability = voice_receive_capability()
        if state is None:
            return {
                "active": False,
                "capability": capability,
                "opted_in_user_ids": [],
            }
        return {
            "active": True,
            "capability": capability,
            "session_id": state.session_id,
            "scope_kind": state.scope_kind,
            "guild_id": state.guild_id,
            "voice_channel_id": state.voice_channel_id,
            "destination_channel_id": state.destination_channel_id,
            "opted_in_user_ids": list(state.bridge.opted_in_user_ids()),
            "health": state.bridge.health.snapshot(),
            "segments_transcribed": state.engine.segments_transcribed,
            "segments_unclear": state.engine.segments_unclear,
            "queue_overflow": state.engine.queue_overflow,
            "segment_failures": state.engine.segment_failures,
        }

    def status_for_guild(self, guild_id: int) -> dict[str, Any]:
        gid = int(guild_id)
        sid = self._guild_owner.get(gid)
        if sid:
            return self.status(sid)
        capability = voice_receive_capability()
        return {
            "active": False,
            "capability": capability,
            "session_id": "",
            "scope_kind": "",
            "guild_id": gid,
            "opted_in_user_ids": [],
        }

    async def start_server(
        self,
        *,
        guild_id: int,
        voice_channel_id: int,
        destination_channel_id: int,
    ) -> CaptionRuntimeState:
        gid = int(guild_id)
        return await self.start(
            {
                "id": server_caption_scope_id(gid),
                "guild_id": gid,
                "voice_channel_id": int(voice_channel_id),
                "panel_channel_id": int(destination_channel_id),
                "caption_scope": "server",
            }
        )

    async def start(self, session: dict[str, Any]) -> CaptionRuntimeState:
        sid = str(session.get("id") or "").strip()
        guild_id = int(session.get("guild_id") or 0)
        voice_channel_id = int(session.get("voice_channel_id") or 0)
        destination_channel_id = int(
            session.get("thread_id")
            or session.get("panel_channel_id")
            or 0
        )
        scope_kind = (
            "server"
            if str(session.get("caption_scope") or "").strip().lower() == "server"
            else "community_hub"
        )
        scope_label = (
            "Dank Shield Live Captions"
            if scope_kind == "server"
            else "Community Hub Live Captions"
        )
        if not live_captions_enabled():
            raise VoiceReceiveUnavailable(
                "Live Captions are disabled on this host until the DAVE receive soak test is completed."
            )
        if not sid or guild_id <= 0:
            raise VoiceReceiveUnavailable("Live Captions session identity is missing.")
        if voice_channel_id <= 0:
            raise VoiceReceiveUnavailable(
                "This Live Captions session does not have a voice room to caption."
            )
        if destination_channel_id <= 0:
            raise VoiceReceiveUnavailable(
                "This Live Captions session has nowhere to publish captions."
            )
        if not os.getenv("OPENAI_API_KEY", "").strip():
            raise VoiceReceiveUnavailable(
                "Live captions need OPENAI_API_KEY configured on the Dank Shield host."
            )

        capability = voice_receive_capability()
        if not capability.available:
            raise VoiceReceiveUnavailable(capability.reason)

        async with self._lock:
            existing = self._sessions.get(sid)
            if existing is not None:
                return existing

            if len(self._sessions) >= self.max_active_guilds:
                raise VoiceReceiveUnavailable(
                    "This Dank Shield process is already at its configured Live Captions session limit."
                )

            other_sid = self._guild_owner.get(guild_id)
            if other_sid and other_sid != sid:
                raise VoiceReceiveUnavailable(
                    "Another Live Captions session in this server already owns the voice receiver."
                )

            guild = self.bot.get_guild(guild_id)
            if guild is None:
                raise VoiceReceiveUnavailable("Dank Shield cannot resolve this server.")
            voice_channel = guild.get_channel(voice_channel_id)
            if not isinstance(voice_channel, discord.VoiceChannel):
                raise VoiceReceiveUnavailable(
                    "The Live Captions voice room no longer exists."
                )

            destination = (
                guild.get_thread(destination_channel_id)
                or guild.get_channel(destination_channel_id)
                or self.bot.get_channel(destination_channel_id)
            )
            if destination is None or not hasattr(destination, "send"):
                raise VoiceReceiveUnavailable(
                    "The Live Captions text destination no longer exists."
                )

            transcriber = openai_transcriber_from_env()

            async def _publish(user_id: int, text: str, confidence: float) -> None:
                member = guild.get_member(int(user_id))
                display = (
                    getattr(member, "display_name", None)
                    or getattr(self.bot.get_user(int(user_id)), "display_name", None)
                    or f"User {int(user_id)}"
                )
                safe_name = discord.utils.escape_markdown(str(display))[:80]
                suffix = ""
                if text == "[unclear audio]":
                    suffix = " • low confidence"
                await destination.send(
                    f"🎙️ **{safe_name} · Live Caption:** {text}{suffix}",
                    allowed_mentions=discord.AllowedMentions.none(),
                )

            engine = CaptionEngine(
                transcriber,
                _publish,
                global_transcribe_semaphore=self._global_transcribe_semaphore,
            )
            loop = asyncio.get_running_loop()
            bridge = PerSpeakerFrameBridge(loop, engine.submit)
            voice_client = await connect_receive_client(voice_channel, bridge)

            try:
                await destination.send(
                    f"📝 **{scope_label} started.**\n"
                    "Dank Shield keeps each opted-in Discord speaker isolated before transcription. "
                    "Only members who explicitly choose **Caption My Voice** are transcribed. "
                    "Opted-in audio is sent to **OpenAI's transcription API** for speech-to-text. "
                    "Dank Shield keeps audio only in bounded memory while processing it and does not save the audio.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                disconnect_receive_client(voice_client)
                try:
                    if getattr(voice_client, "is_connected", lambda: False)():
                        await voice_client.disconnect(force=False)
                except Exception:
                    log.exception("Live Captions failed to roll back voice connection")
                raise VoiceReceiveUnavailable(
                    "Dank Shield could not post the Live Captions privacy notice, so captions were not started."
                ) from exc

            state = CaptionRuntimeState(
                session_id=sid,
                guild_id=guild_id,
                voice_channel_id=voice_channel_id,
                destination_channel_id=destination_channel_id,
                voice_client=voice_client,
                bridge=bridge,
                engine=engine,
                scope_kind=scope_kind,
            )
            self._sessions[sid] = state
            self._guild_owner[guild_id] = sid
            engine.start()
            return state

    async def toggle_consent(self, session_id: str, user_id: int) -> bool:
        sid = str(session_id)
        state = self._sessions.get(sid)
        if state is None:
            raise VoiceReceiveUnavailable(
                "Live Captions are not running for this session."
            )
        uid = int(user_id)
        if state.bridge.is_opted_in(uid):
            state.bridge.opt_out(uid)
            return False
        if len(state.bridge.opted_in_user_ids()) >= self.max_speakers_per_session:
            raise VoiceReceiveUnavailable(
                "This session has reached its configured Live Captions speaker limit."
            )
        state.bridge.opt_in(uid)
        return True

    async def stop(self, session_id: str, *, announce: bool = True) -> bool:
        sid = str(session_id)
        async with self._lock:
            state = self._sessions.pop(sid, None)
            if state is None:
                return False
            if self._guild_owner.get(state.guild_id) == sid:
                self._guild_owner.pop(state.guild_id, None)

        state.bridge.clear_consent()
        try:
            await state.engine.close()
        finally:
            disconnect_receive_client(state.voice_client)

        guild = self.bot.get_guild(state.guild_id)
        destination = None
        if guild is not None:
            destination = (
                guild.get_thread(state.destination_channel_id)
                or guild.get_channel(state.destination_channel_id)
                or self.bot.get_channel(state.destination_channel_id)
            )
        if announce and destination is not None and hasattr(destination, "send"):
            try:
                scope_label = (
                    "Dank Shield Live Captions"
                    if state.scope_kind == "server"
                    else "Community Hub Live Captions"
                )
                await destination.send(
                    f"📝 **{scope_label} stopped.** Speaker consent was cleared.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass

        voice_client = state.voice_client
        try:
            if getattr(voice_client, "is_connected", lambda: False)():
                await voice_client.disconnect(force=False)
        except Exception:
            log.exception("Live Captions voice disconnect failed session=%s", sid)
        return True

    async def stop_guild(self, guild_id: int) -> bool:
        sid = self._guild_owner.get(int(guild_id))
        return await self.stop(sid) if sid else False


_MANAGER_ATTR = "_dank_community_voice_caption_manager"


def ensure_community_voice_caption_manager(bot: Any) -> CommunityVoiceCaptionManager:
    existing = getattr(bot, _MANAGER_ATTR, None)
    if isinstance(existing, CommunityVoiceCaptionManager):
        return existing
    manager = CommunityVoiceCaptionManager(bot)
    setattr(bot, _MANAGER_ATTR, manager)
    return manager


__all__ = [
    "CaptionRuntimeState",
    "CommunityVoiceCaptionManager",
    "SERVER_CAPTION_SCOPE_PREFIX",
    "ensure_community_voice_caption_manager",
    "live_captions_enabled",
    "server_caption_scope_id",
]
