from __future__ import annotations

from datetime import timedelta
from typing import Any

import discord

from .guild_config import get_guild_config

VOICE_MAPPING_KEYS = (
    "vc_verify_channel_id",
    "voice_verify_channel_id",
    "voice_verification_channel_id",
)
VOICE_QUEUE_MAPPING_KEYS = (
    "vc_verify_queue_channel_id",
    "vc_queue_channel_id",
    "vc_request_channel_id",
    "vc_verify_requests_channel_id",
)
VOICE_MANAGED_KEY = "vc_verify_channel_managed_id"
VOICE_QUEUE_MANAGED_KEY = "vc_verify_queue_channel_managed_id"


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    for bucket in ("settings", "config", "metadata", "meta"):
        try:
            nested = cfg.get(bucket) if hasattr(cfg, "get") else getattr(cfg, bucket, None)
            if isinstance(nested, dict) and nested.get(key) is not None:
                return nested.get(key)
        except Exception:
            continue
    return default


def _safe_id(value: Any) -> int:
    try:
        return int(str(value or "0").strip() or 0)
    except Exception:
        return 0


def _ids_from_cfg(cfg: Any, keys: tuple[str, ...]) -> set[int]:
    return {
        parsed
        for key in keys
        if (parsed := _safe_id(_cfg_value(cfg, key, 0))) > 0
    }


def _is_voice_channel(channel: Any) -> bool:
    return bool(
        isinstance(channel, discord.VoiceChannel)
        or getattr(channel, "type", None) == discord.ChannelType.voice
    )


def _is_text_channel(channel: Any) -> bool:
    return bool(
        isinstance(channel, discord.TextChannel)
        or getattr(channel, "type", None) == discord.ChannelType.text
    )


async def _audit_proves_bot_created(
    guild: discord.Guild,
    channel: Any,
) -> bool:
    me = getattr(guild, "me", None)
    if me is None:
        return False
    try:
        if not bool(getattr(me.guild_permissions, "view_audit_log", False)):
            return False
    except Exception:
        return False

    created_at = getattr(channel, "created_at", None)
    kwargs: dict[str, Any] = {
        "limit": 20,
        "action": discord.AuditLogAction.channel_create,
    }
    if created_at is not None:
        try:
            kwargs["after"] = created_at - timedelta(minutes=5)
            kwargs["before"] = created_at + timedelta(minutes=5)
        except Exception:
            pass

    try:
        async for entry in guild.audit_logs(**kwargs):
            target = getattr(entry, "target", None)
            if _safe_id(getattr(target, "id", 0)) != _safe_id(getattr(channel, "id", 0)):
                continue
            user = getattr(entry, "user", None)
            return _safe_id(getattr(user, "id", 0)) == _safe_id(getattr(me, "id", 0))
    except Exception:
        return False
    return False


async def _text_channel_is_empty(channel: Any) -> bool:
    try:
        async for _message in channel.history(limit=1):
            return False
        return True
    except Exception:
        # If history cannot be inspected, preserve the channel rather than risk
        # deleting staff verification history.
        return False


def _add_note(notes: list[str], text: str) -> None:
    if text and text not in notes:
        notes.append(text)


async def reconcile_disabled_voice_verify(
    guild: discord.Guild,
    *,
    actor: Any = None,
) -> str:
    """Detach Voice Verify when OFF and remove only provably bot-owned defaults."""

    from .commands_ext import public_setup_defaults as defaults
    from .commands_ext.public_setup_config_writer import clear_guild_config_keys

    cfg = await get_guild_config(int(guild.id), refresh=True)
    mapped_voice_ids = _ids_from_cfg(cfg, VOICE_MAPPING_KEYS)
    mapped_queue_ids = _ids_from_cfg(cfg, VOICE_QUEUE_MAPPING_KEYS)
    managed_voice_id = _safe_id(_cfg_value(cfg, VOICE_MANAGED_KEY, 0))
    managed_queue_id = _safe_id(_cfg_value(cfg, VOICE_QUEUE_MANAGED_KEY, 0))

    candidate_voice_ids = set(mapped_voice_ids)
    candidate_queue_ids = set(mapped_queue_ids)
    if managed_voice_id > 0:
        candidate_voice_ids.add(managed_voice_id)
    if managed_queue_id > 0:
        candidate_queue_ids.add(managed_queue_id)

    if not candidate_voice_ids and not candidate_queue_ids:
        return ""

    notes: list[str] = []
    clear_keys = set(VOICE_MAPPING_KEYS) | set(VOICE_QUEUE_MAPPING_KEYS)

    if managed_voice_id > 0:
        auto_voice_ids = {managed_voice_id}
    elif len(mapped_voice_ids) == 1:
        auto_voice_ids = set(mapped_voice_ids)
    else:
        auto_voice_ids = set()
        if len(mapped_voice_ids) > 1:
            _add_note(
                notes,
                "Multiple legacy Voice Verify voice mappings disagree, so Dank Shield detached them without deleting any voice channel.",
            )

    if managed_queue_id > 0:
        auto_queue_ids = {managed_queue_id}
    elif len(mapped_queue_ids) == 1:
        auto_queue_ids = set(mapped_queue_ids)
    else:
        auto_queue_ids = set()
        if len(mapped_queue_ids) > 1:
            _add_note(
                notes,
                "Multiple legacy Voice Verify request-channel mappings disagree, so Dank Shield detached them without deleting any request channel.",
            )

    for channel_id in sorted(candidate_voice_ids):
        channel = guild.get_channel(channel_id)
        if channel is None:
            if channel_id == managed_voice_id:
                clear_keys.add(VOICE_MANAGED_KEY)
            continue

        exact_default = str(getattr(channel, "name", "") or "") == defaults.VC_VERIFY_CHANNEL_NAME
        proven = bool(
            channel_id == managed_voice_id
            or (
                managed_voice_id <= 0
                and channel_id in auto_voice_ids
                and await _audit_proves_bot_created(guild, channel)
            )
        )
        if not (_is_voice_channel(channel) and exact_default and proven):
            if channel_id in mapped_voice_ids:
                _add_note(
                    notes,
                    f"Left {getattr(channel, 'mention', channel)} in place because it is not a proven Dank Shield-managed default.",
                )
            continue

        members = list(getattr(channel, "members", []) or [])
        if members:
            _add_note(
                notes,
                f"Kept {getattr(channel, 'mention', channel)} because someone is currently connected.",
            )
            continue

        try:
            await channel.delete(reason="Dank Shield Voice Verify turned OFF")
            _add_note(notes, "Removed Dank Shield's unused Voice Verify voice channel.")
            if channel_id == managed_voice_id:
                clear_keys.add(VOICE_MANAGED_KEY)
        except Exception as exc:
            _add_note(
                notes,
                f"Could not remove the unused Voice Verify channel: `{type(exc).__name__}`.",
            )

    for channel_id in sorted(candidate_queue_ids):
        channel = guild.get_channel(channel_id)
        if channel is None:
            if channel_id == managed_queue_id:
                clear_keys.add(VOICE_QUEUE_MANAGED_KEY)
            continue

        exact_default = str(getattr(channel, "name", "") or "") == defaults.VC_QUEUE_CHANNEL_NAME
        proven = bool(
            channel_id == managed_queue_id
            or (
                managed_queue_id <= 0
                and channel_id in auto_queue_ids
                and await _audit_proves_bot_created(guild, channel)
            )
        )
        if not (_is_text_channel(channel) and exact_default and proven):
            if channel_id in mapped_queue_ids:
                _add_note(
                    notes,
                    f"Left {getattr(channel, 'mention', channel)} in place because it is not a proven Dank Shield-managed default.",
                )
            continue

        if not await _text_channel_is_empty(channel):
            _add_note(
                notes,
                f"Kept {getattr(channel, 'mention', channel)} because it contains staff history or could not be safely inspected.",
            )
            continue

        try:
            await channel.delete(reason="Dank Shield Voice Verify turned OFF")
            _add_note(notes, "Removed Dank Shield's empty Voice Verify staff-request channel.")
            if channel_id == managed_queue_id:
                clear_keys.add(VOICE_QUEUE_MANAGED_KEY)
        except Exception as exc:
            _add_note(
                notes,
                f"Could not remove the unused Voice Verify request channel: `{type(exc).__name__}`.",
            )

    try:
        await clear_guild_config_keys(
            int(guild.id),
            clear_keys,
            source="/dank setup Voice Verify OFF resource reconciliation",
            actor=actor,
        )
        _add_note(notes, "Cleared Voice Verify's saved channel mappings.")
    except Exception as exc:
        _add_note(
            notes,
            "⚠️ Voice Verify is OFF, but its old channel mappings could not be cleared: "
            f"`{type(exc).__name__}: {str(exc)[:160]}`",
        )

    return "\n".join(notes)
