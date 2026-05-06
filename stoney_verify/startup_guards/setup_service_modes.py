from __future__ import annotations

"""Phase 2 setup service-mode layer for Dank Shield.

Goal:
- Let each server choose what Dank Shield is being used for.
- Keep /dank setup simple instead of forcing every owner through tickets,
  verification, VC verification, logging, and SpamGuard all at once.
- Make health checks judge only the services the server enabled.

This is intentionally additive. It stores service flags in guild_configs and
patches the current public setup UI with a small service picker. The existing
setup owner file remains the source of truth for the deeper setup screens.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Mapping, Optional

import discord

try:
    from stoney_verify.commands_ext.public_setup_config_writer import upsert_guild_config
except Exception:
    upsert_guild_config = None  # type: ignore

try:
    from stoney_verify.guild_config import get_guild_config, invalidate_guild_config
except Exception:
    get_guild_config = None  # type: ignore

    def invalidate_guild_config(guild_id: int) -> None:  # type: ignore
        return None

try:
    from stoney_verify.globals import get_supabase, now_utc
except Exception:
    get_supabase = None  # type: ignore

    def now_utc():  # type: ignore
        import datetime

        return datetime.datetime.now(datetime.timezone.utc)


_PATCHED = False

SERVICE_FLAGS = (
    "tickets_enabled",
    "verification_enabled",
    "voice_verification_enabled",
    "spam_guard_enabled",
    "moderation_enabled",
)

PRESETS: dict[str, tuple[str, dict[str, bool], str]] = {
    "tickets": (
        "Tickets only",
        {
            "tickets_enabled": True,
            "verification_enabled": False,
            "voice_verification_enabled": False,
            "spam_guard_enabled": False,
            "moderation_enabled": False,
        },
        "Support tickets without verification or SpamGuard setup checks.",
    ),
    "verification": (
        "Verification only",
        {
            "tickets_enabled": False,
            "verification_enabled": True,
            "voice_verification_enabled": True,
            "spam_guard_enabled": False,
            "moderation_enabled": False,
        },
        "ID/voice verification without ticket-system setup blockers.",
    ),
    "spamguard": (
        "SpamGuard only",
        {
            "tickets_enabled": False,
            "verification_enabled": False,
            "voice_verification_enabled": False,
            "spam_guard_enabled": True,
            "moderation_enabled": True,
        },
        "Spam protection and moderation logging without tickets/verification blockers.",
    ),
    "tickets_verification": (
        "Tickets + Verification",
        {
            "tickets_enabled": True,
            "verification_enabled": True,
            "voice_verification_enabled": True,
            "spam_guard_enabled": False,
            "moderation_enabled": False,
        },
        "TicketTool-style tickets plus verification flow.",
    ),
    "all": (
        "Everything",
        {
            "tickets_enabled": True,
            "verification_enabled": True,
            "voice_verification_enabled": True,
            "spam_guard_enabled": True,
            "moderation_enabled": True,
        },
        "Tickets, verification, SpamGuard, and moderation/logging.",
    ),
}


@dataclass(frozen=True)
class ServiceState:
    tickets: bool
    verification: bool
    voice: bool
    spamguard: bool
    moderation: bool
    source: str = "defaults"

    def as_payload(self) -> dict[str, bool]:
        return {
            "tickets_enabled": self.tickets,
            "verification_enabled": self.verification,
            "voice_verification_enabled": self.voice,
            "spam_guard_enabled": self.spamguard,
            "moderation_enabled": self.moderation,
        }


def _log(message: str) -> None:
    try:
        print(f"🧭 setup_service_modes {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ setup_service_modes {message}")
    except Exception:
        pass


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return bool(default)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled"}:
            return False
        return bool(default)
    except Exception:
        return bool(default)


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
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
    try:
        for bucket in ("settings", "config", "metadata", "meta"):
            nested = getattr(cfg, bucket, None)
            if isinstance(nested, Mapping) and nested.get(key) is not None:
                return nested.get(key)
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, Mapping) and nested.get(key) is not None:
                    return nested.get(key)
    except Exception:
        pass
    return default


async def load_service_state(guild_id: int) -> ServiceState:
    cfg = None
    if get_guild_config is not None:
        try:
            cfg = await get_guild_config(int(guild_id), refresh=True)  # type: ignore[misc]
        except Exception as e:
            _warn(f"could not load guild config for services guild={guild_id}: {e!r}")

    if cfg is None:
        return ServiceState(True, False, False, False, False, "defaults")

    tickets = _safe_bool(_cfg_value(cfg, "tickets_enabled", True), True)
    verification = _safe_bool(_cfg_value(cfg, "verification_enabled", False), False)
    voice = _safe_bool(_cfg_value(cfg, "voice_verification_enabled", False), False)
    spamguard = _safe_bool(_cfg_value(cfg, "spam_guard_enabled", False), False)
    moderation = _safe_bool(_cfg_value(cfg, "moderation_enabled", spamguard), spamguard)
    return ServiceState(tickets, verification, voice, spamguard, moderation, str(_cfg_value(cfg, "source", "guild_configs")))


def _service_summary_text(state: ServiceState) -> str:
    return (
        f"{'✅' if state.tickets else '⬜'} Tickets\n"
        f"{'✅' if state.verification else '⬜'} ID verification\n"
        f"{'✅' if state.voice else '⬜'} Voice verification\n"
        f"{'✅' if state.spamguard else '⬜'} SpamGuard\n"
        f"{'✅' if state.moderation else '⬜'} Moderation/logging"
    )


def _service_mode_hint(state: ServiceState) -> str:
    enabled = []
    if state.tickets:
        enabled.append("Ticket Basics")
    if state.verification or state.voice:
        enabled.append("Access Roles / Verification Channels")
    if state.spamguard or state.moderation:
        enabled.append("Logs + Status")
    if not enabled:
        return "Choose at least one service first."
    return "Health Check will focus on: " + ", ".join(enabled) + "."


async def _save_service_state(guild_id: int, payload: dict[str, bool], actor: Any = None) -> None:
    final = {
        **payload,
        "setup_service_mode_saved_at": now_utc().isoformat(),
        "setup_completed": False,
        "__config_write_mode": "setup_services",
        "__config_write_source": "/dank setup service picker",
    }
    try:
        if actor is not None:
            final["configured_by_id"] = str(getattr(actor, "id", ""))
            final["configured_by_name"] = str(actor)
    except Exception:
        pass

    if upsert_guild_config is not None:
        await upsert_guild_config(int(guild_id), final)  # type: ignore[misc]
    elif get_supabase is not None:
        sb = get_supabase()
        if sb is None:
            raise RuntimeError("Supabase is unavailable.")

        def sync() -> None:
            sb.table("guild_configs").upsert({"guild_id": str(int(guild_id)), **final}, on_conflict="guild_id").execute()

        await asyncio.to_thread(sync)
    else:
        raise RuntimeError("No config writer is available.")

    try:
        invalidate_guild_config(int(guild_id))
    except Exception:
        pass


async def _filter_health_for_services(guild_id: int, blockers: list[str], warnings: list[str], ok: list[str]) -> tuple[list[str], list[str], list[str], ServiceState]:
    state = await load_service_state(guild_id)

    def keep(line: str) -> bool:
        text = str(line or "").lower()
        ticket_terms = ("ticket", "transcript", "archive", "open category", "closed category", "support channel")
        verify_terms = ("verify", "verification", "unverified", "verified", "resident", "vc verify", "voice")
        spam_terms = ("spam", "raid", "modlog", "mod-log", "moderation", "log", "join/exit", "join/leave")
        schema_terms = ("supabase", "database", "guild config", "guild_configs", "ticket_categories")

        if any(term in text for term in schema_terms):
            return True
        if any(term in text for term in ticket_terms):
            return state.tickets
        if any(term in text for term in verify_terms):
            return state.verification or state.voice
        if any(term in text for term in spam_terms):
            return state.spamguard or state.moderation
        return True

    return (
        [line for line in blockers if keep(line)],
        [line for line in warnings if keep(line)],
        [line for line in ok if keep(line)],
        state,
    )


class ServicePresetSelect(discord.ui.Select):
    def __init__(self, current: ServiceState) -> None:
        options = []
        for key, (label, flags, description) in PRESETS.items():
            selected = flags == current.as_payload()
            emoji = "🎫" if key == "tickets" else "✅" if key == "verification" else "🛡️" if key == "spamguard" else "🚀"
            options.append(discord.SelectOption(label=label, value=key, description=description[:100], emoji=emoji, default=selected))
        super().__init__(placeholder="Choose what this server will use", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        key = str(self.values[0])
        preset = PRESETS.get(key)
        if preset is None:
            return await interaction.response.send_message("❌ Unknown service preset.", ephemeral=True)
        label, flags, description = preset
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
        await _save_service_state(guild.id, dict(flags), interaction.user)
        state = await load_service_state(guild.id)
        embed = await build_service_picker_embed(guild, state, saved_message=f"Saved **{label}**. {description}")
        await interaction.edit_original_response(embed=embed, view=ServiceModeView(state))


class ServiceToggleButton(discord.ui.Button):
    def __init__(self, key: str, label: str, enabled: bool, emoji: str, row: int) -> None:
        super().__init__(label=f"{'On' if enabled else 'Off'}: {label}", emoji=emoji, style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary, row=row)
        self.key = key
        self.short_label = label

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        state = await load_service_state(guild.id)
        payload = state.as_payload()
        payload[self.key] = not bool(payload.get(self.key, False))
        if self.key == "spam_guard_enabled" and payload[self.key]:
            payload["moderation_enabled"] = True
        if self.key == "verification_enabled" and not payload[self.key]:
            payload["voice_verification_enabled"] = False
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
        await _save_service_state(guild.id, payload, interaction.user)
        next_state = await load_service_state(guild.id)
        embed = await build_service_picker_embed(guild, next_state, saved_message=f"Updated **{self.short_label}**.")
        await interaction.edit_original_response(embed=embed, view=ServiceModeView(next_state))


class ServiceModeView(discord.ui.View):
    def __init__(self, state: ServiceState) -> None:
        super().__init__(timeout=900)
        self.add_item(ServicePresetSelect(state))
        self.add_item(ServiceToggleButton("tickets_enabled", "Tickets", state.tickets, "🎫", 1))
        self.add_item(ServiceToggleButton("verification_enabled", "ID Verify", state.verification, "✅", 1))
        self.add_item(ServiceToggleButton("voice_verification_enabled", "Voice Verify", state.voice, "🎙️", 1))
        self.add_item(ServiceToggleButton("spam_guard_enabled", "SpamGuard", state.spamguard, "🛡️", 2))
        self.add_item(ServiceToggleButton("moderation_enabled", "Logs/Moderation", state.moderation, "🧾", 2))


async def build_service_picker_embed(guild: discord.Guild, state: Optional[ServiceState] = None, *, saved_message: str = "") -> discord.Embed:
    state = state or await load_service_state(guild.id)
    embed = discord.Embed(
        title="🧭 Choose Dank Shield Services",
        description=(
            "Pick only what this server is using. This keeps setup simple and stops Health Check from yelling about features you do not use."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    if saved_message:
        embed.add_field(name="Saved", value=saved_message[:1024], inline=False)
    embed.add_field(name="Enabled Here", value=_service_summary_text(state), inline=True)
    embed.add_field(name="What This Changes", value=_service_mode_hint(state), inline=False)
    embed.add_field(
        name="Presets",
        value=(
            "🎫 **Tickets only** — ticket panel and ticket lifecycle.\n"
            "✅ **Verification only** — ID/voice verify without ticket blockers.\n"
            "🛡️ **SpamGuard only** — protection/logging without ticket or verify blockers.\n"
            "🚀 **Everything** — full Dank Shield setup."
        ),
        inline=False,
    )
    embed.set_footer(text="Run Health Check after saving services.")
    return embed


def _patch_setup_ui() -> None:
    try:
        from stoney_verify.commands_ext import public_setup_solid as solid
    except Exception as e:
        _warn(f"public_setup_solid unavailable: {e!r}")
        return

    try:
        original_build_main = getattr(solid, "_build_main_setup_payload", None)
        if callable(original_build_main) and not getattr(original_build_main, "_service_modes_wrapped", False):
            async def wrapped_build_main(guild: discord.Guild):
                embed, view = await original_build_main(guild)
                state = await load_service_state(guild.id)
                embed.add_field(name="Enabled Services", value=_service_summary_text(state), inline=True)
                embed.add_field(name="Health Check Focus", value=_service_mode_hint(state), inline=False)
                try:
                    view.add_item(OpenServiceModeButton())
                except Exception:
                    pass
                return embed, view

            setattr(wrapped_build_main, "_service_modes_wrapped", True)
            setattr(solid, "_build_main_setup_payload", wrapped_build_main)

        original_health = getattr(solid, "_build_health_embed", None)
        if callable(original_health) and not getattr(original_health, "_service_modes_wrapped", False):
            async def wrapped_health(guild: discord.Guild):
                embed = await original_health(guild)
                state = await load_service_state(guild.id)
                try:
                    fields = list(getattr(embed, "fields", []) or [])
                    raw: dict[str, list[str]] = {"blockers": [], "warnings": [], "ok": []}
                    for field in fields:
                        name = str(getattr(field, "name", "") or "").lower()
                        value = str(getattr(field, "value", "") or "")
                        lines = [line.strip("• ").strip() for line in value.splitlines() if line.strip() and line.strip() != "✅ None"]
                        if "blocker" in name:
                            raw["blockers"].extend(lines)
                        elif "warning" in name:
                            raw["warnings"].extend(lines)
                        elif "passing" in name:
                            raw["ok"].extend(lines)
                    blockers, warnings, ok, state = await _filter_health_for_services(guild.id, raw["blockers"], raw["warnings"], raw["ok"])
                    filtered = discord.Embed(
                        title="🩺 Setup Health Check",
                        description="✅ **Ready enough to test.**" if not blockers else "🚫 **Fix the blockers first.**",
                        color=discord.Color.green() if not blockers else discord.Color.red(),
                        timestamp=now_utc(),
                    )
                    filtered.add_field(name="Enabled Services", value=_service_summary_text(state), inline=False)
                    filtered.add_field(name="Blockers", value="\n".join(blockers)[:1024] if blockers else "✅ None", inline=False)
                    filtered.add_field(name="Warnings", value="\n".join(warnings)[:1024] if warnings else "✅ None", inline=False)
                    filtered.add_field(name="Passing Checks", value="\n".join(ok)[:1024] if ok else "No passing checks reported.", inline=False)
                    filtered.add_field(name="What To Press Next", value="Use **Choose Services** if this server is not using every Dank Shield feature. Otherwise fix the blockers above, then test tickets/verification.", inline=False)
                    filtered.set_footer(text=f"Guild {guild.id} • /dank setup")
                    return filtered
                except Exception:
                    try:
                        embed.add_field(name="Enabled Services", value=_service_summary_text(state), inline=False)
                    except Exception:
                        pass
                    return embed

            setattr(wrapped_health, "_service_modes_wrapped", True)
            setattr(solid, "_build_health_embed", wrapped_health)
    except Exception as e:
        _warn(f"setup UI patch failed: {e!r}")


class OpenServiceModeButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Choose Services", emoji="🧭", style=discord.ButtonStyle.primary, row=3)

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        try:
            from stoney_verify.commands_ext.public_setup_group import _require_setup_permission
            if not await _require_setup_permission(interaction):
                return
        except Exception:
            pass
        state = await load_service_state(guild.id)
        embed = await build_service_picker_embed(guild, state)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=ServiceModeView(state), ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=ServiceModeView(state), ephemeral=True)


def install_setup_service_modes() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    _patch_setup_ui()
    _PATCHED = True
    _log("installed Phase 2 service-mode setup layer")
    return True


install_setup_service_modes()


__all__ = ["install_setup_service_modes", "load_service_state", "build_service_picker_embed"]
