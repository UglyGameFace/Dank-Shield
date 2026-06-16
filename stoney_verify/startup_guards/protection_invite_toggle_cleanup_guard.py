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


def _cfg_id(center: Any, cfg: Any, *keys: str) -> int:
    for key in keys:
        try:
            raw = center._cfg_value(cfg, key, None)
            cid = _channel_id_from_text(raw)
            if cid > 0:
                return cid
        except Exception:
            continue
    return 0


def _channel_perm_status(channel: discord.TextChannel) -> str:
    try:
        me = channel.guild.me
        if me is None:
            return "bot member unknown"
        perms = channel.permissions_for(me)
        read = "✅" if perms.read_messages else "❌"
        history = "✅" if perms.read_message_history else "❌"
        manage = "✅" if perms.manage_messages else "❌"
        active = "✅" if (perms.read_messages and perms.read_message_history and perms.manage_messages) else "⚠️"
        return f"Active {active} • View {read} • History {history} • Manage Messages {manage}"
    except Exception:
        return "permission check unavailable"


async def _configured_command_channel(guild: discord.Guild, cfg: Any, center: Any) -> discord.TextChannel | None:
    channel_id = _cfg_id(
        center,
        cfg,
        "invite_scan_channel_id",
        "automod_invite_scan_channel_id",
        "staff_command_channel_id",
        "staff_commands_channel_id",
        "command_channel_id",
        "commands_channel_id",
        "bot_command_channel_id",
        "bot_commands_channel_id",
        "setup_command_channel_id",
        "setup_commands_channel_id",
        "staff_channel_id",
    )
    if channel_id <= 0:
        return None
    channel = guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await guild.fetch_channel(channel_id)
        except Exception:
            channel = None
    return channel if isinstance(channel, discord.TextChannel) else None


async def _active_invite_scan_channel(interaction: discord.Interaction) -> discord.TextChannel | None:
    from stoney_verify.commands_ext import public_protection_center as center

    guild = interaction.guild
    if guild is None:
        return None

    cfg = await center.get_guild_config(int(guild.id), refresh=True)
    configured = await _configured_command_channel(guild, cfg, center)
    if configured is not None:
        return configured

    channel = getattr(interaction, "channel", None)
    if isinstance(channel, discord.TextChannel):
        return channel

    channel_id = _safe_int(getattr(interaction, "channel_id", 0), 0)
    if channel_id > 0:
        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except Exception:
                channel = None
        if isinstance(channel, discord.TextChannel):
            return channel

    return None


async def _save_invite_scan_channel(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    from stoney_verify.commands_ext import public_protection_center as center

    guild = interaction.guild
    if guild is None:
        return

    await center._save_automod(
        int(guild.id),
        {
            "automod_invite_scan_channel_id": str(int(channel.id)),
            "invite_scan_channel_id": str(int(channel.id)),
            "automod_updated_by_id": str(int(interaction.user.id)),
        },
    )


def _scan_channel_options(guild: discord.Guild, active_channel_id: int = 0) -> list[discord.SelectOption]:
    rows = []
    me = guild.me

    for channel in list(getattr(guild, "text_channels", []) or []):
        try:
            if me is None:
                can_view = True
                can_history = True
                can_manage = False
            else:
                perms = channel.permissions_for(me)
                can_view = bool(perms.read_messages)
                can_history = bool(perms.read_message_history)
                can_manage = bool(perms.manage_messages)

            if not can_view:
                continue

            score = 0
            name_l = str(channel.name or "").lower()

            if int(channel.id) == int(active_channel_id):
                score += 1000
            if any(token in name_l for token in ("staff", "command", "cmd", "mod", "log", "admin")):
                score += 50
            if can_history:
                score += 10
            if can_manage:
                score += 10

            label = f"#{channel.name}"[:100]
            description = (
                ("ACTIVE • " if int(channel.id) == int(active_channel_id) else "")
                + f"history {'yes' if can_history else 'no'} • delete {'yes' if can_manage else 'no'}"
            )[:100]

            rows.append(
                (
                    score,
                    channel.position,
                    discord.SelectOption(
                        label=label,
                        value=str(int(channel.id)),
                        description=description,
                    ),
                )
            )
        except Exception:
            continue

    rows.sort(key=lambda row: (-row[0], row[1]))
    return [option for _score, _pos, option in rows[:25]]


def _interaction_source_text_channel(interaction: discord.Interaction) -> discord.TextChannel | None:
    try:
        message = getattr(interaction, "message", None)
        channel = getattr(message, "channel", None)
        if isinstance(channel, discord.TextChannel):
            return channel
    except Exception:
        pass

    try:
        channel = getattr(interaction, "channel", None)
        if isinstance(channel, discord.TextChannel):
            return channel
    except Exception:
        pass

    return None


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
    checked = int(result.get("checked") or 0)
    matched = int(result.get("matched") or 0)
    allowed = int(result.get("allowed") or 0)
    deleted = int(result.get("deleted") or 0)
    failed = int(result.get("failed") or 0)

    note = (
        f"{prefix} checked `{checked}` recent messages, "
        f"matched `{matched}`, allowed internal/saved `{allowed}`, "
        f"deleted external `{deleted}`, failed `{failed}`."
    )
    warning = result.get("warning")
    if warning:
        note += f"\n⚠️ {warning}"
    if checked == 0 and not warning:
        note += "\n⚠️ Discord returned 0 recent messages from the active scan channel. If this is not the channel you expected, press **Choose Scan Channel** and re-select it. If it is correct, there may simply be no recent visible messages in the scan window."
    if matched and not deleted and not allowed and not warning:
        note += "\n⚠️ Matches were found but nothing was removed. Check message age, permissions, and channel overrides."
    return note


async def _active_scan_note(interaction: discord.Interaction) -> str:
    from stoney_verify.commands_ext import public_protection_center as center

    channel = await _active_invite_scan_channel(interaction)
    if not isinstance(channel, discord.TextChannel):
        return "⚠️ Active invite scan channel: not set. Use **Choose Scan Channel**."

    try:
        cfg = await center.get_guild_config(int(channel.guild.id), refresh=True)
        configured = await _configured_command_channel(channel.guild, cfg, center)
        source = "saved/configured" if configured and int(configured.id) == int(channel.id) else "current interaction"
    except Exception:
        source = "resolved"

    return f"📍 Active invite scan channel: {channel.mention}\n`{_channel_perm_status(channel)}`\nSource: `{source}`"


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
        channel = await _active_invite_scan_channel(interaction)
        result = await _clean_existing_invites(channel, limit=150)
        prefix = f"Invite scan {channel.mention}" if isinstance(channel, discord.TextChannel) else "Invite scan active channel"
        note += "\n" + _scan_note(prefix, result)
    note += "\n" + await _active_scan_note(interaction)
    await _refresh_card(center, interaction, note=note)


class InviteShieldToggle(discord.ui.Button):
    def __init__(self, enabled: bool) -> None:
        super().__init__(label=f"Invite Shield: {'ON' if enabled else 'OFF'}", emoji="🚫", style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary, custom_id="dank_protection:invite_shield_toggle", row=1)
        self.enabled = bool(enabled)

    async def callback(self, interaction: discord.Interaction) -> None:
        await _set_invite_shield(interaction, enabled=not self.enabled, scan_current=not self.enabled)


class InviteScanChannelSelect(discord.ui.Select):
    def __init__(self, *, guild: discord.Guild, active_channel_id: int = 0) -> None:
        options = _scan_channel_options(guild, active_channel_id)
        if not options:
            options = [
                discord.SelectOption(
                    label="No visible text channels",
                    value="0",
                    description="Give Dank Shield access to the staff command channel.",
                )
            ]
        super().__init__(
            placeholder="Choose the active invite scan channel...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="dank_protection:invite_scan_channel_select",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from stoney_verify.commands_ext import public_protection_center as center

        if not await center._require_setup_permission(interaction):
            return

        guild = interaction.guild
        if guild is None:
            return await center._send_ephemeral(interaction, "❌ This must be used inside a server.")

        channel_id = _safe_int((self.values or ["0"])[0], 0)
        channel = guild.get_channel(channel_id)
        if channel is None and channel_id > 0:
            try:
                channel = await guild.fetch_channel(channel_id)
            except Exception:
                channel = None

        if not isinstance(channel, discord.TextChannel):
            return await center._send_ephemeral(
                interaction,
                "❌ That channel could not be selected. Use the ID fallback if it is hidden from the picker.",
            )

        await _save_invite_scan_channel(interaction, channel)
        note = f"✅ Active invite scan channel set to {channel.mention}.\n`{_channel_perm_status(channel)}`"
        await _refresh_card(center, interaction, note=note)


class InviteScanChannelPickerView(discord.ui.View):
    def __init__(self, *, guild: discord.Guild, active_channel_id: int = 0) -> None:
        super().__init__(timeout=180)
        self.add_item(InviteScanChannelSelect(guild=guild, active_channel_id=active_channel_id))


class ChooseInviteScanChannel(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Choose Scan Channel",
            emoji="🎯",
            style=discord.ButtonStyle.primary,
            custom_id="dank_protection:choose_invite_scan_channel",
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from stoney_verify.commands_ext import public_protection_center as center

        if not await center._require_setup_permission(interaction):
            return

        guild = interaction.guild
        if guild is None:
            return await center._send_ephemeral(interaction, "❌ This must be used inside a server.")

        active = await _active_invite_scan_channel(interaction)
        active_id = int(active.id) if isinstance(active, discord.TextChannel) else 0
        await interaction.response.send_message(
            content=(
                "🎯 Choose the active channel for Invite Shield scans.\n"
                "This picker is built by Dank Shield from channels the bot can see, so private staff channels can appear if Dank Shield has access."
            ),
            view=InviteScanChannelPickerView(guild=guild, active_channel_id=active_id),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class CleanCurrentChannelInvites(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Scan Active Channel",
            emoji="🔍",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_protection:scan_active_invite_channel",
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from stoney_verify.commands_ext import public_protection_center as center
        if not await center._require_setup_permission(interaction):
            return

        channel = await _active_invite_scan_channel(interaction)
        if channel is None:
            return await _refresh_card(
                center,
                interaction,
                note="⚠️ Active invite scan channel is not set. Press **Choose Scan Channel** and select the private channel Dank Shield should scan.",
            )

        result = await _clean_existing_invites(channel, limit=200)
        note = _scan_note(f"🔍 Invite scan {channel.mention}", result)
        note += "\n" + await _active_scan_note(interaction)
        await _refresh_card(center, interaction, note=note)


class TargetChannelCleanupModal(discord.ui.Modal, title="Scan Invite Links in Channel"):
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
        await _refresh_card(center, interaction, note=_scan_note(f"🔍 Invite scan {channel.mention}", result))


class CleanTargetChannelInvites(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Scan by ID / Mention", emoji="🆔", style=discord.ButtonStyle.secondary, custom_id="dank_protection:scan_invite_channel_by_id", row=4)

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
                    "dank_protection:scan_active_invite_channel",
                    "dank_protection:clean_target_channel_invites",
                    "dank_protection:scan_invite_channel_by_id",
                    "dank_protection:choose_invite_scan_channel",
                }:
                    try:
                        self.remove_item(child)
                    except Exception:
                        pass
            try:
                self.add_item(InviteShieldToggle(enabled))
                self.add_item(ChooseInviteScanChannel())
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