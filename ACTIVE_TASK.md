# ACTIVE TASK

## DS-STICKY-026 — Smart StickyBot-style community tools

**Status:** IN PROGRESS — AUDIT COMPLETE, IMPLEMENTING
**Branch:** `feature/ds-sticky-026-smart-community-tools`
**Base:** PR #179 validated head `ea373914c509ae2eeab47886a2333153a87a846f` (stacked so the restored `/dank purge` contract is preserved)
**Started:** 2026-08-11

## Scope

Audit StickyBot's documented feature set and integrate the useful capabilities into Dank Shield without copying StickyBot code, reintroducing a legacy prefix-command surface, duplicating existing Dank Shield systems, or adding multiple competing message listeners.

Target product surface:

- A menu-first **Community Tools** center reachable from `/dank home`.
- Persistent per-channel sticky messages with create/edit, pause/resume, remove, list, safe cadence, plain/embed modes, image/thumbnail support, custom sender persona, and sticky polls.
- General polls, member/server info, embed builder, and channel permission diagnostics.
- Lightweight community utilities that do not require paid credentials: weather, Wikipedia/random Wikipedia, WikiHow random link, Urban Dictionary with NSFW guard, dice, coin flip, and name compatibility.
- StickyBot's premium-only sticky presentation features are ordinary Dank Shield capabilities; there is no paid feature gate.
- Existing Dank Shield Help/Status/Profile/Setup systems remain canonical instead of being duplicated as StickyBot-style aliases.

## Audit findings

1. StickyBot core supports stick/create-or-edit, stop, restart, remove, and server-wide active-sticky listing. Its documented default resend trigger is 15 seconds or 5 intervening messages.
2. StickyBot premium adds embed stickies, slower/custom speed, small/big images, webhook persona stickies, and sticky polls with pause/resume/end/reset.
3. StickyBot utility commands add yes/no and multi-choice polls, user info, server info, embed creation, and permission checks.
4. StickyBot fun/lookup commands add weather, Wikipedia, random Wikipedia, image keyword recognition, WikiHow, Urban Dictionary, compatibility, dice, and coin flip.
5. Dank Shield currently has no sticky-message implementation and no general poll utility.
6. Dank Shield already owns Help, Status, profile/member intelligence, setup/diagnostics, and compact command-surface infrastructure; those must be reused rather than duplicated.
7. Final public Discord surface is intentionally tiny: normal feature work belongs behind `/dank home`; only `home`, `purge`, and `upload` are direct `/dank` children on the validated base.
8. Dank Shield already has several `on_message` consumers. The new feature must register exactly one canonical sticky/community runtime listener through the existing command-module registrar instead of another monkey patch.
9. The repo already depends on `aiohttp` and `discord.py>=2.4,<3`; no new package is required for no-key HTTP utilities or bot-managed webhooks.
10. Raw user-supplied webhook URLs are unnecessary and create secret-handling risk. Dank Shield will use bot-managed channel webhooks/custom sender fields where permitted, with a safe bot-message fallback.
11. StickyBot's custom prefix is not applicable because Dank Shield deliberately uses slash commands and guided UI.
12. StickyBot's image-AI keyword command has no existing Dank Shield AI provider/credential. It will be represented as an explicitly unavailable capability until a real configured vision provider exists rather than silently adding an unreliable third-party dependency.

## Planned changes

- [ ] Add canonical sticky persistence/service layer with one row per guild/channel and restart-safe state.
- [ ] Add one burst/rate-safe sticky runtime listener with per-channel locks, self/webhook-loop suppression, stale-message cleanup, and restart reconciliation.
- [ ] Add sticky poll model/view with one-vote-per-user state, pause/resume/reset/end, and final results.
- [ ] Add Community Tools center and sticky editor/status/list controls behind `/dank home` without adding direct slash-command children.
- [ ] Add general polls, embed builder, member/server info, and permission diagnostics.
- [ ] Add no-key community lookups/games with timeout/error/NSFW safeguards.
- [ ] Add bot-managed custom sticky persona support without storing raw webhook URLs.
- [ ] Add Supabase migration plus static migration guard/smoke coverage.
- [ ] Add focused runtime/service/UI tests and command-surface regression coverage.
- [ ] Preserve PR #179 purge surface and PR #180 member-action responsiveness unchanged.

## Validation

- [ ] Targeted sticky service/runtime tests.
- [ ] Sticky poll interaction/state tests.
- [ ] Community Tools UI and permission tests.
- [ ] Command-surface regression proving no new direct `/dank` child.
- [ ] Supabase migration/static validation.
- [ ] Python compile/static validation.
- [ ] Full repository unit suite.
- [ ] Standalone repository checks and public command audits.
- [ ] Final diff/conflict/duplicate-listener/dead-code inspection.

## Cleanup status

- No implementation has been added yet.
- Existing Help, Status, Profile, Setup, Diagnostics, and moderation systems remain canonical.
- No prefix parser, user-supplied webhook-secret storage, or second generic bot `on_message` monkey patch will be introduced.

## Blockers

- Image keyword recognition requires a real vision provider. No provider is configured in the repository, so the UI must fail clearly rather than inventing one.
- PR #179 is validated and ready to merge but intentionally not merged by the assistant; this feature branch is stacked from its exact validated head to preserve its command contract.

## Backlog

None added after DS-STICKY-026 became active.

## Definition of Done

DS-STICKY-026 is complete only when the documented StickyBot capability families have been mapped deliberately into Dank Shield, the sticky system is persistent/restart-safe/rate-safe and has exactly one canonical runtime owner, sticky and normal polls behave correctly, utility features have permission/NSFW/network failure safeguards, the compact public command surface is preserved, no raw webhook secret is stored, targeted/regression/migration/compile/full-suite/standalone audits pass, and final conflict/duplicate/dead-code inspection is clean.
