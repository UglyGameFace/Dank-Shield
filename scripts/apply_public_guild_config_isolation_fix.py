#!/usr/bin/env python3
from __future__ import annotations

import py_compile
import sys
from pathlib import Path


ROOT = Path.cwd()
EVENTS = ROOT / "stoney_verify" / "events.py"
MODLOG = ROOT / "stoney_verify" / "modlog.py"


def die(message: str) -> None:
    print(f"❌ {message}")
    sys.exit(1)


def ok(message: str) -> None:
    print(f"✅ {message}")


def read(path: Path) -> str:
    if not path.exists():
        die(f"Missing file: {path}")
    return path.read_text(encoding="utf-8")


def write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def replace_once(content: str, old: str, new: str, *, label: str) -> tuple[str, bool]:
    if old not in content:
        if new in content:
            ok(f"{label} already applied")
            return content, False
        die(f"Could not find expected block for: {label}")

    count = content.count(old)
    if count != 1:
        die(f"Expected exactly 1 match for {label}, found {count}")

    return content.replace(old, new, 1), True


def patch_events_imports(content: str) -> tuple[str, bool]:
    old = '''from .vc_verify import _can_manage_channel, _get_vc_channel, vc_sweeper_loop

try:
    from . import vc_sessions
'''

    new = '''from .vc_verify import _can_manage_channel, _get_vc_channel, vc_sweeper_loop

try:
    from .guild_config import get_guild_config, public_config_isolation_enabled
except Exception:
    get_guild_config = None  # type: ignore

    def public_config_isolation_enabled() -> bool:  # type: ignore
        return True

try:
    from . import vc_sessions
'''

    return replace_once(
        content,
        old,
        new,
        label="events.py guild_config import fallback",
    )


def patch_events_helpers(content: str) -> tuple[str, bool]:
    anchor = '''async def _handle_join_verification_failure(member: discord.Member, reason: str) -> None:
'''

    helper = '''async def _verification_role_ids_for_guild(guild: discord.Guild) -> Dict[str, int]:
    """Resolve verification role IDs for this guild without leaking home-guild globals."""

    try:
        if callable(get_guild_config):
            cfg = await get_guild_config(guild.id, force_refresh=False)  # type: ignore[misc]
            return {
                "unverified": _as_int(cfg.get("unverified_role_id"), 0),
                "verified": _as_int(cfg.get("verified_role_id"), 0),
                "resident": _as_int(cfg.get("resident_role_id"), 0),
                "staff": _as_int(cfg.get("staff_role_id"), 0),
                "stoner": _as_int(cfg.get("stoner_role_id"), 0),
                "drunken": _as_int(cfg.get("drunken_role_id"), 0),
            }
    except Exception as e:
        print(f"⚠️ [VERIFY] per-guild role config lookup failed guild={getattr(guild, 'id', 'unknown')} error={repr(e)}")

    allow_global = True
    try:
        if public_config_isolation_enabled():
            home_gid = _as_int(globals().get("GUILD_ID", 0), 0)
            guild_id = _as_int(getattr(guild, "id", 0), 0)
            allow_global = bool(home_gid > 0 and guild_id == home_gid)
    except Exception:
        allow_global = False

    if not allow_global:
        return {
            "unverified": 0,
            "verified": 0,
            "resident": 0,
            "staff": 0,
            "stoner": 0,
            "drunken": 0,
        }

    return {
        "unverified": _as_int(globals().get("UNVERIFIED_ROLE_ID", 0), 0),
        "verified": _as_int(globals().get("VERIFIED_ROLE_ID", 0), 0),
        "resident": _as_int(globals().get("RESIDENT_ROLE_ID", 0), 0),
        "staff": _as_int(globals().get("STAFF_ROLE_ID", 0), 0),
        "stoner": _as_int(globals().get("STONER_ROLE_ID", 0), 0),
        "drunken": _as_int(globals().get("DRUNKEN_ROLE_ID", 0), 0),
    }


async def _verification_config_ready_for_guild(guild: discord.Guild) -> Tuple[bool, str]:
    role_ids = await _verification_role_ids_for_guild(guild)
    uv_id = int(role_ids.get("unverified") or 0)
    if uv_id <= 0:
        return False, "No per-guild Unverified role configured. Setup must finish before join enforcement."

    try:
        role = guild.get_role(uv_id)
        if role is None:
            return False, f"Configured Unverified role {uv_id} does not exist in this guild."
    except Exception:
        return False, "Could not validate this guild's Unverified role."

    return True, "Verification config ready."


'''

    if "async def _verification_role_ids_for_guild" in content:
        ok("events.py verification config helpers already present")
        return content, False

    if anchor not in content:
        die("Could not find insert anchor before _handle_join_verification_failure")

    return content.replace(anchor, helper + anchor, 1), True


def patch_fail_closed_guard(content: str) -> tuple[str, bool]:
    old = '''        if _member_has_any_safe_access_role(member, include_unverified=True):
            print(f"ℹ️ [VERIFY] Fail-closed skipped for {member.id}; member already has a safe role state.")
            return

        embed = discord.Embed(
'''

    new = '''        if _member_has_any_safe_access_role(member, include_unverified=True):
            print(f"ℹ️ [VERIFY] Fail-closed skipped for {member.id}; member already has a safe role state.")
            return

        config_ready, config_reason = await _verification_config_ready_for_guild(guild)
        if not config_ready:
            print(
                f"🛡️ [VERIFY] Fail-closed skipped guild={guild.id} member={member.id}; "
                f"{config_reason}"
            )
            return

        embed = discord.Embed(
'''

    return replace_once(
        content,
        old,
        new,
        label="skip fail-closed kick while per-guild verification config is incomplete",
    )


def patch_ensure_unverified_on_join(content: str) -> tuple[str, bool]:
    old = '''        guild = member.guild
        uv_id = int(UNVERIFIED_ROLE_ID or 0)
        v_id = int(VERIFIED_ROLE_ID or 0)
        resident_id = int(RESIDENT_ROLE_ID or 0) if RESIDENT_ROLE_ID else 0
        staff_id = int(STAFF_ROLE_ID or 0) if STAFF_ROLE_ID else 0
        stoner_id = int(STONER_ROLE_ID or 0) if STONER_ROLE_ID else 0
        drunken_id = int(DRUNKEN_ROLE_ID or 0) if DRUNKEN_ROLE_ID else 0

        if not uv_id:
            print("⚠️ [VERIFY] UNVERIFIED_ROLE_ID missing or invalid.")
            return False
'''

    new = '''        guild = member.guild
        role_ids = await _verification_role_ids_for_guild(guild)
        uv_id = int(role_ids.get("unverified") or 0)
        v_id = int(role_ids.get("verified") or 0)
        resident_id = int(role_ids.get("resident") or 0)
        staff_id = int(role_ids.get("staff") or 0)
        stoner_id = int(role_ids.get("stoner") or 0)
        drunken_id = int(role_ids.get("drunken") or 0)

        if not uv_id:
            print(f"⚠️ [VERIFY] Unverified role missing for guild={guild.id}; setup required before join enforcement.")
            return False
'''

    return replace_once(
        content,
        old,
        new,
        label="resolve join verification role IDs from per-guild config",
    )


def patch_events() -> bool:
    content = read(EVENTS)
    original = content

    content, _ = patch_events_imports(content)
    content, _ = patch_events_helpers(content)
    content, _ = patch_fail_closed_guard(content)
    content, _ = patch_ensure_unverified_on_join(content)

    if content != original:
        write(EVENTS, content)
        ok(f"Updated {EVENTS}")
        return True

    ok(f"No changes needed for {EVENTS}")
    return False


def patch_modlog() -> bool:
    content = read(MODLOG)
    original = content

    old = '''def _candidate_modlog_channel_ids(guild: discord.Guild) -> List[int]:
    out: List[int] = []
    seen: Set[int] = set()

    def _push(value: Any) -> None:
        cid = _safe_int(value, 0)
        if cid > 0 and cid not in seen:
            seen.add(cid)
            out.append(cid)

    try:
        _push(_env_guild_override_int("MODLOG_CHANNEL_ID", int(guild.id), 0))
    except Exception:
        pass

    try:
        _push(globals().get("MODLOG_CHANNEL_ID", 0))
    except Exception:
        pass

    return out
'''

    new = '''def _candidate_modlog_channel_ids(guild: discord.Guild) -> List[int]:
    out: List[int] = []
    seen: Set[int] = set()

    def _push(value: Any) -> None:
        cid = _safe_int(value, 0)
        if cid > 0 and cid not in seen:
            seen.add(cid)
            out.append(cid)

    try:
        _push(_env_guild_override_int("MODLOG_CHANNEL_ID", int(guild.id), 0))
    except Exception:
        pass

    allow_global_modlog = True
    try:
        from .guild_config import public_config_isolation_enabled

        if public_config_isolation_enabled():
            home_gid = _safe_int(globals().get("GUILD_ID", 0), 0)
            allow_global_modlog = bool(home_gid > 0 and int(guild.id) == int(home_gid))
    except Exception:
        allow_global_modlog = False

    if allow_global_modlog:
        try:
            _push(globals().get("MODLOG_CHANNEL_ID", 0))
        except Exception:
            pass

    return out
'''

    content, _ = replace_once(
        content,
        old,
        new,
        label="avoid global MODLOG_CHANNEL_ID fallback for isolated public guilds",
    )

    if content != original:
        write(MODLOG, content)
        ok(f"Updated {MODLOG}")
        return True

    ok(f"No changes needed for {MODLOG}")
    return False


def compile_check() -> None:
    for path in (EVENTS, MODLOG):
        py_compile.compile(str(path), doraise=True)
        ok(f"Compiled {path}")


def main() -> None:
    if not (ROOT / "stoney_verify").exists():
        die("Run this from the repo root. I could not find ./stoney_verify")

    changed = False
    changed = patch_events() or changed
    changed = patch_modlog() or changed
    compile_check()

    if changed:
        print("\n✅ Public-guild config isolation runtime patch applied.")
        print("\nNext commands:")
        print("  git diff -- stoney_verify/events.py stoney_verify/modlog.py")
        print("  git add stoney_verify/events.py stoney_verify/modlog.py")
        print('  git commit -m "Fix public guild config isolation leaks"')
        print("  git push")
    else:
        print("\n✅ No changes needed. Patch was already applied.")


if __name__ == "__main__":
    main()
