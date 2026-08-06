from __future__ import annotations

import ast
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def _node(path: str, name: str) -> ast.AST:
    tree = ast.parse(_read(path), filename=path)
    matches = [
        item
        for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and item.name == name
    ]
    if len(matches) != 1:
        raise RuntimeError(f"{path}: expected one top-level {name}, found {len(matches)}")
    return matches[0]


def replace_node(path: str, name: str, replacement: str) -> None:
    text = _read(path)
    node = _node(path, name)
    lines = text.splitlines(keepends=True)
    start = int(node.lineno) - 1
    end = int(node.end_lineno or node.lineno)
    clean = textwrap.dedent(replacement).strip("\n") + "\n\n"
    lines[start:end] = [clean]
    _write(path, "".join(lines))


def replace_once(path: str, old: str, new: str) -> None:
    text = _read(path)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one replacement, found {count}: {old[:80]!r}")
    _write(path, text.replace(old, new, 1))


def replace_assignment_block(path: str, start_marker: str, end_marker: str, replacement: str) -> None:
    text = _read(path)
    start = text.find(start_marker)
    if start < 0:
        raise RuntimeError(f"{path}: missing assignment start {start_marker!r}")
    end = text.find(end_marker, start)
    if end < 0:
        raise RuntimeError(f"{path}: missing assignment end {end_marker!r}")
    clean = textwrap.dedent(replacement).strip("\n") + "\n\n"
    _write(path, text[:start] + clean + text[end + len(end_marker) :])


def replace_between_in_node(
    path: str,
    name: str,
    start_marker: str,
    end_marker: str,
    replacement: str,
) -> None:
    text = _read(path)
    node = _node(path, name)
    lines = text.splitlines(keepends=True)
    start_offset = sum(len(line) for line in lines[: int(node.lineno) - 1])
    end_offset = sum(len(line) for line in lines[: int(node.end_lineno or node.lineno)])
    segment = text[start_offset:end_offset]
    start = segment.find(start_marker)
    end = segment.find(end_marker, start + len(start_marker))
    if start < 0 or end < 0:
        raise RuntimeError(f"{path}:{name}: markers not found")
    clean = textwrap.dedent(replacement).strip("\n") + "\n\n"
    segment = segment[:start] + clean + segment[end:]
    _write(path, text[:start_offset] + segment + text[end_offset:])


def replace_first_in_node(path: str, name: str, old: str, new: str) -> None:
    text = _read(path)
    node = _node(path, name)
    lines = text.splitlines(keepends=True)
    start_offset = sum(len(line) for line in lines[: int(node.lineno) - 1])
    end_offset = sum(len(line) for line in lines[: int(node.end_lineno or node.lineno)])
    segment = text[start_offset:end_offset]
    if segment.count(old) < 1:
        raise RuntimeError(f"{path}:{name}: replacement not found: {old[:80]!r}")
    segment = segment.replace(old, new, 1)
    _write(path, text[:start_offset] + segment + text[end_offset:])


def patch_service_state() -> None:
    path = "stoney_verify/setup_service_state.py"
    replace_once(
        path,
        '        "verification_enabled": simple,\n        "basic_verify_enabled": simple,',
        '        # Runtime compatibility keeps the aggregate verification switch ON\n'
        '        # whenever any verification route is selected. The explicit basic\n'
        '        # flags below remain the source of truth for Simple Verify itself.\n'
        '        "verification_enabled": bool(simple or voice or id_verify),\n'
        '        "basic_verify_enabled": simple,',
    )


def patch_fresh_choice() -> None:
    path = "stoney_verify/commands_ext/public_setup_fresh_choice.py"
    replace_once(
        path,
        "from ..setup_engine.verification_modes import id_verify_allowed_for_guild\n",
        "from ..setup_engine.verification_modes import (\n"
        "    id_verify_allowed_for_guild,\n"
        "    id_verify_allowed_guild_ids,\n"
        ")\n",
    )
    replace_once(
        path,
        "from ..setup_service_state import (\n"
        "    load_setup_service_state,\n"
        "    normalize_custom_service_patch,\n"
        "    save_custom_service_state,\n"
        ")\n",
        "from ..setup_service_state import (\n"
        "    apply_custom_service_toggle,\n"
        "    load_setup_service_state,\n"
        "    normalize_custom_service_patch,\n"
        "    save_custom_service_state,\n"
        "    toggle_custom_service_state,\n"
        ")\n",
    )

    replace_assignment_block(
        path,
        "CUSTOM_PRESETS:",
        "\n\n\ndef get_plain_setup_choice",
        '''
        CUSTOM_PRESETS: dict[str, tuple[str, dict[str, bool], str, str]] = {
            "tickets": (
                "Tickets only",
                {
                    "tickets_enabled": True,
                    "verification_enabled": False,
                    "voice_verification_enabled": False,
                    "id_verify_enabled": False,
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
                    "id_verify_enabled": False,
                    "spam_guard_enabled": False,
                    "moderation_enabled": False,
                },
                "One Verify button. No ID upload or voice check.",
                "✅",
            ),
            "voice_verify": (
                "Voice Verify",
                {
                    "tickets_enabled": True,
                    "verification_enabled": False,
                    "voice_verification_enabled": True,
                    "id_verify_enabled": False,
                    "spam_guard_enabled": False,
                    "moderation_enabled": True,
                },
                "Staff voice verification without forcing Simple Verify.",
                "🎙️",
            ),
            "id_verify": (
                "ID / Web Verify",
                {
                    "tickets_enabled": True,
                    "verification_enabled": False,
                    "voice_verification_enabled": False,
                    "id_verify_enabled": True,
                    "spam_guard_enabled": False,
                    "moderation_enabled": True,
                },
                "Private ID/Web verification for approved servers.",
                "🪪",
            ),
            "id_voice_verify": (
                "ID / Web + Voice",
                {
                    "tickets_enabled": True,
                    "verification_enabled": False,
                    "voice_verification_enabled": True,
                    "id_verify_enabled": True,
                    "spam_guard_enabled": False,
                    "moderation_enabled": True,
                },
                "Private ID/Web verification plus staff voice verification.",
                "🔐",
            ),
            "spamguard": (
                "SpamGuard only",
                {
                    "tickets_enabled": False,
                    "verification_enabled": False,
                    "voice_verification_enabled": False,
                    "id_verify_enabled": False,
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
                    "id_verify_enabled": False,
                    "spam_guard_enabled": True,
                    "moderation_enabled": True,
                },
                "Tickets, Simple Verify, Voice Verify, SpamGuard, and essential logs.",
                "🚀",
            ),
        }


        def get_plain_setup_choice
        ''',
    )

    replace_node(
        path,
        "_service_flags_for_choice",
        '''
        def _service_flags_for_choice(choice: PlainSetupChoice) -> dict[str, bool]:
            flags = {
                "tickets_enabled": False,
                "verification_enabled": False,
                "voice_verification_enabled": False,
                "id_verify_enabled": False,
                "spam_guard_enabled": False,
                "moderation_enabled": False,
            }
            if choice.key in {"basic_server", "help_desk"}:
                flags.update(
                    tickets_enabled=True,
                    spam_guard_enabled=True,
                    moderation_enabled=True,
                )
            elif choice.key == "basic_verify":
                flags.update(
                    verification_enabled=True,
                    spam_guard_enabled=True,
                    moderation_enabled=True,
                )
            elif choice.key == "voice_check":
                flags.update(
                    tickets_enabled=True,
                    voice_verification_enabled=True,
                    spam_guard_enabled=True,
                    moderation_enabled=True,
                )
            elif choice.key == "id_check":
                flags.update(
                    tickets_enabled=True,
                    id_verify_enabled=True,
                    spam_guard_enabled=True,
                    moderation_enabled=True,
                )
            elif choice.key == "id_voice_check":
                flags.update(
                    tickets_enabled=True,
                    voice_verification_enabled=True,
                    id_verify_enabled=True,
                    spam_guard_enabled=True,
                    moderation_enabled=True,
                )
            return flags
        ''',
    )

    replace_node(
        path,
        "_choice_payload",
        '''
        def _choice_payload(choice: PlainSetupChoice) -> dict[str, Any]:
            service_flags = _service_flags_for_choice(choice)
            simple_verify = bool(service_flags["verification_enabled"])
            voice_verify = bool(service_flags["voice_verification_enabled"])
            id_verify = bool(service_flags["id_verify_enabled"])
            verification_enabled = bool(simple_verify or voice_verify or id_verify)
            verification_mode = (
                "basic_button"
                if simple_verify
                else "custom"
                if choice.key == "custom_setup"
                else choice.panel_style
            )
            return {
                **service_flags,
                "verification_enabled": verification_enabled,
                "setup_choice": choice.key,
                "setup_choice_label": choice.label,
                "setup_choice_description": choice.short,
                "setup_choice_member_sees": choice.member_sees,
                "setup_template_version": "plain_choices_v6_entitled_id_independent",
                "ticket_service_enabled": bool(service_flags["tickets_enabled"]),
                "ticket_flow_style": "fast_no_forced_form",
                "ticket_form_mode": "off",
                "ticket_open_requires_modal": False,
                "ticket_open_requires_form": False,
                "verification_panel_style": choice.panel_style,
                "verification_mode": verification_mode,
                "verify_mode": verification_mode,
                "basic_verify_enabled": simple_verify,
                "basic_button_verify_enabled": simple_verify,
                "voice_verification_enabled": voice_verify,
                "vc_verify_enabled": voice_verify,
                "voice_verify_enabled": voice_verify,
                "verification_requires_id": id_verify,
                "id_verify_enabled": id_verify,
                "web_verify_enabled": id_verify,
                "id_web_verify_enabled": id_verify,
                "verification_allows_voice": voice_verify,
                "verification_style_label": choice.label,
                "stoney_baloney_style_enabled": bool(choice.key == "id_voice_check"),
                "public_branding_mode": "guild_neutral",
            }
        ''',
    )

    replace_node(
        path,
        "_service_summary_text",
        '''
        def _service_summary_text(state: Any) -> str:
            return (
                f"Tickets: **{_state_word(bool(state.tickets))}**\\n"
                f"Simple Verify: **{_state_word(bool(state.verification))}**\\n"
                f"Voice Verify: **{_state_word(bool(state.voice))}**\\n"
                f"ID / Web Verify: **{_state_word(bool(getattr(state, 'id_verify', False)))}**\\n"
                f"SpamGuard: **{_state_word(bool(state.spamguard))}**\\n"
                f"Essential Logs: **{_state_word(bool(state.moderation))}**"
            )
        ''',
    )

    replace_node(
        path,
        "_custom_enabled_labels_from_payload",
        '''
        def _custom_enabled_labels_from_payload(payload: dict[str, Any]) -> list[str]:
            labels: list[str] = []
            if bool(payload.get("tickets_enabled")):
                labels.append("Tickets")
            if bool(payload.get("verification_enabled")):
                labels.append("Simple Verify")
            if bool(payload.get("voice_verification_enabled")):
                labels.append("Voice Verify")
            if bool(payload.get("id_verify_enabled")):
                labels.append("ID/Web Verify")
            if bool(payload.get("spam_guard_enabled")):
                labels.append("SpamGuard")
            if bool(payload.get("moderation_enabled")):
                labels.append("Essential Logs")
            return labels
        ''',
    )

    replace_node(
        path,
        "_service_hint_text",
        '''
        def _service_hint_text(state: Any) -> str:
            enabled = _custom_enabled_labels_from_payload(state.as_payload())
            if not enabled:
                return "Choose at least one core feature first."
            return "Quick Setup will check: " + ", ".join(enabled) + "."
        ''',
    )

    replace_node(
        path,
        "_save_custom_services",
        '''
        async def _save_custom_services(
            guild_id: int,
            payload: dict[str, bool],
            actor: Any,
        ) -> None:
            await save_custom_service_state(
                int(guild_id),
                dict(payload),
                actor=actor,
                allow_id_verify=(int(guild_id) in id_verify_allowed_guild_ids()),
            )
        ''',
    )

    replace_assignment_block(
        path,
        "_CUSTOM_SERVICE_FLAG_KEYS = (",
        "\n\n\ndef _auto_cfg_value",
        '''
        _CUSTOM_SERVICE_FLAG_KEYS = (
            "tickets_enabled",
            "verification_enabled",
            "voice_verification_enabled",
            "id_verify_enabled",
            "spam_guard_enabled",
            "moderation_enabled",
        )


        def _auto_cfg_value
        ''',
    )

    replace_node(
        path,
        "_detect_existing_service_payload",
        '''
        async def _detect_existing_service_payload(
            guild: discord.Guild,
        ) -> tuple[dict[str, bool], list[str]]:
            """Detect existing pieces without turning shared roles into Simple Verify."""

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
                or _cfg_has_any_id(cfg, "verify_channel_id", "verification_channel_id")
                or _guild_has_text_channel(
                    guild,
                    ("・verify", "-verify", "simple verify", "verification button"),
                )
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
                or _guild_has_voice_channel(guild, ("voice verify", "voice verification", "vc verify"))
            )

            entitled = id_verify_allowed_for_guild(guild)
            panel_style = str(_auto_cfg_value(cfg, "verification_panel_style", "") or "").strip().lower()
            id_verify = bool(
                entitled
                and (
                    _auto_truthy(_auto_cfg_value(cfg, "id_verify_enabled", False), False)
                    or _auto_truthy(_auto_cfg_value(cfg, "web_verify_enabled", False), False)
                    or _auto_truthy(_auto_cfg_value(cfg, "id_web_verify_enabled", False), False)
                    or _auto_truthy(_auto_cfg_value(cfg, "verification_requires_id", False), False)
                    or panel_style in {"id_check", "id_voice_check"}
                )
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
                or _guild_has_text_channel(
                    guild,
                    ("modlog", "mod-log", "logs", "join-leave", "bot-status", "status"),
                )
            )

            if voice or id_verify:
                tickets = True
                moderation = True

            detected = {
                "tickets_enabled": tickets,
                "verification_enabled": basic_verify,
                "voice_verification_enabled": voice,
                "id_verify_enabled": id_verify,
                "spam_guard_enabled": spamguard,
                "moderation_enabled": moderation,
            }
            return detected, _custom_enabled_labels_from_payload(detected)
        ''',
    )

    replace_node(
        path,
        "CustomServicePresetSelect",
        '''
        class CustomServicePresetSelect(discord.ui.Select):
            def __init__(
                self,
                current: Any,
                *,
                guild: Optional[discord.Guild] = None,
            ) -> None:
                self.guild = guild
                options: list[discord.SelectOption] = []
                current_payload = {
                    key: bool(current.as_payload().get(key, False))
                    for key in _CUSTOM_SERVICE_FLAG_KEYS
                }
                preset_key = _custom_preset_key_for_payload(current_payload)
                entitled = id_verify_allowed_for_guild(guild)

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
                    if key in {"id_verify", "id_voice_verify"} and not entitled:
                        continue
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

            async def callback(self, interaction: discord.Interaction) -> None:
                if not await solid._require_setup_permission(interaction):
                    return
                guild = interaction.guild
                if guild is None:
                    return await interaction.response.send_message(
                        "❌ This must be used inside a server.",
                        ephemeral=True,
                    )

                key = str(self.values[0])
                await solid._safe_defer_update(interaction)
                if key == "__custom_current__":
                    state = await _load_custom_state(guild.id)
                    return await solid._edit_or_followup(
                        interaction,
                        embed=_custom_services_embed(
                            guild,
                            state,
                            saved_message="Still using your current core feature choices.",
                        ),
                        view=CustomServiceModeView(state, guild=guild),
                    )

                preset = CUSTOM_PRESETS.get(key)
                if preset is None:
                    return await solid._edit_or_followup(
                        interaction,
                        content="❌ That feature choice is no longer available. Reopen setup.",
                        embed=None,
                        view=None,
                    )
                label, flags, desc, _emoji = preset
                if bool(flags.get("id_verify_enabled")) and not id_verify_allowed_for_guild(guild):
                    return await solid._edit_or_followup(
                        interaction,
                        content="❌ ID/Web Verify is not available for this server.",
                        embed=None,
                        view=None,
                    )

                await _save_custom_services(guild.id, dict(flags), interaction.user)
                state = await _load_custom_state(guild.id)
                reconcile_note = await _reconcile_voice_resources_if_disabled(
                    guild,
                    state,
                    actor=interaction.user,
                )
                saved_message = f"Saved **{label}**. {desc}"
                if reconcile_note:
                    saved_message += f"\\n{reconcile_note}"
                    if await _open_legacy_voice_cleanup_if_needed(
                        interaction,
                        guild,
                        saved_message,
                        already_deferred=True,
                    ):
                        return
                await solid._edit_or_followup(
                    interaction,
                    embed=_custom_services_embed(guild, state, saved_message=saved_message),
                    view=CustomServiceModeView(state, guild=guild),
                )
        ''',
    )

    replace_node(
        path,
        "_apply_custom_service_toggle",
        '''
        def _apply_custom_service_toggle(
            payload: dict[str, Any],
            key: str,
            *,
            allow_id_verify: bool = False,
        ) -> tuple[dict[str, bool], bool, bool, str]:
            return apply_custom_service_toggle(
                payload,
                key,
                allow_id_verify=allow_id_verify,
            )
        ''',
    )

    replace_node(
        path,
        "CustomServiceToggleButton",
        '''
        class CustomServiceToggleButton(discord.ui.Button):
            def __init__(
                self,
                key: str,
                label: str,
                selected: bool,
                emoji: str,
                row: int,
                *,
                guild: Optional[discord.Guild] = None,
            ) -> None:
                state_text = "ON ✅" if selected else "OFF ⬜"
                super().__init__(
                    label=f"{label}: {state_text}",
                    emoji=emoji,
                    style=(discord.ButtonStyle.success if selected else discord.ButtonStyle.secondary),
                    custom_id=f"dank_setup_custom_toggle:{key}:v6",
                    row=row,
                )
                self.key = str(key)
                self.short_label = str(label)
                self.selected = bool(selected)
                self.guild = guild

            async def callback(self, interaction: discord.Interaction) -> None:
                if not await solid._require_setup_permission(interaction):
                    return
                guild = interaction.guild
                if guild is None:
                    return await interaction.response.send_message(
                        "❌ This must be used inside a server.",
                        ephemeral=True,
                    )
                allow_id = id_verify_allowed_for_guild(guild)
                if self.key == "id_verify_enabled" and not allow_id:
                    return await interaction.response.send_message(
                        "❌ ID/Web Verify is not available for this server.",
                        ephemeral=True,
                    )

                await solid._safe_defer_update(interaction)
                next_state, effective_value, changed, dependency_note = (
                    await toggle_custom_service_state(
                        guild.id,
                        self.key,
                        actor=interaction.user,
                        allow_id_verify=allow_id,
                        expected_current=self.selected,
                    )
                )

                if changed:
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
                        saved_message += f"\\n{reconcile_note}"
                else:
                    saved_message = dependency_note or (
                        f"Kept **{self.short_label}** "
                        f"**{'ON' if effective_value else 'OFF'}**."
                    )

                if changed and dependency_note:
                    saved_message += f"\\n{dependency_note}"

                if changed and not bool(next_state.voice):
                    if await _open_legacy_voice_cleanup_if_needed(
                        interaction,
                        guild,
                        saved_message,
                        already_deferred=True,
                    ):
                        return

                await solid._edit_or_followup(
                    interaction,
                    embed=_custom_services_embed(
                        guild,
                        next_state,
                        saved_message=saved_message,
                    ),
                    view=CustomServiceModeView(next_state, guild=guild),
                )
        ''',
    )

    replace_node(
        path,
        "CustomServiceModeView",
        '''
        class CustomServiceModeView(discord.ui.View):
            """Independent entitled core-module picker with deterministic navigation."""

            def __init__(
                self,
                state: Any,
                *,
                guild: Optional[discord.Guild] = None,
            ) -> None:
                super().__init__(timeout=900)
                self.guild = guild
                self.add_item(CustomServicePresetSelect(state, guild=guild))
                self.add_item(
                    CustomServiceToggleButton(
                        "tickets_enabled", "Tickets", state.tickets, "🎫", 2, guild=guild
                    )
                )
                self.add_item(
                    CustomServiceToggleButton(
                        "verification_enabled", "Simple Verify", state.verification, "✅", 2, guild=guild
                    )
                )
                self.add_item(
                    CustomServiceToggleButton(
                        "voice_verification_enabled", "Voice Verify", state.voice, "🎙️", 2, guild=guild
                    )
                )
                if id_verify_allowed_for_guild(guild):
                    self.add_item(
                        CustomServiceToggleButton(
                            "id_verify_enabled",
                            "ID/Web Verify",
                            bool(getattr(state, "id_verify", False)),
                            "🪪",
                            2,
                            guild=guild,
                        )
                    )
                self.add_item(
                    CustomServiceToggleButton(
                        "spam_guard_enabled", "SpamGuard", state.spamguard, "🛡️", 3, guild=guild
                    )
                )
                self.add_item(
                    CustomServiceToggleButton(
                        "moderation_enabled", "Essential Logs", state.moderation, "🧾", 3, guild=guild
                    )
                )

            @discord.ui.button(
                label="Continue Setup",
                emoji="➡️",
                style=discord.ButtonStyle.success,
                custom_id="dank_setup_custom:continue_quick:v6",
                row=1,
            )
            async def continue_guided(
                self,
                interaction: discord.Interaction,
                button: discord.ui.Button,
            ) -> None:
                _ = button
                if not await solid._require_setup_permission(interaction):
                    return
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
                custom_id="dank_setup_custom:plans:v6",
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
                custom_id="dank_setup_custom:home:v6",
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
                custom_id="dank_setup_custom:close:v6",
                row=4,
            )
            async def close(
                self,
                interaction: discord.Interaction,
                button: discord.ui.Button,
            ) -> None:
                _ = button
                await recommend._close_setup(interaction)
        ''',
    )

    replace_node(
        path,
        "_open_custom_service_picker",
        '''
        async def _open_custom_service_picker(
            interaction: discord.Interaction,
            *,
            saved_message: str = "",
        ) -> None:
            if not await solid._require_setup_permission(interaction):
                return
            guild = interaction.guild
            if guild is None:
                return await interaction.response.send_message(
                    "❌ This must be used inside a server.",
                    ephemeral=True,
                )
            state = await _load_custom_state(guild.id)
            state, detected_message = await _autofill_custom_state_from_existing(guild, state)
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
                embed=_custom_services_embed(guild, state, saved_message=message),
                view=CustomServiceModeView(state, guild=guild),
            )
        ''',
    )


def patch_defaults() -> None:
    path = "stoney_verify/commands_ext/public_setup_defaults.py"
    replace_once(
        path,
        "from ..guild_config import get_guild_config, invalidate_guild_config\n",
        "from ..guild_config import get_guild_config, invalidate_guild_config\n"
        "from ..setup_service_state import service_state_from_config\n"
        "from ..services.setup_permission_policy import vc_verification_overwrites\n"
        "from ..services.vc_verification_permissions import reconcile_vc_verification_channel\n",
    )

    replace_node(
        path,
        "_service_scope_from_config",
        '''
        def _service_scope_from_config(cfg: Any) -> dict[str, bool]:
            """Return exactly what Make Missing Things may create."""

            state = service_state_from_config(cfg)
            choice = str(state.setup_choice or "").strip().lower()
            resident_role = _first_config_bool(
                cfg,
                ("verification_resident_role_enabled",),
                default=(choice == "id_voice_check"),
            )
            return {
                "tickets": bool(state.tickets),
                "verify": bool(state.verification_enabled),
                "basic_verify": bool(state.simple_verify),
                "voice": bool(state.voice_verify),
                "id": bool(state.id_verify),
                "spam_guard": bool(state.spam_guard),
                "logs": bool(state.logs),
                "welcome": bool(choice == "basic_server"),
                "resident_role": bool(resident_role),
            }
        ''',
    )

    replace_node(
        path,
        "_voice_overwrites",
        '''
        def _voice_overwrites(
            guild: discord.Guild,
            staff_role: Optional[discord.Role],
            control_role: Optional[discord.Role],
            unverified_role: Optional[discord.Role],
        ) -> dict[Any, discord.PermissionOverwrite]:
            """Canonical session-locked Voice Verify room overwrites."""

            return vc_verification_overwrites(
                guild,
                staff_role=staff_role,
                control_role=control_role,
                unverified_role=unverified_role,
                verified_role=None,
                resident_role=None,
            )
        ''',
    )

    replace_once(
        path,
        '    if services["verify"]:\n        verify_channel = (',
        '    if services["basic_verify"]:\n        verify_channel = (',
    )
    replace_once(
        path,
        '''    if services["verify"]:
        required.extend(
            [
                ("approved-member role", verified_role),
                ("verification channel", verify_channel),
            ]
        )
''',
        '''    if services["verify"]:
        required.append(("approved-member role", verified_role))

    if services["basic_verify"]:
        required.append(("Simple Verify channel", verify_channel))
''',
    )
    replace_once(
        path,
        '''    if services["verify"]:
        updates.update(
            {
                "unverified_role_id": _role_value(unverified_role),
                "verified_role_id": _role_value(verified_role),
                "verify_channel_id": item_id(verify_channel),
                "verification_channel_id": item_id(verify_channel),
            }
        )
''',
        '''    if services["verify"]:
        updates.update(
            {
                "unverified_role_id": _role_value(unverified_role),
                "verified_role_id": _role_value(verified_role),
            }
        )

    if services["basic_verify"]:
        updates.update(
            {
                "verify_channel_id": item_id(verify_channel),
                "verification_channel_id": item_id(verify_channel),
            }
        )
''',
    )
    replace_once(
        path,
        '''        cfg_after = await get_guild_config(
            guild.id,
            refresh=True,
        )
''',
        '''        cfg_after = await get_guild_config(
            guild.id,
            refresh=True,
        )
        if services["voice"] and vc_verify_channel is not None:
            vc_result = await reconcile_vc_verification_channel(
                guild,
                vc_verify_channel,
                cfg=cfg_after,
                reason="Dank Shield setup-defaults Voice Verify reconciliation",
            )
            for label in vc_result.changed:
                _unique(ok, f"Reconciled Voice Verify access for {label}.")
            notes.extend(vc_result.failed)
''',
    )


def patch_recommend() -> None:
    path = "stoney_verify/commands_ext/public_setup_recommend.py"
    replace_once(
        path,
        "from ..setup_engine.verification_modes import id_verify_allowed_for_guild\n",
        "from ..setup_engine.verification_modes import id_verify_allowed_for_guild\n"
        "from ..services.vc_verification_permissions import reconcile_vc_verification_channel\n",
    )

    replace_between_in_node(
        path,
        "_build_plain_setup_health_embed",
        '    if services["verify"]:\n',
        '    if services["voice"]:\n',
        '''
        if services["basic_verify"]:
            if _has_channel(
                guild,
                cfg,
                "verify_channel_id",
                "verification_channel_id",
            ):
                passing.append("The Simple Verify channel is chosen.")
            else:
                blockers.append("Choose where members press Simple Verify.")
        else:
            passing.append(
                "Simple Verify is OFF, so no Simple Verify channel is required."
            )

        if services["verify"]:
            if _has_role(
                guild,
                cfg,
                "verified_role_id",
                "member_role_id",
                "approved_role_id",
            ):
                passing.append("The approved-member role is chosen.")
            else:
                blockers.append("Choose the role members receive after verification.")

            if _has_role(guild, cfg, "unverified_role_id"):
                passing.append("The waiting role is chosen.")
            else:
                warnings.append(
                    "A waiting role is optional, but useful when new members should have limited access."
                )
        else:
            passing.append(
                "Verification is OFF, so verification roles and channels are not required."
            )
        ''',
    )

    replace_between_in_node(
        path,
        "_guided_setup_target",
        '    if services["verify"]:\n',
        '    if services["voice"]:\n',
        '''
        if services["basic_verify"]:
            if not _has_channel(
                guild,
                cfg,
                "verify_channel_id",
                "verification_channel_id",
            ):
                return (
                    "channels",
                    "Choose the Simple Verify Channel",
                    "Pick where members should press Simple Verify.",
                    "verification_channel",
                )

        if services["verify"]:
            if not _has_role(
                guild,
                cfg,
                "verified_role_id",
                "member_role_id",
                "approved_role_id",
            ):
                return (
                    "roles",
                    "Choose the Approved-Member Role",
                    "Pick the role members receive after verification.",
                    "verified_role",
                )
        ''',
    )

    replace_node(
        path,
        "_guided_save_existing_item",
        '''
        async def _guided_save_existing_item(
            interaction: discord.Interaction,
            requirement_key: str,
            item: Any,
        ) -> None:
            if not await solid._require_setup_permission(interaction):
                return

            guild = interaction.guild
            if guild is None:
                return await interaction.response.send_message(
                    "❌ This must be used inside a server.",
                    ephemeral=True,
                )

            await solid._safe_defer_update(interaction)
            if not await _guided_step_is_current(guild, requirement_key):
                return await _open_guided_setup(interaction)

            item_id = int(getattr(item, "id", 0) or 0)
            payload = _guided_item_payload(requirement_key, item_id)
            if item_id <= 0 or not payload:
                embed = _guided_item_embed(requirement_key)
                embed.add_field(
                    name="That did not work",
                    value="I could not read that selection. Choose the item again.",
                    inline=False,
                )
                return await solid._edit_or_followup(
                    interaction,
                    embed=embed,
                    view=GuidedOneItemView(requirement_key=requirement_key),
                )

            await solid._save_config(interaction, payload)
            saved_message = "Saved that item. Moving to the next setup step."
            if requirement_key == "voice_verify_channel":
                cfg = await get_guild_config(guild.id, refresh=True)
                result = await reconcile_vc_verification_channel(
                    guild,
                    item,
                    cfg=cfg,
                    reason="Dank Shield guided Voice Verify channel selection",
                )
                if result.failed:
                    saved_message += "\\n⚠️ " + "\\n⚠️ ".join(result.failed)[:700]

            await _open_guided_setup(interaction, saved_message=saved_message)
        ''',
    )

    replace_first_in_node(
        path,
        "_guided_create_item",
        '''        if payload:
            await solid._save_config(interaction, payload)

        voice_id = int(payload.get("vc_verify_channel_id", "0") or 0)
''',
        '''        if payload:
            await solid._save_config(interaction, payload)

        voice_id = int(payload.get("vc_verify_channel_id", "0") or 0)
        if voice_id > 0:
            cfg_after = await get_guild_config(guild.id, refresh=True)
            voice_channel = guild.get_channel(voice_id)
            if voice_channel is not None:
                result = await reconcile_vc_verification_channel(
                    guild,
                    voice_channel,
                    cfg=cfg_after,
                    reason="Dank Shield guided Voice Verify creation",
                )
                notes.extend(result.failed)
''',
    )


def patch_tests() -> None:
    replace_once(
        "tests/test_setup_service_state_behavior.py",
        '    assert patch["verification_enabled"] is False\n',
        '    assert patch["verification_enabled"] is True\n',
    )
    replace_once(
        "tests/test_setup_020_entitled_id_behavior.py",
        '    assert patch["verification_enabled"] is False\n',
        '    assert patch["verification_enabled"] is True\n',
    )


def remove_temporary_overlay() -> None:
    path = "stoney_verify/startup_guards/vc_setup_one_press_fix.py"
    text = _read(path)
    marker = '''\ntry:\n    from stoney_verify.setup_020_entitled_id_guard import install as _install_ds_setup_020\n\n    _install_ds_setup_020()\nexcept Exception as exc:\n    try:\n        print(f"⚠️ DS-SETUP-020 setup integration failed to install: {type(exc).__name__}: {exc}")\n    except Exception:\n        pass\n'''
    if marker not in text:
        raise RuntimeError("temporary DS-SETUP-020 overlay loader not found")
    _write(path, text.replace(marker, "\n", 1))

    for relative in (
        "stoney_verify/setup_020_entitled_id_guard.py",
        "tools/.ds_setup_020_apply.py.gz.b64",
    ):
        target = ROOT / relative
        if target.exists():
            target.unlink()


def main() -> None:
    patch_service_state()
    patch_fresh_choice()
    patch_defaults()
    patch_recommend()
    patch_tests()
    remove_temporary_overlay()
    print("DS-SETUP-020 direct source integration applied")


if __name__ == "__main__":
    main()
