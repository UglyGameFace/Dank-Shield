#!/usr/bin/env python3
from __future__ import annotations

import py_compile
import sys
from pathlib import Path


ROOT = Path.cwd()
TARGET = ROOT / "stoney_verify" / "verification_new" / "service.py"


def die(message: str) -> None:
    print(f"❌ {message}")
    sys.exit(1)


def ok(message: str) -> None:
    print(f"✅ {message}")


def main() -> None:
    if not (ROOT / "stoney_verify").exists():
        die("Run this from the repo root. I could not find ./stoney_verify")

    if not TARGET.exists():
        die(f"Missing file: {TARGET}")

    content = TARGET.read_text(encoding="utf-8")
    original = content

    old = '''async def _apply_verified_roles(
    *,
    member: discord.Member,
    staff_member: discord.Member,
    roles_to_assign: List[discord.Role],
) -> Tuple[List[discord.Role], Optional[str]]:
    grant_roles = _roles_to_grant_for_member(member, roles_to_assign)

    if grant_roles:
        await member.add_roles(
            *grant_roles,
            reason=f"Dank Shield approved by {staff_member} ({staff_member.id})",
        )

    _, remove_error = await _remove_unverified_role_if_present(
        member,
        reason=f"Dank Shield approval cleanup by {staff_member} ({staff_member.id})",
    )

    return grant_roles, remove_error
'''

    new = '''async def _apply_verified_roles(
    *,
    member: discord.Member,
    staff_member: discord.Member,
    roles_to_assign: List[discord.Role],
) -> Tuple[List[discord.Role], Optional[str]]:
    """Apply approval roles while reducing mechanical member-update churn.

    The old flow performed two Discord role operations:

    1. add verified/resident roles
    2. remove Unverified

    Discord can emit a separate member-update event for each operation. For the
    normal verification path, prefer one atomic role-set edit so the approval
    mutation is seen as one final role state.

    Safety guard:
    If the member has managed roles or roles at/above the bot's top role, fall
    back to the old add/remove behavior. That avoids breaking approval for edge
    cases where Discord may reject a full role-set edit because of hierarchy or
    managed integration roles.
    """

    grant_roles = _roles_to_grant_for_member(member, roles_to_assign)
    unverified_role = _role_by_id(member.guild, int(UNVERIFIED_ROLE_ID or 0))
    had_unverified = isinstance(unverified_role, discord.Role) and unverified_role in (member.roles or [])

    if not grant_roles and not had_unverified:
        return [], None

    current_roles = [
        role
        for role in (member.roles or [])
        if isinstance(role, discord.Role)
        and not role.is_default()
        and role.guild.id == member.guild.id
    ]

    final_by_id: Dict[int, discord.Role] = {
        int(role.id): role
        for role in current_roles
    }

    for role in grant_roles:
        final_by_id[int(role.id)] = role

    if isinstance(unverified_role, discord.Role):
        final_by_id.pop(int(unverified_role.id), None)

    atomic_edit_safe = False
    try:
        me = member.guild.me
        bot_top_role = getattr(me, "top_role", None)
        atomic_edit_safe = isinstance(bot_top_role, discord.Role)

        if atomic_edit_safe:
            for role in list(final_by_id.values()):
                if not isinstance(role, discord.Role):
                    atomic_edit_safe = False
                    break
                if getattr(role, "managed", False):
                    atomic_edit_safe = False
                    break
                if role >= bot_top_role:
                    atomic_edit_safe = False
                    break
    except Exception:
        atomic_edit_safe = False

    if atomic_edit_safe:
        await member.edit(
            roles=list(final_by_id.values()),
            reason=f"Dank Shield approval roles by {staff_member} ({staff_member.id})",
        )
        return grant_roles, None

    if grant_roles:
        await member.add_roles(
            *grant_roles,
            reason=f"Dank Shield approved by {staff_member} ({staff_member.id})",
        )

    _, remove_error = await _remove_unverified_role_if_present(
        member,
        reason=f"Dank Shield approval cleanup by {staff_member} ({staff_member.id})",
    )

    return grant_roles, remove_error
'''

    if old not in content:
        if "Apply approval roles while reducing mechanical member-update churn" in content:
            ok("Approval role churn fix already applied")
        else:
            die("Could not find the expected _apply_verified_roles() block")
    else:
        count = content.count(old)
        if count != 1:
            die(f"Expected exactly 1 _apply_verified_roles() block, found {count}")

        content = content.replace(old, new, 1)
        TARGET.write_text(content, encoding="utf-8")
        ok(f"Updated {TARGET}")

    py_compile.compile(str(TARGET), doraise=True)
    ok(f"Compiled {TARGET}")

    if content != original:
        print("\n✅ Approval role churn runtime patch applied.")
        print("\nNext commands:")
        print("  git diff -- stoney_verify/verification_new/service.py")
        print("  git add stoney_verify/verification_new/service.py")
        print('  git commit -m "Reduce approval role churn in verification service"')
        print("  git push")
    else:
        print("\n✅ No changes needed. Patch was already applied.")


if __name__ == "__main__":
    main()
