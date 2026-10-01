# Active Task

## Active task / outcome

**DANK-SHIELD-PROTECTION-024 — issue #375: Protection Center timeout and owner restore recovery**

FORCE SWITCH accepted from the built-in ticket-category task.

Production baseline: `main` = `b2e75bc88ac8ff6b9bd4e0124c6b2c61ad9c1095` (PR #374 merged).

Active branch: `fix/dank-protection-timeout`.

Active PR: #376 — **Fix Protection timeout and owner member restore**.

Issue: #375 — **Protection Center timeout and owner restore recovery**.

## Completed prior task

PR #374 — **Expose built-in ticket category selection in /tickets** — is merged into production main. This branch starts from that merge so the ticket-category fix is preserved.

Issue #367 Slice 3 — Configurable Verification Framework — remains paused from the earlier FORCE SWITCH. Its policy foundation from PR #373 is already merged. Do not resume Verification Framework work until this Protection incident is complete unless another explicit FORCE SWITCH is given.

## Production symptoms

### Protection navigation timeout

Android production screenshot shows:
- `/dank → Safety & Moderation`;
- pressing **Protection**;
- Discord displays **Dank Shield didn't respond in time**.

### Owner unban does not restore the member

A previously AntiNuke-contained member was unbanned by the server owner, but the member still could not return normally.

## Root cause

### Protection timeout

The shared `public_protection_center._refresh_panel()` performed all of the following before acknowledging the interaction:
1. forced guild-config refresh;
2. Spam Guard settings load;
3. forced security-stats refresh;
4. Protection embed/view construction;
5. only then Discord response/edit.

That can exceed Discord's interaction acknowledgement deadline.

The same shared function also used `interaction.response.is_done()` to choose a followup send. For a deferred component interaction, “response is done” means the interaction has been acknowledged, not that a new ephemeral message is desired. That could create a duplicate/fresh message instead of editing the panel that was clicked.

### Unban / hostile reputation

Dank Shield intentionally persists confirmed hostile identities in `guild_security_actor_reputation`.

In contain mode:
- an active exact-ID reputation survives a normal Discord ban removal;
- `anti_nuke_hostile_actor_runtime._on_member_join()` reloads the active record;
- the re-entry runtime can immediately ban the same ID again.

The durable clear function already existed, but owner recovery was incomplete:
- no normal Protection UI exposed it;
- a native Discord owner unban was not interpreted as an explicit owner clear;
- the canonical `/mod` Ban / Unban by ID path did not clear active hostile reputation first.

## Current implementation

### Protection interaction lifecycle

- `_refresh_panel()` immediately defers when the interaction is still unacknowledged;
- guild config and Spam Guard load concurrently with `asyncio.gather`;
- Protection edits the original interaction/panel after defer;
- followup send is fallback-only when original edit fails;
- forced security-stat refresh happens only after the Protection panel is visible;
- direct `/dank protection` now reuses the same canonical refresh owner instead of duplicating state-loading logic.

### Owner restore behavior

- add owner-intent hostile reputation clear helper;
- native `on_member_unban` resolves the recent unban audit entry;
- only a **physical guild-owner** unban clears an active hostile reputation;
- delegated admin/staff unban does **not** clear durable hostile reputation;
- add **Restore Member** to Protection Center:
  - owner-only;
  - exact Discord user ID;
  - clears active hostile reputation;
  - verifies the clear durably;
  - then removes the Discord ban if one still exists;
- canonical `/mod` Ban / Unban by ID clears owner-restored hostile reputation before Discord unban, closing the fast re-entry race.

## Safety / compatibility

- no guild-specific hardcoding;
- no schema change;
- no weakening of AntiNuke for delegated admins;
- physical server owner remains the only recovery authority for durable hostile reputation;
- active hostile identities remain blocked on rejoin until explicit owner restore;
- inactive/cleared exact-ID rows continue to suppress stale linked-identity inheritance;
- existing Protection callers all reuse the same corrected refresh owner;
- no ticket-category, Verification Framework, Captions, or unrelated implementation in this branch.

## Definition of Done

Issue #375 is complete only when:
- Safety & Moderation → Protection acknowledges immediately and opens without Discord timeout;
- direct `/dank protection`, setup Protection, Invite Shield back, AntiNuke back, and Refresh still render through the shared owner;
- deferred component interactions edit the original panel rather than spawning a duplicate followup;
- live stats failure/latency cannot block Protection from appearing;
- physical-owner native unban clears active hostile reputation for that exact ID;
- delegated admin unban does not clear it;
- Protection **Restore Member** can recover an already-unbanned-but-still-hostile member;
- canonical owner `/mod` unban clears hostile state before Discord unban;
- exact-head CI is green;
- merge and post-merge CI/Supabase/Discloud are green;
- Android Protection open + owner member-restore canaries pass.

## Validation status

PR #376 is open as the focused issue #375 fix. The branch is current with production main and no schema change is required.

Fresh exact-head CI is required after this task-record update.

## Next step

Validate PR #376 on its exact head. If green, perform final diff/branch hygiene, mark ready, merge with the exact expected head, verify post-merge CI/Supabase/Discloud, then run the Android Protection open + Restore Member canaries before closing issue #375.
