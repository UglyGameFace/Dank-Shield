from __future__ import annotations

"""Make Invite Shield an actual toggle and add cleanup for existing invite links."""

import re
from typing import Any, Iterable

import discord

_PATCHED = False
_ORIGINAL_INIT: Any = None

INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord(?:app)?\.com\s*/\s*invite|discord\.gg)\s*/\s*[A-Za-z0-9-]+",
    re.IGNORECASE,
)
SPACED_INVITE_RE = re.compile(
    r"(?:discord\s*\.\s*gg|discord(?:app)?\s*\.\s*com\s*/\s*invite)\s*/\s*[A-Za-z0-9-]+",
    re.IGNORECASE,
)
INVITE_CODE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord(?:app)?\.com\s*/\s*invite|discord\.gg)\s*/\s*([A-Za-z0-9-]+)",
    re.IGNORECASE,
)
SPACED_CODE_RE = re.compile(
    r"(?:discord\s*\.\s*gg|discord(?:app)?\s*\.\s*com\s*/\s*invite)\s*/\s*([A-Za-z0-9-]+)",
    re.IGNORECASE,
)


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


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return bool(default)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled", "allow", "allowed"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled", "block", "blocked"}:
            return False
    except Exception:
        pass
    return bool(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        text = str(value or "").strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _normalize_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "")
    return text


def _component_text(component: Any) -> list[str]:
    parts: list[str] = []
    try:
        for attr in ("url", "label", "custom_id"):
            value = getattr(component, attr, None)
            if value:
                parts.append(str(value))
    except Exception:
        pass
    try:
        for child in list(getattr(component, "children", []) or []):
            parts.extend(_component_text(child))
    except Exception:
        pass
    return parts


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
            try:
                footer = getattr(embed, "footer", None)
                if getattr(footer, "text", None):
                    parts.append(str(footer.text))
            except Exception:
                pass
            try:
                author = getattr(embed, "author", None)
                if getattr(author, "name", None):
                    parts.append(str(author.name))
                if getattr(author, "url", None):
                    parts.append(str(author.url))
            except Exception:
                pass
    except Exception:
        pass
    try:
        for row in list(getattr(message, "components", []) or []):
            parts.extend(_component_text(row))
    except Exception:
        pass
    try:
        for attachment in list(getattr(message, "attachments", []) or []):
            for attr in ("url", "proxy_url", "filename", "description"):
                value = getattr(attachment, attr, None)
                if value:
                    parts.append(str(value))
    except Exception:
        pass
    return "\n".join(_normalize_text(part) for part in parts if part)


def _invite_codes(message: discord.Message) -> set[str]:
    text = _message_text(message)
    compact = re.sub(r"\s+", "", text)
    codes: set[str] = set()
    for source in (text, compact):
        try:
            codes.update(code.strip().lower() for code in INVITE_CODE_RE.findall(source) if code.strip())
            codes.update(code.strip().lower() for code in SPACED_CODE_RE.findall(source) if code.strip())
        except Exception:
            pass
    return codes


def _has_invite(message: discord.Message) -> bool:
    text = _message_text(message)
    if INVITE_RE.search(text) or SPACED_INVITE_RE.search(text):
        return True
    compact = re.sub(r"\s+", "", text)
    return bool(INVITE_RE.search(compact) or SPACED_INVITE_RE.search(compact))


def _normalize_codes(values: Any) -> set[str]:
    out: set[str] = set()
    try:
        source = values if isinstance(values, Iterable) and not isinstance(values, (str, bytes, dict)) else [values]
        for raw in source:
            text = str(raw or "").lower().strip().strip("/")
            text = text.replace("https://discord.gg/", "").replace("http://discord.gg/", "")
            text = text.replace("https://discord.com/invite/", "").replace("http://discord.com/invite/", "")
            text = text.replace("https://discordapp.com/invite/", "").replace("http://discordapp.com/invite/", "")
            if text:
                out.add(text)
    except Exception:
        pass
    return out


def _channel_id_from_text(value: Any) -> int:
    text = str(value or "").strip()
    text = text.strip("<#> ")
    match = re.search(r"(\d{15,25})", text)
    return _safe_int(match.group(1), 0) if match else 0


async def _resolve_text_channel(guild: discord.Guild, value: Any) -> discord.TextChannel | None:
    channel_id = _channel_id_from_text(value)
    if channel_id <= 0:
        return None
    channel = guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await guild.fetch_channel(channel_id)
        except Exception:
            channel = None
    return channel if isinstance(channel, discord.TextChannel) else None


async def _own_invite_codes(guild: discord.Guild) -> set[str]:
    try:
        from stoney_verify import spam_guard
        getter = getattr(spam_guard, "_fetch_guild_invite_codes", None)
        if callable(getter):
            return set(str(code).lower() for code in await getter(guild))
    except Exception:
        pass
    try:
        return {str(inv.code).lower() for inv in await guild.invites() if getattr(inv, "code", None)}
    except Exception:
        return set()


async def _spam_settings(guild: discord.Guild) -> dict[str, Any]:
    try:
        from stoney_verify import spam_guard
        return dict(await spam_guard.get_spam_settings(int(guild.id)))
    except Exception:
        return {}


async def _blocked_codes_for_guild(guild: discord.Guild, codes: set[str]) -> tuple[list[str], int]:
    settings = await _spam_settings(guild)
    allowed_codes = _normalize_codes(settings.get("allowed_invite_codes", settings.get("spam_allowed_invite_codes")))
    override_own = _safe_bool(settings.get("invite_override_own_server_invites", settings.get("spam_invite_override_own_server_invites")), False)
    allow_own = _safe_bool(settings.get("allow_server_invites", settings.get("spam_allow_server_invites")), True)
    own_codes: set[str] = set()
    if allow_own and not override_own:
        own_codes = await _own_invite_codes(guild)
    blocked = [code for code in sorted(codes) if code not in allowed_codes and code not in own_codes]
    allowed_count = max(0, len(codes) - len(blocked))
    return blocked, allowed_count


async def _delete_message(message: discord.Message, *, reason: str) -> None:
    try:
        await message.delete(reason=reason)
    except TypeError:
        await message.delete()


async def _clean_existing_invites(channel: Any, *, limit: int = 100) -> dict[str, Any]:
    result: dict[str, Any] = {"checked": 0, "matched": 0, "allowed": 0, "deleted": 0, "failed": 0, "warning": None}
    if not isinstance(channel, discord.TextChannel):
        result["warning"] = "This cleanup can only scan text channels."
        return result
    me = channel.guild.me
    if me is None:
        result["warning"] = "Dank Shield could not resolve its own server member, so permissions could not be checked."
        return result
    perms = channel.permissions_for(me)
    if not perms.read_message_history:
        result["warning"] = f"Dank Shield needs Read Message History in {channel.mention} to scan existing messages."
        return result
    if not perms.manage_messages:
        result["warning"] = f"Dank Shield needs Manage Messages in {channel.mention} to remove existing invite links."
        return result
    try:
        async for message in channel.history(limit=max(1, min(int(limit), 250))):
            result["checked"] += 1
            try:
                if message.author == me:
                    continue
                codes = _invite_codes(message)
                if not codes and not _has_invite(message):
                    continue
                result["matched"] += 1
                blocked, allowed_count = await _blocked_codes_for_guild(channel.guild, codes)
                if allowed_count:
                    result["allowed"] += 1
                if not blocked:
                    continue
                await _delete_message(message, reason="Dank Shield Invite Shield cleanup: external invite")
                result["deleted"] += 1
            except discord.Forbidden:
                result["failed"] += 1
                result["warning"] = f"Discord denied deletion in {channel.mention}. Check Manage Messages and channel permission overrides."
                break
            except discord.NotFound:
                continue
            except Exception as exc:
                result["failed"] += 1
                if result.get("warning") is None:
                    result["warning"] = f"Some matched messages could not be removed in {channel.mention}: {type(exc).__name__}: {str(exc)[:150]}"
                continue
    except discord.Forbidden:
        result["warning"] = f"Dank Shield cannot read message history in {channel.mention}."
    except Exception as exc:
        result["warning"] = f"Scan failed in {channel.mention}: {type(exc).__name__}: {str(exc)[:170]}"
    return result


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


def _scan_note(prefix: str, result: dict[str, Any]) -> str:
    note = (
        f"{prefix} checked `{int(result.get('checked') or 0)}` recent messages, "
        f"matched `{int(result.get('matched') or 0)}`, allowed internal/saved `{int(result.get('allowed') or 0)}`, "
        f"deleted external `{int(result.get('deleted') or 0)}`, failed `{int(result.get('failed') or 0)}`."
    )
    warning = result.get("warning")
    if warning:
        note += f"\n⚠️ {warning}"
    if int(result.get("matched") or 0) and not int(result.get("deleted") or 0) and not int(result.get("allowed") or 0) and not warning:
        note += "\n⚠️ Matches were found but nothing was removed. Check message age, permissions, and channel overrides."
    return note


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
        result = await _clean_existing_invites(interaction.channel, limit=150)
        note += "\n" + _scan_note("Auto-clean command channel", result)
    await _refresh_card(center, interaction, note=note)


class InviteShieldToggle(discord.ui.Button):
    def __init__(self, enabled: bool) -> None:
        super().__init__(label=f"Invite Shield: {'ON' if enabled else 'OFF'}", emoji="🚫", style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary, custom_id="dank_protection:invite_shield_toggle", row=1)
        self.enabled = bool(enabled)

    async def callback(self, interaction: discord.Interaction) -> None:
        await _set_invite_shield(interaction, enabled=not self.enabled, scan_current=not self.enabled)


class CleanCurrentChannelInvites(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Clean Command Channel", emoji="🧹", style=discord.ButtonStyle.secondary, custom_id="dank_protection:clean_current_channel_invites", row=3)

    async def callback(self, interaction: discord.Interaction) -> None:
        from stoney_verify.commands_ext import public_protection_center as center
        if not await center._require_setup_permission(interaction):
            return
        result = await _clean_existing_invites(interaction.channel, limit=200)
        await _refresh_card(center, interaction, note=_scan_note("🧹 Clean Command Channel", result))


class TargetChannelCleanupModal(discord.ui.Modal, title="Clean Invite Links in Channel"):
    def __init__(self) -> None:
        super().__init__(timeout=300)
        self.channel_id = discord.ui.TextInput(
            label="Channel mention or ID to scan",
            placeholder="Example: #bot-commands or 123456789012345678",
            required=True,
            max_length=120,
        )
        self.limit = discord.ui.TextInput(
            label="Recent messages to check",
            placeholder="Default 200, max 250",
            default="200",
            required=False,
            max_length=4,
        )
        self.add_item(self.channel_id)
        self.add_item(self.limit)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from stoney_verify.commands_ext import public_protection_center as center
        if not await center._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await center._send_ephemeral(interaction, "❌ This must be used inside a server.")
        channel = await _resolve_text_channel(guild, self.channel_id.value)
        if channel is None:
            return await _refresh_card(center, interaction, note="⚠️ I could not find that text channel. Paste a channel mention like `<#123>` or the raw channel ID.")
        limit = max(1, min(_safe_int(self.limit.value, 200), 250))
        result = await _clean_existing_invites(channel, limit=limit)
        await _refresh_card(center, interaction, note=_scan_note(f"🧹 Clean {channel.mention}", result))


class CleanTargetChannelInvites(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Clean Selected Channel", emoji="🎯", style=discord.ButtonStyle.primary, custom_id="dank_protection:clean_target_channel_invites", row=3)

    async def callback(self, interaction: discord.Interaction) -> None:
        from stoney_verify.commands_ext import public_protection_center as center
        if not await center._require_setup_permission(interaction):
            return
        await interaction.response.send_modal(TargetChannelCleanupModal())


def _patch_view() -> bool:
    global _ORIGINAL_INIT
    try:
        from stoney_verify.commands_ext import public_protection_center as center
        if _ORIGINAL_INIT is None:
            _ORIGINAL_INIT = center.ProtectionCenterView.__init__

        def patched_init(self: Any, *, author_id: int) -> None:
            _ORIGINAL_INIT(self, author_id=author_id)
            enabled = False
            try:
                from stoney_verify.startup_guards import protection_center_embed_refresh_guard as live
                enabled = bool(getattr(live, "_LAST", {}).get("invites"))
            except Exception:
                pass
            for child in list(getattr(self, "children", []) or []):
                cid = str(getattr(child, "custom_id", "") or "")
                if cid in {
                    "dank_protection:invite_scope",
                    "dank_protection:block_invites",
                    "dank_protection:invite_shield_toggle",
                    "dank_protection:clean_existing_invites",
                    "dank_protection:clean_current_channel_invites",
                    "dank_protection:clean_target_channel_invites",
                }:
                    try:
                        self.remove_item(child)
                    except Exception:
                        pass
            try:
                self.add_item(InviteShieldToggle(enabled))
                self.add_item(CleanCurrentChannelInvites())
                self.add_item(CleanTargetChannelInvites())
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
    print("✅ protection_invite_toggle_cleanup_guard active; Invite Shield cleanup supports target channels and safe delete fallback" if ok else "⚠️ protection_invite_toggle_cleanup_guard loaded but view patch was delayed")
    return ok


apply()

__all__ = ["apply"]