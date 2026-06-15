from __future__ import annotations

"""Granular invite hard-block controls for SpamGuard.

Adds visible Access-page controls for:
- override allow/exempt buckets
- target all-bots toggle
- target bot/user IDs
- target channel IDs

Overrides answer: should the hard blocker ignore normal allow/exempt buckets?
Scope answers: where/who should the hard blocker target?
"""

import re
from typing import Any

import discord

_PATCHED = False
_ORIGINAL_DEFAULT: Any = None
_ORIGINAL_NORMALIZE: Any = None
_ORIGINAL_PAYLOAD: Any = None
_ORIGINAL_GET: Any = None
_ORIGINAL_SAVE: Any = None
_ORIGINAL_EMBED: Any = None
_ORIGINAL_VIEW_BUILD: Any = None

_OVERRIDE_KEYS: tuple[tuple[str, str], ...] = (
    ("invite_override_exempt_users_roles", "Exempt users/roles"),
    ("invite_override_allowed_roles", "Invite-allowed roles"),
    ("invite_override_allowed_channels", "Allowed channels"),
    ("invite_override_allowed_codes", "Allowed invite codes"),
    ("invite_override_own_server_invites", "This-server invite codes"),
)
_SCOPE_BOOL_KEYS: tuple[tuple[str, str], ...] = (
    ("invite_hard_block_target_all_bots", "All bots"),
)
_SCOPE_ID_KEYS: tuple[tuple[str, str], ...] = (
    ("invite_hard_block_target_bot_ids", "Target bot/user IDs"),
    ("invite_hard_block_target_channel_ids", "Target channel IDs"),
)
# Backward-compatible name used by Protection Center guard code.
_SCOPE_KEYS = _SCOPE_ID_KEYS
_ALL_KEYS = (
    tuple(key for key, _label in _OVERRIDE_KEYS)
    + tuple(key for key, _label in _SCOPE_BOOL_KEYS)
    + tuple(key for key, _label in _SCOPE_ID_KEYS)
)


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return bool(default)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled", "override", "block", "all"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled", "allow", "none"}:
            return False
    except Exception:
        pass
    return bool(default)


def _yes_no(value: Any) -> str:
    return "yes" if _safe_bool(value, False) else "no"


def _parse_yes_no(value: Any, *, label: str) -> bool:
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y", "on", "enabled", "override", "block", "all"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disabled", "allow", "none"}:
        return False
    raise ValueError(f"{label} must be yes/no.")


def _parse_ids(value: Any) -> list[str]:
    text = str(value or "")
    ids: list[str] = []
    for part in re.split(r"[\s,;]+", text):
        item = part.strip().strip("<@#!&>")
        if item.isdigit() and item not in ids:
            ids.append(item)
    return ids[:100]


def _ids_text(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(x) for x in value if str(x).strip())
    return str(value or "")


def _cfg_value(cfg: Any, key: str) -> Any:
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
            if isinstance(nested, dict) and nested.get(key) is not None:
                return nested.get(key)
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, dict) and nested.get(key) is not None:
                    return nested.get(key)
        except Exception:
            pass
    return None


def _setting_raw(source: Any, key: str) -> Any:
    raw = source.get(f"spam_{key}", source.get(key)) if isinstance(source, dict) else None
    if raw is None:
        raw = _cfg_value(source, f"spam_{key}")
    if raw is None:
        raw = _cfg_value(source, key)
    return raw


def _merge_invite_policy(settings: dict[str, Any], source: Any) -> dict[str, Any]:
    data = dict(settings or {})
    for key, _label in _OVERRIDE_KEYS:
        raw = _setting_raw(source, key)
        data[key] = _safe_bool(raw, False) if raw is not None else _safe_bool(data.get(key), False)
    for key, _label in _SCOPE_BOOL_KEYS:
        raw = _setting_raw(source, key)
        data[key] = _safe_bool(raw, False) if raw is not None else _safe_bool(data.get(key), False)
    for key, _label in _SCOPE_ID_KEYS:
        raw = _setting_raw(source, key)
        data[key] = _parse_ids(raw if raw is not None else data.get(key))
    return data


def _override_summary(settings: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, label in _OVERRIDE_KEYS:
        state = "🚫 override" if _safe_bool(settings.get(key), False) else "✅ honor"
        lines.append(f"**{label}:** {state}")
    return "\n".join(lines)


def _scope_summary(settings: dict[str, Any]) -> str:
    all_bots = _safe_bool(settings.get("invite_hard_block_target_all_bots"), False)
    bot_ids = _parse_ids(settings.get("invite_hard_block_target_bot_ids"))
    channel_ids = _parse_ids(settings.get("invite_hard_block_target_channel_ids"))
    bot_line = "All bots included" if all_bots else "Humans only; bots ignored unless listed"
    if bot_ids:
        bot_line += "; listed IDs: " + ", ".join(f"`{x}`" for x in bot_ids)
    return (
        f"**All bots:** {'yes' if all_bots else 'no'}\n"
        f"**Target bot/user IDs:** {bot_line}\n"
        f"**Target channel IDs:** {', '.join(f'`{x}`' for x in channel_ids) if channel_ids else 'All message channels'}"
    )


def _patched_default_settings(guild_id: int) -> dict[str, Any]:
    return _merge_invite_policy(dict(_ORIGINAL_DEFAULT(guild_id)), {})


def _patched_normalize_settings(guild_id: int, row: Any) -> dict[str, Any]:
    return _merge_invite_policy(dict(_ORIGINAL_NORMALIZE(guild_id, row)), row)


def _patched_settings_payload_for_db(settings: dict[str, Any], *, updated_by: discord.Member | None = None) -> dict[str, Any]:
    # Keep SQL payload unchanged for compatibility. The new policy is persisted
    # through guild_configs so deployments without new DB columns do not break.
    return dict(_ORIGINAL_PAYLOAD(settings, updated_by=updated_by))


async def _load_policy_config(guild_id: int) -> dict[str, Any]:
    try:
        from stoney_verify.guild_config import get_guild_config

        cfg = await get_guild_config(int(guild_id), refresh=True)
        out: dict[str, Any] = {}
        for key in _ALL_KEYS:
            raw = _cfg_value(cfg, f"spam_{key}")
            if raw is None:
                raw = _cfg_value(cfg, key)
            if raw is not None:
                out[key] = raw
        return _merge_invite_policy({}, out)
    except Exception:
        return {}


async def _save_policy_config(guild_id: int, patch: dict[str, Any]) -> bool:
    wanted: dict[str, Any] = {}
    for key, _label in _OVERRIDE_KEYS:
        if key in patch:
            wanted[key] = bool(patch[key])
    for key, _label in _SCOPE_BOOL_KEYS:
        if key in patch:
            wanted[key] = _safe_bool(patch[key], False)
    for key, _label in _SCOPE_ID_KEYS:
        if key in patch:
            wanted[key] = _parse_ids(patch[key])
    if not wanted:
        return True
    payload: dict[str, Any] = {}
    for key, value in wanted.items():
        payload[key] = value
        payload[f"spam_{key}"] = value
    try:
        from stoney_verify.guild_config import invalidate_guild_config, upsert_guild_config

        await upsert_guild_config(int(guild_id), payload)
        invalidate_guild_config(int(guild_id))
        return True
    except Exception:
        return False


async def _patched_get_spam_settings(guild_id: int) -> dict[str, Any]:
    settings = dict(await _ORIGINAL_GET(int(guild_id)))
    policy = await _load_policy_config(int(guild_id))
    if policy:
        settings.update(policy)
        try:
            from stoney_verify import spam_guard
            spam_guard._cache_runtime_settings(int(guild_id), settings, source="db+guild_config", persisted=True)
        except Exception:
            pass
    return _merge_invite_policy(settings, policy)


async def _patched_save_spam_settings(guild_id: int, patch: dict[str, Any], *, updated_by: discord.Member | None = None):
    raw_patch = dict(patch or {})
    policy_patch = {key: raw_patch[key] for key in _ALL_KEYS if key in raw_patch}
    settings, persisted = await _ORIGINAL_SAVE(int(guild_id), raw_patch, updated_by=updated_by)
    if policy_patch:
        policy_saved = await _save_policy_config(int(guild_id), policy_patch)
        settings = _merge_invite_policy(dict(settings or {}), policy_patch)
        try:
            from stoney_verify import spam_guard
            spam_guard._cache_runtime_settings(int(guild_id), settings, source="db+guild_config" if policy_saved else "runtime", persisted=bool(persisted or policy_saved))
        except Exception:
            pass
        persisted = bool(persisted or policy_saved)
    return settings, bool(persisted)


def _patched_panel_embed(guild: discord.Guild, settings: dict[str, Any], *, page: str, persisted_hint: bool | None = None) -> discord.Embed:
    embed = _ORIGINAL_EMBED(guild, settings, page=page, persisted_hint=persisted_hint)
    if page == "access":
        try:
            embed.add_field(
                name="Invite Hard-Block Overrides",
                value=(_override_summary(settings) + "\n\n🚫 **override** means hard invite deletion ignores that allow/exempt bucket.")[:1024],
                inline=False,
            )
            embed.add_field(
                name="Invite Hard-Block Scope",
                value=(
                    _scope_summary(settings)
                    + "\n\nUse **Invite Scope** to include all bots, target exact bot/user IDs, or target exact channel IDs. "
                    + "IDs are accepted even when Discord's picker cannot show the item."
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
            item = discord.ui.TextInput(label=f"Override {label}", placeholder="yes = block anyway, no = honor this bucket", default=_yes_no(settings.get(key)), required=True, max_length=8)
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
        settings, persisted = await spam_guard.save_spam_settings(self.guild_id, patch, updated_by=interaction.user if isinstance(interaction.user, discord.Member) else None)
        await _rerender_and_reply(interaction, settings, persisted, self.channel_id, self.message_id, self.return_page, "✅ Invite override policy saved.\n" + _override_summary(settings))


class InviteScopeModal(discord.ui.Modal, title="Invite Hard-Block Scope"):
    def __init__(self, guild_id: int, channel_id: int, message_id: int, return_page: str, settings: dict[str, Any]):
        super().__init__(timeout=300)
        self.guild_id = int(guild_id)
        self.channel_id = int(channel_id)
        self.message_id = int(message_id)
        self.return_page = return_page or "access"
        self.all_bots = discord.ui.TextInput(label="Target all bots?", placeholder="yes = delete invite posts from any bot; no = only listed bot/user IDs", default=_yes_no(settings.get("invite_hard_block_target_all_bots")), required=True, max_length=8)
        self.bot_ids = discord.ui.TextInput(label="Target bot/user IDs", placeholder="Comma, space, semicolon, newline, or mentions. Optional when all bots = yes.", default=_ids_text(settings.get("invite_hard_block_target_bot_ids")), required=False, style=discord.TextStyle.paragraph, max_length=800)
        self.channel_ids = discord.ui.TextInput(label="Target channel IDs", placeholder="Blank = all message channels. Paste any channel ID, even if picker hides it.", default=_ids_text(settings.get("invite_hard_block_target_channel_ids")), required=False, style=discord.TextStyle.paragraph, max_length=800)
        self.add_item(self.all_bots)
        self.add_item(self.bot_ids)
        self.add_item(self.channel_ids)

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
            all_bots = _parse_yes_no(self.all_bots.value, label="Target all bots")
        except Exception as exc:
            return await spam_guard._reply_ephemeral(interaction, f"❌ {exc}")
        patch = {
            "invite_hard_block_target_all_bots": all_bots,
            "invite_hard_block_target_bot_ids": _parse_ids(self.bot_ids.value),
            "invite_hard_block_target_channel_ids": _parse_ids(self.channel_ids.value),
        }
        settings, persisted = await spam_guard.save_spam_settings(self.guild_id, patch, updated_by=interaction.user if isinstance(interaction.user, discord.Member) else None)
        await _rerender_and_reply(interaction, settings, persisted, self.channel_id, self.message_id, self.return_page, "✅ Invite hard-block scope saved.\n" + _scope_summary(settings))


async def _rerender_and_reply(interaction: discord.Interaction, settings: dict[str, Any], persisted: bool, channel_id: int, message_id: int, page: str, body: str) -> None:
    from stoney_verify import spam_guard
    guild = interaction.guild
    channel = guild.get_channel(channel_id) if guild else None
    if isinstance(channel, discord.abc.Messageable):
        await spam_guard._rerender_panel_message(guild=guild, channel=channel, message_id=message_id, page=page, persisted_hint=persisted)
    try:
        await interaction.followup.send(body + f"\nPersistence: `{spam_guard._build_persistence_label(int(getattr(guild, 'id', 0) or 0), persisted)}`", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        pass


class InviteOverrideButton(discord.ui.Button):
    def __init__(self, page: str):
        super().__init__(label="Invite Override", emoji="⚙️", style=discord.ButtonStyle.secondary, custom_id=f"spamguard:{page}:invite_override", row=1)
        self.page = page
    async def callback(self, interaction: discord.Interaction) -> None:
        from stoney_verify import spam_guard
        if not await spam_guard._ensure_staff_panel_access(interaction):
            return
        guild, channel, message = interaction.guild, interaction.channel, interaction.message
        if guild is None or not isinstance(channel, discord.abc.Messageable) or message is None:
            return await spam_guard._reply_ephemeral(interaction, "Invalid context.")
        await interaction.response.send_modal(InviteOverrideModal(guild.id, channel.id, message.id, self.page, await spam_guard.get_spam_settings(guild.id)))


class InviteScopeButton(discord.ui.Button):
    def __init__(self, page: str):
        super().__init__(label="Invite Scope", emoji="🎯", style=discord.ButtonStyle.primary, custom_id=f"spamguard:{page}:invite_scope", row=1)
        self.page = page
    async def callback(self, interaction: discord.Interaction) -> None:
        from stoney_verify import spam_guard
        if not await spam_guard._ensure_staff_panel_access(interaction):
            return
        guild, channel, message = interaction.guild, interaction.channel, interaction.message
        if guild is None or not isinstance(channel, discord.abc.Messageable) or message is None:
            return await spam_guard._reply_ephemeral(interaction, "Invalid context.")
        await interaction.response.send_modal(InviteScopeModal(guild.id, channel.id, message.id, self.page, await spam_guard.get_spam_settings(guild.id)))


def _patched_view_build(cls, *, page: str, settings: dict[str, Any]):
    view = _ORIGINAL_VIEW_BUILD(page=page, settings=settings)
    if page == "access":
        for button in (InviteOverrideButton(page), InviteScopeButton(page)):
            try:
                view.add_item(button)
            except Exception:
                pass
    return view


def apply() -> bool:
    global _PATCHED, _ORIGINAL_DEFAULT, _ORIGINAL_NORMALIZE, _ORIGINAL_PAYLOAD, _ORIGINAL_GET, _ORIGINAL_SAVE, _ORIGINAL_EMBED, _ORIGINAL_VIEW_BUILD
    if _PATCHED:
        return True
    try:
        from stoney_verify import spam_guard
        _ORIGINAL_DEFAULT = getattr(spam_guard, "_default_settings")
        _ORIGINAL_NORMALIZE = getattr(spam_guard, "_normalize_settings")
        _ORIGINAL_PAYLOAD = getattr(spam_guard, "_settings_payload_for_db")
        _ORIGINAL_GET = getattr(spam_guard, "get_spam_settings")
        _ORIGINAL_SAVE = getattr(spam_guard, "save_spam_settings")
        _ORIGINAL_EMBED = getattr(spam_guard, "_build_panel_embed")
        _ORIGINAL_VIEW_BUILD = getattr(spam_guard.SpamGuardPanelView, "build")
        spam_guard._default_settings = _patched_default_settings
        spam_guard._normalize_settings = _patched_normalize_settings
        spam_guard._settings_payload_for_db = _patched_settings_payload_for_db
        spam_guard.get_spam_settings = _patched_get_spam_settings
        spam_guard.save_spam_settings = _patched_save_spam_settings
        spam_guard._build_panel_embed = _patched_panel_embed
        spam_guard.SpamGuardPanelView.build = classmethod(_patched_view_build)
        _PATCHED = True
        print("✅ spam_guard_invite_override_options active; access page has Invite Override + all-bot/channel Invite Scope policy")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ spam_guard_invite_override_options failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]