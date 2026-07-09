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

# Native/profile source user-facing copy. Internal names such as
# PROFILE_COSMETIC_ROLE_IDS_KEY intentionally remain stable for existing guilds.
PROFILE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ('label="Server Cosmetics"', 'label="Server Roles / Cosmetics"'),
    ('label="Server Cosmetic Roles"', 'label="Profile Roles / Cosmetics"'),
    ('label="Remove Cosmetic Role"', 'label="Remove Role / Cosmetic"'),
    ('title="➖ Remove Cosmetic Roles"', 'title="➖ Remove Roles / Cosmetics"'),
    ('placeholder="Add an existing cosmetic role…"', 'placeholder="Add an existing server role / cosmetic…"'),
    ('placeholder="Choose cosmetic roles to remove…"', 'placeholder="Choose roles / cosmetics to remove…"'),
    ('description="Remove from Profile Builder cosmetics"', 'description="Remove from Profile Builder roles/cosmetics"'),
    ('description="Currently selected" if selected else "Optional cosmetic role"', 'description="Currently selected" if selected else "Optional role / cosmetic"'),
    ('f"{role.mention} is already a server cosmetic role."', 'f"{role.mention} is already a server role/cosmetic."'),
    ('f"Added {role.mention} as a server cosmetic role."', 'f"Added {role.mention} as a server role/cosmetic."'),
    ('f"Cosmetic role limit reached ({PROFILE_COSMETIC_MAX_ROLES}). Remove one first."', 'f"Role/cosmetic limit reached ({PROFILE_COSMETIC_MAX_ROLES}). Remove one first."'),
    ('"No cosmetic roles are configured yet."', '"No profile roles/cosmetics are configured yet."'),
    ('label=str(role.name or "Cosmetic Role")[:100]', 'label=str(role.name or "Role / Cosmetic")[:100]'),
    ('"Choose one or more roles to remove from the Profile Builder cosmetic allowlist."', '"Choose one or more roles to remove from the Profile Builder roles/cosmetics allowlist."'),
)

# Only patch helper guard comments/copy. Do not rewrite tests: the tests must keep
# the legacy strings as forbidden values.
GUARD_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ('old buttons like "Server Cosmetics" remain', 'legacy profile role/cosmetic buttons remain'),
    ('Make the old Server Cosmetics button obvious to normal users.', 'Make the profile role/cosmetic button obvious to normal users.'),
)

FORBIDDEN_PROFILE_COPY: tuple[str, ...] = (
    'label="Server Cosmetics"',
    'label="Server Cosmetic Roles"',
    'label="Remove Cosmetic Role"',
    'title="➖ Remove Cosmetic Roles"',
    'placeholder="Add an existing cosmetic role…"',
    'placeholder="Choose cosmetic roles to remove…"',
    'description="Remove from Profile Builder cosmetics"',
    '"Optional cosmetic role"',
    '"No cosmetic roles are configured yet."',
    '"Cosmetic Role"',
    '"Cosmetic role limit reached',
    '"Choose one or more roles to remove from the Profile Builder cosmetic allowlist."',
    'server cosmetic role',
)

REQUIRED_PROFILE_COPY: tuple[str, ...] = (
    'label="Server Roles / Cosmetics"',
    'label="Profile Roles / Cosmetics"',
    'label="Remove Role / Cosmetic"',
    'title="➖ Remove Roles / Cosmetics"',
    'placeholder="Add an existing server role / cosmetic…"',
    'placeholder="Choose roles / cosmetics to remove…"',
)


def replace_in(path: Path, replacements: tuple[tuple[str, str], ...]) -> bool:
    text = path.read_text(encoding="utf-8")
    original = text
    for old, new in replacements:
        text = text.replace(old, new)
    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def main() -> None:
    if not PROFILE.exists():
        raise SystemExit(f"Missing file: {PROFILE}")

    touched: list[str] = []
    if replace_in(PROFILE, PROFILE_REPLACEMENTS):
        touched.append(str(PROFILE.relative_to(ROOT)))

    for path in (GUARD, SELF_GUARD):
        if path.exists() and replace_in(path, GUARD_REPLACEMENTS):
            touched.append(str(path.relative_to(ROOT)))

    profile_text = PROFILE.read_text(encoding="utf-8")
    remaining = [item for item in FORBIDDEN_PROFILE_COPY if item in profile_text]
    if remaining:
        raise SystemExit("Legacy profile wording still remains in native source: " + ", ".join(remaining))

    missing = [item for item in REQUIRED_PROFILE_COPY if item not in profile_text]
    if missing:
        raise SystemExit("Expected new profile wording missing from native source: " + ", ".join(missing))

    print("✅ Profile role/cosmetic native cleanup complete")
    if touched:
        print("Touched:")
        for path in touched:
            print(f" - {path}")
    else:
        print("No files needed changes.")


if __name__ == "__main__":
    main()
