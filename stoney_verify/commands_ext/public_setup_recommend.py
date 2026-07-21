from __future__ import annotations

import asyncio

"""Canonical plain-language product flow for public ``/dank setup``.

The low-level builders remain in ``public_setup_solid``. This module owns the
customer-facing home, guided path, review, testing, completion, and navigation
language.
"""

from typing import Any, Optional

import discord

from ..globals import now_utc
from ..guild_config import get_guild_config
from ..setup_service_state import (
    SetupServiceState,
    load_setup_service_state,
    mark_setup_completed,
    service_state_from_config,
)
from ..setup_engine.verification_modes import id_verify_allowed_for_guild
from ..setup_new import (
    build_setup_template_embed,
    build_setup_template_select_options,
    get_setup_template,
    setup_template_payload,
)
from . import public_setup_solid as solid


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


def _selected_setup_services(cfg: Any) -> dict[str, bool]:
    """Return the one canonical feature selection for every setup screen."""
    state = service_state_from_config(cfg)
    return {
        "tickets": bool(state.tickets),
        "verify": bool(state.verification_enabled),
        "basic_verify": bool(state.simple_verify),
        "voice": bool(state.voice_verify),
        "id": bool(state.id_verify),
        "spam_guard": bool(state.spam_guard),
        "logs": bool(state.logs),
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
            "Press **Continue Setup** and turn on at least one feature."
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
                    "Ticket choices could not be checked. Press **Continue Setup** to fix them."
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
            "Choose Simple Verify or Voice Verify instead."
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
            f"Simple Verify: `{'ON' if services['basic_verify'] else 'OFF'}`\n"
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
            "Press **Continue Setup** to finish "
            "anything required.\n"
            "Press **Test Your Setup** after this page says ready."
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
        description=(
            "Setup is meant to be simple: start setup, choose what you want, "
            "then follow one step at a time."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(
        name="Where do I start?",
        value="Press **Start Setup**. Choose what you want Dank Shield to do in this server.",
        inline=False,
    )
    embed.add_field(
        name="What do I do after that?",
        value=(
            "Press **Set Up This Step**. Dank Shield shows one thing at a time. "
            "After you finish it, setup moves to the next thing you need."
        ),
        inline=False,
    )
    embed.add_field(
        name="What if I already have roles or channels?",
        value=(
            "Choose the role or channel you already use when setup asks for it. "
            "You can also let Dank Shield create the needed item for you."
        ),
        inline=False,
    )
    embed.add_field(
        name="How do I know when setup is finished?",
        value=(
            "Dank Shield checks setup automatically after the last required step. "
            "Fix any problem it shows, then press **Test Your Setup**."
        ),
        inline=False,
    )
    embed.add_field(
        name="Where are the extra settings?",
        value=(
            "Press **Manage Setup** on Setup Home. Most servers do not need those "
            "extra settings during normal setup."
        ),
        inline=False,
    )
    embed.add_field(
        name="What is ID / Web + Voice?",
        value=(
            "It combines private ID review with a staff voice check. "
            "Those options only appear for servers approved to use ID/Web Verify."
        ),
        inline=False,
    )
    embed.add_field(
        name="Will setup delete my server?",
        value=(
            "No. The guided setup only connects or creates the item it is asking for. "
            "Starting over is kept separately under **Manage Setup**."
        ),
        inline=False,
    )
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
        "Press **Start Setup** and choose what you want Dank Shield to do.",
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
        "Press **Continue Setup** and choose at least one feature.",
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
            "Press **Continue Setup** to choose the role for people who answer tickets.",
        )

        check(
            "New-ticket folder",
            _has_channel(
                guild,
                cfg,
                "ticket_category_id",
            ),
            "Press **Continue Setup** to choose where new tickets should open.",
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
                        "Press **Continue Setup** to choose what members can request in a ticket."
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
                        "Press **Continue Setup** to choose what members can request in a ticket."
                    )
        except Exception:
            total += 1
            lines.append(
                "⚠️ Ticket choices could not be checked."
            )

            if next_step == default_next:
                next_step = (
                    "Press **Continue Setup** to choose what members can request in a ticket."
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
            "Press **Continue Setup** to choose where members press Verify.",
        )

        check(
            "Approved-member role",
            _has_role(
                guild,
                cfg,
                "verified_role_id",
            ),
            "Press **Continue Setup** to choose the role members get after verification.",
        )

    if services["voice"]:
        check(
            "Voice Verify channel",
            _has_channel(
                guild,
                cfg,
                "vc_verify_channel_id",
            ),
            "Press **Continue Setup** to choose the Voice Verify channel.",
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
            "Press **Continue Setup** to choose where staff receive Voice Verify requests.",
        )

    if services["id"]:
        check(
            "ID/Web Verify permission",
            id_verify_allowed_for_guild(guild),
            "Choose Simple Verify or Voice Verify instead.",
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
            "Press **Continue Setup** to choose where logs should be posted.",
        )

    if total and done == total:
        next_step = (
            "Press **Test Your Setup** and test with a second Discord account."
        )

    return (
        "\n".join(lines)[:1024],
        done,
        total,
        next_step,
    )


def _enabled_feature_text(state: SetupServiceState) -> str:
    labels = state.enabled_labels()
    if not labels:
        return "No features are selected yet."
    return " • ".join(f"**{label}**" for label in labels)


async def _product_main_setup_payload(
    guild: discord.Guild,
) -> tuple[discord.Embed, discord.ui.View]:
    progress_text, done, total, next_step = await _setup_progress(guild)
    try:
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception:
        cfg = None

    state = service_state_from_config(cfg)
    started = bool(state.setup_choice)
    ready = bool(total and done >= total)
    completed = bool(ready and state.completed)
    issues = [
        line.strip()
        for line in str(progress_text or "").splitlines()
        if line.strip().startswith(("⚠️", "🚫", "❌"))
    ][:3]

    if not started:
        status = "Not started"
        recommended = "Press **Start Setup** and choose what this server needs."
    elif completed:
        status = "Setup finished"
        recommended = (
            "Your setup is saved and marked finished. Open **View Setup Summary** "
            "to review it, or use **Manage Setup** when you want to change something."
        )
    elif ready:
        status = "Ready for testing"
        recommended = (
            "Press **Test Your Setup**. When the enabled features work, press **Finish Setup**."
        )
    else:
        status = "Needs attention"
        recommended = str(next_step or "Press Continue Setup.")[:350]

    embed = discord.Embed(
        title="🚀 Dank Shield Setup",
        description=(
            "Follow the recommended next step. Settings you do not need stay under "
            "**Manage Setup**."
        ),
        color=discord.Color.green() if completed or ready else discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(
        name="Status",
        value=(
            f"**{status}**\n"
            f"Setup plan: **{state.setup_label}**\n"
            f"`{done}/{total}` required steps complete"
        )[:1024],
        inline=False,
    )
    embed.add_field(
        name="Enabled Features",
        value=_enabled_feature_text(state)[:1024],
        inline=False,
    )
    embed.add_field(name="Recommended Next Step", value=recommended[:1024], inline=False)
    embed.add_field(
        name="Needs Attention",
        value="\n".join(issues)[:900] if issues else "✅ No required setup problem is blocking you.",
        inline=False,
    )
    embed.set_footer(text=f"Guild {guild.id} • /dank setup")
    return embed, ProductSetupHomeView(ready=ready, started=started, completed=completed)

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
            value="Press **Use This Plan** to save this choice, or pick another option from the menu.",
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=view)


class SetupChoiceView(solid.BackToSetupView):
    def __init__(self, *, selected_key: Optional[str] = None) -> None:
        super().__init__()
        self.selected_key = selected_key
        self.add_item(SetupChoiceSelect(selected_key=selected_key))

    @discord.ui.button(label="Use This Plan", emoji="✅", style=discord.ButtonStyle.success, custom_id="dank_setup_choice:publish", row=1)
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
                        "Saved **Choose Core Features**. Choose the core modules this server should use, then press **Continue Setup**."
                    ),
                )
            except Exception as e:
                embed = discord.Embed(
                    title="✅ Feature Choices Saved",
                    description=(
                        "Saved your feature choices, but the feature screen did not open.\n\n"
                        f"Error: `{type(e).__name__}: {str(e)[:220]}`\n\n"
                        "Nothing else was changed. Return to Setup Home and try again."
                    ),
                    color=discord.Color.orange(),
                    timestamp=now_utc(),
                )
                return await solid._edit_or_followup(interaction, embed=embed, view=ProductSetupHomeView())

        embed = build_setup_template_embed(selected_key=selected, guild_name=str(guild.name))
        embed.title = "✅ Setup Choice Saved"
        embed.description = (
            f"Saved **{choice.label}** for this server.\n\n"
            "Next, return to Quick Setup and continue one required step at a time."
        )
        embed.add_field(
            name="Next",
            value=(
                "Press **Continue Setup** on Setup Home. "
                "Dank Shield will show only the next thing you need to set up."
            ),
            inline=False,
        )
        await solid._edit_or_followup(interaction, embed=embed, view=ProductSetupHomeView())

    @discord.ui.button(label="Preview", emoji="👀", style=discord.ButtonStyle.secondary, custom_id="dank_setup_choice:preview", row=1)
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
            label="Continue Setup",
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
            label="Test Your Setup",
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


class SetupReviewHomeButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Setup Home",
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
    """After Setup Check, show only the next correct action and navigation."""

    def __init__(self, *, ready: bool) -> None:
        super().__init__(timeout=900)

        if ready:
            self.add_item(SetupReviewLaunchButton())
        else:
            self.add_item(SetupReviewFixNextButton())

        self.add_item(SetupReviewHomeButton())
        close_button = discord.ui.Button(
            label="Close",
            emoji="✖️",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_setup_review:close",
            row=2,
        )
        close_button.callback = self._close
        self.add_item(close_button)

    async def _close(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await _close_setup(interaction)

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


async def _close_setup(
    interaction: discord.Interaction,
) -> None:
    """Close the interactive setup message without changing configuration."""

    if not await solid._require_setup_permission(interaction):
        return

    embed = discord.Embed(
        title="Setup Closed",
        description=(
            "Nothing else was changed. Run `/dank setup` whenever "
            "you want to continue."
        ),
        color=discord.Color.dark_grey(),
        timestamp=now_utc(),
    )
    await solid._edit_or_followup(
        interaction,
        embed=embed,
        view=None,
    )


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
        title="⚡ Choose a Quick Setup Plan",
        description=(
            "Pick the closest goal. Dank Shield applies smart defaults and then asks only for missing essentials. "
            "You can change the plan or any AIO module later from **Manage Setup**."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )


    if not fresh.id_verify_allowed_for_guild(guild):
        embed.add_field(
            name="🔒 ID Verification",
            value=(
                "ID/Web Verify is only available for servers approved to use it, "
                "so those options are hidden here."
            ),
            inline=False,
        )

    embed.set_footer(
        text="Choose one option from the menu. Nothing is deleted."
    )

    await solid._edit_or_followup(
        interaction,
        embed=embed,
        view=fresh.SetupTypeChoiceView(guild=guild),
    )


async def _open_existing_server(
    interaction: discord.Interaction,
    *,
    parent: str = "features",
) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    embed = discord.Embed(
        title="🧭 Choose Existing Roles & Channels",
        description="Choose the roles, channels, and folders your server already uses. Dank Shield remembers the Discord items you pick.",
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(
        name="Choose These in Order",
        value="Choose only the section you need: roles, ticket folders, member channels, staff/log channels, or timers and rules.",
        inline=False,
    )
    from . import public_setup_full_customization as customization

    await interaction.response.edit_message(
        embed=embed,
        view=customization.FullChooseExistingView(parent=parent),
    )


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
            "❌ Creating missing setup items failed: "
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
            saved_message="Choose which features are ON or OFF.",
        )
    except Exception as e:
        embed = discord.Embed(
            title="Feature Settings Did Not Open",
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




async def _open_bot_access_check(
    interaction: discord.Interaction,
) -> None:
    """Open the read-only activity coverage access check."""

    if not await solid._require_setup_permission(interaction):
        return

    from stoney_verify import setup_activity_access

    await setup_activity_access.open_activity_access_check(interaction)


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
            "🛡️ Spam & Raid Protection opened from "
            "**All Features & Settings**."
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
        title="⏱️ Timers & Rules",
        description=(
            "Change verification timers, ticket names, "
            "and other setup rules. "
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
            "All Features & Settings • use Back to All Features to return"
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
        title="🧩 Features, Roles & Channels",
        description="Turn features on or off, change timers and rules, or choose different roles and channels.",
        items=(
            "🧩 **Turn Features On / Off** — choose which features this server uses.",
            "⏱️ **Timers & Rules** — change timers, names, and how setup actions work.",
            "🧭 **Choose Roles & Channels** — change which Discord roles and channels Dank Shield uses.",
        ),
        view=AdvancedCoreSetupView(),
    )


async def _open_advanced_member_experience(
    interaction: discord.Interaction,
) -> None:
    await _open_advanced_section(
        interaction,
        title="🎫 Tickets",
        description="Edit the choices members can select when they open a ticket.",
        items=(
            "🧾 **Ticket Choices** — edit what members can request.",
        ),
        view=AdvancedMemberExperienceView(),
    )



async def _open_advanced_verification(
    interaction: discord.Interaction,
) -> None:
    await _open_advanced_section(
        interaction,
        title="✅ Verification",
        description=(
            "Set up member access, Simple Verify, Voice Verify, and "
            "approved ID/Web verification options."
        ),
        items=(
            "✅ **Core Features** — turn Simple Verify or Voice Verify on or off.",
            "🧭 **Roles & Channels** — choose member roles and verification channels.",
            "⏱️ **Timers & Rules** — adjust verification timing and behavior.",
        ),
        view=AdvancedVerificationView(),
    )


async def _open_advanced_security(
    interaction: discord.Interaction,
) -> None:
    await _open_advanced_section(
        interaction,
        title="🛡️ Security & SpamGuard",
        description=(
            "Manage SpamGuard, raid protection, AntiNuke, bot access, "
            "and channel permission repairs."
        ),
        items=(
            "🛡️ **Protection Center** — SpamGuard, raid protection, and AntiNuke.",
            "🔐 **Check Bot Access** — find channels Dank Shield cannot inspect.",
            "🛠️ **Fix Channel Permissions** — preview and apply safe permission repairs.",
        ),
        view=AdvancedSecurityView(),
    )


async def _open_advanced_logs_activity(
    interaction: discord.Interaction,
) -> None:
    await _open_advanced_section(
        interaction,
        title="🧾 Logs & Activity",
        description=(
            "Choose what Dank Shield records and verify that activity tracking "
            "can see the channels it needs."
        ),
        items=(
            "🧾 **Choose What Gets Logged** — select moderation and server events.",
            "🔐 **Check Activity Access** — review activity-tracking coverage.",
            "🧭 **Log Channels** — choose where enabled logs are posted.",
        ),
        view=AdvancedLogsActivityView(),
    )

async def _open_config_history(
    interaction: discord.Interaction,
) -> None:
    from stoney_verify import config_history_ui

    await config_history_ui.open_config_history(interaction)


async def _open_advanced_appearance(
    interaction: discord.Interaction,
) -> None:
    await _open_advanced_section(
        interaction,
        title="🎨 Server Design",
        description="Change how the server looks, preview changes, or undo the last design change.",
        items=(
            "🎨 **Server Design** — fonts, frames, emojis, previews, and undo tools.",
        ),
        view=AdvancedAppearanceView(),
    )


async def _open_advanced_danger_zone(
    interaction: discord.Interaction,
) -> None:
    await _open_advanced_section(
        interaction,
        title="🧯 Repair or Restart Setup",
        description="Use this only if setup is broken or you want to start again.",
        items=(
            "🧯 **Repair or Restart** — repair setup or restart it safely.",
        ),
        view=AdvancedDangerZoneView(),
        danger=True,
    )


async def _open_advanced_settings(
    interaction: discord.Interaction,
) -> None:
    if not await solid._require_setup_permission(interaction):
        return

    embed = discord.Embed(
        title="🧰 All Features & Settings",
        description=(
            "Everything Dank Shield can configure is grouped by purpose below. "
            "The normal Quick Setup only asks for required items."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(
        name="🧩 Setup Plan & Server Items",
        value="Change core modules, roles, channels, folders, timers, and rules.",
        inline=False,
    )
    embed.add_field(
        name="🎫 Tickets",
        value="Ticket panels, staff routing, folders, and member ticket choices.",
        inline=False,
    )
    embed.add_field(
        name="✅ Verification",
        value="Simple Verify, Voice Verify, approved ID/Web flows, roles, and channels.",
        inline=False,
    )
    embed.add_field(
        name="🛡️ Security & SpamGuard",
        value="SpamGuard, raids, AntiNuke, access checks, and permission repairs.",
        inline=False,
    )
    embed.add_field(
        name="🧾 Logs & Activity",
        value="Logging choices, log channels, and activity-tracking access.",
        inline=False,
    )
    embed.add_field(
        name="🎨 Server Design",
        value="Smart Auto-Detect, previews, styling, and undo tools.",
        inline=False,
    )
    embed.add_field(
        name="💾 Backups & History",
        value="Back up selected configuration areas and restore only what you choose.",
        inline=False,
    )
    embed.set_footer(text="All Features & Settings • choose one category")

    await solid._edit_or_followup(
        interaction,
        embed=embed,
        view=AdvancedSettingsHubView(),
    )


async def _open_manage_setup(
    interaction: discord.Interaction,
) -> None:
    """Open the task-based management hub for the AIO bot."""

    if not await solid._require_setup_permission(interaction):
        return

    embed = discord.Embed(
        title="⚙️ Manage Setup",
        description=(
            "Use **Quick Setup** for the fastest guided path. Use this hub to "
            "change a plan, manage any AIO module, review problems, or repair setup."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(
        name="🧭 Change Setup Plan",
        value="Choose a different recommended plan or select your own core modules.",
        inline=False,
    )
    embed.add_field(
        name="🧰 All Features & Settings",
        value="Open Tickets, Verification, Security, Logs, Design, Backups, and more.",
        inline=False,
    )
    embed.add_field(
        name="🩺 Review Setup",
        value="See what is ready, optional, missing, or configured incorrectly.",
        inline=False,
    )
    embed.add_field(
        name="🧯 Repair or Restart Setup",
        value="Use recovery tools only when setup is broken or you intentionally want a reset.",
        inline=False,
    )
    embed.set_footer(text="Manage Setup • Quick Setup remains available from Setup Home")

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


async def _launch_state(guild: discord.Guild) -> dict[str, Any]:
    state = await load_setup_service_state(guild.id)
    return {
        "tickets": bool(state.tickets),
        "basic_verify": bool(state.simple_verify),
        "voice_verify": bool(state.voice_verify),
        "id_verify": bool(state.id_verify),
        "spam_guard": bool(state.spam_guard),
        "logs": bool(state.logs),
        "completed": bool(state.completed),
        "setup_choice": state.setup_choice,
        "setup_label": state.setup_label,
    }

def _launch_state_text(state: dict[str, Any]) -> str:
    lines: list[str] = []
    if state.get("tickets"):
        lines.append("🎫 **Tickets**")
    if state.get("basic_verify"):
        lines.append("✅ **Simple Verify**")
    if state.get("voice_verify"):
        lines.append("🎙️ **Voice Verify**")
    if state.get("id_verify"):
        lines.append("🪪 **ID/Web Verify**")
    if state.get("spam_guard"):
        lines.append("🛡️ **SpamGuard**")
    if state.get("logs"):
        lines.append("🧾 **Logs**")
    return "\n".join(lines) or "No features are enabled."

async def _open_test_launch(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

    await solid._safe_defer_update(interaction)
    target, _title, _explanation, _key = await _guided_setup_target(guild)
    if target != "ready":
        return await _open_health_check(interaction, already_deferred=True)

    state = await _launch_state(guild)
    actions: list[str] = []
    if state.get("tickets"):
        actions.append("Post the ticket panel and create one test ticket. Try the staff controls, then delete the test ticket.")
    if state.get("basic_verify"):
        actions.append("Post the Simple Verify panel and test it with a second account.")
    if state.get("voice_verify"):
        actions.append("Use a second account to request Voice Verify and confirm staff receive the request.")
    if state.get("id_verify"):
        actions.append("Test the private ID/Web flow with an approved staff test account.")
    if state.get("spam_guard"):
        actions.append("Review SpamGuard in a private test channel and confirm its actions appear in the configured log.")
    if state.get("logs"):
        actions.append("Confirm the test actions appear in the correct log channels.")

    numbered = "\n".join(f"{index}. {action}" for index, action in enumerate(actions, start=1))
    embed = discord.Embed(
        title="🧪 Setup Test Tools" if state.get("completed") else "🧪 Test Your Setup",
        description=(
            "Only features enabled for this server are shown below. Nothing is posted until you press a matching button."
        ),
        color=discord.Color.green(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Setup Plan", value=f"**{state.get('setup_label') or 'Current setup'}**", inline=False)
    embed.add_field(name="Enabled Features", value=_launch_state_text(state), inline=False)
    embed.add_field(name="Test These", value=numbered[:1024] or "Run Setup Check before testing.", inline=False)
    if not state.get("completed"):
        embed.add_field(
            name="When Everything Works",
            value=(
                "Press **Finish Setup**. Setup Home will then show **Setup finished** instead of sending you back here."
            ),
            inline=False,
        )
    embed.set_footer(text=f"Guild {guild.id} • enabled features only")
    await solid._edit_or_followup(interaction, embed=embed, view=LaunchTestView(state))


async def _finish_setup(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

    await solid._safe_defer_update(interaction)
    target, _title, _explanation, _key = await _guided_setup_target(guild)
    if target != "ready":
        return await _open_health_check(interaction, already_deferred=True)

    state = await mark_setup_completed(guild.id, actor=interaction.user)
    embed = discord.Embed(
        title="✅ Setup Finished",
        description=(
            "Dank Shield saved this setup as finished. Setup Home will no longer send you into the testing screen."
        ),
        color=discord.Color.green(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Enabled Features", value=_enabled_feature_text(state), inline=False)
    embed.add_field(
        name="Changing Something Later",
        value=(
            "Any future setup edit automatically changes this server back to **Needs review** until you test and finish it again."
        ),
        inline=False,
    )
    await solid._edit_or_followup(interaction, embed=embed, view=FinishedSetupView())


async def _open_completed_summary(interaction: discord.Interaction) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

    await solid._safe_defer_update(interaction)
    embed = await solid._build_current_setup_embed(guild)
    embed.title = "✅ Setup Summary"
    embed.description = (
        "This server is marked **Setup finished**. Use **Test Again** for the enabled test tools or **Manage Setup** to make changes."
    )
    await solid._edit_or_followup(interaction, embed=embed, view=FinishedSetupView())

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
                "You have not turned on any features yet."
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
                "Choose Simple Verify or Voice Verify."
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
                    "5. Return and press **Continue Setup**."
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
                "Return here and press **Continue Setup** again."
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
            full.RoleCustomizationPageOne(parent="guided")
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
        view = full.DiscordCategoryCustomizationView(parent="guided")

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
        view = full.ChannelCustomizationPageOne(parent="guided")

    elif target == "logs":
        embed = discord.Embed(
            title="🧾 Choose the Needed Log Channel",
            description=(
                "Choose where the enabled log feature should post."
            ),
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        view = full.LogStatusCustomizationView(parent="guided")

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
        title="⚡ Quick Setup",
        description=(
            "The fastest path: one required step at a time, with optional AIO tools kept out of the way."
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
        name="Do This Next",
        value=(
            f"**{title}**\n{explanation}"
        )[:1024],
        inline=False,
    )

    embed.add_field(
        name="Progress",
        value=(
            f"**{done}/{total} required steps complete**"
            if total
            else "Setup has not started yet."
        ),
        inline=False,
    )

    embed.set_footer(
        text=(
            f"Guild {guild.id} • guided setup"
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
    """Setup Home with one fast path, management, and a clean exit."""

    def __init__(
        self,
        *,
        ready: bool = False,
        started: bool = False,
        completed: bool = False,
    ) -> None:
        super().__init__(timeout=900)
        self.ready = bool(ready)
        self.started = bool(started)
        self.completed = bool(completed)

        if self.completed:
            self.continue_setup.label = "View Setup Summary"
            self.continue_setup.emoji = "✅"
        elif self.ready:
            self.continue_setup.label = "Test Your Setup"
            self.continue_setup.emoji = "🧪"
        elif self.started:
            self.continue_setup.label = "Continue Setup"
            self.continue_setup.emoji = "➡️"
        else:
            self.continue_setup.label = "Start Setup"
            self.continue_setup.emoji = "⚡"

    @discord.ui.button(
        label="Start Setup",
        emoji="⚡",
        style=discord.ButtonStyle.success,
        custom_id="dank_setup_home:continue",
        row=0,
    )
    async def continue_setup(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if self.completed:
            await _open_completed_summary(interaction)
            return
        if self.ready:
            await _open_test_launch(interaction)
            return
        if self.started:
            await _open_guided_setup(interaction)
            return
        await _open_choose_setup_type(interaction)

    @discord.ui.button(
        label="Manage Setup",
        emoji="⚙️",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_home:manage",
        row=1,
    )
    async def more_options(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _open_manage_setup(interaction)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_home:close",
        row=1,
    )
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _close_setup(interaction)


class ContinueSetupView(discord.ui.View):
    """The Quick Setup path: perform one action, go home, or close."""

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

    @discord.ui.button(
        label="Set Up This Step",
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
        _ = button
        await _open_guided_target(
            interaction,
            self.target,
            self.requirement_key,
        )

    @discord.ui.button(
        label="Setup Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_guided:home",
        row=1,
    )
    async def home(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _home_edit(interaction)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_guided:close",
        row=1,
    )
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _close_setup(interaction)


class ManageSetupView(discord.ui.View):
    """Task-based AIO setup management hub."""

    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(
        label="Change Setup Plan",
        emoji="🧭",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_manage:plan",
        row=0,
    )
    async def change_type(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _open_choose_setup_type(interaction)

    @discord.ui.button(
        label="All Features & Settings",
        emoji="🧰",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_manage:features",
        row=0,
    )
    async def advanced_settings(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _open_advanced_settings(interaction)

    @discord.ui.button(
        label="Review Setup",
        emoji="🩺",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_manage:review",
        row=1,
    )
    async def health(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _open_health_check(interaction)

    @discord.ui.button(
        label="Repair or Restart Setup",
        emoji="🧯",
        style=discord.ButtonStyle.danger,
        custom_id="dank_setup_manage:repair",
        row=1,
    )
    async def recovery(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _open_advanced_danger_zone(interaction)

    @discord.ui.button(
        label="Help",
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
        _ = button
        if not await solid._require_setup_permission(interaction):
            return
        await interaction.response.edit_message(
            embed=_build_setup_help_embed(),
            view=ManageSetupView(),
        )

    @discord.ui.button(
        label="Setup Home",
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
        _ = button
        await _home_edit(interaction)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_manage:close",
        row=3,
    )
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _close_setup(interaction)


class AdvancedSettingsHubView(discord.ui.View):
    """AIO feature categories with predictable navigation."""

    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(
        label="Setup Plan & Server Items",
        emoji="🧩",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_features:core",
        row=0,
    )
    async def core(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_advanced_core_setup(interaction)

    @discord.ui.button(
        label="Tickets",
        emoji="🎫",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_features:tickets",
        row=0,
    )
    async def tickets(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_advanced_member_experience(interaction)

    @discord.ui.button(
        label="Verification",
        emoji="✅",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_features:verification",
        row=1,
    )
    async def verification(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_advanced_verification(interaction)

    @discord.ui.button(
        label="Security & SpamGuard",
        emoji="🛡️",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_features:security",
        row=1,
    )
    async def security(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_advanced_security(interaction)

    @discord.ui.button(
        label="Logs & Activity",
        emoji="🧾",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_features:logs",
        row=2,
    )
    async def logs_activity(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_advanced_logs_activity(interaction)

    @discord.ui.button(
        label="Server Design",
        emoji="🎨",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_features:design",
        row=2,
    )
    async def design(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_advanced_appearance(interaction)

    @discord.ui.button(
        label="Backups & History",
        emoji="💾",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_features:history",
        row=3,
    )
    async def history(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _open_config_history(interaction)

    @discord.ui.button(
        label="Back to Manage Setup",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_features:back",
        row=4,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_manage_setup(interaction)

    @discord.ui.button(
        label="Setup Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_features:home",
        row=4,
    )
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _home_edit(interaction)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_features:close",
        row=4,
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)


class AdvancedCoreSetupView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Choose Core Modules", emoji="🧩", style=discord.ButtonStyle.primary, custom_id="dank_setup_core:services", row=0)
    async def services(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_services(interaction)

    @discord.ui.button(label="Timers & Rules", emoji="⏱️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_core:timers", row=0)
    async def timers_behavior(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_timers_behavior(interaction)

    @discord.ui.button(label="Choose Roles & Channels", emoji="🧭", style=discord.ButtonStyle.secondary, custom_id="dank_setup_core:mapping", row=1)
    async def detailed_mapping(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_existing_server(interaction, parent="core")

    @discord.ui.button(label="Back to All Features", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_core:back", row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_advanced_settings(interaction)

    @discord.ui.button(label="Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_core:home", row=2)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _home_edit(interaction)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_core:close", row=2)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)


class AdvancedMemberExperienceView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Ticket Choices", emoji="🧾", style=discord.ButtonStyle.primary, custom_id="dank_setup_tickets:choices", row=0)
    async def ticket_choices(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_ticket_menu(interaction)

    @discord.ui.button(label="Roles & Channels", emoji="🧭", style=discord.ButtonStyle.secondary, custom_id="dank_setup_tickets:mapping", row=0)
    async def mapping(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_existing_server(interaction, parent="tickets")

    @discord.ui.button(label="Timers & Rules", emoji="⏱️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_tickets:rules", row=1)
    async def rules(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_timers_behavior(interaction)

    @discord.ui.button(label="Back to All Features", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_tickets:back", row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_advanced_settings(interaction)

    @discord.ui.button(label="Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_tickets:home", row=2)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _home_edit(interaction)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_tickets:close", row=2)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)


class AdvancedVerificationView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Choose Core Modules", emoji="✅", style=discord.ButtonStyle.primary, custom_id="dank_setup_verify:features", row=0)
    async def features(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_services(interaction)

    @discord.ui.button(label="Roles & Channels", emoji="🧭", style=discord.ButtonStyle.primary, custom_id="dank_setup_verify:mapping", row=0)
    async def mapping(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_existing_server(interaction, parent="verification")

    @discord.ui.button(label="Timers & Rules", emoji="⏱️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_verify:rules", row=1)
    async def rules(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_timers_behavior(interaction)

    @discord.ui.button(label="Back to All Features", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_verify:back", row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_advanced_settings(interaction)

    @discord.ui.button(label="Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_verify:home", row=2)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _home_edit(interaction)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_verify:close", row=2)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)


class AdvancedSecurityView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Protection Center", emoji="🛡️", style=discord.ButtonStyle.primary, custom_id="dank_setup_security:protection", row=0)
    async def protection(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_protection_options(interaction)

    @discord.ui.button(label="Check Bot Access", emoji="🔐", style=discord.ButtonStyle.secondary, custom_id="dank_setup_security:access", row=0)
    async def bot_access(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_bot_access_check(interaction)

    @discord.ui.button(label="Fix Channel Permissions", emoji="🛠️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_security:repair", row=1)
    async def permission_repair(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_permission_repair(interaction)

    @discord.ui.button(label="Back to All Features", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_security:back", row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_advanced_settings(interaction)

    @discord.ui.button(label="Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_security:home", row=2)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _home_edit(interaction)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_security:close", row=2)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)


class AdvancedLogsActivityView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Choose What Gets Logged", emoji="🧾", style=discord.ButtonStyle.primary, custom_id="dank_setup_logs:events", row=0)
    async def modlog_tracking(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_modlog_tracking(interaction)

    @discord.ui.button(label="Check Activity Access", emoji="🔐", style=discord.ButtonStyle.secondary, custom_id="dank_setup_logs:access", row=0)
    async def bot_access(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_bot_access_check(interaction)

    @discord.ui.button(label="Log Channels", emoji="🧭", style=discord.ButtonStyle.secondary, custom_id="dank_setup_logs:channels", row=1)
    async def channels(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_existing_server(interaction, parent="logs")

    @discord.ui.button(label="Back to All Features", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_logs:back", row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_advanced_settings(interaction)

    @discord.ui.button(label="Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_logs:home", row=2)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _home_edit(interaction)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_logs:close", row=2)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)


class AdvancedAppearanceView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(
        label="Open Server Design",
        emoji="🎨",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_design:open",
        row=0,
    )
    async def server_design(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await solid._require_setup_permission(interaction):
            return
        from . import public_design_bridge
        await public_design_bridge.open_design_studio_from_setup(interaction)

    @discord.ui.button(label="Back to All Features", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_design:back", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_advanced_settings(interaction)

    @discord.ui.button(label="Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_design:home", row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _home_edit(interaction)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_design:close", row=1)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)


class AdvancedDangerZoneView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Open Repair & Restart Tools", emoji="🧯", style=discord.ButtonStyle.danger, custom_id="dank_setup_repair:open", row=0)
    async def recovery(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_recovery_center(interaction)

    @discord.ui.button(label="Back to Manage Setup", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_repair:back", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_manage_setup(interaction)

    @discord.ui.button(label="Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_repair:home", row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _home_edit(interaction)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_repair:close", row=1)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)

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
                    "Created from the canonical /dank setup test screen"
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
    """Render test actions only for enabled features."""

    def __init__(self, state: Optional[dict[str, Any]] = None) -> None:
        super().__init__(timeout=900)
        self.state = dict(state or {})
        actions: list[tuple[str, str, discord.ButtonStyle, str, Any]] = []

        if self.state.get("tickets"):
            actions.extend([
                ("Post Ticket Panel", "🎫", discord.ButtonStyle.success, "dank_setup_test:ticket_panel", self._post_ticket_panel),
                ("Create Test Ticket", "🧪", discord.ButtonStyle.success, "dank_setup_test:test_ticket", self._create_test_ticket),
            ])
        if self.state.get("basic_verify"):
            actions.append(("Post Simple Verify Panel", "✅", discord.ButtonStyle.success, "dank_setup_test:verify_panel", self._post_basic_verify))
        if not self.state.get("completed"):
            actions.append(("Finish Setup", "🏁", discord.ButtonStyle.primary, "dank_setup_test:finish", self._finish))
        actions.extend([
            ("Review Setup", "🩺", discord.ButtonStyle.secondary, "dank_setup_test:review", self._review),
            ("Setup Home", "🏠", discord.ButtonStyle.secondary, "dank_setup_test:home", self._home),
            ("Close", "✖️", discord.ButtonStyle.secondary, "dank_setup_test:close", self._close),
        ])

        for index, (label, emoji, style, custom_id, callback) in enumerate(actions):
            button = discord.ui.Button(
                label=label,
                emoji=emoji,
                style=style,
                custom_id=custom_id,
                row=min(4, index // 2),
            )
            button.callback = callback
            self.add_item(button)

    async def _post_ticket_panel(self, interaction: discord.Interaction) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        state = await _launch_state(guild)
        if not state.get("tickets"):
            return await interaction.response.send_message("🎫 Tickets are OFF. Open **Manage Setup** to turn them on.", ephemeral=True)
        try:
            from .public_ticket_panel_commands import post_ticket_panel_callback
            await post_ticket_panel_callback(interaction)
        except Exception as exc:
            await interaction.response.send_message(
                "❌ Could not post the ticket panel: " f"`{type(exc).__name__}: {str(exc)[:220]}`",
                ephemeral=True,
            )

    async def _post_basic_verify(self, interaction: discord.Interaction) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        state = await _launch_state(guild)
        if not state.get("basic_verify"):
            return await interaction.response.send_message("✅ Simple Verify is OFF. Open **Manage Setup** to turn it on.", ephemeral=True)
        try:
            from .public_verify_basic_panel import verify_panel
            await verify_panel(interaction)
        except Exception as exc:
            await interaction.response.send_message(
                "❌ Could not post the Simple Verify panel: " f"`{type(exc).__name__}: {str(exc)[:220]}`",
                ephemeral=True,
            )

    async def _create_test_ticket(self, interaction: discord.Interaction) -> None:
        await _create_setup_test_ticket(interaction)

    async def _finish(self, interaction: discord.Interaction) -> None:
        await _finish_setup(interaction)

    async def _review(self, interaction: discord.Interaction) -> None:
        await _open_health_check(interaction)

    async def _home(self, interaction: discord.Interaction) -> None:
        await _home_edit(interaction)

    async def _close(self, interaction: discord.Interaction) -> None:
        await _close_setup(interaction)


class FinishedSetupView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Test Again", emoji="🧪", style=discord.ButtonStyle.secondary, custom_id="dank_setup_finished:test", row=0)
    async def test_again(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_test_launch(interaction)

    @discord.ui.button(label="Manage Setup", emoji="⚙️", style=discord.ButtonStyle.primary, custom_id="dank_setup_finished:manage", row=0)
    async def edit_setup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_manage_setup(interaction)

    @discord.ui.button(label="Setup Home", emoji="🏠", style=discord.ButtonStyle.secondary, custom_id="dank_setup_finished:home", row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _home_edit(interaction)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_finished:close", row=1)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)

def register_public_setup_recommend_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    print("✅ public_setup_recommend: canonical /dank setup UX ready")


__all__ = ["register_public_setup_recommend_commands"]
