from __future__ import annotations

"""
Setup role safety patch.

Purpose:
Discord's role creation UI offers friendly presets such as Cosmetic, Member,
Moderator, and Manager. Those presets are fine for normal Discord management,
but Stoney Verify setup roles should be safer:

- Bot-created default roles should be plain roles with no server-level powers.
- Verification/member roles must not accidentally have Moderator/Manager/Admin
  powers because the bot may grant them to normal users.
- Ticket/support/control roles can be powerful if the owner chose that on
  purpose, but setup should warn clearly when they look like Discord Moderator
  or Manager presets.

This patch adds role-safety blockers/warnings to setup validation without
breaking existing per-guild config behavior.
"""

import builtins
import sys
from typing import Any, Iterable, Optional

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED: set[str] = set()

_DANGEROUS_FOR_PUBLIC_ROLES = (
    ("administrator", "Administrator"),
    ("manage_guild", "Manage Server"),
    ("manage_roles", "Manage Roles"),
    ("manage_channels", "Manage Channels"),
    ("manage_webhooks", "Manage Webhooks"),
    ("ban_members", "Ban Members"),
    ("kick_members", "Kick Members"),
    ("moderate_members", "Moderate Members"),
    ("mention_everyone", "Mention Everyone"),
)

_POWERFUL_STAFF_PERMS = (
    ("administrator", "Administrator"),
    ("manage_guild", "Manage Server"),
    ("manage_roles", "Manage Roles"),
    ("manage_channels", "Manage Channels"),
    ("manage_webhooks", "Manage Webhooks"),
    ("ban_members", "Ban Members"),
    ("kick_members", "Kick Members"),
    ("moderate_members", "Moderate Members"),
    ("manage_messages", "Manage Messages"),
    ("mention_everyone", "Mention Everyone"),
)


def _log(message: str) -> None:
    try:
        print(f"🧷 runtime_setup_role_safety {message}")
    except Exception:
        pass


def _role_id(role: Any) -> int:
    try:
        return int(getattr(role, "id", 0) or 0)
    except Exception:
        return 0


def _mention(role: Any) -> str:
    try:
        return str(getattr(role, "mention", None) or getattr(role, "name", None) or role)
    except Exception:
        return "unknown role"


def _perm_names(role: Any, spec: Iterable[tuple[str, str]]) -> list[str]:
    out: list[str] = []
    try:
        perms = getattr(role, "permissions", None)
        if perms is None:
            return out
        for attr, label in spec:
            try:
                if bool(getattr(perms, attr, False)):
                    out.append(label)
            except Exception:
                continue
    except Exception:
        pass
    return out


def _looks_like_discord_preset(role: Any) -> str:
    try:
        perms = getattr(role, "permissions", None)
        if perms is None:
            return "unknown"
        if bool(getattr(perms, "administrator", False)) or bool(getattr(perms, "manage_roles", False)) or bool(getattr(perms, "manage_channels", False)):
            return "Manager/Admin-style"
        if bool(getattr(perms, "ban_members", False)) or bool(getattr(perms, "kick_members", False)) or bool(getattr(perms, "moderate_members", False)):
            return "Moderator-style"
        if bool(getattr(perms, "send_messages", False)) or bool(getattr(perms, "view_channel", False)):
            return "Member-style"
        return "Cosmetic/plain"
    except Exception:
        return "unknown"


def _bot_member(guild: Any) -> Optional[Any]:
    try:
        return getattr(guild, "me", None)
    except Exception:
        return None


def _role_is_above_bot(guild: Any, role: Any) -> bool:
    try:
        me = _bot_member(guild)
        if me is None:
            return False
        return bool(role >= getattr(me, "top_role", role) and int(getattr(guild, "owner_id", 0) or 0) != int(getattr(me, "id", 0) or 0))
    except Exception:
        return False


def _add_unique(target: list[str], items: Iterable[str]) -> None:
    seen = set(target)
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            target.append(text)
            seen.add(text)


def _role_safety_notes(
    guild: Any,
    role: Any,
    *,
    label: str,
    grantable_to_members: bool = False,
    setup_control_or_staff: bool = False,
) -> tuple[list[str], list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []

    if role is None:
        return blockers, warnings, ok

    try:
        if bool(role.is_default()):
            blockers.append(f"{label} role cannot be @everyone.")
            return blockers, warnings, ok
    except Exception:
        pass

    preset = _looks_like_discord_preset(role)
    dangerous_public = _perm_names(role, _DANGEROUS_FOR_PUBLIC_ROLES)
    powerful_staff = _perm_names(role, _POWERFUL_STAFF_PERMS)

    try:
        if bool(getattr(role, "managed", False)):
            warnings.append(f"{label} role {_mention(role)} is managed by an integration/bot. A normal Discord role is easier to maintain for setup.")
    except Exception:
        pass

    if grantable_to_members:
        if dangerous_public:
            blockers.append(
                f"{label} role {_mention(role)} looks like a Discord {preset} preset and has dangerous permissions for a role the bot may grant to regular users: "
                f"{', '.join(dangerous_public)}. Create/use a plain Member/Cosmetic-style role instead."
            )
        else:
            ok.append(f"{label} role {_mention(role)} is safe to grant to members ({preset}; no server-control powers detected).")
    elif setup_control_or_staff:
        if powerful_staff:
            warnings.append(
                f"{label} role {_mention(role)} looks like a Discord {preset} preset and has elevated server permissions: "
                f"{', '.join(powerful_staff)}. That can be intentional, but Stoney only needs the role for bot access checks."
            )
        else:
            ok.append(f"{label} role {_mention(role)} is a safe plain/access role ({preset}).")
    else:
        if powerful_staff:
            warnings.append(f"{label} role {_mention(role)} has elevated permissions: {', '.join(powerful_staff)}.")

    if _role_is_above_bot(guild, role):
        if grantable_to_members:
            blockers.append(f"{label} role {_mention(role)} is above/equal to the bot role. Move the bot role above it so Stoney can manage verification roles.")
        elif setup_control_or_staff:
            warnings.append(f"{label} role {_mention(role)} is above/equal to the bot role. This is okay for access checks, but the bot cannot edit/assign that role.")

    return blockers, warnings, ok


def _patch_public_setup_group(module: Any) -> None:
    key = "public_setup_group"
    if key in _PATCHED:
        return

    original_ticket = getattr(module, "_validate_ticket_setup", None)
    if callable(original_ticket) and not getattr(original_ticket, "_role_safety_wrapped", False):
        def _validate_ticket_setup_patched(guild: Any, ticket_category: Any, staff_role: Any, archive_category: Any, transcripts_channel: Any):
            blockers, warnings, ok = original_ticket(guild, ticket_category, staff_role, archive_category, transcripts_channel)
            rb, rw, ro = _role_safety_notes(guild, staff_role, label="Ticket staff", setup_control_or_staff=True)
            _add_unique(blockers, rb)
            _add_unique(warnings, rw)
            _add_unique(ok, ro)
            return blockers, warnings, ok

        try:
            setattr(_validate_ticket_setup_patched, "_role_safety_wrapped", True)
        except Exception:
            pass
        setattr(module, "_validate_ticket_setup", _validate_ticket_setup_patched)

    original_verify = getattr(module, "_validate_verify_setup", None)
    if callable(original_verify) and not getattr(original_verify, "_role_safety_wrapped", False):
        def _validate_verify_setup_patched(guild: Any, verify_channel: Any, unverified_role: Any, verified_role: Any, resident_role: Any, vc_verify_channel: Any, vc_queue_channel: Any):
            blockers, warnings, ok = original_verify(guild, verify_channel, unverified_role, verified_role, resident_role, vc_verify_channel, vc_queue_channel)
            for label, role in (("Unverified", unverified_role), ("Verified", verified_role), ("Member/Resident", resident_role)):
                rb, rw, ro = _role_safety_notes(guild, role, label=label, grantable_to_members=True)
                _add_unique(blockers, rb)
                _add_unique(warnings, rw)
                _add_unique(ok, ro)
            return blockers, warnings, ok

        try:
            setattr(_validate_verify_setup_patched, "_role_safety_wrapped", True)
        except Exception:
            pass
        setattr(module, "_validate_verify_setup", _validate_verify_setup_patched)

    original_health = getattr(module, "_build_setup_health", None)
    if callable(original_health) and not getattr(original_health, "_role_safety_wrapped", False):
        def _build_setup_health_patched(guild: Any, cfg: Any):
            blockers, warnings, ok = original_health(guild, cfg)

            def role_from_cfg(*keys: str) -> Any:
                for field in keys:
                    try:
                        rid = int(getattr(cfg, field, 0) or 0)
                    except Exception:
                        rid = 0
                    if rid > 0:
                        role = guild.get_role(rid)
                        if role is not None:
                            return role
                return None

            role_specs = (
                ("Server-control", role_from_cfg("server_control_role_id", "control_role_id", "perm_role_id"), False, True),
                ("Ticket staff", role_from_cfg("staff_role_id"), False, True),
                ("VC staff", role_from_cfg("vc_staff_role_id"), False, True),
                ("Unverified", role_from_cfg("unverified_role_id"), True, False),
                ("Verified", role_from_cfg("verified_role_id"), True, False),
                ("Member/Resident", role_from_cfg("resident_role_id"), True, False),
            )
            for label, role, grantable, access_role in role_specs:
                if role is None:
                    continue
                rb, rw, ro = _role_safety_notes(guild, role, label=label, grantable_to_members=grantable, setup_control_or_staff=access_role)
                _add_unique(blockers, rb)
                _add_unique(warnings, rw)
                _add_unique(ok, ro)
            return blockers, warnings, ok

        try:
            setattr(_build_setup_health_patched, "_role_safety_wrapped", True)
        except Exception:
            pass
        setattr(module, "_build_setup_health", _build_setup_health_patched)

    _PATCHED.add(key)
    _log("patched public_setup_group validation with Discord role-preset safety checks")


def _patch_public_access_control(module: Any) -> None:
    key = "public_access_control"
    if key in _PATCHED:
        return

    original = getattr(module, "_setup_access_callback", None)
    if callable(original) and not getattr(original, "_role_safety_wrapped", False):
        async def _setup_access_callback_patched(interaction: Any, control_role: Any, ticket_staff_role: Any = None, vc_staff_role: Any = None) -> None:
            guild = getattr(interaction, "guild", None)
            role_warnings: list[str] = []
            role_ok: list[str] = []
            if guild is not None:
                for label, role in (("Server-control", control_role), ("Ticket staff", ticket_staff_role), ("VC staff", vc_staff_role)):
                    if role is None:
                        continue
                    _rb, rw, ro = _role_safety_notes(guild, role, label=label, setup_control_or_staff=True)
                    _add_unique(role_warnings, rw)
                    _add_unique(role_ok, ro)

            await original(interaction, control_role, ticket_staff_role, vc_staff_role)

            if role_warnings:
                try:
                    import discord
                    embed = discord.Embed(
                        title="⚠️ Role Permission Safety Notes",
                        description="These roles were saved, but review their Discord permissions so new servers do not accidentally over-power setup/staff roles.",
                        color=discord.Color.gold(),
                    )
                    embed.add_field(name="Warnings", value="\n".join(role_warnings)[:1024], inline=False)
                    if role_ok:
                        embed.add_field(name="Passing Role Checks", value="\n".join(role_ok)[:1024], inline=False)
                    await interaction.followup.send(embed=embed, ephemeral=True)
                except Exception:
                    pass

        try:
            setattr(_setup_access_callback_patched, "_role_safety_wrapped", True)
        except Exception:
            pass
        setattr(module, "_setup_access_callback", _setup_access_callback_patched)

    _PATCHED.add(key)
    _log("patched public_access_control setup-access with role-preset safety notes")


def _patch_setup_defaults(module: Any) -> None:
    key = "public_setup_defaults"
    if key in _PATCHED:
        return

    original = getattr(module, "_ensure_role", None)
    if callable(original) and not getattr(original, "_role_safety_wrapped", False):
        async def _ensure_role_patched(guild: Any, name: str, *, create_missing_roles: bool, notes: list[str], created: list[str], reused: list[str]) -> Any:
            before_created = len(created)
            role = await original(guild, name, create_missing_roles=create_missing_roles, notes=notes, created=created, reused=reused)
            try:
                if role is not None and len(created) > before_created:
                    notes.append(f"Created `{name}` as a plain safe role with no Discord Moderator/Manager permissions. Staff/control access is enforced by Stoney config, not by overpowering the role.")
            except Exception:
                pass
            return role

        try:
            setattr(_ensure_role_patched, "_role_safety_wrapped", True)
        except Exception:
            pass
        setattr(module, "_ensure_role", _ensure_role_patched)

    _PATCHED.add(key)
    _log("patched public_setup_defaults to explain safe plain role creation")


def _patch_loaded() -> None:
    modules = {
        "stoney_verify.commands_ext.public_setup_group": _patch_public_setup_group,
        "stoney_verify.commands_ext.public_access_control": _patch_public_access_control,
        "stoney_verify.commands_ext.public_setup_defaults": _patch_setup_defaults,
    }
    for name, patcher in modules.items():
        try:
            module = sys.modules.get(name)
            if module is not None:
                patcher(module)
        except Exception:
            pass


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.commands_ext.public_setup_group" or name.endswith("commands_ext.public_setup_group"):
            target = sys.modules.get("stoney_verify.commands_ext.public_setup_group") or sys.modules.get(name)
            if target is not None:
                _patch_public_setup_group(target)
        elif name == "stoney_verify.commands_ext.public_access_control" or name.endswith("commands_ext.public_access_control"):
            target = sys.modules.get("stoney_verify.commands_ext.public_access_control") or sys.modules.get(name)
            if target is not None:
                _patch_public_access_control(target)
        elif name == "stoney_verify.commands_ext.public_setup_defaults" or name.endswith("commands_ext.public_setup_defaults"):
            target = sys.modules.get("stoney_verify.commands_ext.public_setup_defaults") or sys.modules.get(name)
            if target is not None:
                _patch_setup_defaults(target)
        else:
            _patch_loaded()
    except Exception:
        pass
    return module


builtins.__import__ = _safe_import
_patch_loaded()
_log("loaded; Discord setup role preset safety active")
