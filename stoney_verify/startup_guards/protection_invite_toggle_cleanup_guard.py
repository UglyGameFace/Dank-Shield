from __future__ import annotations

"""Make Invite Shield an actual toggle and add cleanup for existing invite links."""

import re
from typing import Any

import discord

_PATCHED = False
_ORIGINAL_INIT: Any = None

INVITE_RE = re.compile(r"(?:https?://)?(?:www\.)?(?:discord(?:app)?\.com/invite|discord\.gg)\s*/\s*[A-Za-z0-9-]+", re.IGNORECASE)


def _cfg_bool(center: Any, cfg: Any, key: str, default: bool = False) -> bool:
    try:
        return bool(center._cfg_bool(cfg, key, default))
    except Exception:
        raw = default
        try:
            raw = center._cfg_value(cfg, key, default)
        except Exception:
            pass
        if isinstance(raw, bool):
            return raw
        return str(raw or "").strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}


def _message_text(message: discord.Message) -> str:
    parts = [str(getattr(message, "content", "") or "")]
    try:
        for embed in list(getattr(message, "embeds", []) or []):
            for attr in ("title", "description", "url"):
                value = getattr(embed, attr, None)
                if value:
                    parts.append(str(value))
            for field in list(getattr(embed, "fields", []) or []):
                parts.append(str(getattr(field, "name", "") or ""))
                parts.append(str(getattr(field, "value", "") or ""))
    except Exception:
        pass
    return "\n".join(part for part in parts if part)


async def _clean_existing_invites(channel: Any, *, limit: int = 100) -> tuple[int, int, str | None]:
    if not isinstance(channel, discord.TextChannel):
        return 0, 0, "This cleanup can only scan text channels."
    if not channel.permissions_for(channel.guild.me).read_message_history:
        return 0, 0, "Dank Shield needs Read Message History to scan existing messages."
    checked = 0
    deleted = 0
    denied: str | None = None
    try:
        async for message in channel.history(limit=max(1, min(int(limit), 250))):
            checked += 1
            try:
                if message.author == channel.guild.me:
                    continue
                if not INVITE_RE.search(_message_text(message)):
                    continue
                await message.delete(reason="Dank Shield Invite Shield cleanup")
                deleted += 1
            except discord.Forbidden:
                denied = "Dank Shield needs Manage Messages to delete existing invite links."
                break
            except discord.NotFound:
                continue
            except Exception:
                continue
    except discord.Forbidden:
        return checked, deleted, "Dank Shield cannot read message history in this channel."
    except Exception as exc:
        return checked, deleted, f"Scan failed: {type(exc).__name__}: {str(exc)[:180]}"
    return checked, deleted, denied


async def _refresh_card(center: Any, interaction: discord.Interaction, *, note: str | None = None) -> None:
    try:
        guild = interaction.guild
        if guild is None:
            return await center._send_ephemeral(interaction, "❌ This must be used inside a server.")
        cfg = await center.get_guild_config(int(guild.id), refresh=True)
        spam, source = await center._load_spam_settings(int(guild.id))
        embed = center._protection_embed(guild, cfg, spam, source)
        if note:
            embed.add_field(name="Last action", value=note[:1024], inline=False)
        view = center.ProtectionCenterView(author_id=int(interaction.user.id))
        if interaction.response.is_done():
            await interaction.edit_original_response(content=None, embed=embed, view=view)
        else:
            await interaction.response.edit_message(content=None, embed=embed, view=view)
    except Exception:
        try:
            await interaction.followup.send(note or "Protection Center updated.", ephemeral=True)
        except Exception:
            pass


async def _set_invite_shield(interaction: discord.Interaction, *, enabled: bool, scan_current: bool = False) -> None:
    from stoney_verify.commands_ext import public_protection_center as center
    if not await center._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await center._send_ephemeral(interaction, "❌ This must be used inside a server.")
    cfg = await center.get_guild_config(int(guild.id), refresh=True)
    link_on = _cfg_bool(center, cfg, "automod_block_links", False)
    updates = {
        "automod_enabled": bool(enabled or link_on),
        "automod_block_invites": bool(enabled),
        "automod_block_links": bool(link_on),
        "automod_link_policy": "link_lockdown" if link_on else "invite_shield" if enabled else "allow_links",
        "automod_updated_by_id": str(int(interaction.user.id)),
    }
    await center._save_automod(int(guild.id), updates)
    note = "✅ Invite Shield enabled. Normal links remain allowed." if enabled else "⚪ Invite Shield disabled."
    if enabled and scan_current:
        checked, deleted, warning = await _clean_existing_invites(interaction.channel, limit=100)
        note += f"\nCleaned current channel: `{deleted}` invite message(s) removed after checking `{checked}` recent messages."
        if warning:
            note += f"\n⚠️ {warning}"
    await _refresh_card(center, interaction, note=note)


class InviteShieldToggle(discord.ui.Button):
    def __init__(self, enabled: bool) -> None:
        super().__init__(label=f"Invite Shield: {'ON' if enabled else 'OFF'}", emoji="🚫", style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary, custom_id="dank_protection:invite_shield_toggle", row=1)
        self.enabled = bool(enabled)

    async def callback(self, interaction: discord.Interaction) -> None:
        await _set_invite_shield(interaction, enabled=not self.enabled, scan_current=not self.enabled)


class CleanExistingInvites(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Clean Existing Invites", emoji="🧹", style=discord.ButtonStyle.secondary, custom_id="dank_protection:clean_existing_invites", row=3)

    async def callback(self, interaction: discord.Interaction) -> None:
        from stoney_verify.commands_ext import public_protection_center as center
        if not await center._require_setup_permission(interaction):
            return
        checked, deleted, warning = await _clean_existing_invites(interaction.channel, limit=150)
        note = f"🧹 Clean Existing Invites checked `{checked}` recent messages and removed `{deleted}` invite message(s) in this channel."
        if warning:
            note += f"\n⚠️ {warning}"
        await _refresh_card(center, interaction, note=note)


def _patch_view() -> bool:
    global _ORIGINAL_INIT
    try:
        from stoney_verify.commands_ext import public_protection_center as center
        if _ORIGINAL_INIT is None:
            _ORIGINAL_INIT = center.ProtectionCenterView.__init__

        def patched_init(self: Any, *, author_id: int) -> None:
            _ORIGINAL_INIT(self, author_id=author_id)
            try:
                state = getattr(center, "_LAST", None)
            except Exception:
                state = None
            enabled = False
            try:
                # protection_center_embed_refresh_guard stores last state in its module.
                from stoney_verify.startup_guards import protection_center_embed_refresh_guard as live
                enabled = bool(getattr(live, "_LAST", {}).get("invites"))
            except Exception:
                pass
            for child in list(getattr(self, "children", []) or []):
                cid = str(getattr(child, "custom_id", "") or "")
                if cid in {"dank_protection:invite_scope", "dank_protection:block_invites", "dank_protection:invite_shield_toggle"}:
                    try:
                        self.remove_item(child)
                    except Exception:
                        pass
            try:
                self.add_item(InviteShieldToggle(enabled))
                self.add_item(CleanExistingInvites())
            except Exception:
                pass

        center.ProtectionCenterView.__init__ = patched_init
        return True
    except Exception:
        return False


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        _patch_view()
        return True
    ok = _patch_view()
    _PATCHED = True
    print("✅ protection_invite_toggle_cleanup_guard active; Invite Shield toggles independently and can clean existing invites" if ok else "⚠️ protection_invite_toggle_cleanup_guard loaded but view patch was delayed")
    return ok


apply()

__all__ = ["apply"]