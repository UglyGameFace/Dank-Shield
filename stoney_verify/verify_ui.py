from __future__ import annotations

import os
import re
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Dict, Any

import discord

from .globals import *  # noqa

from .store import gen_token, sb_insert_token
from .tickets import find_ticket_owner_retry

# Optional helpers (present in some versions of your codebase)
try:
    from .tickets import ensure_ticket_ready_and_scoped  # type: ignore
except Exception:
    ensure_ticket_ready_and_scoped = None  # type: ignore

try:
    from .tickets import ensure_postback_url  # type: ignore
except Exception:
    ensure_postback_url = None  # type: ignore

try:
    from .tickets import get_or_create_webhook  # type: ignore
except Exception:
    get_or_create_webhook = None  # type: ignore


VERIFY_UI_TITLE = "Stoney Baloney Verification"
VERIFY_UI_FOOTER = "stoney_verify:verify_ui:v9"


# ============================================================
# Safe defaults for globals across versions
# ============================================================

try:
    VERIFY_EMBED_COLOR  # type: ignore[name-defined]
except Exception:
    VERIFY_EMBED_COLOR = discord.Color.green()

try:
    VERIFY_EMBED_THUMBNAIL_URL  # type: ignore[name-defined]
except Exception:
    VERIFY_EMBED_THUMBNAIL_URL = ""

try:
    TOKEN_TTL_MINUTES  # type: ignore[name-defined]
except Exception:
    TOKEN_TTL_MINUTES = 20

try:
    VC_REQUEST_TTL_MINUTES  # type: ignore[name-defined]
except Exception:
    VC_REQUEST_TTL_MINUTES = 240

try:
    ALLOW_USER_VERIFYLINK  # type: ignore[name-defined]
except Exception:
    ALLOW_USER_VERIFYLINK = False

try:
    VC_REQUEST_COOLDOWN_SECONDS  # type: ignore[name-defined]
except Exception:
    VC_REQUEST_COOLDOWN_SECONDS = 60

try:
    RUNTIME_STATS  # type: ignore[name-defined]
except Exception:
    RUNTIME_STATS = {}

try:
    VC_REQUEST_COOLDOWNS  # type: ignore[name-defined]
except Exception:
    VC_REQUEST_COOLDOWNS: Dict[int, datetime] = {}


# ============================================================
# Time / formatting helpers
# ============================================================

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _now_utc() -> datetime:
    try:
        return now_utc()  # type: ignore[name-defined]
    except Exception:
        return utcnow()


def _safe_avatar_url(user: Optional[discord.abc.User]) -> str:
    try:
        if user:
            return str(user.display_avatar.url)
    except Exception:
        pass
    return ""


def _safe_domain(site_url: str) -> str:
    try:
        if not site_url:
            return "—"
        p = urllib.parse.urlparse(site_url)
        host = p.netloc or site_url.replace("https://", "").replace("http://", "").split("/")[0]
        return f"`{host}`"
    except Exception:
        return f"`{site_url}`" if site_url else "—"


def _vc_channel_id() -> int:
    try:
        v = int(globals().get("VC_VERIFY_CHANNEL_ID", 0) or 0)
        if v > 0:
            return v
    except Exception:
        pass

    try:
        v2 = int(globals().get("VC_VERIFY_VC_ID", 0) or 0)
        if v2 > 0:
            return v2
    except Exception:
        pass

    return 0


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _is_staff_member(member: Optional[discord.Member]) -> bool:
    try:
        if not isinstance(member, discord.Member):
            return False
        return bool(is_staff(member))  # type: ignore[name-defined]
    except Exception:
        try:
            return bool(
                member and (
                    member.guild_permissions.manage_channels
                    or member.guild_permissions.manage_messages
                    or member.guild_permissions.administrator
                )
            )
        except Exception:
            return False


def _member_mention_or_fallback(
    member: Optional[discord.Member],
    member_id: int,
) -> str:
    try:
        if isinstance(member, discord.Member):
            return member.mention
    except Exception:
        pass

    try:
        mid = int(member_id or 0)
    except Exception:
        mid = 0

    if mid > 0:
        return f"<@{mid}>"
    return ""


# ============================================================
# Owner parsing / resolution
# ============================================================

def _ticket_owner_id_from_embed(embed: Optional[discord.Embed]) -> int:
    if not embed:
        return 0

    try:
        for f in (embed.fields or []):
            if (f.name or "").strip().lower() in ("👤 user", "user"):
                val = str(f.value or "")
                m = re.search(r"\b(\d{15,22})\b", val)
                if m:
                    return int(m.group(1))
    except Exception:
        pass

    return 0


async def _resolve_ticket_owner_id(
    *,
    channel: Optional[discord.abc.GuildChannel],
    interaction: discord.Interaction,
) -> int:
    if isinstance(channel, discord.TextChannel):
        try:
            owner = await find_ticket_owner_retry(channel, tries=6, delay=1.0)
            if owner:
                return int(getattr(owner, "id", 0) or 0)
        except Exception:
            pass

    try:
        msg = getattr(interaction, "message", None)
        if msg and getattr(msg, "embeds", None):
            owner_id = _ticket_owner_id_from_embed(msg.embeds[0] if msg.embeds else None)
            if owner_id > 0:
                return owner_id
    except Exception:
        pass

    return 0


async def _resolve_ticket_owner_member(
    *,
    channel: Optional[discord.abc.GuildChannel],
    interaction: discord.Interaction,
) -> Optional[discord.Member]:
    if not isinstance(channel, discord.TextChannel):
        return None

    try:
        owner = await find_ticket_owner_retry(channel, tries=6, delay=1.0)
        if isinstance(owner, discord.Member):
            return owner
    except Exception:
        pass

    owner_id = await _resolve_ticket_owner_id(channel=channel, interaction=interaction)
    if owner_id <= 0 or not channel.guild:
        return None

    try:
        member = channel.guild.get_member(owner_id)
        if member is not None:
            return member
    except Exception:
        pass

    try:
        return await channel.guild.fetch_member(owner_id)
    except Exception:
        return None


# ============================================================
# Verify link helpers
# ============================================================

def build_verify_link(token: str) -> str:
    base = ""
    try:
        base = str(globals().get("VERIFY_SITE_URL", "") or "").strip()
    except Exception:
        base = ""

    if not base:
        try:
            base = str(globals().get("SITE_URL", "") or "").strip()
        except Exception:
            base = ""

    if not base:
        return token

    joiner = "&" if "?" in base else "?"
    return f"{base}{joiner}token={token}"


# ============================================================
# Embed / view builders
# ============================================================

def build_verify_embed(
    *,
    member: Optional[discord.abc.User],
    member_id: int,
    site_url: str,
    ttl_minutes: int,
    reason: str,
    show_domain: bool = True,
) -> discord.Embed:
    ts = _now_utc()

    e = discord.Embed(
        title=VERIFY_UI_TITLE,
        color=VERIFY_EMBED_COLOR if isinstance(VERIFY_EMBED_COLOR, discord.Color) else discord.Color.green(),
        timestamp=ts,
    )

    try:
        if member:
            av = _safe_avatar_url(member)
            if av:
                e.set_thumbnail(url=av)
                e.set_author(name=f"Ticket Owner: {member}", icon_url=av)
            else:
                e.set_author(name=f"Ticket Owner: {member}")
        else:
            e.set_author(name="Ticket Owner")
    except Exception:
        pass

    e.description = (
        "Press **Upload ID** for your private upload link.\n"
        "The website won’t work without a token (use the button first)."
    )

    if member:
        user_value = f"{member.mention}\n`{member}` • `{int(member_id or getattr(member, 'id', 0) or 0)}`"
    elif member_id:
        user_value = f"<@{member_id}>\n`{member_id}`"
    else:
        user_value = "Unknown"

    e.add_field(name="👤 User", value=user_value, inline=False)
    e.add_field(name="⏳ Expiration", value=f"**{int(ttl_minutes)} minutes**", inline=True)
    e.add_field(name="🔒 Privacy", value="Link is sent **ephemeral** (owner only).", inline=True)
    e.add_field(name="🧾 Review", value="Staff approves / denies inside this ticket.", inline=False)

    if show_domain and site_url:
        e.add_field(name="🌐 Verified Domain", value=_safe_domain(site_url), inline=False)

    footer = VERIFY_UI_FOOTER
    if reason:
        footer = f"{VERIFY_UI_FOOTER} • {reason}"
    e.set_footer(text=footer)

    return e


class VerifyView(discord.ui.View):
    def __init__(
        self,
        *,
        site_url: str,
        ttl_minutes: int,
        allow_regen: bool,
        reason: str = "",
    ):
        super().__init__(timeout=None)
        self.site_url = str(site_url or "")
        self.ttl_minutes = int(ttl_minutes or 20)
        self.allow_regen = bool(allow_regen)
        self.reason = str(reason or "")

        self.add_item(
            discord.ui.Button(
                label="Upload ID",
                style=discord.ButtonStyle.primary,
                emoji="🔐",
                custom_id="sv:verify:get",
                row=0,
            )
        )

        if _vc_channel_id() > 0:
            self.add_item(
                discord.ui.Button(
                    label="Verify in VC",
                    style=discord.ButtonStyle.secondary,
                    emoji="🎙️",
                    custom_id="sv:verify:vc",
                    row=0,
                )
            )

        self.add_item(
            discord.ui.Button(
                label="Reveal Raw Link",
                style=discord.ButtonStyle.secondary,
                emoji="🔎",
                custom_id="sv:verify:raw",
                row=1,
            )
        )

        if self.allow_regen:
            self.add_item(
                discord.ui.Button(
                    label="Generate New Link",
                    style=discord.ButtonStyle.secondary,
                    emoji="🔁",
                    custom_id="sv:verify:regen",
                    row=1,
                )
            )

        if self.site_url:
            self.add_item(
                discord.ui.Button(
                    label="Tap to view website",
                    style=discord.ButtonStyle.link,
                    url=self.site_url,
                    emoji="🌐",
                    row=2,
                )
            )


# ============================================================
# Token storage helpers
# ============================================================

def _fallback_postback_url(channel: discord.TextChannel) -> str:
    try:
        return f"bot://channel/{int(channel.id)}"
    except Exception:
        return "bot://channel/0"


async def _best_effort_webhook_url(channel: Optional[discord.TextChannel]) -> str:
    if not isinstance(channel, discord.TextChannel):
        return ""

    if ensure_postback_url:
        try:
            url = await ensure_postback_url(channel)  # type: ignore[misc]
            url = str(url or "").strip()
            if url:
                return url
        except Exception:
            pass

    if get_or_create_webhook:
        try:
            url = await get_or_create_webhook(channel)  # type: ignore[misc]
            url = str(url or "").strip()
            if url:
                return url
        except Exception:
            pass

    return _fallback_postback_url(channel)


async def _store_token_best_effort(
    *,
    token: str,
    guild: Optional[discord.Guild],
    channel: Optional[discord.abc.GuildChannel],
    requester_id: int,
    ttl_minutes: int,
) -> bool:
    try:
        expires_at = _now_utc() + timedelta(minutes=int(ttl_minutes or 20))
        gid = int(getattr(guild, "id", 0) or 0)
        cid = int(getattr(channel, "id", 0) or 0)
        rid = int(requester_id or 0)

        if rid <= 0:
            return False

        webhook_url = ""
        if isinstance(channel, discord.TextChannel):
            webhook_url = await _best_effort_webhook_url(channel)

        webhook_url = str(webhook_url or "").strip()
        if not webhook_url:
            if isinstance(channel, discord.TextChannel):
                webhook_url = _fallback_postback_url(channel)
            else:
                return False

        ok = sb_insert_token(
            token,
            webhook_url=webhook_url,
            expires_at=expires_at,
            guild_id=gid or None,
            channel_id=cid or None,
            requester_id=rid or None,
        )
        return bool(ok)
    except Exception:
        return False


async def _issue_token_url(
    *,
    site_url: str,
    guild: Optional[discord.Guild],
    channel: Optional[discord.abc.GuildChannel],
    requester_id: int,
    ttl_minutes: int,
) -> Tuple[str, str]:
    try:
        token = make_token()  # type: ignore[name-defined]
    except Exception:
        try:
            token = gen_token()
        except Exception:
            token = os.urandom(16).hex()

    try:
        await _store_token_best_effort(
            token=token,
            guild=guild,
            channel=channel,
            requester_id=int(requester_id or 0),
            ttl_minutes=int(ttl_minutes or 20),
        )
    except Exception:
        pass

    url = build_verify_link(token)
    return token, url


# ============================================================
# Verify UI posting
# ============================================================

async def post_or_replace_verify_ui(
    channel: discord.TextChannel,
    *,
    requester_id: Optional[int] = None,
    reason: str = "",
    site_url: str,
    ttl_minutes: int,
    allow_regen: bool,
) -> str:
    if not isinstance(channel, discord.TextChannel):
        return ""

    if ensure_ticket_ready_and_scoped:
        try:
            ch2 = await ensure_ticket_ready_and_scoped(channel.guild, channel.id)  # type: ignore[misc]
            if not isinstance(ch2, discord.TextChannel):
                return ""
            channel = ch2
        except Exception:
            return ""

    guild = channel.guild

    owner_id = int(requester_id or 0)
    if owner_id <= 0:
        try:
            owner = await find_ticket_owner_retry(channel, tries=6, delay=1.0)
            if owner:
                owner_id = int(getattr(owner, "id", 0) or 0)
        except Exception:
            owner_id = 0

    if owner_id <= 0:
        return ""

    member_obj: Optional[discord.Member] = None
    try:
        if guild:
            member_obj = guild.get_member(int(owner_id)) or None
            if member_obj is None:
                member_obj = await guild.fetch_member(int(owner_id))
    except Exception:
        member_obj = None

    embed = build_verify_embed(
        member=member_obj,
        member_id=int(owner_id or 0),
        site_url=site_url,
        ttl_minutes=int(ttl_minutes or 20),
        reason=reason,
        show_domain=True,
    )

    view = VerifyView(
        site_url=site_url,
        ttl_minutes=int(ttl_minutes or 20),
        allow_regen=bool(allow_regen),
        reason=reason,
    )

    try:
        me_id = int(getattr(getattr(bot, "user", None), "id", 0) or 0)  # type: ignore[name-defined]
        async for msg in channel.history(limit=80):
            if int(getattr(getattr(msg, "author", None), "id", 0) or 0) != me_id:
                continue
            if not msg.embeds:
                continue

            e0 = msg.embeds[0]
            title_ok = (e0.title or "") == VERIFY_UI_TITLE
            footer_text = str(getattr(getattr(e0, "footer", None), "text", "") or "")
            footer_ok = VERIFY_UI_FOOTER.split(" • ")[0] in footer_text

            if title_ok or footer_ok:
                await msg.edit(embed=embed, view=view)
                return "updated"
    except Exception:
        pass

    try:
        await channel.send(embed=embed, view=view)
        return "posted"
    except Exception:
        return ""


# ============================================================
# VC request helpers
# ============================================================

async def _post_user_vc_status_message(
    *,
    ticket_channel: discord.TextChannel,
    owner_id: int,
    owner_member: Optional[discord.Member],
    vc_id: int,
    staff_posted: bool,
) -> None:
    try:
        mention = _member_mention_or_fallback(owner_member, owner_id)
        prefix = f"{mention} " if mention else ""

        if staff_posted:
            await ticket_channel.send(
                f"🎙️ {prefix}**VC verification request sent.**\n"
                "Staff has been notified. Please wait here — when a staff member is ready, they'll tell you to join VC."
            )
        else:
            await ticket_channel.send(
                f"⚠️ {prefix}**VC request was created, but staff routing failed.**\n"
                "A staff member can still recover it with `/vc_status`, `/vc_reissue`, or by checking the VC queue config."
            )
    except Exception:
        pass


async def _post_vc_request_to_staff_only(
    *,
    guild: discord.Guild,
    ticket_channel: discord.TextChannel,
    owner_id: int,
    token: str,
) -> bool:
    """
    Back-compat shim.
    """
    try:
        from .commands_ext.vc_flow import create_vc_request_for_ticket
    except Exception:
        return False

    result = await create_vc_request_for_ticket(
        guild=guild,
        ticket_channel=ticket_channel,
        requester_id=int(owner_id or 0),
        requested_by_id=int(owner_id or 0),
        token=str(token or ""),
        owner_member=(guild.get_member(int(owner_id)) if int(owner_id or 0) > 0 else None),
    )
    return bool(result.get("staff_posted"))


# ============================================================
# Interaction handler
# ============================================================

async def maybe_handle_verify_ui_interaction(interaction: discord.Interaction, *, site_url: str) -> bool:
    """
    Handle Verify UI button clicks. Returns True if handled.
    """
    try:
        data = getattr(interaction, "data", None) or {}
        custom_id = str(data.get("custom_id") or "")
        if not custom_id.startswith("sv:verify:"):
            return False

        parts = custom_id.split(":")
        if len(parts) < 3:
            return False
        action = (parts[2] or "").strip().lower()

        user = interaction.user
        guild = interaction.guild
        channel = interaction.channel

        if not isinstance(channel, discord.TextChannel):
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "❌ This action only works inside a verification ticket.",
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(
                        "❌ This action only works inside a verification ticket.",
                        ephemeral=True,
                    )
            except Exception:
                pass
            return True

        staff = False
        try:
            if guild and isinstance(user, discord.Member):
                staff = _is_staff_member(user)
        except Exception:
            staff = False

        owner_id = await _resolve_ticket_owner_id(channel=channel, interaction=interaction)
        owner_member = await _resolve_ticket_owner_member(channel=channel, interaction=interaction)

        owner_only_actions = {"get", "raw", "regen", "vc"}
        if action in owner_only_actions and not staff:
            if owner_id <= 0:
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            "❌ I couldn't verify the ticket owner for this action. Ask staff to repost or repair the verify UI.",
                            ephemeral=True,
                        )
                    else:
                        await interaction.followup.send(
                            "❌ I couldn't verify the ticket owner for this action. Ask staff to repost or repair the verify UI.",
                            ephemeral=True,
                        )
                except Exception:
                    pass
                return True

            if int(user.id) != int(owner_id):
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            "❌ Only the **ticket owner** can use that button.",
                            ephemeral=True,
                        )
                    else:
                        await interaction.followup.send(
                            "❌ Only the **ticket owner** can use that button.",
                            ephemeral=True,
                        )
                except Exception:
                    pass
                return True

        if action == "regen" and not staff:
            if not bool(ALLOW_USER_VERIFYLINK):
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            "❌ Generating a new link is currently **disabled**.",
                            ephemeral=True,
                        )
                    else:
                        await interaction.followup.send(
                            "❌ Generating a new link is currently **disabled**.",
                            ephemeral=True,
                        )
                except Exception:
                    pass
                return True

        # --------------------------------------------------------
        # Secure upload token issuing
        # --------------------------------------------------------
        if action in ("get", "raw", "regen"):
            requester_id = int(owner_id or 0)
            if requester_id <= 0:
                await interaction.response.send_message(
                    "❌ I couldn't resolve the ticket owner, so I won't issue a verification token.",
                    ephemeral=True,
                )
                return True

            ttl = int(TOKEN_TTL_MINUTES or 20)
            token, url = await _issue_token_url(
                site_url=site_url,
                guild=guild,
                channel=channel,
                requester_id=requester_id,
                ttl_minutes=ttl,
            )

            if action == "raw":
                try:
                    RUNTIME_STATS["raw_link_clicks"] = int(RUNTIME_STATS.get("raw_link_clicks", 0) or 0) + 1
                except Exception:
                    pass

                await interaction.response.send_message(
                    "🔗 **Raw link (tap to reveal):**\n"
                    f"||<{url}>||",
                    ephemeral=True,
                )
                return True

            try:
                if action == "get":
                    RUNTIME_STATS["open_link_clicks"] = int(RUNTIME_STATS.get("open_link_clicks", 0) or 0) + 1
            except Exception:
                pass

            view = discord.ui.View(timeout=120)
            view.add_item(
                discord.ui.Button(
                    label="Open Secure Upload",
                    style=discord.ButtonStyle.link,
                    url=url,
                    emoji="🔐",
                )
            )

            msg = (
                "🔒 Here’s your secure upload link (private):\n"
                f"||<{url}>||\n\n"
                f"⏳ Expires in **{ttl} minutes**."
            )
            await interaction.response.send_message(msg, view=view, ephemeral=True)
            return True

        # --------------------------------------------------------
        # VC verify flow
        # --------------------------------------------------------
        if action == "vc":
            if not guild:
                await interaction.response.send_message(
                    "🎙️ VC verification is currently unavailable here. Please use secure upload in this ticket.",
                    ephemeral=True,
                )
                return True

            if owner_id <= 0:
                await interaction.response.send_message(
                    "❌ I couldn't resolve the ticket owner, so I won't create a VC verification request.",
                    ephemeral=True,
                )
                return True

            vc_id = _vc_channel_id()
            if vc_id <= 0:
                await interaction.response.send_message(
                    "🎙️ VC verification is currently unavailable. Please use secure upload in this ticket.",
                    ephemeral=True,
                )
                return True

            try:
                last = VC_REQUEST_COOLDOWNS.get(int(user.id))
                if last and (_now_utc() - last).total_seconds() < int(VC_REQUEST_COOLDOWN_SECONDS):
                    left = int(int(VC_REQUEST_COOLDOWN_SECONDS) - (_now_utc() - last).total_seconds())
                    await interaction.response.send_message(
                        f"⏳ Please wait **{left}s** before requesting VC verify again.",
                        ephemeral=True,
                    )
                    return True
                VC_REQUEST_COOLDOWNS[int(user.id)] = _now_utc()
            except Exception:
                pass

            ttl = int(VC_REQUEST_TTL_MINUTES or 0) or int(TOKEN_TTL_MINUTES or 20)
            requester_id = int(owner_id)

            token, _url = await _issue_token_url(
                site_url=site_url,
                guild=guild,
                channel=channel,
                requester_id=requester_id,
                ttl_minutes=ttl,
            )

            try:
                from .commands_ext.vc_flow import create_vc_request_for_ticket
            except Exception as e:
                await interaction.response.send_message(
                    f"❌ VC verify request service is unavailable: {e}",
                    ephemeral=True,
                )
                return True

            result = await create_vc_request_for_ticket(
                guild=guild,
                ticket_channel=channel,
                requester_id=int(owner_id),
                requested_by_id=int(user.id),
                token=token,
                owner_member=owner_member,
            )

            if not result.get("ok"):
                await interaction.response.send_message(
                    f"❌ {result.get('message') or 'Failed to create VC request.'}",
                    ephemeral=True,
                )
                return True

            if result.get("duplicate"):
                await interaction.response.send_message(
                    "✅ VC request is already queued for this ticket. Staff will respond soon.",
                    ephemeral=True,
                )
                return True

            await _post_user_vc_status_message(
                ticket_channel=channel,
                owner_id=int(owner_id),
                owner_member=owner_member,
                vc_id=int(vc_id),
                staff_posted=bool(result.get("staff_posted")),
            )

            if result.get("staff_posted"):
                await interaction.response.send_message(
                    "✅ VC request sent.\nStay in this ticket — staff will message you when ready.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "⚠️ VC request was created, but staff routing failed.\nA staff member can recover it with `/vc_status` or `/vc_reissue`.",
                    ephemeral=True,
                )
            return True

        return False

    except Exception as e:
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ Verify UI error: {e}", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ Verify UI error: {e}", ephemeral=True)
        except Exception:
            pass
        return True


__all__ = [
    "VERIFY_UI_TITLE",
    "VERIFY_UI_FOOTER",
    "VerifyView",
    "build_verify_link",
    "build_verify_embed",
    "post_or_replace_verify_ui",
    "maybe_handle_verify_ui_interaction",
    "_issue_token_url",
    "_post_vc_request_to_staff_only",
]
