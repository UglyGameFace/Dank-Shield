from __future__ import annotations

"""Add an all-bots target switch to invite hard-block scope.

Paged bot lists can only show bots that are visible in the member cache. This
switch lets a server owner target *every* bot account without needing every bot
listed in the select menu.
"""

from typing import Any

import discord

_PATCHED = False
_ORIGINAL_GET: Any = None
_ORIGINAL_SAVE: Any = None
_ORIGINAL_SCOPE_STATUS: Any = None
_ORIGINAL_INVITE_SCOPE_VIEW: Any = None

_KEY = "invite_hard_block_target_all_bots"


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return bool(default)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled", "all"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled", "none"}:
            return False
    except Exception:
        pass
    return bool(default)


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


async def _load_all_bots(guild_id: int) -> bool:
    try:
        from stoney_verify.guild_config import get_guild_config

        cfg = await get_guild_config(int(guild_id), refresh=True)
        raw = _cfg_value(cfg, f"spam_{_KEY}")
        if raw is None:
            raw = _cfg_value(cfg, _KEY)
        return _safe_bool(raw, False)
    except Exception:
        return False


async def _save_all_bots(guild_id: int, enabled: bool) -> bool:
    try:
        from stoney_verify.guild_config import invalidate_guild_config, upsert_guild_config

        await upsert_guild_config(int(guild_id), {_KEY: bool(enabled), f"spam_{_KEY}": bool(enabled)})
        invalidate_guild_config(int(guild_id))
        return True
    except Exception:
        return False


async def _patched_get_spam_settings(guild_id: int) -> dict[str, Any]:
    settings = dict(await _ORIGINAL_GET(int(guild_id)))
    settings[_KEY] = await _load_all_bots(int(guild_id))
    try:
        from stoney_verify import spam_guard
        spam_guard._cache_runtime_settings(int(guild_id), settings, source="db+guild_config", persisted=True)
    except Exception:
        pass
    return settings


async def _patched_save_spam_settings(guild_id: int, patch: dict[str, Any], *, updated_by: discord.Member | None = None):
    raw_patch = dict(patch or {})
    has_all_bots = _KEY in raw_patch or f"spam_{_KEY}" in raw_patch
    enabled = _safe_bool(raw_patch.pop(_KEY, raw_patch.pop(f"spam_{_KEY}", False)), False) if has_all_bots else False
    settings, persisted = await _ORIGINAL_SAVE(int(guild_id), raw_patch, updated_by=updated_by)
    if has_all_bots:
        saved = await _save_all_bots(int(guild_id), enabled)
        settings = dict(settings or {})
        settings[_KEY] = bool(enabled)
        try:
            from stoney_verify import spam_guard
            spam_guard._cache_runtime_settings(int(guild_id), settings, source="db+guild_config" if saved else "runtime", persisted=bool(persisted or saved))
        except Exception:
            pass
        persisted = bool(persisted or saved)
    return settings, bool(persisted)


def _patched_scope_status(policy: Any, settings: dict[str, Any]) -> str:
    base = _ORIGINAL_SCOPE_STATUS(policy, settings)
    all_bots = _safe_bool(settings.get(_KEY, settings.get(f"spam_{_KEY}")), False)
    if all_bots:
        base = base.replace("Humans only; bots ignored unless listed", "ALL BOTS + humans")
    return base + f"\n**All-bot mode:** {'✅ enabled' if all_bots else '— off'}"


def _refresh_embed_with_all_bots_hint(pc: Any, policy: Any, guild: discord.Guild, settings: dict[str, Any], *, bot_page: int = 0, channel_page: int = 0) -> discord.Embed:
    embed = pc._scope_editor_embed(policy, guild, settings, bot_page=bot_page, channel_page=channel_page)
    try:
        embed.add_field(
            name="All-bot targeting",
            value=(
                "Use **Target All Bots** when a bot is not visible in the paged list or when every bot account should be policed. "
                "Manual IDs still works for specific hidden/uncached bots or human users."
            ),
            inline=False,
        )
    except Exception:
        pass
    return embed


def apply() -> bool:
    global _PATCHED, _ORIGINAL_GET, _ORIGINAL_SAVE, _ORIGINAL_SCOPE_STATUS, _ORIGINAL_INVITE_SCOPE_VIEW
    if _PATCHED:
        return True
    try:
        from stoney_verify import spam_guard
        from stoney_verify.startup_guards import protection_center_invite_controls_guard as pc
        from stoney_verify.startup_guards import spam_guard_invite_override_options as policy

        _ORIGINAL_GET = spam_guard.get_spam_settings
        _ORIGINAL_SAVE = spam_guard.save_spam_settings
        _ORIGINAL_SCOPE_STATUS = pc._format_scope_status
        _ORIGINAL_INVITE_SCOPE_VIEW = pc.InviteScopeEditorView

        spam_guard.get_spam_settings = _patched_get_spam_settings
        spam_guard.save_spam_settings = _patched_save_spam_settings
        pc._format_scope_status = _patched_scope_status

        class AllBotsInviteScopeEditorView(_ORIGINAL_INVITE_SCOPE_VIEW):
            @discord.ui.button(label="Target All Bots", emoji="🤖", style=discord.ButtonStyle.success, row=4)
            async def target_all_bots(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
                center, spam_guard_inner, policy_inner = pc._patch_helpers()
                if not await center._require_setup_permission(interaction):
                    return
                guild = interaction.guild
                if guild is None:
                    return await center._send_ephemeral(interaction, "❌ This must be used inside a server.")
                current = await spam_guard_inner.get_spam_settings(int(guild.id))
                next_value = not _safe_bool(current.get(_KEY), False)
                settings, _persisted = await spam_guard_inner.save_spam_settings(
                    int(guild.id),
                    {_KEY: next_value},
                    updated_by=interaction.user if isinstance(interaction.user, discord.Member) else None,
                )
                try:
                    await pc._refresh_original_protection_message(
                        guild=guild,
                        author_id=int(interaction.user.id),
                        channel_id=self.channel_id,
                        message_id=self.message_id,
                        center=center,
                    )
                except Exception:
                    pass
                await interaction.response.edit_message(
                    embed=_refresh_embed_with_all_bots_hint(policy=policy_inner, pc=pc, guild=guild, settings=settings, bot_page=self.bot_page, channel_page=self.channel_page),
                    view=AllBotsInviteScopeEditorView(channel_id=self.channel_id, message_id=self.message_id, settings=settings, bot_page=self.bot_page, channel_page=self.channel_page, guild=guild),
                )

        pc.InviteScopeEditorView = AllBotsInviteScopeEditorView
        _PATCHED = True
        print("✅ invite_hard_block_all_bots_controls_guard active; Invite Scope has Target All Bots")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ invite_hard_block_all_bots_controls_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
