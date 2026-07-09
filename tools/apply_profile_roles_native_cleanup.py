from __future__ import annotations

"""Remove legacy Profile Builder cosmetics wording from native source.

Run from repo root:
    python tools/apply_profile_roles_native_cleanup.py

This intentionally edits the real profile source instead of relying on runtime
rename patches. It keeps the underlying config key names stable for existing
servers while making all user-facing copy say roles/cosmetics clearly.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "stoney_verify/commands_ext/public_self_roles_group.py"
GUARD = ROOT / "stoney_verify/startup_guards/profile_role_editor_guard.py"
SELF_GUARD = ROOT / "stoney_verify/startup_guards/self_roles_command_guard.py"
TEST = ROOT / "tools/test_profile_role_editor_guard_static.py"
LEGACY_TEST = ROOT / "tools/test_profile_cosmetic_roles_static.py"

REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ('label="Server Cosmetics"', 'label="Server Roles / Cosmetics"'),
    ('label="Server Cosmetic Roles"', 'label="Profile Roles / Cosmetics"'),
    ('label="Remove Cosmetic Role"', 'label="Remove Role / Cosmetic"'),
    ('title="➖ Remove Cosmetic Roles"', 'title="➖ Remove Roles / Cosmetics"'),
    ('placeholder="Add an existing cosmetic role…"', 'placeholder="Add an existing server role / cosmetic…"'),
    ('placeholder="Choose cosmetic roles to remove…"', 'placeholder="Choose roles / cosmetics to remove…"'),
    ('description="Remove from Profile Builder cosmetics"', 'description="Remove from Profile Builder roles/cosmetics"'),
    ('f"{role.mention} is already a server cosmetic role."', 'f"{role.mention} is already a server role/cosmetic."'),
    ('f"Added {role.mention} as a server cosmetic role."', 'f"Added {role.mention} as a server role/cosmetic."'),
    ('"No cosmetic roles are configured yet."', '"No profile roles/cosmetics are configured yet."'),
    ('label=str(role.name or "Cosmetic Role")[:100]', 'label=str(role.name or "Role / Cosmetic")[:100]'),
    ('"Choose one or more roles to remove from the Profile Builder cosmetic allowlist."', '"Choose one or more roles to remove from the Profile Builder roles/cosmetics allowlist."'),
    ('"Server Cosmetics"', '"Server Roles / Cosmetics"'),
    ('"Server Cosmetic Roles"', '"Profile Roles / Cosmetics"'),
)

# Keep internal names stable, but kill old user-facing copy in comments/tests.
SOFT_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("old buttons like \"Server Cosmetics\" remain", "legacy profile role/cosmetic buttons remain"),
    ("Server Cosmetics", "Server Roles / Cosmetics"),
    ("Server Cosmetic Roles", "Profile Roles / Cosmetics"),
    ("Add an existing cosmetic role", "Add an existing server role / cosmetic"),
    ("Remove Cosmetic Role", "Remove Role / Cosmetic"),
    ("Remove Cosmetic Roles", "Remove Roles / Cosmetics"),
)


def replace_in(path: Path, replacements: tuple[tuple[str, str], ...]) -> int:
    text = path.read_text(encoding="utf-8")
    original = text
    for old, new in replacements:
        text = text.replace(old, new)
    if text != original:
        path.write_text(text, encoding="utf-8")
    return original.count("Server Cosmetics") + original.count("Server Cosmetic Roles")


def main() -> None:
    if not PROFILE.exists():
        raise SystemExit(f"Missing file: {PROFILE}")

    touched = []
    before_profile = PROFILE.read_text(encoding="utf-8")
    replace_in(PROFILE, REPLACEMENTS)
    if PROFILE.read_text(encoding="utf-8") != before_profile:
        touched.append(str(PROFILE.relative_to(ROOT)))

    for path in (GUARD, SELF_GUARD, TEST, LEGACY_TEST):
        if not path.exists():
            continue
        before = path.read_text(encoding="utf-8")
        replace_in(path, SOFT_REPLACEMENTS)
        after = path.read_text(encoding="utf-8")
        if after != before:
            touched.append(str(path.relative_to(ROOT)))

    profile_text = PROFILE.read_text(encoding="utf-8")
    forbidden = [
        "Server Cosmetics",
        "Server Cosmetic Roles",
        "Add an existing cosmetic role",
        "Remove Cosmetic Role",
        "Remove Cosmetic Roles",
    ]
    remaining = [item for item in forbidden if item in profile_text]
    if remaining:
        raise SystemExit("Legacy profile wording still remains in native source: " + ", ".join(remaining))

    print("✅ Profile role/cosmetic native cleanup complete")
    if touched:
        print("Touched:")
        for path in touched:
            print(f" - {path}")
    else:
        print("No files needed changes.")


if __name__ == "__main__":
    main()
