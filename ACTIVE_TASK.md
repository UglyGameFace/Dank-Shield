# ACTIVE TASK

## DS-STICKY-026 — Smart StickyBot-style community tools

**Status:** FINAL VALIDATION — IMPLEMENTED, SUPABASE PREVIEW GREEN
**Branch:** `feature/ds-sticky-026-smart-community-tools`
**Base:** current `main` after merged PR #179 (`591ca027e66d79a51ca3caaae539d1ca0fa97d48`)
**PR:** #181 (draft until exact-final-head CI is green)
**Started:** 2026-08-11

## Scope

Integrate the useful StickyBot capability families into Dank Shield without copying StickyBot code, reintroducing a legacy prefix-command surface, duplicating existing Dank Shield systems, or adding competing message listeners.

Target product surface:

- Menu-first **Community Tools** center reachable from `/dank home`.
- Persistent per-channel sticky messages with create/edit, pause/resume, remove, list, safe cadence, plain/embed modes, image/thumbnail support, custom sender persona, and sticky polls.
- General Discord polls, member/server info, embed builder, and channel permission diagnostics.
- No-key community utilities: weather, Wikipedia/random Wikipedia, WikiHow, NSFW-gated Urban Dictionary, dice, coin flip, and name compatibility.
- Existing Dank Shield Help/Status/Profile/Setup systems remain canonical.

## Root cause / implementation findings

1. StickyBot core maps cleanly to one persistent Dank Shield sticky owner per channel with 15-second / 5-human-message defaults.
2. Premium-style presentation features do not need a Dank Shield paywall; embeds, images, cadence, bot-managed sender personas, and sticky polls are ordinary Community Tools capabilities.
3. Dank Shield already owns Help, Status, profile/member intelligence, setup/diagnostics, and a deliberately compact public command surface, so this feature stays behind `/dank home`.
4. Exactly one Community Tools runtime listener owns sticky message movement; no `@bot.event` replacement or channel-history scan was added.
5. Unknown non-sticky channels use an in-memory zero-database hot path; Supabase is not queried for every ordinary message.
6. Sticky refresh burst coalescing carries the already-made trigger decision through the worker so counter reset cannot lose a refresh.
7. Raw webhook URLs/tokens are never persisted; custom sender personas use a bot-managed webhook only when permitted.
8. Image recognition has no configured vision provider, so the UI reports it unavailable instead of inventing an unreliable dependency.
9. Community Tools migration uses a 14-digit Supabase timestamp and has an apply-twice PostgreSQL smoke test with RLS and persistence checks.
10. Production Supabase had a separate pending migration blocker: `20260802042000_ticket_category_setup_selection.sql` introduced `cod_services` and `game_services`, while the older `ticket_categories_intake_type_check` rejected both before reconciliation could finish.
11. `20260802041900_expand_ticket_category_intake_types_v2.sql` now expands that production constraint immediately before the blocked migration while preserving all prior allowed routing values and rejecting unknown values.
12. A production-like SQL workflow reproduces the historical constraint, applies the preflight twice, applies the formerly failing migration twice, verifies COD/game rows, and verifies invalid intake values remain blocked.
13. The Supabase PR preview's stale migration history was cleared by recreating the preview branch; Database, Services, APIs, Configurations, Migrations, Seeding, and Edge Functions all reported green afterward.
14. Native community polls now require the invoking member's own channel `Send Messages` permission both before the modal opens and again on submit, preventing Dank Shield from acting as a posting proxy into read-only channels.
15. Sticky polls are normalized/validated before the sticky row is persisted, preventing invalid poll input from leaving a mode=`poll` sticky without a valid poll definition.

## Changes

- [x] Persistent sticky persistence/service layer with restart-safe state.
- [x] Single burst/rate-safe sticky runtime listener with per-channel locks and loop suppression.
- [x] Sticky poll model/view with one-vote-per-user state and pause/resume/reset/end controls.
- [x] Community Tools center behind `/dank home` with no new direct `/dank` child.
- [x] General polls, embed builder, member/server info, and permission diagnostics.
- [x] No-key community lookups/games with timeout/error/NSFW safeguards.
- [x] Bot-managed custom sticky persona support without raw webhook-secret storage.
- [x] Community Tools Supabase migration plus dedicated SQL smoke coverage.
- [x] Zero-database non-sticky message fast-path regression coverage.
- [x] Production ticket-category intake constraint preflight and exact historical failure reproduction.
- [x] Native poll channel-permission guard and sticky-poll pre-write validation.
- [x] Temporary branch finalizer removed from the final diff.
- [x] Preserve merged #179 direct purge surface and #180 member-action responsiveness.

## Validation

- [x] Targeted sticky service/runtime tests passed on prior exact heads.
- [x] Community Tools UI/static safety tests passed, including final poll-safety static guard.
- [x] Unknown-channel zero-database fast-path tests passed.
- [x] Community Tools SQL Smoke passed after the production migration repair.
- [x] Ticket Category Intake Preflight SQL passed and reproduces the Aug 2 production failure conditions.
- [x] Application Command Size Diagnostics passed after the production migration repair.
- [x] Ticket Owner Emergency Override passed after the production migration repair.
- [x] Profile Runtime Diagnostics passed after the production migration repair.
- [x] Supabase PR preview recreation cleared the stale migration-history error and all preview tasks reported green.
- [x] Branch is zero commits behind `main` and has no inline review threads.
- [ ] Dank Shield CI exact-final-head run must complete after this final task-record commit.

## Cleanup status

- One canonical Community Tools runtime listener; no duplicate sticky engine.
- No prefix parser or new direct slash-command family.
- No raw webhook URL/token storage.
- No channel history scans for sticky movement.
- No temporary finalizer workflow remains in the final tree/diff.
- Existing Help, Status, Profile, Setup, Diagnostics, purge, and moderation systems remain canonical.
- Production migration repair is an idempotent preflight ordered directly before the previously blocked migration; it does not rewrite remote migration history.

## Blockers

- Only the exact-final-head GitHub CI run remains before PR #181 can be marked ready for review.

## Backlog

- Image keyword recognition can be enabled later only when Dank Shield has a real configured vision provider.

## Definition of Done

DS-STICKY-026 is complete only when the StickyBot capability families are deliberately mapped into Dank Shield, the sticky system is persistent/restart-safe/rate-safe and has exactly one canonical runtime owner, sticky and normal polls behave safely, utility features have permission/NSFW/network-failure safeguards, the compact public command surface is preserved, no raw webhook secret is stored, the production migration chain is no longer blocked by the legacy intake constraint, targeted/regression/migration/compile/full-suite/standalone audits pass, the Supabase PR preview is clean, and final conflict/duplicate/dead-code inspection is clean. Merge and production deployment remain separate actions.