# ACTIVE TASK

## DS-AUD-CONTEXTUAL-REPAIR-LIVE-REGRESSION — Fix false Fix Issues failures

**Status:** IMPLEMENTATION / VALIDATION

**Branch:** `audit/contextual-repair-permission-cache-fix`
**Base main:** `6b8abf9f66db55251c2c2e133526d1ac867c5755`
**Previous integrated task:** PR #248 merged and verified on `main` at `6b8abf9f66db55251c2c2e133526d1ac867c5755`.

## User-reported production regression

The guided setup `Fix Issues` action reported four targets as not repaired:

- Verification start channel: still missing `send_messages`, `embed_links`, `attach_files`
- Ticket archive category: still missing `view_channel`
- Ticket transcripts channel: claimed Dank Shield lacked Manage Channels and could not repair its own overwrite
- Moderation log channel: same Manage Channels blocker

This regression takes the Single Active Task Lock. Do not move to Protection or any later audit item until this task is implemented, exact-head validated, merged, and verified on `main`.

## Root causes

1. `permission_repair_core.audit_target()` used `effective.manage_channels` as the prerequisite for `GuildChannel.set_permissions(...)`. Discord's Edit Channel Permissions endpoint actually requires `MANAGE_ROLES` (shown as Manage Permissions in channel UI). That produced false manual blockers when Manage Channels was absent but overwrite editing was authorized.
2. `contextual_permission_repair.repair_context()` called `set_permissions(...)` and then immediately re-audited `guild.get_channel(...)`. discord.py sends the overwrite update over HTTP, while the gateway-backed channel object can still contain the pre-repair overwrite until the Channel Update event arrives. A same-tick cache-only audit can therefore claim a successful repair is still missing.

## Implementation

- permission-overwrite authorization now checks effective `manage_roles`, not `manage_channels`
- Discord Forbidden wording now identifies Manage Roles / Manage Permissions rather than falsely naming Manage Channels
- the contextual post-repair audit fetches each exact configured target from Discord over HTTP before deciding whether repair succeeded
- if the HTTP refresh itself fails, the audit falls back to the cached channel and remains fail-closed rather than claiming success
- no member/@everyone visibility changes, Administrator grants, target guessing, role movement, or explicit-deny clearing were added
- all permission mutation remains in `permission_repair_core`

## Regression coverage

`tests/test_contextual_permission_repair_live_regression.py` covers:

- Manage Roles present + Manage Channels absent does not block overwrite repair
- missing Manage Roles produces the manual overwrite blocker
- successful mutation followed by a stale cached channel is verified against a fresh Discord channel
- failed fresh fetch falls back to cache and does not falsely claim healthy access

## Validation gate

- focused regression tests pass
- full unit suite passes
- Python compile and standalone audits pass
- every triggered PR workflow succeeds on the exact final head
- final branch is 0 behind current `main`
- changed-file scope is limited to the repair core, contextual re-audit, focused tests, and task bookkeeping
- no unresolved review/thread issue
- exact validated head is merged
- resulting merge commit is verified as current `main`

## Backlog after this regression closes

1. Protection contextual repair adoption
2. remaining VC-specific repair cleanup
3. Embed / Status contextual repair adoption
4. admin-only `/dank tickettool-check` contextual repair adoption
5. `/dank protection` remaining non-invite picker/guard cleanup
6. `/dank design` picker migration
7. admin-only legacy setup picker cleanup

## Next step

Open the corrective PR, inspect the exact diff, run the full exact-head validation wave, fix any regression found, merge only the final validated head, verify `main`, then release this lock.
