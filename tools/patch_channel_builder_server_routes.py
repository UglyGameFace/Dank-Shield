#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import argparse
import difflib
import sys

ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "stoney_verify" / "api_new" / "server.py"

SYS_IMPORT = "import sys"
ROUTE_IMPORT = "from .channel_builder_routes import register_channel_builder_routes"
ROUTE_CALL = "    register_channel_builder_routes(app, sys.modules[__name__])"

IMPORT_HMAC_ANCHOR = "import hmac\n"
EVENTS_IMPORT_ANCHOR = "from ..events_new.members import (\n    run_full_member_sync_for_guild,\n    run_departed_reconciliation_for_guild,\n    run_role_member_sync,\n)\n"
MEMBER_ROUTES_ANCHOR = (
    "    app.router.add_post(\"/members/sync\", force_member_sync)\n"
    "    app.router.add_post(\"/members/reconcile\", reconcile_departed)\n"
    "    app.router.add_post(\"/members/role-sync\", role_member_sync)\n"
)


def patch_text(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    patched = text

    if SYS_IMPORT not in patched.split("\n", 12)[:12]:
        if IMPORT_HMAC_ANCHOR not in patched:
            raise RuntimeError("Could not find import hmac anchor")
        patched = patched.replace(IMPORT_HMAC_ANCHOR, IMPORT_HMAC_ANCHOR + f"{SYS_IMPORT}\n", 1)
        changes.append("added import sys")

    if ROUTE_IMPORT not in patched:
        if EVENTS_IMPORT_ANCHOR not in patched:
            raise RuntimeError("Could not find events import anchor")
        patched = patched.replace(EVENTS_IMPORT_ANCHOR, EVENTS_IMPORT_ANCHOR + f"{ROUTE_IMPORT}\n", 1)
        changes.append("added Channel Builder route import")

    if ROUTE_CALL not in patched:
        if MEMBER_ROUTES_ANCHOR not in patched:
            raise RuntimeError("Could not find member routes anchor inside start_api")
        patched = patched.replace(MEMBER_ROUTES_ANCHOR, MEMBER_ROUTES_ANCHOR + "\n\n" + ROUTE_CALL, 1)
        changes.append("added direct Channel Builder route registration")

    return patched, changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safely patch api_new/server.py with direct Channel Builder route registration.")
    parser.add_argument("--apply", action="store_true", help="Write the patch to server.py")
    parser.add_argument("--check", action="store_true", help="Only verify the patch can be applied or is already applied")
    parser.add_argument("--diff", action="store_true", help="Print the proposed unified diff")
    args = parser.parse_args(argv)

    text = SERVER_PATH.read_text(encoding="utf-8")
    patched, changes = patch_text(text)

    if args.diff and patched != text:
        print("".join(difflib.unified_diff(text.splitlines(True), patched.splitlines(True), fromfile=str(SERVER_PATH), tofile=str(SERVER_PATH))))

    if args.apply:
        if patched == text:
            print("server.py already has direct Channel Builder route registration")
            return 0
        SERVER_PATH.write_text(patched, encoding="utf-8")
        print("Patched server.py: " + ", ".join(changes))
        return 0

    if args.check or not args.apply:
        if patched == text:
            print("server.py already has direct Channel Builder route registration")
        else:
            print("server.py can be safely patched: " + ", ".join(changes))
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
