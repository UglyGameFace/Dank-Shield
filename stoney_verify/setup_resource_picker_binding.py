from __future__ import annotations

"""Canonical `/dank setup` resource-picker binding.

The three public setup owners (public_setup_solid,
public_setup_full_customization, public_setup_recommend) used to define
Discord-native RoleSelect/ChannelSelect classes, and this module bound
their live class names to buttons that open the shared cache-backed
resource browser (DankGuildResourceBrowserView, a DankPickerView).

Since the Slice 3 picker migration, the owners define those browser
buttons natively and delegate picks to their own save handlers, so
there is nothing left to substitute. This module now provides the
shared custom_id / resource-kind helpers the owners use, and
``apply_setup_resource_picker_binding`` verifies at bootstrap that
every owner is on the canonical browser binding — commands.py treats a
False result as a fatal bootstrap error, exactly as it did when the
binding performed the substitution itself.

It is not a startup guard, import hook, config writer, or second
business-logic owner.
"""

from typing import Any, Iterable

import discord

_BOUND = False


def _custom_id(prefix: str, columns: Iterable[str], row: int) -> str:
    suffix = "_".join(str(value or "").strip() for value in columns if str(value or "").strip())
    suffix = suffix or str(int(row))
    return f"{prefix}:{suffix}"[:100]


def _resource_kinds(channel_types: Iterable[discord.ChannelType]) -> tuple[str, ...]:
    out: list[str] = []
    for channel_type in list(channel_types or []):
        kind = ""
        if channel_type == discord.ChannelType.category:
            kind = "category"
        elif channel_type in {discord.ChannelType.text, discord.ChannelType.news}:
            kind = "text"
        elif channel_type == discord.ChannelType.voice:
            kind = "voice"
        elif getattr(discord.ChannelType, "stage_voice", None) is not None and channel_type == discord.ChannelType.stage_voice:
            kind = "stage"
        elif getattr(discord.ChannelType, "forum", None) is not None and channel_type == discord.ChannelType.forum:
            kind = "forum"
        if kind and kind not in out:
            out.append(kind)
    return tuple(out) or ("channel",)


def _message_embed(interaction: discord.Interaction) -> discord.Embed | None:
    try:
        embeds = list(getattr(getattr(interaction, "message", None), "embeds", []) or [])
        return embeds[0] if embeds else None
    except Exception:
        return None


async def _restore_parent(
    interaction: discord.Interaction,
    *,
    parent_view: discord.ui.View | None,
    parent_embed: discord.Embed | None,
) -> None:
    kwargs: dict[str, Any] = {"view": parent_view}
    if parent_embed is not None:
        kwargs["embed"] = parent_embed
    await interaction.response.edit_message(**kwargs)


def _owner_is_native(module: Any, *, opener_name: str, legacy_names: tuple[str, ...]) -> bool:
    """True when the owner defines its browser opener natively and no
    legacy Discord-native select class survives under the old names."""
    opener = getattr(module, opener_name, None)
    if not (isinstance(opener, type) and issubclass(opener, discord.ui.Button)):
        return False
    for name in legacy_names:
        legacy = getattr(module, name, None)
        if isinstance(legacy, type) and issubclass(
            legacy, (discord.ui.Select, discord.ui.RoleSelect, discord.ui.ChannelSelect, discord.ui.UserSelect)
        ):
            return False
    return True


def _verify_solid() -> bool:
    from .commands_ext import public_setup_solid as solid

    return _owner_is_native(
        solid,
        opener_name="_SolidPickButton",
        legacy_names=("SaveRoleSelect", "SaveChannelSelect"),
    )


def _verify_full_customization() -> bool:
    from .commands_ext import public_setup_full_customization as full

    return _owner_is_native(
        full,
        opener_name="_CustomPickButton",
        legacy_names=("SaveRoleSelect", "SaveChannelSelect"),
    )


def _verify_guided_recommend() -> bool:
    from .commands_ext import public_setup_recommend as recommend

    return _owner_is_native(
        recommend,
        opener_name="_GuidedExistingOpenButton",
        legacy_names=("GuidedExistingRoleSelect", "GuidedExistingChannelSelect"),
    )


def apply_setup_resource_picker_binding() -> bool:
    """Verify all public setup entity choices use the canonical browser."""

    global _BOUND
    if _BOUND:
        return True
    try:
        ok = _verify_solid() and _verify_full_customization() and _verify_guided_recommend()
    except Exception as exc:
        try:
            print(f"❌ setup resource picker binding failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False
    if not ok:
        try:
            print("❌ setup resource picker binding failed: a setup owner is not on the canonical browser binding")
        except Exception:
            pass
        return False
    _BOUND = True
    try:
        print("✅ setup resource picker binding active")
    except Exception:
        pass
    return True


__all__ = ["apply_setup_resource_picker_binding"]
