from __future__ import annotations

import asyncio

"""Plain-language public /dank setup home.

This module patches the hardened setup flow from public_setup_solid.py into a
simple first-run screen. It deliberately avoids developer/product terms.

Public language rules:
- Say Dank Shield, not Dank Shield.
- Use plain labels: Basic server, Help desk, ID check, Voice check,
  ID + voice check, Custom setup.
- No forced forms by default.
- Do not show raw role/channel IDs as public setup instructions.
"""

from typing import Any, Optional

import discord

from ..globals import now_utc
from ..guild_config import get_guild_config
from ..setup_engine.verification_modes import id_verify_allowed_for_guild
from ..setup_new import (
    build_setup_template_embed,
    build_setup_template_select_options,
    get_setup_template,
    setup_template_payload,
)
from . import public_setup_solid as solid

_PATCHED = False


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        value = solid._cfg_value(cfg, key)
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
    return default


def _attr_id(cfg: Any, name: str) -> int:
    try:
        return int(_cfg_value(cfg, name, 0) or 0)
    except Exception:
        return 0


def _saved_choice_text(cfg: Any) -> str:
    label = str(_cfg_value(cfg, "setup_choice_label", "") or "").strip()
    key = str(_cfg_value(cfg, "setup_choice", "") or "").strip()
    if label:
        return f"✅ Saved setup choice: **{label}**"
    if key:
        choice = get_setup_template(key)
        if choice is not None:
            return f"✅ Saved setup choice: **{choice.label}**"
    return "⚠️ No setup choice saved yet. Press **Choose Setup Type** first."


def _plain_lines(lines: list[str], *, empty: str = "✅ Nothing here.", limit: int = 1000) -> str:
    clean = [str(line).strip() for line in lines if str(line).strip()]
    if not clean:
        return empty
    out: list[str] = []
    used = 0
    for line in clean:
        text = line if line.startswith(("•", "✅", "⚠️", "🚫")) else f"• {line}"
        if used + len(text) + 1 > limit:
            out.append(f"…and {len(clean) - len(out)} more")
            break
        out.append(text)
        used += len(text) + 1
    return "\n".join(out)[:limit] or empty


def _has_role(
    guild: discord.Guild,
    cfg: Any,
    *keys: str,
) -> bool:
    for key in keys:
        if guild.get_role(_attr_id(cfg, key)) is not None:
            return True

    return False


def _has_channel(guild: discord.Guild, cfg: Any, *keys: str) -> bool:
    for key in keys:
        if guild.get_channel(_attr_id(cfg, key)) is not None:
            return True
    return False

def _verified_role_voice_access(
    guild: discord.Guild,
    cfg: Any,
) -> tuple[bool, str, tuple[str, ...]]:
    """Check whether approved members can use Voice Verify."""

    role_id = 0

    for key in (
        "verified_role_id",
        "member_role_id",
        "approved_role_id",
    ):
        role_id = _attr_id(cfg, key)

        if role_id > 0:
            break

    channel_id = 0

    for key in (
        "vc_verify_channel_id",
        "vc_verify_vc_id",
        "voice_verify_channel_id",
    ):
        channel_id = _attr_id(cfg, key)

        if channel_id > 0:
            break

    role = guild.get_role(role_id)
    channel = guild.get_channel(channel_id)

    if role is None:
        return (
            False,
            "Choose the approved-member role before "
            "testing Voice Verify.",
            (),
        )

    if not isinstance(channel, discord.VoiceChannel):
        return (
            False,
            "Choose a valid Voice Verify voice channel.",
            (),
        )

    try:
        permissions = channel.permissions_for(role)
    except Exception as exc:
        return (
            False,
            (
                "Dank Shield could not inspect the approved "
                "role's Voice Verify access: "
                f"`{type(exc).__name__}`."
            ),
            (),
        )

    required = (
        ("view_channel", "View Channel"),
        ("connect", "Connect"),
        ("speak", "Speak"),
    )

    missing = tuple(
        label
        for attribute, label in required
        if not bool(
            getattr(
                permissions,
                attribute,
                False,
            )
        )
    )

    if missing:
        return (
            False,
            (
                f"Allow {role.mention} to use "
                f"{channel.mention}: **"
                + ", ".join(missing)
                + "**."
            ),
            missing,
        )

    return (
        True,
        (
            f"{role.mention} can View, Connect, and Speak "
            f"in {channel.mention}."
        ),
        (),
    )


def _setup_choice_label(cfg: Any) -> str:
    label = str(_cfg_value(cfg, "setup_choice_label", "") or "").strip()
    if label:
        return label
    key = str(_cfg_value(cfg, "setup_choice", "") or "").strip()
    choice = get_setup_template(key)
    return choice.label if choice is not None else "Not chosen yet"


def _needs_id_check(cfg: Any) -> bool:
    style = str(_cfg_value(cfg, "verification_panel_style", "") or "").strip()
    if style == "custom":
        return bool(_cfg_value(cfg, "id_verify_enabled", False) or _cfg_value(cfg, "web_verify_enabled", False) or _cfg_value(cfg, "id_web_verify_enabled", False) or _cfg_value(cfg, "verification_requires_id", False))
    return style in {"id_check", "id_voice_check"} or bool(_cfg_value(cfg, "verification_requires_id", False))


def _needs_voice_check(cfg: Any) -> bool:
    style = str(_cfg_value(cfg, "verification_panel_style", "") or "").strip()
    if style == "custom":
        return bool(_cfg_value(cfg, "voice_verification_enabled", False) or _cfg_value(cfg, "vc_verify_enabled", False) or _cfg_value(cfg, "voice_verify_enabled", False) or _cfg_value(cfg, "verification_allows_voice", False))
    return style in {"voice_check", "id_voice_check"} or bool(_cfg_value(cfg, "verification_allows_voice", False))


def _plain_bool(
    value: Any,
    *,
    default: bool = False,
) -> bool:
    if isinstance(value, bool):
        return value

    if value is None:
        return bool(default)

    clean = str(value).strip().lower()

    if clean in {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }:
        return True

    if clean in {
        "0",
        "false",
        "no",
        "off",
        "disabled",
        "",
    }:
        return False

    return bool(default)


def _first_config_bool(
    cfg: Any,
    keys: tuple[str, ...],
    *,
    default: bool,
) -> bool:
    for key in keys:
        value = _cfg_value(cfg, key, None)

        if value is not None:
            return _plain_bool(
                value,
                default=default,
            )

    return bool(default)


def _selected_setup_services(
    cfg: Any,
) -> dict[str, bool]:
    """Return the services this guild actually selected."""

    choice = str(
        _cfg_value(cfg, "setup_choice", "") or ""
    ).strip().lower()

    tickets_default = choice in {
        "basic_server",
        "help_desk",
        "voice_check",
        "id_check",
        "id_voice_check",
    }

    basic_default = choice == "basic_verify"

    voice_default = choice in {
        "voice_check",
        "id_voice_check",
    }

    id_default = choice in {
        "id_check",
        "id_voice_check",
    }

    logs_default = choice in {
        "basic_server",
        "help_desk",
        "voice_check",
        "id_check",
        "id_voice_check",
    }

    tickets = _first_config_bool(
        cfg,
        (
            "tickets_enabled",
            "ticket_service_enabled",
        ),
        default=tickets_default,
    )

    basic_verify = _first_config_bool(
        cfg,
        (
            "basic_verify_enabled",
            "basic_button_verify_enabled",
        ),
        default=basic_default,
    )

    verification_enabled = _first_config_bool(
        cfg,
        ("verification_enabled",),
        default=(
            basic_default
            or voice_default
            or id_default
        ),
    )

    voice = _first_config_bool(
        cfg,
        (
            "voice_verification_enabled",
            "vc_verify_enabled",
            "voice_verify_enabled",
            "verification_allows_voice",
        ),
        default=voice_default,
    )

    id_verify = _first_config_bool(
        cfg,
        (
            "id_verify_enabled",
            "web_verify_enabled",
            "id_web_verify_enabled",
            "verification_requires_id",
        ),
        default=id_default,
    )

    spam_guard = _first_config_bool(
        cfg,
        ("spam_guard_enabled",),
        default=False,
    )

    logs = _first_config_bool(
        cfg,
        (
            "moderation_enabled",
            "logs_enabled",
        ),
        default=logs_default,
    )

    verify = bool(
        basic_verify
        or verification_enabled
        or voice
        or id_verify
    )

    # These workflows depend on tickets and staff logs.
    if voice or id_verify:
        tickets = True
        logs = True

    if spam_guard:
        logs = True

    return {
        "tickets": bool(tickets),
        "verify": bool(verify),
        "basic_verify": bool(basic_verify),
        "voice": bool(voice),
        "id": bool(id_verify),
        "spam_guard": bool(spam_guard),
        "logs": bool(logs),
    }


def _missing_setup_permissions(
    bot_permissions: Any,
    services: dict[str, bool],
) -> list[str]:
    required: list[tuple[str, str]] = [
        ("view_channel", "View Channels"),
        ("send_messages", "Send Messages"),
        ("embed_links", "Embed Links"),
        (
            "read_message_history",
            "Read Message History",
        ),
    ]

    if services["tickets"]:
        required.extend(
            [
                ("manage_channels", "Manage Channels"),
                ("manage_roles", "Manage Roles"),
                ("attach_files", "Attach Files"),
            ]
        )

    if services["verify"]:
        required.append(
            ("manage_roles", "Manage Roles")
        )

    if services["voice"]:
        required.append(
            ("manage_channels", "Manage Channels")
        )

    if services["spam_guard"]:
        required.append(
            ("manage_messages", "Manage Messages")
        )

    missing: list[str] = []
    seen: set[str] = set()

    for attribute, label in required:
        if label in seen:
            continue

        seen.add(label)

        if not bool(
            getattr(
                bot_permissions,
                attribute,
                False,
            )
        ):
            missing.append(label)

    return missing


async def _build_plain_setup_health_embed(
    guild: discord.Guild,
) -> discord.Embed:
    """Check only the services this guild turned on."""

    blockers: list[str] = []
    warnings: list[str] = []
    passing: list[str] = []

    try:
        cfg = await get_guild_config(
            guild.id,
            refresh=True,
        )
    except Exception as exc:
        embed = discord.Embed(
            title="🩺 Setup Check",
            description=(
                "🚫 I could not read this server's saved setup."
            ),
            color=discord.Color.red(),
            timestamp=now_utc(),
        )
        embed.add_field(
            name="Try this",
            value=(
                "Wait a moment and try again.\n"
                f"Error: `{type(exc).__name__}`"
            ),
            inline=False,
        )
        return embed

    setup_choice = str(
        _cfg_value(cfg, "setup_choice", "") or ""
    ).strip()

    choice_label = _setup_choice_label(cfg)
    services = _selected_setup_services(cfg)

    any_service = any(
        (
            services["tickets"],
            services["verify"],
            services["spam_guard"],
            services["logs"],
        )
    )

    if setup_choice:
        passing.append(
            f"Setup type chosen: **{choice_label}**"
        )
    else:
        blockers.append(
            "Choose what this server should use."
        )

    if setup_choice and not any_service:
        blockers.append(
            "Turn on at least one feature under "
            "**Advanced Options → Features On / Off**."
        )

    bot_member = getattr(guild, "me", None)
    bot_permissions = getattr(
        bot_member,
        "guild_permissions",
        None,
    )

    missing_permissions = _missing_setup_permissions(
        bot_permissions,
        services,
    )

    if missing_permissions:
        blockers.append(
            "Give Dank Shield these permissions: **"
            + ", ".join(missing_permissions)
            + "**."
        )
    else:
        passing.append(
            "Dank Shield has the permissions needed by "
            "the enabled features."
        )

    if services["tickets"]:
        if _has_role(
            guild,
            cfg,
            "staff_role_id",
        ):
            passing.append(
                "The ticket staff role is chosen."
            )
        else:
            blockers.append(
                "Choose which role answers tickets."
            )

        if _has_channel(
            guild,
            cfg,
            "ticket_category_id",
        ):
            passing.append(
                "The new-ticket folder is chosen."
            )
        else:
            blockers.append(
                "Choose where new tickets should open."
            )

        if _has_channel(
            guild,
            cfg,
            "ticket_archive_category_id",
            "archive_category_id",
        ):
            passing.append(
                "The closed-ticket folder is chosen."
            )
        else:
            warnings.append(
                "A closed-ticket folder is optional, but keeps "
                "finished tickets away from open ones."
            )

        if _has_channel(
            guild,
            cfg,
            "transcripts_channel_id",
        ):
            passing.append(
                "The transcript channel is chosen."
            )
        else:
            warnings.append(
                "Choose a transcript channel later if staff "
                "should receive saved ticket history."
            )

        if _has_channel(
            guild,
            cfg,
            "ticket_panel_channel_id",
            "support_channel_id",
        ):
            passing.append(
                "The ticket-panel channel is chosen."
            )
        else:
            warnings.append(
                "Choose where the Create Ticket panel should "
                "be posted before launch."
            )

        try:
            category_load = await solid._category_load(
                guild
            )

            if category_load.error:
                blockers.append(
                    "Ticket choices could not be checked. "
                    "Open **Advanced Options → Ticket Choices**."
                )
            elif category_load.rows:
                passing.append(
                    f"The ticket menu has "
                    f"{len(category_load.rows)} choice(s)."
                )
            else:
                blockers.append(
                    "Create at least one ticket choice."
                )
        except Exception:
            warnings.append(
                "Ticket choices could not be checked right now."
            )
    else:
        passing.append(
            "Tickets are OFF, so ticket roles, folders, "
            "transcripts, and menu choices are not required."
        )

    if services["verify"]:
        if _has_channel(
            guild,
            cfg,
            "verify_channel_id",
            "verification_channel_id",
        ):
            passing.append(
                "The verification channel is chosen."
            )
        else:
            blockers.append(
                "Choose where members press Verify."
            )

        if _has_role(
            guild,
            cfg,
            "verified_role_id",
            "member_role_id",
            "approved_role_id",
        ):
            passing.append(
                "The approved-member role is chosen."
            )
        else:
            blockers.append(
                "Choose the role members receive after "
                "verification."
            )

        if _has_role(
            guild,
            cfg,
            "unverified_role_id",
        ):
            passing.append(
                "The waiting role is chosen."
            )
        else:
            warnings.append(
                "A waiting role is optional, but useful when "
                "new members should have limited access."
            )
    else:
        passing.append(
            "Verification is OFF, so verification roles and "
            "channels are not required."
        )

    if services["voice"]:
        if _has_channel(
            guild,
            cfg,
            "vc_verify_channel_id",
            "vc_verify_vc_id",
            "voice_verify_channel_id",
        ):
            passing.append(
                "The Voice Verify channel is chosen."
            )
        else:
            blockers.append(
                "Choose the voice channel used for Voice Verify."
            )

        if _has_channel(
            guild,
            cfg,
            "vc_verify_queue_channel_id",
            "vc_queue_channel_id",
            "vc_request_channel_id",
            "vc_verify_requests_channel_id",
        ):
            passing.append(
                "The staff Voice Verify request channel "
                "is chosen."
            )
        else:
            blockers.append(
                "Choose where staff receive Voice Verify "
                "requests."
            )

        if (
            _has_role(
                guild,
                cfg,
                "verified_role_id",
                "member_role_id",
                "approved_role_id",
            )
            and _has_channel(
                guild,
                cfg,
                "vc_verify_channel_id",
                "vc_verify_vc_id",
                "voice_verify_channel_id",
            )
        ):
            access_ok, access_text, _missing_access = (
                _verified_role_voice_access(
                    guild,
                    cfg,
                )
            )

            if access_ok:
                passing.append(access_text)
            else:
                blockers.append(access_text)

    if (
        services["id"]
        and not id_verify_allowed_for_guild(guild)
    ):
        blockers.append(
            "ID/Web Verify is not available for this server. "
            "Choose Basic Verify or Voice Verify instead."
        )

    if services["logs"]:
        if _has_channel(
            guild,
            cfg,
            "modlog_channel_id",
            "raidlog_channel_id",
        ):
            passing.append(
                "The moderation/security log channel is chosen."
            )
        else:
            blockers.append(
                "Choose where moderation and security logs go."
            )
    else:
        passing.append(
            "Logs are OFF, so a log channel is not required."
        )

    control_role_keys = (
        "server_control_role_id",
        "control_role_id",
        "perm_role_id",
    )

    has_saved_control_id = any(
        _attr_id(cfg, key) > 0
        for key in control_role_keys
    )

    has_control_role = any(
        guild.get_role(_attr_id(cfg, key)) is not None
        for key in control_role_keys
    )

    if has_saved_control_id and not has_control_role:
        warnings.append(
            "An old setup-admin role no longer exists. "
            "Choose another later or leave it unused."
        )
    elif has_control_role:
        passing.append(
            "The optional setup-admin role is chosen."
        )

    ready = not blockers

    embed = discord.Embed(
        title="🩺 Setup Check",
        description=(
            "✅ **Ready to test the features you turned on.**"
            if ready
            else "🚫 **Finish the required items below first.**"
        ),
        color=(
            discord.Color.green()
            if ready
            else discord.Color.red()
        ),
        timestamp=now_utc(),
    )

    embed.add_field(
        name="Your Setup",
        value=(
            f"**{choice_label}**\n"
            f"Tickets: `{'ON' if services['tickets'] else 'OFF'}` • "
            f"Basic Verify: `{'ON' if services['basic_verify'] else 'OFF'}`\n"
            f"Voice Verify: `{'ON' if services['voice'] else 'OFF'}` • "
            f"SpamGuard: `{'ON' if services['spam_guard'] else 'OFF'}` • "
            f"Logs: `{'ON' if services['logs'] else 'OFF'}`"
        )[:1024],
        inline=False,
    )

    embed.add_field(
        name="Fix These First",
        value=_plain_lines(
            blockers,
            empty="✅ Nothing required is missing.",
        ),
        inline=False,
    )

    embed.add_field(
        name="Already Good",
        value=_plain_lines(
            passing,
            empty="No completed checks yet.",
        ),
        inline=False,
    )

    embed.add_field(
        name="Optional Later",
        value=_plain_lines(
            warnings,
            empty="✅ No optional reminders.",
        ),
        inline=False,
    )

    embed.add_field(
        name="What to press",
        value=(
            "Press **Start / Continue Setup** to finish "
            "anything required.\n"
            "Press **Test / Launch** after this page says ready."
        ),
        inline=False,
    )

    embed.set_footer(
        text=(
            f"Guild {guild.id} • only enabled features "
            "are checked"
        )
    )

    return embed


def _build_setup_help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="❓ Dank Shield Setup Help",
        description="Simple answers for the setup screen. No technical terms needed.",
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name="What should I press first?", value="Press **Choose Setup Type**. Pick the option closest to your server. You can change it later.", inline=False)
    embed.add_field(name="What if I already made my roles/channels?", value="Press **Use My Existing Server**. Then pick your existing roles and channels from Discord menus.", inline=False)
    embed.add_field(name="What if I do not have roles/channels yet?", value="Press **Create Missing Items**. Dank Shield creates missing basics only. It does not delete your server setup.", inline=False)
    embed.add_field(name="What is ID + voice check?", value="That is the upload-link plus voice-check style like your current legacy single-server setup, but without hardcoded server names, role IDs, or channel IDs.", inline=False)
    embed.add_field(name="What if setup says owner/admin role is missing?", value="That is optional. It came from older server-specific setup. Pick a new owner/admin role only if you want that feature.", inline=False)
    embed.add_field(name="Will this force forms on members?", value="No. Ticket flow stays fast by default. Forms are optional only.", inline=False)
    embed.add_field(name="Will this copy legacy single-server settings to other servers?", value="No. Every server saves its own setup. No legacy single-server IDs or branding should be used for other guilds.", inline=False)
    return embed


async def _setup_progress(
    guild: discord.Guild,
) -> tuple[str, int, int, str]:
    """Return a feature-aware four-value progress tuple."""

    done = 0
    total = 0
    lines: list[str] = []
    default_next = "Choose what this server should use."
    next_step = default_next

    try:
        cfg = await get_guild_config(
            guild.id,
            refresh=True,
        )
    except Exception as exc:
        return (
            f"🚫 Saved setup could not load: "
            f"`{type(exc).__name__}`",
            0,
            1,
            "Try again in a moment.",
        )

    def check(
        label: str,
        okay: bool,
        fail_hint: str,
    ) -> None:
        nonlocal done, total, next_step

        total += 1

        if okay:
            done += 1
            lines.append(f"✅ {label}")
            return

        lines.append(
            f"⚠️ {label}: {fail_hint}"
        )

        if next_step == default_next:
            next_step = fail_hint

    setup_choice = str(
        _cfg_value(cfg, "setup_choice", "") or ""
    ).strip()

    check(
        "Setup type",
        bool(setup_choice),
        "Press Start / Continue Setup and choose a type.",
    )

    if not setup_choice:
        return (
            "\n".join(lines)[:1024],
            done,
            total,
            next_step,
        )

    services = _selected_setup_services(cfg)

    any_service = any(
        (
            services["tickets"],
            services["verify"],
            services["spam_guard"],
            services["logs"],
        )
    )

    check(
        "At least one feature",
        any_service,
        "Open Manage Setup → Features On / Off.",
    )

    bot_member = getattr(guild, "me", None)
    bot_permissions = getattr(
        bot_member,
        "guild_permissions",
        None,
    )

    missing_permissions = _missing_setup_permissions(
        bot_permissions,
        services,
    )

    check(
        "Bot permissions",
        not missing_permissions,
        (
            "Give Dank Shield: "
            + ", ".join(missing_permissions)
            if missing_permissions
            else "Check the bot permissions."
        ),
    )

    if services["tickets"]:
        check(
            "Ticket staff role",
            _has_role(
                guild,
                cfg,
                "staff_role_id",
            ),
            "Use Things I Already Made → ticket staff role.",
        )

        check(
            "New-ticket folder",
            _has_channel(
                guild,
                cfg,
                "ticket_category_id",
            ),
            "Use Things I Already Made → new-ticket folder.",
        )

        try:
            category_load = await solid._category_load(
                guild
            )

            total += 1

            if category_load.error:
                lines.append(
                    "🚫 Ticket choices could not be checked."
                )

                if next_step == default_next:
                    next_step = (
                        "Open Manage Setup → Ticket Choices."
                    )
            elif category_load.rows:
                done += 1
                lines.append(
                    f"✅ Ticket choices: "
                    f"{len(category_load.rows)} configured"
                )
            else:
                lines.append(
                    "⚠️ Ticket choices: none configured"
                )

                if next_step == default_next:
                    next_step = (
                        "Open Manage Setup → Ticket Choices."
                    )
        except Exception:
            total += 1
            lines.append(
                "⚠️ Ticket choices could not be checked."
            )

            if next_step == default_next:
                next_step = (
                    "Open Manage Setup → Ticket Choices."
                )

    if services["verify"]:
        check(
            "Verification channel",
            _has_channel(
                guild,
                cfg,
                "verify_channel_id",
                "verification_channel_id",
            ),
            "Use Things I Already Made → verification channel.",
        )

        check(
            "Approved-member role",
            _has_role(
                guild,
                cfg,
                "verified_role_id",
            ),
            "Use Things I Already Made → approved-member role.",
        )

    if services["voice"]:
        check(
            "Voice Verify channel",
            _has_channel(
                guild,
                cfg,
                "vc_verify_channel_id",
            ),
            "Choose the Voice Verify channel.",
        )

        check(
            "Voice Verify staff requests",
            _has_channel(
                guild,
                cfg,
                "vc_verify_queue_channel_id",
                "vc_queue_channel_id",
                "vc_request_channel_id",
                "vc_verify_requests_channel_id",
            ),
            "Choose where staff receive Voice Verify requests.",
        )

    if services["id"]:
        check(
            "ID/Web Verify permission",
            id_verify_allowed_for_guild(guild),
            "Choose Basic Verify or Voice Verify instead.",
        )

    if services["logs"]:
        check(
            "Moderation/security logs",
            _has_channel(
                guild,
                cfg,
                "modlog_channel_id",
                "raidlog_channel_id",
            ),
            "Use Things I Already Made → Logs + Status.",
        )

    if total and done == total:
        next_step = (
            "Press Test / Launch and test with an alt account."
        )

    return (
        "\n".join(lines)[:1024],
        done,
        total,
        next_step,
    )


async def _product_main_setup_payload(guild: discord.Guild) -> tuple[discord.Embed, discord.ui.View]:
    progress_text, done, total, next_step = await _setup_progress(guild)
    try:
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception:
        cfg = None

    saved_choice = _saved_choice_text(cfg) if cfg is not None else "⚠️ Saved setup could not be read."
    ready = bool(total and done >= total)
    started = bool(cfg is not None and str(_cfg_value(cfg, "setup_choice", "") or "").strip())

    issues = [
        line.strip()
        for line in str(progress_text or "").splitlines()
        if line.strip().startswith(("⚠️", "🚫", "❌"))
    ][:3]

    if not started:
        status = "Not started"
        recommended = "Press **Start / Continue Setup** and choose the setup type."
    elif ready:
        status = "Ready to test"
        recommended = "Press **Test / Launch** and test with an alt account."
    else:
        status = "Needs setup work"
        recommended = str(next_step or "Press Start / Continue Setup.")[:350]

    embed = discord.Embed(
        title="🚀 Dank Shield Setup",
        description="One screen. One next step. Everything else is tucked under Manage Setup.",
        color=discord.Color.green() if ready else discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(
        name="Status",
        value=(
            f"**{status}**\n"
            f"{saved_choice}\n"
            f"`{done}/{total}` setup checks complete"
        )[:1024],
        inline=False,
    )
    embed.add_field(name="Recommended Next Step", value=recommended[:1024], inline=False)
    embed.add_field(
        name="Needs Attention",
        value="\n".join(issues)[:900] if issues else "✅ No required setup problem shown here. Run **Setup Check** for the full truth check.",
        inline=False,
    )
    embed.set_footer(text=f"Guild {guild.id} • /dank setup • simple home")
    return embed, ProductSetupHomeView(ready=ready, started=started)

class SetupChoiceSelect(discord.ui.Select):
    def __init__(self, selected_key: Optional[str] = None) -> None:
        super().__init__(
            placeholder="Choose what this server needs…",
            min_values=1,
            max_values=1,
            options=build_setup_template_select_options(),
            row=0,
        )
        if selected_key:
            for option in self.options:
                option.default = option.value == selected_key

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        selected = str(self.values[0]) if self.values else ""
        view = self.view
        if isinstance(view, SetupChoiceView):
            view.selected_key = selected
        guild_name = getattr(getattr(interaction, "guild", None), "name", "this server")
        embed = build_setup_template_embed(selected_key=selected, guild_name=str(guild_name or "this server"))
        embed.add_field(
            name="Next",
            value="Press **Use This Setup** to save this choice, or pick another option from the menu.",
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=view)


class SetupChoiceView(solid.BackToSetupView):
    def __init__(self, *, selected_key: Optional[str] = None) -> None:
        super().__init__()
        self.selected_key = selected_key
        self.add_item(SetupChoiceSelect(selected_key=selected_key))

    @discord.ui.button(label="Use This Setup", emoji="✅", style=discord.ButtonStyle.success, custom_id="dank_setup_choice:publish", row=1)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

        selected = str(self.selected_key or "").strip()
        choice = get_setup_template(selected)
        if choice is None:
            return await interaction.response.send_message("Pick a setup type from the menu first.", ephemeral=True)

        await solid._safe_defer_update(interaction)
        payload = setup_template_payload(selected)
        payload.update(
            {
                "setup_choice_selected_at": solid._utc_iso(),
                "setup_choice_selected_by_id": str(interaction.user.id),
                "setup_choice_selected_by_name": str(interaction.user),
            }
        )
        await solid._save_config(interaction, payload)

        if selected == "custom_setup":
            try:
                from . import public_setup_fresh_choice
                return await public_setup_fresh_choice._open_custom_service_picker(
                    interaction,
                    saved_message=(
                        "Saved **Custom setup**. Now turn each service on/off below. "
                        "This is the actual manual editor."
                    ),
                )
            except Exception as e:
                embed = discord.Embed(
                    title="✅ Custom Setup Saved",
                    description=(
                        "Saved **Custom setup**, but the manual service editor did not open.\n\n"
                        f"Error: `{type(e).__name__}: {str(e)[:220]}`\n\n"
                        "Nothing else was changed. Use **Use My Existing Server** while this is repaired."
                    ),
                    color=discord.Color.orange(),
                    timestamp=now_utc(),
                )
                return await solid._edit_or_followup(interaction, embed=embed, view=ProductSetupHomeView())

        embed = build_setup_template_embed(selected_key=selected, guild_name=str(guild.name))
        embed.title = "✅ Setup Choice Saved"
        embed.description = (
            f"Saved **{choice.label}** for this server.\n\n"
            "Next, choose your existing roles/channels or create missing basics."
        )
        embed.add_field(
            name="Next step",
            value=(
                "• Press **Use My Existing Server** if your roles/channels already exist.\n"
                "• Press **Create Missing Items** if you want Dank Shield to create missing basics.\n"
                "• Press **Health Check** when you think setup is ready."
            ),
            inline=False,
        )
        await solid._edit_or_followup(interaction, embed=embed, view=ProductSetupHomeView())

    @discord.ui.button(label="Preview Only", emoji="👀", style=discord.ButtonStyle.secondary, custom_id="dank_setup_choice:preview", row=1)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        selected = str(self.selected_key or "").strip()
        guild_name = getattr(getattr(interaction, "guild", None), "name", "this server")
        embed = build_setup_template_embed(selected_key=selected, guild_name=str(guild_name or "this server"))
        embed.add_field(name="Preview only", value="Nothing has been saved yet.", inline=False)
        await interaction.response.edit_message(embed=embed, view=self)



class SetupReviewFixNextButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Fix Next Item",
            emoji="➡️",
            style=discord.ButtonStyle.success,
            custom_id="dank_setup_review:fix_next",
            row=0,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await solid._require_setup_permission(
            interaction
        ):
            return

        guild = interaction.guild

        if guild is None:
            return await interaction.response.send_message(
                "❌ This must be used inside a server.",
                ephemeral=True,
            )

        target, _title, _explanation, requirement_key = (
            await _guided_setup_target(guild)
        )

        if target == "ready":
            return await _open_health_check(interaction)

        await _open_guided_target(
            interaction,
            target,
            requirement_key,
        )


class SetupReviewLaunchButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Test / Launch",
            emoji="🧪",
            style=discord.ButtonStyle.success,
            custom_id="dank_setup_review:launch",
            row=0,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await _open_test_launch(interaction)


class SetupReviewAdvancedButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Advanced Options",
            emoji="⚙️",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_setup_review:advanced",
            row=1,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await _open_manage_setup(interaction)


class SetupReviewChangeTypeButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Change Setup Type",
            emoji="🧭",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_setup_review:change_type",
            row=1,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await _open_choose_setup_type(interaction)


class SetupReviewHelpButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Help / FAQ",
            emoji="❓",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_setup_review:help",
            row=2,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await solid._require_setup_permission(
            interaction
        ):
            return

        await interaction.response.edit_message(
            embed=_build_setup_help_embed(),
            view=solid.BackToSetupView(),
        )


class SetupReviewHomeButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Back Home",
            emoji="🏠",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_setup_review:home",
            row=2,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await _home_edit(interaction)


class SetupReviewView(discord.ui.View):
    """Show one correct main action after Setup Check."""

    def __init__(self, *, ready: bool) -> None:
        super().__init__(timeout=900)

        if ready:
            self.add_item(SetupReviewLaunchButton())
        else:
            self.add_item(SetupReviewFixNextButton())

        self.add_item(SetupReviewAdvancedButton())
        self.add_item(SetupReviewChangeTypeButton())
        self.add_item(SetupReviewHelpButton())
        self.add_item(SetupReviewHomeButton())


class SetupHealthHelpView(solid.BackToSetupView):
    @discord.ui.button(label="Help / FAQ", emoji="❓", style=discord.ButtonStyle.primary, custom_id="dank_setup:help_from_health", row=0)
    async def help_faq(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        await interaction.response.edit_message(embed=_build_setup_help_embed(), view=solid.BackToSetupView())


async def _home_edit(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    await solid._safe_defer_update(interaction)
    embed, view = await _product_main_setup_payload(guild)
    await solid._edit_or_followup(interaction, embed=embed, view=view)


async def _open_choose_setup_type(
    interaction: discord.Interaction,
) -> None:
    if not await solid._require_setup_permission(interaction):
        return

    guild = interaction.guild

    if guild is None:
        return await interaction.response.send_message(
            "❌ This must be used inside a server.",
            ephemeral=True,
        )

    if not interaction.response.is_done():
        await solid._safe_defer_update(interaction)

    # Use the newer guild-safe choices. This includes Basic Verify
    # and hides ID/Web verification unless this guild is allowed.
    from . import public_setup_fresh_choice as fresh

    choices = fresh._choices_for_guild(guild)

    embed = discord.Embed(
        title="🧭 What Should Dank Shield Do?",
        description=(
            "Pick the closest match. You can change it later.\n\n"
            "**Choose one setup type:**\n"
            "🏠 Basic Server — tickets and normal server tools\n"
            "✅ Basic Verify — one simple Verify button\n"
            "🎫 Help Desk — support tickets\n"
            "🎙️ Voice Verify — verification with a staff voice check\n"
            "⚙️ Custom — choose features yourself"
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )

    for choice in choices:
        embed.add_field(
            name=f"{choice.emoji} {choice.label}",
            value=(
                f"{choice.short}\n"
                f"Members see: {choice.member_sees}"
            )[:1024],
            inline=False,
        )

    if not fresh.id_verify_allowed_for_guild(guild):
        embed.add_field(
            name="🔒 ID Verification",
            value=(
                "ID/Web choices are hidden because this server "
                "has not been specifically allowed to use them."
            ),
            inline=False,
        )

    embed.set_footer(
        text="Press one choice below. Nothing else is deleted."
    )

    await solid._edit_or_followup(
        interaction,
        embed=embed,
        view=fresh.SetupTypeChoiceView(guild=guild),
    )


async def _open_existing_server(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    embed = discord.Embed(
        title="🧭 Use Existing Roles / Channels",
        description="Map the roles, channels, and folders your server already has. Names do not matter; Dank Shield saves Discord IDs.",
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(
        name="Recommended order",
        value="1. Ticket Basics\n2. Access Roles\n3. Verification Channels\n4. Logs + Status\n5. Behavior Settings",
        inline=False,
    )
    await interaction.response.edit_message(embed=embed, view=solid.ChooseExistingView())


async def _open_create_missing(
    interaction: discord.Interaction,
) -> None:
    if not await solid._require_setup_permission(interaction):
        return

    guild = interaction.guild

    if guild is None:
        return await interaction.response.send_message(
            "❌ This must be used inside a server.",
            ephemeral=True,
        )

    try:
        cfg = await get_guild_config(
            guild.id,
            refresh=True,
        )
        services = _selected_setup_services(cfg)

        from . import public_setup_defaults

        handled = await (
            public_setup_defaults._setup_defaults_callback(
                interaction
            )
        )

        if handled is not True:
            return

        # Ticket choices belong only to servers with Tickets ON.
        if not services["tickets"]:
            return

        created, skipped, error = (
            await solid._seed_recommended_categories(guild)
        )

        if error:
            await interaction.followup.send(
                (
                    "⚠️ The server items were handled, but ticket "
                    f"choices could not be checked: `{error}`"
                ),
                ephemeral=True,
            )
        elif created:
            await interaction.followup.send(
                (
                    "✅ Added ticket choices: "
                    + ", ".join(
                        f"`{name}`"
                        for name in created
                    )
                ),
                ephemeral=True,
            )
        elif skipped:
            await interaction.followup.send(
                "✅ Ticket choices already existed.",
                ephemeral=True,
            )

    except Exception as exc:
        message = (
            "❌ Make Missing Things failed: "
            f"`{type(exc).__name__}: {str(exc)[:250]}`"
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


async def _open_ticket_menu(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    await solid._safe_defer_update(interaction)
    embed, view = await solid._build_category_manager_payload(guild)
    await solid._edit_or_followup(interaction, embed=embed, view=view)


async def _open_services(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    await solid._safe_defer_update(interaction)
    try:
        from . import public_setup_fresh_choice
        return await public_setup_fresh_choice._open_custom_service_picker(
            interaction,
            saved_message="Service switches opened. Turn each feature ON/OFF here.",
        )
    except Exception as e:
        embed = discord.Embed(
            title="Service Switches Did Not Open",
            description=f"Error: `{type(e).__name__}: {str(e)[:220]}`",
            color=discord.Color.orange(),
            timestamp=now_utc(),
        )
        await solid._edit_or_followup(interaction, embed=embed, view=ManageSetupView())


async def _open_health_check(
    interaction: discord.Interaction,
    *,
    saved_message: str = "",
    already_deferred: bool = False,
) -> None:
    """Open the feature-aware review with one correct action."""

    if not await solid._require_setup_permission(interaction):
        return

    guild = interaction.guild

    if guild is None:
        return await interaction.response.send_message(
            "❌ This must be used inside a server.",
            ephemeral=True,
        )

    if not already_deferred:
        await solid._safe_defer_update(interaction)

    embed = await _build_plain_setup_health_embed(guild)

    target, _title, _explanation, _requirement_key = (
        await _guided_setup_target(guild)
    )
    ready = target == "ready"

    if saved_message:
        embed.add_field(
            name="Last Step Finished",
            value=saved_message[:1024],
            inline=False,
        )

    await solid._edit_or_followup(
        interaction,
        embed=embed,
        view=SetupReviewView(ready=ready),
    )




async def _open_permission_repair(
    interaction: discord.Interaction,
) -> None:
    """Open the owned permission-repair preview safely."""

    if not await solid._require_setup_permission(
        interaction
    ):
        return

    if interaction.guild is None:
        return await interaction.response.send_message(
            "❌ This must be used inside a server.",
            ephemeral=True,
        )

    from stoney_verify import (
        setup_permission_repair_services,
    )

    await setup_permission_repair_services.open_permission_repair(
        interaction
    )


async def _open_recovery_center(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    await solid._safe_defer_update(interaction)
    try:
        from . import public_setup_cleanup
        from . import public_setup_recovery

        embed_builder = getattr(public_setup_cleanup, "patched_recovery_embed", None) or getattr(public_setup_recovery, "_build_recovery_embed")
        view_cls = getattr(public_setup_cleanup, "PatchedRecoveryCenterView", None) or getattr(public_setup_recovery, "RecoveryCenterView")
        embed = await embed_builder(guild)
        await solid._edit_or_followup(interaction, embed=embed, view=view_cls())
    except Exception as e:
        embed = discord.Embed(
            title="Recovery Center Did Not Open",
            description=f"Error: `{type(e).__name__}: {str(e)[:220]}`",
            color=discord.Color.orange(),
            timestamp=now_utc(),
        )
        await solid._edit_or_followup(interaction, embed=embed, view=ManageSetupView())


async def _open_protection_options(
    interaction: discord.Interaction,
) -> None:
    """Open the existing guild-scoped Protection Center."""

    if not await solid._require_setup_permission(interaction):
        return

    from . import public_protection_center

    await public_protection_center._refresh_panel(
        interaction,
        content=(
            "🛡️ Protection opened from "
            "**Advanced Options**."
        ),
    )


async def _open_timers_behavior(
    interaction: discord.Interaction,
) -> None:
    """Open the existing behavior and verification timer tools."""

    if not await solid._require_setup_permission(interaction):
        return

    guild = interaction.guild

    if guild is None:
        return await interaction.response.send_message(
            "❌ This must be used inside a server.",
            ephemeral=True,
        )

    embed = discord.Embed(
        title="⏱️ Timers & Behavior",
        description=(
            "Change verification timers, ticket naming, "
            "verification style, and other server behavior. "
            "Nothing here deletes roles or channels."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )

    await solid._add_saved_setup_section(
        embed,
        guild,
        "behavior",
    )

    await solid._edit_or_followup(
        interaction,
        embed=embed,
        view=solid.BehaviorSettingsView(),
    )


async def _open_modlog_tracking(
    interaction: discord.Interaction,
) -> None:
    """Open the guild-scoped Modlog Tracking service."""

    from stoney_verify import modlog_tracking_service

    await modlog_tracking_service.open_modlog_tracking(
        interaction
    )


def _advanced_section_embed(
    *,
    title: str,
    description: str,
    items: tuple[str, ...],
    danger: bool = False,
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=(
            discord.Color.red()
            if danger
            else discord.Color.blurple()
        ),
        timestamp=now_utc(),
    )
    embed.add_field(
        name="In This Section",
        value="\n".join(items)[:1024],
        inline=False,
    )
    embed.set_footer(
        text=(
            "Advanced Options • Back to Advanced returns to the "
            "grouped menu"
        )
    )
    return embed


async def _open_advanced_section(
    interaction: discord.Interaction,
    *,
    title: str,
    description: str,
    items: tuple[str, ...],
    view: discord.ui.View,
    danger: bool = False,
) -> None:
    if not await solid._require_setup_permission(interaction):
        return

    await solid._edit_or_followup(
        interaction,
        embed=_advanced_section_embed(
            title=title,
            description=description,
            items=items,
            danger=danger,
        ),
        view=view,
    )


async def _open_advanced_core_setup(
    interaction: discord.Interaction,
) -> None:
    await _open_advanced_section(
        interaction,
        title="🧰 Core Setup",
        description=(
            "Change the main setup behavior without digging through "
            "unrelated tools."
        ),
        items=(
            "🧩 **Features On / Off** — choose which services run.",
            "⏱️ **Timers & Behavior** — timers, naming, and flow settings.",
            "🧭 **Detailed Role / Channel Mapping** — deliberately remap saved items.",
        ),
        view=AdvancedCoreSetupView(),
    )


async def _open_advanced_member_experience(
    interaction: discord.Interaction,
) -> None:
    await _open_advanced_section(
        interaction,
        title="👥 Member Experience",
        description=(
            "Adjust what members interact with during tickets and "
            "server protection flows."
        ),
        items=(
            "🧾 **Ticket Choices** — edit what members can request.",
            "🛡️ **Protection** — open the Protection Center.",
        ),
        view=AdvancedMemberExperienceView(),
    )


async def _open_advanced_monitoring_repair(
    interaction: discord.Interaction,
) -> None:
    await _open_advanced_section(
        interaction,
        title="🧰 Monitoring & Repair",
        description=(
            "Manage server event tracking and repair saved setup "
            "permissions."
        ),
        items=(
            "🧾 **Modlog Tracking** — choose which server events are recorded.",
            "🛠️ **Permission Repair** — preview and repair saved setup channel permissions.",
        ),
        view=AdvancedMonitoringRepairView(),
    )


async def _open_advanced_appearance(
    interaction: discord.Interaction,
) -> None:
    await _open_advanced_section(
        interaction,
        title="🎨 Appearance",
        description=(
            "Open the visual design tools without mixing them into "
            "normal server setup."
        ),
        items=(
            "🎨 **Server Design** — fonts, frames, emojis, preview, and rollback.",
        ),
        view=AdvancedAppearanceView(),
    )


async def _open_advanced_danger_zone(
    interaction: discord.Interaction,
) -> None:
    await _open_advanced_section(
        interaction,
        title="🚨 Danger Zone",
        description=(
            "Use this only when you deliberately want to recover or "
            "start setup over. Normal setup tools are kept out of "
            "this section."
        ),
        items=(
            "🧯 **Recovery / Start Over** — safely reset or recover setup.",
        ),
        view=AdvancedDangerZoneView(),
        danger=True,
    )


async def _open_manage_setup(
    interaction: discord.Interaction,
) -> None:
    """Open the grouped Advanced Options hub."""

    if not await solid._require_setup_permission(interaction):
        return

    embed = discord.Embed(
        title="⚙️ Advanced Options",
        description=(
            "Choose one group. Regular setup should usually be done "
            "from **Start / Continue Setup**."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(
        name="🧰 Core Setup",
        value=(
            "Features, timers, behavior, and detailed role/channel "
            "mapping."
        ),
        inline=False,
    )
    embed.add_field(
        name="👥 Member Experience",
        value="Ticket choices and Protection Center settings.",
        inline=False,
    )
    embed.add_field(
        name="🧰 Monitoring & Repair",
        value="Modlog tracking and permission repair.",
        inline=False,
    )
    embed.add_field(
        name="🎨 Appearance",
        value="Server Design, preview, and rollback tools.",
        inline=False,
    )
    embed.add_field(
        name="🚨 Danger Zone",
        value=(
            "Recovery / Start Over is isolated here so it is never "
            "mixed with normal setup actions."
        ),
        inline=False,
    )
    embed.add_field(
        name="Simple Setup",
        value=(
            "Use **Back Home → Start / Continue Setup** for the "
            "one-item-at-a-time guided route."
        ),
        inline=False,
    )

    await solid._edit_or_followup(
        interaction,
        embed=embed,
        view=ManageSetupView(),
    )



def _setup_bool(value: Any, default: bool = False) -> bool:
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


async def _launch_state(guild: discord.Guild) -> dict[str, bool]:
    try:
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception:
        cfg = None

    return {
        "tickets": _setup_bool(_cfg_value(cfg, "tickets_enabled", True), True),
        "basic_verify": _setup_bool(
            _cfg_value(cfg, "basic_verify_enabled", _cfg_value(cfg, "basic_button_verify_enabled", _cfg_value(cfg, "verification_enabled", False))),
            False,
        ),
        "voice_verify": _setup_bool(
            _cfg_value(cfg, "voice_verification_enabled", _cfg_value(cfg, "vc_verify_enabled", _cfg_value(cfg, "verification_allows_voice", False))),
            False,
        ),
        "id_verify": _setup_bool(
            _cfg_value(cfg, "id_verify_enabled", _cfg_value(cfg, "web_verify_enabled", _cfg_value(cfg, "id_web_verify_enabled", _cfg_value(cfg, "verification_requires_id", False)))),
            False,
        ),
        "logs": _setup_bool(_cfg_value(cfg, "logs_enabled", _cfg_value(cfg, "moderation_enabled", False)), False),
    }


def _launch_state_text(state: dict[str, bool]) -> str:
    return (
        f"🎫 Tickets: **{'ON ✅' if state.get('tickets') else 'OFF ⬜'}**\n"
        f"✅ Basic Verify: **{'ON ✅' if state.get('basic_verify') else 'OFF ⬜'}**\n"
        f"🎙️ Voice Verify: **{'ON ✅' if state.get('voice_verify') else 'OFF ⬜'}**\n"
        f"🪪 ID/Web Verify: **{'ON ✅' if state.get('id_verify') else 'OFF ⬜'}**\n"
        f"🧾 Logs: **{'ON ✅' if state.get('logs') else 'OFF ⬜'}**"
    )


async def _open_test_launch(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

    target, _title, _explanation, _requirement_key = (
        await _guided_setup_target(guild)
    )

    if target != "ready":
        return await _open_health_check(interaction)

    state = await _launch_state(guild)

    embed = discord.Embed(
        title="🧪 Test / Launch",
        description=(
            "This is where you post the panels and run the real test. "
            "Use an alt account before real members."
        ),
        color=discord.Color.green(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Selected Services", value=_launch_state_text(state), inline=False)

    actions: list[str] = []
    if state.get("tickets"):
        actions.append("1. Press **Post Ticket Panel**, then **Create Test Ticket**.")
    if state.get("basic_verify"):
        actions.append("2. Press **Post Basic Verify Panel**.")
    if state.get("voice_verify"):
        actions.append("3. Join the saved voice verify channel with an alt and request staff verification.")
    if state.get("id_verify"):
        actions.append("4. ID/Web verify is ON. Only use this for allowlisted/private servers.")
    actions.append("5. Join with an alt, click the public panel(s), and confirm roles/logs.")

    embed.add_field(name="Launch Actions", value="\n".join(actions)[:1024], inline=False)
    embed.add_field(
        name="Expected Result",
        value="Ticket panel opens a ticket. Basic Verify grants the approved role. No ID/Voice flow appears unless those switches are ON.",
        inline=False,
    )

    await solid._edit_or_followup(interaction, embed=embed, view=LaunchTestView(state))


async def _guided_setup_target(
    guild: discord.Guild,
) -> tuple[str, str, str, str]:
    """Return one structured next step for the guided setup."""

    try:
        cfg = await get_guild_config(
            guild.id,
            refresh=True,
        )
    except Exception:
        return (
            "retry",
            "Try Setup Again",
            "Dank Shield could not read the saved setup.",
            "retry",
        )

    setup_choice = str(
        _cfg_value(cfg, "setup_choice", "") or ""
    ).strip()

    if not setup_choice:
        return (
            "setup_type",
            "Choose What Dank Shield Should Do",
            "Pick the setup that most closely matches this server.",
            "setup_type",
        )

    services = _selected_setup_services(cfg)

    if not any(
        (
            services["tickets"],
            services["verify"],
            services["spam_guard"],
            services["logs"],
        )
    ):
        return (
            "services",
            "Choose Which Features Are On",
            (
                "This Custom Setup does not have any features "
                "turned on yet."
            ),
            "services",
        )

    bot_member = getattr(guild, "me", None)
    bot_permissions = getattr(
        bot_member,
        "guild_permissions",
        None,
    )

    missing_permissions = _missing_setup_permissions(
        bot_permissions,
        services,
    )

    if missing_permissions:
        return (
            "permissions",
            "Give Dank Shield Its Permissions",
            ", ".join(missing_permissions),
            "permissions",
        )

    if services["tickets"]:
        if not _has_role(
            guild,
            cfg,
            "staff_role_id",
        ):
            return (
                "roles",
                "Choose the Ticket Staff Role",
                "Pick the role for people who answer tickets.",
                "ticket_staff_role",
            )

        if not _has_channel(
            guild,
            cfg,
            "ticket_category_id",
        ):
            return (
                "folders",
                "Choose the New-Ticket Folder",
                "Pick the Discord category where tickets open.",
                "ticket_folder",
            )

        try:
            category_load = await solid._category_load(
                guild
            )

            if (
                category_load.error
                or not category_load.rows
            ):
                return (
                    "ticket_choices",
                    "Create Ticket Choices",
                    (
                        "Choose what members can request when "
                        "they open a ticket."
                    ),
                    "ticket_choices",
                )
        except Exception:
            return (
                "ticket_choices",
                "Check Ticket Choices",
                (
                    "Dank Shield could not confirm the ticket "
                    "choices yet."
                ),
                "ticket_choices",
            )

    if services["verify"]:
        if not _has_channel(
            guild,
            cfg,
            "verify_channel_id",
            "verification_channel_id",
        ):
            return (
                "channels",
                "Choose the Verification Channel",
                "Pick where members should press Verify.",
                "verification_channel",
            )

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
                (
                    "Pick the role members receive after "
                    "verification."
                ),
                "verified_role",
            )

    if services["voice"]:
        if not _has_channel(
            guild,
            cfg,
            "vc_verify_channel_id",
            "vc_verify_vc_id",
            "voice_verify_channel_id",
        ):
            return (
                "channels",
                "Choose the Voice Verify Channel",
                (
                    "Pick the voice channel used for the "
                    "staff check."
                ),
                "voice_verify_channel",
            )

        if not _has_channel(
            guild,
            cfg,
            "vc_verify_queue_channel_id",
            "vc_queue_channel_id",
            "vc_request_channel_id",
            "vc_verify_requests_channel_id",
        ):
            return (
                "channels",
                "Choose the Voice Verify Staff Channel",
                (
                    "Pick where staff should receive Voice "
                    "Verify requests."
                ),
                "voice_verify_staff_channel",
            )

        access_ok, access_text, _missing_access = (
            _verified_role_voice_access(
                guild,
                cfg,
            )
        )

        if not access_ok:
            return (
                "permissions",
                "Allow Approved Members Into Voice Verify",
                access_text,
                "verified_voice_access",
            )

    if (
        services["id"]
        and not id_verify_allowed_for_guild(guild)
    ):
        return (
            "setup_type",
            "Choose a Different Verification Type",
            (
                "ID/Web Verify is not available for this server. "
                "Choose Basic Verify or Voice Verify."
            ),
            "setup_type",
        )

    if services["logs"]:
        if not _has_channel(
            guild,
            cfg,
            "modlog_channel_id",
            "raidlog_channel_id",
        ):
            return (
                "logs",
                "Choose the Moderation Log Channel",
                (
                    "Pick where moderation and security records "
                    "should be posted."
                ),
                "modlog_channel",
            )

    return (
        "ready",
        "Setup Is Ready to Test",
        (
            "All required items for the enabled features "
            "are configured."
        ),
        "ready",
    )



_GUIDED_ONE_ITEM_SPECS: dict[str, dict[str, Any]] = {
    "ticket_staff_role": {
        "title": "Choose the Ticket Staff Role",
        "description": (
            "Choose the role for people who answer tickets, "
            "or let Dank Shield create the normal staff role."
        ),
        "kind": "role",
        "save_keys": (
            "staff_role_id",
            "vc_staff_role_id",
        ),
        "default_name": "DEFAULT_STAFF_ROLE_NAME",
    },
    "ticket_folder": {
        "title": "Choose the New-Ticket Folder",
        "description": (
            "Choose the Discord category where new tickets open, "
            "or let Dank Shield create the normal ticket folder."
        ),
        "kind": "category",
        "save_keys": (
            "ticket_category_id",
        ),
        "default_name": "TICKET_CATEGORY_NAME",
    },
    "verification_channel": {
        "title": "Choose the Verification Channel",
        "description": (
            "Choose where members should press Verify, "
            "or let Dank Shield create the normal verify channel."
        ),
        "kind": "text",
        "save_keys": (
            "verify_channel_id",
            "verification_channel_id",
        ),
        "default_name": "VERIFY_CHANNEL_NAME",
        "topic": (
            "Press Verify here to receive server access."
        ),
        "category_keys": (
            "start_category_id",
            "welcome_category_id",
        ),
        "overwrite_kind": "public",
    },
    "verified_role": {
        "title": "Choose the Approved-Member Role",
        "description": (
            "Choose the role members receive after verification, "
            "or let Dank Shield create the normal verified role."
        ),
        "kind": "role",
        "save_keys": (
            "verified_role_id",
        ),
        "default_name": "DEFAULT_VERIFIED_ROLE_NAME",
    },
    "voice_verify_channel": {
        "title": "Choose the Voice Verify Channel",
        "description": (
            "Choose the voice channel used for staff checks, "
            "or let Dank Shield create the normal voice channel."
        ),
        "kind": "voice",
        "save_keys": (
            "vc_verify_channel_id",
        ),
        "default_name": "VC_VERIFY_CHANNEL_NAME",
        "category_keys": (
            "start_category_id",
            "welcome_category_id",
        ),
        "overwrite_kind": "voice",
    },
    "voice_verify_staff_channel": {
        "title": "Choose the Voice Verify Staff Channel",
        "description": (
            "Choose the private text channel where staff receive "
            "Voice Verify requests, or let Dank Shield create it."
        ),
        "kind": "text",
        "save_keys": (
            "vc_verify_queue_channel_id",
            "vc_queue_channel_id",
            "vc_request_channel_id",
            "vc_verify_requests_channel_id",
        ),
        "default_name": "VC_QUEUE_CHANNEL_NAME",
        "topic": (
            "Staff requests and updates for Voice Verify."
        ),
        "category_keys": (
            "management_category_id",
            "staff_tools_category_id",
        ),
        "overwrite_kind": "staff",
    },
    "modlog_channel": {
        "title": "Choose the Moderation Log Channel",
        "description": (
            "Choose where moderation and security records should "
            "be posted, or let Dank Shield create the normal log."
        ),
        "kind": "text",
        "save_keys": (
            "modlog_channel_id",
            "raidlog_channel_id",
            "force_verify_log_channel_id",
        ),
        "default_name": "MODLOG_CHANNEL_NAME",
        "topic": (
            "Moderation and security logs are posted here."
        ),
        "category_keys": (
            "management_category_id",
            "staff_tools_category_id",
        ),
        "overwrite_kind": "staff",
    },
}


def _guided_item_spec(
    requirement_key: str,
) -> dict[str, Any]:
    return dict(
        _GUIDED_ONE_ITEM_SPECS.get(
            str(requirement_key or ""),
            {},
        )
    )


def _guided_configured_role(
    guild: discord.Guild,
    cfg: Any,
    *keys: str,
) -> Optional[discord.Role]:
    role_id = _cfg_snowflake(cfg, *keys)

    if role_id <= 0:
        return None

    return guild.get_role(role_id)


def _guided_configured_channel(
    guild: discord.Guild,
    cfg: Any,
    expected_type: type,
    *keys: str,
) -> Any:
    channel_id = _cfg_snowflake(cfg, *keys)

    if channel_id <= 0:
        return None

    channel = guild.get_channel(channel_id)

    if isinstance(channel, expected_type):
        return channel

    return None


def _guided_item_payload(
    requirement_key: str,
    item_id: int,
) -> dict[str, str]:
    spec = _guided_item_spec(requirement_key)

    if not spec:
        return {}

    clean_id = str(int(item_id))

    return {
        str(key): clean_id
        for key in spec.get("save_keys", ())
    }


def _guided_item_embed(
    requirement_key: str,
) -> discord.Embed:
    spec = _guided_item_spec(requirement_key)

    embed = discord.Embed(
        title=(
            f"🧩 {spec.get('title', 'Fix This Setup Item')}"
        ),
        description=(
            f"{spec.get('description', '')}\n\n"
            "**Choose one I already have** using the picker below, "
            "or press **Create this for me**."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )

    embed.add_field(
        name="Only this item",
        value=(
            "Nothing else will be created, renamed, moved, "
            "deleted, or overwritten."
        ),
        inline=False,
    )

    return embed


async def _guided_step_is_current(
    guild: discord.Guild,
    requirement_key: str,
) -> bool:
    _target, _title, _explanation, current_key = (
        await _guided_setup_target(guild)
    )

    return current_key == requirement_key


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

    if not await _guided_step_is_current(
        guild,
        requirement_key,
    ):
        return await _open_guided_setup(interaction)

    item_id = int(getattr(item, "id", 0) or 0)
    payload = _guided_item_payload(
        requirement_key,
        item_id,
    )

    if item_id <= 0 or not payload:
        embed = _guided_item_embed(requirement_key)
        embed.add_field(
            name="That did not work",
            value=(
                "I could not read that selection. "
                "Choose the item again."
            ),
            inline=False,
        )
        return await solid._edit_or_followup(
            interaction,
            embed=embed,
            view=GuidedOneItemView(
                requirement_key=requirement_key,
            ),
        )

    await solid._save_config(
        interaction,
        payload,
    )

    await _open_guided_setup(
        interaction,
        saved_message=(
            "Saved that item. Moving to the next setup step."
        ),
    )


async def _guided_create_exact_item(
    guild: discord.Guild,
    cfg: Any,
    requirement_key: str,
) -> tuple[Any, list[str], list[str], list[str]]:
    from . import public_setup_defaults as defaults

    spec = _guided_item_spec(requirement_key)
    notes: list[str] = []
    created: list[str] = []
    reused: list[str] = []

    if not spec:
        return None, notes, created, reused

    default_name = str(
        getattr(
            defaults,
            str(spec.get("default_name", "")),
            "",
        )
        or ""
    )

    staff_role = _guided_configured_role(
        guild,
        cfg,
        "staff_role_id",
        "vc_staff_role_id",
    )
    control_role = _guided_configured_role(
        guild,
        cfg,
        "server_control_role_id",
        "control_role_id",
        "perm_role_id",
    )
    unverified_role = _guided_configured_role(
        guild,
        cfg,
        "unverified_role_id",
    )

    staff_overwrites = defaults._staff_overwrites(
        guild,
        staff_role,
        control_role,
    )
    public_overwrites = defaults._public_overwrites(
        guild,
        staff_role,
        control_role,
        unverified_role,
    )
    voice_overwrites = defaults._voice_overwrites(
        guild,
        staff_role,
        control_role,
        unverified_role,
    )

    category_keys = tuple(
        str(key)
        for key in spec.get("category_keys", ())
    )
    category = (
        _guided_configured_channel(
            guild,
            cfg,
            discord.CategoryChannel,
            *category_keys,
        )
        if category_keys
        else None
    )

    kind = str(spec.get("kind", ""))
    overwrite_kind = str(
        spec.get("overwrite_kind", "")
    )

    if kind == "role":
        item = await defaults._ensure_role(
            guild,
            default_name,
            create_missing_roles=True,
            notes=notes,
            created=created,
            reused=reused,
        )
    elif kind == "category":
        item = await defaults._ensure_category(
            guild,
            default_name,
            overwrites=staff_overwrites,
            notes=notes,
            created=created,
            reused=reused,
        )
    elif kind == "voice":
        item = await defaults._ensure_voice(
            guild,
            default_name,
            category=category,
            overwrites=voice_overwrites,
            notes=notes,
            created=created,
            reused=reused,
        )
    elif kind == "text":
        if overwrite_kind == "public":
            overwrites = public_overwrites
        else:
            overwrites = staff_overwrites

        item = await defaults._ensure_text(
            guild,
            default_name,
            category=category,
            overwrites=overwrites,
            topic=str(spec.get("topic", "") or ""),
            notes=notes,
            created=created,
            reused=reused,
        )
    else:
        item = None
        notes.append(
            "Dank Shield does not recognize this setup item."
        )

    return item, notes, created, reused


async def _guided_create_item(
    interaction: discord.Interaction,
    requirement_key: str,
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

    if not await _guided_step_is_current(
        guild,
        requirement_key,
    ):
        return await _open_guided_setup(interaction)

    cfg = await get_guild_config(
        guild.id,
        refresh=True,
    )

    item, notes, created, reused = (
        await _guided_create_exact_item(
            guild,
            cfg,
            requirement_key,
        )
    )

    item_id = int(getattr(item, "id", 0) or 0)
    payload = _guided_item_payload(
        requirement_key,
        item_id,
    )

    if item_id <= 0 or not payload:
        embed = _guided_item_embed(requirement_key)
        embed.add_field(
            name="I could not create it",
            value=(
                "\n".join(notes)[-1000:]
                or (
                    "Discord did not create the item. "
                    "Check the bot permissions and try again."
                )
            ),
            inline=False,
        )
        return await solid._edit_or_followup(
            interaction,
            embed=embed,
            view=GuidedOneItemView(
                requirement_key=requirement_key,
            ),
        )

    await solid._save_config(
        interaction,
        payload,
    )

    result = (
        "Created this item for you."
        if created
        else "Found the matching item and connected it."
    )

    await _open_guided_setup(
        interaction,
        saved_message=(
            f"{result} Moving to the next setup step."
        ),
    )


class GuidedExistingRoleSelect(discord.ui.RoleSelect):
    def __init__(self, *, requirement_key: str) -> None:
        self.requirement_key = str(requirement_key)
        super().__init__(
            placeholder="Choose one I already have",
            min_values=1,
            max_values=1,
            custom_id=(
                "dank_setup_guided_existing_role:"
                f"{self.requirement_key}"
            ),
            row=0,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await _guided_save_existing_item(
            interaction,
            self.requirement_key,
            self.values[0],
        )


class GuidedExistingChannelSelect(
    discord.ui.ChannelSelect
):
    def __init__(
        self,
        *,
        requirement_key: str,
        channel_type: discord.ChannelType,
    ) -> None:
        self.requirement_key = str(requirement_key)
        super().__init__(
            placeholder="Choose one I already have",
            channel_types=[channel_type],
            min_values=1,
            max_values=1,
            custom_id=(
                "dank_setup_guided_existing_channel:"
                f"{self.requirement_key}"
            ),
            row=0,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await _guided_save_existing_item(
            interaction,
            self.requirement_key,
            self.values[0],
        )


class GuidedCreateItemButton(discord.ui.Button):
    def __init__(self, *, requirement_key: str) -> None:
        self.requirement_key = str(requirement_key)
        super().__init__(
            label="Create this for me",
            emoji="✨",
            style=discord.ButtonStyle.success,
            custom_id=(
                "dank_setup_guided_create:"
                f"{self.requirement_key}"
            ),
            row=1,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await _guided_create_item(
            interaction,
            self.requirement_key,
        )


class GuidedBackButton(discord.ui.Button):
    def __init__(self, *, requirement_key: str) -> None:
        super().__init__(
            label="Back to Guided Setup",
            emoji="↩️",
            style=discord.ButtonStyle.secondary,
            custom_id=(
                "dank_setup_guided_item_back:"
                f"{requirement_key}"
            ),
            row=1,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await _open_guided_setup(interaction)


class GuidedOneItemView(discord.ui.View):
    def __init__(
        self,
        *,
        requirement_key: str,
    ) -> None:
        super().__init__(timeout=900)

        self.requirement_key = str(
            requirement_key or ""
        )
        spec = _guided_item_spec(
            self.requirement_key
        )
        kind = str(spec.get("kind", ""))

        if kind == "role":
            self.add_item(
                GuidedExistingRoleSelect(
                    requirement_key=self.requirement_key,
                )
            )
        else:
            channel_type = {
                "category": discord.ChannelType.category,
                "text": discord.ChannelType.text,
                "voice": discord.ChannelType.voice,
            }.get(kind)

            if channel_type is not None:
                self.add_item(
                    GuidedExistingChannelSelect(
                        requirement_key=self.requirement_key,
                        channel_type=channel_type,
                    )
                )

        self.add_item(
            GuidedCreateItemButton(
                requirement_key=self.requirement_key,
            )
        )
        self.add_item(
            GuidedBackButton(
                requirement_key=self.requirement_key,
            )
        )


async def _open_guided_one_item(
    interaction: discord.Interaction,
    requirement_key: str,
) -> None:
    spec = _guided_item_spec(requirement_key)

    if not spec:
        return await _open_guided_setup(interaction)

    await solid._edit_or_followup(
        interaction,
        embed=_guided_item_embed(requirement_key),
        view=GuidedOneItemView(
            requirement_key=requirement_key,
        ),
    )


async def _open_guided_target(
    interaction: discord.Interaction,
    target: str,
    requirement_key: str = "",
) -> None:
    """Open only the screen needed for the current guided step."""

    if requirement_key in _GUIDED_ONE_ITEM_SPECS:
        return await _open_guided_one_item(
            interaction,
            requirement_key,
        )


    if target == "setup_type":
        return await _open_choose_setup_type(interaction)

    if target == "services":
        return await _open_services(interaction)

    if target == "ticket_choices":
        return await _open_ticket_menu(interaction)

    if target == "ready":
        return await _open_health_check(interaction)

    if target == "retry":
        return await _open_guided_setup(interaction)

    if target == "permissions":
        guild = interaction.guild

        if guild is None:
            return await interaction.response.send_message(
                "❌ This must be used inside a server.",
                ephemeral=True,
            )

        cfg = await get_guild_config(
            guild.id,
            refresh=True,
        )

        if requirement_key == "verified_voice_access":
            access_ok, access_text, missing_access = (
                _verified_role_voice_access(
                    guild,
                    cfg,
                )
            )

            if access_ok:
                return await _open_health_check(
                    interaction
                )

            required_text = (
                ", ".join(missing_access)
                or "View Channel, Connect, Speak"
            )

            embed = discord.Embed(
                title=(
                    "🔊 Allow Approved Members Into "
                    "Voice Verify"
                ),
                description=(
                    "The Voice Verify channel exists, but "
                    "approved members cannot fully use it yet."
                ),
                color=discord.Color.orange(),
                timestamp=now_utc(),
            )

            embed.add_field(
                name="What is blocked",
                value=access_text[:1024],
                inline=False,
            )

            embed.add_field(
                name="Fix it in Discord",
                value=(
                    "1. Open the saved Voice Verify channel.\n"
                    "2. Choose **Edit Channel → Permissions**.\n"
                    "3. Select the approved-member role.\n"
                    f"4. Allow: **{required_text}**.\n"
                    "5. Return and press **Fix Next Item**."
                )[:1024],
                inline=False,
            )

            return await solid._edit_or_followup(
                interaction,
                embed=embed,
                view=ContinueSetupView(
                    target="permissions",
                    ready=False,
                ),
            )

        services = _selected_setup_services(cfg)

        bot_member = getattr(guild, "me", None)
        bot_permissions = getattr(
            bot_member,
            "guild_permissions",
            None,
        )

        missing = _missing_setup_permissions(
            bot_permissions,
            services,
        )

        embed = discord.Embed(
            title="🔐 Dank Shield Needs Permission",
            description=(
                "Give the bot only the permissions needed by "
                "the features you turned on."
            ),
            color=discord.Color.orange(),
            timestamp=now_utc(),
        )

        embed.add_field(
            name="Missing",
            value=(
                "\n".join(
                    f"• {name}"
                    for name in missing
                )
                or "Nothing is missing now."
            ),
            inline=False,
        )

        embed.add_field(
            name="After fixing it",
            value=(
                "Return here and press **Fix Next Item** again."
            ),
            inline=False,
        )

        return await solid._edit_or_followup(
            interaction,
            embed=embed,
            view=ContinueSetupView(
                target="permissions",
                ready=False,
            ),
        )

    from . import public_setup_full_customization as full

    if target == "roles":
        embed = discord.Embed(
            title="👥 Choose the Needed Role",
            description=(
                "Choose the role named in the guided step. "
                "You can ignore the other optional choices."
            ),
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        view: discord.ui.View = (
            full.RoleCustomizationPageOne()
        )

    elif target == "folders":
        embed = discord.Embed(
            title="📁 Choose the Needed Folder",
            description=(
                "Choose the folder named in the guided step. "
                "Discord calls folders categories."
            ),
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        view = full.DiscordCategoryCustomizationView()

    elif target == "channels":
        embed = discord.Embed(
            title="💬 Choose the Needed Channel",
            description=(
                "Choose the channel named in the guided step. "
                "Skip channels for features that are off."
            ),
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        view = full.ChannelCustomizationPageOne()

    elif target == "logs":
        embed = discord.Embed(
            title="🧾 Choose the Needed Log Channel",
            description=(
                "Choose where the enabled log feature should post."
            ),
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        view = full.LogStatusCustomizationView()

    else:
        return await _open_guided_setup(interaction)

    await solid._edit_or_followup(
        interaction,
        embed=embed,
        view=view,
    )


async def _open_guided_setup(
    interaction: discord.Interaction,
    *,
    saved_message: str = "",
) -> None:
    """Show one next action instead of competing setup paths."""

    if not await solid._require_setup_permission(interaction):
        return

    guild = interaction.guild

    if guild is None:
        return await interaction.response.send_message(
            "❌ This must be used inside a server.",
            ephemeral=True,
        )

    await solid._safe_defer_update(interaction)

    target, title, explanation, requirement_key = (
        await _guided_setup_target(guild)
    )
    if target == "ready":
        return await _open_health_check(
            interaction,
            saved_message=saved_message,
            already_deferred=True,
        )


    progress, done, total, _next_step = (
        await _setup_progress(guild)
    )

    embed = discord.Embed(
        title="🧭 Guided Setup",
        description=(
            "One step at a time. Dank Shield shows only the "
            "next required item."
        ),
        color=(
            discord.Color.green()
            if target == "ready"
            else discord.Color.blurple()
        ),
        timestamp=now_utc(),
    )

    if saved_message:
        embed.add_field(
            name="Saved",
            value=saved_message[:1024],
            inline=False,
        )

    embed.add_field(
        name="Next Step",
        value=(
            f"**{title}**\n{explanation}"
        )[:1024],
        inline=False,
    )

    embed.add_field(
        name="Progress",
        value=(
            f"**{done}/{total} required checks complete**"
            if total
            else "Setup has not started yet."
        ),
        inline=False,
    )

    remaining = [
        line
        for line in str(progress or "").splitlines()
        if line.startswith(("⚠️", "🚫", "❌"))
    ]

    if remaining:
        embed.add_field(
            name="Coming After This",
            value="\n".join(remaining[:3])[:1024],
            inline=False,
        )

    embed.set_footer(
        text=(
            f"Guild {guild.id} • one guided setup route"
        )
    )

    await solid._edit_or_followup(
        interaction,
        embed=embed,
        view=ContinueSetupView(
            target=target,
            requirement_key=requirement_key,
            ready=(target == "ready"),
        ),
    )


class ProductSetupHomeView(discord.ui.View):
    def __init__(
        self,
        *,
        ready: bool = False,
        started: bool = False,
    ) -> None:
        super().__init__(timeout=900)

        self.ready = bool(ready)
        self.started = bool(started)

        try:
            self.continue_setup.label = (
                "Continue Setup"
                if self.started
                else "Start Setup"
            )
            self.launch.disabled = not self.ready
        except Exception:
            pass

    @discord.ui.button(
        label="Start / Continue Setup",
        emoji="▶️",
        style=discord.ButtonStyle.success,
        custom_id="dank_setup_home:continue",
        row=0,
    )
    async def continue_setup(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self.started:
            await _open_guided_setup(interaction)
            return

        await _open_choose_setup_type(interaction)

    @discord.ui.button(
        label="Setup Check",
        emoji="🩺",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_home:health",
        row=0,
    )
    async def health(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await _open_health_check(interaction)

    @discord.ui.button(
        label="Test / Launch",
        emoji="🧪",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_home:launch",
        row=1,
    )
    async def launch(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await _open_test_launch(interaction)

    @discord.ui.button(
        label="Manage Setup",
        emoji="⚙️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_home:manage",
        row=1,
    )
    async def manage(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await _open_manage_setup(interaction)


class ContinueSetupView(discord.ui.View):
    def __init__(
        self,
        *,
        target: str,
        requirement_key: str = "",
        ready: bool,
    ) -> None:
        super().__init__(timeout=900)

        self.target = str(target)
        self.requirement_key = str(requirement_key or "")
        self.ready = bool(ready)

        try:
            self.fix_next.disabled = self.ready
            self.fix_next.label = (
                "Setup Complete"
                if self.ready
                else "Fix Next Item"
            )
        except Exception:
            pass

    @discord.ui.button(
        label="Fix Next Item",
        emoji="➡️",
        style=discord.ButtonStyle.success,
        custom_id="dank_setup_guided:fix_next",
        row=0,
    )
    async def fix_next(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await _open_guided_target(
            interaction,
            self.target,
            self.requirement_key,
        )

    @discord.ui.button(
        label="Setup Check",
        emoji="🩺",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_guided:review",
        row=0,
    )
    async def review(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await _open_health_check(interaction)

    @discord.ui.button(
        label="Change Setup Type",
        emoji="🧭",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_guided:change_type",
        row=1,
    )
    async def change_type(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await _open_choose_setup_type(interaction)

    @discord.ui.button(
        label="Advanced Options",
        emoji="⚙️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_guided:advanced",
        row=1,
    )
    async def advanced(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await _open_manage_setup(interaction)

    @discord.ui.button(
        label="Back Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_guided:home",
        row=2,
    )
    async def home(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await _home_edit(interaction)


class ManageSetupView(discord.ui.View):
    """The canonical grouped Advanced Options hub."""

    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(
        label="Core Setup",
        emoji="🧰",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_manage:core",
        row=0,
    )
    async def core_setup(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await _open_advanced_core_setup(interaction)

    @discord.ui.button(
        label="Member Experience",
        emoji="👥",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_manage:members",
        row=0,
    )
    async def member_experience(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await _open_advanced_member_experience(interaction)

    @discord.ui.button(
        label="Monitoring & Repair",
        emoji="🧰",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_manage:monitoring_repair",
        row=1,
    )
    async def monitoring_repair(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await _open_advanced_monitoring_repair(interaction)

    @discord.ui.button(
        label="Appearance",
        emoji="🎨",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_manage:appearance",
        row=1,
    )
    async def appearance(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await _open_advanced_appearance(interaction)

    @discord.ui.button(
        label="Danger Zone",
        emoji="🚨",
        style=discord.ButtonStyle.danger,
        custom_id="dank_setup_manage:danger",
        row=2,
    )
    async def danger_zone(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await _open_advanced_danger_zone(interaction)

    @discord.ui.button(
        label="Help / FAQ",
        emoji="❓",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_manage:help",
        row=2,
    )
    async def help_faq(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await solid._require_setup_permission(interaction):
            return

        await interaction.response.edit_message(
            embed=_build_setup_help_embed(),
            view=ManageSetupView(),
        )

    @discord.ui.button(
        label="Back Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_manage:home",
        row=3,
    )
    async def home(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await _home_edit(interaction)


class AdvancedCoreSetupView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(
        label="Features On / Off",
        emoji="🧩",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_advanced_core:services",
        row=0,
    )
    async def services(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_services(interaction)

    @discord.ui.button(
        label="Timers & Behavior",
        emoji="⏱️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_advanced_core:timers_behavior",
        row=0,
    )
    async def timers_behavior(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_timers_behavior(interaction)

    @discord.ui.button(
        label="Detailed Role / Channel Mapping",
        emoji="🧭",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_advanced_core:existing",
        row=1,
    )
    async def detailed_mapping(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_existing_server(interaction)

    @discord.ui.button(
        label="Back to Advanced",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_advanced_core:back",
        row=2,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_manage_setup(interaction)

    @discord.ui.button(
        label="Back Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_advanced_core:home",
        row=2,
    )
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _home_edit(interaction)


class AdvancedMemberExperienceView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(
        label="Ticket Choices",
        emoji="🧾",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_advanced_members:ticket_menu",
        row=0,
    )
    async def ticket_choices(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_ticket_menu(interaction)

    @discord.ui.button(
        label="Protection",
        emoji="🛡️",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_advanced_members:protection",
        row=0,
    )
    async def protection(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_protection_options(interaction)

    @discord.ui.button(
        label="Back to Advanced",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_advanced_members:back",
        row=1,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_manage_setup(interaction)

    @discord.ui.button(
        label="Back Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_advanced_members:home",
        row=1,
    )
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _home_edit(interaction)


class AdvancedMonitoringRepairView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(
        label="Modlog Tracking",
        emoji="🧾",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_advanced_monitoring:modlog_tracking",
        row=0,
    )
    async def modlog_tracking(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_modlog_tracking(interaction)

    @discord.ui.button(
        label="Permission Repair",
        emoji="🛠️",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_advanced_monitoring:permission_repair",
        row=0,
    )
    async def permission_repair(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_permission_repair(interaction)

    @discord.ui.button(
        label="Back to Advanced",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_advanced_monitoring:back",
        row=1,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_manage_setup(interaction)

    @discord.ui.button(
        label="Back Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_advanced_monitoring:home",
        row=1,
    )
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _home_edit(interaction)


class AdvancedAppearanceView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(
        label="Server Design",
        emoji="🎨",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_advanced_appearance:design",
        row=0,
    )
    async def server_design(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        from . import public_design_bridge
        await public_design_bridge.open_design_studio_from_setup(interaction)

    @discord.ui.button(
        label="Back to Advanced",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_advanced_appearance:back",
        row=1,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_manage_setup(interaction)

    @discord.ui.button(
        label="Back Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_advanced_appearance:home",
        row=1,
    )
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _home_edit(interaction)


class AdvancedDangerZoneView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(
        label="Recovery / Start Over",
        emoji="🧯",
        style=discord.ButtonStyle.danger,
        custom_id="dank_setup_advanced_danger:recovery",
        row=0,
    )
    async def recovery(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_recovery_center(interaction)

    @discord.ui.button(
        label="Back to Advanced",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_advanced_danger:back",
        row=1,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_manage_setup(interaction)

    @discord.ui.button(
        label="Back Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_advanced_danger:home",
        row=1,
    )
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _home_edit(interaction)



_SETUP_TEST_TICKET_LOCKS: dict[
    tuple[int, int],
    asyncio.Lock,
] = {}


def _setup_test_ticket_channel_id(
    row: Any,
) -> int:
    """Read a saved ticket channel ID without guessing."""

    if not isinstance(row, dict):
        return 0

    for key in (
        "channel_id",
        "ticket_channel_id",
        "discord_channel_id",
        "channel",
    ):
        try:
            channel_id = int(row.get(key) or 0)
        except (TypeError, ValueError):
            channel_id = 0

        if channel_id > 0:
            return channel_id

    return 0


async def _resolve_setup_test_ticket_channel(
    guild: discord.Guild,
    row: Any,
) -> Optional[discord.TextChannel]:
    """Resolve an existing ticket channel safely."""

    channel_id = _setup_test_ticket_channel_id(row)

    if channel_id <= 0:
        return None

    channel = guild.get_channel(channel_id)

    if isinstance(channel, discord.TextChannel):
        return channel

    try:
        fetched = await guild.fetch_channel(channel_id)
    except Exception:
        return None

    if isinstance(fetched, discord.TextChannel):
        return fetched

    return None


async def _create_setup_test_ticket(
    interaction: discord.Interaction,
) -> None:
    """Create one collision-safe setup test ticket."""

    if not await solid._require_setup_permission(
        interaction
    ):
        return

    guild = interaction.guild
    member = (
        interaction.user
        if isinstance(interaction.user, discord.Member)
        else None
    )

    if guild is None or member is None:
        return await interaction.response.send_message(
            (
                "❌ This must be used inside a server "
                "as a server member."
            ),
            ephemeral=True,
        )

    state = await _launch_state(guild)

    if not state.get("tickets"):
        return await interaction.response.send_message(
            (
                "🎫 Tickets are OFF for this server. "
                "Turn Tickets ON before creating a test ticket."
            ),
            ephemeral=True,
        )

    target, _title, _explanation, _requirement_key = (
        await _guided_setup_target(guild)
    )

    if target != "ready":
        return await _open_health_check(interaction)

    lock_key = (
        int(guild.id),
        int(member.id),
    )
    lock = _SETUP_TEST_TICKET_LOCKS.get(lock_key)

    if lock is None:
        lock = asyncio.Lock()
        _SETUP_TEST_TICKET_LOCKS[lock_key] = lock

    if lock.locked():
        return await interaction.response.send_message(
            (
                "⏳ A setup test-ticket action is already "
                "running for you."
            ),
            ephemeral=True,
        )

    async with lock:
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(
                    ephemeral=True,
                    thinking=True,
                )
        except Exception:
            pass

        try:
            from stoney_verify.tickets_new.service import (
                create_ticket_channel,
                find_open_ticket_for_owner,
            )

            existing = await find_open_ticket_for_owner(
                guild_id=guild.id,
                owner_id=member.id,
                category=None,
            )

            if isinstance(existing, dict):
                existing_channel = (
                    await _resolve_setup_test_ticket_channel(
                        guild,
                        existing,
                    )
                )
                existing_category = str(
                    existing.get("matched_category_slug")
                    or existing.get("category")
                    or ""
                ).strip().lower()

                if isinstance(
                    existing_channel,
                    discord.TextChannel,
                ):
                    if existing_category == "setup_test":
                        return await interaction.followup.send(
                            (
                                "✅ Your setup test ticket is "
                                f"already open: "
                                f"{existing_channel.mention}"
                            ),
                            ephemeral=True,
                            allowed_mentions=(
                                discord.AllowedMentions.none()
                            ),
                        )

                    return await interaction.followup.send(
                        embed=discord.Embed(
                            title=(
                                "🎫 Open Ticket Already Exists"
                            ),
                            description=(
                                "You already have an open ticket: "
                                f"{existing_channel.mention}\n\n"
                                "Dank Shield will not create a "
                                "test ticket on top of a real "
                                "ticket. Close or delete that "
                                "ticket first, or use it to test "
                                "the staff controls."
                            ),
                            color=discord.Color.orange(),
                        ),
                        ephemeral=True,
                        allowed_mentions=(
                            discord.AllowedMentions.none()
                        ),
                    )

            channel = await create_ticket_channel(
                guild=guild,
                owner=member,
                category="setup_test",
                source="setup_health_test_ticket",
                opening_message=(
                    f"🧪 {member.mention} this is a Dank Shield "
                    "setup test ticket.\n\n"
                    "Use it to test claim, close, reopen, "
                    "transcript, and delete controls. "
                    "It is safe to delete when finished."
                ),
                priority="low",
                matched_category_slug="setup_test",
                matched_category_name="Setup Test",
                matched_intake_type="test",
                matched_category_reason=(
                    "Created from the canonical /dank setup "
                    "Test / Launch screen"
                ),
                matched_category_score=100,
                category_override=True,
            )

            if isinstance(channel, discord.TextChannel):
                return await interaction.followup.send(
                    (
                        "✅ Test ticket ready: "
                        f"{channel.mention}\n"
                        "Try the staff controls, then delete it "
                        "when finished."
                    ),
                    ephemeral=True,
                    allowed_mentions=(
                        discord.AllowedMentions.none()
                    ),
                )

            await interaction.followup.send(
                (
                    "🚫 The test ticket could not be created. "
                    "Run **Setup Check** and fix the blocker shown."
                ),
                ephemeral=True,
                allowed_mentions=(
                    discord.AllowedMentions.none()
                ),
            )

        except Exception as exc:
            await interaction.followup.send(
                (
                    "🚫 Test ticket failed: "
                    f"`{type(exc).__name__}: "
                    f"{str(exc)[:300]}`"
                ),
                ephemeral=True,
                allowed_mentions=(
                    discord.AllowedMentions.none()
                ),
            )


class LaunchTestView(discord.ui.View):
    def __init__(self, state: Optional[dict[str, bool]] = None) -> None:
        super().__init__(timeout=900)
        self.state = dict(state or {})

        try:
            self.create_test_ticket.disabled = not bool(
                self.state.get("tickets")
            )
        except Exception:
            pass

    @discord.ui.button(label="Post Ticket Panel", emoji="🎫", style=discord.ButtonStyle.success, custom_id="dank_setup_launch:post_ticket_panel", row=0)
    async def post_ticket_panel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        state = await _launch_state(guild)
        if not state.get("tickets"):
            return await interaction.response.send_message("🎫 Tickets are OFF in Custom Setup. Turn Tickets ON first.", ephemeral=True)
        try:
            from .public_ticket_panel_commands import post_ticket_panel_callback
            return await post_ticket_panel_callback(interaction)
        except Exception as e:
            return await interaction.response.send_message(f"❌ Could not post ticket panel: `{type(e).__name__}: {str(e)[:220]}`", ephemeral=True)

    @discord.ui.button(label="Post Basic Verify Panel", emoji="✅", style=discord.ButtonStyle.success, custom_id="dank_setup_launch:post_basic_verify", row=0)
    async def post_basic_verify(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        state = await _launch_state(guild)
        if not state.get("basic_verify"):
            return await interaction.response.send_message("✅ Basic Verify is OFF in Custom Setup. Turn Basic Verify ON first.", ephemeral=True)
        try:
            from .public_verify_basic_panel import verify_panel
            return await verify_panel(interaction)
        except Exception as e:
            return await interaction.response.send_message(f"❌ Could not post Basic Verify panel: `{type(e).__name__}: {str(e)[:220]}`", ephemeral=True)

    @discord.ui.button(
        label="Create Test Ticket",
        emoji="🎫",
        style=discord.ButtonStyle.success,
        custom_id="dank_setup_launch:create_test_ticket",
        row=0,
    )
    async def create_test_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await _create_setup_test_ticket(interaction)

    @discord.ui.button(label="Run Setup Check", emoji="🩺", style=discord.ButtonStyle.primary, custom_id="dank_setup_launch:health", row=1)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_health_check(interaction)

    @discord.ui.button(label="View Current Setup", emoji="📋", style=discord.ButtonStyle.secondary, custom_id="dank_setup_launch:current", row=1)
    async def current(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await solid._safe_defer_update(interaction)
        embed = await solid._build_current_setup_embed(guild)
        await solid._edit_or_followup(interaction, embed=embed, view=LaunchTestView(await _launch_state(guild)))

    @discord.ui.button(label="Back Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_launch:home", row=2)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _home_edit(interaction)

def _patch() -> None:
    global _PATCHED
    solid._build_main_setup_payload = _product_main_setup_payload
    _PATCHED = True


_patch()


def register_public_setup_recommend_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    _patch()
    print("✅ public_setup_recommend: plain-language /dank setup choices active")


__all__ = ["register_public_setup_recommend_commands"]
