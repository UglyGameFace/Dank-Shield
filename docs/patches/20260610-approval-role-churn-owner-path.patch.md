# Approval role churn owner-path patch

Issue: #47
Prepared branch: `fix/approval-role-churn-owner-path`

This file documents the exact surgical code patch required to reduce verification approval role-change modlog churn.

Do **not** treat this as the runtime fix by itself. The runtime fix must be applied to owner files only.

Primary target:

- `stoney_verify/verification_new/service.py`

Fallback target only if atomic role edit is unsafe:

- `stoney_verify/modlog.py::maybe_log_member_update_diff()`

No startup guards. No runtime monkey patches. Do not revive PR #45.

---

## Confirmed root cause

`stoney_verify/verification_new/service.py::_apply_verified_roles()` currently performs two Discord role operations:

```py
await member.add_roles(
    *grant_roles,
    reason=f"Dank Shield approved by {staff_member} ({staff_member.id})",
)

_, remove_error = await _remove_unverified_role_if_present(
    member,
    reason=f"Dank Shield approval cleanup by {staff_member} ({staff_member.id})",
)
```

Discord can emit separate member-update events for each mechanical mutation.

---

## Preferred patch: source-level atomic role edit

Replace `_apply_verified_roles()` with:

```py
async def _apply_verified_roles(
    *,
    member: discord.Member,
    staff_member: discord.Member,
    roles_to_assign: List[discord.Role],
) -> Tuple[List[discord.Role], Optional[str]]:
    grant_roles = _roles_to_grant_for_member(member, roles_to_assign)

    try:
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

        unverified_role = _role_by_id(member.guild, int(UNVERIFIED_ROLE_ID or 0))
        had_unverified = isinstance(unverified_role, discord.Role) and unverified_role in (member.roles or [])
        if isinstance(unverified_role, discord.Role):
            final_by_id.pop(int(unverified_role.id), None)

        changed = bool(grant_roles) or bool(had_unverified)
        if changed:
            await member.edit(
                roles=list(final_by_id.values()),
                reason=f"Dank Shield approval roles by {staff_member} ({staff_member.id})",
            )

        return grant_roles, None
    except discord.Forbidden:
        raise
    except Exception as e:
        return grant_roles, str(e)
```

### Expected behavior

- Approval role mutation becomes one Discord API operation.
- Unverified role is removed as part of the same final role set.
- Newly granted roles are still returned for existing approval message behavior.
- Unrelated existing normal roles are preserved.
- `@everyone` is excluded from the explicit role edit payload.
- `discord.Forbidden` still bubbles to existing caller handling.

---

## Fallback patch: modlog owner-path suppression only

Use this only if `member.edit(roles=...)` is unsafe for this bot/runtime version.

Inside `stoney_verify/modlog.py::maybe_log_member_update_diff()` only:

1. Detect role-only changes.
2. Resolve configured guild verification role IDs.
3. Confirm all changed role IDs are a subset of configured approval roles.
4. Confirm audit actor/source is the bot.
5. Confirm audit reason matches approval flow.
6. Return handled without posting mechanical role-churn embed.

Do **not** filter `_build_member_context_fields()` globally. Ban/kick logs use that context.

---

## Required validation

- [ ] Approving a user does not create repeated role-change modlog spam.
- [ ] Approval still grants the correct verified roles.
- [ ] Unverified is removed.
- [ ] Unrelated existing member roles are preserved.
- [ ] Manual add/remove of similarly named roles still logs.
- [ ] Manual add/remove of configured Verified role by staff still logs unless it was bot approval flow.
- [ ] Nickname update logs.
- [ ] Timeout update logs.
- [ ] Ban/kick risk context remains visible.
- [ ] Missing View Audit Log permission does not crash.
- [ ] Multi-server role IDs do not cross-suppress.

---

## Why this patch is safe

- Owner file only.
- No startup guard.
- No runtime monkey patch.
- Does not touch ban/kick context helpers.
- Uses configured role IDs, not role names.
