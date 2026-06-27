from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional
import hashlib

import discord


PROFILE_STAFF_REQUEST_CHANNEL_KEYS: tuple[str, ...] = (
    "profile_staff_request_channel_id",
    "profile_staff_requests_channel_id",
    "profile_request_channel_id",
    "profile_requests_channel_id",
    "staff_request_channel_id",
    "staff_requests_channel_id",
    "ticket_log_channel_id",
    "tickets_log_channel_id",
    "transcripts_channel_id",
    "modlog_channel_id",
)

PROFILE_STAFF_REQUEST_NAME_HINTS: tuple[str, ...] = (
    "staff-requests",
    "staff-request",
    "staff-queue",
    "request-queue",
    "profile-requests",
    "profile-request",
    "ticket-requests",
    "ticket-logs",
    "tickets",
    "mod-log",
    "modlog",
    "staff-log",
    "staff-commands",
    "staff",
    "support",
)


@dataclass(frozen=True)
class ProfileStaffRequest:
    request_type: str
    requested_value: str
    member_id: int
    member_display: str
    member_mention: str
    member_tag: str
    guild_id: int
    source_channel_id: int | None = None
    source_channel_mention: str = ""

    @property
    def stable_key(self) -> str:
        raw = f"{self.guild_id}|{self.member_id}|{self.request_type}|{self.requested_value}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:18]


@dataclass(frozen=True)
class ProfileStaffRequestDelivery:
    ok: bool
    reason: str
    channel_id: int | None = None
    channel_mention: str = ""
    message_id: int | None = None
    request_key: str = ""


def _safe_int(value: Any) -> int:
    try:
        return int(str(value or "0").strip())
    except Exception:
        return 0


def _channel_name_key(channel: discord.abc.GuildChannel) -> str:
    return str(getattr(channel, "name", "") or "").strip().lower().replace("_", "-").replace(" ", "-")


def _can_send(channel: Any, guild: discord.Guild) -> bool:
    if not isinstance(channel, discord.TextChannel):
        return False
    me = getattr(guild, "me", None)
    if isinstance(me, discord.Member):
        try:
            perms = channel.permissions_for(me)
            return bool(perms.view_channel and perms.send_messages and perms.embed_links)
        except Exception:
            return False
    return True


def _dedupe_channels(channels: Iterable[discord.TextChannel]) -> list[discord.TextChannel]:
    seen: set[int] = set()
    out: list[discord.TextChannel] = []
    for channel in channels:
        if not isinstance(channel, discord.TextChannel):
            continue
        cid = int(channel.id)
        if cid in seen:
            continue
        seen.add(cid)
        out.append(channel)
    return out


async def profile_staff_request_channels(guild: discord.Guild) -> list[discord.TextChannel]:
    """Resolve the centralized staff queue for profile requests.

    Order matters: explicit config wins, then modlog, then staff/ticket-like
    fallback channels. The sender still verifies permissions before delivery.
    """

    candidates: list[discord.TextChannel] = []

    try:
        from stoney_verify.guild_config import get_guild_config

        cfg = await get_guild_config(int(guild.id), refresh=True)
        for key in PROFILE_STAFF_REQUEST_CHANNEL_KEYS:
            channel_id = _safe_int(cfg.get(key))
            if channel_id <= 0:
                continue
            channel = guild.get_channel(channel_id)
            if isinstance(channel, discord.TextChannel):
                candidates.append(channel)
    except Exception:
        pass

    try:
        from stoney_verify.guild_config import get_guild_config
        from stoney_verify.commands_ext import public_modlog_group as modlog

        cfg = await get_guild_config(int(guild.id), refresh=True)
        channel = modlog._modlog_channel(guild, cfg)
        if isinstance(channel, discord.TextChannel):
            candidates.append(channel)
    except Exception:
        pass

    for hint in PROFILE_STAFF_REQUEST_NAME_HINTS:
        for channel in list(getattr(guild, "text_channels", []) or []):
            if not isinstance(channel, discord.TextChannel):
                continue
            name = _channel_name_key(channel)
            if hint in name:
                candidates.append(channel)

    return [channel for channel in _dedupe_channels(candidates) if _can_send(channel, guild)]


def build_profile_staff_request_embed(request: ProfileStaffRequest) -> discord.Embed:
    label = "Missing Interest" if request.request_type == "interest" else "Missing Identity"
    emoji = "🎮" if request.request_type == "interest" else "🪪"
    role_prefix = "Interest" if request.request_type == "interest" else "Identity"
    embed = discord.Embed(
        title=f"{emoji} {label} Request",
        description=(
            "Centralized staff request from the Profile Builder.\n\n"
            "This does **not** create or assign a role automatically. Staff must review and approve manually."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Member",
        value=f"{request.member_mention}\n`{request.member_tag}` (`{request.member_id}`)",
        inline=False,
    )
    embed.add_field(name="Requested label", value=f"`{request.requested_value}`", inline=False)
    embed.add_field(name="Request type", value=label, inline=True)
    embed.add_field(name="Request ID", value=f"`{request.stable_key}`", inline=True)
    if request.source_channel_mention:
        embed.add_field(name="Submitted from", value=request.source_channel_mention, inline=True)
    embed.add_field(
        name="Safe staff action",
        value=(
            "Approve only if appropriate for this server. Recommended role name if approved:\n"
            f"`{role_prefix}: {request.requested_value}`"
        ),
        inline=False,
    )
    embed.set_footer(text="Dank Shield centralized profile request queue")
    return embed


async def dispatch_profile_staff_request(
    *,
    guild: discord.Guild,
    member: discord.Member,
    request_type: str,
    requested_value: str,
    source_channel: Any = None,
) -> ProfileStaffRequestDelivery:
    clean_type = str(request_type or "").strip().lower()
    if clean_type not in {"interest", "identity"}:
        return ProfileStaffRequestDelivery(False, "Unsupported profile request type.")

    clean_value = " ".join(str(requested_value or "").split()).strip()
    if not clean_value:
        return ProfileStaffRequestDelivery(False, "Request value was empty.")

    source_channel_id = int(getattr(source_channel, "id", 0) or 0) or None
    source_channel_mention = str(getattr(source_channel, "mention", "") or "")
    request = ProfileStaffRequest(
        request_type=clean_type,
        requested_value=clean_value,
        member_id=int(member.id),
        member_display=str(getattr(member, "display_name", "") or member),
        member_mention=str(getattr(member, "mention", "") or f"<@{int(member.id)}>"),
        member_tag=str(member),
        guild_id=int(guild.id),
        source_channel_id=source_channel_id,
        source_channel_mention=source_channel_mention,
    )

    channels = await profile_staff_request_channels(guild)
    if not channels:
        try:
            print(
                "⚠️ profile_staff_request no_delivery_channel "
                f"guild={guild.id} type={clean_type} request={request.stable_key} value={clean_value!r}"
            )
        except Exception:
            pass
        return ProfileStaffRequestDelivery(False, "No staff request/modlog/ticket channel is configured or sendable.", request_key=request.stable_key)

    embed = build_profile_staff_request_embed(request)
    last_error = ""
    for channel in channels:
        try:
            message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            try:
                print(
                    "✅ profile_staff_request delivered "
                    f"guild={guild.id} channel={channel.id} type={clean_type} request={request.stable_key}"
                )
            except Exception:
                pass
            return ProfileStaffRequestDelivery(
                True,
                "Delivered to centralized staff request queue.",
                channel_id=int(channel.id),
                channel_mention=str(channel.mention),
                message_id=int(getattr(message, "id", 0) or 0) or None,
                request_key=request.stable_key,
            )
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
            continue

    try:
        print(
            "⚠️ profile_staff_request delivery_failed "
            f"guild={guild.id} type={clean_type} request={request.stable_key} error={last_error}"
        )
    except Exception:
        pass
    return ProfileStaffRequestDelivery(False, f"Could not deliver staff request: {last_error or 'unknown error'}", request_key=request.stable_key)
