from __future__ import annotations

"""Plain-language UX polish for /dank setup.

This guard does not create a second setup flow. It wraps only setup screens and
makes the copy easier for normal server owners to understand on mobile.
"""

from typing import Any

import discord

_PATCHED = False

_SETUP_CUSTOM_ID_PREFIXES = (
    "stoney_solid:",
    "dank_setup:",
    "setup_",
    "public_setup:",
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
        "Use this before Auto-Build if you want Stoney to create missing items with your own names.": "Use this before Auto-Build if you want Dank Shield to create missing roles/channels/categories with your own names.",
        "Use cleanup when setup got messy.": "Use cleanup when setup got messy or you picked the wrong item.",
        "Use `/dank cleanup` for cleanup tools, then return to `/dank setup`.": "Use the cleanup options from this setup screen, then run `/dank setup` again when you are ready to continue.",
        "Choose how strict setup should be. Keep this simple: pick the closest style, then save prefix/timer only if you need them.": "Choose optional behavior rules. Leave these alone until the main setup is green, then turn on only the features your server actually wants.",
        "Stoney": "Dank Shield",
        "/stoney": "/dank",
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
            label = str(getattr(child, "label", "") or "").lower()
            if label in {
                "auto-build missing items",
                "auto-build missing setup",
                "name items before build",
                "choose names first",
                "use my existing server",
                "pick existing roles/channels",
                "ticket menu options",
                "ticket menu choices",
                "run health check",
                "check setup health",
                "start over / cleanup",
                "fix or clean setup",
                "ticket basics",
                "tickets: main setup",
                "access roles",
                "roles: member access",
                "verification channels",
                "verification: channels",
                "logs + status",
                "logs and status",
                "behavior settings",
                "optional rules",
            }:
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
            if label == "Auto-Build Missing Items":
                child.label = "Auto-Build Missing Setup"
            elif label == "Name Items Before Build":
                child.label = "Choose Names First"
            elif label == "Use My Existing Server":
                child.label = "Pick Existing Roles/Channels"
            elif label == "Ticket Menu Options":
                child.label = "Ticket Menu Choices"
            elif label == "Set This as Status Channel":
                child.label = "Use This Channel for Status"
            elif label == "Run Health Check":
                child.label = "Check Setup Health"
            elif label == "Start Over / Cleanup":
                child.label = "Fix or Clean Setup"
            elif label == "Ticket Basics":
                child.label = "Tickets: Main Setup"
            elif label == "Access Roles":
                child.label = "Roles: Member Access"
            elif label == "Verification Channels":
                child.label = "Verification: Channels"
            elif label == "Logs + Status":
                child.label = "Logs and Status"
            elif label == "Behavior Settings":
                child.label = "Optional Rules"

            if placeholder:
                child.placeholder = _clean_old_words(placeholder)
    except Exception:
        pass
    return view


def _add_field_once(embed: discord.Embed, *, name: str, value: str) -> None:
    try:
        existing = {str(getattr(field, "name", "") or "") for field in getattr(embed, "fields", []) or []}
        if name not in existing:
            embed.add_field(name=name, value=value[:1024], inline=False)
    except Exception:
        pass


def _polish_main_embed(embed: discord.Embed) -> discord.Embed:
    _add_field_once(
        embed,
        name="Which button should I press?",
        value=(
            "• **Auto-Build Missing Setup**: easiest choice for a new server.\n"
            "• **Choose Names First**: pick your own role/channel names before auto-build.\n"
            "• **Pick Existing Roles/Channels**: use channels and roles you already made.\n"
            "• **Ticket Menu Choices**: edit what members can choose when opening a ticket.\n"
            "• **Check Setup Health**: shows what is ready, missing, or skipped."
        ),
    )
    _add_field_once(
        embed,
        name="Important rule",
        value="Dank Shield saves setup per server. Do not put server role/channel IDs in hosting env for public bots.",
    )
    return embed


def _wrap_payload_builder(solid: Any) -> bool:
    original = getattr(solid, "_build_main_setup_payload", None)
    if not callable(original) or getattr(original, "_setup_ux_clarity_wrapped", False):
        return False

    async def wrapped_build_main_setup_payload(*args: Any, **kwargs: Any):
        embed, view = await original(*args, **kwargs)
        embed = _polish_main_embed(_clean_embed(embed))
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
