from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import discord

from .guild_config import get_guild_config
from .setup_resource_reconcile import (
    VOICE_MANAGED_KEY,
    VOICE_MAPPING_KEYS,
    VOICE_QUEUE_MANAGED_KEY,
    VOICE_QUEUE_MAPPING_KEYS,
    _safe_id,
    _text_channel_is_empty,
)
from .setup_service_state import service_state_from_config


@dataclass(frozen=True)
class LegacyVoiceCleanupPreview:
    voice_id: int = 0
    queue_id: int = 0
    notes: tuple[str, ...] = ()
    blocked_reason: str = ""

    @property
    def has_candidates(self) -> bool:
        return bool(self.voice_id > 0 or self.queue_id > 0)


def _flat_config_items(cfg: Any) -> dict[str, Any]:
    items: dict[str, Any] = {}

    if isinstance(cfg, Mapping):
        for key, value in cfg.items():
            items[str(key)] = value
    else:
        try:
            for key, value in vars(cfg).items():
                items[str(key)] = value
        except Exception:
            pass

    for bucket in ("settings", "config", "metadata", "meta"):
        nested = items.get(bucket)
        if isinstance(nested, Mapping):
            for key, value in nested.items():
                items[str(key)] = value

    return items


def _referenced_elsewhere(
    cfg: Any,
    channel_id: int,
    *,
    allowed_keys: set[str],
) -> str:
    target = int(channel_id)
    if target <= 0:
        return ""

    for key, value in _flat_config_items(cfg).items():
        if key in allowed_keys:
            continue
        if _safe_id(value) == target:
            return key
    return ""


def _exact_named_channels(items: Any, name: str) -> list[Any]:
    expected = str(name or "")
    return [
        item
        for item in list(items or [])
        if str(getattr(item, "name", "") or "") == expected
    ]


async def find_legacy_voice_cleanup_candidates(
    guild: discord.Guild,
) -> LegacyVoiceCleanupPreview:
    """Find exact old Voice Verify defaults for owner-confirmed cleanup only."""

    from .commands_ext import public_setup_defaults as defaults

    cfg = await get_guild_config(int(guild.id), refresh=True)
    state = service_state_from_config(cfg)
    if bool(state.voice_verify):
        return LegacyVoiceCleanupPreview(
            blocked_reason=(
                "Voice Verify is currently ON. Turn it OFF before reviewing old Voice Verify items."
            )
        )

    notes: list[str] = []
    voice_id = 0
    queue_id = 0

    voice_matches = _exact_named_channels(
        getattr(guild, "voice_channels", []),
        defaults.VC_VERIFY_CHANNEL_NAME,
    )
    queue_matches = _exact_named_channels(
        getattr(guild, "text_channels", []),
        defaults.VC_QUEUE_CHANNEL_NAME,
    )

    voice_allowed = set(VOICE_MAPPING_KEYS) | {VOICE_MANAGED_KEY}
    queue_allowed = set(VOICE_QUEUE_MAPPING_KEYS) | {VOICE_QUEUE_MANAGED_KEY}

    if len(voice_matches) == 1:
        candidate = voice_matches[0]
        candidate_id = _safe_id(getattr(candidate, "id", 0))
        other_key = _referenced_elsewhere(
            cfg,
            candidate_id,
            allowed_keys=voice_allowed,
        )
        if other_key:
            notes.append(
                "The default Voice Verify voice channel is also referenced by another saved setting, so it will not be offered for cleanup."
            )
        else:
            voice_id = candidate_id
    elif len(voice_matches) > 1:
        notes.append(
            "More than one exact default Voice Verify voice channel exists, so Dank Shield will not guess which one is old."
        )

    if len(queue_matches) == 1:
        candidate = queue_matches[0]
        candidate_id = _safe_id(getattr(candidate, "id", 0))
        other_key = _referenced_elsewhere(
            cfg,
            candidate_id,
            allowed_keys=queue_allowed,
        )
        if other_key:
            notes.append(
                "The default Voice Verify staff-request channel is also referenced by another saved setting, so it will not be offered for cleanup."
            )
        else:
            queue_id = candidate_id
    elif len(queue_matches) > 1:
        notes.append(
            "More than one exact default Voice Verify staff-request channel exists, so Dank Shield will not guess which one is old."
        )

    return LegacyVoiceCleanupPreview(
        voice_id=voice_id,
        queue_id=queue_id,
        notes=tuple(notes),
    )


async def remove_legacy_voice_cleanup_candidates(
    guild: discord.Guild,
    *,
    expected_voice_id: int = 0,
    expected_queue_id: int = 0,
    actor: Any = None,
) -> str:
    """Remove only the exact owner-confirmed legacy candidates after revalidation."""

    from .commands_ext import public_setup_defaults as defaults
    from .commands_ext.public_setup_config_writer import clear_guild_config_keys

    preview = await find_legacy_voice_cleanup_candidates(guild)
    if preview.blocked_reason:
        return preview.blocked_reason

    notes: list[str] = []
    removed_any = False

    voice_id = int(expected_voice_id or 0)
    if voice_id > 0:
        if voice_id != preview.voice_id:
            notes.append(
                "The Voice Verify voice-channel candidate changed, so nothing was deleted. Review the list again."
            )
        else:
            channel = guild.get_channel(voice_id)
            exact = bool(
                channel is not None
                and getattr(channel, "type", None) == discord.ChannelType.voice
                and str(getattr(channel, "name", "") or "")
                == defaults.VC_VERIFY_CHANNEL_NAME
            )
            if not exact:
                notes.append(
                    "The Voice Verify voice channel no longer matches the reviewed default, so it was kept."
                )
            elif list(getattr(channel, "members", []) or []):
                notes.append(
                    f"Kept {getattr(channel, 'mention', channel)} because someone is currently connected."
                )
            else:
                try:
                    await channel.delete(
                        reason="Dank Shield owner-confirmed legacy Voice Verify cleanup"
                    )
                    removed_any = True
                    notes.append(
                        "Removed the reviewed legacy Voice Verify voice channel."
                    )
                except Exception as exc:
                    notes.append(
                        f"Could not remove the reviewed Voice Verify voice channel: `{type(exc).__name__}`."
                    )

    queue_id = int(expected_queue_id or 0)
    if queue_id > 0:
        if queue_id != preview.queue_id:
            notes.append(
                "The Voice Verify staff-request candidate changed, so nothing was deleted. Review the list again."
            )
        else:
            channel = guild.get_channel(queue_id)
            exact = bool(
                channel is not None
                and getattr(channel, "type", None) == discord.ChannelType.text
                and str(getattr(channel, "name", "") or "")
                == defaults.VC_QUEUE_CHANNEL_NAME
            )
            if not exact:
                notes.append(
                    "The Voice Verify staff-request channel no longer matches the reviewed default, so it was kept."
                )
            elif not await _text_channel_is_empty(channel):
                notes.append(
                    f"Kept {getattr(channel, 'mention', channel)} because it contains staff history or could not be safely inspected."
                )
            else:
                try:
                    await channel.delete(
                        reason="Dank Shield owner-confirmed legacy Voice Verify cleanup"
                    )
                    removed_any = True
                    notes.append(
                        "Removed the reviewed empty Voice Verify staff-request channel."
                    )
                except Exception as exc:
                    notes.append(
                        f"Could not remove the reviewed Voice Verify staff-request channel: `{type(exc).__name__}`."
                    )

    clear_keys = (
        set(VOICE_MAPPING_KEYS)
        | set(VOICE_QUEUE_MAPPING_KEYS)
        | {VOICE_MANAGED_KEY, VOICE_QUEUE_MANAGED_KEY}
    )
    try:
        await clear_guild_config_keys(
            int(guild.id),
            clear_keys,
            source="/dank setup owner-confirmed legacy Voice Verify cleanup",
            actor=actor,
        )
    except Exception as exc:
        notes.append(
            "⚠️ Cleanup finished, but stale Voice Verify mappings could not be cleared: "
            f"`{type(exc).__name__}: {str(exc)[:140]}`"
        )

    if not notes:
        notes.append("No reviewed legacy Voice Verify items were eligible for removal.")
    elif removed_any:
        notes.append("No other channels were touched.")

    return "\n".join(notes)


__all__ = [
    "LegacyVoiceCleanupPreview",
    "find_legacy_voice_cleanup_candidates",
    "remove_legacy_voice_cleanup_candidates",
]
