from __future__ import annotations

"""Plain setup choice owner for /dank setup."""

from dataclasses import dataclass
from typing import Any, Optional

import discord

from ..globals import now_utc
from . import public_setup_recommend as recommend

from . import public_setup_solid as solid
from ..setup_engine.verification_modes import id_verify_allowed_for_guild



@dataclass(frozen=True)
class PlainSetupChoice:
    key: str
    label: str
    emoji: str
    short: str
    member_sees: str
    needs_tickets: bool
    needs_id: bool
    needs_voice: bool
    panel_style: str


SETUP_CHOICES: tuple[PlainSetupChoice, ...] = (
    PlainSetupChoice("basic_server", "Tickets + Server Basics", "🏠", "Sets up support tickets and basic logs. A good choice for most servers that do not need member verification.", "A support button when they need help from staff.", True, False, False, "basic"),
    PlainSetupChoice("basic_verify", "Simple Verify", "✅", "Members press one Verify button to get the member role. No ID upload or voice check.", "One Verify button that gives them server access.", False, False, False, "basic_verify"),
    PlainSetupChoice("help_desk", "Help Desk / Tickets", "🎫", "Sets up support tickets for help requests, reports, appeals, and staff support.", "A ticket panel where they choose what they need help with.", True, False, False, "help_desk"),
    PlainSetupChoice("voice_check", "Voice Verify", "🎙️", "Members request staff voice verification without ID upload or website upload flow.", "A verification ticket with a button to request a staff voice check.", True, False, True, "voice_check"),
    PlainSetupChoice("id_check", "ID / Web Verify", "🪪", "Private ID upload verification for servers approved to use this feature.", "A private button to upload an ID for staff review.", True, True, False, "id_check"),
    PlainSetupChoice("id_voice_check", "ID / Web + Voice", "🔐", "Private ID upload plus a staff voice check for servers approved to use this feature.", "Private ID upload and a button to request a staff voice check.", True, True, True, "id_voice_check"),
    PlainSetupChoice("custom_setup", "Choose My Own Features", "⚙️", "Choose exactly which features you want: tickets, Simple Verify, Voice Verify, SpamGuard, and logs.", "Only the features you choose on the next screen.", False, False, False, "custom"),
)

CHOICES_BY_KEY: dict[str, PlainSetupChoice] = {choice.key: choice for choice in SETUP_CHOICES}

CUSTOM_PRESETS: dict[str, tuple[str, dict[str, bool], str, str]] = {
    "tickets": ("Tickets only", {"tickets_enabled": True, "verification_enabled": False, "voice_verification_enabled": False, "spam_guard_enabled": False, "moderation_enabled": False}, "Support ticket panel and ticket tools.", "🎫"),
    "basic_verify": ("Simple Verify only", {"tickets_enabled": False, "verification_enabled": True, "voice_verification_enabled": False, "spam_guard_enabled": False, "moderation_enabled": False}, "One Verify button. No tickets, ID upload, or voice check.", "✅"),
    "voice_verify": ("Simple + Voice Verify", {"tickets_enabled": True, "verification_enabled": True, "voice_verification_enabled": True, "spam_guard_enabled": False, "moderation_enabled": True}, "Simple Verify plus a staff voice check.", "🎙️"),
    "spamguard": ("SpamGuard only", {"tickets_enabled": False, "verification_enabled": False, "voice_verification_enabled": False, "spam_guard_enabled": True, "moderation_enabled": True}, "Spam and raid protection with logs.", "🛡️"),
    "all": ("Everything", {"tickets_enabled": True, "verification_enabled": True, "voice_verification_enabled": True, "spam_guard_enabled": True, "moderation_enabled": True}, "Tickets, Simple Verify, Voice Verify, SpamGuard, and logs.", "🚀"),
}


def get_plain_setup_choice(key: Any) -> Optional[PlainSetupChoice]:
    return CHOICES_BY_KEY.get(str(key or "").strip().lower())


def _choices_for_guild(guild: Optional[discord.Guild]) -> tuple[PlainSetupChoice, ...]:
    return SETUP_CHOICES if id_verify_allowed_for_guild(guild) else tuple(choice for choice in SETUP_CHOICES if not choice.needs_id)


def _plain_saved_choice_from_cfg(cfg: Any) -> str:
    for key in ("setup_choice_label",):
        try:
            text = str(getattr(cfg, key, "") or "").strip()
            if text:
                return text
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                text = str(cfg.get(key) or "").strip()
                if text:
                    return text
        except Exception:
            pass
    try:
        raw = str(getattr(cfg, "setup_choice", "") or "").strip()
        choice = get_plain_setup_choice(raw)
        if choice:
            return choice.label
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            raw = str(cfg.get("setup_choice") or "").strip()
            choice = get_plain_setup_choice(raw)
            if choice:
                return choice.label
    except Exception:
        pass
    return "Not chosen yet"


def _service_flags_for_choice(choice: PlainSetupChoice) -> dict[str, bool]:
    if choice.key == "basic_server":
        return {"tickets_enabled": True, "verification_enabled": False, "voice_verification_enabled": False, "spam_guard_enabled": True, "moderation_enabled": True}
    if choice.key == "basic_verify":
        return {"tickets_enabled": False, "verification_enabled": True, "voice_verification_enabled": False, "spam_guard_enabled": True, "moderation_enabled": True}
    if choice.key == "help_desk":
        return {"tickets_enabled": True, "verification_enabled": False, "voice_verification_enabled": False, "spam_guard_enabled": True, "moderation_enabled": True}
    if choice.key == "voice_check":
        return {"tickets_enabled": True, "verification_enabled": True, "voice_verification_enabled": True, "spam_guard_enabled": True, "moderation_enabled": True}
    if choice.key in {"id_check", "id_voice_check"}:
        return {"tickets_enabled": True, "verification_enabled": True, "voice_verification_enabled": bool(choice.needs_voice), "spam_guard_enabled": True, "moderation_enabled": True}
    return {"tickets_enabled": False, "verification_enabled": False, "voice_verification_enabled": False, "spam_guard_enabled": False, "moderation_enabled": False}


def _choice_payload(choice: PlainSetupChoice) -> dict[str, Any]:
    basic_verify = choice.key == "basic_verify"
    service_flags = _service_flags_for_choice(choice)
    verification_mode = "basic_button" if basic_verify else "custom" if choice.key == "custom_setup" else choice.panel_style
    return {
        **service_flags,
        "setup_choice": choice.key,
        "setup_choice_label": choice.label,
        "setup_choice_description": choice.short,
        "setup_choice_member_sees": choice.member_sees,
        "setup_template_version": "plain_choices_v4_custom_basic_verify_picker",
        "ticket_service_enabled": bool(service_flags.get("tickets_enabled", False)),
        "ticket_flow_style": "fast_no_forced_form",
        "ticket_form_mode": "off",
        "ticket_open_requires_modal": False,
        "ticket_open_requires_form": False,
        "verification_panel_style": choice.panel_style,
        "verification_mode": verification_mode,
        "verify_mode": verification_mode,
        "basic_verify_enabled": bool(basic_verify),
        "basic_button_verify_enabled": bool(basic_verify),
        "verification_requires_id": bool(choice.needs_id),
        "verification_allows_voice": bool(choice.needs_voice),
        "verification_style_label": choice.label,
        "stoney_baloney_style_enabled": bool(choice.key == "id_voice_check"),
        "public_branding_mode": "guild_neutral",
    }


async def _save_choice(interaction: discord.Interaction, choice: PlainSetupChoice) -> None:
    await solid._save_config(interaction, _choice_payload(choice))  # type: ignore[attr-defined]


def _state_word(value: bool) -> str:
    return "ON ✅" if value else "OFF ⬜"


def _service_summary_text(state: Any) -> str:
    return (
        f"Tickets: **{_state_word(bool(state.tickets))}**\n"
        f"Simple Verify: **{_state_word(bool(state.verification))}**\n"
        f"Voice Verify: **{_state_word(bool(state.voice))}**\n"
        f"SpamGuard: **{_state_word(bool(state.spamguard))}**\n"
        f"Logs: **{_state_word(bool(state.moderation))}**"
    )


def _custom_enabled_labels_from_payload(payload: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    if bool(payload.get("tickets_enabled")):
        labels.append("Tickets")
    if bool(payload.get("verification_enabled")):
        labels.append("Simple Verify")
    if bool(payload.get("voice_verification_enabled")):
        labels.append("Voice Verify")
    if bool(payload.get("spam_guard_enabled")):
        labels.append("SpamGuard")
    if bool(payload.get("moderation_enabled")):
        labels.append("Logs")
    return labels


def _custom_mix_label(payload: dict[str, Any]) -> str:
    labels = _custom_enabled_labels_from_payload(payload)
    return "Your features: " + (", ".join(labels) if labels else "No features selected")


def _custom_preset_key_for_payload(payload: dict[str, Any]) -> str:
    clean = {key: bool(payload.get(key, False)) for key in _CUSTOM_SERVICE_FLAG_KEYS}
    for preset_key, (_label, flags, _desc, _emoji) in CUSTOM_PRESETS.items():
        preset_clean = {key: bool(flags.get(key, False)) for key in _CUSTOM_SERVICE_FLAG_KEYS}
        if preset_clean == clean:
            return preset_key
    return ""


def _custom_service_config_patch(payload: dict[str, Any]) -> dict[str, Any]:
    """Save service switches plus the derived setup flags other modules read."""

    clean = {key: bool(payload.get(key, False)) for key in _CUSTOM_SERVICE_FLAG_KEYS}
    voice_on = bool(clean.get("voice_verification_enabled", False))
    basic_on = bool(clean.get("verification_enabled", False))
    tickets_on = bool(clean.get("tickets_enabled", False))
    spam_on = bool(clean.get("spam_guard_enabled", False))
    logs_on = bool(clean.get("moderation_enabled", False))

    if voice_on:
        basic_on = True
        tickets_on = True
        logs_on = True

    clean.update(
        {
            "tickets_enabled": tickets_on,
            "ticket_service_enabled": tickets_on,
            "verification_enabled": basic_on,
            "basic_verify_enabled": basic_on,
            "basic_button_verify_enabled": basic_on,
            "voice_verification_enabled": voice_on,
            "vc_verify_enabled": voice_on,
            "voice_verify_enabled": voice_on,
            "verification_allows_voice": voice_on,
            "spam_guard_enabled": spam_on,
            "moderation_enabled": logs_on,
            "logs_enabled": logs_on,
            # Custom setup is public-safe by default. ID/web is never implied.
            "id_verify_enabled": False,
            "web_verify_enabled": False,
            "id_web_verify_enabled": False,
            "verification_requires_id": False,
            "verification_panel_style": "voice_check" if voice_on else "basic_verify" if basic_on else "none",
            "verification_mode": "voice_check" if voice_on else "basic_button" if basic_on else "none",
            "verify_mode": "voice_check" if voice_on else "basic_button" if basic_on else "none",
            "setup_choice": "custom_setup",
            "setup_choice_label": _custom_mix_label(clean),
            "setup_choice_description": "Custom feature choices.",
            "setup_choice_member_sees": _custom_mix_label(clean),
        }
    )
    return clean


def _service_hint_text(state: Any) -> str:
    enabled: list[str] = []
    if state.tickets:
        enabled.append("Tickets")
    if state.verification:
        enabled.append("Simple Verify")
    if state.voice:
        enabled.append("Voice Verify")
    if state.spamguard or state.moderation:
        enabled.append("SpamGuard / Logs")
    return "Choose at least one feature first." if not enabled else "Setup will check: " + ", ".join(enabled) + "."


async def _save_custom_services(guild_id: int, payload: dict[str, bool], actor: Any) -> None:
    from stoney_verify.startup_guards import setup_service_modes as modes
    await modes._save_service_state(guild_id, _custom_service_config_patch(dict(payload)), actor)


async def _load_custom_state(guild_id: int) -> Any:
    from stoney_verify.startup_guards import setup_service_modes as modes
    return await modes.load_service_state(guild_id)



_CUSTOM_SERVICE_FLAG_KEYS = (
    "tickets_enabled",
    "verification_enabled",
    "voice_verification_enabled",
    "spam_guard_enabled",
    "moderation_enabled",
)


def _auto_cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass

    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass

    try:
        for bucket in ("settings", "config", "metadata", "meta"):
            nested = cfg.get(bucket) if hasattr(cfg, "get") else getattr(cfg, bucket, None)
            if isinstance(nested, dict) and nested.get(key) is not None:
                return nested.get(key)
    except Exception:
        pass

    return default


def _auto_truthy(value: Any, default: bool = False) -> bool:
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
    except Exception:
        pass
    return bool(default)


def _auto_int(value: Any) -> int:
    try:
        return int(str(value or "0").strip() or 0)
    except Exception:
        return 0


def _cfg_has_any_id(cfg: Any, *keys: str) -> bool:
    for key in keys:
        if _auto_int(_auto_cfg_value(cfg, key, 0)) > 0:
            return True
    return False


def _name_has_any(value: Any, markers: tuple[str, ...]) -> bool:
    try:
        text = str(getattr(value, "name", value) or "").lower()
        return any(marker in text for marker in markers)
    except Exception:
        return False


def _guild_has_category(guild: discord.Guild, markers: tuple[str, ...]) -> bool:
    try:
        return any(_name_has_any(category, markers) for category in getattr(guild, "categories", []) or [])
    except Exception:
        return False


def _guild_has_text_channel(guild: discord.Guild, markers: tuple[str, ...]) -> bool:
    try:
        return any(_name_has_any(channel, markers) for channel in getattr(guild, "text_channels", []) or [])
    except Exception:
        return False


def _guild_has_voice_channel(guild: discord.Guild, markers: tuple[str, ...]) -> bool:
    try:
        return any(_name_has_any(channel, markers) for channel in getattr(guild, "voice_channels", []) or [])
    except Exception:
        return False


def _guild_has_role(guild: discord.Guild, markers: tuple[str, ...]) -> bool:
    try:
        return any(_name_has_any(role, markers) for role in getattr(guild, "roles", []) or [])
    except Exception:
        return False


async def _detect_existing_service_payload(guild: discord.Guild) -> tuple[dict[str, bool], list[str]]:
    """Detect already-installed server pieces. This never creates anything."""

    cfg = None
    try:
        cfg = await solid.get_guild_config(guild.id, refresh=True)  # type: ignore[attr-defined]
    except Exception:
        cfg = None

    tickets = bool(
        _cfg_has_any_id(
            cfg,
            "ticket_category_id",
            "ticket_archive_category_id",
            "ticket_closed_category_id",
            "ticket_panel_channel_id",
            "support_channel_id",
            "staff_role_id",
            "transcripts_channel_id",
        )
        or _guild_has_category(guild, ("ticket", "archive", "support"))
        or _guild_has_text_channel(guild, ("ticket", "support", "transcript"))
    )

    basic_verify = bool(
        _auto_truthy(_auto_cfg_value(cfg, "basic_verify_enabled", False), False)
        or _auto_truthy(_auto_cfg_value(cfg, "basic_button_verify_enabled", False), False)
        or _cfg_has_any_id(
            cfg,
            "verify_channel_id",
            "verification_channel_id",
            "unverified_role_id",
            "verified_role_id",
            "resident_role_id",
        )
        or _guild_has_text_channel(guild, ("verify", "verification"))
        or _guild_has_role(guild, ("unverified", "verified", "resident", "member"))
    )

    voice = bool(
        _auto_truthy(_auto_cfg_value(cfg, "voice_verification_enabled", False), False)
        or _auto_truthy(_auto_cfg_value(cfg, "verification_allows_voice", False), False)
        or _cfg_has_any_id(
            cfg,
            "vc_verify_channel_id",
            "vc_verify_queue_channel_id",
            "voice_verify_channel_id",
            "voice_verification_channel_id",
        )
        or _guild_has_text_channel(guild, ("vc-verify", "voice-verify", "verify-queue"))
        or _guild_has_voice_channel(guild, ("verify", "verification", "waiting"))
    )

    spamguard = bool(
        _auto_truthy(_auto_cfg_value(cfg, "spam_guard_enabled", False), False)
        or _auto_truthy(_auto_cfg_value(cfg, "automod_enabled", False), False)
        or _auto_truthy(_auto_cfg_value(cfg, "automod_block_invites", False), False)
        or _auto_truthy(_auto_cfg_value(cfg, "invite_shield_enabled", False), False)
    )

    moderation = bool(
        _auto_truthy(_auto_cfg_value(cfg, "moderation_enabled", False), False)
        or spamguard
        or _cfg_has_any_id(
            cfg,
            "modlog_channel_id",
            "raidlog_channel_id",
            "raid_log_channel_id",
            "join_log_channel_id",
            "join_exit_log_channel_id",
            "status_channel_id",
            "bot_status_channel_id",
            "health_channel_id",
        )
        or _guild_has_text_channel(guild, ("modlog", "mod-log", "logs", "join-leave", "bot-status", "status"))
    )

    if voice:
        tickets = True
        basic_verify = True
        moderation = True

    detected = {
        "tickets_enabled": tickets,
        "verification_enabled": basic_verify,
        "voice_verification_enabled": voice,
        "spam_guard_enabled": spamguard,
        "moderation_enabled": moderation,
    }

    labels = []
    if tickets:
        labels.append("Tickets")
    if basic_verify:
        labels.append("Simple Verify")
    if voice:
        labels.append("Voice Verify")
    if spamguard:
        labels.append("SpamGuard")
    if moderation:
        labels.append("Logs")

    return detected, labels


async def _autofill_custom_state_from_existing(guild: discord.Guild, state: Any) -> tuple[Any, str]:
    """If Custom Setup is blank, pre-check services that already exist.

    This only saves setup-focus flags. It does not create/delete channels, roles,
    tickets, panels, or permissions.
    """

    try:
        current = state.as_payload()
    except Exception:
        current = {}

    if any(bool(current.get(key, False)) for key in _CUSTOM_SERVICE_FLAG_KEYS):
        return state, ""

    detected, labels = await _detect_existing_service_payload(guild)
    if not any(detected.values()):
        return state, ""

    await _save_custom_services(guild.id, detected, guild.me or guild.owner)
    next_state = await _load_custom_state(guild.id)

    label_text = ", ".join(labels) if labels else "existing setup"
    return (
        next_state,
        f"Found existing setup and turned on matching features: **{label_text}**. Nothing was created.",
    )


def _custom_services_embed(guild: discord.Guild, state: Any, *, saved_message: str = "") -> discord.Embed:
    payload = {key: bool(state.as_payload().get(key, False)) for key in _CUSTOM_SERVICE_FLAG_KEYS}
    preset_key = _custom_preset_key_for_payload(payload)
    preset_label = CUSTOM_PRESETS.get(preset_key, ("Your choices", {}, "", "🧩"))[0] if preset_key else "Your choices"

    embed = discord.Embed(
        title="🧩 Choose Your Features",
        description=(
            "Choose what you want Dank Shield to do in this server. "
            "A green button means the feature is ON. A gray button means it is OFF."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    if saved_message:
        embed.add_field(name="Saved", value=saved_message[:1024], inline=False)

    embed.add_field(name="Your Setup", value=f"**{preset_label}**\n{_custom_mix_label(payload)}", inline=False)
    embed.add_field(name="Features", value=_service_summary_text(state), inline=False)
    embed.add_field(
        name="Next",
        value=(
            "Turn the features on or off, then press **Continue Setup**. "
            "Dank Shield will walk you through the rest one step at a time."
        ),
        inline=False,
    )
    embed.set_footer(text=f"Guild {guild.id} • choose your features")
    return embed

class CustomServicePresetSelect(discord.ui.Select):
    def __init__(self, current: Any) -> None:
        options = []
        current_payload = {key: bool(current.as_payload().get(key, False)) for key in _CUSTOM_SERVICE_FLAG_KEYS}
        preset_key = _custom_preset_key_for_payload(current_payload)

        if not preset_key:
            options.append(
                discord.SelectOption(
                    label=_custom_mix_label(current_payload)[:100],
                    value="__custom_current__",
                    description="Your current feature choices.",
                    emoji="🧩",
                    default=True,
                )
            )

        for key, (label, flags, desc, emoji) in CUSTOM_PRESETS.items():
            preset_clean = {flag_key: bool(flags.get(flag_key, False)) for flag_key in _CUSTOM_SERVICE_FLAG_KEYS}
            options.append(
                discord.SelectOption(
                    label=label,
                    value=key,
                    description=desc[:100],
                    emoji=emoji,
                    default=(key == preset_key),
                )
            )

        super().__init__(
            placeholder=_custom_mix_label(current_payload)[:150],
            min_values=1,
            max_values=1,
            options=options[:25],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

        key = str(self.values[0])
        if key == "__custom_current__":
            await interaction.response.defer(ephemeral=True)
            state = await _load_custom_state(guild.id)
            return await interaction.edit_original_response(
                embed=_custom_services_embed(guild, state, saved_message="Still using your current feature choices."),
                view=CustomServiceModeView(state),
            )

        preset = CUSTOM_PRESETS.get(key)
        if preset is None:
            return await interaction.response.send_message("❌ That feature choice is no longer available. Choose another option.", ephemeral=True)
        label, flags, desc, _emoji = preset
        await interaction.response.defer(ephemeral=True)
        await _save_custom_services(guild.id, dict(flags), interaction.user)
        state = await _load_custom_state(guild.id)
        await interaction.edit_original_response(
            embed=_custom_services_embed(guild, state, saved_message=f"Saved **{label}**. {desc}"),
            view=CustomServiceModeView(state),
        )



class CustomServiceToggleButton(discord.ui.Button):
    def __init__(self, key: str, label: str, selected: bool, emoji: str, row: int) -> None:
        state_text = "ON ✅" if selected else "OFF ⬜"
        super().__init__(
            label=f"{label}: {state_text}",
            emoji=emoji,
            style=discord.ButtonStyle.success if selected else discord.ButtonStyle.secondary,
            custom_id=f"dank_setup_custom_toggle:{key}",
            row=row,
        )
        self.key = key
        self.short_label = label

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

        state = await _load_custom_state(guild.id)
        payload = state.as_payload()
        next_value = not bool(payload.get(self.key, False))
        payload[self.key] = next_value

        if self.key == "voice_verification_enabled" and payload[self.key]:
            payload["verification_enabled"] = True
            payload["tickets_enabled"] = True
            payload["moderation_enabled"] = True

        if self.key == "verification_enabled" and not payload[self.key]:
            payload["voice_verification_enabled"] = False

        if self.key == "spam_guard_enabled" and payload[self.key]:
            payload["moderation_enabled"] = True

        await interaction.response.defer(ephemeral=True)
        await _save_custom_services(guild.id, payload, interaction.user)
        next_state = await _load_custom_state(guild.id)

        await interaction.edit_original_response(
            embed=_custom_services_embed(
                guild,
                next_state,
                saved_message=f"Set **{self.short_label}** to **{'ON' if next_value else 'OFF'}**.",
            ),
            view=CustomServiceModeView(next_state),
        )



class CustomServiceModeView(discord.ui.View):
    """Custom Setup only: choose services here, then return to one guided path."""

    def __init__(self, state: Any) -> None:
        super().__init__(timeout=900)
        self.add_item(CustomServicePresetSelect(state))
        self.add_item(CustomServiceToggleButton("tickets_enabled", "Tickets", state.tickets, "🎫", 2))
        self.add_item(CustomServiceToggleButton("verification_enabled", "Simple Verify", state.verification, "✅", 2))
        self.add_item(CustomServiceToggleButton("voice_verification_enabled", "Voice Verify", state.voice, "🎙️", 2))
        self.add_item(CustomServiceToggleButton("spam_guard_enabled", "SpamGuard", state.spamguard, "🛡️", 3))
        self.add_item(CustomServiceToggleButton("moderation_enabled", "Logs", state.moderation, "🧾", 3))

    @discord.ui.button(
        label="Continue Setup",
        emoji="➡️",
        style=discord.ButtonStyle.success,
        custom_id="dank_setup_custom:continue_guided",
        row=1,
    )
    async def continue_guided(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await recommend._open_guided_setup(interaction)

    @discord.ui.button(
        label="Back",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_custom:back",
        row=4,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await recommend._open_choose_setup_type(interaction)

async def _open_custom_service_picker(interaction: discord.Interaction, *, saved_message: str = "") -> None:
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    state = await _load_custom_state(guild.id)
    state, detected_message = await _autofill_custom_state_from_existing(guild, state)
    message = saved_message or detected_message or "Saved **Choose My Own Features**. Dank Shield checks what is already set up and pre-selects matching features. Turn off anything you do not want."
    await solid._edit_or_followup(interaction, embed=_custom_services_embed(guild, state, saved_message=message), view=CustomServiceModeView(state))


async def _open_plain_health(
    interaction: discord.Interaction,
) -> None:
    """Use the one canonical feature-aware Setup Check."""

    await recommend._open_health_check(interaction)




class SetupTypeChoiceSelect(discord.ui.Select):
    def __init__(self, *, guild: Optional[discord.Guild] = None) -> None:
        choices = _choices_for_guild(guild)
        super().__init__(
            placeholder="What do you want Dank Shield to do?",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=choice.label,
                    value=choice.key,
                    description=choice.short[:100],
                    emoji=choice.emoji,
                )
                for choice in choices
            ][:25],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, SetupTypeChoiceView):
            return
        choice = CHOICES_BY_KEY.get(str(self.values[0]))
        if choice is None:
            return await interaction.response.send_message("❌ Unknown setup type.", ephemeral=True)
        await view._save_and_show(interaction, choice)


class SetupTypeChoiceView(solid.BackToSetupView):
    def __init__(self, *, guild: Optional[discord.Guild] = None) -> None:
        super().__init__()
        self.add_item(SetupTypeChoiceSelect(guild=guild))

    async def _save_and_show(
        self,
        interaction: discord.Interaction,
        choice: PlainSetupChoice,
    ) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        if choice.needs_id and not id_verify_allowed_for_guild(guild):
            return await interaction.response.send_message(
                "🔒 ID/Web Verify is not available for this server. Use **Simple Verify** instead.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        await solid._safe_defer_update(interaction)
        await _save_choice(interaction, choice)
        if choice.key == "custom_setup":
            return await _open_custom_service_picker(
                interaction,
                saved_message=(
                    "Saved **Choose My Own Features**. Choose which features this server should use, "
                    "then press **Continue Setup**."
                ),
            )
        await recommend._open_guided_setup(
            interaction,
            saved_message=f"Saved **{choice.label}**.",
        )

def register_public_setup_fresh_choice_commands(
    bot: Any,
    tree: Any,
) -> None:
    """Register choice helpers without replacing setup home."""

    _ = bot, tree
    print(
        "✅ public_setup_fresh_choice: "
        "guided setup choices ready"
    )


__all__ = ["register_public_setup_fresh_choice_commands", "get_plain_setup_choice", "PlainSetupChoice", "SETUP_CHOICES", "CHOICES_BY_KEY"]
