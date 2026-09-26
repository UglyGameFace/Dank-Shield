# Active Task

## Active task / desired outcome

**P0-ANTINUKE-BULK-DELETE-SELF-PROOF-013 — stop legitimate Dank Shield bulk-message cleanup from being misclassified as bot-token compromise**

Desired outcome: a `message_bulk_delete` audit event caused by this running Dank Shield process must consume a one-time, guild/channel-scoped self-action receipt even when Discord does not surface a usable audit-log reason. A truly unexplained bulk deletion attributed to the bot must still fail closed.

## Scope / single active task lock

Only the AntiNuke self-provenance path for locally issued message deletions is active. This includes the shared reasonless-message fallback only where required to add bulk-delete parity safely. Do not broaden into Invite Shield, Spam Guard policy, channel-cleanup design, verification, tickets, or unrelated AntiNuke redesign.

## Prior task closure

PR #329, **Fix Invite Shield missed app-card and history enforcement**, is merged into `main` as `dec4664b13ed6769937eab6bfc145a01fd54cc1e`.

Its exact head `16841f44f4ee302824d963b766aa3860cb72cd9b` completed every observed workflow successfully: Dank Shield CI, Dank Design Regression CI, Invite Shield CI, Ticket Owner Emergency Override, Schema Authority SQL, Profile Runtime Diagnostics, and Application Command Size Diagnostics.

## Evidence / root cause

Production reported another durable self-compromise quarantine at 21:41 on 2026-09-25, this time for `message_bulk_delete`, after the earlier delayed-`channel_update` lifecycle fix in PR #324.

Current code proves a separate correctness gap:

1. Local POST `/channels/{id}/messages/bulk-delete` requests receive the normal DSA marker and pending authorization.
2. The runtime already creates a second, reason-independent expected-action receipt for local single `message_delete` because Discord audit reasons can be sparse.
3. Local `message_bulk_delete` has no equivalent fallback. It therefore depends entirely on the audit event carrying a usable DSA marker.
4. Dank Shield has multiple legitimate bulk-delete callers, including channel cleanup and Spam Guard.
5. discord.py 2.7.1 models a bulk-delete audit entry with the channel as `entry.target` while `entry.extra` carries only the delete count. The existing target-key fallback reaches `entry.target`, so channel scoping is available.
6. When the DSA marker is absent/unusable, `_audit_guard()` currently reaches the zero-damage unmatched path and persists quarantine/self-ejects even though a matching local bulk-delete request can be proven independently.

The fix must correlate the local request rather than exempting `message_bulk_delete`.

## Execution path

Legitimate bulk cleanup:

`channel.delete_messages(...) -> HTTP POST /channels/{channel}/messages/bulk-delete -> self-action HTTP wrapper -> DSA authorization + reasonless one-time bulk receipt -> Discord audit event -> DSA marker match OR channel-scoped fallback -> consume receipt -> no compromise response`

Unexplained bulk deletion:

`bot-attributed message_bulk_delete -> no matching DSA authorization and no matching local reasonless receipt -> zero-damage durable quarantine/self-ejection`

## Changes

Branch: `fix/antinuke-bulk-delete-self-proof-20260925`

Implemented:

- arm a one-time `message_bulk_delete` expected-action receipt whenever this process issues the protected bulk-delete REST request;
- reuse the existing in-flight/completed lifecycle, so the fallback cannot expire while Discord/rate-limit pacing still owns the request and is cancelled if the request fails;
- scope the fallback to guild + channel and action;
- consume/clear the paired fallback when the stronger DSA marker succeeds;
- do not allow a present-but-invalid DSA marker on direct message deletions to fall through to the reasonless fallback;
- preserve existing integration-delete side-effect behavior;
- add regressions for reasonless local bulk delete, marker-success cleanup, channel scoping, and invalid-marker fail-closed behavior.

## Validation / results

Implementation is being prepared. Required before completion:

- compile the changed runtime and tests;
- run `tests/test_antinuke_self_action_runtime.py`;
- run zero-damage, gateway, guardian, incident, lockdown, product-policy, and runtime-coordinator AntiNuke regressions;
- run the full repository test suite through normal CI;
- inspect the final diff for unrelated changes;
- require all exact-head PR workflows green before merge.

## Cleanup / conflicts

No protected action is exempted. No second listener, retry layer, alternate AntiNuke owner, or global trust bypass is being added. The fix extends the existing authoritative self-action receipt mechanism to the bulk-delete case that lacked parity with single-message deletion.

## Blockers / risks

The production screenshot proves the unmatched bulk-delete path fired, but no 21:41 production log excerpt is available in this conversation to identify which legitimate caller initiated that exact bulk request. The repair is caller-independent because it binds directly to the authoritative outbound REST request.

## Backlog

Preserve unrelated reported issues without investigation inside this task.

## Next step

Publish the focused implementation, open a draft PR, run exact-head CI, repair only same-root failures, then perform final cleanup/conflict review before merge.
