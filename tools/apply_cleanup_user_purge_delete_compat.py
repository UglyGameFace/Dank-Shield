from __future__ import annotations

"""Fix user-message purge delete compatibility.

discord.py's Message.delete/PartialMessage.delete in this bot runtime does not
accept a reason keyword. User-message purge already stores the moderation reason
in the command summary/context; deletion itself must call msg.delete() without a
reason to work across the installed discord.py version.

Run from repo root:
    python tools/apply_cleanup_user_purge_delete_compat.py
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATHS = (
    ROOT / "stoney_verify/commands_ext/public_cleanup_group.py",
    ROOT / "tools/apply_cleanup_user_message_purge.py",
)
TEST = ROOT / "tools/test_cleanup_user_message_purge_static.py"


def patch_text(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    old = text
    text = text.replace("await msg.delete(reason=reason)", "await msg.delete()")
    if text != old:
        path.write_text(text, encoding="utf-8")
        print(f"✅ patched {path.relative_to(ROOT)}")
        return True
    print(f"✅ no delete(reason=...) found in {path.relative_to(ROOT)}")
    return False


def patch_test() -> None:
    if not TEST.exists():
        return
    text = TEST.read_text(encoding="utf-8")
    text = text.replace('assert "await msg.delete(reason=reason)" in CLEANUP', 'assert "await msg.delete()" in CLEANUP')
    text = text.replace('assert "await msg.delete(reason=reason)" not in CLEANUP', 'assert "await msg.delete(reason=reason)" not in CLEANUP')
    if 'assert "await msg.delete(reason=reason)" not in CLEANUP' not in text:
        text = text.replace(
            'assert "await msg.delete()" in CLEANUP\n',
            'assert "await msg.delete()" in CLEANUP\n    assert "await msg.delete(reason=reason)" not in CLEANUP\n',
        )
    TEST.write_text(text, encoding="utf-8")
    print(f"✅ patched {TEST.relative_to(ROOT)}")


def main() -> None:
    for path in PATHS:
        if path.exists():
            patch_text(path)
    patch_test()

    cleanup = PATHS[0].read_text(encoding="utf-8")
    if "await msg.delete(reason=reason)" in cleanup:
        raise SystemExit("public_cleanup_group.py still uses msg.delete(reason=reason)")
    if "await msg.delete()" not in cleanup:
        raise SystemExit("public_cleanup_group.py missing msg.delete()")
    print("✅ User purge delete compatibility fixed")


if __name__ == "__main__":
    main()
