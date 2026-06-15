from __future__ import annotations

"""Make the Protection Center read like product categories, not internals.

The goal is simple: staff should immediately know which button handles
behavior spam, blocked words, Discord invites, and normal links.
"""

from typing import Any

import discord

_PATCHED = False
_ORIGINAL_EMBED: Any = None
_ORIGINAL_VIEW_INIT: Any = None
_ORIGINAL_INVITE_EDITOR_INIT: Any = None
_ORIGINAL_INVITE_EDITOR_EMBED: Any = None


_CATEGORY_FIELD_NAMES = {
    "what buttons do",
    "quick recommendation",
    "invite & link controls",
    "invite protection snapshot",
}


def _helpers() -> tuple[Any, Any]:
    from stoney_verify.commands_ext import public_protection_center as center

    try:
        from stoney_verify.startup_guards import protection_center_invite_controls_guard as invite_controls

        invite_controls.apply()
    except Exception:
        pass

    try:
        from stoney_verify.startup_guards import protection_center_invite_status_guard as invite_status

        invite_status.apply()
    except Exception:
        pass

    return center, discord


def _set_button(child: Any, *, label: str, emoji: str | None = None, style: discord.ButtonStyle | None = None, row: int | None = None) -> None:
    try:
        child.label = label
    except Exception:
        pass
    if emoji is not None:
        try:
            child.emoji = emoji
        except Exception:
            pass
    if style is not None:
        try:
            child.style = style
        except Exception:
            pass
    if row is not None:
        try:
            child.row = int(row)
        except Exception:
            pass


def _category_help() -> str:
    return (
        "**🛡️ Spam Guard** — stops behavior spam: message bursts, duplicate floods, raid-style posting, and timeouts.\n"
        "**🚫 Discord Invite Blocker** — controls Discord invite links, including bot posts like bump bots and protected channels.\n"
        "**🧼 Bad Word Filter** — manages blocked words/phrases and lets staff test text before trusting it live.\n"
        "**🔗 Link Blocker** — controls normal external URLs separately from Discord invites."
    )


def _setup_order() -> str:
    return (
        "1. Pick **Safe Defaults** for a normal public server.\n"
        "2. Open the exact category you care about.\n"
        "3. Use **Protected Bots/Channels** when a bot is allowed to post but invite links should still be removed.\n"
        "4. Use **Allowed Exceptions** only when a trusted role/channel/code should bypass invite protection."
    )


def _replace_help_fields(embed: discord.Embed) -> discord.Embed:
    try:
        fields = list(embed.fields)
        embed.clear_fields()
        inserted = False
        for field in fields:
            name = str(getattr(field, "name", "") or "")
            if name.strip().lower() in _CATEGORY_FIELD_NAMES:
                if not inserted:
                    embed.add_field(name="Pick a protection category", value=_category_help(), inline=False)
                    embed.add_field(name="No-confusion setup order", value=_setup_order(), inline=False)
                    inserted = True
                continue
            embed.add_field(name=field.name, value=field.value, inline=field.inline)
        if not inserted:
            embed.add_field(name="Pick a protection category", value=_category_help(), inline=False)
            embed.add_field(name="No-confusion setup order", value=_setup_order(), inline=False)
    except Exception:
        try:
            embed.add_field(name="Pick a protection category", value=_category_help(), inline=False)
        except Exception:
            pass
    try:
        embed.description = (
            "Choose the exact protection category you want. Dank Shield keeps content filters, invite-link controls, "
            "normal link controls, and behavior spam protection separated so staff never have to guess."
        )
    except Exception:
        pass
    return embed


def _clarify_buttons(view: Any) -> None:
    for child in list(getattr(view, "children", []) or []):
        custom_id = str(getattr(child, "custom_id", "") or "")
        if custom_id == "dank_protection:safe":
            _set_button(child, label="Safe Defaults", emoji="🟢", style=discord.ButtonStyle.success, row=0)
        elif custom_id == "dank_protection:strict":
            _set_button(child, label="Strict Mode", emoji="🔒", style=discord.ButtonStyle.primary, row=0)
        elif custom_id == "dank_protection:off":
            _set_button(child, label="Turn Off", emoji="⏸️", style=discord.ButtonStyle.secondary, row=0)
        elif custom_id == "dank_protection:edit_spamguard":
            _set_button(child, label="Spam Guard", emoji="🛡️", style=discord.ButtonStyle.primary, row=1)
        elif custom_id == "dank_protection:add_filter":
            _set_button(child, label="Bad Word Filter", emoji="🧼", style=discord.ButtonStyle.primary, row=1)
        elif custom_id == "dank_protection:test":
            _set_button(child, label="Test Filter", emoji="🧪", style=discord.ButtonStyle.secondary, row=1)
        elif custom_id == "dank_protection:block_invites":
            _set_button(child, label="Invite Blocker On", emoji="🚫", style=discord.ButtonStyle.primary, row=2)
        elif custom_id == "dank_protection:invite_scope":
            _set_button(child, label="Protected Bots/Channels", emoji="🤖", style=discord.ButtonStyle.primary, row=2)
        elif custom_id == "dank_protection:invite_override":
            _set_button(child, label="Allowed Exceptions", emoji="🔓", style=discord.ButtonStyle.secondary, row=2)
        elif custom_id == "dank_protection:block_links":
            _set_button(child, label="All Links Lockdown", emoji="🔗", style=discord.ButtonStyle.secondary, row=3)
        elif custom_id == "dank_protection:allow_links":
            _set_button(child, label="Allow Normal Links", emoji="🔓", style=discord.ButtonStyle.secondary, row=3)
        elif custom_id == "dank_protection:refresh":
            _set_button(child, label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=4)
        elif custom_id == "dank_protection:close":
            _set_button(child, label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, row=4)


def _id_list(policy: Any, settings: dict[str, Any], key: str) -> list[str]:
    try:
        return list(policy._parse_ids(settings.get(key)))
    except Exception:
        return []


def _plain_invite_editor_embed(invite_controls: Any, policy: Any, guild: discord.Guild, settings: dict[str, Any], *, bot_page: int = 0, channel_page: int = 0) -> discord.Embed:
    bot_ids = _id_list(policy, settings, "invite_hard_block_target_bot_ids")
    channel_ids = _id_list(policy, settings, "invite_hard_block_target_channel_ids")
    all_bots = False
    try:
        all_bots = bool(policy._safe_bool(settings.get("invite_hard_block_target_all_bots"), False))
    except Exception:
        pass

    bots_text = "Every bot is watched" if all_bots else (f"{len(bot_ids)} selected bot/user ID(s)" if bot_ids else "No bots selected yet")
    channels_text = f"{len(channel_ids)} selected channel(s)" if channel_ids else "All message channels are watched"

    embed = discord.Embed(
        title="🚫 Discord Invite Blocker Setup",
        description=(
            "Use this when a bot or member is allowed to talk, but Discord invite links should still be handled by Dank Shield.\n\n"
            "For OneBump: choose **Watch Every Bot** or add the OneBump bot, then choose **This Channel** or **All Channels**."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="What is watched right now",
        value=f"**Bots/users:** {bots_text}\n**Channels:** {channels_text}",
        inline=False,
    )
    embed.add_field(
        name="Use these buttons",
        value=(
            "**Watch Every Bot** = easiest option for bump bots and ad bots.\n"
            "**Pick Bot** = choose one visible bot from the list.\n"
            "**This Channel** = only this channel.\n"
            "**All Channels** = all channels Dank Shield can monitor.\n"
            "**Paste IDs** = bulk paste bot, user, or channel IDs."
        ),
        inline=False,
    )
    embed.add_field(
        name="Advanced pages",
        value=(
            f"Bot list page `{int(bot_page) + 1}`. Channel list page `{int(channel_page) + 1}`. "
            "Most servers never need the page buttons unless the dropdown is missing something."
        ),
        inline=False,
    )
    return embed


def _clarify_invite_editor(view: Any) -> None:
    for child in list(getattr(view, "children", []) or []):
        label = str(getattr(child, "label", "") or "")
        placeholder = str(getattr(child, "placeholder", "") or "")
        if label == "◀ Bots":
            _set_button(child, label="◀ Bot Page", row=3)
        elif label == "Bots ▶":
            _set_button(child, label="Bot Page ▶", row=3)
        elif label == "◀ Channels":
            _set_button(child, label="◀ Channel Page", row=3)
        elif label == "Channels ▶":
            _set_button(child, label="Channel Page ▶", row=3)
        elif label == "Manual IDs":
            _set_button(child, label="Paste IDs", emoji="✍️", style=discord.ButtonStyle.primary, row=4)
        elif label == "Clear":
            _set_button(child, label="Clear Choices", emoji="🧹", style=discord.ButtonStyle.secondary, row=4)
        elif label == "Done":
            _set_button(child, label="Done", emoji="✅", style=discord.ButtonStyle.success, row=4)
        elif label == "All Bots":
            _set_button(child, label="Watch Every Bot", emoji="🤖", style=discord.ButtonStyle.success, row=2)
        elif label == "Humans Only":
            _set_button(child, label="Only Listed Bots", emoji="👤", style=discord.ButtonStyle.secondary, row=2)
        elif label == "All Channels":
            _set_button(child, label="All Channels", emoji="🌐", style=discord.ButtonStyle.success, row=2)
        elif label == "This Channel":
            _set_button(child, label="This Channel", emoji="#️⃣", style=discord.ButtonStyle.primary, row=2)
        if placeholder.startswith("Add bot target") or placeholder.startswith("Add bots/users"):
            try:
                child.placeholder = "Pick a bot to watch for invite links"
            except Exception:
                pass
        elif placeholder.startswith("Add channel target") or placeholder.startswith("Add channels"):
            try:
                child.placeholder = "Pick a channel to watch"
            except Exception:
                pass


def _patch_invite_editor() -> None:
    global _ORIGINAL_INVITE_EDITOR_INIT, _ORIGINAL_INVITE_EDITOR_EMBED
    try:
        from stoney_verify.startup_guards import protection_center_invite_controls_guard as invite_controls
        from stoney_verify.startup_guards import spam_guard_invite_override_options as policy

        if _ORIGINAL_INVITE_EDITOR_INIT is None:
            _ORIGINAL_INVITE_EDITOR_INIT = invite_controls.InviteScopeEditorView.__init__
        if _ORIGINAL_INVITE_EDITOR_EMBED is None:
            _ORIGINAL_INVITE_EDITOR_EMBED = invite_controls._scope_editor_embed

        def patched_scope_editor_embed(policy_arg: Any, guild: discord.Guild, settings: dict[str, Any], *, bot_page: int = 0, channel_page: int = 0) -> discord.Embed:
            return _plain_invite_editor_embed(invite_controls, policy_arg or policy, guild, settings, bot_page=bot_page, channel_page=channel_page)

        def patched_invite_editor_init(self, *args: Any, **kwargs: Any) -> None:
            _ORIGINAL_INVITE_EDITOR_INIT(self, *args, **kwargs)
            _clarify_invite_editor(self)

        invite_controls._scope_editor_embed = patched_scope_editor_embed
        invite_controls.InviteScopeEditorView.__init__ = patched_invite_editor_init
    except Exception:
        pass


def apply() -> bool:
    global _PATCHED, _ORIGINAL_EMBED, _ORIGINAL_VIEW_INIT
    if _PATCHED:
        return True
    try:
        center, _discord = _helpers()
        _patch_invite_editor()
        _ORIGINAL_EMBED = center._protection_embed
        _ORIGINAL_VIEW_INIT = center.ProtectionCenterView.__init__

        def patched_embed(guild: discord.Guild, cfg: Any, spam: dict[str, Any], spam_source: str) -> discord.Embed:
            embed = _ORIGINAL_EMBED(guild, cfg, spam, spam_source)
            return _replace_help_fields(embed)

        def patched_init(self, *, author_id: int) -> None:
            _ORIGINAL_VIEW_INIT(self, author_id=author_id)
            _clarify_buttons(self)
            _patch_invite_editor()

        center._protection_embed = patched_embed
        center.ProtectionCenterView.__init__ = patched_init
        _PATCHED = True
        print("✅ protection_center_clear_categories_guard active; Protection Center and invite setup use plain category wording")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ protection_center_clear_categories_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]