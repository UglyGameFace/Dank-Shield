from __future__ import annotations

"""Granular invite hard-block override controls for SpamGuard.

This adds a visible access-page option for the hard invite blocker. Server owners
can decide whether invite deletion should ignore the normal allow/exempt buckets:
exempt users/roles, invite-allowed roles, allowed channels, allowed invite codes,
and this-server invite codes.
"""

from typing import Any

import discord

_PATCHED = False
_ORIGINAL_DEFAULT: Any = None
_ORIGINAL_NORMALIZE: Any = None
_ORIGINAL_PAYLOAD: Any = None
_ORIGINAL_EMBED: Any = None
_ORIGINAL_VIEW_BUILD: Any = None

_OVERRIDE_KEYS: tuple[tuple[str, str], ...] = (
    ("invite_override_exempt_users_roles", "Exempt users/roles"),
    ("invite_override_allowed_roles", "Invite-allowed roles"),
    ("invite_override_allowed_channels", "Allowed channels"),
    ("invite_override_allowed_codes", "Allowed invite codes"),
    ("invite_override_own_server_invites", "This-server invite codes"),
)


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return bool(default)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled", "override", "block"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled", "allow"}:
            return False
    except Exception:
        pass
    return bool(default)


def _yes_no(value: Any) -> str:
    return "yes" if _safe_bool(value, False) else "no"


def _parse_yes_no(value: Any, *, label: str) -> bool:
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y", "on", "enabled", "override", "block"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disabled", "allow"}:
        return False
    raise ValueError(f"{label} must be yes/no.")


def _override_summary(settings: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, label in _OVERRIDE_KEYS:
        state = "🚫 override" if _safe_bool(settings.get(key), False) else "✅ honor"
        lines.append(f"**{label}:** {state}")
    return "\n".join(lines)


def _patched_default_settings(guild_id: int) -> dict[str, Any]:
    data = dict(_ORIGINAL_DEFAULT(guild_id))
    for key, _label in _OVERRIDE_KEYS:
        data.setdefault(key, False)
    return data


def _patched_normalize_settings(guild_id: int, row: Any) -> dict[str, Any]:
    data = dict(_ORIGINAL_NORMALIZE(guild_id, row))
    source = row if isinstance(row, dict) else {}
    for key, _label in _OVERRIDE_KEYS:
        data[key] = _safe_bool(source.get(f"spam_{key}", source.get(key, data.get(key, False))), False)
    return data


def _patched_settings_payload_for_db(settings: dict[str, Any], *, updated_by: discord.Member | None = None) -> dict[str, Any]:
    payload = dict(_ORIGINAL_PAYLOAD(settings, updated_by=updated_by))
    for key, _label in _OVERRIDE_KEYS:
        payload[f"spam_{key}"] = bool(settings.get(key))
    return payload


def _patched_panel_embed(guild: discord.Guild, settings: dict[str, Any], *, page: str, persisted_hint: bool | None = None) -> discord.Embed:
    embed = _ORIGINAL_EMBED(guild, settings, page=page, persisted_hint=persisted_hint)
    if page == "access":
        try:
            embed.add_field(
                name="Invite Hard-Block Overrides",
                value=(
                    _override_summary(settings)
                    + "\n\n🚫 **override** means hard invite deletion ignores that allow/exempt bucket. "
                    "Use this when no one should be able to post Discord invites except after you turn the override back off."
                )[:1024],
                inline=False,
            )
        except Exception:
            pass
    return embed


class InviteOverrideModal(discord.ui.Modal, title="Invite Override Policy"):
    def __init__(self, guild_id: int, channel_id: int, message_id: int, return_page: str, settings: dict[str, Any]):
        super().__init__(timeout=300)
        self.guild_id = int(guild_id)
        self.channel_id = int(channel_id)
        self.message_id = int(message_id)
        self.return_page = return_page or "access"
        self.fields: dict[str, discord.ui.TextInput] = {}
        for key, label in _OVERRIDE_KEYS:
            item = discord.ui.TextInput(
                label=f"Override {label}",
                placeholder="yes = block anyway, no = honor this allow/exempt bucket",
                default=_yes_no(settings.get(key)),
                required=True,
                max_length=8,
            )
            self.fields[key] = item
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from stoney_verify import spam_guard

        if not await spam_guard._ensure_staff_panel_access(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await spam_guard._reply_ephemeral(interaction, "Invalid context.")
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
        try:
            patch = {key: _parse_yes_no(item.value, label=label) for key, label in _OVERRIDE_KEYS for item in [self.fields[key]]}
        except Exception as exc:
            return await spam_guard._reply_ephemeral(interaction, f"❌ {exc}")
        settings, persisted = await spam_guard.save_spam_settings(
            self.guild_id,
            patch,
            updated_by=interaction.user if isinstance(interaction.user, discord.Member) else None,
        )
        channel = guild.get_channel(self.channel_id)
        if isinstance(channel, discord.TextChannel):
            await spam_guard._rerender_panel_message(
                guild=guild,
                channel=channel,
                message_id=self.message_id,
                page=self.return_page,
                persisted_hint=persisted,
            )
        try:
            await interaction.followup.send(
                "✅ Invite override policy saved.\n"
                + _override_summary(settings)
                + f"\nPersistence: `{spam_guard._build_persistence_label(self.guild_id, persisted)}`",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            pass


class InviteOverrideButton(discord.ui.Button):
    def __init__(self, page: str):
        super().__init__(
            label="Invite Override",
            emoji="🚫",
            style=discord.ButtonStyle.danger,
            custom_id=f"spamguard:{page}:invite_override",
            row=1,
        )
        self.page = page

    async def callback(self, interaction: discord.Interaction) -> None:
        from stoney_verify import spam_guard

        if not await spam_guard._ensure_staff_panel_access(interaction):
            return
        guild = interaction.guild
        channel = interaction.channel
        message = interaction.message
        if guild is None or not isinstance(channel, discord.TextChannel) or message is None:
            return await spam_guard._reply_ephemeral(interaction, "Invalid context.")
        settings = spam_guard._fast_settings_for_ui(guild.id)
        await interaction.response.send_modal(InviteOverrideModal(guild.id, channel.id, message.id, self.page, settings))


def _patched_view_build(cls, *, page: str, settings: dict[str, Any]):
    view = _ORIGINAL_VIEW_BUILD(page=page, settings=settings)
    if page == "access":
        try:
            view.add_item(InviteOverrideButton(page))
        except Exception:
            pass
    return view


def apply() -> bool:
    global _PATCHED, _ORIGINAL_DEFAULT, _ORIGINAL_NORMALIZE, _ORIGINAL_PAYLOAD, _ORIGINAL_EMBED, _ORIGINAL_VIEW_BUILD
    if _PATCHED:
        return True
    try:
        from stoney_verify import spam_guard

        _ORIGINAL_DEFAULT = getattr(spam_guard, "_default_settings")
        _ORIGINAL_NORMALIZE = getattr(spam_guard, "_normalize_settings")
        _ORIGINAL_PAYLOAD = getattr(spam_guard, "_settings_payload_for_db")
        _ORIGINAL_EMBED = getattr(spam_guard, "_build_panel_embed")
        _ORIGINAL_VIEW_BUILD = getattr(spam_guard.SpamGuardPanelView, "build")

        spam_guard._default_settings = _patched_default_settings
        spam_guard._normalize_settings = _patched_normalize_settings
        spam_guard._settings_payload_for_db = _patched_settings_payload_for_db
        spam_guard._build_panel_embed = _patched_panel_embed
        spam_guard.SpamGuardPanelView.build = classmethod(_patched_view_build)

        _PATCHED = True
        print("✅ spam_guard_invite_override_options active; access page has Invite Override policy")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ spam_guard_invite_override_options failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
