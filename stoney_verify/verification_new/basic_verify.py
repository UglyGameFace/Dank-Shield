from __future__ import annotations

"""Basic Discord-button verification flow.

This is the default public-server verification flow:
- new members keep the configured Unverified role
- the verification channel shows a Verify button
- clicking Verify grants Verified / effective Member access and removes Unverified
- no ID upload website, token, ticket, or old Stoney panel is involved
"""

import asyncio
from typing import Any, Mapping, Optional

import discord

from stoney_verify.guild_config import get_guild_config
from stoney_verify.setup_engine.loader import snapshot_from_config
from stoney_verify.setup_engine.verification_modes import BASIC_VERIFY_CUSTOM_ID, BASIC_VERIFY_FOOTER

_BASIC_VERIFY_LOCKS: dict[str, asyncio.Lock] = {}
_RUNTIME_VIEW_REGISTERED = False


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        if not text or text.lower() in {"none", "null"}:
            return int(default)
        return int(text)
    except Exception:
        return int(default)


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    for bucket in ("settings", "config", "metadata", "meta"):
        try:
            nested = getattr(cfg, bucket, None)
            if isinstance(nested, Mapping) and nested.get(key) is not None:
                return nested.get(key)
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, Mapping) and nested.get(key) is not None:
                    return nested.get(key)
        except Exception:
            pass
    return default


def _channel_from_cfg(guild: discord.Guild, cfg: Any, *keys: str) -> Optional[discord.TextChannel]:
    for key in keys:
        cid = _safe_int(_cfg_value(cfg, key, 0), 0)
        if cid <= 0:
            continue
        channel = guild.get_channel(cid)
        if isinstance(channel, discord.TextChannel):
            return channel
    return None


def _role(guild: discord.Guild, role_id: int) -> Optional[discord.Role]:
    try:
        role = guild.get_role(int(role_id or 0)) if int(role_id or 0) > 0 else None
        return role if isinstance(role, discord.Role) else None
    except Exception:
        return None


def _bot_can_manage_role(guild: discord.Guild, role: discord.Role) -> tuple[bool, str]:
    me = getattr(guild, "me", None)
    if not isinstance(me, discord.Member):
        return False, "Dank Shield could not resolve its bot member."
    try:
        if not me.guild_permissions.manage_roles and not me.guild_permissions.administrator:
            return False, "Dank Shield is missing Manage Roles."
        if role >= me.top_role:
            return False, f"Dank Shield's role must be above {role.mention}."
    except Exception:
        return False, "Discord role hierarchy could not be checked."
    return True, ""


def _dedupe_roles(roles: list[Optional[discord.Role]]) -> list[discord.Role]:
    out: list[discord.Role] = []
    seen: set[int] = set()
    for role in roles:
        if not isinstance(role, discord.Role) or role.is_default():
            continue
        if int(role.id) in seen:
            continue
        seen.add(int(role.id))
        out.append(role)
    return out


def _lock_for(guild_id: int, user_id: int) -> asyncio.Lock:
    key = f"{int(guild_id)}:{int(user_id)}"
    lock = _BASIC_VERIFY_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _BASIC_VERIFY_LOCKS[key] = lock
    return lock


def build_basic_verify_embed(guild: discord.Guild, cfg: Any) -> discord.Embed:
    rules = _channel_from_cfg(guild, cfg, "rules_channel_id", "rule_channel_id", "rules_text_channel_id")
    verify = _channel_from_cfg(guild, cfg, "verify_channel_id", "verification_channel_id")
    support = _channel_from_cfg(guild, cfg, "ticket_panel_channel_id", "support_channel_id", "panel_channel_id")

    rules_text = rules.mention if rules else "the rules channel"
    verify_text = verify.mention if verify else "this verification channel"
    support_text = support.mention if support else "the support/ticket channel"

    embed = discord.Embed(
        title=f"🎮 Welcome to {guild.name}! 🍁",
        description=(
            "Hey there! Thanks for dropping into the lobby. Grab a seat, light one up, and get ready to chill.\n\n"
            "Right now, your access to the server is **limited** because you are currently **Unverified**. "
            "To prevent bots and keep the vibes immaculate, unlock the full server first."
        ),
        color=discord.Color.green(),
    )
    embed.add_field(
        name="🔓 How to Get the Verified Role",
        value=(
            f"**Step 1:** Head over to {rules_text} and give the rules a quick read.\n"
            f"**Step 2:** Click the **Verify** button below in {verify_text}.\n"
            "**Step 3:** Dank Shield will automatically give you the **Verified** role and open the rest of the server."
        ),
        inline=False,
    )
    embed.add_field(
        name="⚠️ Need Help?",
        value=f"If the button is not working, head to {support_text} and open a ticket so a mod can get you sorted out.",
        inline=False,
    )
    embed.set_footer(text="Dank Shield Basic Verify")
    return embed


async def _reply(interaction: discord.Interaction, message: str, *, ok: bool) -> None:
    prefix = "✅ " if ok else "❌ "
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(prefix + message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.followup.send(prefix + message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception as exc:
        try:
            print(f"basic_verify reply failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass


class BasicVerifyButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Verify", emoji="✅", style=discord.ButtonStyle.success, custom_id=BASIC_VERIFY_CUSTOM_ID)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await _reply(interaction, "This only works inside the server.", ok=False)
            return
        try:
            ok, message = await apply_basic_verification(interaction.user)
            await _reply(interaction, message, ok=ok)
        except Exception as exc:
            try:
                print(f"basic_verify button failed guild={getattr(interaction.guild, 'id', 0)} user={getattr(interaction.user, 'id', 0)} error={type(exc).__name__}: {exc}")
            except Exception:
                pass
            await _reply(interaction, f"Basic verification failed: {type(exc).__name__}. Staff should check setup health.", ok=False)


class BasicVerifyView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(BasicVerifyButton())


def register_basic_verify_runtime(bot: Any) -> bool:
    global _RUNTIME_VIEW_REGISTERED
    if _RUNTIME_VIEW_REGISTERED:
        return True
    try:
        add_view = getattr(bot, "add_view", None)
        if callable(add_view):
            add_view(BasicVerifyView())
            _RUNTIME_VIEW_REGISTERED = True
            print("✅ basic_verify: persistent Basic Verify button view registered")
            return True
    except Exception as exc:
        try:
            print(f"⚠️ basic_verify: persistent view registration failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
    return False


async def post_basic_verify_panel(channel: discord.TextChannel, *, actor_id: int = 0) -> str:
    if not isinstance(channel, discord.TextChannel):
        return "invalid_channel"
    cfg = await get_guild_config(channel.guild.id, refresh=True)
    embed = build_basic_verify_embed(channel.guild, cfg)
    view = BasicVerifyView()

    try:
        me = channel.guild.me
        me_id = int(getattr(me, "id", 0) or 0)
        async for msg in channel.history(limit=80):
            if int(getattr(getattr(msg, "author", None), "id", 0) or 0) != me_id:
                continue
            if not msg.embeds:
                continue
            footer_text = str(getattr(getattr(msg.embeds[0], "footer", None), "text", "") or "")
            if BASIC_VERIFY_FOOTER in footer_text or footer_text == "Dank Shield Basic Verify":
                await msg.edit(embed=embed, view=view)
                return "updated"
    except Exception:
        pass

    await channel.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
    _ = actor_id
    return "posted"


async def apply_basic_verification(member: discord.Member) -> tuple[bool, str]:
    guild = member.guild
    cfg = await get_guild_config(guild.id, refresh=True)
    snap = snapshot_from_config(guild.id, cfg)

    verified = _role(guild, snap.verified_role_id)
    member_access = _role(guild, snap.effective_member_role_id)
    unverified = _role(guild, snap.unverified_role_id)

    roles_to_add = _dedupe_roles([verified, member_access])
    if not roles_to_add:
        return False, "Verified role is not configured. Staff should run `/dank setup` → Use My Existing Server → Roles."

    for role in roles_to_add + ([unverified] if isinstance(unverified, discord.Role) else []):
        if not isinstance(role, discord.Role):
            continue
        ok, why = _bot_can_manage_role(guild, role)
        if not ok:
            return False, why

    async with _lock_for(int(guild.id), int(member.id)):
        try:
            fresh = guild.get_member(member.id) or await guild.fetch_member(member.id)
        except Exception:
            fresh = member
        if not isinstance(fresh, discord.Member):
            return False, "Could not refresh your server member profile. Try again in a moment."

        add_now = [role for role in roles_to_add if role not in fresh.roles]
        remove_now = [unverified] if isinstance(unverified, discord.Role) and unverified in fresh.roles else []

        if not add_now and not remove_now:
            return True, "You are already verified. Welcome back!"

        try:
            final_roles = [role for role in fresh.roles if isinstance(role, discord.Role) and not role.is_default() and role not in remove_now]
            for role in add_now:
                if role not in final_roles:
                    final_roles.append(role)
            await fresh.edit(roles=final_roles, reason="Dank Shield basic button verification")
        except discord.Forbidden:
            return False, "Discord blocked the role update. Staff should move Dank Shield above Verified/Unverified and grant Manage Roles."
        except Exception:
            try:
                if add_now:
                    await fresh.add_roles(*add_now, reason="Dank Shield basic button verification")
                if remove_now:
                    await fresh.remove_roles(*remove_now, reason="Dank Shield basic button verification cleanup")
            except discord.Forbidden:
                return False, "Discord blocked the role update. Staff should move Dank Shield above Verified/Unverified and grant Manage Roles."
            except Exception as exc:
                return False, f"Verification failed: {type(exc).__name__}. Staff should check bot role hierarchy and setup."

        added = ", ".join(role.mention for role in add_now) if add_now else "already had access role"
        removed = f" Removed {unverified.mention}." if remove_now and isinstance(unverified, discord.Role) else ""
        return True, f"You are verified! Added {added}.{removed}"


async def maybe_handle_basic_verify_interaction(interaction: discord.Interaction) -> bool:
    try:
        data = getattr(interaction, "data", None) or {}
        custom_id = str(data.get("custom_id") or "")
        if custom_id != BASIC_VERIFY_CUSTOM_ID:
            return False
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await _reply(interaction, "This only works inside the server.", ok=False)
            return True
        ok, message = await apply_basic_verification(interaction.user)
        await _reply(interaction, message, ok=ok)
        return True
    except Exception as exc:
        try:
            print(f"basic_verify interaction failed guild={getattr(getattr(interaction, 'guild', None), 'id', 0)} user={getattr(getattr(interaction, 'user', None), 'id', 0)} error={type(exc).__name__}: {exc}")
        except Exception:
            pass
        try:
            await _reply(interaction, f"Basic verification failed: {type(exc).__name__}. Staff should check setup health.", ok=False)
        except Exception:
            pass
        return True


__all__ = [
    "BasicVerifyView",
    "register_basic_verify_runtime",
    "apply_basic_verification",
    "post_basic_verify_panel",
    "maybe_handle_basic_verify_interaction",
]
