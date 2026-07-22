from __future__ import annotations

"""Quick Setup choice owner for public ``/dank setup``."""

from dataclasses import dataclass
from typing import Any, Optional

import discord

from ..globals import now_utc
from ..setup_engine.verification_modes import id_verify_allowed_for_guild
from ..setup_service_state import (
    load_setup_service_state,
    normalize_custom_service_patch,
    save_custom_service_state,
)
from . import public_setup_recommend as recommend
from . import public_setup_solid as solid


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
    PlainSetupChoice(
        "basic_server",
        "Recommended Setup",
        "🏠",
        "Fast AIO starter with tickets, SpamGuard, and essential logs.",
        "A simple support path plus baseline spam protection.",
        True,
        False,
        False,
        "basic",
    ),
    PlainSetupChoice(
        "basic_verify",
        "Simple Verify",
        "✅",
        "One-button member verification with SpamGuard and essential logs.",
        "One Verify button that gives approved members server access.",
        False,
        False,
        False,
        "basic_verify",
    ),
    PlainSetupChoice(
        "help_desk",
        "Help Desk / Tickets",
        "🎫",
        "Support tickets with SpamGuard and essential staff logs.",
        "A ticket panel for help requests, reports, appeals, and staff support.",
        True,
        False,
        False,
        "help_desk",
    ),
    PlainSetupChoice(
        "voice_check",
        "Voice Verify",
        "🎙️",
        "Member verification with a staff voice-check option.",
        "A verification flow with an option to request a staff voice check.",
        True,
        False,
        True,
        "voice_check",
    ),
    PlainSetupChoice(
        "id_check",
        "ID / Web Verify",
        "🪪",
        "Private ID/Web verification for approved servers.",
        "A private verification flow for staff review.",
        True,
        True,
        False,
        "id_check",
    ),
    PlainSetupChoice(
        "id_voice_check",
        "ID / Web + Voice",
        "🔐",
        "Private ID/Web verification plus a staff voice-check option.",
        "Private verification plus an option to request a staff voice check.",
        True,
        True,
        True,
        "id_voice_check",
    ),
    PlainSetupChoice(
        "custom_setup",
        "Choose Core Features",
        "⚙️",
        (
            "Choose only the core modules that need roles, channels, or "
            "permissions. Other AIO tools stay under Manage Setup."
        ),
        "Only the core features you enable on the next screen.",
        False,
        False,
        False,
        "custom",
    ),
)

CHOICES_BY_KEY: dict[str, PlainSetupChoice] = {
    choice.key: choice
    for choice in SETUP_CHOICES
}

CUSTOM_PRESETS: dict[str, tuple[str, dict[str, bool], str, str]] = {
    "tickets": (
        "Tickets only",
        {
            "tickets_enabled": True,
            "verification_enabled": False,
            "voice_verification_enabled": False,
            "spam_guard_enabled": False,
            "moderation_enabled": False,
        },
        "Support ticket panel and ticket tools.",
        "🎫",
    ),
    "basic_verify": (
        "Simple Verify only",
        {
            "tickets_enabled": False,
            "verification_enabled": True,
            "voice_verification_enabled": False,
            "spam_guard_enabled": False,
            "moderation_enabled": False,
        },
        "One Verify button. No tickets, ID upload, or voice check.",
        "✅",
    ),
    "voice_verify": (
        "Simple + Voice Verify",
        {
            "tickets_enabled": True,
            "verification_enabled": True,
            "voice_verification_enabled": True,
            "spam_guard_enabled": False,
            "moderation_enabled": True,
        },
        "Simple Verify plus a staff voice check.",
        "🎙️",
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
        "Spam and raid protection with logs.",
        "🛡️",
    ),
    "all": (
        "All Core Features",
        {
            "tickets_enabled": True,
            "verification_enabled": True,
            "voice_verification_enabled": True,
            "spam_guard_enabled": True,
            "moderation_enabled": True,
        },
        "Tickets, Simple Verify, Voice Verify, SpamGuard, and essential logs.",
        "🚀",
    ),
}


def get_plain_setup_choice(key: Any) -> Optional[PlainSetupChoice]:
    return CHOICES_BY_KEY.get(str(key or "").strip().lower())


def _choices_for_guild(
    guild: Optional[discord.Guild],
) -> tuple[PlainSetupChoice, ...]:
    if id_verify_allowed_for_guild(guild):
        return SETUP_CHOICES
    return tuple(
        choice
        for choice in SETUP_CHOICES
        if not choice.needs_id
    )


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


def _service_flags_for_choice(
    choice: PlainSetupChoice,
) -> dict[str, bool]:
    if choice.key == "basic_server":
        return {
            "tickets_enabled": True,
            "verification_enabled": False,
            "voice_verification_enabled": False,
            "spam_guard_enabled": True,
            "moderation_enabled": True,
        }
    if choice.key == "basic_verify":
        return {
            "tickets_enabled": False,
            "verification_enabled": True,
            "voice_verification_enabled": False,
            "spam_guard_enabled": True,
            "moderation_enabled": True,
        }
    if choice.key == "help_desk":
        return {
            "tickets_enabled": True,
            "verification_enabled": False,
            "voice_verification_enabled": False,
            "spam_guard_enabled": True,
            "moderation_enabled": True,
        }
    if choice.key == "voice_check":
        return {
            "tickets_enabled": True,
            "verification_enabled": True,
            "voice_verification_enabled": True,
            "spam_guard_enabled": True,
            "moderation_enabled": True,
        }
    if choice.key in {"id_check", "id_voice_check"}:
        return {
            "tickets_enabled": True,
            "verification_enabled": True,
            "voice_verification_enabled": bool(choice.needs_voice),
            "spam_guard_enabled": True,
            "moderation_enabled": True,
        }
    return {
        "tickets_enabled": False,
        "verification_enabled": False,
        "voice_verification_enabled": False,
        "spam_guard_enabled": False,
        "moderation_enabled": False,
    }


def _choice_payload(choice: PlainSetupChoice) -> dict[str, Any]:
    basic_verify = choice.key == "basic_verify"
    service_flags = _service_flags_for_choice(choice)
    verification_mode = (
        "basic_button"
        if basic_verify
        else "custom"
        if choice.key == "custom_setup"
        else choice.panel_style
    )
    return {
        **service_flags,
        "setup_choice": choice.key,
        "setup_choice_label": choice.label,
        "setup_choice_description": choice.short,
        "setup_choice_member_sees": choice.member_sees,
        "setup_template_version": "plain_choices_v5_aio_quick_setup",
        "ticket_service_enabled": bool(
            service_flags.get("tickets_enabled", False)
        ),
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
        "stoney_baloney_style_enabled": bool(
            choice.key == "id_voice_check"
        ),
        "public_branding_mode": "guild_neutral",
    }


async def _save_choice(
    interaction: discord.Interaction,
    choice: PlainSetupChoice,
) -> None:
    await solid._save_config(
        interaction,
        _choice_payload(choice),
    )  # type: ignore[attr-defined]


def _state_word(value: bool) -> str:
    return "ON ✅" if value else "OFF ⬜"


def _service_summary_text(state: Any) -> str:
    return (
        f"Tickets: **{_state_word(bool(state.tickets))}**\n"
        f"Simple Verify: **{_state_word(bool(state.verification))}**\n"
        f"Voice Verify: **{_state_word(bool(state.voice))}**\n"
        f"SpamGuard: **{_state_word(bool(state.spamguard))}**\n"
        f"Essential Logs: **{_state_word(bool(state.moderation))}**"
    )


def _custom_enabled_labels_from_payload(
    payload: dict[str, Any],
) -> list[str]:
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
        labels.append("Essential Logs")
    return labels


def _custom_mix_label(payload: dict[str, Any]) -> str:
    labels = _custom_enabled_labels_from_payload(payload)
    return "Your features: " + (
        ", ".join(labels)
        if labels
        else "No features selected"
    )


def _custom_preset_key_for_payload(
    payload: dict[str, Any],
) -> str:
    clean = {
        key: bool(payload.get(key, False))
        for key in _CUSTOM_SERVICE_FLAG_KEYS
    }
    for preset_key, (
        _label,
        flags,
        _desc,
        _emoji,
    ) in CUSTOM_PRESETS.items():
        preset_clean = {
            key: bool(flags.get(key, False))
            for key in _CUSTOM_SERVICE_FLAG_KEYS
        }
        if preset_clean == clean:
            return preset_key
    return ""


def _custom_service_config_patch(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Use the canonical native service-state normalizer."""

    return normalize_custom_service_patch(payload)


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
    if not enabled:
        return "Choose at least one core feature first."
    return "Quick Setup will check: " + ", ".join(enabled) + "."


async def _save_custom_services(
    guild_id: int,
    payload: dict[str, bool],
    actor: Any,
) -> None:
    await save_custom_service_state(
        int(guild_id),
        dict(payload),
        actor=actor,
    )


async def _load_custom_state(guild_id: int) -> Any:
    return await load_setup_service_state(int(guild_id))


_CUSTOM_SERVICE_FLAG_KEYS = (
    "tickets_enabled",
    "verification_enabled",
    "voice_verification_enabled",
    "spam_guard_enabled",
    "moderation_enabled",
)


def _auto_cfg_value(
    cfg: Any,
    key: str,
    default: Any = None,
) -> Any:
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
            nested = (
                cfg.get(bucket)
                if hasattr(cfg, "get")
                else getattr(cfg, bucket, None)
            )
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


def _guild_has_category(
    guild: discord.Guild,
    markers: tuple[str, ...],
) -> bool:
    try:
        return any(
            _name_has_any(category, markers)
            for category in getattr(guild, "categories", []) or []
        )
    except Exception:
        return False


def _guild_has_text_channel(
    guild: discord.Guild,
    markers: tuple[str, ...],
) -> bool:
    try:
        return any(
            _name_has_any(channel, markers)
            for channel in getattr(guild, "text_channels", []) or []
        )
    except Exception:
        return False


def _guild_has_voice_channel(
    guild: discord.Guild,
    markers: tuple[str, ...],
) -> bool:
    try:
        return any(
            _name_has_any(channel, markers)
            for channel in getattr(guild, "voice_channels", []) or []
        )
    except Exception:
        return False


def _guild_has_role(
    guild: discord.Guild,
    markers: tuple[str, ...],
) -> bool:
    try:
        return any(
            _name_has_any(role, markers)
            for role in getattr(guild, "roles", []) or []
        )
    except Exception:
        return False


async def _detect_existing_service_payload(
    guild: discord.Guild,
) -> tuple[dict[str, bool], list[str]]:
    """Detect already-installed server pieces. This never creates anything."""

    cfg = None
    try:
        cfg = await solid.get_guild_config(
            guild.id,
            refresh=True,
        )  # type: ignore[attr-defined]
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
        or _guild_has_category(
            guild,
            ("ticket", "archive", "support"),
        )
        or _guild_has_text_channel(
            guild,
            ("ticket", "support", "transcript"),
        )
    )

    basic_verify = bool(
        _auto_truthy(
            _auto_cfg_value(cfg, "basic_verify_enabled", False),
            False,
        )
        or _auto_truthy(
            _auto_cfg_value(
                cfg,
                "basic_button_verify_enabled",
                False,
            ),
            False,
        )
        or _cfg_has_any_id(
            cfg,
            "verify_channel_id",
            "verification_channel_id",
            "unverified_role_id",
            "verified_role_id",
            "resident_role_id",
        )
        or _guild_has_text_channel(
            guild,
            ("verify", "verification"),
        )
        or _guild_has_role(
            guild,
            ("unverified", "verified", "resident", "member"),
        )
    )

    voice = bool(
        _auto_truthy(
            _auto_cfg_value(
                cfg,
                "voice_verification_enabled",
                False,
            ),
            False,
        )
        or _auto_truthy(
            _auto_cfg_value(
                cfg,
                "verification_allows_voice",
                False,
            ),
            False,
        )
        or _cfg_has_any_id(
            cfg,
            "vc_verify_channel_id",
            "vc_verify_queue_channel_id",
            "voice_verify_channel_id",
            "voice_verification_channel_id",
        )
        or _guild_has_text_channel(
            guild,
            ("vc-verify", "voice-verify", "verify-queue"),
        )
        or _guild_has_voice_channel(
            guild,
            ("verify", "verification", "waiting"),
        )
    )

    spamguard = bool(
        _auto_truthy(
            _auto_cfg_value(cfg, "spam_guard_enabled", False),
            False,
        )
        or _auto_truthy(
            _auto_cfg_value(cfg, "automod_enabled", False),
            False,
        )
        or _auto_truthy(
            _auto_cfg_value(cfg, "automod_block_invites", False),
            False,
        )
        or _auto_truthy(
            _auto_cfg_value(cfg, "invite_shield_enabled", False),
            False,
        )
    )

    moderation = bool(
        _auto_truthy(
            _auto_cfg_value(cfg, "moderation_enabled", False),
            False,
        )
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
        or _guild_has_text_channel(
            guild,
            (
                "modlog",
                "mod-log",
                "logs",
                "join-leave",
                "bot-status",
                "status",
            ),
        )
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

    labels: list[str] = []
    if tickets:
        labels.append("Tickets")
    if basic_verify:
        labels.append("Simple Verify")
    if voice:
        labels.append("Voice Verify")
    if spamguard:
        labels.append("SpamGuard")
    if moderation:
        labels.append("Essential Logs")

    return detected, labels


async def _autofill_custom_state_from_existing(
    guild: discord.Guild,
    state: Any,
) -> tuple[Any, str]:
    """Preselect existing core services when Custom Setup is still blank.

    This only saves setup-focus flags. It does not create or delete channels,
    roles, tickets, panels, or permissions.
    """

    try:
        current = state.as_payload()
    except Exception:
        current = {}

    # Once an owner has explicitly saved Custom Setup feature switches, those
    # choices are authoritative even when every switch is OFF. Do not resurrect
    # disabled services merely because old Discord resources still exist.
    try:
        cfg = await solid.get_guild_config(
            guild.id,
            refresh=True,
        )  # type: ignore[attr-defined]
    except Exception:
        cfg = None
    if str(
        _auto_cfg_value(cfg, "setup_service_mode_saved_at", "") or ""
    ).strip():
        return state, ""

    if any(
        bool(current.get(key, False))
        for key in _CUSTOM_SERVICE_FLAG_KEYS
    ):
        return state, ""

    detected, labels = await _detect_existing_service_payload(guild)
    if not any(detected.values()):
        return state, ""

    await _save_custom_services(
        guild.id,
        detected,
        guild.me or guild.owner,
    )
    next_state = await _load_custom_state(guild.id)

    label_text = ", ".join(labels) if labels else "existing setup"
    return (
        next_state,
        (
            "Found matching server setup and pre-selected: "
            f"**{label_text}**. Nothing was created."
        ),
    )


def _custom_services_embed(
    guild: discord.Guild,
    state: Any,
    *,
    saved_message: str = "",
) -> discord.Embed:
    payload = {
        key: bool(state.as_payload().get(key, False))
        for key in _CUSTOM_SERVICE_FLAG_KEYS
    }
    preset_key = _custom_preset_key_for_payload(payload)
    preset_label = (
        CUSTOM_PRESETS.get(
            preset_key,
            ("Your choices", {}, "", "🧩"),
        )[0]
        if preset_key
        else "Your choices"
    )

    embed = discord.Embed(
        title="🧩 Choose Core Features",
        description=(
            "Choose the core modules that need server setup. Green means ON "
            "and gray means OFF. Server Design, Backups & History, activity "
            "tools, and repair options stay available under **Manage Setup**."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    if saved_message:
        embed.add_field(
            name="Saved",
            value=saved_message[:1024],
            inline=False,
        )

    embed.add_field(
        name="Core Setup Plan",
        value=(
            f"**{preset_label}**\n"
            f"{_custom_mix_label(payload)}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Core Modules",
        value=_service_summary_text(state),
        inline=False,
    )
    embed.add_field(
        name="Next",
        value=(
            "Choose the core modules, then press **Continue Setup**. "
            "Dank Shield asks only for the roles, channels, and permissions "
            "those modules require."
        ),
        inline=False,
    )
    embed.set_footer(
        text=f"Guild {guild.id} • choose core features"
    )
    return embed


async def _reconcile_voice_resources_if_disabled(
    guild: discord.Guild,
    state: Any,
    *,
    actor: Any = None,
) -> str:
    if bool(getattr(state, "voice", False)):
        return ""
    try:
        from ..setup_resource_reconcile import (
            reconcile_disabled_voice_verify,
        )

        return await reconcile_disabled_voice_verify(
            guild,
            actor=actor,
        )
    except Exception as exc:
        return (
            "⚠️ Voice Verify is OFF, but its unused server items could not "
            f"be reconciled: `{type(exc).__name__}: {str(exc)[:180]}`"
        )


async def _open_legacy_voice_cleanup_if_needed(
    interaction: discord.Interaction,
    guild: discord.Guild,
    result_message: str,
    *,
    already_deferred: bool,
) -> bool:
    """Open explicit cleanup review when legacy Voice items remain after OFF."""

    if not str(result_message or "").strip():
        return False

    from .. import setup_legacy_voice_cleanup
    from .. import setup_legacy_voice_cleanup_ui

    preview = await (
        setup_legacy_voice_cleanup.find_legacy_voice_cleanup_candidates(
            guild
        )
    )
    if preview.blocked_reason or not preview.has_candidates:
        return False

    await setup_legacy_voice_cleanup_ui.open_legacy_voice_cleanup_review(
        interaction,
        result_message=str(result_message),
        already_deferred=already_deferred,
    )
    return True


class CustomServicePresetSelect(discord.ui.Select):
    def __init__(self, current: Any) -> None:
        options: list[discord.SelectOption] = []
        current_payload = {
            key: bool(current.as_payload().get(key, False))
            for key in _CUSTOM_SERVICE_FLAG_KEYS
        }
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

        for key, (
            label,
            flags,
            desc,
            emoji,
        ) in CUSTOM_PRESETS.items():
            preset_clean = {
                flag_key: bool(flags.get(flag_key, False))
                for flag_key in _CUSTOM_SERVICE_FLAG_KEYS
            }
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

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "❌ This must be used inside a server.",
                ephemeral=True,
            )

        key = str(self.values[0])
        if key == "__custom_current__":
            await interaction.response.defer(ephemeral=True)
            state = await _load_custom_state(guild.id)
            return await interaction.edit_original_response(
                embed=_custom_services_embed(
                    guild,
                    state,
                    saved_message=(
                        "Still using your current core feature choices."
                    ),
                ),
                view=CustomServiceModeView(state),
            )

        preset = CUSTOM_PRESETS.get(key)
        if preset is None:
            return await interaction.response.send_message(
                (
                    "❌ That feature choice is no longer available. "
                    "Choose another option."
                ),
                ephemeral=True,
            )
        label, flags, desc, _emoji = preset
        await interaction.response.defer(ephemeral=True)
        await _save_custom_services(
            guild.id,
            dict(flags),
            interaction.user,
        )
        state = await _load_custom_state(guild.id)
        reconcile_note = await _reconcile_voice_resources_if_disabled(
            guild,
            state,
            actor=interaction.user,
        )
        saved_message = f"Saved **{label}**. {desc}"
        if reconcile_note:
            saved_message += f"\n{reconcile_note}"
            if await _open_legacy_voice_cleanup_if_needed(
                interaction,
                guild,
                saved_message,
                already_deferred=True,
            ):
                return
        await interaction.edit_original_response(
            embed=_custom_services_embed(
                guild,
                state,
                saved_message=saved_message,
            ),
            view=CustomServiceModeView(state),
        )


def _apply_custom_service_toggle(
    payload: dict[str, Any],
    key: str,
) -> tuple[dict[str, bool], bool, bool, str]:
    """Apply one visible toggle without hiding dependency changes from owners."""

    clean = {
        flag: bool(payload.get(flag, False))
        for flag in _CUSTOM_SERVICE_FLAG_KEYS
    }
    if key not in clean:
        return clean, False, False, "That core feature is no longer available."

    next_value = not clean[key]

    if not next_value:
        required_by: list[str] = []
        if key in {"tickets_enabled", "verification_enabled"} and clean[
            "voice_verification_enabled"
        ]:
            required_by.append("Voice Verify")
        if key == "moderation_enabled":
            if clean["voice_verification_enabled"]:
                required_by.append("Voice Verify")
            if clean["spam_guard_enabled"]:
                required_by.append("SpamGuard")

        if required_by:
            names = " and ".join(required_by)
            return (
                clean,
                True,
                False,
                (
                    f"**{names}** needs **"
                    + {
                        "tickets_enabled": "Tickets",
                        "verification_enabled": "Simple Verify",
                        "moderation_enabled": "Essential Logs",
                    }[key]
                    + "**. Turn the dependent feature off first."
                ),
            )

    clean[key] = next_value
    dependency_note = ""

    if key == "voice_verification_enabled" and next_value:
        required = (
            ("verification_enabled", "Simple Verify"),
            ("tickets_enabled", "Tickets"),
            ("moderation_enabled", "Essential Logs"),
        )
        enabled_for_dependency = [
            label
            for dependency_key, label in required
            if not clean[dependency_key]
        ]
        for dependency_key, _label in required:
            clean[dependency_key] = True
        if enabled_for_dependency:
            dependency_note = (
                "Voice Verify needs Simple Verify, Tickets, and Essential Logs, "
                "so Dank Shield also turned on: **"
                + "**, **".join(enabled_for_dependency)
                + "**."
            )

    if key == "spam_guard_enabled" and next_value and not clean["moderation_enabled"]:
        clean["moderation_enabled"] = True
        dependency_note = (
            "SpamGuard needs Essential Logs, so Dank Shield also turned on "
            "**Essential Logs**."
        )

    return clean, bool(clean[key]), True, dependency_note


class CustomServiceToggleButton(discord.ui.Button):
    def __init__(
        self,
        key: str,
        label: str,
        selected: bool,
        emoji: str,
        row: int,
    ) -> None:
        state_text = "ON ✅" if selected else "OFF ⬜"
        super().__init__(
            label=f"{label}: {state_text}",
            emoji=emoji,
            style=(
                discord.ButtonStyle.success
                if selected
                else discord.ButtonStyle.secondary
            ),
            custom_id=f"dank_setup_custom_toggle:{key}",
            row=row,
        )
        self.key = key
        self.short_label = label

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "❌ This must be used inside a server.",
                ephemeral=True,
            )

        state = await _load_custom_state(guild.id)
        payload, effective_value, changed, dependency_note = (
            _apply_custom_service_toggle(
                state.as_payload(),
                self.key,
            )
        )

        await interaction.response.defer(ephemeral=True)
        if changed:
            await _save_custom_services(
                guild.id,
                payload,
                interaction.user,
            )
            next_state = await _load_custom_state(guild.id)
            saved_message = (
                f"Set **{self.short_label}** to "
                f"**{'ON' if effective_value else 'OFF'}**."
            )
            reconcile_note = await _reconcile_voice_resources_if_disabled(
                guild,
                next_state,
                actor=interaction.user,
            )
            if reconcile_note:
                saved_message += f"\n{reconcile_note}"
        else:
            next_state = state
            saved_message = (
                f"Kept **{self.short_label}** "
                f"**{'ON' if effective_value else 'OFF'}**."
            )

        if dependency_note:
            saved_message += f"\n{dependency_note}"

        if changed and not bool(getattr(next_state, "voice", False)):
            if await _open_legacy_voice_cleanup_if_needed(
                interaction,
                guild,
                saved_message,
                already_deferred=True,
            ):
                return

        await interaction.edit_original_response(
            embed=_custom_services_embed(
                guild,
                next_state,
                saved_message=saved_message,
            ),
            view=CustomServiceModeView(next_state),
        )


class CustomServiceModeView(discord.ui.View):
    """Choose core modules, then return to the single Quick Setup path."""

    def __init__(self, state: Any) -> None:
        super().__init__(timeout=900)
        self.add_item(CustomServicePresetSelect(state))
        self.add_item(
            CustomServiceToggleButton(
                "tickets_enabled",
                "Tickets",
                state.tickets,
                "🎫",
                2,
            )
        )
        self.add_item(
            CustomServiceToggleButton(
                "verification_enabled",
                "Simple Verify",
                state.verification,
                "✅",
                2,
            )
        )
        self.add_item(
            CustomServiceToggleButton(
                "voice_verification_enabled",
                "Voice Verify",
                state.voice,
                "🎙️",
                2,
            )
        )
        self.add_item(
            CustomServiceToggleButton(
                "spam_guard_enabled",
                "SpamGuard",
                state.spamguard,
                "🛡️",
                3,
            )
        )
        self.add_item(
            CustomServiceToggleButton(
                "moderation_enabled",
                "Essential Logs",
                state.moderation,
                "🧾",
                3,
            )
        )

    @discord.ui.button(
        label="Continue Setup",
        emoji="➡️",
        style=discord.ButtonStyle.success,
        custom_id="dank_setup_custom:continue_quick",
        row=1,
    )
    async def continue_guided(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "❌ This must be used inside a server.",
                ephemeral=True,
            )
        await solid._safe_defer_update(interaction)
        state = await _load_custom_state(guild.id)
        reconcile_note = await _reconcile_voice_resources_if_disabled(
            guild,
            state,
            actor=interaction.user,
        )
        if reconcile_note and await _open_legacy_voice_cleanup_if_needed(
            interaction,
            guild,
            reconcile_note,
            already_deferred=True,
        ):
            return
        await recommend._open_guided_setup(
            interaction,
            saved_message=reconcile_note,
        )

    @discord.ui.button(
        label="Back",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_custom:plans",
        row=4,
    )
    async def back(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await recommend._open_choose_setup_type(interaction)

    @discord.ui.button(
        label="Setup Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_custom:home",
        row=4,
    )
    async def home(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await recommend._home_edit(interaction)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        custom_id="dank_setup_custom:close",
        row=4,
    )
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await recommend._close_setup(interaction)


async def _open_custom_service_picker(
    interaction: discord.Interaction,
    *,
    saved_message: str = "",
) -> None:
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(
            "❌ This must be used inside a server.",
            ephemeral=True,
        )
    state = await _load_custom_state(guild.id)
    state, detected_message = await _autofill_custom_state_from_existing(
        guild,
        state,
    )
    message = (
        saved_message
        or detected_message
        or (
            "Saved **Choose Core Features**. Dank Shield checked the existing "
            "server and pre-selected matching core modules. Turn off anything "
            "you do not want."
        )
    )
    await solid._edit_or_followup(
        interaction,
        embed=_custom_services_embed(
            guild,
            state,
            saved_message=message,
        ),
        view=CustomServiceModeView(state),
    )


async def _open_plain_health(
    interaction: discord.Interaction,
) -> None:
    """Use the one canonical feature-aware Setup Check."""

    await recommend._open_health_check(interaction)


class SetupTypeChoiceSelect(discord.ui.Select):
    def __init__(
        self,
        *,
        guild: Optional[discord.Guild] = None,
    ) -> None:
        choices = _choices_for_guild(guild)
        super().__init__(
            placeholder="Choose a setup plan…",
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

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        view = self.view
        if not isinstance(view, SetupTypeChoiceView):
            return
        choice = CHOICES_BY_KEY.get(str(self.values[0]))
        if choice is None:
            return await interaction.response.send_message(
                "❌ Unknown setup plan.",
                ephemeral=True,
            )
        await view._save_and_show(interaction, choice)


class SetupTypeChoiceView(discord.ui.View):
    """Root Quick Setup plan picker with no advanced-page back route."""

    def __init__(
        self,
        *,
        guild: Optional[discord.Guild] = None,
    ) -> None:
        super().__init__(timeout=900)
        self.add_item(SetupTypeChoiceSelect(guild=guild))

    @discord.ui.button(
        label="Setup Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_plans:home",
        row=1,
    )
    async def home(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await recommend._home_edit(interaction)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        custom_id="dank_setup_plans:close",
        row=1,
    )
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await recommend._close_setup(interaction)

    async def _save_and_show(
        self,
        interaction: discord.Interaction,
        choice: PlainSetupChoice,
    ) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "❌ This must be used inside a server.",
                ephemeral=True,
            )
        if (
            choice.needs_id
            and not id_verify_allowed_for_guild(guild)
        ):
            return await interaction.response.send_message(
                (
                    "🔒 ID/Web Verify is not available for this server. "
                    "Use **Simple Verify** instead."
                ),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        await solid._safe_defer_update(interaction)
        await _save_choice(interaction, choice)
        if choice.key == "custom_setup":
            return await _open_custom_service_picker(
                interaction,
                saved_message=(
                    "Saved **Choose Core Features**. Choose the core modules "
                    "this server should use, then press "
                    "**Continue Setup**."
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
    """Register Quick Setup choices without replacing Setup Home."""

    _ = bot, tree
    print(
        "✅ public_setup_fresh_choice: "
        "Quick Setup choices ready"
    )


__all__ = [
    "register_public_setup_fresh_choice_commands",
    "get_plain_setup_choice",
    "PlainSetupChoice",
    "SETUP_CHOICES",
    "CHOICES_BY_KEY",
]
