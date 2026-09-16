from __future__ import annotations

"""Historical Protection Center category-wording compatibility.

Invite Shield targeting and cleanup are now owned natively by
``commands_ext.public_protection_invite_ui``. This dormant guard may still be
used by compatibility tests or manual imports for general Protection Center
wording, but it must not import or patch any retired Invite Shield editor.
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


def _set_button(
    child: Any,
    *,
    label: str,
    emoji: str | None = None,
    style: discord.ButtonStyle | None = None,
    row: int | None = None,
) -> None:
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
        "**🚫 Invite Blocker** — opens the native Invite Shield targeting and cleanup flow.\n"
        "**🧼 Bad Word Filter** — manages blocked words/phrases and lets staff test text before trusting it live.\n"
        "**🔗 Link Shield** — controls normal external URLs separately from Discord invites."
    )


def _setup_order() -> str:
    return (
        "1. Pick **Safe Defaults** for a normal public server.\n"
        "2. Open the exact protection category you care about.\n"
        "3. Use **Invite Blocker** for Invite Shield targeting and historical invite cleanup.\n"
        "4. Use advanced exceptions only from the canonical feature that owns them."
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


def _guard_protection_state(cfg: Any = None, spam: dict[str, Any] | None = None) -> str:
    try:
        from stoney_verify.commands_ext import public_protection_center as center

        return str(center._protection_state(cfg, dict(spam or {})))
    except Exception:
        return "custom"


def _clarify_buttons(view: Any, *, cfg: Any = None, spam: dict[str, Any] | None = None) -> None:
    state = _guard_protection_state(cfg, spam)

    for child in list(getattr(view, "children", []) or []):
        custom_id = str(getattr(child, "custom_id", "") or "")
        if custom_id == "dank_protection:safe":
            _set_button(
                child,
                label=f"Safe Defaults: {'ON' if state == 'safe' else 'OFF'}",
                emoji="🟢",
                style=discord.ButtonStyle.success if state == "safe" else discord.ButtonStyle.secondary,
                row=0,
            )
        elif custom_id == "dank_protection:strict":
            _set_button(
                child,
                label=f"Strict Mode: {'ON' if state == 'strict' else 'OFF'}",
                emoji="🔒",
                style=discord.ButtonStyle.success if state == "strict" else discord.ButtonStyle.secondary,
                row=0,
            )
        elif custom_id == "dank_protection:off":
            _set_button(
                child,
                label="Protection: OFF" if state == "off" else "Turn Off",
                emoji="⏸️",
                style=discord.ButtonStyle.success if state == "off" else discord.ButtonStyle.danger,
                row=0,
            )
        elif custom_id == "dank_protection:edit_spamguard":
            _set_button(child, label="Spam Guard Actions", emoji="🛡️", style=discord.ButtonStyle.primary, row=1)
        elif custom_id == "dank_protection:add_filter":
            _set_button(child, label="Bad Word Filter", emoji="🧼", style=discord.ButtonStyle.primary, row=1)
        elif custom_id == "dank_protection:test":
            _set_button(child, label="Test Filter", emoji="🧪", style=discord.ButtonStyle.secondary, row=1)
        elif custom_id == "dank_protection:block_invites":
            _set_button(child, label="Invite Blocker", emoji="🚫", style=discord.ButtonStyle.primary, row=2)
        elif custom_id == "dank_protection:block_links":
            _set_button(child, label="Link Shield", emoji="🔗", style=discord.ButtonStyle.secondary, row=3)
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
        from stoney_verify.commands_ext import public_protection_center as center

        _ORIGINAL_EMBED = center._protection_embed
        _ORIGINAL_VIEW_INIT = center.ProtectionCenterView.__init__

        def patched_embed(guild: discord.Guild, cfg: Any, spam: dict[str, Any], spam_source: str) -> discord.Embed:
            embed = _ORIGINAL_EMBED(guild, cfg, spam, spam_source)
            return _replace_help_fields(embed)

        def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
            _ORIGINAL_VIEW_INIT(self, *args, **kwargs)
            _clarify_buttons(self, cfg=kwargs.get("cfg"), spam=kwargs.get("spam"))

        center._protection_embed = patched_embed
        center.ProtectionCenterView.__init__ = patched_init
        _PATCHED = True
        print("✅ protection_center_clear_categories_guard active; category wording only")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ protection_center_clear_categories_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
