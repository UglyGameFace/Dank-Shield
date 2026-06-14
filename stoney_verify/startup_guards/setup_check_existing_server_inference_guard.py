from __future__ import annotations

"""Keep the plain /dank setup check honest for existing servers.

Older servers may not have a saved setup type. This guard can infer the setup
shape from saved roles/channels, but the plain checklist must not claim the
server is safe unless the canonical safety health check also passes.
"""

from typing import Any, Tuple

import discord


def _log(message: str) -> None:
    try:
        print(f"✅ setup_check_existing_server_inference_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ setup_check_existing_server_inference_guard: {message}")
    except Exception:
        pass


def _cfg_value(recommend: Any, cfg: Any, key: str, default: Any = None) -> Any:
    try:
        return recommend._cfg_value(cfg, key, default)
    except Exception:
        try:
            if hasattr(cfg, "get"):
                value = cfg.get(key)
                return default if value is None else value
        except Exception:
            pass
    return default


def _attr_id(recommend: Any, cfg: Any, name: str) -> int:
    try:
        return int(_cfg_value(recommend, cfg, name, 0) or 0)
    except Exception:
        return 0


def _has_role(recommend: Any, guild: discord.Guild, cfg: Any, key: str) -> bool:
    try:
        return guild.get_role(_attr_id(recommend, cfg, key)) is not None
    except Exception:
        return False


def _has_channel(recommend: Any, guild: discord.Guild, cfg: Any, *keys: str) -> bool:
    for key in keys:
        try:
            if guild.get_channel(_attr_id(recommend, cfg, key)) is not None:
                return True
        except Exception:
            pass
    return False


def _ticket_core_saved(recommend: Any, guild: discord.Guild, cfg: Any) -> bool:
    return (
        _has_role(recommend, guild, cfg, "staff_role_id")
        and _has_channel(recommend, guild, cfg, "ticket_category_id")
        and _has_channel(recommend, guild, cfg, "ticket_panel_channel_id", "support_channel_id")
    )


def _verification_saved(recommend: Any, guild: discord.Guild, cfg: Any) -> bool:
    return (
        _has_channel(recommend, guild, cfg, "verify_channel_id")
        and _has_role(recommend, guild, cfg, "verified_role_id")
    )


def _voice_saved(recommend: Any, guild: discord.Guild, cfg: Any) -> bool:
    return _has_channel(recommend, guild, cfg, "vc_verify_channel_id") or _has_channel(
        recommend,
        guild,
        cfg,
        "vc_verify_queue_channel_id",
        "vc_queue_channel_id",
        "vc_request_channel_id",
        "vc_verify_requests_channel_id",
    )


def _explicit_choice(recommend: Any, cfg: Any) -> str:
    return str(_cfg_value(recommend, cfg, "setup_choice", "") or "").strip()


def _choice_label(recommend: Any, key: str) -> str:
    try:
        choice = recommend.get_setup_template(key)
        if choice is not None:
            return str(choice.label)
    except Exception:
        pass
    labels = {
        "help_desk": "Help desk",
        "id_voice_check": "ID + voice check",
        "id_check": "ID check",
        "voice_check": "Voice check",
        "basic_server": "Basic server",
        "custom_setup": "Custom setup",
    }
    return labels.get(str(key or ""), "Existing Server")


def _infer_choice(recommend: Any, guild: discord.Guild, cfg: Any) -> Tuple[str, str, bool]:
    explicit = _explicit_choice(recommend, cfg)
    if explicit:
        return explicit, _choice_label(recommend, explicit), False

    if not _ticket_core_saved(recommend, guild, cfg):
        return "", "Not chosen yet", False

    has_verification = _verification_saved(recommend, guild, cfg)
    has_voice = _voice_saved(recommend, guild, cfg)
    if has_verification and has_voice:
        return "id_voice_check", "ID + voice check (inferred from saved setup)", True
    if has_verification:
        return "id_check", "ID check (inferred from saved setup)", True
    if has_voice:
        return "voice_check", "Voice check (inferred from saved setup)", True
    return "help_desk", "Help desk (inferred from saved setup)", True


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


async def _category_rows(recommend: Any, solid: Any, guild: discord.Guild) -> tuple[int, str]:
    try:
        category_load = await solid._category_load(guild)
        if category_load.error:
            return 0, "Ticket menu options could not be checked. Press **Ticket Menu Options** and create recommended options."
        return len(category_load.rows or []), ""
    except Exception:
        return 0, "Ticket menu options could not be checked right now."


def _needs_id_check(recommend: Any, cfg: Any, inferred_key: str) -> bool:
    if inferred_key in {"id_check", "id_voice_check"}:
        return True
    try:
        return bool(recommend._needs_id_check(cfg))
    except Exception:
        return False


def _needs_voice_check(recommend: Any, cfg: Any, inferred_key: str) -> bool:
    if inferred_key in {"voice_check", "id_voice_check"}:
        return True
    try:
        return bool(recommend._needs_voice_check(cfg))
    except Exception:
        return False


async def _setup_progress(recommend: Any, guild: discord.Guild) -> tuple[str, int, int, str]:
    done = 0
    total = 0
    lines: list[str] = []
    next_step = "Choose the setup type that best matches this server."

    try:
        cfg = await recommend.get_guild_config(guild.id, refresh=True)
    except Exception as exc:
        return (f"🚫 Saved setup could not load: `{type(exc).__name__}: {str(exc)[:180]}`", 0, 1, "Fix Supabase/config loading first.")

    inferred_key, inferred_label, inferred = _infer_choice(recommend, guild, cfg)

    def check(label: str, ok: bool, fail_hint: str, *, optional: bool = False) -> None:
        nonlocal done, total, next_step
        if optional:
            if ok:
                lines.append(f"✅ {label}")
            else:
                lines.append(f"⚠️ {label}: {fail_hint}")
            return
        total += 1
        if ok:
            done += 1
            lines.append(f"✅ {label}")
        else:
            lines.append(f"⚠️ {label}: {fail_hint}")
            if next_step == "Choose the setup type that best matches this server.":
                next_step = fail_hint

    check("Setup type", bool(inferred_key), "Press Choose Setup Type.")
    if inferred:
        lines[-1] = f"✅ Setup type: {inferred_label}"

    bot_member = getattr(guild, "me", None)
    bot_perms = getattr(bot_member, "guild_permissions", None)
    check("Bot permissions", bool(bot_perms and bot_perms.manage_channels and bot_perms.manage_roles and bot_perms.send_messages), "Give the bot Manage Channels, Manage Roles, Send Messages, Embed Links, and Attach Files.")

    check("Ticket staff role", _has_role(recommend, guild, cfg, "staff_role_id"), "Use My Existing Server → Ticket Basics → Ticket staff role.")
    check("Open ticket folder", _has_channel(recommend, guild, cfg, "ticket_category_id"), "Use My Existing Server → Ticket Basics → Open ticket folder.")
    check("Closed ticket folder", _has_channel(recommend, guild, cfg, "ticket_archive_category_id", "archive_category_id"), "Optional: pick a closed/archive ticket folder later.", optional=True)
    check("Transcript channel", _has_channel(recommend, guild, cfg, "transcripts_channel_id"), "Optional: pick a transcript text channel later.", optional=True)
    check("Public ticket panel channel", _has_channel(recommend, guild, cfg, "ticket_panel_channel_id", "support_channel_id"), "Use My Existing Server → Ticket Basics → public ticket panel channel.")

    needs_id = _needs_id_check(recommend, cfg, inferred_key)
    needs_voice = _needs_voice_check(recommend, cfg, inferred_key)
    if needs_id or needs_voice:
        check("Verify text channel", _has_channel(recommend, guild, cfg, "verify_channel_id"), "Use My Existing Server → Verification Channels → Verify text channel.")
        check("Approved role", _has_role(recommend, guild, cfg, "verified_role_id"), "Use My Existing Server → Access Roles → Approved role.")
        check("New/waiting role", _has_role(recommend, guild, cfg, "unverified_role_id"), "Optional: pick a New/waiting role later.", optional=True)
    if needs_voice:
        check("Voice check channel", _has_channel(recommend, guild, cfg, "vc_verify_channel_id"), "Use My Existing Server → Voice Verification → Voice check channel.")
        check("Voice check request channel", _has_channel(recommend, guild, cfg, "vc_verify_queue_channel_id", "vc_queue_channel_id", "vc_request_channel_id", "vc_verify_requests_channel_id"), "Optional: pick a voice-check request channel later.", optional=True)

    check("Modlog channel", _has_channel(recommend, guild, cfg, "modlog_channel_id", "raidlog_channel_id"), "Use My Existing Server → Logs + Status → Modlog channel.")

    rows, error = await _category_rows(recommend, recommend.solid, guild)
    total += 1
    if error:
        lines.append(f"⚠️ Ticket menu: {error}")
        if next_step == "Choose the setup type that best matches this server.":
            next_step = "Press Ticket Menu Options → Create Recommended Ticket Menu."
    elif rows:
        done += 1
        lines.append(f"✅ Ticket menu: {rows} option(s) configured")
    else:
        lines.append("⚠️ Ticket menu: no options yet")
        if next_step == "Choose the setup type that best matches this server.":
            next_step = "Press Ticket Menu Options → Create Recommended Ticket Menu."

    if done == total:
        next_step = "Run Setup Health/Safety, then post your ticket panel and open a test ticket."
    return "\n".join(lines)[:1024], done, total, next_step


def _append_unique(dest: list[str], src: list[str], *, prefix: str = "") -> None:
    existing = {str(x).strip() for x in dest}
    for item in src:
        text = str(item).strip()
        if not text:
            continue
        if prefix and not text.startswith(prefix):
            text = f"{prefix}{text}"
        if text not in existing:
            dest.append(text)
            existing.add(text)


def _canonical_health(recommend: Any, guild: discord.Guild, cfg: Any) -> tuple[list[str], list[str], list[str]]:
    try:
        from stoney_verify.startup_guards import setup_visibility_health_guard
        setup_visibility_health_guard.apply()
    except Exception:
        pass
    try:
        from stoney_verify.commands_ext import public_setup_group as group
        full = getattr(group, "_build_setup_health", None)
        if callable(full):
            return full(guild, cfg)
    except Exception as exc:
        return [], [f"Full safety health overlay could not run: {type(exc).__name__}."], []
    return [], ["Full safety health overlay is unavailable; do not treat this basic check as final."], []


async def _build_plain_setup_health_embed(recommend: Any, guild: discord.Guild) -> discord.Embed:
    blockers: list[str] = []
    warnings: list[str] = []
    passing: list[str] = []

    try:
        cfg = await recommend.get_guild_config(guild.id, refresh=True)
    except Exception as exc:
        embed = discord.Embed(title="🩺 Setup Check", description="🚫 I could not read this server's saved setup yet.", color=discord.Color.red(), timestamp=recommend.now_utc())
        embed.add_field(name="How to fix", value=f"Check Supabase/config first. Error: `{type(exc).__name__}`", inline=False)
        return embed

    inferred_key, choice_label, inferred = _infer_choice(recommend, guild, cfg)
    needs_id = _needs_id_check(recommend, cfg, inferred_key)
    needs_voice = _needs_voice_check(recommend, cfg, inferred_key)

    if inferred_key:
        passing.append(f"Setup type {'inferred' if inferred else 'chosen'}: **{choice_label}**")
    else:
        blockers.append("Choose a setup type first, or finish Ticket Basics so Dank Shield can infer this is an existing-server setup.")

    bot_member = getattr(guild, "me", None)
    bot_perms = getattr(bot_member, "guild_permissions", None)
    if bot_perms and bot_perms.manage_channels and bot_perms.manage_roles and bot_perms.send_messages:
        passing.append("Bot has the basic server permissions it needs.")
    else:
        blockers.append("Give the bot **Manage Channels**, **Manage Roles**, **Send Messages**, **Embed Links**, and **Attach Files**.")

    if _has_role(recommend, guild, cfg, "staff_role_id"):
        passing.append("Ticket staff role is chosen.")
    else:
        blockers.append("Choose the role that can answer tickets.")

    if _has_channel(recommend, guild, cfg, "ticket_category_id"):
        passing.append("Open ticket folder is chosen.")
    else:
        blockers.append("Choose where new tickets should open.")

    if _has_channel(recommend, guild, cfg, "ticket_archive_category_id", "archive_category_id"):
        passing.append("Closed ticket folder is chosen.")
    else:
        warnings.append("Closed ticket folder is optional. Pick one later if you want closed tickets separated.")

    if _has_channel(recommend, guild, cfg, "transcripts_channel_id"):
        passing.append("Transcript channel is chosen.")
    else:
        warnings.append("Transcript channel is optional. Pick one later if you want transcripts posted to a channel.")

    if _has_channel(recommend, guild, cfg, "ticket_panel_channel_id", "support_channel_id"):
        passing.append("Public ticket panel channel is chosen.")
    else:
        blockers.append("Choose where members should click to open tickets.")

    if needs_id or needs_voice:
        if _has_channel(recommend, guild, cfg, "verify_channel_id"):
            passing.append("Verify text channel is chosen.")
        else:
            blockers.append("Choose the text channel where members start verification.")
        if _has_role(recommend, guild, cfg, "verified_role_id"):
            passing.append("Approved role is chosen.")
        else:
            blockers.append("Choose the role members get after they are approved.")
        if _has_role(recommend, guild, cfg, "unverified_role_id"):
            passing.append("New/waiting role is chosen.")
        else:
            warnings.append("New/waiting role is optional unless your server locks new members before approval.")
    if needs_voice:
        if _has_channel(recommend, guild, cfg, "vc_verify_channel_id"):
            passing.append("Voice check channel is chosen.")
        else:
            blockers.append("Choose the voice channel used for voice checks.")
        if _has_channel(recommend, guild, cfg, "vc_verify_queue_channel_id", "vc_queue_channel_id", "vc_request_channel_id", "vc_verify_requests_channel_id"):
            passing.append("Voice check request channel is chosen.")
        else:
            warnings.append("Voice check request channel is optional, but staff may miss voice-check requests without it.")

    if _has_channel(recommend, guild, cfg, "modlog_channel_id", "raidlog_channel_id"):
        passing.append("Log channel is chosen.")
    else:
        warnings.append("Log channel is optional, but recommended.")

    control_role_keys = ("server_control_role_id", "control_role_id", "perm_role_id")
    has_saved_control_id = any(_attr_id(recommend, cfg, key) > 0 for key in control_role_keys)
    has_control_role = any(guild.get_role(_attr_id(recommend, cfg, key)) is not None for key in control_role_keys)
    if has_saved_control_id and not has_control_role:
        warnings.append("An old owner/admin role choice is saved but no longer exists. You can pick a new one later or ignore it if your server does not use that feature.")
    elif has_control_role:
        passing.append("Optional owner/admin role is chosen.")

    rows, error = await _category_rows(recommend, recommend.solid, guild)
    if error:
        warnings.append(error)
    elif rows:
        passing.append(f"Ticket menu has {rows} option(s).")
    else:
        blockers.append("Create at least one ticket menu option.")

    full_blockers, full_warnings, full_ok = _canonical_health(recommend, guild, cfg)
    _append_unique(blockers, full_blockers, prefix="Safety: ")
    _append_unique(warnings, full_warnings, prefix="Safety: ")
    for line in full_ok:
        text = str(line).strip()
        if "visibility" in text.lower() or "privacy" in text.lower() or "vc" in text.lower() or "permission" in text.lower():
            _append_unique(passing, [text], prefix="Safety: ")

    ready = not blockers and not warnings
    if blockers:
        desc = "🚫 **Setup is not safe yet. Fix blockers before testing with members.**"
        color = discord.Color.red()
    elif warnings:
        desc = "⚠️ **Core setup exists, but warnings remain. Review before calling this production-ready.**"
        color = discord.Color.gold()
    else:
        desc = "✅ **Ready to test.** Full safety health did not report blockers or warnings."
        color = discord.Color.green()
    embed = discord.Embed(
        title="🩺 Setup Safety Check",
        description=desc,
        color=color,
        timestamp=recommend.now_utc(),
    )
    embed.add_field(name="Setup Type", value=f"**{choice_label}**", inline=False)
    embed.add_field(name="Needs Fixing", value=_plain_lines(blockers, empty="✅ Nothing required is missing."), inline=False)
    embed.add_field(name="Looks Good", value=_plain_lines(passing, empty="No passing checks yet."), inline=False)
    embed.add_field(name="Warnings / Review", value=_plain_lines(warnings, empty="✅ No warnings."), inline=False)
    embed.add_field(
        name="How to fix this",
        value=(
            "Press **Safety & Repair → Fix Permissions** for scoped permission repairs.\n"
            "Press **Use My Existing Server** to pick roles/channels you already have.\n"
            "Press **Create Missing Items** if you want Dank Shield to create missing basics.\n"
            "Run this check again after every repair."
        ),
        inline=False,
    )
    embed.set_footer(text=f"Guild {guild.id} • /dank setup • full safety overlay included • no raw IDs shown")
    return embed


def _saved_choice_text(recommend: Any, cfg: Any) -> str:
    label = str(_cfg_value(recommend, cfg, "setup_choice_label", "") or "").strip()
    key = _explicit_choice(recommend, cfg)
    if label:
        return f"✅ Saved setup choice: **{label}**"
    if key:
        return f"✅ Saved setup choice: **{_choice_label(recommend, key)}**"
    return "ℹ️ No setup type was manually chosen yet. Health Check can infer Existing Server when roles/channels are already saved."


def _setup_choice_label(recommend: Any, cfg: Any) -> str:
    label = str(_cfg_value(recommend, cfg, "setup_choice_label", "") or "").strip()
    if label:
        return label
    key = _explicit_choice(recommend, cfg)
    if key:
        return _choice_label(recommend, key)
    return "Not chosen yet"


def apply() -> bool:
    try:
        from ..commands_ext import public_setup_recommend as recommend
    except Exception as exc:
        _warn(f"could not import public_setup_recommend: {exc!r}")
        return False

    try:
        recommend._setup_progress = lambda guild: _setup_progress(recommend, guild)
        recommend._build_plain_setup_health_embed = lambda guild: _build_plain_setup_health_embed(recommend, guild)
        recommend._saved_choice_text = lambda cfg: _saved_choice_text(recommend, cfg)
        recommend._setup_choice_label = lambda cfg: _setup_choice_label(recommend, cfg)
        setattr(recommend, "_SETUP_CHECK_EXISTING_SERVER_INFERENCE_GUARD", True)
        _log("patched /dank setup health check setup-type inference with full safety overlay")
        return True
    except Exception as exc:
        _warn(f"patch failed: {exc!r}")
        return False


apply()

__all__ = ["apply"]
