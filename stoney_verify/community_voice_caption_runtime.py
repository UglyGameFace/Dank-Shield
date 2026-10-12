from __future__ import annotations

"""Single-owner runtime for Dank Shield live voice captions.

Community Hub sessions and general server voice channels share this same
receiver owner. One caption receiver may own a guild voice connection at a
time. A member can explicitly remember auto-caption consent and a language hint
per server; those preferences persist, while raw audio and live audio buffers
never do. Audio is never persisted by Dank Shield.
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import discord

from .community_voice_captions import (
    CaptionEngine,
    gemini_live_transcriber_from_env,
    gemini_text_translator_from_env,
    normalize_caption_output_mode,
)
from .voice_safety_policy import VoiceSafetyAdvisor
from .voice_safety_consent import read_voice_safety_agreement, save_voice_safety_agreement
from .community_voice_receive import (
    PerSpeakerFrameBridge,
    VoiceReceiveUnavailable,
    connect_receive_client,
    disconnect_receive_client,
    voice_receive_capability,
    voice_receive_connection_diagnostics,
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
    advisor: Optional[VoiceSafetyAdvisor] = None
    notice_channel_id: int = 0
    safety_consents: set[int] = field(default_factory=set)
    safety_consent_epochs: dict[int, int] = field(default_factory=dict)
    safety_automatic: bool = False
    notice_message_id: int = 0
    soak_test: bool = False
    receive_recoveries: int = 0
    receive_recovery_failures: int = 0
    last_receive_recovery_reason: str = ""
    last_receive_recovery_at: float = 0.0
    receive_recovery_window: list[float] = field(default_factory=list)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, str(default))).strip())
    except (TypeError, ValueError):
        value = int(default)
    return max(int(minimum), min(int(maximum), value))


SERVER_CAPTION_SCOPE_PREFIX = "server:"
VOICE_SAFETY_SCOPE_PREFIX = "voice_safety:"


def voice_safety_scope_id(guild_id: int) -> str:
    gid = int(guild_id)
    if gid <= 0:
        raise ValueError("guild_id must be positive")
    return f"{VOICE_SAFETY_SCOPE_PREFIX}{gid}"


def voice_safety_host_checks() -> dict[str, bool]:
    """Operator-readable booleans. Never include secret values or imply attestation was verified."""
    affirmative = {"1", "true", "yes", "on"}
    flags = (
        "DANK_COMMUNITY_LIVE_CAPTIONS_ENABLED",
        "DANK_VOICE_SAFETY_ENABLED",
        "DANK_VOICE_SAFETY_GEMINI_PAID_CONFIRMED",
        "DANK_VOICE_SAFETY_ADULT_ONLY_CONFIRMED",
    )
    checks = {
        name: os.getenv(name, "").strip().lower() in affirmative
        for name in flags
    }
    # A configured key is not proof of valid credentials, billing or model access.
    checks["GEMINI_API_KEY"] = bool(os.getenv("GEMINI_API_KEY", "").strip())
    return checks


def voice_safety_host_enabled() -> bool:
    # The Gemini billing/adult-only values are explicit operator attestations,
    # not checks against Google's billing or user age systems.
    checks = voice_safety_host_checks()
    return all(checks.get(key, False) for key in (
        "DANK_VOICE_SAFETY_ENABLED",
        "DANK_VOICE_SAFETY_GEMINI_PAID_CONFIRMED",
        "DANK_VOICE_SAFETY_ADULT_ONLY_CONFIRMED",
    ))



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


def live_captions_start_allowed(*, scope_kind: str, soak_test: bool = False) -> bool:
    if live_captions_enabled():
        return True
    return bool(str(scope_kind or "").strip().lower() == "server" and soak_test)


class CommunityVoiceCaptionManager:
    def __init__(self, bot: Any) -> None:
        self.bot = bot
        self._sessions: dict[str, CaptionRuntimeState] = {}
        self._guild_owner: dict[int, str] = {}
        # Durable preferences are cached by (guild_id, user_id). Empty language
        # means Gemini automatic multilingual detection.
        self._user_language_hints: dict[tuple[int, int], str] = {}
        self._auto_opt_in: dict[tuple[int, int], bool] = {}
        self._receive_probe_tasks: dict[tuple[str, int], asyncio.Task[Any]] = {}
        self._receive_recovery_locks: dict[str, asyncio.Lock] = {}
        self._voice_safety_start_locks: dict[int, asyncio.Lock] = {}
        self._voice_safety_retry_after: dict[int, float] = {}
        self._voice_safety_staff_paused: set[int] = set()
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
        opted_ids = list(state.bridge.opted_in_user_ids())
        speaker_language_hints: dict[str, str] = {}
        speaker_audio_rms_dbfs: dict[str, float] = {}
        speaker_detected_languages: dict[str, str] = {}
        for uid in opted_ids:
            hint = self.user_language_hint(state.guild_id, uid)
            if hint:
                speaker_language_hints[str(uid)] = hint
            try:
                level = state.engine.transcriber.last_audio_rms_dbfs(uid)
            except Exception:
                level = None
            if level is not None:
                speaker_audio_rms_dbfs[str(uid)] = float(level)
            try:
                detected = state.engine.transcriber.last_detected_language_code(uid)
            except Exception:
                detected = ""
            if detected:
                speaker_detected_languages[str(uid)] = detected

        return {
            "active": True,
            "capability": capability,
            "session_id": state.session_id,
            "scope_kind": state.scope_kind,
            "soak_test": bool(state.soak_test),
            "guild_id": state.guild_id,
            "voice_channel_id": state.voice_channel_id,
            "destination_channel_id": state.destination_channel_id,
            "opted_in_user_ids": opted_ids,
            "voice_safety_notice_message_id": int(state.notice_message_id),
            "voice_safety_alerts": int(state.advisor.alerts) if state.advisor else 0,
            "voice_safety_requests": int(state.advisor.requests) if state.advisor else 0,
            "speaker_language_hints": speaker_language_hints,
            "speaker_audio_rms_dbfs": speaker_audio_rms_dbfs,
            "speaker_detected_languages": speaker_detected_languages,
            "health": state.bridge.health.snapshot(),
            "segments_transcribed": state.engine.segments_transcribed,
            "segments_published": state.engine.segments_published,
            "segments_empty": state.engine.segments_empty,
            "segments_unclear": state.engine.segments_unclear,
            "queue_overflow": state.engine.queue_overflow,
            "segment_failures": state.engine.segment_failures,
            "provider_skipped": state.engine.provider_skipped,
            "provider_fallbacks": int(getattr(state.engine.transcriber, "fallback_count", 0) or 0),
            "provider_live_connections": int(getattr(state.engine.transcriber, "live_connections", 0) or 0),
            "provider_live_reconnects": int(getattr(state.engine.transcriber, "live_reconnects", 0) or 0),
            "provider_audio_input_frames": int(getattr(state.engine.transcriber, "audio_input_frames", 0) or 0),
            "provider_audio_chunks_sent": int(getattr(state.engine.transcriber, "audio_chunks_sent", 0) or 0),
            "provider_audio_bytes_sent": int(getattr(state.engine.transcriber, "audio_bytes_sent", 0) or 0),
            "provider_activity_starts": int(getattr(state.engine.transcriber, "activity_starts", 0) or 0),
            "provider_activity_ends": int(getattr(state.engine.transcriber, "activity_ends", 0) or 0),
            "provider_interim_events": int(getattr(state.engine.transcriber, "interim_transcript_events", 0) or 0),
            "provider_final_events": int(getattr(state.engine.transcriber, "final_transcript_events", 0) or 0),
            "provider_interrupted_events": int(getattr(state.engine.transcriber, "interrupted_server_events", 0) or 0),
            "provider_interim_timeout_fallbacks": int(getattr(state.engine.transcriber, "interim_timeout_fallbacks", 0) or 0),
            "receive_recoveries": int(state.receive_recoveries),
            "receive_recovery_failures": int(state.receive_recovery_failures),
            "last_receive_recovery_reason": str(state.last_receive_recovery_reason or ""),
            "last_receive_recovery_age_seconds": (
                max(0.0, time.monotonic() - float(state.last_receive_recovery_at))
                if state.last_receive_recovery_at
                else None
            ),
            "language_hint_mismatches": int(getattr(state.engine.transcriber, "language_hint_mismatches", 0) or 0),
            "language_codes": list(getattr(state.engine.transcriber, "language_codes", []) or []),
            "output_mode": str(getattr(state.engine, "output_mode", "original") or "original"),
            "translation_requests": int(getattr(getattr(state.engine, "translator", None), "requests", 0) or 0),
            "translation_cache_hits": int(getattr(getattr(state.engine, "translator", None), "cache_hits", 0) or 0),
            "translation_failures": int(getattr(getattr(state.engine, "translator", None), "failures", 0) or 0),
            "translation_skipped": int(getattr(getattr(state.engine, "translator", None), "skipped", 0) or 0),
            "translation_blocked_reason": str(getattr(getattr(state.engine, "translator", None), "blocked_reason", "") or ""),
            "provider_blocked_reason": str(state.engine.provider_blocked_reason or ""),
            "provider_blocked_code": str(state.engine.provider_blocked_code or ""),
            "queue_depth": state.engine.queue.qsize(),
            "last_failure": str(state.engine.last_failure or ""),
            "receive_connection": voice_receive_connection_diagnostics(state.voice_client),
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

    @staticmethod
    def _preference_key(guild_id: int, user_id: int) -> tuple[int, int]:
        return (int(guild_id), int(user_id))

    async def preferences_for_user(
        self,
        guild_id: int,
        user_id: int,
        *,
        refresh: bool = False,
    ) -> dict[str, Any]:
        gid = int(guild_id)
        uid = int(user_id)
        key = self._preference_key(gid, uid)

        from .profile_card_service import get_live_caption_preferences

        try:
            prefs = await get_live_caption_preferences(
                gid,
                uid,
                refresh=refresh,
            )
        except Exception as exc:
            log.warning(
                "Live Captions preference read failed guild=%s user=%s error=%s",
                gid,
                uid,
                type(exc).__name__,
            )
            return {
                "auto_opt_in": bool(self._auto_opt_in.get(key, False)),
                "language_code": str(
                    self._user_language_hints.get(key, "") or ""
                ),
                "storage_available": False,
            }

        auto_opt_in = bool(prefs.get("auto_opt_in", False))
        language_code = str(prefs.get("language_code") or "").strip()[:35]
        self._auto_opt_in[key] = auto_opt_in
        if language_code:
            self._user_language_hints[key] = language_code
        else:
            self._user_language_hints.pop(key, None)
        return {
            "auto_opt_in": auto_opt_in,
            "language_code": language_code,
            "storage_available": True,
        }

    async def set_voice_safety_consent(self, guild_id: int, user_id: int, enabled: bool, *, source: str = "voice_safety_notice_v1") -> bool:
        """Durable one-time choice independent of live session availability.

        Withdraw admission immediately even if persistence is unavailable.
        """
        gid, uid = int(guild_id), int(user_id)
        sid = self._guild_owner.get(gid)
        state = self._sessions.get(sid) if sid else None
        if state is not None and state.scope_kind != "voice_safety":
            state = None
        if not enabled and state is not None:
            state.safety_consent_epochs[uid] = state.safety_consent_epochs.get(uid, 0) + 1
            state.safety_consents.discard(uid)
            await self._disable_user_for_state(state, uid)
        try:
            saved = await save_voice_safety_agreement(gid, uid, enabled, source=source)
        except Exception as exc:
            if state is not None:
                if enabled:
                    state.safety_consents.discard(uid)
                    await self._disable_user_for_state(state, uid)
                # Opt-out already revoked audio before the attempted write.
                # Fail closed for the entire guild when revocation cannot persist.
                if not enabled:
                    await self.stop(state.session_id)
            raise VoiceReceiveUnavailable(
                "Could not confirm the voice moderation agreement in storage. "
                "No audio was admitted. If opting out, monitoring was stopped for safety."
            ) from exc
        if not saved:
            return False
        if state is None:
            return True
        guild = self.bot.get_guild(gid)
        member = guild.get_member(uid) if guild is not None else None
        in_channel = int(getattr(getattr(getattr(member, "voice", None), "channel", None), "id", 0) or 0) == int(state.voice_channel_id)
        if in_channel:
            await self._enable_user_for_state(state, uid, refresh_preferences=True)
        return True

    def user_language_hint(self, guild_id: int, user_id: int) -> str:
        key = self._preference_key(guild_id, user_id)
        return str(self._user_language_hints.get(key, "") or "")

    def auto_caption_cached(self, guild_id: int, user_id: int) -> bool:
        return bool(
            self._auto_opt_in.get(
                self._preference_key(guild_id, user_id),
                False,
            )
        )

    async def _write_preferences(
        self,
        guild_id: int,
        user_id: int,
        *,
        auto_opt_in: Optional[bool] = None,
        language_code: Optional[str] = None,
    ) -> dict[str, Any]:
        gid = int(guild_id)
        uid = int(user_id)
        from .profile_card_service import upsert_live_caption_preferences

        try:
            prefs = await upsert_live_caption_preferences(
                gid,
                uid,
                auto_opt_in=auto_opt_in,
                language_code=language_code,
            )
        except Exception as exc:
            log.exception(
                "Live Captions preference write failed guild=%s user=%s",
                gid,
                uid,
            )
            raise VoiceReceiveUnavailable(
                "Dank Shield could not save your Live Captions preference right now. Your existing preference was left unchanged."
            ) from exc

        key = self._preference_key(gid, uid)
        remembered = bool(prefs.get("auto_opt_in", False))
        code = str(prefs.get("language_code") or "").strip()[:35]
        self._auto_opt_in[key] = remembered
        if code:
            self._user_language_hints[key] = code
        else:
            self._user_language_hints.pop(key, None)
        return {
            "auto_opt_in": remembered,
            "language_code": code,
            "storage_available": True,
        }

    async def set_user_language_hint(
        self,
        guild_id: int,
        user_id: int,
        language_code: str,
    ) -> str:
        gid = int(guild_id)
        uid = int(user_id)
        code = str(language_code or "").strip()[:35]
        await self._write_preferences(
            gid,
            uid,
            language_code=code,
        )

        # Gemini configuration is fixed at WebSocket setup. Reconnect only this
        # speaker, and prewarm the replacement if they are currently opted in.
        for state in tuple(self._sessions.values()):
            if int(state.guild_id) != gid:
                continue
            setter = getattr(
                state.engine.transcriber,
                "set_user_language_codes",
                None,
            )
            if callable(setter):
                await setter(uid, [code] if code else [])
            if state.bridge.is_opted_in(uid):
                prepare = getattr(
                    state.engine.transcriber,
                    "prepare_user",
                    None,
                )
                if callable(prepare):
                    await prepare(uid)
        return code

    async def _enable_user_for_state(
        self,
        state: CaptionRuntimeState,
        user_id: int,
        *,
        refresh_preferences: bool = False,
    ) -> None:
        uid = int(user_id)
        if state.bridge.is_opted_in(uid):
            return
        safety = getattr(state, "scope_kind", "server") == "voice_safety"
        epoch = state.safety_consent_epochs.get(uid, 0) if safety else 0
        if safety and not await read_voice_safety_agreement(state.guild_id, uid):
            return
        if len(state.bridge.opted_in_user_ids()) >= self.max_speakers_per_session:
            raise VoiceReceiveUnavailable(
                "This session has reached its configured Live Captions speaker limit."
            )

        prefs = await self.preferences_for_user(
            state.guild_id,
            uid,
            refresh=refresh_preferences,
        )
        hint = str(prefs.get("language_code") or "")
        setter = getattr(
            state.engine.transcriber,
            "set_user_language_codes",
            None,
        )
        if callable(setter):
            await setter(uid, [hint] if hint else [])

        # Establish Gemini before admitting the speaker's PCM. That prevents the
        # first speech burst from queueing behind a WebSocket handshake.
        prepare = getattr(
            state.engine.transcriber,
            "prepare_user",
            None,
        )
        if callable(prepare):
            await prepare(uid)

        if safety:
            # A user may withdraw consent, leave or stop during Gemini
            # prewarm. Only the current live owner with fresh agreement
            # may admit PCM after that await.
            guild = self.bot.get_guild(state.guild_id)
            member = guild.get_member(uid) if guild is not None else None
            in_target = int(getattr(getattr(getattr(member, "voice", None), "channel", None), "id", 0) or 0) == state.voice_channel_id
            if (
                self._sessions.get(state.session_id) is not state
                or epoch != state.safety_consent_epochs.get(uid, 0)
                or not in_target
                or not await read_voice_safety_agreement(state.guild_id, uid)
            ):
                return
        state.engine.allow_user(uid)
        state.bridge.opt_in(uid)
        if safety:
            state.safety_consents.add(uid)

    async def _disable_user_for_state(
        self,
        state: CaptionRuntimeState,
        user_id: int,
    ) -> None:
        uid = int(user_id)
        if not state.bridge.is_opted_in(uid):
            return
        state.bridge.opt_out(uid)
        if getattr(state, "advisor", None) is not None:
            state.advisor.revoke(uid)
        await state.engine.revoke_user(uid)

    async def set_auto_caption_preference(
        self,
        guild_id: int,
        user_id: int,
        enabled: bool,
    ) -> bool:
        gid = int(guild_id)
        uid = int(user_id)
        enabled = bool(enabled)
        await self._write_preferences(
            gid,
            uid,
            auto_opt_in=enabled,
        )

        sid = self._guild_owner.get(gid)
        state = self._sessions.get(sid) if sid else None
        if state is None or getattr(state, "scope_kind", "server") == "voice_safety":
            return enabled

        guild = self.bot.get_guild(gid)
        member = guild.get_member(uid) if guild is not None else None
        channel = getattr(getattr(member, "voice", None), "channel", None)
        in_target = bool(
            channel is not None
            and int(getattr(channel, "id", 0) or 0)
            == int(state.voice_channel_id)
        )

        if enabled and in_target:
            try:
                await self._enable_user_for_state(
                    state,
                    uid,
                    refresh_preferences=True,
                )
            except Exception as exc:
                raise VoiceReceiveUnavailable(
                    "Your auto-caption preference was saved, but the current Gemini Live speaker session could not be prepared yet. It will retry the next time captions start or you rejoin the captioned voice channel."
                ) from exc
        elif not enabled:
            await self._disable_user_for_state(state, uid)
        return enabled

    async def _restore_auto_opt_ins(
        self,
        state: CaptionRuntimeState,
        voice_channel: discord.VoiceChannel,
    ) -> int:
        restored = 0
        for member in tuple(getattr(voice_channel, "members", ()) or ()):
            if getattr(member, "bot", False):
                continue
            if restored >= self.max_speakers_per_session:
                break
            if getattr(state, "scope_kind", "server") == "voice_safety":
                consented = await read_voice_safety_agreement(state.guild_id, int(member.id))
            else:
                prefs = await self.preferences_for_user(state.guild_id, int(member.id), refresh=True)
                consented = bool(prefs.get("auto_opt_in"))
            if not consented:
                continue
            try:
                await self._enable_user_for_state(
                    state,
                    int(member.id),
                    refresh_preferences=False,
                )
            except Exception:
                log.exception(
                    "Live Captions could not restore remembered auto-caption guild=%s user=%s",
                    state.guild_id,
                    member.id,
                )
                continue
            restored += 1
        return restored

    def pause_voice_safety_auto_start(self, guild_id: int, paused: bool = True) -> None:
        gid = int(guild_id)
        if paused:
            self._voice_safety_staff_paused.add(gid)
        else:
            self._voice_safety_staff_paused.discard(gid)

    async def maybe_auto_start_voice_safety(
        self, guild: discord.Guild, *, joined_voice_id: int = 0
    ) -> bool:
        """Start one configured VC only when populated; never add a receiver.

        Startup and join events use the same gate and per-guild lock. Discord
        connection setup is not instantaneous; monitoring never covers speech
        before the receiver is ready.
        """
        if not (voice_safety_host_enabled() and live_captions_enabled()):
            return False
        gid = int(guild.id)
        if gid in self._voice_safety_staff_paused or gid in self._guild_owner:
            return False
        now = time.monotonic()
        if now < self._voice_safety_retry_after.get(gid, 0):
            return False
        lock = self._voice_safety_start_locks.setdefault(gid, asyncio.Lock())
        async with lock:
            if gid in self._guild_owner or gid in self._voice_safety_staff_paused:
                return False
            if len(self._sessions) >= self.max_active_guilds:
                return False
            from .guild_config import get_guild_config

            try:
                cfg = await get_guild_config(gid)
                if not bool(cfg.get("voice_safety_enabled", False)):
                    return False
                voice_id = int(cfg.get("voice_safety_voice_channel_id") or 0)
                if voice_id <= 0 or (joined_voice_id and int(joined_voice_id) != voice_id):
                    return False
                voice = guild.get_channel(voice_id)
                if not isinstance(voice, discord.VoiceChannel):
                    return False
                if not any(not getattr(m, "bot", False) for m in tuple(voice.members or ())):
                    return False
                state = await self.start_voice_safety(
                    guild_id=gid,
                    voice_channel_id=voice_id,
                    staff_alert_channel_id=int(cfg.get("voice_safety_staff_channel_id") or 0),
                    notice_channel_id=int(cfg.get("voice_safety_notice_channel_id") or 0),
                    automatic=True,
                )
                return bool(state)
            except Exception as exc:
                self._voice_safety_retry_after[gid] = time.monotonic() + 45.0
                log.warning(
                    "Voice Safety auto-start unavailable guild=%s type=%s",
                    gid, type(exc).__name__,
                )
                return False

    async def restore_voice_safety_after_ready(self) -> None:
        """Recover previously armed, currently occupied voice rooms after restart.

        Runs as an asynchronous on_ready listener and stops when the process's
        configured active receiver capacity is filled.
        """
        if not (voice_safety_host_enabled() and live_captions_enabled()):
            return
        for guild in tuple(getattr(self.bot, "guilds", ()) or ()):
            if len(self._sessions) >= self.max_active_guilds:
                break
            voice_channels = getattr(guild, "voice_channels", ()) or ()
            if not any(
                any(not getattr(m, "bot", False) for m in (getattr(vc, "members", ()) or ()))
                for vc in voice_channels
            ):
                continue
            await self.maybe_auto_start_voice_safety(guild)
            await asyncio.sleep(0)

    async def handle_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if getattr(member, "bot", False):
            return
        gid = int(member.guild.id)
        sid = self._guild_owner.get(gid)
        state = self._sessions.get(sid) if sid else None
        if state is None:
            before_join = int(getattr(getattr(before, "channel", None), "id", 0) or 0)
            after_join = int(getattr(getattr(after, "channel", None), "id", 0) or 0)
            if after_join and before_join != after_join:
                await self.maybe_auto_start_voice_safety(member.guild, joined_voice_id=after_join)
            return

        uid = int(member.id)
        before_id = int(
            getattr(getattr(before, "channel", None), "id", 0) or 0
        )
        after_id = int(
            getattr(getattr(after, "channel", None), "id", 0) or 0
        )
        target = int(state.voice_channel_id)

        if before_id == target and after_id != target:
            # Active audio admission ends on departure; stored agreement persists until withdrawn.
            if getattr(state, "scope_kind", "server") == "voice_safety":
                state.safety_consent_epochs[uid] = state.safety_consent_epochs.get(uid, 0) + 1
                state.safety_consents.discard(uid)
            await self._disable_user_for_state(state, uid)
            if getattr(state, "scope_kind", "server") == "voice_safety" and bool(getattr(state, "safety_automatic", False)):
                voice = member.guild.get_channel(target)
                if voice is None or not any(
                    not getattr(m, "bot", False) and int(m.id) != uid
                    for m in tuple(getattr(voice, "members", ()) or ())
                ):
                    await self.stop(state.session_id)
            return

        if after_id != target or before_id == target:
            return

        if getattr(state, "scope_kind", "server") == "voice_safety":
            consented = await read_voice_safety_agreement(gid, uid)
        else:
            prefs = await self.preferences_for_user(gid, uid, refresh=True)
            consented = bool(prefs.get("auto_opt_in"))
        if not consented:
            return
        try:
            await self._enable_user_for_state(
                state,
                uid,
                refresh_preferences=False,
            )
        except Exception:
            log.exception(
                "Live Captions automatic voice-join opt-in failed guild=%s user=%s",
                gid,
                uid,
            )


    def _schedule_receive_probe(self, session_id: str, user_id: int) -> None:
        sid = str(session_id)
        uid = int(user_id)
        key = (sid, uid)

        existing = self._receive_probe_tasks.get(key)
        if existing is not None and not existing.done():
            return

        state = self._sessions.get(sid)
        if state is None or not state.bridge.is_opted_in(uid):
            return

        baseline = state.bridge.health.snapshot()
        expected_voice_client = state.voice_client
        task = asyncio.create_task(
            self._verify_receive_after_speaking(
                sid,
                uid,
                expected_voice_client,
                baseline,
            ),
            name=f"live-captions-receive-probe:{sid}:{uid}",
        )
        self._receive_probe_tasks[key] = task

        def _done(done: asyncio.Task[Any]) -> None:
            if self._receive_probe_tasks.get(key) is done:
                self._receive_probe_tasks.pop(key, None)
            try:
                done.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception(
                    "Live Captions receive probe failed session=%s user=%s",
                    sid,
                    uid,
                )

        task.add_done_callback(_done)

    async def _verify_receive_after_speaking(
        self,
        session_id: str,
        user_id: int,
        expected_voice_client: Any,
        baseline: dict[str, int],
    ) -> None:
        # A real Discord speech burst produces many 20ms RTP/PCM frames. Give
        # the media path enough time to advance before deciding it is stalled.
        await asyncio.sleep(2.5)

        sid = str(session_id)
        uid = int(user_id)
        state = self._sessions.get(sid)
        if (
            state is None
            or state.voice_client is not expected_voice_client
            or not state.bridge.is_opted_in(uid)
        ):
            return

        current = state.bridge.health.snapshot()
        frames_delta = int(current.get("frames_seen") or 0) - int(
            baseline.get("frames_seen") or 0
        )
        if frames_delta > 0:
            return

        connection = voice_receive_connection_diagnostics(state.voice_client)
        if not bool(connection.get("reader_listening")):
            reason = "Discord signaled an opted-in speaker, but the receive reader stopped before PCM arrived."
        elif not bool(connection.get("dave_session_ready")):
            # An MLS/DAVE transition can legitimately pause media briefly. Let
            # the next speaking signal re-check instead of forcing a reconnect
            # while the session is still negotiating.
            return
        elif int(connection.get("mapped_ssrcs") or 0) <= 0:
            return
        else:
            raw_delta = int(current.get("raw_udp_packets") or 0) - int(
                baseline.get("raw_udp_packets") or 0
            )
            reason = (
                "Discord signaled an opted-in speaker but no PCM arrived "
                f"after 2.5s (UDP delta={max(0, raw_delta)})."
            )

        await self._recover_receive_transport(
            sid,
            expected_voice_client=expected_voice_client,
            reason=reason,
        )

    async def _recover_receive_transport(
        self,
        session_id: str,
        *,
        expected_voice_client: Any,
        reason: str,
    ) -> bool:
        sid = str(session_id)
        lock = self._receive_recovery_locks.setdefault(sid, asyncio.Lock())

        async with lock:
            state = self._sessions.get(sid)
            if state is None or state.voice_client is not expected_voice_client:
                return False

            now = time.monotonic()
            if (
                state.last_receive_recovery_at
                and now - state.last_receive_recovery_at < 20.0
            ):
                return False

            state.receive_recovery_window[:] = [
                stamp
                for stamp in state.receive_recovery_window
                if now - float(stamp) < 60.0
            ]
            if len(state.receive_recovery_window) >= 2:
                state.last_receive_recovery_reason = (
                    "Automatic receive recovery is rate-limited after two attempts in one minute."
                )
                return False
            state.receive_recovery_window.append(now)

            guild = self.bot.get_guild(state.guild_id)
            voice_channel = (
                guild.get_channel(state.voice_channel_id)
                if guild is not None
                else None
            )
            if not isinstance(voice_channel, discord.VoiceChannel):
                state.receive_recovery_failures += 1
                state.last_receive_recovery_reason = (
                    "Receive recovery could not resolve the configured voice channel."
                )
                return False

            opted_users = tuple(state.bridge.opted_in_user_ids())
            old_voice_client = state.voice_client
            old_bridge = state.bridge
            old_bridge.set_speaking_callback(None)

            log.warning(
                "Live Captions receive transport stalled; rebuilding voice receive session=%s guild=%s channel=%s reason=%s",
                sid,
                state.guild_id,
                state.voice_channel_id,
                reason,
            )

            try:
                disconnect_receive_client(old_voice_client)
                try:
                    if getattr(old_voice_client, "is_connected", lambda: False)():
                        await old_voice_client.disconnect(force=True)
                except Exception:
                    log.warning(
                        "Live Captions old voice transport disconnect failed during recovery session=%s",
                        sid,
                        exc_info=True,
                    )

                loop = asyncio.get_running_loop()
                new_bridge = PerSpeakerFrameBridge(loop, state.engine.submit)
                for uid in opted_users:
                    if state.scope_kind == "voice_safety" and uid not in state.safety_consents:
                        continue
                    new_bridge.opt_in(uid)
                new_bridge.set_speaking_callback(
                    lambda uid, _sid=sid: self._schedule_receive_probe(_sid, uid)
                )

                new_voice_client = await connect_receive_client(
                    voice_channel,
                    new_bridge,
                )
            except Exception as exc:
                state.receive_recovery_failures += 1
                state.last_receive_recovery_at = time.monotonic()
                state.last_receive_recovery_reason = (
                    f"Receive transport recovery failed: {type(exc).__name__}: {str(exc)[:180]}"
                )
                log.exception(
                    "Live Captions receive transport recovery failed session=%s",
                    sid,
                )
                return False

            # Stop, departure, or consent revocation may have run during await
            # connect_receive_client(). Never resurrect a stopped session.
            if self._sessions.get(sid) is not state:
                disconnect_receive_client(new_voice_client)
                try:
                    await new_voice_client.disconnect(force=True)
                except Exception:
                    pass
                return False
            if state.scope_kind == "voice_safety":
                guild_now = self.bot.get_guild(state.guild_id)
                for uid in tuple(new_bridge.opted_in_user_ids()):
                    member = guild_now.get_member(uid) if guild_now is not None else None
                    still_in_vc = int(getattr(getattr(getattr(member, "voice", None), "channel", None), "id", 0) or 0) == state.voice_channel_id
                    if uid not in state.safety_consents or not still_in_vc:
                        new_bridge.opt_out(uid)
                # Preserve new explicit opt-ins that happened during reconnect.
                for uid in tuple(state.safety_consents):
                    member = guild_now.get_member(uid) if guild_now is not None else None
                    in_target = int(getattr(getattr(getattr(member, "voice", None), "channel", None), "id", 0) or 0) == state.voice_channel_id
                    if in_target:
                        new_bridge.opt_in(uid)

            state.bridge = new_bridge
            state.voice_client = new_voice_client
            state.receive_recoveries += 1
            state.last_receive_recovery_at = time.monotonic()
            state.last_receive_recovery_reason = reason
            log.warning(
                "Live Captions receive transport recovered session=%s guild=%s recoveries=%s",
                sid,
                state.guild_id,
                state.receive_recoveries,
            )
            return True

    def _cancel_receive_probes(self, session_id: str) -> None:
        sid = str(session_id)
        for key, task in tuple(self._receive_probe_tasks.items()):
            if key[0] != sid:
                continue
            self._receive_probe_tasks.pop(key, None)
            if not task.done():
                task.cancel()

    async def start_server(
        self,
        *,
        guild_id: int,
        voice_channel_id: int,
        destination_channel_id: int,
        soak_test: bool = False,
    ) -> CaptionRuntimeState:
        gid = int(guild_id)
        return await self.start(
            {
                "id": server_caption_scope_id(gid),
                "guild_id": gid,
                "voice_channel_id": int(voice_channel_id),
                "panel_channel_id": int(destination_channel_id),
                "caption_scope": "server",
                "soak_test": bool(soak_test),
            }
        )

    async def start_voice_safety(
        self,
        *,
        guild_id: int,
        voice_channel_id: int,
        staff_alert_channel_id: int,
        notice_channel_id: int,
        automatic: bool = False,
    ) -> CaptionRuntimeState:
        return await self.start({
            "id": voice_safety_scope_id(guild_id),
            "guild_id": int(guild_id),
            "voice_channel_id": int(voice_channel_id),
            "panel_channel_id": int(staff_alert_channel_id),
            "notice_channel_id": int(notice_channel_id),
            "caption_scope": "voice_safety",
            "voice_safety_automatic": bool(automatic),
        })

    async def start(self, session: dict[str, Any]) -> CaptionRuntimeState:
        sid = str(session.get("id") or "").strip()
        guild_id = int(session.get("guild_id") or 0)
        voice_channel_id = int(session.get("voice_channel_id") or 0)
        destination_channel_id = int(
            session.get("thread_id")
            or session.get("panel_channel_id")
            or 0
        )
        requested_scope = str(session.get("caption_scope") or "").strip().lower()
        scope_kind = requested_scope if requested_scope in {"server", "voice_safety"} else "community_hub"
        safety_mode = scope_kind == "voice_safety"
        requested_soak = bool(session.get("soak_test"))
        soak_test = bool(
            not live_captions_enabled()
            and live_captions_start_allowed(scope_kind=scope_kind, soak_test=requested_soak)
        )
        scope_label = (
            "Dank Shield Live Captions DAVE Soak Test"
            if soak_test
            else "Dank Shield Live Captions"
            if scope_kind == "server"
            else "Community Hub Live Captions"
        )
        if safety_mode and (not voice_safety_host_enabled() or not live_captions_enabled()):
            raise VoiceReceiveUnavailable("Voice Safety is disabled until this host passes the existing DAVE receive soak and is explicitly enabled.")
        if not live_captions_start_allowed(scope_kind=scope_kind, soak_test=requested_soak):
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
            raise VoiceReceiveUnavailable("A staff alert / Live Captions destination is required.")
        notice_channel_id = int(session.get("notice_channel_id") or 0) if safety_mode else 0
        if safety_mode and notice_channel_id <= 0:
            raise VoiceReceiveUnavailable("Voice Safety requires a public notice channel.")
        if not os.getenv("GEMINI_API_KEY", "").strip():
            raise VoiceReceiveUnavailable(
                "Live Captions need GEMINI_API_KEY configured on the Dank Shield host."
            )

        # One language/output policy per guild is shared by ordinary-server and
        # Community Hub captions. Routing remains session-specific.
        from .guild_config import get_guild_config

        caption_cfg = await get_guild_config(guild_id, refresh=True)
        if safety_mode:
            if not bool(caption_cfg.get("voice_safety_enabled", False)):
                raise VoiceReceiveUnavailable("Voice Safety has not been enabled by this server.")
            for key, actual in (("voice_safety_voice_channel_id", voice_channel_id), ("voice_safety_staff_channel_id", destination_channel_id), ("voice_safety_notice_channel_id", notice_channel_id)):
                if int(caption_cfg.get(key) or 0) != int(actual):
                    raise VoiceReceiveUnavailable("Voice Safety channel configuration changed; refresh setup.")
        output_mode = "original" if safety_mode else normalize_caption_output_mode(
            caption_cfg.get("live_captions_output_mode", "original")
        )
        language_codes = caption_cfg.get("live_captions_language_codes") or []

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

            notice_channel = guild.get_channel(notice_channel_id) if safety_mode else None
            if safety_mode:
                if not isinstance(destination, discord.TextChannel) or not isinstance(notice_channel, discord.TextChannel):
                    raise VoiceReceiveUnavailable("Voice Safety channels must be existing text channels.")
                if destination.permissions_for(guild.default_role).view_channel:
                    raise VoiceReceiveUnavailable("The staff alert channel is visible to everyone. Choose a private staff channel.")
                if not notice_channel.permissions_for(guild.default_role).view_channel:
                    raise VoiceReceiveUnavailable("The Voice Safety notice must be visible to @everyone.")
                me = guild.me
                for channel in (destination, notice_channel):
                    perms = channel.permissions_for(me) if me is not None else None
                    if perms is None or not (perms.view_channel and perms.send_messages):
                        raise VoiceReceiveUnavailable("Dank Shield needs View Channel and Send Messages in both Voice Safety channels.")

            transcriber = gemini_live_transcriber_from_env(
                language_codes=language_codes,
            )
            translator = (
                gemini_text_translator_from_env()
                if output_mode in {"english", "bilingual"}
                else None
            )
            advisor = VoiceSafetyAdvisor() if safety_mode else None

            async def _publish(user_id: int, text: str, confidence: Optional[float]) -> None:
                if advisor is not None:
                    uid = int(user_id)
                    verdict = await advisor.inspect(
                        uid, text, confidence,
                        is_admitted=lambda value: state.bridge.is_opted_in(value)
                        and bool(self._sessions.get(sid) is state),
                    )
                    if verdict is None:
                        return
                    # A consent withdrawal or a stopped session must never publish a stale result.
                    if not state.bridge.is_opted_in(uid) or self._sessions.get(sid) is not state:
                        return
                    safe_reason = discord.utils.escape_markdown(verdict.reason)[:220]
                    await destination.send(
                        f"🛡️ **Voice Safety · staff review only**\n"
                        f"Member: <@{uid}> (`{uid}`) · VC: <#{voice_channel_id}>\n"
                        f"Category: `{verdict.category}` · Urgency: `{verdict.severity}`\n"
                        f"AI triage (unverified): {safe_reason}\n"
                        "No automatic disciplinary action was taken. Check context and speak with members.",
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    return
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
                source = (
                    f" · <#{voice_channel_id}>"
                    if scope_kind == "server"
                    else ""
                )
                await destination.send(
                    f"🎙️ **{safe_name}{source} · Live Caption:** {text}{suffix}",
                    allowed_mentions=discord.AllowedMentions.none(),
                )

            engine = CaptionEngine(
                transcriber,
                _publish,
                translator=translator,
                output_mode=output_mode,
                global_transcribe_semaphore=self._global_transcribe_semaphore,
            )
            loop = asyncio.get_running_loop()
            bridge = PerSpeakerFrameBridge(loop, engine.submit)
            voice_client = await connect_receive_client(voice_channel, bridge)

            try:
                if safety_mode:
                    from .commands_ext.public_voice_safety import VoiceSafetyConsentView

                    notice_message = await notice_channel.send(
                        f"🛡️ **Voice Safety review is active for <#{voice_channel_id}>.** "
                        "This is an optional AI-assisted staff-alert feature, not a guaranteed content block. "
                        "Members who accepted the disclosed Voice Safety agreement during verification or on this notice may be monitored automatically in the selected VC. "
                        "Consented audio is sent to Google Gemini, and transcript text is sent to Gemini "
                        "for conduct classification. Staff may see an incident notice. "
                        "This operator has confirmed the Gemini project uses paid no-training data terms and the monitored space is restricted to adults. "
                        "Dank Shield does not retain raw audio; moderation incidents remain in the private staff channel. "
                        "Press **Opt Out** at any time to revoke ongoing monitoring. Existing members may press **Agree Once** here.",
                        view=VoiceSafetyConsentView(),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                else:
                    source_line = (
                        f"Voice channel: {voice_channel.mention}\n"
                        if scope_kind == "server"
                        else ""
                    )
                    await destination.send(
                        f"📝 **{scope_label} started.**\n"
                        f"{source_line}"
                        "Dank Shield keeps each opted-in Discord speaker isolated before transcription. "
                        "Only members who explicitly choose **Caption My Voice** are transcribed. "
                        "Opted-in audio is sent to **Google Gemini's transcription API** for speech-to-text. "
                        "Gemini automatically detects its supported languages and can handle code-switching; this server may optionally display English translation of finalized text. "
                        "This deployment uses Gemini's **Free Tier**; Google states Free Tier submitted content may be used to improve its products. "
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
                soak_test=soak_test,
                advisor=advisor,
                notice_channel_id=notice_channel_id,
                notice_message_id=int(notice_message.id) if safety_mode else 0,
                safety_automatic=bool(session.get("voice_safety_automatic", False)) if safety_mode else False,
            )
            self._sessions[sid] = state
            self._guild_owner[guild_id] = sid
            bridge.set_speaking_callback(
                lambda uid, _sid=sid: self._schedule_receive_probe(_sid, uid)
            )
            engine.start()
            restored = await self._restore_auto_opt_ins(state, voice_channel)
            if restored:
                log.info(
                    "Live Captions restored remembered auto-caption speakers session=%s guild=%s restored=%s",
                    sid,
                    guild_id,
                    restored,
                )
            return state

    async def toggle_consent(self, session_id: str, user_id: int) -> bool:
        """Toggle remembered per-server auto-caption consent for one member.

        Turning it on persists explicit consent for this server and immediately
        admits the member when they are in the captioned VC. Turning it off
        persists the revocation and purges current buffered/in-flight audio.
        """

        sid = str(session_id)
        state = self._sessions.get(sid)
        if state is None:
            raise VoiceReceiveUnavailable(
                "Live Captions are not running for this session."
            )
        uid = int(user_id)
        prefs = await self.preferences_for_user(
            state.guild_id,
            uid,
            refresh=True,
        )
        currently_enabled = bool(prefs.get("auto_opt_in"))
        return await self.set_auto_caption_preference(
            state.guild_id,
            uid,
            not currently_enabled,
        )


    async def stop(self, session_id: str, *, announce: bool = True) -> bool:
        sid = str(session_id)
        async with self._lock:
            state = self._sessions.pop(sid, None)
            if state is None:
                return False
            if self._guild_owner.get(state.guild_id) == sid:
                self._guild_owner.pop(state.guild_id, None)

        self._cancel_receive_probes(sid)
        self._receive_recovery_locks.pop(sid, None)
        state.bridge.set_speaking_callback(None)
        state.bridge.clear_consent()
        state.safety_consents.clear()
        state.safety_consent_epochs.clear()
        if state.advisor is not None:
            state.advisor.close()
        try:
            await state.engine.close()
        finally:
            disconnect_receive_client(state.voice_client)

        guild = self.bot.get_guild(state.guild_id)
        if state.scope_kind == "voice_safety" and state.notice_message_id and guild is not None:
            # Do not leave an old public "active" notice behind after staff stop.
            notice = guild.get_channel(state.notice_channel_id)
            if isinstance(notice, discord.TextChannel):
                try:
                    from .commands_ext.public_voice_safety import VoiceSafetyEndedView

                    await notice.get_partial_message(state.notice_message_id).edit(
                        content="🛡️ **Voice Safety review ended.** No new audio is admitted. "
                        "The Opt Out button remains available; staff must start a new session and post a new notice to resume.",
                        view=VoiceSafetyEndedView(),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                    log.warning("Voice Safety notice could not be marked ended guild=%s", state.guild_id)
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
                    "Dank Shield Live Captions DAVE Soak Test"
                    if state.soak_test
                    else "Dank Shield Live Captions"
                    if state.scope_kind == "server"
                    else "Community Hub Live Captions"
                )
                source = (
                    f" Voice channel: <#{state.voice_channel_id}>."
                    if state.scope_kind == "server"
                    else ""
                )
                await destination.send(
                    ("🛡️ **Voice Safety stopped.** All active voice admissions cleared; no more speech is reviewed." if getattr(state, "scope_kind", "server") == "voice_safety" else f"📝 **{scope_label} stopped.**{source} Active audio admission and buffered caption audio were cleared. Members who enabled remembered auto-caption stay opted in for future sessions until they turn it off."),
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
    "voice_safety_scope_id",
    "voice_safety_host_enabled",
    "ensure_community_voice_caption_manager",
    "live_captions_enabled",
    "live_captions_start_allowed",
    "server_caption_scope_id",
]
