# Active Task

## Active task / outcome

**DANK-SHIELD-COMMUNITY-PINGS-021 — issue #367 slice 2: Generic Community & Pings Builder**

Replace the fixed Stoner/Sesh product model with a bounded, revisioned, per-guild Community & Pings system while preserving legacy compatibility and keeping later #367 slices separate.

Production baseline: `main` = `2dd78604255026561c22a97507a6d7afd5b8b93f` (PR #371 merged and deployed).

Active branch: `fix/367-role-staff-community-pings-ux`.

Active PR: #372 — **Fix staff /role Community & Pings navigation**.

Issue #367 remains the umbrella epic. Slice 1 is complete. This record covers slice 2 only.

## Scope

Included:
- bounded `community_pings_v2` model;
- up to 25 options and 10 groups;
- Community vs Notification semantics;
- label, emoji, description, group, ordering, enabled state, and member-removable policy;
- prerequisite roles, mutual exclusion, and group selection caps;
- Search-Safe role discovery;
- generic **Community & Pings Manager**;
- generic **My Community & Pings** member picker;
- generic profile Community identity rendering;
- `/role` self-service through the same Community & Pings model;
- generic `/toke` starter and notification capabilities;
- role-dependency discovery for mapped/prerequisite roles;
- legacy Stoner/Sesh compatibility before v2 exists;
- first model save converts the legacy preset to v2 without deleting Discord roles;
- CAS-protected writes for the v2 model.

Explicitly out of scope:
- Configurable Verification Framework;
- Action & Reminder Center;
- Member Activity & Reverification Lifecycle;
- Community & Pings acknowledgement/reminder delivery;
- `/toke` cooldown or Cheers behavior;
- quiet-server / active-VC behavior;
- Live Captions, AntiNuke, Minecraft, Unity, Idle Grow, or other projects.

## Status

**PR #371 is merged and production gates are green. Slice 2 remains open because the Android canary found one same-slice /role UX defect.**

The canary showed owner/staff users the member-facing **Community & Pings** shortcut inside `/role` and then appended staff tools beneath it. PR #372 is the focused follow-up: member and staff Community & Pings surfaces are now audience-aware.

PR #372 is the only active implementation task. No later #367 slice has started.

## Findings / root cause

Original root cause:
- Community & Pings was hardcoded around `stoner_role_id`, `sesh_ping_role_id`, and one preferred `toke_channel_id`;
- member selection, profile identity, `/role`, and `/toke` special-cased those identities.

Final pre-merge audit found additional same-slice correctness issues:
1. A member could keep an old **My Community & Pings** panel open while the model changed. The callback reloaded current config but interpreted the stale panel values against the new option set, which could treat newly-added choices as deselected.
2. `/role` used one fresh config read to authorize a role and a second fresh read for Community rules. If the mapping disappeared between reads, the toggle could continue under authorization from the first snapshot.
3. The member picker and `/role` used different per-member locks even though both mutate the same Community & Pings role state.
4. `with_option()` could silently replace an existing option when a new option reused an existing key or role identity.
5. Identical option/group updates still bumped the model revision even though the contract says revisions represent meaningful changes.
6. `public_toke.py` retained an unused legacy Community-role lock after the generic member picker replaced that path.
7. `ACTIVE_TASK.md` contained more than 1,000 lines of stale historical task state, including an obsolete Live Captions “Next step,” despite the current #367 Slice 2 header.
8. A malformed-but-present `community_pings_v2` value fell back to legacy Stoner/Sesh because parsing treated non-mapping v2 as “missing.” That violated the authority contract that legacy fallback is allowed only when v2 is absent.
9. Production Android canary found that `/role` rendered the member-facing **Community & Pings** shortcut for owner/staff users, then appended management tools. The same concept therefore appeared in two different contexts and made the staff doorway ambiguous.

## Runtime execution path

Staff configuration:
`/dank` registry route / Roles & Profiles shortcut
→ `public_toke.open_community_ping_setup`
→ generic `public_community_pings.open_community_ping_setup`
→ `CommunityPingsManagerView`
→ fresh guild config
→ domain mutation helper
→ `compare_and_swap_guild_config_key(COMMUNITY_PINGS_KEY)`.

Member picker:
**My Community & Pings**
→ current model + safe live-role resolution
→ multi-picker
→ one shared per-guild/member Community lock
→ fresh model equality/staleness check
→ fresh role-safety resolution
→ prerequisite / exclusion / group-cap / enabled / removable validation
→ Discord role add/remove
→ profile-card invalidation.

`/role` self-service:
role resolution
→ shared per-guild/member Community lock
→ one fresh guild-config snapshot
→ role safety + self-service authority from that same snapshot
→ Community selection validation when mapped
→ Discord role add/remove
→ profile-card invalidation.

`/toke`:
fresh guild config
→ `parse_community_pings`
→ generic `toke_start` / `toke_notify` capabilities
→ legacy fallback only when v2 does not exist
→ preferred text channel remains separate delivery config.

## Changes

Current PR implementation includes:
- generic `community_pings_v2` service/model;
- generic staff manager and member picker;
- Search-Safe role browser integration;
- legacy preset parsing and conversion;
- v2-authoritative `/toke` capability lookup;
- profile identity and role-dependency integration;
- `/role` integration;
- focused regression coverage.

Final audit remediation added:
- canonical `community_member_lock()` shared by the member picker and `/role`;
- stale member-panel rejection when the current model differs from the model that rendered the picker;
- single-snapshot `/role` authorization + Community validation;
- duplicate option key/role conflict rejection instead of silent replacement;
- no-op option/group updates preserve the current revision;
- Add Group refuses to overwrite an existing group key;
- removal of the dead legacy Community-role lock in `public_toke.py`;
- generic Cheers denial copy now refers to the configured `/toke` starter role without changing Cheers behavior.
- malformed-but-present v2 now fails closed as an empty v2 model, so legacy Stoner/Sesh IDs cannot reactivate `/toke` capabilities or profile Community identity.

## Legacy compatibility contract

- If `community_pings_v2` is absent, existing Stoner/Sesh IDs are parsed as a compatibility preset.
- First Community & Pings model save converts that preset to v2.
- Conversion does not delete Discord roles.
- Once v2 exists, v2 is authoritative.
- If the stored v2 blob is malformed, the system fails closed as v2 rather than falling back to legacy IDs.
- Legacy Stoner/Sesh IDs cannot silently reactivate removed v2 capabilities.
- The old Stoner/Sesh setup remains only a legacy helper; normal public entrypoints use the generic manager/member owner.
- Preferred `/toke` channel remains a separate delivery setting.

## Concurrency / safety

- v2 admin model writes use `compare_and_swap_guild_config_key`.
- stale admin writes fail closed and require refresh.
- member option roles are filtered through the existing profile-safe role blocker.
- member execution re-checks current config and current role safety.
- stale member panels fail closed instead of applying old values to a new model.
- member picker and `/role` share one member mutation lock.
- Discord permission/hierarchy failures remain fail-closed at mutation time.
- option/group limits remain bounded for public-bot scale.

## Validation / results

PR #371 production evidence:
- final PR head `4e1056083fd7ef26c410092eef801a0387392648` passed all required PR workflows;
- merge commit `2dd78604255026561c22a97507a6d7afd5b8b93f` had a tree identical to the final PR head;
- canonical Dank Shield CI #3301 — success;
- chained Deploy Supabase migrations run `36777460887` — success on the exact merge SHA;
- Discloud commit status — success on the exact merge SHA;
- production `main` remained the exact merge SHA through validation.

Android canary:
- found one remaining same-slice UX issue in `/role`: owner/staff saw the member Community & Pings shortcut alongside staff management controls;
- PR #372 contains the focused fix and regression tests;
- exact head `4aa2e4e759b9188026a883837e730b825de49cb9` failed Dank Shield CI only because one older profile regression still expected the old member button label `Community & Pings`; runtime behavior was correct and 2355 other tests passed;
- the stale assertion is updated to the audience-aware member label `My Community & Pings`;
- fresh exact-head PR #372 CI is required before merge;
- after merge, rerun the `/role` owner/staff + normal-member canary before closing Slice 2.

## Cleanup / conflicts

Confirmed scope boundary:
- no Verification Framework implementation added;
- no Action & Reminder Center implementation added;
- no Activity/Reverification implementation added;
- no quiet-server/VC logic added;
- no `/toke` cooldown or Cheers timeout repair added.

The final audit removed one obsolete Community-role lock and consolidated member Community role mutation locking across the two active self-service entrypoints.

## Blockers / risks

Current blocker: fresh PR #372 exact-head CI after the stale-label test correction, followed by the focused post-deploy `/role` Android canary.

Live behavior cannot be called complete until deployment and Android canaries verify manager and member interactions on the merged production SHA.

## Backlog

Issue #367 future slices, in order:
1. Slice 3 — Configurable Verification Framework.
2. Slice 4 — Action & Reminder Center foundation/integrations.
3. Slice 5 — Member Activity & Reverification Lifecycle.
4. Slice 6 — cross-feature lifecycle polish, diagnostics, scale/soak validation.

Separate retained regression backlog:
- quiet-server notice should account for configured meaningful activity such as active voice participation;
- Cheers button timeout needs its own root-cause investigation of persistent view/callback ownership and must not be assumed to require VC presence.

## Definition of Done

Slice 2 is complete only when:
- legacy Stoner/Sesh behavior works before conversion;
- generic options/groups can be safely added, edited, removed, and reordered;
- unsafe member-selectable roles are blocked;
- prerequisites, exclusion, group caps, enabled state, and non-removable rules are enforced from fresh current state;
- stale member/admin interactions fail closed;
- member picker and `/role` cannot race each other through separate locks;
- generic `/toke` capabilities work without legacy reactivation after v2 exists;
- exact-head CI passes;
- final diff is clean and branch remains current;
- PR is marked ready and merged with an expected-head guard;
- canonical post-merge CI/deployment evidence is green;
- Android manager/member canaries pass.

## Next step

Validate PR #372 on its exact head. If CI is green, recheck branch divergence and diff hygiene, mark it ready, merge with the exact expected head SHA, verify post-merge CI/Supabase/Discloud, then rerun the focused Android `/role` canary for owner/staff and a normal member. Close Slice 2 only after that passes.
