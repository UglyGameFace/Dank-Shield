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


def apply() -> bool:
    global _PATCHED, _ORIGINAL_EMBED, _ORIGINAL_VIEW_INIT
    if _PATCHED:
        return True
    try:
        center, _discord = _helpers()
        _ORIGINAL_EMBED = center._protection_embed
        _ORIGINAL_VIEW_INIT = center.ProtectionCenterView.__init__

        def patched_embed(guild: discord.Guild, cfg: Any, spam: dict[str, Any], spam_source: str) -> discord.Embed:
            embed = _ORIGINAL_EMBED(guild, cfg, spam, spam_source)
            return _replace_help_fields(embed)

        def patched_init(self, *, author_id: int) -> None:
            _ORIGINAL_VIEW_INIT(self, author_id=author_id)
            _clarify_buttons(self)

        center._protection_embed = patched_embed
        center.ProtectionCenterView.__init__ = patched_init
        _PATCHED = True
        print("✅ protection_center_clear_categories_guard active; Protection Center uses separate category wording")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ protection_center_clear_categories_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]