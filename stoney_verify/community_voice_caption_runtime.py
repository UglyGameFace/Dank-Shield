from __future__ import annotations

"""Runtime ownership for Community Hub live voice captions.

One caption receiver may own a guild voice connection at a time. Speaker
consent is memory-only and must be re-established after restart. Audio is never
persisted by Dank Shield.
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


class CommunityVoiceCaptionManager:
    def __init__(self, bot: Any) -> None:
        self.bot = bot
        self._sessions: dict[str, CaptionRuntimeState] = {}
        self._guild_owner: dict[int, str] = {}
        self._lock = asyncio.Lock()

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
            "guild_id": state.guild_id,
            "voice_channel_id": state.voice_channel_id,
            "destination_channel_id": state.destination_channel_id,
            "opted_in_user_ids": sorted(state.bridge._allowed_user_ids),
            "health": state.bridge.health.snapshot(),
            "segments_transcribed": state.engine.segments_transcribed,
            "segments_unclear": state.engine.segments_unclear,
            "queue_overflow": state.engine.queue_overflow,
        }

    async def start(self, session: dict[str, Any]) -> CaptionRuntimeState:
        sid = str(session.get("id") or "").strip()
        guild_id = int(session.get("guild_id") or 0)
        voice_channel_id = int(session.get("voice_channel_id") or 0)
        destination_channel_id = int(
            session.get("thread_id")
            or session.get("panel_channel_id")
            or 0
        )
        if not sid or guild_id <= 0:
            raise VoiceReceiveUnavailable("Community Hub session identity is missing.")
        if voice_channel_id <= 0:
            raise VoiceReceiveUnavailable(
                "This Community Hub session does not have a voice room to caption."
            )
        if destination_channel_id <= 0:
            raise VoiceReceiveUnavailable(
                "This Community Hub session has nowhere to publish captions."
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

            other_sid = self._guild_owner.get(guild_id)
            if other_sid and other_sid != sid:
                raise VoiceReceiveUnavailable(
                    "Another Community Hub session in this server already owns live captions."
                )

            guild = self.bot.get_guild(guild_id)
            if guild is None:
                raise VoiceReceiveUnavailable("Dank Shield cannot resolve this server.")
            voice_channel = guild.get_channel(voice_channel_id)
            if not isinstance(voice_channel, discord.VoiceChannel):
                raise VoiceReceiveUnavailable(
                    "The Community Hub voice room no longer exists."
                )

            destination = (
                guild.get_thread(destination_channel_id)
                or guild.get_channel(destination_channel_id)
                or self.bot.get_channel(destination_channel_id)
            )
            if destination is None or not hasattr(destination, "send"):
                raise VoiceReceiveUnavailable(
                    "The Community Hub caption destination no longer exists."
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

            engine = CaptionEngine(transcriber, _publish)
            loop = asyncio.get_running_loop()
            bridge = PerSpeakerFrameBridge(loop, engine.submit)
            voice_client = await connect_receive_client(voice_channel, bridge)
            state = CaptionRuntimeState(
                session_id=sid,
                guild_id=guild_id,
                voice_channel_id=voice_channel_id,
                destination_channel_id=destination_channel_id,
                voice_client=voice_client,
                bridge=bridge,
                engine=engine,
            )
            self._sessions[sid] = state
            self._guild_owner[guild_id] = sid
            engine.start()

            await destination.send(
                "📝 **Community Hub Live Captions started.**\n"
                "Dank Shield keeps each opted-in Discord speaker isolated before transcription. "
                "Only members who explicitly choose **Caption My Voice** are transcribed. "
                "Audio is held only in bounded memory while it is being transcribed and is not saved by Dank Shield.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return state

    async def toggle_consent(self, session_id: str, user_id: int) -> bool:
        sid = str(session_id)
        state = self._sessions.get(sid)
        if state is None:
            raise VoiceReceiveUnavailable(
                "Live captions are not running for this Community Hub session."
            )
        uid = int(user_id)
        if state.bridge.is_opted_in(uid):
            state.bridge.opt_out(uid)
            return False
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
                await destination.send(
                    "📝 **Community Hub Live Captions stopped.** Speaker consent was cleared.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass

        voice_client = state.voice_client
        try:
            if getattr(voice_client, "is_connected", lambda: False)():
                await voice_client.disconnect(force=False)
        except Exception:
            log.exception("Community Hub caption voice disconnect failed session=%s", sid)
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
    "ensure_community_voice_caption_manager",
]
