from __future__ import annotations

"""Plain-language UX polish for /dank setup.

This guard does not create a second setup flow. It keeps every feature, but makes
setup screens easier for normal server owners to understand on mobile by reducing
copy, using one clear recommended action, and marking advanced tools as optional.
"""

from typing import Any

import discord

_PATCHED = False

_SETUP_CUSTOM_ID_PREFIXES = (
    "stoney_solid:",
    "dank_setup:",
    "setup_",
    "public_setup:",
    "stoney_setup:",
)
_SETUP_TITLE_MARKERS = (
    "setup",
    "ticket basics",
    "access roles",
    "verification channels",
    "logs + status",
    "logs and status",
    "behavior settings",
    "optional rules",
    "ticket menu",
)


def _log(message: str) -> None:
    try:
        print(f"🧭 setup_ux_clarity_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ setup_ux_clarity_guard {message}")
    except Exception:
        pass


def _clean_old_words(text: Any) -> Any:
    if not isinstance(text, str):
        return text
    out = text
    replacements = {
        "Use this before Auto-Build if you want Dank Shield to create missing items with your own names.": "Use this before Start Setup if you want Dank Shield to create missing roles/channels/categories with your own names.",
        "Use cleanup when setup got messy.": "Use cleanup when setup got messy or you picked the wrong item.",
        "Use `/dank cleanup` for cleanup tools, then return to `/dank setup`.": "Use the cleanup options from this setup screen, then run `/dank setup` again when you are ready to continue.",
        "Choose how strict setup should be. Keep this simple: pick the closest style, then save prefix/timer only if you need them.": "Choose optional behavior rules after the main setup is green.",
        "Dank Shield": "Dank Shield",
        "/dank": "/dank",
        "Auto-Build Missing Items": "Start Setup / Fix Missing",
        "Auto-Build Missing Setup": "Start Setup / Fix Missing",
        "Name Items Before Build": "Customize Names First",
        "Choose Names First": "Customize Names First",
        "Use My Existing Server": "Use Existing Roles/Channels",
        "Pick Existing Roles/Channels": "Use Existing Roles/Channels",
        "Ticket Menu Options": "Advanced Ticket Routing",
        "Ticket Menu Choices": "Advanced Ticket Routing",
        "Run Health Check": "Health Check",
        "Check Setup Health": "Health Check",
        "Behavior Settings": "Optional Rules",
    }
    for old, new in replacements.items():
        out = out.replace(old, new)
    return out


def _is_setup_embed(embed: Any) -> bool:
    try:
        parts = [
            str(getattr(embed, "title", "") or ""),
            str(getattr(embed, "description", "") or ""),
            str(getattr(getattr(embed, "footer", None), "text", "") or ""),
        ]
        for field in list(getattr(embed, "fields", []) or []):
            parts.append(str(getattr(field, "name", "") or ""))
            parts.append(str(getattr(field, "value", "") or ""))
        haystack = "\n".join(parts).lower()
        if "/dank setup" in haystack or "dank shield setup" in haystack:
            return True
        return any(marker in haystack for marker in _SETUP_TITLE_MARKERS)
    except Exception:
        return False


def _view_has_setup_controls(view: Any) -> bool:
    try:
        for child in list(getattr(view, "children", []) or []):
            custom_id = str(getattr(child, "custom_id", "") or "")
            if custom_id.startswith(_SETUP_CUSTOM_ID_PREFIXES):
                return True
    except Exception:
        pass
    return False


def _clean_embed(embed: discord.Embed) -> discord.Embed:
    try:
        embed.title = _clean_old_words(embed.title)
        embed.description = _clean_old_words(embed.description)
        for idx, field in enumerate(list(getattr(embed, "fields", []) or [])):
            embed.set_field_at(
                idx,
                name=_clean_old_words(getattr(field, "name", "") or ""),
                value=_clean_old_words(getattr(field, "value", "") or ""),
                inline=bool(getattr(field, "inline", False)),
            )
    except Exception:
        pass
    return embed


def _normalize_view_labels(view: Any) -> Any:
    try:
        for child in list(getattr(view, "children", []) or []):
            label = str(getattr(child, "label", "") or "")
            placeholder = str(getattr(child, "placeholder", "") or "")
            if label in {"Auto-Build Missing Items", "Auto-Build Missing Setup"}:
                child.label = "Start Setup / Fix Missing"
                child.emoji = "🚀"
                child.style = discord.ButtonStyle.success
            elif label in {"Name Items Before Build", "Choose Names First"}:
                child.label = "Customize Names First"
                child.emoji = "✏️"
            elif label in {"Use My Existing Server", "Pick Existing Roles/Channels"}:
                child.label = "Use Existing Roles/Channels"
                child.emoji = "🧩"
            elif label in {"Ticket Menu Options", "Ticket Menu Choices"}:
                child.label = "Advanced Ticket Routing"
                child.emoji = "🗂️"
            elif label == "Set This as Status Channel":
                child.label = "Set Status Here"
            elif label in {"Run Health Check", "Check Setup Health"}:
                child.label = "Health Check"
            elif label == "Start Over / Cleanup":
                child.label = "Fix or Clean Setup"
            elif label == "Ticket Basics":
                child.label = "Ticket Basics"
            elif label == "Access Roles":
                child.label = "Access Roles"
            elif label == "Logs + Status":
                child.label = "Logs + Status"
            elif label == "Behavior Settings":
                child.label = "Optional Rules"

            if placeholder:
                child.placeholder = _clean_old_words(placeholder)
    except Exception:
        pass
    return view


def _field_value(embed: discord.Embed, field_name: str) -> str:
    try:
        for field in list(getattr(embed, "fields", []) or []):
            if str(getattr(field, "name", "") or "") == field_name:
                return str(getattr(field, "value", "") or "")[:1024]
    except Exception:
        pass
    return ""


def _replace_fields(embed: discord.Embed, fields: list[tuple[str, str]]) -> discord.Embed:
    try:
        embed.clear_fields()
        for name, value in fields:
            if value:
                embed.add_field(name=name[:256], value=value[:1024], inline=False)
    except Exception:
        pass
    return embed


def _polish_main_embed(embed: discord.Embed) -> discord.Embed:
    embed = _clean_embed(embed)
    snapshot = _field_value(embed, "Current Setup Snapshot")
    embed.title = "🚀 Dank Shield Setup"
    embed.description = (
        "**Start here.** For most servers, press **🚀 Start Setup / Fix Missing**.\n"
        "It creates or repairs only missing setup items. It does **not** delete existing roles, channels, tickets, or messages."
    )
    fields: list[tuple[str, str]] = [
        (
            "Recommended First Step",
            "🚀 **Start Setup / Fix Missing** — best for new servers or anyone unsure what to press.",
        ),
    ]
    if snapshot:
        fields.append(("Current Snapshot", snapshot))
    fields.extend(
        [
            (
                "Already Built Your Server?",
                "🧩 **Use Existing Roles/Channels** to map your current categories, channels, and roles. Names do not matter; Dank Shield saves Discord IDs.",
            ),
            (
                "Optional Advanced Tools",
                "✏️ **Customize Names First** before auto-creating defaults.\n🗂️ **Advanced Ticket Routing** changes the menu users pick from.\n🩺 **Health Check** shows exactly what is still missing.",
            ),
        ]
    )
    _replace_fields(embed, fields)
    try:
        embed.set_footer(text="All setup tools are still available; this screen is just organized for first-time users.")
    except Exception:
        pass
    return embed


def _wrap_payload_builder(solid: Any) -> bool:
    original = getattr(solid, "_build_main_setup_payload", None)
    if not callable(original) or getattr(original, "_setup_ux_clarity_wrapped", False):
        return False

    async def wrapped_build_main_setup_payload(*args: Any, **kwargs: Any):
        embed, view = await original(*args, **kwargs)
        embed = _polish_main_embed(embed)
        view = _normalize_view_labels(view)
        return embed, view

    setattr(wrapped_build_main_setup_payload, "_setup_ux_clarity_wrapped", True)
    setattr(solid, "_build_main_setup_payload", wrapped_build_main_setup_payload)
    return True


def _wrap_interaction_edit_response() -> bool:
    original = getattr(discord.InteractionResponse, "edit_message", None)
    if not callable(original) or getattr(original, "_setup_ux_clarity_wrapped", False):
        return False

    async def wrapped_edit_message(self: discord.InteractionResponse, *args: Any, **kwargs: Any):
        embed = kwargs.get("embed")
        view = kwargs.get("view")
        should_polish = (isinstance(embed, discord.Embed) and _is_setup_embed(embed)) or _view_has_setup_controls(view)
        if should_polish:
            if isinstance(embed, discord.Embed):
                kwargs["embed"] = _clean_embed(embed)
            if view is not None:
                kwargs["view"] = _normalize_view_labels(view)
        return await original(self, *args, **kwargs)

    setattr(wrapped_edit_message, "_setup_ux_clarity_wrapped", True)
    setattr(discord.InteractionResponse, "edit_message", wrapped_edit_message)
    return True


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.commands_ext import public_setup_solid as solid

        payload_wrapped = _wrap_payload_builder(solid)
        edit_wrapped = _wrap_interaction_edit_response()
        _PATCHED = True
        _log(f"active main_payload={payload_wrapped} edit_message={edit_wrapped}")
        return True
    except Exception as e:
        _warn(f"failed: {e!r}")
        return False


apply()

__all__ = ["apply"]
