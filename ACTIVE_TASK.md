# ACTIVE TASK

## DS-COMMUNITY-031 — Community Tools correctness, durability, and UX hardening

**Status:** IN PROGRESS — ROOT CAUSES CONFIRMED, IMPLEMENTATION STARTED
**Branch:** `fix/ds-community-031-community-tools-hardening`
**Base:** `d7ee1420e4cadef915919e59bb401408a6489dde` (`main`, merged DS-DESIGN-030)
**Started:** 2026-09-05

## Outcome required

Make the entire public **Community Tools** section reliable and coherent rather than a collection of individually plausible buttons. Preserve the compact `/dank home` command surface while hardening stickies, sticky polls, quiet notices, native polls, embeds, member/server information, permission diagnostics, fun/lookups, persistence, restart behavior, and failure handling.

The task is complete only when the real execution paths are corrected, affected UX is internally consistent, regressions cover the failures, migrations/static guards remain safe, and the final branch passes applicable repository validation.

## Scope

- Community Tools public UI and navigation.
- Sticky configuration, delivery, preview, custom sender, cadence, listing, and removal.
- Sticky polls and native Discord polls.
- Quiet-server notices and their server-wide activity runtime.
- Embed Builder.
- Member / Server Info and Permission Check.
- Fun & Lookup network utilities and simple games.
- Community Tools persistence, startup reconciliation, permissions, concurrency, and focused CI/tests.
- No unrelated moderation, ticketing, verification, Dank Design, profile, or welcome-card redesign.

## Findings / root causes

### Durable delivery and runtime

- [x] Sticky refresh currently deletes the previous live message **before** the replacement is sent and durably recorded. A failed send can therefore erase a healthy sticky; a failed delivery-state write can leave a stale DB pointer and later duplicate/orphan messages.
- [x] Sticky-poll creation writes the sticky row and poll row separately. A failure between writes can leave `mode=poll` without poll state.
- [x] Sticky poll vote storage is serialized, but the public message edit is not. Concurrent voters can save correctly and still race the visible totals backwards.
- [x] Quiet watcher has no per-iteration/per-guild exception isolation; one unexpected runtime error can kill the watcher task.
- [x] Quiet activity persistence writes the timestamp captured when the worker was scheduled instead of the newest in-memory activity after a burst, so a quick restart can restore an older quiet timestamp.
- [x] Enabled sticky / quiet-notice startup queries are not explicitly paginated, which is unsafe for a public multi-server bot once PostgREST row limits are reached.
- [x] Startup reconciliation fan-outs every configured item at once instead of using bounded concurrency.

### Configuration authority / destructive ordering

- [x] Editing an existing guild-wide Quiet Server Notice from another text channel silently replaces its destination with the editor's current channel.
- [x] Quiet-notice temporary tests post in the panel's current channel, not necessarily the configured destination.
- [x] Pause/remove delete the current quiet notice before durable state is changed. If persistence fails, an enabled config can survive and repost after its visible notice was destroyed.
- [x] Sticky cadence editing drops current sticky-poll state from the returned center view, causing an existing poll to look like a new-poll action.
- [x] Switching a sticky from poll mode to plain/embed leaves stale poll state unless explicitly cleaned.

### Permissions and truthfulness

- [x] Several Community Tools permission checks use guild-level permissions instead of effective channel permissions, ignoring channel overwrites.
- [x] Custom Sender can be saved even when Dank Shield cannot manage webhooks in the destination, after which runtime silently falls back to a normal bot message while UI still claims a custom sender is active.
- [x] Native poll creation does not explicitly check Discord's poll permissions even though discord.py 2.4+ exposes poll permission flags.
- [x] Permission Check reports only Dank Shield and does not distinguish required vs optional feature permissions or the invoking user's effective access.
- [x] Quiet Server Notice copy says "whole server" even though activity coverage can only include channels/messages Dank Shield can receive.

### Input/UX consistency

- [x] Sticky type is entered as free text (`plain`/`embed`) and the sticky editor cannot edit the persisted embed color.
- [x] Several modal fields advertise hard ranges or yes/no values but silently clamp/coerce invalid input instead of rejecting it.
- [x] Native poll choice de-duplication happens before visible truncation, allowing two long choices to collapse to the same displayed answer.
- [x] Embed Builder and native poll posting are immediate while stickies already use the safer preview/publish pattern.
- [x] Existing sticky-poll editing is immediate and can replace another sticky mode without a reviewed draft step.
- [x] Sticky cadence copy sounds like an independent timer even though time eligibility is evaluated when human activity arrives.
- [x] Server Stickies silently truncates at 25 rows.
- [x] Image AI is exposed as a dead public button even though no provider exists.
- [x] External lookup parsing can leak malformed provider payload exceptions past `CommunityLookupError`; each lookup also creates a fresh HTTP session and has no concurrency bound/cache.
- [x] Fixed two-dice behavior is needlessly limited for a utility advertised as Dice.

## Execution path inspected

- [x] `/dank home` → compact public surface → `open_community_tools()`.
- [x] `CommunityToolsView` and all nested views/modals.
- [x] `community_tools_service.py` Supabase validation/read/write path.
- [x] `community_tools_runtime.py` single-owner `on_message` + `on_ready` runtime.
- [x] Sticky preview/publish path and managed-webhook sender path.
- [x] Quiet-notice service/UI/runtime path.
- [x] Lookup service and provider failure handling.
- [x] Community Tools migrations, focused Python tests, static ownership guards, and SQL workflows.
- [x] discord.py 2.4+ poll support/permissions checked against current library documentation.

## Planned changes

- [ ] Make sticky replacement non-destructive: send replacement → persist new delivery → remove old; roll back the new replacement if durable state cannot be recorded.
- [ ] Add an atomic service operation for sticky + sticky-poll state transitions, including stale poll cleanup when leaving poll mode.
- [ ] Serialize sticky-poll vote + visible refresh per channel.
- [ ] Make quiet watcher resilient and persist the latest observed activity.
- [ ] Paginate persistent configuration reads and bound startup reconciliation concurrency.
- [ ] Preserve quiet-notice destination on ordinary edits; add an explicit destination-change action and test in the saved destination.
- [ ] Persist pause/remove before deleting live quiet messages.
- [ ] Use effective channel permissions and validate both operator and bot capabilities for each feature.
- [ ] Make Permission Check feature-oriented and show operator + bot blockers.
- [ ] Replace error-prone sticky type text with guided type selection; expose embed color.
- [ ] Reject invalid ranges/booleans instead of silently changing user input.
- [ ] Add safe preview/publish to immediate-post builders where practical without expanding the slash-command surface.
- [ ] Improve server sticky listing, wording, lookup robustness/resource reuse, dice utility, and remove unavailable Image AI from the public menu.
- [ ] Expand focused runtime/service/surface/static regressions and affected CI path coverage.

## Validation / results

- [ ] Affected modules compile.
- [ ] Focused Community Tools service/runtime tests pass.
- [ ] Smart Stickies regressions pass.
- [ ] Community Tools surface/static guards pass.
- [ ] SQL migrations/workflows pass if persistence schema/RPC changes are required.
- [ ] Full repository test/CI validation passes on the exact final PR head.
- [ ] Final diff / accidental-file / secret / conflict-marker inspection passes.

## Cleanup / compatibility

- [x] Existing single-owner Community Tools runtime is authoritative; do not add a second listener/runtime or monkey patch.
- [x] Raw webhook URLs/tokens remain forbidden from persistent storage.
- [x] Compact public command roots stay unchanged; improvements remain menu-first.
- [ ] Remove/integrate stale or conflicting Community Tools logic encountered in the affected paths.
- [ ] Verify no obsolete poll/quiet/sticky state is left behind after mode transitions/removal.

## Conflicts / blockers

- None identified at task start. The older `feature/ds-sticky-026-smart-community-tools` branch is obsolete and intentionally not reused because current `main` contains substantially newer merged work.

## Backlog outside this task

- Discord Gateway uptime/reconnect flapping is a separate runtime/hosting task and is not being mixed into Community Tools.

## Next step

Implement the durability and state-authority corrections first, then build UX improvements on top of those corrected primitives before running focused and full validation.

## Definition of Done

Every currently exposed Community Tools feature follows one authoritative, tested execution path; destructive actions are persistence-safe; restart/reconnect behavior remains coherent; permissions reflect real channel capability; user-entered values are not silently rewritten; public UI does not advertise unavailable functionality; network/provider failures fail cleanly; scalable reads/reconciliation do not silently omit large deployments; affected regressions and CI are green; final diff is scoped and clean; and any remaining limitation is stated explicitly rather than hidden behind fallback behavior.
