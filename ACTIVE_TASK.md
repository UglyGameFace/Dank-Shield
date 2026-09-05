# ACTIVE TASK

## DS-COMMUNITY-031 — Community Tools correctness, durability, and UX hardening

**Status:** COMPLETE — IMPLEMENTATION + FOCUSED/FULL VALIDATION GREEN
**Branch:** `fix/ds-community-031-community-tools-hardening`
**PR:** #187
**Base:** `d7ee1420e4cadef915919e59bb401408a6489dde` (`main`, merged DS-DESIGN-030)
**Implementation head validated:** `ff4a7cc0da7d5cf8027aae66d6aceffe39760cf3`
**Started:** 2026-09-05
**Completed:** 2026-09-05

## Outcome

The public **Community Tools** section now follows one coherent, durability-first execution model while preserving the compact `/dank home` command surface. Stickies, sticky polls, quiet notices, native polls, embeds, member/server information, permission diagnostics, fun/lookups, persistence, restart handling, and failure paths were all inspected and hardened in scope.

## Scope

- Community Tools public UI and navigation.
- Sticky configuration, delivery, preview, custom sender, cadence, listing, and removal.
- Sticky polls and native Discord polls.
- Quiet-server notices and their server-wide activity runtime.
- Embed Builder.
- Member / Server Info and Permission Check.
- Fun & Lookup network utilities and simple games.
- Community Tools persistence, startup reconciliation, permissions, concurrency, migrations, and focused CI/tests.
- No unrelated moderation, ticketing, verification, Dank Design, profile, or welcome-card redesign.

## Findings / root causes confirmed

- [x] Sticky replacement used destructive ordering and could remove the healthy live copy before a durable replacement existed.
- [x] Sticky + sticky-poll persistence could split across two writes and leave partial state.
- [x] Poll vote persistence was serialized while visible message rendering could still race backwards.
- [x] Quiet watcher exceptions could terminate the watcher and burst activity persistence could save an older timestamp.
- [x] Persistent configuration loading relied on unpaged PostgREST reads and startup reconciliation was unbounded.
- [x] Quiet-notice edits could silently replace the configured destination; preview/destructive actions also used unsafe authority/order.
- [x] Effective channel permissions were not consistently authoritative and Custom Sender could claim a state the bot could not actually maintain.
- [x] Poll/embed/sticky editors had inconsistent preview/publish behavior, silent input coercion, stale-editor risks, and stale poll-state transitions.
- [x] Server Sticky listing truncated, external lookups had brittle provider handling/resource usage, Dice was unnecessarily fixed, and Image AI advertised a provider that did not exist.

## Execution path inspected

- [x] `/dank home` → compact public surface → `open_community_tools()`.
- [x] `CommunityToolsView` and all nested views/modals.
- [x] `community_tools_service.py` Supabase validation/read/write path.
- [x] `community_tools_runtime.py` single-owner `on_message` + `on_ready` runtime.
- [x] Sticky preview/publish path and managed-webhook sender path.
- [x] Sticky-poll vote/update/render path.
- [x] Quiet-notice service/UI/runtime path.
- [x] Lookup service and provider failure handling.
- [x] Community Tools migrations, focused Python tests, static ownership guards, SQL smoke, and full repository CI.
- [x] discord.py poll support/permissions behavior used by the implementation.

## Changes

- [x] Sticky replacement is non-destructive: send replacement → persist delivery → remove old; failed persistence rolls back the replacement.
- [x] Sticky + sticky-poll mode transitions use an atomic service-role-only RPC and remove stale poll rows when leaving poll mode.
- [x] Sticky-poll vote + visible render updates are serialized and obsolete poll cards are rejected.
- [x] Quiet watcher isolates failures, preserves latest activity, and keeps destructive actions persistence-first.
- [x] Persistent configuration reads paginate and startup reconciliation uses bounded concurrency.
- [x] Quiet-notice destination is preserved unless deliberately changed; stale controls and destination-specific tests are guarded.
- [x] Effective channel permissions are authoritative; Custom Sender fails closed and managed-webhook cleanup is handled safely.
- [x] Permission Check is feature-oriented and reports operator/bot blockers.
- [x] Sticky creation uses guided type selection; embed color is editable; invalid ranges/booleans are rejected instead of silently rewritten.
- [x] Native polls, embeds, message/embed stickies, and sticky polls use reviewed preview/publish flows where applicable.
- [x] Stale drafts/editors cannot overwrite newer live state while runtime-only state such as votes/delivery movement is preserved correctly.
- [x] Server Stickies pagination, weather/lookups, redirect/payload validation, Dice notation, and user-facing copy were improved.
- [x] Unavailable Image AI was removed from the public menu rather than pretending a provider exists.
- [x] Focused regressions, static ownership guards, migration/RLS/atomic SQL smoke, rollback/concurrency tests, and CI coverage were expanded.

## Validation / results

- [x] Affected modules compile.
- [x] Focused Community Tools suite passes: **42 passed** on the validated implementation head.
- [x] Smart Stickies regressions pass.
- [x] Community Tools surface/static guards pass.
- [x] PostgreSQL migration/RLS/atomic transition smoke passes, including applying migrations twice.
- [x] Full `Dank Shield CI` passes on the validated implementation head.
- [x] Application Command Size Diagnostics passes.
- [x] Ticket Owner Emergency Override passes.
- [x] Profile Runtime Diagnostics passes.
- [x] Supabase Preview recovered and completed successfully.
- [x] Branch comparison is clean: **24 commits ahead, 0 behind** `main` before this bookkeeping-only completion commit.
- [x] Changed-file scope is limited to the expected 15 Community Tools/runtime/test/migration/workflow/task files.
- [x] Conflict-marker inspection found no `<<<<<<<` / `>>>>>>>` markers in the PR patch.
- [x] Obvious credential-prefix inspection found no committed `ghp_`, `sk-`, or `xoxb-` secrets in the PR patch.

## Cleanup / compatibility

- [x] Existing single-owner Community Tools runtime remains authoritative; no second listener/runtime or monkey patch was introduced.
- [x] Raw webhook URLs/tokens remain forbidden from persistent storage.
- [x] Compact public command roots remain unchanged; improvements stay menu-first.
- [x] Stale/conflicting affected Community Tools logic was integrated or removed.
- [x] Mode transitions/removal do not intentionally leave obsolete sticky-poll state behind.
- [x] Unrelated user/project work was not modified.

## Conflicts / blockers

None remaining for DS-COMMUNITY-031. The earlier Supabase preview service-health warning recovered to a successful check on the validated head.

## Backlog outside this task

- Discord Gateway uptime/reconnect flapping remains a separate runtime/hosting task and was intentionally not mixed into Community Tools.

## Next step

PR #187 is ready to leave draft after the bookkeeping-only completion commit is observed clean by GitHub. No additional Community Tools implementation is required unless runtime testing discovers a new reproducible defect.

## Definition of Done

Met. Every currently exposed Community Tools feature now follows the corrected authoritative paths covered by this task; destructive actions are persistence-safe; restart/reconnect state remains coherent; permissions reflect real channel capability; user-entered values are not silently rewritten; unavailable functionality is not advertised; network/provider failures fail cleanly; scalable reads/reconciliation do not silently omit large deployments; affected regressions and repository CI are green; and the final diff remains scoped and clean.
