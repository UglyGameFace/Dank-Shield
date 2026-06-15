from __future__ import annotations

"""Make VC modlog events say joined, left, moved, or state changed."""

from typing import Any

import discord

_PATCHED = False
_ORIGINAL: Any = None


def _chan(channel: Any) -> str:
    try:
        return str(getattr(channel, "mention", None) or f"#{getattr(channel, 'name', 'unknown')}")
    except Exception:
        return "unknown"


def _title(before: discord.VoiceState, after: discord.VoiceState) -> str:
    b = getattr(before, "channel", None)
    a = getattr(after, "channel", None)
    if b is None and a is not None:
        return "🔊 Voice Channel Joined"
    if b is not None and a is None:
        return "🔇 Voice Channel Left"
    if b is not None and a is not None:
        try:
            if int(b.id) != int(a.id):
                return "🔁 Voice Channel Moved"
        except Exception:
            pass
    return "🎙️ Voice State Changed"


def _activity(before: discord.VoiceState, after: discord.VoiceState) -> str:
    b = getattr(before, "channel", None)
    a = getattr(after, "channel", None)
    if b is None and a is not None:
        return f"Joined {_chan(a)}"
    if b is not None and a is None:
        return f"Left {_chan(b)}"
    if b is not None and a is not None:
        try:
            if int(b.id) != int(a.id):
                return f"Moved from {_chan(b)} to {_chan(a)}"
        except Exception:
            pass
    changes: list[str] = []
    for label, attr in (("Server mute", "mute"), ("Server deaf", "deaf"), ("Self mute", "self_mute"), ("Self deaf", "self_deaf"), ("Streaming", "self_stream"), ("Video", "self_video")):
        try:
            if bool(getattr(before, attr, False)) != bool(getattr(after, attr, False)):
                changes.append(f"{label}: {'ON' if bool(getattr(after, attr, False)) else 'OFF'}")
        except Exception:
            pass
    return "\n".join(changes) or "Voice state changed"


async def _patched(guild: discord.Guild, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> bool:
    try:
        import stoney_verify.modlog as modlog
        b = getattr(before, "channel", None)
        a = getattr(after, "channel", None)
        if b is None and a is None:
            return False
        embed = discord.Embed(title=_title(before, after), color=discord.Color.blurple(), timestamp=modlog._now_utc())
        embed.add_field(name="Member", value=f"{member.mention} (`{member}` | `{member.id}`)", inline=False)
        embed.add_field(name="Activity", value=modlog._truncate(_activity(before, after), 1024), inline=False)
        if b is not None:
            embed.add_field(name="Before", value=f"{_chan(b)} (`{b.id}`)", inline=True)
        if a is not None:
            embed.add_field(name="After", value=f"{_chan(a)} (`{a.id}`)", inline=True)
        try:
            entry = await modlog._audit_find_recent_voice_action(guild, int(member.id))
            actor, reason = modlog._format_actor_from_audit(entry)
            if entry is not None:
                embed.add_field(name="By", value=modlog._truncate(actor, 1024), inline=False)
                if reason:
                    embed.add_field(name="Reason", value=modlog._truncate(reason, 1024), inline=False)
        except Exception:
            pass
        await modlog._post_modlog(guild, embed)
        return True
    except Exception as exc:
        try:
            print(f"⚠️ vc_join_leave_modlog_labels_guard failed to log: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


def apply() -> bool:
    global _PATCHED, _ORIGINAL
    if _PATCHED:
        return True
    try:
        import stoney_verify.modlog as modlog
        _ORIGINAL = getattr(modlog, "maybe_log_voice_state_update", None)
        modlog.maybe_log_voice_state_update = _patched
        try:
            import stoney_verify.events as events
            events.maybe_log_voice_state_update = _patched
        except Exception:
            pass
        _PATCHED = True
        print("✅ vc_join_leave_modlog_labels_guard active; VC join/leave/move logs are explicit")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ vc_join_leave_modlog_labels_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]