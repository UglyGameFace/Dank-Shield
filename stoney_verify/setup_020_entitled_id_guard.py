from __future__ import annotations

"""Integrated DS-SETUP-020 compatibility layer.

This module is loaded after the legacy setup guards and replaces the broken
owner-facing service picker with the canonical setup-service state model.  It
also narrows Simple Verify channel requirements to Simple Verify itself and
reconciles the Voice Verify room against the single baseline permission policy.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import Any, Mapping, Optional

import discord

from stoney_verify.setup_engine.verification_modes import (
    id_verify_allowed_for_guild,
    id_verify_allowed_guild_ids,
)
from stoney_verify.setup_service_state import (
    SetupServiceState,
    apply_custom_service_toggle,
    load_setup_service_state,
    normalize_custom_service_patch,
    save_custom_service_state,
    toggle_custom_service_state,
)
from stoney_verify.services.setup_permission_policy import vc_verification_overwrites
from stoney_verify.services.vc_verification_permissions import (
    reconcile_vc_verification_channel,
)

_PATCHED = False
_HEALTH_PATCH_LOCKS: dict[int, asyncio.Lock] = {}
_DEFAULTS_PATCH_LOCKS: dict[int, asyncio.Lock] = {}


def _loop_lock(bucket: dict[int, asyncio.Lock]) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    key = id(loop)
    lock = bucket.get(key)
    if lock is None:
        lock = asyncio.Lock()
        bucket[key] = lock
    return lock


def _state_word(value: bool) -> str:
    return "ON ✅" if value else "OFF ⬜"


def _state_payload(state: Any) -> dict[str, bool]:
    if hasattr(state, "as_payload"):
        try:
            payload = dict(state.as_payload())
        except Exception:
            payload = {}
    else:
        payload = {}
    return {
        "tickets_enabled": bool(payload.get("tickets_enabled", getattr(state, "tickets", False))),
        "verification_enabled": bool(payload.get("verification_enabled", getattr(state, "simple_verify", getattr(state, "verification", False)))),
        "voice_verification_enabled": bool(payload.get("voice_verification_enabled", getattr(state, "voice_verify", getattr(state, "voice", False)))),
        "id_verify_enabled": bool(payload.get("id_verify_enabled", getattr(state, "id_verify", False))),
        "spam_guard_enabled": bool(payload.get("spam_guard_enabled", getattr(state, "spam_guard", getattr(state, "spamguard", False)))),
        "moderation_enabled": bool(payload.get("moderation_enabled", getattr(state, "logs", getattr(state, "moderation", False)))),
    }


def _service_summary_text(state: Any) -> str:
    payload = _state_payload(state)
    return (
        f"Tickets: **{_state_word(payload['tickets_enabled'])}**\n"
        f"Simple Verify: **{_state_word(payload['verification_enabled'])}**\n"
        f"Voice Verify: **{_state_word(payload['voice_verification_enabled'])}**\n"
        f"ID / Web Verify: **{_state_word(payload['id_verify_enabled'])}**\n"
        f"SpamGuard: **{_state_word(payload['spam_guard_enabled'])}**\n"
        f"Essential Logs: **{_state_word(payload['moderation_enabled'])}**"
    )


def _custom_enabled_labels_from_payload(payload: Mapping[str, Any]) -> list[str]:
    labels: list[str] = []
    for key, label in (
        ("tickets_enabled", "Tickets"),
        ("verification_enabled", "Simple Verify"),
        ("voice_verification_enabled", "Voice Verify"),
        ("id_verify_enabled", "ID/Web Verify"),
        ("spam_guard_enabled", "SpamGuard"),
        ("moderation_enabled", "Essential Logs"),
    ):
        if bool(payload.get(key, False)):
            labels.append(label)
    return labels


def _custom_mix_label(payload: Mapping[str, Any]) -> str:
    labels = _custom_enabled_labels_from_payload(payload)
    return "Your features: " + (", ".join(labels) if labels else "No features selected")


def _service_hint_text(state: Any) -> str:
    labels = _custom_enabled_labels_from_payload(_state_payload(state))
    return (
        "Quick Setup will check: " + ", ".join(labels) + "."
        if labels
        else "Choose at least one core feature first."
    )


def _service_flags_for_choice(choice: Any) -> dict[str, bool]:
    key = str(getattr(choice, "key", "") or "").strip().lower()
    flags = {
        "tickets_enabled": False,
        "verification_enabled": False,
        "voice_verification_enabled": False,
        "id_verify_enabled": False,
        "spam_guard_enabled": False,
        "moderation_enabled": False,
    }
    if key in {"basic_server", "help_desk"}:
        flags.update(tickets_enabled=True, spam_guard_enabled=True, moderation_enabled=True)
    elif key == "basic_verify":
        flags.update(verification_enabled=True, spam_guard_enabled=True, moderation_enabled=True)
    elif key == "voice_check":
        flags.update(tickets_enabled=True, voice_verification_enabled=True, spam_guard_enabled=True, moderation_enabled=True)
    elif key == "id_check":
        flags.update(tickets_enabled=True, id_verify_enabled=True, spam_guard_enabled=True, moderation_enabled=True)
    elif key == "id_voice_check":
        flags.update(
            tickets_enabled=True,
            voice_verification_enabled=True,
            id_verify_enabled=True,
            spam_guard_enabled=True,
            moderation_enabled=True,
        )
    return flags


def _choice_payload(choice: Any) -> dict[str, Any]:
    key = str(getattr(choice, "key", "") or "").strip().lower()
    basic_verify = key == "basic_verify"
    flags = _service_flags_for_choice(choice)
    panel_style = str(getattr(choice, "panel_style", "custom") or "custom")
    verification_mode = "basic_button" if basic_verify else "custom" if key == "custom_setup" else panel_style
    return {
        **flags,
        "ticket_service_enabled": bool(flags["tickets_enabled"]),
        "basic_verify_enabled": bool(basic_verify),
        "basic_button_verify_enabled": bool(basic_verify),
        "vc_verify_enabled": bool(flags["voice_verification_enabled"]),
        "voice_verify_enabled": bool(flags["voice_verification_enabled"]),
        "verification_allows_voice": bool(flags["voice_verification_enabled"]),
        "web_verify_enabled": bool(flags["id_verify_enabled"]),
        "id_web_verify_enabled": bool(flags["id_verify_enabled"]),
        "verification_requires_id": bool(flags["id_verify_enabled"]),
        "logs_enabled": bool(flags["moderation_enabled"]),
        "setup_choice": key,
        "setup_choice_label": str(getattr(choice, "label", key) or key),
        "setup_choice_description": str(getattr(choice, "short", "") or ""),
        "setup_choice_member_sees": str(getattr(choice, "member_sees", "") or ""),
        "setup_template_version": "plain_choices_v6_entitled_id_independent",
        "ticket_flow_style": "fast_no_forced_form",
        "ticket_form_mode": "off",
        "ticket_open_requires_modal": False,
        "ticket_open_requires_form": False,
        "verification_panel_style": panel_style,
        "verification_mode": verification_mode,
        "verify_mode": verification_mode,
        "verification_style_label": str(getattr(choice, "label", key) or key),
        "stoney_baloney_style_enabled": bool(key == "id_voice_check"),
        "public_branding_mode": "guild_neutral",
    }


async def _save_custom_services(
    guild_id: int,
    payload: Mapping[str, Any],
    actor: Any,
    *,
    allow_id_verify: bool = False,
) -> None:
    allow_id_verify = bool(allow_id_verify or int(guild_id) in id_verify_allowed_guild_ids())
    await save_custom_service_state(
        int(guild_id),
        dict(payload),
        actor=actor,
        allow_id_verify=allow_id_verify,
    )


def _custom_service_config_patch(
    payload: Mapping[str, Any],
    *,
    allow_id_verify: bool = False,
) -> dict[str, Any]:
    return normalize_custom_service_patch(payload, allow_id_verify=allow_id_verify)


def _custom_preset_key_for_payload(payload: Mapping[str, Any], fresh: Any) -> str:
    keys = tuple(fresh._CUSTOM_SERVICE_FLAG_KEYS)
    clean = {key: bool(payload.get(key, False)) for key in keys}
    for preset_key, (_label, flags, _description, _emoji) in fresh.CUSTOM_PRESETS.items():
        if {key: bool(flags.get(key, False)) for key in keys} == clean:
            return str(preset_key)
    return ""


def _auto_truthy(fresh: Any, cfg: Any, *keys: str, default: bool = False) -> bool:
    for key in keys:
        value = fresh._auto_cfg_value(cfg, key, None)
        if value is not None:
            return fresh._auto_truthy(value, default)
    return bool(default)


async def _detect_existing_for_custom_service(
    guild: discord.Guild,
    cfg: Any,
    *,
    fresh: Any,
) -> tuple[dict[str, bool], list[str]]:
    tickets = bool(
        _auto_truthy(fresh, cfg, "tickets_enabled", "ticket_service_enabled")
        or fresh._cfg_has_any_id(cfg, "ticket_category_id", "ticket_panel_channel_id", "support_channel_id")
        or fresh._guild_has_category(guild, ("ticket", "support"))
    )
    simple = bool(
        _auto_truthy(fresh, cfg, "basic_verify_enabled", "basic_button_verify_enabled")
        or fresh._cfg_has_any_id(cfg, "verify_channel_id", "verification_channel_id")
        or fresh._guild_has_text_channel(guild, ("・verify", "-verify", "simple verify", "verification button"))
    )
    voice = bool(
        _auto_truthy(
            fresh,
            cfg,
            "voice_verification_enabled",
            "vc_verify_enabled",
            "voice_verify_enabled",
            "verification_allows_voice",
        )
        or fresh._cfg_has_any_id(cfg, "vc_verify_channel_id", "voice_verify_channel_id", "vc_verify_queue_channel_id", "vc_queue_channel_id")
        or fresh._guild_has_voice_channel(guild, ("voice verify", "voice verification", "vc verify"))
    )
    entitled = id_verify_allowed_for_guild(guild)
    panel_style = str(fresh._auto_cfg_value(cfg, "verification_panel_style", "") or "").strip().lower()
    id_verify = bool(
        entitled
        and (
            _auto_truthy(
                fresh,
                cfg,
                "id_verify_enabled",
                "web_verify_enabled",
                "id_web_verify_enabled",
                "verification_requires_id",
            )
            or panel_style in {"id_check", "id_voice_check"}
        )
    )
    spam_guard = _auto_truthy(fresh, cfg, "spam_guard_enabled")
    logs = bool(
        _auto_truthy(fresh, cfg, "moderation_enabled", "logs_enabled")
        or fresh._cfg_has_any_id(cfg, "modlog_channel_id", "raidlog_channel_id", "force_verify_log_channel_id")
        or fresh._guild_has_text_channel(guild, ("mod log", "mod-log", "moderation log", "security log"))
    )
    if voice or id_verify:
        tickets = True
        logs = True
    if spam_guard:
        logs = True
    payload = {
        "tickets_enabled": tickets,
        "verification_enabled": simple,
        "voice_verification_enabled": voice,
        "id_verify_enabled": id_verify,
        "spam_guard_enabled": spam_guard,
        "moderation_enabled": logs,
    }
    found = _custom_enabled_labels_from_payload(payload)
    return payload, [f"Found **{label}** already configured." for label in found]


class _CustomServicePresetSelect(discord.ui.Select):
    def __init__(self, state: Any, *, fresh: Any, guild: Optional[discord.Guild] = None) -> None:
        self._fresh = fresh
        self._guild = guild
        current = _custom_preset_key_for_payload(_state_payload(state), fresh)
        entitled = id_verify_allowed_for_guild(guild)
        options: list[discord.SelectOption] = []
        for key, (label, _flags, description, emoji) in fresh.CUSTOM_PRESETS.items():
            if key in {"id_verify", "id_voice_verify"} and not entitled:
                continue
            options.append(
                discord.SelectOption(
                    label=str(label),
                    value=str(key),
                    description=str(description)[:100],
                    emoji=str(emoji),
                    default=str(key) == current,
                )
            )
        super().__init__(
            placeholder="Choose a starting feature mix",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="dank_setup_custom_service_preset:v6",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await self._fresh.solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        selected = str(self.values[0] or "")
        preset = self._fresh.CUSTOM_PRESETS.get(selected)
        if preset is None:
            return await interaction.response.send_message("❌ That feature mix is no longer available. Reopen setup.", ephemeral=True)
        _label, flags, _description, _emoji = preset
        allow_id = id_verify_allowed_for_guild(guild)
        await self._fresh.solid._safe_defer_update(interaction)
        await _save_custom_services(guild.id, flags, interaction.user, allow_id_verify=allow_id)
        state = await load_setup_service_state(guild.id)
        await self._fresh.solid._edit_or_followup(
            interaction,
            embed=self._fresh._custom_services_embed(guild, state),
            view=self._fresh.CustomServiceModeView(state, guild=guild),
        )


class _CustomServiceToggleButton(discord.ui.Button):
    def __init__(self, key: str, label: str, emoji: str, selected: bool, *, fresh: Any, guild: Optional[discord.Guild] = None, row: int = 1) -> None:
        self._fresh = fresh
        self._guild = guild
        self.key = str(key)
        self.selected = bool(selected)
        super().__init__(
            label=f"{label}: {'ON ✅' if selected else 'OFF ⬜'}",
            emoji=emoji,
            style=discord.ButtonStyle.success if selected else discord.ButtonStyle.secondary,
            custom_id=f"dank_setup_custom_service_toggle:{self.key}:v6",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await self._fresh.solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        allow_id = id_verify_allowed_for_guild(guild)
        if self.key == "id_verify_enabled" and not allow_id:
            return await interaction.response.send_message("❌ ID/Web Verify is not available for this server.", ephemeral=True)
        await self._fresh.solid._safe_defer_update(interaction)
        state, _effective, changed, note = await toggle_custom_service_state(
            guild.id,
            self.key,
            actor=interaction.user,
            allow_id_verify=allow_id,
            expected_current=self.selected,
        )
        if self.key == "voice_verification_enabled" and changed and not state.voice_verify:
            try:
                from stoney_verify.verification_new.vc_session_runtime_service import reconcile_voice_service_disabled
                result = await reconcile_voice_service_disabled(guild.id, reason="Voice Verify disabled from /dank setup")
                sessions = int(result.get("sessions_closed", 0) or 0)
                requests = int(result.get("requests_closed", 0) or 0)
                if sessions or requests:
                    suffix = f"Closed {sessions} active session(s) and {requests} pending request(s)."
                    note = f"{note}\n{suffix}" if note else suffix
            except Exception as exc:
                suffix = f"Voice Verify was saved OFF, but cleanup reported `{type(exc).__name__}`."
                note = f"{note}\n{suffix}" if note else suffix
        embed = self._fresh._custom_services_embed(guild, state)
        if note:
            embed.add_field(name="Saved" if changed else "Refreshed", value=str(note)[:1024], inline=False)
        await self._fresh.solid._edit_or_followup(
            interaction,
            embed=embed,
            view=self._fresh.CustomServiceModeView(state, guild=guild),
        )


class _CustomServiceModeView(discord.ui.View):
    def __init__(self, state: Any, *, fresh: Any, guild: Optional[discord.Guild] = None) -> None:
        super().__init__(timeout=900)
        self._fresh = fresh
        self._guild = guild
        payload = _state_payload(state)
        self.add_item(_CustomServicePresetSelect(state, fresh=fresh, guild=guild))
        specs = (
            ("tickets_enabled", "Tickets", "🎫", 1),
            ("verification_enabled", "Simple Verify", "✅", 1),
            ("voice_verification_enabled", "Voice Verify", "🎙️", 1),
        )
        for key, label, emoji, row in specs:
            self.add_item(_CustomServiceToggleButton(key, label, emoji, payload[key], fresh=fresh, guild=guild, row=row))
        if id_verify_allowed_for_guild(guild):
            self.add_item(_CustomServiceToggleButton("id_verify_enabled", "ID/Web Verify", "🪪", payload["id_verify_enabled"], fresh=fresh, guild=guild, row=1))
        for key, label, emoji in (
            ("spam_guard_enabled", "SpamGuard", "🛡️"),
            ("moderation_enabled", "Essential Logs", "🧾"),
        ):
            self.add_item(_CustomServiceToggleButton(key, label, emoji, payload[key], fresh=fresh, guild=guild, row=2))
        self.add_item(fresh.CustomServiceContinueButton())
        self.add_item(fresh.CustomServiceBackButton())
        self.add_item(fresh.CustomServiceHomeButton())
        self.add_item(fresh.CustomServiceCloseButton())


async def _open_custom_service_picker(interaction: discord.Interaction, *, fresh: Any) -> None:
    if not await fresh.solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    await fresh.solid._safe_defer_update(interaction)
    state = await load_setup_service_state(guild.id)
    await fresh.solid._edit_or_followup(
        interaction,
        embed=fresh._custom_services_embed(guild, state),
        view=fresh.CustomServiceModeView(state, guild=guild),
    )


def _canonical_service_scope(cfg: Any) -> dict[str, bool]:
    from stoney_verify.setup_service_state import service_state_from_config
    state = service_state_from_config(cfg)
    choice = str(state.setup_choice or "").strip().lower()
    resident = False
    try:
        resident = bool(cfg.get("verification_resident_role_enabled", choice == "id_voice_check"))
    except Exception:
        resident = choice == "id_voice_check"
    return {
        "tickets": bool(state.tickets),
        "verify": bool(state.verification_enabled),
        "basic_verify": bool(state.simple_verify),
        "voice": bool(state.voice_verify),
        "id": bool(state.id_verify),
        "spam_guard": bool(state.spam_guard),
        "logs": bool(state.logs),
        "welcome": bool(choice == "basic_server"),
        "resident_role": bool(resident),
    }


def _voice_overwrites_compat(
    guild: discord.Guild,
    staff_role: Optional[discord.Role],
    control_role: Optional[discord.Role],
    unverified_role: Optional[discord.Role],
) -> dict[object, discord.PermissionOverwrite]:
    return vc_verification_overwrites(
        guild,
        staff_role=staff_role,
        control_role=control_role,
        unverified_role=unverified_role,
        verified_role=None,
        resident_role=None,
    )


async def _guided_setup_target(guild: discord.Guild, *, recommend: Any) -> tuple[str, str, str, str]:
    try:
        cfg = await recommend.get_guild_config(guild.id, refresh=True)
    except Exception:
        return "retry", "Try Setup Again", "Dank Shield could not read the saved setup.", "retry"
    setup_choice = str(recommend._cfg_value(cfg, "setup_choice", "") or "").strip()
    if not setup_choice:
        return "setup_type", "Choose What Dank Shield Should Do", "Pick the setup that most closely matches this server.", "setup_type"
    services = recommend._selected_setup_services(cfg)
    if not any((services["tickets"], services["verify"], services["spam_guard"], services["logs"])):
        return "services", "Choose Which Features Are On", "You have not turned on any features yet.", "services"
    bot_permissions = getattr(getattr(guild, "me", None), "guild_permissions", None)
    missing = recommend._missing_setup_permissions(bot_permissions, services)
    if missing:
        return "permissions", "Give Dank Shield Its Permissions", ", ".join(missing), "permissions"
    if services["tickets"]:
        if not recommend._has_role(guild, cfg, "staff_role_id"):
            return "roles", "Choose the Ticket Staff Role", "Pick the role for people who answer tickets.", "ticket_staff_role"
        if not recommend._has_channel(guild, cfg, "ticket_category_id"):
            return "folders", "Choose the New-Ticket Folder", "Pick the Discord category where tickets open.", "ticket_folder"
        try:
            category_load = await recommend.solid._category_load(guild)
            if category_load.error or not category_load.rows:
                return "ticket_choices", "Create Ticket Choices", "Choose what members can request when they open a ticket.", "ticket_choices"
        except Exception:
            return "ticket_choices", "Check Ticket Choices", "Dank Shield could not confirm the ticket choices yet.", "ticket_choices"
    if services["basic_verify"] and not recommend._has_channel(guild, cfg, "verify_channel_id", "verification_channel_id"):
        return "channels", "Choose the Simple Verify Channel", "Pick where members should press Simple Verify.", "verification_channel"
    if services["verify"] and not recommend._has_role(guild, cfg, "verified_role_id", "member_role_id", "approved_role_id"):
        return "roles", "Choose the Approved-Member Role", "Pick the role members receive after verification.", "verified_role"
    if services["voice"]:
        if not recommend._has_typed_channel(guild, cfg, discord.VoiceChannel, "vc_verify_channel_id", "vc_verify_vc_id", "voice_verify_channel_id"):
            return "channels", "Set Up the Private Voice Verify Room", "Dank Shield will connect or create the private room used only by the active requester and assigned staff.", "voice_verify_channel"
        if not recommend._has_typed_channel(guild, cfg, discord.TextChannel, "vc_verify_queue_channel_id", "vc_queue_channel_id", "vc_request_channel_id", "vc_verify_requests_channel_id"):
            return "channels", "Set Up Voice Verify Staff Requests", "Dank Shield will connect or create the private text channel where staff receive and claim Voice Verify requests.", "voice_verify_staff_channel"
    if services["id"] and not id_verify_allowed_for_guild(guild):
        return "setup_type", "Choose a Different Verification Type", "ID/Web Verify is not available for this server. Choose Simple Verify or Voice Verify.", "setup_type"
    if services["logs"] and not recommend._has_channel(guild, cfg, "modlog_channel_id", "raidlog_channel_id"):
        return "logs", "Choose the Moderation Log Channel", "Pick where moderation and security records should be posted.", "modlog_channel"
    return "ready", "Setup Is Ready to Test", "All required items for the enabled features are configured.", "ready"


@asynccontextmanager
async def _pretend_simple_channel_present(recommend: Any, enabled: bool):
    if not enabled:
        yield
        return
    lock = _loop_lock(_HEALTH_PATCH_LOCKS)
    async with lock:
        original = recommend._has_channel

        def wrapped(guild: Any, cfg: Any, *keys: str) -> bool:
            keyset = set(keys)
            if keyset and keyset.issubset({"verify_channel_id", "verification_channel_id"}):
                return True
            return original(guild, cfg, *keys)

        recommend._has_channel = wrapped
        try:
            yield
        finally:
            recommend._has_channel = original


async def _build_plain_setup_health_embed(guild: discord.Guild, *, recommend: Any, original: Any) -> discord.Embed:
    try:
        cfg = await recommend.get_guild_config(guild.id, refresh=True)
        services = recommend._selected_setup_services(cfg)
    except Exception:
        services = {"verify": False, "basic_verify": False}
    specialized_without_simple = bool(services.get("verify") and not services.get("basic_verify"))
    async with _pretend_simple_channel_present(recommend, specialized_without_simple):
        embed = await original(guild)
    if specialized_without_simple:
        replacement = "Simple Verify is OFF, so no Simple Verify channel is required."
        for field in embed.fields:
            try:
                text = str(field.value)
                if "The verification channel is chosen." in text:
                    field.value = text.replace("The verification channel is chosen.", replacement)
            except Exception:
                pass
    return embed


async def _setup_progress(guild: discord.Guild, *, recommend: Any, original: Any) -> Any:
    try:
        cfg = await recommend.get_guild_config(guild.id, refresh=True)
        services = recommend._selected_setup_services(cfg)
    except Exception:
        services = {"verify": False, "basic_verify": False}
    specialized_without_simple = bool(services.get("verify") and not services.get("basic_verify"))
    async with _pretend_simple_channel_present(recommend, specialized_without_simple):
        return await original(guild)


class _SkippedSimpleChannel:
    id = 0
    name = "Simple Verify is disabled"
    mention = "Simple Verify is disabled"


async def _setup_defaults_callback(
    interaction: discord.Interaction,
    control_role: Optional[discord.Role] = None,
    staff_role: Optional[discord.Role] = None,
    create_missing_roles: bool = True,
    apply_channel_permissions: bool = True,
    *,
    defaults: Any,
    original: Any,
) -> bool:
    guild = interaction.guild
    if guild is None:
        return await original(interaction, control_role, staff_role, create_missing_roles, apply_channel_permissions)
    try:
        cfg = await defaults.get_guild_config(guild.id, refresh=True)
        services = _canonical_service_scope(cfg)
    except Exception:
        services = {"verify": False, "basic_verify": False}
    skip_simple = bool(services.get("verify") and not services.get("basic_verify"))
    if not skip_simple:
        return await original(interaction, control_role, staff_role, create_missing_roles, apply_channel_permissions)

    lock = _loop_lock(_DEFAULTS_PATCH_LOCKS)
    async with lock:
        original_ensure_text = defaults._ensure_text
        original_repair = defaults._repair_existing_permissions
        original_upsert = defaults._upsert_config

        async def ensure_text(guild_obj: Any, name: str, **kwargs: Any) -> Any:
            if str(name) == str(defaults.VERIFY_CHANNEL_NAME):
                return _SkippedSimpleChannel()
            return await original_ensure_text(guild_obj, name, **kwargs)

        async def repair(channel: Any, overwrites: Any, **kwargs: Any) -> Any:
            if isinstance(channel, _SkippedSimpleChannel):
                return None
            return await original_repair(channel, overwrites, **kwargs)

        async def upsert(guild_id: int, payload: Mapping[str, Any]) -> Any:
            clean = dict(payload)
            for key in ("verify_channel_id", "verification_channel_id"):
                if str(clean.get(key, "")) in {"0", ""}:
                    clean.pop(key, None)
            return await original_upsert(guild_id, clean)

        defaults._ensure_text = ensure_text
        defaults._repair_existing_permissions = repair
        defaults._upsert_config = upsert
        try:
            return await original(interaction, control_role, staff_role, create_missing_roles, apply_channel_permissions)
        finally:
            defaults._ensure_text = original_ensure_text
            defaults._repair_existing_permissions = original_repair
            defaults._upsert_config = original_upsert


async def _guided_save_existing_item(
    interaction: discord.Interaction,
    requirement_key: str,
    item: Any,
    *,
    recommend: Any,
    original: Any,
) -> None:
    await original(interaction, requirement_key, item)
    guild = interaction.guild
    if guild is not None and str(requirement_key) == "voice_verify_channel":
        try:
            await reconcile_vc_verification_channel(guild, item)
        except Exception:
            pass


async def _guided_create_voice_bundle(
    guild: discord.Guild,
    cfg: Any,
    *,
    recommend: Any,
    original: Any,
) -> Any:
    result = await original(guild, cfg)
    try:
        payload = result[0]
        channel_id = int(payload.get("vc_verify_channel_id", 0) or 0)
        channel = guild.get_channel(channel_id) if channel_id > 0 else None
        if channel is not None:
            await reconcile_vc_verification_channel(guild, channel, cfg=cfg)
    except Exception:
        pass
    return result


def install() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    from stoney_verify.commands_ext import public_setup_defaults as defaults
    from stoney_verify.commands_ext import public_setup_fresh_choice as fresh
    from stoney_verify.commands_ext import public_setup_recommend as recommend

    fresh._CUSTOM_SERVICE_FLAG_KEYS = (
        "tickets_enabled",
        "verification_enabled",
        "voice_verification_enabled",
        "id_verify_enabled",
        "spam_guard_enabled",
        "moderation_enabled",
    )
    fresh.CUSTOM_PRESETS = {
        "tickets": (
            "Tickets only",
            {"tickets_enabled": True, "verification_enabled": False, "voice_verification_enabled": False, "id_verify_enabled": False, "spam_guard_enabled": False, "moderation_enabled": False},
            "Support ticket panel and ticket tools.",
            "🎫",
        ),
        "basic_verify": (
            "Simple Verify only",
            {"tickets_enabled": False, "verification_enabled": True, "voice_verification_enabled": False, "id_verify_enabled": False, "spam_guard_enabled": False, "moderation_enabled": False},
            "One Verify button. No ID upload or voice check.",
            "✅",
        ),
        "voice_verify": (
            "Voice Verify",
            {"tickets_enabled": True, "verification_enabled": False, "voice_verification_enabled": True, "id_verify_enabled": False, "spam_guard_enabled": False, "moderation_enabled": True},
            "Staff voice verification without forcing Simple Verify.",
            "🎙️",
        ),
        "id_verify": (
            "ID / Web Verify",
            {"tickets_enabled": True, "verification_enabled": False, "voice_verification_enabled": False, "id_verify_enabled": True, "spam_guard_enabled": False, "moderation_enabled": True},
            "Private ID/Web verification for approved servers.",
            "🪪",
        ),
        "id_voice_verify": (
            "ID / Web + Voice",
            {"tickets_enabled": True, "verification_enabled": False, "voice_verification_enabled": True, "id_verify_enabled": True, "spam_guard_enabled": False, "moderation_enabled": True},
            "Private ID/Web verification plus staff voice verification.",
            "🔐",
        ),
        "spamguard": (
            "SpamGuard only",
            {"tickets_enabled": False, "verification_enabled": False, "voice_verification_enabled": False, "id_verify_enabled": False, "spam_guard_enabled": True, "moderation_enabled": True},
            "Spam and raid protection with logs.",
            "🛡️",
        ),
        "all": (
            "All Core Features",
            {"tickets_enabled": True, "verification_enabled": True, "voice_verification_enabled": True, "id_verify_enabled": False, "spam_guard_enabled": True, "moderation_enabled": True},
            "Tickets, Simple Verify, Voice Verify, SpamGuard, and essential logs.",
            "🚀",
        ),
    }

    fresh._service_summary_text = _service_summary_text
    fresh._custom_enabled_labels_from_payload = _custom_enabled_labels_from_payload
    fresh._custom_mix_label = _custom_mix_label
    fresh._service_hint_text = _service_hint_text
    fresh._service_flags_for_choice = _service_flags_for_choice
    fresh._choice_payload = _choice_payload
    fresh._custom_service_config_patch = _custom_service_config_patch
    fresh._save_custom_services = _save_custom_services
    fresh._apply_custom_service_toggle = apply_custom_service_toggle
    fresh._custom_preset_key_for_payload = lambda payload: _custom_preset_key_for_payload(payload, fresh)

    async def detect(guild: discord.Guild, cfg: Any) -> tuple[dict[str, bool], list[str]]:
        return await _detect_existing_for_custom_service(guild, cfg, fresh=fresh)

    fresh._detect_existing_for_custom_service = detect
    fresh.CustomServicePresetSelect = lambda state, guild=None: _CustomServicePresetSelect(state, fresh=fresh, guild=guild)
    fresh.CustomServiceToggleButton = lambda key, label, emoji, selected, row=1, guild=None: _CustomServiceToggleButton(key, label, emoji, selected, fresh=fresh, guild=guild, row=row)

    class CustomServiceModeView(_CustomServiceModeView):
        def __init__(self, state: Any, guild: Optional[discord.Guild] = None) -> None:
            super().__init__(state, fresh=fresh, guild=guild)

    fresh.CustomServiceModeView = CustomServiceModeView

    async def open_picker(interaction: discord.Interaction) -> None:
        await _open_custom_service_picker(interaction, fresh=fresh)

    fresh._open_custom_service_picker = open_picker

    defaults._service_scope_from_config = _canonical_service_scope
    defaults._voice_overwrites = _voice_overwrites_compat
    original_defaults_callback = defaults._setup_defaults_callback

    async def defaults_callback(
        interaction: discord.Interaction,
        control_role: Optional[discord.Role] = None,
        staff_role: Optional[discord.Role] = None,
        create_missing_roles: bool = True,
        apply_channel_permissions: bool = True,
    ) -> bool:
        return await _setup_defaults_callback(
            interaction,
            control_role,
            staff_role,
            create_missing_roles,
            apply_channel_permissions,
            defaults=defaults,
            original=original_defaults_callback,
        )

    defaults._setup_defaults_callback = defaults_callback
    try:
        command = defaults.dank_group.get_command("setup-defaults")
        if command is not None:
            command.callback = defaults_callback
    except Exception:
        pass

    recommend._guided_setup_target = lambda guild: _guided_setup_target(guild, recommend=recommend)
    original_health = recommend._build_plain_setup_health_embed

    async def health(guild: discord.Guild) -> discord.Embed:
        return await _build_plain_setup_health_embed(guild, recommend=recommend, original=original_health)

    recommend._build_plain_setup_health_embed = health
    if hasattr(recommend, "_setup_progress"):
        original_progress = recommend._setup_progress

        async def progress(guild: discord.Guild) -> Any:
            return await _setup_progress(guild, recommend=recommend, original=original_progress)

        recommend._setup_progress = progress

    original_save_existing = recommend._guided_save_existing_item

    async def save_existing(interaction: discord.Interaction, requirement_key: str, item: Any) -> None:
        await _guided_save_existing_item(interaction, requirement_key, item, recommend=recommend, original=original_save_existing)

    recommend._guided_save_existing_item = save_existing
    original_voice_bundle = recommend._guided_create_voice_bundle

    async def voice_bundle(guild: discord.Guild, cfg: Any) -> Any:
        return await _guided_create_voice_bundle(guild, cfg, recommend=recommend, original=original_voice_bundle)

    recommend._guided_create_voice_bundle = voice_bundle

    _PATCHED = True
    try:
        print("✅ DS-SETUP-020: entitled ID selection and Voice Verify permission integration active")
    except Exception:
        pass
    return True


__all__ = ["install"]
