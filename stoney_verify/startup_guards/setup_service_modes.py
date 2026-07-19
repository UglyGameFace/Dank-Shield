from __future__ import annotations

"""Phase 2 setup service modes for Dank Shield.

This is the owner layer for the simple `/dank setup` service picker.

Important language rule:
- Selected service = the server wants this feature in setup/health checks.
- Active guard = the runtime/security feature is actually enforcing.

The old UI mixed those two ideas and made SpamGuard look like it was lying. This
file keeps them separate and provides a native SpamGuard setup page instead of
opening the standalone `/dank spam panel` flow from setup.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Mapping, Optional

import discord

from stoney_verify.spam_guard_defaults import SPAM_GUARD_DEFAULT_ENABLED

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


@dataclass(frozen=True)
class SpamGuardActualState:
    service_selected: bool
    moderation_selected: bool
    guard_active: bool
    mode: str
    persisted: bool
    persistence_label: str
    apply_to_verified: bool
    external_only: bool
    allow_own_invites: bool
    window_seconds: int
    message_threshold: int
    duplicate_threshold: int
    invite_threshold: int
    timeout_minutes: int
    save_note: str = ""
    settings: Mapping[str, Any] | None = None


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


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


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
        return ServiceState(True, False, False, SPAM_GUARD_DEFAULT_ENABLED, SPAM_GUARD_DEFAULT_ENABLED, "defaults")

    tickets = _safe_bool(_cfg_value(cfg, "tickets_enabled", True), True)
    verification = _safe_bool(_cfg_value(cfg, "verification_enabled", False), False)
    voice = _safe_bool(_cfg_value(cfg, "voice_verification_enabled", False), False)
    spamguard = _safe_bool(_cfg_value(cfg, "spam_guard_enabled", SPAM_GUARD_DEFAULT_ENABLED), SPAM_GUARD_DEFAULT_ENABLED)
    moderation = _safe_bool(_cfg_value(cfg, "moderation_enabled", spamguard), spamguard)
    return ServiceState(tickets, verification, voice, spamguard, moderation, str(_cfg_value(cfg, "source", "guild_configs")))


def _service_summary_text(state: ServiceState) -> str:
    return (
        f"{'✅' if state.tickets else '⬜'} Tickets\n"
        f"{'✅' if state.verification else '⬜'} ID verification\n"
        f"{'✅' if state.voice else '⬜'} Voice verification\n"
        f"{'✅' if state.spamguard else '⬜'} SpamGuard service\n"
        f"{'✅' if state.moderation else '⬜'} Moderation/logging"
    )


def _service_mode_hint(state: ServiceState) -> str:
    enabled = []
    if state.tickets:
        enabled.append("Ticket Basics")
    if state.verification or state.voice:
        enabled.append("Access Roles / Verification Channels")
    if state.spamguard or state.moderation:
        enabled.append("SpamGuard setup / Logs")
    if not enabled:
        return "Choose at least one service first."
    return "Health Check will focus on: " + ", ".join(enabled) + "."


async def _save_service_state(guild_id: int, payload: dict[str, bool], actor: Any = None) -> None:
    final = {
        **payload,
        "setup_service_mode_saved_at": now_utc().isoformat(),
        "setup_completed": False,
        "__config_write_mode": "setup_builder",
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


def _spam_guard_module() -> Any | None:
    try:
        from stoney_verify import spam_guard

        return spam_guard
    except Exception:
        return None


def _default_spam_settings(guild_id: int) -> dict[str, Any]:
    return {
        "guild_id": str(int(guild_id)),
        "enabled": SPAM_GUARD_DEFAULT_ENABLED,
        "mode": "timeout",
        "apply_to_verified_users": True,
        "block_external_invites_only": True,
        "allow_server_invites": True,
        "window_seconds": 12,
        "message_threshold": 5,
        "duplicate_threshold": 3,
        "invite_threshold": 2,
        "delete_limit": 25,
        "timeout_minutes": 60,
    }


def _normalize_spam_settings(guild_id: int, raw: Mapping[str, Any] | None) -> dict[str, Any]:
    data = _default_spam_settings(guild_id)
    if isinstance(raw, Mapping):
        data.update(dict(raw))

    module = _spam_guard_module()
    normalizer = getattr(module, "_normalize_settings", None) if module is not None else None
    if callable(normalizer):
        try:
            normalized = normalizer(int(guild_id), dict(data))
            if isinstance(normalized, Mapping):
                data.update(dict(normalized))
        except Exception:
            pass

    data["guild_id"] = str(int(guild_id))
    data["enabled"] = _safe_bool(data.get("enabled", data.get("spam_blocker_enabled")), SPAM_GUARD_DEFAULT_ENABLED)
    data["mode"] = _safe_str(data.get("mode", data.get("spam_mode", "timeout")), "timeout")
    data["apply_to_verified_users"] = _safe_bool(data.get("apply_to_verified_users"), True)
    data["block_external_invites_only"] = _safe_bool(data.get("block_external_invites_only"), True)
    data["allow_server_invites"] = _safe_bool(data.get("allow_server_invites"), True)
    data["window_seconds"] = max(5, min(120, _safe_int(data.get("window_seconds"), 12)))
    data["message_threshold"] = max(2, min(25, _safe_int(data.get("message_threshold"), 5)))
    data["duplicate_threshold"] = max(2, min(15, _safe_int(data.get("duplicate_threshold"), 3)))
    data["invite_threshold"] = max(1, min(10, _safe_int(data.get("invite_threshold"), 2)))
    data["delete_limit"] = max(1, min(100, _safe_int(data.get("delete_limit"), 25)))
    data["timeout_minutes"] = max(1, min(10080, _safe_int(data.get("timeout_minutes"), 60)))
    return data


def _load_spam_settings(guild_id: int) -> dict[str, Any]:
    module = _spam_guard_module()
    getter = getattr(module, "_fast_settings_for_ui", None) if module is not None else None
    if callable(getter):
        try:
            return _normalize_spam_settings(int(guild_id), getter(int(guild_id)) or {})
        except Exception:
            pass
    return _normalize_spam_settings(int(guild_id), None)


def _spam_persistence_label(guild_id: int) -> tuple[str, bool]:
    module = _spam_guard_module()
    label = "Runtime only (resets on restart)"
    builder = getattr(module, "_build_persistence_label", None) if module is not None else None
    if callable(builder):
        try:
            label = str(builder(int(guild_id)) or label)
        except Exception:
            pass
    return label, "db-backed" in label.lower()


async def _load_spam_actual_state(guild_id: int, service_state: Optional[ServiceState] = None, *, save_note: str = "") -> SpamGuardActualState:
    service_state = service_state or await load_service_state(guild_id)
    settings = _load_spam_settings(guild_id)
    label, persisted = _spam_persistence_label(guild_id)
    return SpamGuardActualState(
        service_selected=bool(service_state.spamguard),
        moderation_selected=bool(service_state.moderation),
        guard_active=_safe_bool(settings.get("enabled", settings.get("spam_blocker_enabled")), False),
        mode=_safe_str(settings.get("mode", settings.get("spam_mode", "timeout")), "timeout"),
        persisted=bool(persisted),
        persistence_label=label,
        apply_to_verified=_safe_bool(settings.get("apply_to_verified_users"), True),
        external_only=_safe_bool(settings.get("block_external_invites_only"), True),
        allow_own_invites=_safe_bool(settings.get("allow_server_invites"), True),
        window_seconds=_safe_int(settings.get("window_seconds"), 12),
        message_threshold=_safe_int(settings.get("message_threshold"), 5),
        duplicate_threshold=_safe_int(settings.get("duplicate_threshold"), 3),
        invite_threshold=_safe_int(settings.get("invite_threshold"), 2),
        timeout_minutes=_safe_int(settings.get("timeout_minutes"), 60),
        save_note=save_note,
        settings=settings,
    )


def _spam_settings_payload(settings: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "guild_id": str(settings.get("guild_id")),
        "spam_blocker_enabled": _safe_bool(settings.get("enabled", settings.get("spam_blocker_enabled")), False),
        "spam_mode": _safe_str(settings.get("mode", settings.get("spam_mode", "timeout")), "timeout"),
        "apply_to_verified_users": _safe_bool(settings.get("apply_to_verified_users"), True),
        "block_external_invites_only": _safe_bool(settings.get("block_external_invites_only"), True),
        "allow_server_invites": _safe_bool(settings.get("allow_server_invites"), True),
        "window_seconds": _safe_int(settings.get("window_seconds"), 12),
        "message_threshold": _safe_int(settings.get("message_threshold"), 5),
        "duplicate_threshold": _safe_int(settings.get("duplicate_threshold"), 3),
        "invite_threshold": _safe_int(settings.get("invite_threshold"), 2),
        "delete_limit": _safe_int(settings.get("delete_limit"), 25),
        "timeout_minutes": _safe_int(settings.get("timeout_minutes"), 60),
        "updated_at": now_utc().isoformat(),
    }


async def _persist_spam_settings(guild_id: int, settings: dict[str, Any]) -> tuple[bool, str]:
    if get_supabase is None:
        return False, "Supabase client is unavailable."
    sb = get_supabase()
    if sb is None:
        return False, "Supabase client is unavailable."

    full_payload = _spam_settings_payload(settings)
    minimal_payload = {
        "guild_id": str(int(guild_id)),
        "spam_blocker_enabled": bool(full_payload["spam_blocker_enabled"]),
        "spam_mode": str(full_payload["spam_mode"]),
    }

    def sync() -> tuple[bool, str]:
        try:
            sb.table("guild_security_settings").upsert(full_payload, on_conflict="guild_id").execute()
            return True, "Saved to guild_security_settings."
        except Exception as first:
            try:
                sb.table("guild_security_settings").upsert(minimal_payload, on_conflict="guild_id").execute()
                return True, "Saved core SpamGuard settings to guild_security_settings."
            except Exception as second:
                return False, f"Could not persist SpamGuard settings: {type(second).__name__}: {str(second)[:180]}"

    return await asyncio.to_thread(sync)


def _cache_spam_settings(guild_id: int, settings: dict[str, Any], *, persisted: bool) -> None:
    module = _spam_guard_module()
    cacher = getattr(module, "_cache_runtime_settings", None) if module is not None else None
    if callable(cacher):
        try:
            cacher(int(guild_id), dict(settings), source="/dank setup", persisted=bool(persisted))
            return
        except Exception:
            pass
    try:
        runtime = getattr(module, "_RUNTIME_SETTINGS", None) if module is not None else None
        if isinstance(runtime, dict):
            payload = dict(settings)
            payload["__meta_source"] = "/dank setup"
            payload["__meta_persisted"] = bool(persisted)
            runtime[int(guild_id)] = payload
    except Exception:
        pass


async def _save_spam_actual_settings(guild_id: int, patch: Mapping[str, Any]) -> tuple[SpamGuardActualState, str]:
    service_state = await load_service_state(guild_id)
    settings = _load_spam_settings(guild_id)
    settings.update(dict(patch))
    settings = _normalize_spam_settings(guild_id, settings)
    persisted, note = await _persist_spam_settings(guild_id, settings)
    _cache_spam_settings(guild_id, settings, persisted=bool(persisted))
    state = await _load_spam_actual_state(guild_id, service_state, save_note=note)
    return state, note


def _spam_guard_solution_lines(state: SpamGuardActualState) -> list[str]:
    lines: list[str] = []
    if not state.service_selected:
        lines.append("**Service not selected:** press **Back to Services** and turn on **Use: SpamGuard service**.")
    if state.service_selected and not state.guard_active:
        lines.append("**Guard is not active:** press **Enable Actual Guard** on this page.")
    if state.guard_active and not state.persisted:
        lines.append(
            "**Runtime-only saving:** create/fix the `guild_security_settings` table, keep `SUPABASE_SERVICE_ROLE_KEY` set, "
            "then press **Enable Actual Guard** again. Runtime-only settings reset on restart."
        )
    if state.mode == "quarantine":
        qrid = _safe_str((state.settings or {}).get("quarantine_role_id"))
        if not qrid.isdigit():
            lines.append("**Quarantine mode needs a role:** switch to **Timeout Mode** or configure a quarantine role before using quarantine.")
    if not lines:
        lines.append("✅ No SpamGuard setup warnings. Test with a harmless duplicate/link burst in a private staff channel.")
    return lines


def _spamguard_status_text(state: SpamGuardActualState) -> str:
    return (
        f"**Service selected:** {'✅ Yes' if state.service_selected else '❌ No'}\n"
        f"**Actual guard active:** {'✅ On' if state.guard_active else '❌ Off'}\n"
        f"**Response mode:** `{state.mode}`\n"
        f"**Saving:** `{state.persistence_label}`"
    )


def _spamguard_rule_text(state: SpamGuardActualState) -> str:
    return (
        f"window=`{state.window_seconds}s` • messages=`{state.message_threshold}` • duplicates=`{state.duplicate_threshold}`\n"
        f"invite messages=`{state.invite_threshold}` • timeout=`{state.timeout_minutes}m`\n"
        f"external-only invites: {'✅ On' if state.external_only else '❌ Off'}\n"
        f"allow this server's invites: {'✅ On' if state.allow_own_invites else '❌ Off'}\n"
        f"watch verified members: {'✅ On' if state.apply_to_verified else '❌ Off'}"
    )


async def build_spamguard_setup_embed(guild: discord.Guild, *, save_note: str = "") -> discord.Embed:
    service_state = await load_service_state(guild.id)
    state = await _load_spam_actual_state(guild.id, service_state, save_note=save_note)
    embed = discord.Embed(
        title="🛡️ SpamGuard Setup",
        description=(
            "This page is part of `/dank setup`. It shows the difference between selecting SpamGuard as a service "
            "and actually turning the guard on."
        ),
        color=discord.Color.green() if state.guard_active else discord.Color.orange(),
        timestamp=now_utc(),
    )
    if save_note or state.save_note:
        embed.add_field(name="Last Action", value=(save_note or state.save_note)[:1024], inline=False)
    embed.add_field(name="Truthful Status", value=_spamguard_status_text(state), inline=False)
    embed.add_field(name="Detection Defaults", value=_spamguard_rule_text(state), inline=False)
    embed.add_field(name="Warnings + Exact Fixes", value="\n".join(_spam_guard_solution_lines(state))[:1024], inline=False)
    embed.add_field(
        name="Recommended Public Setup",
        value=(
            "Use **Timeout Mode**, keep **External Only** on, keep **Allow Own Invites** on, and keep **Watch Verified** on. "
            "That protects against hacked verified accounts without blocking your own invite links."
        ),
        inline=False,
    )
    embed.set_footer(text=f"Guild {guild.id} • /dank setup → Services → SpamGuard Setup")
    return embed


async def _show_spamguard_setup(interaction: discord.Interaction, *, save_note: str = "") -> None:
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    embed = await build_spamguard_setup_embed(guild, save_note=save_note)
    view = SpamGuardSetupView()
    if interaction.response.is_done():
        await interaction.edit_original_response(embed=embed, view=view)
    else:
        await interaction.response.edit_message(embed=embed, view=view)


async def _spamguard_update_and_show(interaction: discord.Interaction, patch: Mapping[str, Any]) -> None:
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    try:
        await interaction.response.defer()
    except Exception:
        pass
    _state, note = await _save_spam_actual_settings(guild.id, patch)
    embed = await build_spamguard_setup_embed(guild, save_note=note)
    await interaction.edit_original_response(embed=embed, view=SpamGuardSetupView())


def _line_mentions_ticket(text: str) -> bool:
    ticket_terms = ("ticket", "transcript", "archive", "open category", "closed category", "support channel", "active tickets")
    return any(term in text for term in ticket_terms)


def _line_mentions_verification(text: str) -> bool:
    verify_terms = ("verify", "verification", "unverified", "verified role", "resident role", "vc verify", "voice verification", "voice channel", "vc/ticket staff")
    return any(term in text for term in verify_terms)


def _line_mentions_spamguard(text: str) -> bool:
    spam_terms = (
        "spam",
        "spamguard",
        "spam guard",
        "raid",
        "security",
        "guild_security",
        "quarantine",
        "modlog",
        "mod-log",
        "moderation",
        "join/exit",
        "join/leave",
        "allow list",
        "allowlist",
        "exempt",
        "external invite",
        "invite blocker",
        "url flood",
        "hacked",
        "compromised",
    )
    return any(term in text for term in spam_terms)


def _line_mentions_schema(text: str) -> bool:
    schema_terms = ("supabase", "database", "guild config", "guild_configs", "ticket_categories", "tickets table", "schema")
    return any(term in text for term in schema_terms)


def _keep_health_line_for_state(line: str, state: ServiceState) -> bool:
    text = str(line or "").lower()
    if not text or text in {"✅ none", "none", "no passing checks reported."}:
        return False
    if _line_mentions_schema(text):
        return True

    mentions_verification = _line_mentions_verification(text)
    mentions_spamguard = _line_mentions_spamguard(text)
    mentions_ticket = _line_mentions_ticket(text)

    if mentions_verification and not (state.verification or state.voice):
        return False
    if mentions_spamguard and not (state.spamguard or state.moderation):
        return False
    if mentions_ticket and not state.tickets:
        return False
    return True


async def _filter_health_for_services(guild_id: int, blockers: list[str], warnings: list[str], ok: list[str]) -> tuple[list[str], list[str], list[str], ServiceState]:
    state = await load_service_state(guild_id)
    return (
        [line for line in blockers if _keep_health_line_for_state(line, state)],
        [line for line in warnings if _keep_health_line_for_state(line, state)],
        [line for line in ok if _keep_health_line_for_state(line, state)],
        state,
    )


def _warning_fix_for_line(line: str) -> Optional[str]:
    text = str(line or "").lower()
    if "verify channel and ticket panel channel are the same" in text:
        return "Separate the verify channel from the ticket panel channel in **Existing Server → Verification Channels**, or ignore only if intentionally shared."
    if "server-control role" in text and "elevated" in text:
        return "Create a lower-permission Bot Manager/setup role and select that instead of an Admin preset. Dank Shield does not need full admin for setup checks."
    if "runtime only" in text or "resets on restart" in text:
        return "Fix persistence by creating `guild_security_settings`, confirming `SUPABASE_SERVICE_ROLE_KEY`, then saving the setting again."
    if "spamguard" in text and "off" in text:
        return "Open **Services → SpamGuard Setup** and press **Enable Actual Guard**."
    if "permission" in text:
        return "Open **Existing Server** and reselect the channel/role so the bot can validate permissions, then rerun Health Check."
    return None


def _warning_fixes(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        fix = _warning_fix_for_line(line)
        if fix and fix not in out:
            out.append(f"• {fix}")
    return out[:5]


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
    def __init__(self, key: str, label: str, selected: bool, emoji: str, row: int) -> None:
        super().__init__(label=f"{'Use' if selected else 'Skip'}: {label}", emoji=emoji, style=discord.ButtonStyle.success if selected else discord.ButtonStyle.secondary, row=row)
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
        embed = await build_service_picker_embed(guild, next_state, saved_message=f"Updated selected service: **{self.short_label}**.")
        await interaction.edit_original_response(embed=embed, view=ServiceModeView(next_state))


class SpamGuardSetupButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="SpamGuard Setup", emoji="🛡️", style=discord.ButtonStyle.primary, row=3)

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            await _show_spamguard_setup(interaction)
        except Exception as e:
            msg = f"❌ SpamGuard setup failed: `{type(e).__name__}: {str(e)[:250]}`"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            else:
                await interaction.response.send_message(msg, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class NativeSetupNavigationButton(discord.ui.Button):
    """Navigate service pages through canonical setup owners."""

    def __init__(
        self,
        *,
        label: str,
        emoji: str,
        route_name: str,
        style: discord.ButtonStyle,
        custom_id: str,
    ) -> None:
        super().__init__(
            label=label,
            emoji=emoji,
            style=style,
            custom_id=custom_id,
            row=4,
        )
        self.route_name = route_name

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        from stoney_verify.commands_ext import (
            public_setup_recommend as recommend,
        )

        route = getattr(
            recommend,
            self.route_name,
            None,
        )

        if not callable(route):
            message = (
                "❌ That setup destination is unavailable. "
                "Return to `/dank setup` and try again."
            )

            if interaction.response.is_done():
                await interaction.followup.send(
                    message,
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    message,
                    ephemeral=True,
                )

            return

        await route(interaction)


def _append_native_setup_navigation(
    view: discord.ui.View,
) -> None:
    """Add the same four canonical exits to every service page."""

    view.add_item(
        NativeSetupNavigationButton(
            label="Continue Guided Setup",
            emoji="➡️",
            route_name="_open_guided_setup",
            style=discord.ButtonStyle.success,
            custom_id="dank_setup_service_nav:guided",
        )
    )
    view.add_item(
        NativeSetupNavigationButton(
            label="Setup Check",
            emoji="🩺",
            route_name="_open_health_check",
            style=discord.ButtonStyle.primary,
            custom_id="dank_setup_service_nav:check",
        )
    )
    view.add_item(
        NativeSetupNavigationButton(
            label="Advanced Options",
            emoji="⚙️",
            route_name="_open_manage_setup",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_setup_service_nav:advanced",
        )
    )
    view.add_item(
        NativeSetupNavigationButton(
            label="Setup Home",
            emoji="🏠",
            route_name="_home_edit",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_setup_service_nav:home",
        )
    )


class ServiceModeView(discord.ui.View):
    def __init__(self, state: ServiceState) -> None:
        super().__init__(timeout=900)
        self.add_item(ServicePresetSelect(state))
        self.add_item(ServiceToggleButton("tickets_enabled", "Tickets", state.tickets, "🎫", 1))
        self.add_item(ServiceToggleButton("verification_enabled", "ID Verify", state.verification, "✅", 1))
        self.add_item(ServiceToggleButton("voice_verification_enabled", "Voice Verify", state.voice, "🎙️", 1))
        self.add_item(ServiceToggleButton("spam_guard_enabled", "SpamGuard service", state.spamguard, "🛡️", 2))
        self.add_item(ServiceToggleButton("moderation_enabled", "Logs/Moderation", state.moderation, "🧾", 2))
        if state.spamguard or state.moderation:
            self.add_item(SpamGuardSetupButton())

        _append_native_setup_navigation(self)


class SpamGuardSetupView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)
        _append_native_setup_navigation(self)

    @discord.ui.button(label="Enable Actual Guard", emoji="✅", style=discord.ButtonStyle.success, row=0)
    async def enable_guard(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _spamguard_update_and_show(interaction, {"enabled": True, "mode": "timeout"})

    @discord.ui.button(label="Disable Actual Guard", emoji="🛑", style=discord.ButtonStyle.danger, row=0)
    async def disable_guard(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _spamguard_update_and_show(interaction, {"enabled": False})

    @discord.ui.button(label="Refresh Status", emoji="🔄", style=discord.ButtonStyle.secondary, row=0)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _show_spamguard_setup(interaction)

    @discord.ui.button(label="Use Timeout Mode", emoji="⏱️", style=discord.ButtonStyle.primary, row=1)
    async def timeout_mode(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _spamguard_update_and_show(interaction, {"mode": "timeout"})

    @discord.ui.button(label="External Only", emoji="🌍", style=discord.ButtonStyle.secondary, row=1)
    async def external_only(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        state = await _load_spam_actual_state(interaction.guild.id) if interaction.guild else None
        await _spamguard_update_and_show(interaction, {"block_external_invites_only": not bool(state.external_only if state else True)})

    @discord.ui.button(label="Allow Own Invites", emoji="🔗", style=discord.ButtonStyle.secondary, row=1)
    async def allow_own(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        state = await _load_spam_actual_state(interaction.guild.id) if interaction.guild else None
        await _spamguard_update_and_show(interaction, {"allow_server_invites": not bool(state.allow_own_invites if state else True)})

    @discord.ui.button(label="Watch Verified", emoji="👀", style=discord.ButtonStyle.secondary, row=2)
    async def watch_verified(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        state = await _load_spam_actual_state(interaction.guild.id) if interaction.guild else None
        await _spamguard_update_and_show(interaction, {"apply_to_verified_users": not bool(state.apply_to_verified if state else True)})

    @discord.ui.button(label="Back to Services", emoji="⬅️", style=discord.ButtonStyle.secondary, row=3)
    async def back_services(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        state = await load_service_state(guild.id)
        embed = await build_service_picker_embed(guild, state)
        await interaction.response.edit_message(embed=embed, view=ServiceModeView(state))

    @discord.ui.button(label="Advanced Standalone Panel", emoji="🧰", style=discord.ButtonStyle.secondary, row=3)
    async def advanced_panel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            from stoney_verify.commands_ext import public_spam_group

            helper = getattr(public_spam_group, "open_spamguard_panel", None)
            if callable(helper):
                await helper(interaction)
                return
        except Exception as e:
            _warn(f"advanced SpamGuard panel failed: {e!r}")
        if interaction.response.is_done():
            await interaction.followup.send("❌ Advanced SpamGuard panel is unavailable.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Advanced SpamGuard panel is unavailable.", ephemeral=True)


async def build_service_picker_embed(guild: discord.Guild, state: Optional[ServiceState] = None, *, saved_message: str = "") -> discord.Embed:
    state = state or await load_service_state(guild.id)
    spam_state = await _load_spam_actual_state(guild.id, state)
    embed = discord.Embed(
        title="🧭 Choose Dank Shield Services",
        description=(
            "Pick what this server is using. These are setup services, not necessarily active runtime toggles. "
            "SpamGuard has a separate actual guard switch below."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    if saved_message:
        embed.add_field(name="Saved", value=saved_message[:1024], inline=False)
    embed.add_field(name="Selected Services", value=_service_summary_text(state), inline=True)
    embed.add_field(name="Health Check Focus", value=_service_mode_hint(state), inline=False)
    embed.add_field(
        name="SpamGuard Truth",
        value=(
            f"Service selected: {'✅ Yes' if spam_state.service_selected else '❌ No'}\n"
            f"Actual guard active: {'✅ On' if spam_state.guard_active else '❌ Off'}\n"
            f"Saving: `{spam_state.persistence_label}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Presets",
        value=(
            "🎫 **Tickets only** — ticket panel and ticket lifecycle.\n"
            "✅ **Verification only** — ID/voice verify without ticket blockers.\n"
            "🛡️ **SpamGuard only** — select SpamGuard setup/logging without ticket or verify blockers.\n"
            "🚀 **Everything** — full Dank Shield setup."
        ),
        inline=False,
    )
    if state.spamguard or state.moderation:
        embed.add_field(
            name="SpamGuard Setup",
            value="Use **SpamGuard Setup** to turn the actual guard on/off, confirm persistence, and get exact fixes.",
            inline=False,
        )
    embed.set_footer(text="Run Health Check after saving services. Use SpamGuard Setup to activate enforcement.")
    return embed


def _format_health_value(lines: list[str], *, empty: str) -> str:
    if not lines:
        return empty
    text = "\n".join(lines)
    return text[:1024] if text else empty


def _extract_health_lines(value: str) -> list[str]:
    out: list[str] = []
    for raw in str(value or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        line = line.lstrip("•").strip()
        if line in {"✅ None", "None"}:
            continue
        out.append(line)
    return out




class OpenServiceModeButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Services", emoji="🧭", style=discord.ButtonStyle.primary, row=3)

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
    """Compatibility installer for the native service helpers.

    Service selection and SpamGuard setup are now opened by
    their native command/view owners. Importing this module
    must not replace setup-home or health builders.
    """
    global _PATCHED

    if _PATCHED:
        return True

    _PATCHED = True
    _log("native service-mode helpers available")
    return True




__all__ = [
    "install_setup_service_modes",
    "load_service_state",
    "build_service_picker_embed",
    "build_spamguard_setup_embed",
    "ServiceModeView",
    "SpamGuardSetupView",
]
