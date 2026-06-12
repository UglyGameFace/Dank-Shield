#!/usr/bin/env python3
from __future__ import annotations

"""Remove duplicate _vc_runtime_deps definitions from events.py."""

import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / "stoney_verify" / "events.py"
SERVICE = ROOT / "stoney_verify" / "verification_new" / "vc_session_runtime_service.py"

START = "def _vc_runtime_deps():\n"
END = "async def _vc_channel_is_empty(channel: discord.abc.GuildChannel) -> bool:\n"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def main() -> int:
    if not EVENTS.exists():
        print(f"❌ missing {EVENTS}")
        return 1

    text = read(EVENTS)
    first = text.find(START)
    if first < 0:
        print("❌ _vc_runtime_deps not found")
        return 1

    second = text.find(START, first + len(START))
    if second < 0:
        print("✅ only one _vc_runtime_deps definition present")
    else:
        end = text.find(END, second)
        if end < 0:
            print(f"❌ could not find end marker {END!r}")
            return 1
        text = text[:second] + text[end:]
        write(EVENTS, text)
        print("✅ removed duplicate _vc_runtime_deps definition")

    text = read(EVENTS)
    count = text.count(START)
    if count != 1:
        print(f"❌ expected exactly one _vc_runtime_deps definition, found {count}")
        return 1

    py_compile.compile(str(EVENTS), doraise=True)
    if SERVICE.exists():
        py_compile.compile(str(SERVICE), doraise=True)
    print("✅ compiled events.py and vc_session_runtime_service.py")
    print("\nNext commands:")
    print("  git diff -- stoney_verify/events.py")
    print("  python tools/cleanup_duplicate_vc_runtime_deps.py")
    print("  python -m py_compile stoney_verify/events.py stoney_verify/verification_new/vc_session_runtime_service.py")
    print("  git add stoney_verify/events.py")
    print('  git commit -m "Remove duplicate VC runtime dependency builder"')
    print("  git push origin main")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
