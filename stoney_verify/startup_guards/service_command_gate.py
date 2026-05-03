from __future__ import annotations

"""
Slash command service gate.

Runtime service presets should affect live command execution, not just setup
health. This guard patches discord.py app command invocation so commands for a
disabled service fail early with a friendly ephemeral message.

Why command-level instead of rewriting every command file now:
- the command modules are large and still being migrated
- this protects legacy and future commands by name convention
- setup commands remain always available

Covered command families:
- VC commands -> voice_verification
- mod commands -> moderation
- verification commands -> verification
- ticket panel/create/admin commands -> tickets

The gate is intentionally conservative. It does not block setup/help/health
commands and it allows unknown commands through.
"""

from typing import Any, Optional

import discord
from discord import app_commands

from ..config_new.service_gate import is_service_enabled, send_disabled_service_interaction

_PATCHED = False
_ORIGINAL_COMMAND_INVOKE = None
_ORIGINAL_CONTEXT_MENU_INVOKE = None

_ALWAYS_ALLOW_PREFIXES = (
    "setup",
    "stoney setup",
    "stoney help",
    "stoney commands",
    "stoney cleanup",
)
_ALWAYS_ALLOW_EXACT = {
    "help",
    "stoney",
    "setup-services",
    "setup-targets",
    "setup-health",
    "setup-finish",
}


def _log(message: str) -> None:
    try:
        print(f"🧭 service_command_gate {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ service_command_gate {message}")
    except Exception:
        pass


def _command_name(command: Any) -> str:
    try:
        qn = str(getattr(command, "qualified_name", "") or "").strip().lower()
        if qn:
            return qn
    except Exception:
        pass
    try:
        return str(getattr(command, "name", "") or "").strip().lower()
    except Exception:
        return ""


def _extract_interaction(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Optional[discord.Interaction]:
    for value in list(args) + list(kwargs.values()):
        try:
            if isinstance(value, discord.Interaction):
                return value
        except Exception:
            continue
    return None


def _allowed_command(name: str) -> bool:
    cleaned = str(name or "").strip().lower()
    if not cleaned:
        return True
    if cleaned in _ALWAYS_ALLOW_EXACT:
        return True
    if any(cleaned.startswith(prefix) for prefix in _ALWAYS_ALLOW_PREFIXES):
        return True
    return False


def _service_for_command(name: str) -> Optional[str]:
    cleaned = str(name or "").strip().lower().replace("-", "_")
    if not cleaned or _allowed_command(cleaned.replace("_", "-")):
        return None

    parts = cleaned.split()
    first = parts[0] if parts else cleaned
    last = parts[-1] if parts else cleaned

    # Ticket service commands.
    if first in {"ticket", "tickets", "ticket_intake", "ticket_category", "ticket_panel"}:
        return "tickets"
    if cleaned.startswith("ticket_") or cleaned.startswith("tickets_"):
        return "tickets"
    if "ticket_panel" in cleaned or "post_ticket_panel" in cleaned or "close_ticket" in cleaned:
        return "tickets"

    # Voice verification commands.
    if first in {"vc", "voice_verify", "voice_verification"}:
        return "voice_verification"
    if cleaned.startswith("vc_") or " vc_" in cleaned or last.startswith("vc_"):
        return "voice_verification"
    if "voice_verify" in cleaned or "voice verification" in cleaned:
        return "voice_verification"

    # Moderation commands.
    if first in {"mod", "moderation"}:
        return "moderation"
    if cleaned.startswith("mod_") or " mod_" in cleaned:
        return "moderation"
    if last in {"kick", "ban", "timeout", "mute", "warn"} and first in {"mod", "moderation"}:
        return "moderation"

    # ID/member verification commands.
    if first in {"verify", "verification"}:
        return "verification"
    if cleaned.startswith("verify_") or " verify_" in cleaned:
        return "verification"
    if cleaned in {
        "set_verified",
        "set_resident",
        "grant_vr",
        "fix_unverified",
        "fix_unverified_member",
        "repair_verify_ui",
        "verify_status",
        "verify_diagnose",
    }:
        return "verification"

    return None


async def _command_allowed(interaction: Optional[discord.Interaction], command_name: str) -> bool:
    if interaction is None or interaction.guild is None:
        return True

    service = _service_for_command(command_name)
    if not service:
        return True

    try:
        enabled = await is_service_enabled(interaction.guild.id, service)
    except Exception as e:
        _warn(f"service lookup failed command={command_name} guild={interaction.guild.id}: {repr(e)}; allowing")
        return True

    if enabled:
        return True

    responded = await send_disabled_service_interaction(interaction, service)
    _log(
        f"blocked command={command_name} service={service} guild={interaction.guild.id} responded={responded}"
    )
    return False


async def _patched_command_invoke(self: app_commands.Command[Any, ..., Any], *args: Any, **kwargs: Any):
    original = _ORIGINAL_COMMAND_INVOKE
    if original is None:
        raise RuntimeError("Original app command invoker missing")

    interaction = _extract_interaction(args, kwargs)
    name = _command_name(self)
    if not await _command_allowed(interaction, name):
        return None

    return await original(self, *args, **kwargs)


async def _patched_context_menu_invoke(self: app_commands.ContextMenu, *args: Any, **kwargs: Any):
    original = _ORIGINAL_CONTEXT_MENU_INVOKE
    if original is None:
        raise RuntimeError("Original context menu invoker missing")

    interaction = _extract_interaction(args, kwargs)
    name = _command_name(self)
    if not await _command_allowed(interaction, name):
        return None

    return await original(self, *args, **kwargs)


def install_service_command_gate() -> None:
    global _PATCHED, _ORIGINAL_COMMAND_INVOKE, _ORIGINAL_CONTEXT_MENU_INVOKE
    if _PATCHED:
        return
    _PATCHED = True

    try:
        current = getattr(app_commands.Command, "_invoke_with_namespace", None)
        if callable(current) and not getattr(current, "_sv_service_command_gate", False):
            _ORIGINAL_COMMAND_INVOKE = current
            setattr(_patched_command_invoke, "_sv_service_command_gate", True)
            app_commands.Command._invoke_with_namespace = _patched_command_invoke  # type: ignore[assignment]
            _log("patched app_commands.Command._invoke_with_namespace")
        else:
            _warn("Command._invoke_with_namespace unavailable or already patched")
    except Exception as e:
        _warn(f"failed patching Command invoker: {repr(e)}")

    try:
        current_cm = getattr(app_commands.ContextMenu, "_invoke", None)
        if callable(current_cm) and not getattr(current_cm, "_sv_service_command_gate", False):
            _ORIGINAL_CONTEXT_MENU_INVOKE = current_cm
            setattr(_patched_context_menu_invoke, "_sv_service_command_gate", True)
            app_commands.ContextMenu._invoke = _patched_context_menu_invoke  # type: ignore[assignment]
            _log("patched app_commands.ContextMenu._invoke")
    except Exception as e:
        _warn(f"failed patching ContextMenu invoker: {repr(e)}")


install_service_command_gate()


__all__ = ["install_service_command_gate"]
