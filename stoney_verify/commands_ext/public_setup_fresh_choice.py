from __future__ import annotations

"""Plain setup choice owner for /dank setup."""

from dataclasses import dataclass
from typing import Any, Optional

import discord

from ..globals import now_utc
from . import public_setup_recommend as recommend
from . import public_setup_recovery as recovery
from . import public_setup_solid as solid
from ..setup_engine.verification_modes import id_verify_allowed_for_guild

_PATCHED = False


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
    PlainSetupChoice("basic_server", "Basic server", "🏠", "Simple server setup with support tickets, starter logs, and normal public-server defaults.", "A clean support button when they need staff help.", True, False, False, "basic"),
    PlainSetupChoice("basic_verify", "Basic verify", "✅", "Simple Verify button flow: no ID upload, no website token, no voice check, no forced ticket.", "A Verify button that grants the configured access role and removes the waiting role.", False, False, False, "basic_verify"),
    PlainSetupChoice("help_desk", "Help desk", "🎫", "Support-ticket focused setup for help requests, reports, appeals, and staff triage.", "A clean ticket panel with fast support choices.", True, False, False, "help_desk"),
    PlainSetupChoice("voice_check", "Voice check", "🎙️", "Members request staff voice verification without ID upload or website upload flow.", "A verification ticket with a Verify in VC option.", True, False, True, "voice_check"),
    PlainSetupChoice("id_check", "ID check", "🪪", "Private ID upload verification for allowlisted servers only.", "A verification ticket with an Upload ID button.", True, True, False, "id_check"),
    PlainSetupChoice("id_voice_check", "ID + voice check", "🔐", "Private ID upload plus voice-check workflow for allowlisted servers only.", "Upload ID, Verify in VC, reveal link, regenerate link if enabled, and website button if configured.", True, True, True, "id_voice_check"),
    PlainSetupChoice("custom_setup", "Custom setup", "⚙️", "Choose every service yourself: tickets, Basic Verify, voice verify, SpamGuard, and logs.", "Whatever services you turn on in the next screen.", False, False, False, "custom"),
)

CHOICES_BY_KEY: dict[str, PlainSetupChoice] = {choice.key: choice for choice in SETUP_CHOICES}

CUSTOM_PRESETS: dict[str, tuple[str, dict[str, bool], str, str]] = {
    "tickets": ("Tickets only", {"tickets_enabled": True, "verification_enabled": False, "voice_verification_enabled": False, "spam_guard_enabled": False, "moderation_enabled": False}, "Ticket panel and ticket lifecycle only.", "🎫"),
    "basic_verify": ("Basic Verify only", {"tickets_enabled": False, "verification_enabled": True, "voice_verification_enabled": False, "spam_guard_enabled": False, "moderation_enabled": False}, "One-button verify gate. No ID, no VC, no ticket required.", "✅"),
    "voice_verify": ("Basic + Voice Verify", {"tickets_enabled": True, "verification_enabled": True, "voice_verification_enabled": True, "spam_guard_enabled": False, "moderation_enabled": True}, "Basic Verify plus staff voice-check support.", "🎙️"),
    "spamguard": ("SpamGuard only", {"tickets_enabled": False, "verification_enabled": False, "voice_verification_enabled": False, "spam_guard_enabled": True, "moderation_enabled": True}, "Spam protection and logs without ticket or verify blockers.", "🛡️"),
    "all": ("Everything", {"tickets_enabled": True, "verification_enabled": True, "voice_verification_enabled": True, "spam_guard_enabled": True, "moderation_enabled": True}, "Tickets, Basic Verify, Voice Verify, SpamGuard, and logs.", "🚀"),
}


def get_plain_setup_choice(key: Any) -> Optional[PlainSetupChoice]:
    return CHOICES_BY_KEY.get(str(key or "").strip().lower())


def _choices_for_guild(guild: Optional[discord.Guild]) -> tuple[PlainSetupChoice, ...]:
    return SETUP_CHOICES if id_verify_allowed_for_guild(guild) else tuple(choice for choice in SETUP_CHOICES if not choice.needs_id)


def _choice_lines(guild: Optional[discord.Guild] = None) -> str:
    lines = "\n".join(f"{c.emoji} **{c.label}** — {c.short}" for c in _choices_for_guild(guild))
    if not id_verify_allowed_for_guild(guild):
        lines += "\n\n🔒 ID/web verification choices are hidden for this server. Use **Basic verify** for a simple one-button verification gate."
    return lines


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


def _bool_icon(value: bool) -> str:
    return "✅" if value else "—"


async def _setup_progress_for_home(guild: discord.Guild) -> tuple[str, int, int, str]:
    try:
        return await recommend._setup_progress(guild)  # type: ignore[attr-defined]
    except Exception:
        return "Run **Setup Check** to see what is ready.", 0, 1, "Choose Setup Type"


async def _service_summary_for_home(guild: discord.Guild) -> tuple[str, str]:
    try:
        cfg = await solid.get_guild_config(guild.id, refresh=True)  # type: ignore[attr-defined]
    except Exception:
        cfg = None
    return (f"**Chosen:** {_plain_saved_choice_from_cfg(cfg)}\nTickets: fast when enabled\nBasic verify: available for every server\nForms: off unless you turn them on", "Pick **Choose Setup Type** first if this is a new server.")


def _service_flags_for_choice(choice: PlainSetupChoice) -> dict[str, bool]:
    if choice.key == "basic_server":
        return {"tickets_enabled": True, "verification_enabled": False, "voice_verification_enabled": False, "spam_guard_enabled": False, "moderation_enabled": True}
    if choice.key == "basic_verify":
        return {"tickets_enabled": False, "verification_enabled": True, "voice_verification_enabled": False, "spam_guard_enabled": False, "moderation_enabled": False}
    if choice.key == "help_desk":
        return {"tickets_enabled": True, "verification_enabled": False, "voice_verification_enabled": False, "spam_guard_enabled": False, "moderation_enabled": True}
    if choice.key == "voice_check":
        return {"tickets_enabled": True, "verification_enabled": True, "voice_verification_enabled": True, "spam_guard_enabled": False, "moderation_enabled": True}
    if choice.key in {"id_check", "id_voice_check"}:
        return {"tickets_enabled": True, "verification_enabled": True, "voice_verification_enabled": bool(choice.needs_voice), "spam_guard_enabled": False, "moderation_enabled": True}
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


def _service_summary_text(state: Any) -> str:
    return (
        f"{'✅' if state.tickets else '⬜'} Tickets\n"
        f"{'✅' if state.verification else '⬜'} Basic Verify\n"
        f"{'✅' if state.voice else '⬜'} Voice verification\n"
        f"{'✅' if state.spamguard else '⬜'} SpamGuard service\n"
        f"{'✅' if state.moderation else '⬜'} Logs/Moderation"
    )


def _service_hint_text(state: Any) -> str:
    enabled: list[str] = []
    if state.tickets:
        enabled.append("Ticket Basics")
    if state.verification:
        enabled.append("Basic Verify")
    if state.voice:
        enabled.append("Voice Verification")
    if state.spamguard or state.moderation:
        enabled.append("SpamGuard setup / Logs")
    return "Choose at least one service first." if not enabled else "Health Check will focus on: " + ", ".join(enabled) + "."


async def _save_custom_services(guild_id: int, payload: dict[str, bool], actor: Any) -> None:
    from stoney_verify.startup_guards import setup_service_modes as modes
    await modes._save_service_state(guild_id, payload, actor)


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
        labels.append("Basic Verify")
    if voice:
        labels.append("Voice Verify")
    if spamguard:
        labels.append("SpamGuard")
    if moderation:
        labels.append("Logs/Moderation")

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
        f"Detected existing server setup and pre-selected: **{label_text}**. Nothing was created.",
    )


def _custom_services_embed(guild: discord.Guild, state: Any, *, saved_message: str = "") -> discord.Embed:
    embed = discord.Embed(
        title="🧭 Choose Dank Shield Services",
        description="Pick exactly what this server uses. **Basic Verify** is the simple one-button verify gate. Voice Verify is separate. SpamGuard still has its own actual guard switch.",
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    if saved_message:
        embed.add_field(name="Saved", value=saved_message[:1024], inline=False)
    embed.add_field(name="Selected Services", value=_service_summary_text(state), inline=False)
    embed.add_field(name="Health Check Focus", value=_service_hint_text(state), inline=False)
    embed.add_field(name="Presets", value="\n".join(f"{emoji} **{label}** — {desc}" for label, _flags, desc, emoji in CUSTOM_PRESETS.values())[:1024], inline=False)
    embed.add_field(name="Next", value="After picking services, press **Use My Existing Server** to map roles/channels or **Create Missing Items** to create safe defaults.", inline=False)
    embed.set_footer(text=f"Guild {guild.id} • custom setup services")
    return embed


class CustomServicePresetSelect(discord.ui.Select):
    def __init__(self, current: Any) -> None:
        options = []
        current_payload = current.as_payload()
        for key, (label, flags, desc, emoji) in CUSTOM_PRESETS.items():
            options.append(discord.SelectOption(label=label, value=key, description=desc[:100], emoji=emoji, default=flags == current_payload))
        super().__init__(placeholder="Choose a preset or use the toggles below", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        key = str(self.values[0])
        preset = CUSTOM_PRESETS.get(key)
        if preset is None:
            return await interaction.response.send_message("❌ Unknown preset.", ephemeral=True)
        label, flags, desc, _emoji = preset
        await interaction.response.defer(ephemeral=True)
        await _save_custom_services(guild.id, dict(flags), interaction.user)
        state = await _load_custom_state(guild.id)
        await interaction.edit_original_response(embed=_custom_services_embed(guild, state, saved_message=f"Saved **{label}**. {desc}"), view=CustomServiceModeView(state))


class CustomServiceToggleButton(discord.ui.Button):
    def __init__(self, key: str, label: str, selected: bool, emoji: str, row: int) -> None:
        action = "Turn OFF" if selected else "Turn ON"
        super().__init__(label=f"{action}: {label}", emoji=emoji, style=discord.ButtonStyle.success if selected else discord.ButtonStyle.secondary, row=row)
        self.key = key
        self.short_label = label

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        state = await _load_custom_state(guild.id)
        payload = state.as_payload()
        payload[self.key] = not bool(payload.get(self.key, False))
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
        await interaction.edit_original_response(embed=_custom_services_embed(guild, next_state, saved_message=f"Updated **{self.short_label}**."), view=CustomServiceModeView(next_state))


class CustomServiceModeView(discord.ui.View):
    def __init__(self, state: Any) -> None:
        super().__init__(timeout=900)
        self.add_item(CustomServicePresetSelect(state))
        self.add_item(CustomServiceToggleButton("tickets_enabled", "Tickets", state.tickets, "🎫", 1))
        self.add_item(CustomServiceToggleButton("verification_enabled", "Basic Verify", state.verification, "✅", 1))
        self.add_item(CustomServiceToggleButton("voice_verification_enabled", "Voice Verify", state.voice, "🎙️", 1))
        self.add_item(CustomServiceToggleButton("spam_guard_enabled", "SpamGuard service", state.spamguard, "🛡️", 2))
        self.add_item(CustomServiceToggleButton("moderation_enabled", "Logs/Moderation", state.moderation, "🧾", 2))

    @discord.ui.button(label="Use My Existing Server", emoji="🧩", style=discord.ButtonStyle.primary, custom_id="dank_setup_custom_existing", row=3)
    async def existing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_existing_server_setup(interaction)

    @discord.ui.button(label="Review / Create Missing Items", emoji="✨", style=discord.ButtonStyle.success, custom_id="dank_setup_custom_create", row=3)
    async def create_missing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_create_missing_items(interaction)

    @discord.ui.button(label="Setup Check", emoji="🩺", style=discord.ButtonStyle.secondary, custom_id="dank_setup_custom_health", row=4)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_plain_health(interaction)

    @discord.ui.button(label="Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_custom_home", row=4)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await solid._safe_defer_update(interaction)
        embed, view = await _plain_choice_main_payload(guild)
        await solid._edit_or_followup(interaction, embed=embed, view=view)


async def _open_custom_service_picker(interaction: discord.Interaction, *, saved_message: str = "") -> None:
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    state = await _load_custom_state(guild.id)
    state, detected_message = await _autofill_custom_state_from_existing(guild, state)
    message = saved_message or detected_message or "Saved **Custom setup**. Existing server items are detected automatically. Turn on/off only what this server should actually use."
    await solid._edit_or_followup(interaction, embed=_custom_services_embed(guild, state, saved_message=message), view=CustomServiceModeView(state))


def _choice_preview_embed(guild: discord.Guild, choice: PlainSetupChoice) -> discord.Embed:
    basic_verify = choice.key == "basic_verify"
    custom = choice.key == "custom_setup"
    embed = discord.Embed(title=f"{choice.emoji} {choice.label}", description=choice.short, color=discord.Color.green() if basic_verify else discord.Color.blurple(), timestamp=now_utc())
    embed.add_field(name="What members will see", value=choice.member_sees[:1024], inline=False)
    embed.add_field(name="What this turns on", value=("⬜ Tickets\n⬜ Basic Verify button\n⬜ Voice check\n⬜ SpamGuard\n⬜ Logs/Moderation\nNext screen lets you turn each one on/off." if custom else f"{_bool_icon(choice.needs_tickets)} Tickets\n{_bool_icon(basic_verify)} Basic Verify button\n{_bool_icon(choice.needs_id)} ID upload link\n{_bool_icon(choice.needs_voice)} Voice check\n✅ Fast ticket opening when tickets are enabled\n✅ Forms off by default"), inline=False)
    if basic_verify:
        embed.add_field(name="Important", value="Users press **Verify**, Dank Shield grants the configured Verified/full-access role, and removes the waiting role. No ID upload, website token, VC check, or forced ticket.", inline=False)
    elif custom:
        embed.add_field(name="Important", value="Custom setup opens the service picker so you can turn on exactly what this server uses.", inline=False)
    elif choice.needs_id:
        embed.add_field(name="Allowlisted ID/Web Verification", value="This is restricted to allowlisted guild IDs so public servers do not accidentally inherit the old ID upload flow.", inline=False)
    embed.add_field(name="Next", value=("Custom setup opens the service picker next. Turn on exactly what you want, then map roles/channels." if custom else "Press **Use My Existing Server** if your roles/channels already exist.\nPress **Create Missing Items** if you want Dank Shield to create missing basics.\nPress **Setup Check** when you are done."), inline=False)
    embed.set_footer(text=f"Guild {guild.id} • choice saved per server")
    return embed


async def _edit_setup_message(interaction: discord.Interaction, *, embed: discord.Embed, view: discord.ui.View) -> None:
    if interaction.response.is_done():
        await solid._edit_or_followup(interaction, embed=embed, view=view)
    else:
        await interaction.response.edit_message(embed=embed, view=view)


async def _open_existing_server_setup(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    embed = discord.Embed(title="🧩 Use My Existing Server", description="Pick the roles/channels/folders your server already uses. Names do not matter. Dank Shield saves Discord IDs per server.", color=discord.Color.blurple(), timestamp=now_utc())
    embed.add_field(name="Sections", value="🎫 **Ticket Basics** — ticket folders, staff role, transcripts\n🎭 **Access Roles** — waiting role, approved role, member role\n🎙️ **Verification Channels** — Basic Verify or voice check channels\n🧾 **Logs + Status** — modlog, join log, status channel\n⚙️ **Behavior Settings** — ticket prefix, kick timer, verification style", inline=False)
    await _edit_setup_message(interaction, embed=embed, view=solid.ChooseExistingView())


async def _open_create_missing_items(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    embed = discord.Embed(title="✨ Create Missing Items", description="Dank Shield can create missing starter roles/channels/folders. It does **not** delete your server setup.", color=discord.Color.green(), timestamp=now_utc())
    embed.add_field(name="Before it creates anything", value="Review this screen. Press **Create Basic Missing Items** only if you want the starter layout.", inline=False)
    await _edit_setup_message(interaction, embed=embed, view=CreateMissingItemsView())


async def _open_ticket_menu_options(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    await solid._safe_defer_update(interaction)
    try:
        embed, view = await recommend._better_category_manager_payload(guild, title="🧾 Ticket Menu Options")  # type: ignore[attr-defined]
    except Exception:
        embed, view = await solid._build_category_manager_payload(guild)  # type: ignore[attr-defined]
    await solid._edit_or_followup(interaction, embed=embed, view=view)


async def _open_plain_health(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    await solid._safe_defer_update(interaction)
    try:
        embed = await recommend._build_plain_setup_health_embed(guild)  # type: ignore[attr-defined]
        view: Optional[discord.ui.View] = getattr(recommend, "SetupHealthHelpView", solid.BackToSetupView)()
    except Exception:
        embed = await solid._build_health_embed(guild)
        view = solid.BackToSetupView()
    await solid._edit_or_followup(interaction, embed=embed, view=view)


def _build_setup_help_embed() -> discord.Embed:
    embed = discord.Embed(title="❓ Dank Shield Setup Help", description="Simple answers for the setup screen.", color=discord.Color.blurple(), timestamp=now_utc())
    embed.add_field(name="What should I press first?", value="Press **Choose Setup Type**. Pick the closest match. You can change it later.", inline=False)
    embed.add_field(name="What is Basic verify?", value="A public-safe Verify button. Members click it, get the configured Verified/full-access role, and lose the waiting role. No ID upload, website token, voice check, or forced ticket.", inline=False)
    embed.add_field(name="What if I choose Custom setup?", value="Custom setup opens a service picker with **Basic Verify** as its own toggle. Turn on only what you want.", inline=False)
    embed.add_field(name="What if my server already has roles/channels?", value="Press **Use My Existing Server** and pick what you already use from Discord menus.", inline=False)
    return embed


async def _plain_choice_main_payload(guild: discord.Guild) -> tuple[discord.Embed, discord.ui.View]:
    progress_text, done, total, next_step = await _setup_progress_for_home(guild)
    service_summary, service_hint = await _service_summary_for_home(guild)
    embed = discord.Embed(title="🚀 Dank Shield Setup", description="Pick what this server actually needs. Start with **Choose Setup Type**. Then map roles/channels or create missing basics.", color=discord.Color.blurple(), timestamp=now_utc())
    embed.add_field(name="Setup Choices", value=_choice_lines(guild)[:1024], inline=False)
    embed.add_field(name="Current Choice", value=service_summary[:1024], inline=False)
    embed.add_field(name="Health Check Focus", value=service_hint[:1024], inline=False)
    embed.add_field(name=f"Setup Progress: {done}/{total} complete", value=progress_text or "No setup checks ran.", inline=False)
    embed.add_field(name="Recommended Next Step", value=str(next_step or "Choose Setup Type")[:1024], inline=False)
    embed.add_field(name="Product Rule", value="Basic verify is public-safe. Tickets open fast. Forms are optional only. Setup stays per-server.", inline=False)
    embed.set_footer(text=f"Guild {guild.id} • /dank setup")
    return embed, PlainSetupHomeView()


class PlainSetupHomeView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Choose Setup Type", emoji="🧭", style=discord.ButtonStyle.primary, custom_id="dank_setup_plain:choose", row=0)
    async def choose(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        embed = discord.Embed(title="🧭 Choose Setup Type", description="Pick the closest match. This only saves the style this server wants. You can change it later.", color=discord.Color.blurple(), timestamp=now_utc())
        for choice in _choices_for_guild(interaction.guild):
            embed.add_field(name=f"{choice.emoji} {choice.label}", value=f"{choice.short}\nMembers see: {choice.member_sees}", inline=False)
        if not id_verify_allowed_for_guild(interaction.guild):
            embed.add_field(name="🔒 ID/web verification hidden", value="Use **Basic verify** for simple one-button verification. ID/web upload verification is only available for allowlisted guild IDs.", inline=False)
        await interaction.response.edit_message(embed=embed, view=PlainSetupChoiceView(guild=interaction.guild))

    @discord.ui.button(label="Use My Existing Server", emoji="🧩", style=discord.ButtonStyle.primary, custom_id="dank_setup_plain:existing", row=0)
    async def existing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_existing_server_setup(interaction)

    @discord.ui.button(label="Create Missing Items", emoji="✨", style=discord.ButtonStyle.success, custom_id="dank_setup_plain:create_missing", row=0)
    async def create_missing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_create_missing_items(interaction)

    @discord.ui.button(label="Ticket Menu Options", emoji="🧾", style=discord.ButtonStyle.secondary, custom_id="dank_setup_plain:ticket_menu", row=1)
    async def ticket_menu(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_ticket_menu_options(interaction)

    @discord.ui.button(label="Setup Check", emoji="🩺", style=discord.ButtonStyle.secondary, custom_id="dank_setup_plain:health", row=1)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_plain_health(interaction)

    @discord.ui.button(label="Help / FAQ", emoji="❓", style=discord.ButtonStyle.secondary, custom_id="dank_setup_plain:help", row=1)
    async def help_faq(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        await interaction.response.edit_message(embed=_build_setup_help_embed(), view=solid.BackToSetupView())


class PlainSetupChoiceView(solid.BackToSetupView):
    def __init__(self, *, guild: Optional[discord.Guild] = None) -> None:
        super().__init__()
        if not id_verify_allowed_for_guild(guild):
            for child in list(getattr(self, "children", []) or []):
                if str(getattr(child, "custom_id", "") or "") in {"dank_setup_choice:id", "dank_setup_choice:id_voice"}:
                    try:
                        self.remove_item(child)
                    except Exception:
                        pass

    async def _save_and_show(self, interaction: discord.Interaction, choice: PlainSetupChoice) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        if choice.needs_id and not id_verify_allowed_for_guild(guild):
            return await interaction.response.send_message("🔒 ID/web verification is only available for allowlisted guild IDs. Use **Basic verify** instead.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        await solid._safe_defer_update(interaction)
        await _save_choice(interaction, choice)
        if choice.key == "custom_setup":
            return await _open_custom_service_picker(interaction)
        await solid._edit_or_followup(interaction, embed=_choice_preview_embed(guild, choice), view=AfterChoiceView())

    @discord.ui.button(label="Basic server", emoji="🏠", style=discord.ButtonStyle.primary, custom_id="dank_setup_choice:basic", row=0)
    async def basic(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._save_and_show(interaction, CHOICES_BY_KEY["basic_server"])

    @discord.ui.button(label="Basic verify", emoji="✅", style=discord.ButtonStyle.success, custom_id="dank_setup_choice:basic_verify", row=0)
    async def basic_verify(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._save_and_show(interaction, CHOICES_BY_KEY["basic_verify"])

    @discord.ui.button(label="Help desk", emoji="🎫", style=discord.ButtonStyle.primary, custom_id="dank_setup_choice:helpdesk", row=1)
    async def helpdesk(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._save_and_show(interaction, CHOICES_BY_KEY["help_desk"])

    @discord.ui.button(label="Voice check", emoji="🎙️", style=discord.ButtonStyle.primary, custom_id="dank_setup_choice:voice", row=1)
    async def voice_check(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._save_and_show(interaction, CHOICES_BY_KEY["voice_check"])

    @discord.ui.button(label="ID check", emoji="🪪", style=discord.ButtonStyle.primary, custom_id="dank_setup_choice:id", row=2)
    async def id_check(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._save_and_show(interaction, CHOICES_BY_KEY["id_check"])

    @discord.ui.button(label="ID + voice check", emoji="🔐", style=discord.ButtonStyle.success, custom_id="dank_setup_choice:id_voice", row=2)
    async def id_voice_check(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._save_and_show(interaction, CHOICES_BY_KEY["id_voice_check"])

    @discord.ui.button(label="Custom setup", emoji="⚙️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_choice:custom", row=3)
    async def custom(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._save_and_show(interaction, CHOICES_BY_KEY["custom_setup"])


class AfterChoiceView(solid.BackToSetupView):
    @discord.ui.button(label="Use My Existing Server", emoji="🧩", style=discord.ButtonStyle.primary, custom_id="dank_setup_after_choice:existing", row=0)
    async def existing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_existing_server_setup(interaction)

    @discord.ui.button(label="Create Missing Items", emoji="✨", style=discord.ButtonStyle.success, custom_id="dank_setup_after_choice:create", row=0)
    async def create_missing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_create_missing_items(interaction)

    @discord.ui.button(label="Setup Check", emoji="🩺", style=discord.ButtonStyle.secondary, custom_id="dank_setup_after_choice:health", row=1)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_plain_health(interaction)


class CreateMissingItemsView(solid.BackToSetupView):
    @discord.ui.button(label="Create Basic Missing Items", emoji="✨", style=discord.ButtonStyle.success, custom_id="dank_setup_plain:confirm_create_missing", row=0)
    async def confirm_create(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        try:
            from . import public_setup_defaults
            await public_setup_defaults._setup_defaults_callback(interaction)
            if interaction.guild is not None:
                try:
                    created, skipped, error = await solid._seed_recommended_categories(interaction.guild)
                except Exception as e:
                    created, skipped, error = [], [], f"{type(e).__name__}: {str(e)[:220]}"
                msg = "✅ Missing starter items were handled.\n\n**Next:** run `/dank setup`, press **Setup Check**, then post the panel you need: `/verify panel` for Basic Verify or `/ticket-panel post` for tickets."
                if error:
                    msg += f"\n\n⚠️ Ticket menu options could not be checked: `{error}`"
                elif created:
                    msg += f"\n\nCreated ticket menu options: {', '.join(f'`{x}`' for x in created)}"
                elif skipped:
                    msg += "\n\nTicket menu options already existed."
                await interaction.followup.send(msg, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except Exception as e:
            msg = f"❌ Create Missing Items failed: `{type(e).__name__}: {str(e)[:250]}`"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)


FreshChoiceHomeView = PlainSetupHomeView
FreshServerChoiceView = PlainSetupHomeView


def _patch() -> None:
    global _PATCHED
    try:
        recovery._ORIGINAL_BUILD_MAIN = _plain_choice_main_payload
        solid._build_main_setup_payload = recovery._build_main_with_recovery
    except Exception:
        solid._build_main_setup_payload = _plain_choice_main_payload
    _PATCHED = True


_patch()


def register_public_setup_fresh_choice_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    _patch()
    print("✅ public_setup_fresh_choice: plain setup choices active")


__all__ = ["register_public_setup_fresh_choice_commands", "get_plain_setup_choice", "PlainSetupChoice", "SETUP_CHOICES", "CHOICES_BY_KEY"]
