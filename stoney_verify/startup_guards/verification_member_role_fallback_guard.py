from __future__ import annotations

"""Treat a configured Verified role as the effective member/resident role.

Public servers do not always want a separate Resident/Member role. The setup
builder can create one, but existing/custom servers often use one normal access
role, e.g. Verified. This guard keeps health checks and runtime config honest:

- Verified remains the required approval/access role.
- Resident/Member is optional.
- If Resident/Member is blank or points at a deleted starter-template role,
  health checks fall back to Verified instead of blocking setup.
- Runtime discovery exposes member_role_id/resident_role_id as Verified only
  when no separate member role is configured.
"""

from typing import Any, Optional

import discord

_PATCHED = False


def _log(message: str) -> None:
    try:
        print(f"✅ verification_member_role_fallback_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ verification_member_role_fallback_guard: {message}")
    except Exception:
        pass


def _truthy_id(value: Any) -> str:
    try:
        if value is None or isinstance(value, bool):
            return ""
        text = str(value).strip()
        if not text or text == "0" or text.lower() in {"none", "null"}:
            return ""
        num = int(text)
        return str(num) if num > 0 else ""
    except Exception:
        return ""


def _is_resident_label(label: Any) -> bool:
    text = str(label or "").lower()
    return "resident" in text or "member" in text


def _patch_runtime_validator() -> bool:
    try:
        from stoney_verify.startup_guards import guild_config_runtime_validator as validator
    except Exception as e:
        _warn(f"runtime validator import failed: {e!r}")
        return False

    original = getattr(validator, "_apply_runtime_discovery", None)
    if not callable(original):
        _warn("runtime validator discovery helper missing")
        return False
    if getattr(original, "_verified_member_fallback_wrapped", False):
        return True

    def _apply_runtime_discovery_with_verified_member_fallback(guild: discord.Guild, cfg: dict[str, Any]) -> dict[str, Any]:
        resolved = original(guild, cfg)
        if not isinstance(resolved, dict):
            return resolved

        verified_id = _truthy_id(resolved.get("verified_role_id"))
        resident_id = _truthy_id(resolved.get("resident_role_id"))
        member_id = _truthy_id(resolved.get("member_role_id"))

        # Keep explicit separate member/resident choices paired for older runtime
        # readers, but never override a real configured role with Verified.
        if resident_id and not member_id:
            resolved["member_role_id"] = resident_id
            return resolved
        if member_id and not resident_id:
            resolved["resident_role_id"] = member_id
            return resolved

        if verified_id and not resident_id and not member_id:
            resolved["resident_role_id"] = verified_id
            resolved["member_role_id"] = verified_id
            resolved["effective_member_role_source"] = "verified_role_id"
            fields = list(resolved.get("runtime_discovered_fields") or [])
            for key in ("resident_role_id", "member_role_id"):
                if key not in fields:
                    fields.append(key)
            resolved["runtime_discovered_fields"] = fields
        return resolved

    setattr(_apply_runtime_discovery_with_verified_member_fallback, "_verified_member_fallback_wrapped", True)
    validator._apply_runtime_discovery = _apply_runtime_discovery_with_verified_member_fallback  # type: ignore[attr-defined]
    return True


def _patch_public_setup_group() -> bool:
    try:
        from stoney_verify.commands_ext import public_setup_group as group
    except Exception as e:
        _warn(f"public setup group import failed: {e!r}")
        return False

    current = getattr(group, "_check_role_exists", None)
    if not callable(current):
        _warn("public setup role checker missing")
        return False
    if getattr(current, "_verified_member_fallback_wrapped", False):
        return True

    def _check_role_exists_with_optional_resident(
        *,
        guild: discord.Guild,
        role_id: int,
        label: str,
        required: bool,
        blockers: list[str],
        warnings: list[str],
        ok: list[str],
    ) -> Optional[discord.Role]:
        rid = group._safe_int(role_id, 0)

        if rid <= 0:
            if required:
                blockers.append(f"{label} role is not set.")
            elif _is_resident_label(label):
                ok.append("Resident/member role is not separate; Verified can be used as this server's full-access member role.")
            else:
                warnings.append(f"{label} role is not set.")
            return None

        role = guild.get_role(rid)
        if role is None:
            if required:
                blockers.append(f"{label} role is missing: `{rid}`.")
            elif _is_resident_label(label):
                warnings.append(f"Saved Resident/member role `{rid}` is missing/deleted. Ignoring it because a separate member role is optional when Verified is configured.")
            else:
                warnings.append(f"Optional {label} role is missing: `{rid}`.")
            return None

        ok.append(f"{label} role exists: {role.mention}.")
        return role

    setattr(_check_role_exists_with_optional_resident, "_verified_member_fallback_wrapped", True)
    group._check_role_exists = _check_role_exists_with_optional_resident  # type: ignore[attr-defined]
    return True


def _patch_full_setup_health() -> bool:
    try:
        from stoney_verify.startup_guards import full_setup_health_autofix as health
    except Exception as e:
        _warn(f"full setup health import failed: {e!r}")
        return False

    current = getattr(health, "_audit_roles", None)
    if not callable(current):
        _warn("full setup role audit missing")
        return False
    if getattr(current, "_verified_member_fallback_wrapped", False):
        return True

    def _audit_roles_with_verified_member_fallback(guild: discord.Guild, cfg: Any, result: Any) -> dict[str, Optional[discord.Role]]:
        roles: dict[str, Optional[discord.Role]] = {}
        me = health._bot_member(guild)
        verified_role: Optional[discord.Role] = None

        for label, keys in health.ROLE_KEYS.items():
            role_id = health._cfg_int(cfg, *keys)

            if _is_resident_label(label):
                fallback = verified_role or health._role(guild, health._cfg_int(cfg, "verified_role_id"))
                if role_id <= 0:
                    roles[label] = fallback
                    if fallback is not None:
                        result.ok.append(f"Resident/member is not separate; using Verified as the full-access member role: {health._mention(fallback)}.")
                    else:
                        result.add_warning("Resident/member role is not separately saved. This is okay once the Verified role is configured.", manual=True)
                    continue

                role = health._role(guild, role_id)
                if role is None:
                    roles[label] = fallback
                    if fallback is not None:
                        result.add_warning(
                            f"Resident/member role is saved as `{role_id}`, but that role is missing/deleted. Using Verified as the full-access member role instead: {health._mention(fallback)}.",
                            manual=True,
                        )
                    else:
                        result.add_warning(f"Resident/member role is saved as `{role_id}`, but that optional role is missing/deleted.", manual=True)
                    continue
            else:
                role = health._role(guild, role_id)

            roles[label] = role

            if role_id <= 0:
                if label == "Server-control role":
                    result.add_warning(f"{label} is not saved. Setup control will rely on administrators/manage-guild users.", manual=True)
                else:
                    result.add_blocker(f"{label} is not saved in setup.", manual=True)
                continue

            if role is None:
                result.add_blocker(f"{label} is saved as `{role_id}`, but that role is missing/deleted.", manual=True)
                continue

            if label == "Verified role":
                verified_role = role

            result.ok.append(f"{label} exists: {health._mention(role)}.")

            if label in {"Unverified/waiting role", "Verified role", "Resident/member role"}:
                if not health._can_manage_role(me, role):
                    result.add_blocker(
                        f"Bot cannot manage {label} {health._mention(role)}. Move Stoney's bot role above it and make sure Manage Roles is enabled.",
                        manual=True,
                    )
                else:
                    result.ok.append(f"Bot can manage {label}: {health._mention(role)}.")

        return roles

    setattr(_audit_roles_with_verified_member_fallback, "_verified_member_fallback_wrapped", True)
    health._audit_roles = _audit_roles_with_verified_member_fallback  # type: ignore[attr-defined]
    return True


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    ok_runtime = _patch_runtime_validator()
    ok_group = _patch_public_setup_group()
    ok_health = _patch_full_setup_health()
    _PATCHED = bool(ok_runtime or ok_group or ok_health)
    if _PATCHED:
        _log(f"active runtime={ok_runtime} setup_group={ok_group} full_health={ok_health}")
    return _PATCHED


apply()


__all__ = ["apply"]
