#!/usr/bin/env python3
from __future__ import annotations

"""Physically route join verification role assignment out of events.py."""

import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / "stoney_verify" / "events.py"
SERVICE = ROOT / "stoney_verify" / "members_new" / "join_verification_service.py"

VERIFY_CONFIG_DELEGATES = '''async def _resolve_bot_member(guild: discord.Guild) -> Optional[discord.Member]:
    from .members_new.join_verification_service import resolve_bot_member

    bot_user_id = None
    try:
        bot_user_id = int(getattr(getattr(bot, "user", None), "id", 0) or 0) or None
    except Exception:
        bot_user_id = None
    return await resolve_bot_member(guild, bot_user_id=bot_user_id)


async def _verification_role_ids_for_guild(guild: discord.Guild) -> Dict[str, int]:
    from .members_new.join_verification_service import verification_role_ids_for_guild

    return await verification_role_ids_for_guild(guild)


async def _verification_config_ready_for_guild(guild: discord.Guild) -> Tuple[bool, str]:
    from .members_new.join_verification_service import verification_config_ready_for_guild

    return await verification_config_ready_for_guild(guild)


'''

ENSURE_UNVERIFIED_DELEGATE = '''async def _ensure_unverified_on_join(member: discord.Member) -> bool:
    from .members_new.join_verification_service import ensure_unverified_on_join

    bot_user_id = None
    try:
        bot_user_id = int(getattr(getattr(bot, "user", None), "id", 0) or 0) or None
    except Exception:
        bot_user_id = None
    return await ensure_unverified_on_join(member, bot_user_id=bot_user_id)


'''

REQUIRED_SERVICE_MARKERS = (
    "async def resolve_bot_member",
    "async def verification_role_ids_for_guild",
    "async def verification_config_ready_for_guild",
    "async def ensure_unverified_on_join",
)

FORBIDDEN_EVENTS_MARKERS = (
    'globals().get("UNVERIFIED_ROLE_ID"',
    'globals().get("VERIFIED_ROLE_ID"',
    'globals().get("RESIDENT_ROLE_ID"',
    'globals().get("STAFF_ROLE_ID"',
    'globals().get("STONER_ROLE_ID"',
    'globals().get("DRUNKEN_ROLE_ID"',
    "Auto-assign Unverified on join (not Verified)",
    "Cannot assign Unverified because role hierarchy blocks it",
    "UNVERIFIED_ROLE_ID not found in guild",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def die(message: str) -> None:
    print(f"❌ {message}")
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"✅ {message}")


def replace_block(text: str, *, start_marker: str, end_marker: str, replacement: str, label: str) -> tuple[str, bool]:
    start = text.find(start_marker)
    if start < 0:
        if replacement.strip() in text:
            ok(f"{label} already applied")
            return text, False
        die(f"could not find start marker for {label}: {start_marker!r}")
    end = text.find(end_marker, start)
    if end < 0:
        die(f"could not find end marker for {label}: {end_marker!r}")
    current = text[start:end]
    if replacement.strip() in current:
        ok(f"{label} already applied")
        return text, False
    return text[:start] + replacement + text[end:], True


def verify_service_ready() -> None:
    service = read(SERVICE)
    missing = [marker for marker in REQUIRED_SERVICE_MARKERS if marker not in service]
    if missing:
        print("❌ join_verification_service is not ready:")
        for marker in missing:
            print(" -", marker)
        raise SystemExit(1)


def main() -> int:
    if not EVENTS.exists():
        die(f"missing {EVENTS}")
    if not SERVICE.exists():
        die(f"missing {SERVICE}")

    verify_service_ready()
    text = read(EVENTS)
    changed = False

    text, did = replace_block(
        text,
        start_marker="async def _resolve_bot_member(guild: discord.Guild) -> Optional[discord.Member]:\n",
        end_marker="async def _handle_join_verification_failure(member: discord.Member, reason: str) -> None:\n",
        replacement=VERIFY_CONFIG_DELEGATES,
        label="events join verification config delegates",
    )
    changed = changed or did

    text, did = replace_block(
        text,
        start_marker="async def _ensure_unverified_on_join(member: discord.Member) -> bool:\n",
        end_marker="async def _resolve_unverified_chat_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:\n",
        replacement=ENSURE_UNVERIFIED_DELEGATE,
        label="events ensure_unverified_on_join service delegate",
    )
    changed = changed or did

    offenders = [marker for marker in FORBIDDEN_EVENTS_MARKERS if marker in text]
    if offenders:
        print("❌ events.py still contains join verification role ownership markers:")
        for marker in offenders:
            print(" -", marker)
        return 1

    if changed:
        write(EVENTS, text)
        ok("updated stoney_verify/events.py join verification ownership")
    else:
        ok("events.py join verification delegates already present")

    py_compile.compile(str(EVENTS), doraise=True)
    py_compile.compile(str(SERVICE), doraise=True)
    ok("compiled events.py and join_verification_service.py")

    print("\nNext commands:")
    print("  git diff -- stoney_verify/events.py")
    print("  python tools/apply_join_verification_service_handoff.py")
    print("  python -m py_compile stoney_verify/events.py stoney_verify/members_new/join_verification_service.py")
    print("  git add stoney_verify/events.py")
    print('  git commit -m "Physically hand off join verification roles"')
    print("  git push origin main")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
