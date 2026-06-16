from __future__ import annotations

"""Shared Invite Shield helpers for this-server invite detection and sanitizing.

Discord does not allow bots to edit another user's or another bot's message.
For mixed invite posts, Dank Shield deletes the unsafe original and reposts a
clean local-only summary so external server information is not preserved.
"""

import time
from typing import Any, Iterable

import discord

_INVITE_CODE_GUILD_CACHE: dict[str, tuple[float, int]] = {}


def normalize_invite_code(code: Any) -> str:
    text = str(code or "").strip().lower().strip("/")
    for prefix in (
        "https://discord.gg/",
        "http://discord.gg/",
        "discord.gg/",
        "https://discord.com/invite/",
        "http://discord.com/invite/",
        "discord.com/invite/",
        "https://discordapp.com/invite/",
        "http://discordapp.com/invite/",
        "discordapp.com/invite/",
    ):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text.strip().strip("/")


def invite_url(code: Any) -> str:
    clean = normalize_invite_code(code)
    return f"https://discord.gg/{clean}" if clean else ""


async def fetch_invite_guild_id(code: Any) -> int:
    """Resolve an invite code to its guild id without guild.invites().

    guild.invites() often requires Manage Guild. fetch_invite can resolve public
    invite codes without requiring that guild permission, which protects each
    server's own invite links in minimal-permission public installs.
    """

    clean = normalize_invite_code(code)
    if not clean:
        return 0

    now = time.monotonic()
    cached = _INVITE_CODE_GUILD_CACHE.get(clean)
    if cached is not None:
        saved_at, guild_id = cached
        ttl = 300.0 if int(guild_id or 0) > 0 else 45.0
        if now - float(saved_at) <= ttl:
            return int(guild_id or 0)

    client = None
    try:
        from stoney_verify.globals import bot
        client = bot
    except Exception:
        client = None

    guild_id = 0
    if client is not None:
        try:
            try:
                invite = await client.fetch_invite(clean, with_counts=False, with_expiration=False)
            except TypeError:
                invite = await client.fetch_invite(clean)
            invite_guild = getattr(invite, "guild", None)
            guild_id = int(getattr(invite_guild, "id", 0) or 0)
        except Exception:
            guild_id = 0

    _INVITE_CODE_GUILD_CACHE[clean] = (now, int(guild_id or 0))
    return int(guild_id or 0)


async def invite_code_belongs_to_guild(guild: discord.Guild, code: Any) -> bool:
    try:
        return int(await fetch_invite_guild_id(code) or 0) == int(guild.id)
    except Exception:
        return False


async def this_guild_invite_codes(guild: discord.Guild, codes: Iterable[Any]) -> list[str]:
    kept: list[str] = []
    for raw in list(codes or []):
        clean = normalize_invite_code(raw)
        if not clean or clean in kept:
            continue
        if await invite_code_belongs_to_guild(guild, clean):
            kept.append(clean)
    return kept


def _author_name(message: discord.Message) -> str:
    try:
        author = getattr(message, "author", None)
        name = str(getattr(author, "display_name", "") or getattr(author, "name", "") or author or "unknown")
        user_id = int(getattr(author, "id", 0) or 0)
        return f"{name} (`{user_id}`)" if user_id > 0 else name
    except Exception:
        return "unknown"


async def send_mixed_invite_sanitized_notice(
    message: discord.Message,
    *,
    kept_codes: Iterable[Any],
    removed_count: int,
    source: str = "invite-shield",
) -> bool:
    """Post a safe replacement for a deleted mixed invite message.

    The replacement intentionally does not copy original text, embeds, buttons,
    external server names, banners, descriptions, or channel metadata.
    """

    safe_codes = []
    for raw in list(kept_codes or []):
        clean = normalize_invite_code(raw)
        if clean and clean not in safe_codes:
            safe_codes.append(clean)

    if not safe_codes or int(removed_count or 0) <= 0:
        return False

    channel = getattr(message, "channel", None)
    if not isinstance(channel, discord.TextChannel):
        return False

    try:
        me = channel.guild.me
        if not isinstance(me, discord.Member):
            return False
        perms = channel.permissions_for(me)
        if not perms.send_messages:
            return False

        invite_lines = "\n".join(invite_url(code) for code in safe_codes[:5] if invite_url(code))
        if not invite_lines:
            return False

        embed = discord.Embed(
            title="🛡️ Invite Shield cleaned a mixed invite post",
            description=(
                "The original message contained this server's invite **and** an external Discord invite.\n\n"
                "Dank Shield removed the unsafe original and reposted only the this-server invite."
            ),
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Original author", value=_author_name(message), inline=False)
        embed.add_field(name="Kept this-server invite", value=invite_lines[:1024], inline=False)
        embed.add_field(
            name="Removed",
            value=f"{int(removed_count or 0)} external Discord invite(s) and any external invite preview/card content.",
            inline=False,
        )
        embed.set_footer(text=f"Dank Shield Invite Shield • sanitized • {source}")

        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        return True
    except Exception:
        return False
