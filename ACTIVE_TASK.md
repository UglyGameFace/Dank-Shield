# Active Task

## Active task / desired outcome

**SEARCH-SAFE-MASTER-AUDIT-018 — prove and harden Search-Safe Naming, shared guild configuration, Server Design integration, Member Setup coexistence, and production-facing UI from first principles**

Production baseline at audit start: `main` = `7bf1bf0799ffaf70cb4b207da9e42750797123cf`, the merge of PR #362.

The active outcome is the 100-point master audit requested for Search-Safe Naming plus the later explicit global footer audit. The work is not complete when individual tests turn green. Completion requires the final exact-head branch to be re-audited for architecture ownership, integration wiring, persistence/concurrency, permissions, Unicode behavior, command schema, scaling, CI coverage, final diff, deployment startup, and live canary behavior.

## Scope / single active task lock

This is the only active implementation task.

The remediation order is severity-driven:
1. P0 canonical guild-config lost-update race.
2. P0 Search-Safe actor-vs-target role hierarchy authorization.
3. P1 Search-Safe/Dank Design/Role Editor/runtime integration and concurrency defects.
4. P2 reliability/UX/observability hardening, including Member Setup Access Role vs Prerequisite Role clarity and the global Dank Shield footer cleanup.
5. Exact-head re-audit, deployment evidence, and live canary/soak validation.

Do not switch to unrelated Dank Shield work. Findings outside this master-audit scope are backlog only unless they share the same root cause or are required to validate the active repair.

## Read-only audit baseline / findings

The initial audit was completed without modifying code.

Confirmed baseline:
- PR #362 merged to production `main`; it is not draft.
- PR #362 head: `f5204ebb5297e90c0e94b16337e29dac30425202`.
- PR #362 merge base: `3427a1700fe522ce95239bbf96690aab3aaeb3b8`, exactly merged PR #361.
- PR #362 merge commit/current production baseline: `7bf1bf0799ffaf70cb4b207da9e42750797123cf`.
- PR #361 Member Setup files survived reconciliation; the overlap is primarily the role doorway/task record.
- Exact PR-head workflows observed for #362 were green, and push CI on the merge commit was green. Those results do not validate fixes made by this remediation branch.

Confirmed P0 findings:
- **P0 config lost update:** canonical `guild_config` previously did client-side read → rebuild complete JSON compatibility payloads → update. Concurrent writers such as Naming Identity and Member Setup could read the same old row and overwrite each other's unrelated nested changes.
- **P0 actor hierarchy:** Search-Safe role mutation checks the bot hierarchy but does not yet reuse the human actor-vs-target hierarchy boundary already enforced by the canonical Role Editor.

Confirmed P1 findings include:
- Search-Safe Design-plan adjustment helper exists but is not wired into the production Dank Design plan/apply path.
- `policy_adjusted_name()` exists but has no production caller, so Dank Shield-created styled resources can require a second gateway-triggered PATCH.
- Dank Design direct rename/saved-rule/Undo behavior can disagree with Search-Safe enforcement.
- rapid alias debounce uses unordered sets, losing semantic rename chronology.
- naming policy/config caches are process-local and stacked over canonical guild config.
- naming resource lock cleanup can race waiters.
- naming persistence failure can discard already-popped pending aliases/deletes.
- unknown naming schema versions are not rejected explicitly.
- Search-Safe normalization currently applies compatibility normalization more broadly than the stated known-styled-letter contract.
- reviewed repair preview is not an immutable/revalidated transaction input.
- naming mutation traffic does not yet share one proven aggregate pacing owner.

Confirmed P2/product findings include:
- Member Setup **Access Role** is the role Dank Shield grants/removes to unlock protected categories, while **Prerequisite Role** is only an optional eligibility requirement such as Verified. The current UI presents them as near-identical role pickers and does not explain that relationship clearly.
- Every production-facing Dank Shield embed footer must be audited. Internal/debug/operator metadata such as raw guild IDs, config sources, runtime identifiers, schema/version tags, monitor/service names, `dank_shield:...` identifiers, or phrases such as “canonical live runtime” must not leak into ordinary member/admin UI. Useful user guidance, safety context, pagination, counts, and confirmation semantics should remain.

## Completed remediation slice: P0 canonical guild-config atomicity

The shared `guild_configs` lost-update root cause has been repaired on this branch.

Implemented:
- service-role-only PostgreSQL RPC `patch_dank_guild_config(guild_id, patch, clear_keys)`;
- database-side sparse JSON set/clear behavior instead of client-side whole-object replacement;
- canonical `guild_config` routes normal writes and explicit clears through that RPC;
- setup/onboarding/service-mode compatibility paths that could write `guild_configs` directly were consolidated back into the canonical owner;
- full and selective Core Settings restores now perform sparse canonical mutations rather than restoring stale whole JSON containers;
- concurrent Naming Identity + Member Setup writes and concurrent clear/write behavior have dedicated regressions and a real PostgreSQL CI race.

Validation evidence:
- exact remediation head `d02bcde8381e0226de5f92443a6568ec33e119e5` completed all six triggered workflows successfully;
- Dank Shield CI full unit suite passed;
- the PostgreSQL atomic concurrent-writer/clear smoke test passed;
- Schema Authority SQL, Dank Design Regression CI, Ticket Owner Emergency Override, Application Command Size Diagnostics, and Profile Runtime Diagnostics passed;
- the branch was 0 behind production `main` at that checkpoint.

Production closure still requires the migration to deploy after the final remediation PR merges; pre-merge green CI proves the migration behavior, not that production Supabase has received it.

## Completed remediation slice: P0 Search-Safe actor-vs-target role hierarchy

The reviewed Search-Safe repair privilege boundary has been repaired and validated.

Implemented:
- added `stoney_verify/services/role_mutation_authority.py` as the shared live Discord role-mutation authority helper;
- canonical `/role` actor permission, owner, bot Manage Roles, and actor/bot hierarchy checks delegate to that shared owner;
- Search-Safe preview and reviewed Apply bind the initiating interaction actor;
- role authority is rechecked against the live guild member and again inside the resource lock immediately before the Discord PATCH;
- a Manage Roles actor cannot use Dank Shield to rename a role at or above that actor's highest role merely because Dank Shield itself outranks it;
- automatic event-driven policy enforcement remains actorless and retains its explicit bot/policy authority boundary.

Validation evidence:
- exact hierarchy head `361fe24e126a7126379025a3a76f988dd7d96bca` completed all six triggered workflows successfully;
- full Python unit suite and compile checks passed;
- existing Role Editor regressions remained green;
- focused Search-Safe hierarchy regressions passed;
- Dank Design Regression CI, Application Command Size Diagnostics, Schema Authority SQL, Ticket Owner Emergency Override, and Profile Runtime Diagnostics all passed.

## Completed remediation slice: P1 Dank Design ↔ Search-Safe authority consistency

The first P1 integration slice is repaired and validated.

Implemented:
- reviewed Dank Design previews normalize final channel names through the active Search-Safe policy before the admin reviews them;
- every active V2 and surviving legacy reviewed preview producer stores a naming-policy snapshot;
- Apply fails closed before any Discord rename when that policy changed after Preview;
- direct Rename computes the Search-Safe effective name before Discord mutation and persists the actual final live name as the exact-name rule;
- direct-Rename rollback also respects the current naming policy;
- Undo previews effective restore targets under the current policy and rechecks that policy before mutation;
- Search-Safe-adjusted Undo restores searchable letters instead of intentionally reintroducing styled compatibility letters while the policy is enabled.

Validation evidence:
- exact head `314f911757531e8cd4f3e5af074cd98427c937ec` completed all six triggered workflows successfully;
- full Python compile/unit suite passed;
- Dank Design Regression CI, Application Command Size Diagnostics, Profile Runtime Diagnostics, Schema Authority SQL, and the unrelated security workflows all passed;
- source sweep confirmed every active reviewed `_store_pending()` producer stores `naming_policy`;
- branch was 0 behind production `main` at that checkpoint.

## Completed implementation slice awaiting current-head validation: P1 internal mutation double-PATCH elimination

Implemented:
- canonical `/role` create, edit-appearance, and duplicate paths compute the Search-Safe-permitted role name before Discord mutation;
- Channel Builder loads one naming policy snapshot per execution and applies it to arbitrary channel/category create and rename names;
- Setup Assistant loads one policy snapshot per missing-item repair and applies it to custom role/category/text/voice names before lookup/create;
- Server Stats loads one policy snapshot per enable/refresh/disable operation and uses Search-Safe-effective Design-synced names for discovery, creation, and refresh;
- fixed canonical names that are already ordinary searchable text intentionally avoid redundant policy reads.

Validation evidence before later runtime-hardening commits:
- focused Dank Design Regression CI, Application Command Size Diagnostics, Schema Authority SQL, Ticket Owner Emergency Override, DS Backlog 027 Validation, and Profile Runtime Diagnostics were green on `d0f67fa8aaa02322b8562637cf30d8e6b30ccc4e`;
- the full Dank Shield CI run on that now-superseded head was still executing when further same-task commits were made, so it is not treated as completion evidence.

## P1 implementation complete — exact-head validation pending

The remaining Search-Safe/runtime P1 findings have now been implemented on this same remediation branch.

### Runtime reliability / semantic history
- per-resource mutation locks use weak lifecycle ownership, eliminating the queued-waiter split-lock race without permanent lock-registry growth;
- rapid semantic aliases preserve actual event order instead of set/alphabetical order;
- failed alias/delete persistence requeues claimed events ahead of newer events;
- cancellation after a flush has claimed events also requeues them rather than losing them;
- exact saved aliases outrank unrelated partial live matches while exact live-name matches keep the no-DB fast path;
- future naming schema versions fail closed and older workers refuse to overwrite/downgrade them;
- malformed policy booleans such as `"false"` are parsed explicitly instead of Python truthiness.

### Unicode / Search-Safe scope
- Dank Design remains the one Unicode font-map owner;
- its reverse font map is cached;
- Search-Safe rewrites only known styled glyphs that decode to one ASCII alphabetic character;
- unrelated compatibility symbols, ligatures, temperatures, trademark/service marks, and decorative digits remain untouched.

### Reviewed repair transaction correctness
- Preview freezes the exact bounded editable resource identities the admin reviewed;
- Apply mutates only those reviewed IDs and rechecks current name, permission/hierarchy blocker, and derived Search-Safe output before each PATCH;
- resources added after Preview cannot silently enter Apply;
- reviewed resources renamed after Preview fail closed and require a fresh preview;
- **Preview Next 25** now builds a new reviewed batch instead of immediately mutating a freshly rescanned batch.

### Shared Dank Design/Search-Safe mutation authority
- one weak per-guild naming mutation lock is owned by `services/naming_mutation_locks.py`;
- Dank Design's existing `_lock_for()` is now a compatibility facade over that shared owner;
- Search-Safe automatic enforcement and reviewed batch repair use the same guild lock plus their resource lock;
- gateway enforcement therefore waits for a Design Apply/Undo transaction rather than racing its preflight assumptions.

### Discord REST pacing / retries
- Search-Safe name edits funnel through one `_safe_name_edit()` helper;
- the helper shares the existing process-wide Discord REST budget from `discord_api_safety`;
- the same edit uses the canonical operation-queue retry helper for retryable Discord/API failures;
- no Search-Safe role/channel name PATCH bypasses that helper.

### Cross-process config correctness
- the canonical atomic guild-config RPC now supports optional compare-and-swap expectations for a shared nested feature key;
- stale CAS writers return the newest canonical row without writing;
- Naming Identity state mutations replay their pure transform against the winner's state and retry with a bounded attempt count;
- concurrent workers therefore preserve each other's aliases/policy rather than last-writer-wins replacing the whole `naming_identity_v1` blob;
- protected setup keys cannot use the CAS helper as a bypass;
- mutation/review paths force fresh naming-policy reads before live Discord changes;
- the naming cache TTL was reduced from 300s to 60s, and an expired naming cache bypasses the guild-config cache so the two TTLs no longer stack.

### Shutdown durability
- naming debounce work has an immediate `_flush_pending_once()` primitive;
- claimed events are requeued when a flush is cancelled;
- `flush_pending_naming_identity()` cancels debounce timers and performs bounded immediate persistence;
- the native Dank bot-class owner calls that flush from `Bot.close()` before Discord tears down the event loop;
- no `atexit` or instance monkey-patch shutdown owner was added.

### Internal double-PATCH elimination
- `/role` create/edit/duplicate, Channel Builder, Setup Assistant custom names, Server Stats, Dank Design previews/direct rename/Undo all consume the active Search-Safe policy before their own live mutations;
- gateway enforcement remains the safety net for external/manual Discord edits rather than the normal second PATCH for Dank Shield-owned actions.

Focused regressions now cover the lock lifecycle, alias chronology/retry/priority, future schema protection, Unicode scope, immutable reviewed batches, shared Design/Search-Safe locking, REST pacing ownership, fresh policy reads, nested CAS conflict replay, strict booleans, and shutdown flush behavior.

## P2 implementation complete — final exact-head re-audit pending

### Member Setup clarity

The Member Setup gate engine itself was left unchanged. The admin UX now explains its existing semantics explicitly:

- **Eligibility prerequisite (optional):** a role such as Verified that the member must already have; Dank Shield does not grant this role.
- **Member Access role (automatic):** the role Dank Shield grants/removes after an eligible member completes the current setup revision.
- the manager shows the complete rule chain before Strict Gate activation:
  **Eligibility → Complete Member Setup → Member Access → Protected Categories**;
- protected-category visibility, revision IDs, prerequisite/access role IDs, strict-gate reconciliation, guild isolation, and existing member completion state were not redefined;
- focused regression coverage protects the wording and the four-step relationship from collapsing back into two ambiguous role pickers.

### Global production footer cleanup

The requested global footer audit was completed as an inventory-first production pass.

Removed or rewritten from live member/admin UI:
- raw guild/channel IDs used only as debug/footer metadata;
- `config source` and internal config-key wording such as `modlog_channel_id`;
- `canonical live runtime`, runtime/schema/version labels, and internal service/monitor language;
- visible `dank_shield:...` and `stoney_verify:...` machine tokens;
- internal Community Hub session/version footer metadata;
- raw live-profile user/trigger identifiers;
- Spam Guard panel `page=...` runtime markers;
- Spam Guard quarantine case/guild/user footer identifiers;
- ticket/transcript runtime marker strings.

Preserved or standardized:
- safety warnings such as “preview only,” “nothing was changed,” or confirmation requirements;
- pagination/counts;
- action guidance and destination context;
- useful audit labels such as Webhook audit, Emoji audit, Scheduled event audit, Automod audit;
- user-facing product/navigation labels.

Persistent/runtime compatibility was preserved rather than sacrificed for cosmetics:
- old Welcome, Basic Verify, ticket/transcript, live-profile, Spam Guard panel, and Spam Guard incident marker strings remain recognized as **legacy detection inputs only**;
- new Basic Verify panels use `Dank Shield Basic Verify`;
- new ticket/transcript controls use human-readable marker labels;
- new live-profile cards use `Dank Shield live profile` with durable state as the ownership source;
- new Spam Guard panels use `Spam Guard • <Page>`;
- Spam Guard incident restore resolves the durable quarantine case from `modlog_message_id` first and falls back to parsing the old encoded footer only for already-posted legacy cards.

Dedicated footer regression coverage now:
- scans production footer calls for raw `Guild {id}` leakage;
- rejects known debug/footer phrases;
- verifies human-readable current runtime markers plus backward legacy detection for persistent surfaces.

Role autocomplete privacy is also hardened:
- `/role` autocomplete filters choices before Discord returns them;
- role managers see only roles they can actually manage under the shared hierarchy blocker;
- ordinary members see only configured self-service roles;
- normal-member filtering reuses one fresh guild config read for the whole suggestion batch rather than one read per role.

Final audit ledger: `SEARCH_SAFE_MASTER_AUDIT_018.md`.

## Final validation / re-audit gate

Implementation changes are now frozen except for concrete validation failures.

Required before this master remediation can be considered complete:
- branch ancestry remains clean and 0 behind production `main`;
- exact-head full Python compile/unit suite passes;
- Dank Design Regression CI passes;
- Application Command Size Diagnostics passes;
- Profile Runtime Diagnostics passes;
- Schema Authority SQL passes;
- real PostgreSQL sparse write/clear/CAS smoke checks pass;
- Ticket Owner Emergency Override and other triggered security regressions remain green;
- final changed-file/diff review finds no unrelated task contamination or duplicate authority owners;
- final 100-point audit ledger in `SEARCH_SAFE_MASTER_AUDIT_018.md` is reviewed against the exact head;
- Supabase migration deployment is observed after merge;
- Discloud production startup is observed after merge;
- live canary confirms Search-Safe reviewed repair, styled-role lookup, Member Setup manager wording, Design preview/apply/Undo consistency, and representative cleaned footers;
- no production-complete claim is made before deployment/canary evidence exists.

## Remaining production evidence only

All known P0/P1/P2 implementation findings in the master ledger are now resolved.

The final implementation additions also include:
- fixed-cardinality Search-Safe/Naming Identity metrics exposed in read-only `/dank diagnostics`;
- automatic enforcement blocked/failed counters;
- normalized-name collision warnings in Search-Safe scan/preview;
- explicit searchability-versus-role-mention guidance;
- removal of the unused channel autocomplete surface while preserving/regression-protecting the real `/role` resolver and all naming gateway listeners;
- opportunistic CAS cleanup of identity records for resources deleted while the bot was offline, triggered only when an authorized admin opens Search-Safe Naming and never from startup/global polling;
- footer hygiene regression scanning production `set_footer()` calls for raw IDs/internal runtime markers.

The only remaining gate is exact-head and post-merge evidence:
- final exact-head workflow suite;
- Supabase migration deployment after merge;
- Discloud startup after merge;
- live/mobile canary for `/role`, Search-Safe repair, Dank Design Preview/Apply/Undo, Member Setup manager wording, and representative cleaned persistent footers.

## Next step

Run the final exact-head validation and re-audit once. Fix only concrete regressions found by that pass. If the branch is green and the ledger has no unresolved merge blocker, prepare PR #363 for merge; then require migration deployment, Discloud startup, and live canary evidence before marking the master task production-complete.

## Prior merged task record: SEARCH-SAFE-NAMING-017

PR #362, **Add scalable search-safe naming identity**, merged into production `main` as `7bf1bf0799ffaf70cb4b207da9e42750797123cf`. Its implementation established the naming identity/Search-Safe foundation but the master audit found the P0/P1/P2 issues recorded above. Prior PR-head CI remains historical evidence and must not be used as proof for remediation changes.

---

# Prior completed task record: MEMBER-SETUP-REVISION-GATE-017

## Completed task / desired outcome

**MEMBER-SETUP-REVISION-GATE-017 — add guild-scoped versioned Member Setup reviews with optional strict Discord access gating**

Desired outcome: every Dank Shield server may publish versioned member setup changes without wiping valid member role/profile choices. Members review only changed sections. Minor updates require no action, Recommended updates surface new choices, Required updates require an explicit review, and Access-Gated updates may temporarily withhold a dedicated Member Access role until the member confirms the current setup. All setup definitions, completion state, role/channel mappings, and gate behavior must remain isolated by guild.

## Scope / single active task lock

Included:
- repurpose the existing permanent profile panel as the member-facing **Member Setup & Profile** doorway;
- `/role → Member Setup` for live member revision status and review;
- `/role → Member Setup Manager` for authorized server managers;
- durable guild setup revision state in canonical `guild_configs`;
- durable per-member/per-guild completion state in existing service-role-only `dank_profile_guild_settings.settings`;
- setup severities: Minor, Recommended, Required, Access-Gated;
- changed-section tracking for Community, Notifications, Profile & Cosmetics, and Interests;
- preserve existing valid selections instead of resetting roles;
- one explicit **Confirm Current Choices** acceptance action after review;
- configurable permanent setup channel;
- configurable dedicated/reused Member Access role;
- optional prerequisite role such as Verified;
- explicit protected-category list;
- strict gate health checks before activation;
- block activation when setup recovery channel is inaccessible, bot role/channel authority is insufficient, access role is unsafe, or protected categories contain unsynced child permissions;
- exact-server-name confirmation before activation/suspension;
- grandfather existing eligible members before changing category visibility;
- preserve and restore pre-gate category view-channel overwrite state;
- strict-gate join/member-role/guild-available reconciliation;
- paged guild member setup-state reads for scalable reconciliation;
- exact-head CI and final diff review before merge.

Excluded:
- forcing Discord Community Onboarding to be enabled;
- replacing Discord Rules Screening;
- hardcoding The 420 Lobby channel names or Stoner role names into the generic setup engine;
- unrelated moderation, tickets, AntiNuke, Invite Shield, Live Captions, or Community Hub changes.

## Product model

- One permanent member-facing channel, currently renamed by the owner to **#member-setup**, hosts the persistent Member Setup & Profile panel.
- Guild configuration is authoritative by Discord IDs, not names.
- Each guild has its own current revision, setup channel, access role, prerequisite role, protected categories, severity history, and gate-active state.
- Each member has an independent completion revision and per-section review revisions for each guild.
- New members always review the whole current setup; returning members review only sections changed since their completed revision.
- Minor revisions never force review.
- Recommended/Required revisions do not remove Discord access.
- Access-Gated revisions remove only the configured Member Access role when Strict Gate is already active.
- Completing setup restores Member Access only when any configured prerequisite role is also present.
- Server owner and Administrator members are exempt because they bypass channel visibility restrictions; bots are excluded from member gating. Manage Server/Manage Roles staff still receive Member Access because those permissions do not bypass channel visibility denies.

## Safety / failure behavior

- Configuring Strict Gate and activating it are separate operations.
- Strict Gate does not activate merely because a role/channel was selected.
- Member Setup channel must remain visible without Member Access.
- Protected categories must have synced child permissions before activation.
- Existing category overwrite values are snapshotted before gating and restored on confirmed suspension.
- Existing eligible members are marked current and given Member Access before category visibility is changed.
- Activation rolls back category changes when a category mutation fails.
- Access role must be non-managed, below Dank Shield, and free of privileged administration/moderation permissions.
- Gate reconciliation is derived from durable revision state after restarts instead of relying on process memory.
- Guild-wide reconciliation reads stored member state in PostgREST pages instead of issuing one database query per member.

## Changes

- Branch: `feat/versioned-member-setup-review-20260929`, based exactly on merged PR #360 production main `afa5930d8f0fd55762d19c04471605906bbaf5c8`.
- Added `member_setup_service.py` as the canonical revision/completion state owner.
- Added safe generic per-guild member namespace persistence and paged guild-settings reads to `profile_card_service.py`; no new database table or migration is required for v1.
- Added `commands_ext/public_member_setup.py` owning member review UI, manager UI, gate health, confirmed activation/suspension, and access reconciliation.
- Member Setup runtime installs strictly during command bootstrap with join, member-update, and guild-available listeners.
- Roles & Profiles now exposes **Member Setup** to members and **Member Setup Manager** to authorized setup managers.
- `/role` dynamically surfaces whether the caller is current or has setup changes to review.
- The persistent Profile panel now exposes **Member Setup / Review** and is presented as **Member Setup & Profile**.
- Profile Builder exposes **Member Setup Manager** without replacing existing profile/cosmetic role tooling.
- Added focused regression coverage in `tests/test_member_setup_revision_runtime.py`.
- New Member Setup/Profile panel posts persist the canonical channel/message ID. **Refresh Public Panel** upgrades one uniquely identifiable legacy bot-authored profile panel in place; zero/multiple candidates fail closed rather than editing an arbitrary message.

## Validation / completion

PR #361 exact head `0e1705e44e8de7f43e5756979241dce95e73335a` completed all six triggered repository workflows successfully: Dank Shield CI, Profile Runtime Diagnostics, Dank Design Regression CI, Application Command Size Diagnostics, Ticket Owner Emergency Override, and Ticket Panel Single Owner.

PR #361 then merged into production `main` as `3427a1700fe522ce95239bbf96690aab3aaeb3b8`. The Search-Safe Naming branch must preserve this Member Setup runtime and its access-gate invariants while adding naming behavior.

## Completed state

Merged and retained here as a completed task record. Any later changes to Member Setup require an explicit task switch rather than being folded into Search-Safe Naming.

## Prior task closure

PR #360, **Add self-service community roles, /toke, and smart /role**, merged to production `main` as `afa5930d8f0fd55762d19c04471605906bbaf5c8` after all six exact-head workflows passed.

## Prior completed task closure

PR #359, **Add staff-only Roles & Profiles role editor**, merged to production `main` as `3e4252e8ea0d381d0fbb5c633df489b2b52c9048` after all six exact-head workflows passed.

---

# Prior completed task record: PROFILE-COMMUNITY-TOKE-016

## Completed task / desired outcome

**PROFILE-COMMUNITY-TOKE-016 — unify self-service/community roles with safe `/toke` and a smart `/role` doorway**

Desired outcome: Dank Shield Profile Builder must let staff map existing safe community roles without recreating them. Members can self-select the configured **Stoner** identity/community role and optional **Sesh Pings** notification role, use one smart `/role` doorway for approved self-service/profile roles, and use `/toke` from the Stoner role to ping the opt-in sesh audience. Staff/member-role and full server-role shortcuts must reuse the existing guarded Roles & Profiles owners rather than creating parallel role engines.

## Scope / single active task lock

Included:
- staff **Community & Pings** setup inside Profile Builder;
- map an existing Stoner role;
- map an existing Sesh Pings role (may intentionally be the same role for simple mode);
- optional preferred smoke-session text channel;
- member self-selection UI for Stoner / Sesh Pings alongside the existing cosmetic/profile tools;
- `/toke` as one intentional member-facing global slash command;
- `/role` as one intentional smart global slash command with optional `member` and `role` targets;
- `/role` with no options opens the canonical Roles & Profiles center;
- `/role member:@User` opens a role-focused Member Role Manager card; Add/Remove reuse the existing guarded MemberRoleActionView, while View Profile and an explicit Full Member Panel remain available;
- `/role role:@Role` opens the existing Server Role Editor for live role managers, otherwise exposes Add/Remove only for roles already recognized by Profile Builder / Community & Pings;
- only current Stoner-role members may invoke `/toke`;
- only the configured sesh audience role may be mentioned;
- no arbitrary user supplied role mentions;
- per-member and per-guild cooldowns;
- clear setup/permission errors instead of silent dead pings;
- small Cheers interaction that does not send another role ping;
- command-surface contract/docs/tests updated intentionally;
- exact-head CI and final diff review before merge.

Excluded:
- cannabis procurement, dosage, consumption instructions, or marketplace behavior;
- Community Hub matchmaking/session architecture;
- unrelated moderation/protection/ticket/design work;
- replacing Discord Onboarding or requiring it for profile roles.

## Product model

- **Stoner** = self-selected community/profile identity and authority to start `/toke`.
- **Sesh Pings** = self-selected notification subscription that receives `/toke` role pings.
- Servers that want one-role simplicity may map both settings to the same Stoner role.
- Dank Shield Profile Builder remains canonical for these richer member role choices; Discord Onboarding may still be used independently for first-join essentials.

## Safety / anti-abuse

- role mappings are Discord IDs from staff-selected roles, never role-name guesses;
- selected roles must pass existing profile-safe/manageability checks;
- invocation re-resolves current config, member roles, target role, target channel, and bot permissions;
- outbound AllowedMentions permits only the mapped Sesh Pings role and forbids users/everyone;
- a non-mentionable target role requires Dank Shield's channel-level Mention @everyone/@here/all roles permission or Discord will not be treated as ping-ready;
- default cooldown target: 15 minutes per invoking member and 5 minutes per guild, process-local and bounded;
- Cheers is response-only and never emits the sesh role mention;
- Cheers is also Stoner-only and tracks one response per member for the card lifetime;
- community self-role mutations are serialized per guild/member and re-read current mappings before changing roles.

## Changes

- Branch: `feat/profile-community-toke-20260928`, based exactly on merged role-editor production main `3e4252e8ea0d381d0fbb5c633df489b2b52c9048`.
- Added canonical `commands_ext/public_toke.py` owning Community & Pings setup, member self-selection, scoped `/toke`, cooldowns, and Cheers.
- Profile Builder now exposes staff **Community & Pings** setup for mapping existing safe Stoner/Sesh Pings roles and an optional preferred text channel.
- Member Profile Panel, Edit Profile, and Roles & Profiles center expose **Community & Pings** self-selection.
- Separate-mode Sesh Pings requires Stoner; removing Stoner also removes the separate Sesh Pings subscription. Same-role mode remains supported.
- Existing profile-safe role checks remain authoritative for both mapped roles; no second role-safety policy was created.
- The configured Stoner role appears on Dank Profile cards as a **Community** identity label; Sesh Pings is intentionally hidden from profile identity display.
- Added intentional top-level `/toke [message]` and smart top-level `/role [member] [role]`; the public application-command contract is now nine items total: eight slash roots plus **View Dank Profile**.
- `/toke` acknowledges before config I/O, re-resolves live mappings and Discord permissions, requires the invoker's current Stoner role, serializes sends per guild, and records cooldowns only after a successful send.
- Outbound role mention scope is constructed by one testable helper permitting only the configured Sesh Pings role, with users/everyone disabled.
- Added focused behavioral coverage in `tests/test_profile_community_toke.py`; updated every known public-command guard, payload test, command-tree test, ownership doc, and production command-count contract for `/toke`.
- Existing profile suggestion compatibility subclasses preserve the new controls; Profile Panel/Edit Profile component rows remain within Discord's five-component row limit.
- `/role` is only a doorway: it routes normal members to existing self-service role ownership, staff member targets to a role-focused facade over the existing guarded member-role engine, and Manage Roles/Admin/owner role targets to the existing Server Role Editor.
- Direct self-role toggles are serialized per guild/member, re-read current durable mappings before mutation, preserve the Stoner → Sesh Pings dependency, and never make arbitrary server roles self-assignable.
- Added focused regression coverage in `tests/test_role_command_doorway.py` and updated all public command-surface contracts from eight to nine total application-command items.

## Validation / results

Implementation is frozen again after the `/role` expansion pending exact-head PR CI. Pre-PR compare against `main` shows the branch ahead with **0 behind**, and the diff is limited to Profile/Community roles, the canonical `/toke` owner, command-surface contracts/docs, and focused tests.

Required before merge:
- all six repository workflows green on the exact PR head;
- full Dank Shield CI including `pytest tests/`, compile, standalone tools, and public-surface audits;
- final compare against current `main` still 0 behind;
- PR diff remains task-scoped with no unrelated runtime/migration/dependency changes.

## Next step

Open a draft PR from the frozen branch, inspect every exact-head workflow failure instead of blindly retrying, then merge only after all gates are green and the final main comparison is clean.

## Prior task closure

PR #359, **Add staff-only Roles & Profiles role editor**, merged to production `main` as `3e4252e8ea0d381d0fbb5c633df489b2b52c9048` after all six exact-head workflows passed.

---

# Prior completed task record: ROLES-PROFILES-ROLE-EDITOR-015

## Completed task / desired outcome

**ROLES-PROFILES-ROLE-EDITOR-015 — add one safe Roles & Profiles center with staff-only Discord role administration**

Desired outcome: `/dank home → Roles & Profiles` must become one capability-aware center. Normal members see only their own profile/cosmetic surfaces. Recognized staff may reach existing member-role/profile management. Only the server owner, Administrator, or a member with live Discord **Manage Roles** permission may see or execute server-role creation, rename/edit, permissions, hierarchy movement, duplication, or deletion. Every mutation must re-check actor authority, bot authority, role hierarchy, managed/default-role restrictions, and relevant Dank Shield dependencies at execution time.

## Scope / single active task lock

Only the Roles & Profiles center and server-role editor behavior required for this feature are active.

Included:
- replace the current staff `Roles & Profiles` shortcut to Profile Builder with a real role/profile center;
- capability-aware member vs staff vs role-manager surfaces;
- create role;
- select and inspect a role;
- rename / colour / hoist / mentionable / unicode role icon editing where supported;
- grouped server-permission editing with grant-safety checks;
- safe hierarchy movement;
- duplicate role;
- dependency-aware, confirmed role deletion;
- reuse existing Profile Builder, profile/cosmetic role manager, Member Role Manager, permission/hierarchy safety helpers where structurally correct;
- targeted regression tests, full repository validation, cleanup, final diff review, PR/CI/deployment evidence before any completion claim.

Excluded:
- Server Design naming/category/channel behavior;
- Invite Shield, AntiNuke policy redesign, tickets, verification redesign, Live Captions, Community Hub feature work;
- unrelated role-system cleanup.

## Switch record

User explicitly issued:

`FORCE SWITCH: Dank Shield Roles & Profiles Role Editor`

Reason: build the staff-only server role creation, editing, rename, hierarchy, permissions, duplicate, and deletion system.

### Suspended task state: Community Hub / Live Captions

The previously active **COMMUNITY-HUB-COMPLETION-RELIABILITY-014** task is suspended, not abandoned. Its full prior task record is preserved below under **Suspended prior task record**. At suspension:
- production `main` advanced through Live Captions work to merge `b774ab13f8fba18819f453f165108e6aa1d29e97` (PR #358);
- the Community Hub / Live Captions scope, findings, implementation history, validation evidence, cleanup constraints, blockers/risks, backlog, and next soak/validation steps remain exactly documented in the archived record;
- no Community Hub code is part of this Role Editor branch unless directly required by shared command-surface correctness.

## Findings / root cause

1. Dank Shield already has role-adjacent features, but no authoritative general Discord role editor.
2. `/dank home → Roles & Profiles` currently sends server managers directly to the Profile Builder, while normal members go to their profile entry.
3. Existing role systems are fragmented by purpose: profile/cosmetic roles, member-role assignment/browser, verification/setup role mapping, and AntiNuke rollback. None owns general role mutation.
4. Existing member-role authorization intentionally treats configured staff / moderation permissions more broadly than Discord `Manage Roles`. That broader staff gate is appropriate for browsing/review but is too permissive for server-role mutation.
5. Discord's own contract requires Manage Roles for create/edit/delete/reorder and hierarchy limits role mutation to roles below the actor; Dank Shield must independently enforce the same boundary for both the actor and bot before API calls.
6. Existing `member_role_browser_common.py` already contains reusable protected-role/config and actor/bot hierarchy concepts, but server-role editing needs a dedicated authority check because member moderation actions and role-definition mutations are different capabilities.
7. Server Design explicitly excludes roles, so the canonical home for the full editor is **Roles & Profiles**, not Server Design. A future visual-only shortcut can route into the same editor without creating duplicate ownership.

## Execution path

`/dank home`
→ `CompactDankHomeView.roles`
→ new canonical Roles & Profiles center
→ member-safe profile route OR recognized-staff tools
→ role-manager-only server role administration
→ fresh role resolution + fresh authority/hierarchy/dependency checks
→ Discord role mutation
→ refreshed editor/center.

## Changes

- Branch created from production main `b774ab13f8fba18819f453f165108e6aa1d29e97`: `feat/roles-profiles-role-editor-20260928`; draft PR #359 opened.
- Added canonical `commands_ext/public_role_center.py` and routed both current **Roles & Profiles** home implementations through it.
- Normal members receive only profile/cosmetic controls. Recognized staff may reach the existing Member Role Manager. Profile Builder is only rendered when the opener also has its established setup-management authority.
- Server role administration is only rendered for the server owner, Administrator, or a live Discord member with **Manage Roles**.
- Added role creation, inspect/select, rename/appearance, colour, hoist, mentionable, supported unicode role icon editing, grouped permission editing, one-step hierarchy movement, duplication, Role Health, and exact-name-confirmed deletion.
- Every mutation re-resolves current state and re-checks actor authority, bot Manage Roles, managed/default-role restrictions, and actor/bot hierarchy. Mutations for the same role are serialized through one per-role lock.
- Permission editing preserves permissions outside the selected group, rejects grants the actor/bot cannot grant, keeps each Discord select within 25 options, and requires exact current-role-name confirmation before enabling Administrator.
- Deletion discovers current Dank Shield role dependencies recursively from guild config **and** the separate authoritative Spam Guard security settings, then checks again under the mutation lock immediately before deletion; unreadable durable role settings fail closed.
- Existing profile/cosmetic management, Member Role Manager, shared role picker, and AntiNuke HTTP self-action provenance remain authoritative; no competing assignment, profile, or AntiNuke owner was added.
- Duplicate creation re-checks fresh permissions under lock; optional placement failure leaves the created role intact and surfaces a warning rather than pretending placement succeeded.
- Updated empty cosmetic-role guidance to the public `/dank home → Roles & Profiles → Profile Builder` route.
- Added focused regression coverage in `tests/test_roles_profiles_role_editor.py` and updated the existing owner-authority command-surface contract.

## Validation / results

Exact-head validation completed successfully on `b4e251d0cd3ae0789911352b592e8d95b995fce9` before this documentation-only update:
- Dank Shield CI — success;
- Profile Runtime Diagnostics — success;
- Community Hub CI — success;
- Dank Design Regression CI — success;
- Ticket Owner Emergency Override — success;
- Application Command Size Diagnostics — success.

Final pre-documentation compare showed:
- PR #359 mergeable;
- branch 26 commits ahead and 0 behind production main `b774ab13f8fba18819f453f165108e6aa1d29e97`;
- exactly seven task-scoped files changed: ACTIVE_TASK.md, the two Roles & Profiles home routes, the canonical role center, one profile guidance line, the owner-authority contract test, and the dedicated role-editor regression test;
- no Server Design, AntiNuke implementation, Community Hub, Live Captions, ticket, verification, migration, dependency, or deployment file changes.

Because this task-record update changes the branch SHA, all triggered workflows must be green again on the new exact head before merge.

## Cleanup / conflicts

- No Server Design role mutation path was added; Server Design remains channel/category naming-only.
- No second member-role assignment engine or profile/cosmetic manager was created.
- No AntiNuke bypass or new provenance system was created. Normal Discord role API calls remain covered by `anti_nuke_self_action_runtime` role create/update/delete authorization. AntiNuke trusted-role settings already live in guild config; Spam Guard's separate `guild_security_settings` role references are read through its existing owner.
- The old direct **Roles & Profiles → Profile Builder** routing is removed from both home implementations.
- Stale public guidance to the compacted `/dank profile builder` doorway was corrected in the canonical profile-role UI.
- No unrelated Community Hub, Live Captions, Invite Shield, ticket, verification, or Server Design behavior is intentionally changed.

## Blockers / risks

- Exact-head CI is still required before merge/completion claims.
- Discord itself remains the final authority on role hierarchy and server feature availability; the editor pre-checks these boundaries and reports Discord API refusals without weakening them.
- Custom image role icons are preserved/displayed but this UI only edits supported unicode role icons; adding binary role-icon uploads would require an attachment-capable upload surface and is not silently emulated.

## Backlog

- Suspended Community Hub / Live Captions completion task, with full state preserved below.
- Optional future Server Design → Role Appearance shortcut into this same authoritative editor after this task is complete.

## Next step

Confirm every workflow triggered by this documentation-only final head is green, verify `main` has not advanced, then mark PR #359 ready for merge. Do not merge on stale-head evidence.

---

# Suspended prior task record: COMMUNITY-HUB-COMPLETION-RELIABILITY-014

## Suspended task / desired outcome

**COMMUNITY-HUB-COMPLETION-RELIABILITY-014 — finish the Dank Shield Community Hub as a reliable, general-use product instead of a one-pass scaffold**

Desired outcome: Community Hub interactions must acknowledge reliably, stale private Hub panels must recover without red Discord failures, the user-facing flow must match the intended plain-English experience, and missing promised Hub capabilities must be completed without weakening public-session persistence, permissions, cleanup, rate-limit safety, or privacy boundaries.

## Scope / single active task lock

Only Community Hub completion and the interaction/runtime behavior required for Community Hub correctness are active.

Included:
- Community Hub private-menu and public-session interaction lifecycle;
- stale/restarted Hub recovery;
- Hub interaction acknowledgement diagnostics and regression coverage;
- member UX: Find Players, Open to Play, Quick Match, groups/sessions, events, notifications, safety;
- staff Hub settings/health/session/event/partner management;
- approved partner activity/discovery behavior;
- Hub runtime, service, SQL, and cleanup/reconciliation only where required by the Hub.

Do not broaden into Invite Shield, AntiNuke policy redesign, verification, tickets, Dank Design, or unrelated cleanup.

## Prior task closure

PR #330, **Prevent AntiNuke self-ejection on local bulk message cleanup**, merged into `main` as `d55ace775f1ec1f2f401dbeca9c5e72875b9537d`.

Its exact head `6ee520397771e4c71d0d7c84f8df6f06d5896fe4` completed every observed workflow successfully:
- Profile Runtime Diagnostics
- Dank Shield CI
- Dank Design Regression CI
- Ticket Owner Emergency Override
- Application Command Size Diagnostics

The AntiNuke task therefore satisfies its repository completion gates and the active-task lock can move to Community Hub.

## Findings / root cause

PR #326 merged the first broad Community Hub implementation, but the feature was treated as production-ready before live UX/reliability follow-through was complete.

Confirmed current-state gaps:

1. Private Community Hub views use the shared 15-minute in-memory private-menu lifetime. After timeout or a bot restart/redeploy, the old ephemeral message can remain visible while its ViewStore owner is gone.
2. The shared component runtime can recover definitely unowned private panels, but it applies a generic recovery grace delay before claiming the interaction. For a known Community Hub custom-id namespace that is already proven unowned, that delay only consumes Discord's initial-response window.
3. Community Hub's durable public session card is correctly persistent (`timeout=None`) and must remain separate from private-menu recovery.
4. The existing Community Hub contract test proves helper presence and static structure but does not exercise the stale Hub recovery path end-to-end.
5. Current partner activity is aggregate-only (groups, voice count, Open to Play count, optional online/gaming counts). It does not yet provide the chosen-partner live member experience originally requested.
6. The merged Hub contains substantial backend/state-machine work, but the user-facing product still needs a completion pass rather than another disconnected feature layer.
7. PR #335 merged the Live Captions backend, but the Community Hub home exposed no discoverability path; host controls were only reachable through a session's Manage flow and participant consent only from the public session card.
8. The merged caption callbacks referenced the caption manager / voice receive capability / exception names without importing them into `public_community_hub.py`, so pressing the controls could raise `NameError` even though Python compilation passed.
9. PR #336's first discovery implementation read `membership["user_id"]`, but `list_user_sessions()` intentionally does not select that column. Consent status therefore needed to use the actual interaction user ID instead of widening persistence reads.
10. PR #336 made Community Hub captions discoverable, but general server Live Captions still did not exist: the only start path required a Community Hub session with a stored voice channel.
11. Caption opt-out stopped new receive frames but did not explicitly purge that speaker's already-buffered, queued, or in-flight caption work. General server exposure makes that privacy edge unacceptable, so revocation must clear the speaker pipeline before returning.
12. The packet-router thread could pass the consent check immediately before opt-out and schedule its event-loop callback afterward. Without a consent generation token, a rapid opt-out/re-opt-in could make that stale pre-revocation frame look valid again.
13. The first general-server caption UI used the text channel where staff opened the panel as its destination and had no durable owner setup for existing gaming VCs/categories. That made routing accidental instead of server-configured and gave owners no safe create/select flow for a dedicated caption output.
14. Live production exposed a discord.py 2.7.x channel-select contract bug in the shared `DankChannelSelect`: Discord returned an `AppCommandChannel` partial for the selected `vc-chat` text channel, while the wrapper accepted only concrete `discord.abc.GuildChannel` instances. The valid selection was therefore rejected with “Pick a server channel first.”
15. After that picker fix deployed, the configured output saved correctly but the global DAVE validation gate made the required real Discord soak test impossible to start. A safe validation path must not require globally enabling an unproven receive stack.
16. The first live soak could join voice and accept self-consent but produce no visible caption. The caption engine collapsed provider/publish exceptions into an integer failure counter and exposed no safe last-error or stage diagnosis, so DAVE receive failure, consent/identity rejection, OpenAI billing/key errors, empty transcripts, and Discord publish failures were indistinguishable from the user side.
17. The first instrumented live soak then proved the active session/consent path while reporting **0 sink frames / 0 routed frames**. The pinned PR #62 receive patch decrypts DAVE early in `reader.py` and drops packets before the sink whenever sender mapping/session decryption is unavailable; it has no passthrough, packet-sync recovery, or hardened decoder-stage behavior. The better-tested PR #54 line decrypts after per-SSRC member resolution, checks DAVE readiness, preserves packet sequence/timestamp on decrypt failure, supports transition passthrough, and prevents unresolved speakers from reaching the sink.
18. The next live soak on the PR #54-derived fork reported **Raw UDP 141**, **DAVE ready yes**, **SessionStatus.active**, protocol **1**, epoch **44**, **6 mapped SSRCs**, but **Reader: stopped**, **sink PCM 0**, and **routed 0**. This exactly matches upstream issue #43 / PR #54 field reports where speaking triggers `OpusError: corrupted stream` and `PacketRouter.run()` tears down listening. Upstream PR #58 fixes the inbound DAVE decrypt path with guarded session/SSRC/passthrough handling, while PR #57 separately prevents one corrupt Opus frame from killing the router thread.
19. The first soak after PR #342 then failed earlier and more deterministically: raw UDP reached **1593**, DAVE stayed active at protocol **1 / epoch 46**, **6 SSRCs** were mapped, but the receive worker stopped with **`discord.opus.OpusNotLoaded`** before the first `PacketDecoder` could create a PCM decoder. Dank Shield had no deterministic native Opus source and no explicit preload/capability gate.
20. PR #343 correctly added a fail-closed Opus capability check but made the wrong deployment assumption: Discloud's documented `ffmpeg` APT option installs the `ffmpeg` package; it does **not** promise a standalone `libopus.so` discoverable by Python `ctypes`. The post-deploy soak failed before joining with the new explicit **native Opus runtime unavailable** error, proving that assumption false. The corrected design must supply its own known libopus binary instead of inheriting one accidentally from the host image.
21. The first soak after PR #344 proved the entire Discord receive path: bundled Opus loaded, DAVE active, reader listening, PCM reaching Dank Shield, and a caption segment was created for the opted-in user's exact Discord ID. The next failure is now external to Discord audio: OpenAI returned **HTTP 429** from `/v1/audio/transcriptions`. The prior provider wrapper discarded OpenAI's structured `error.code` / `error.type`, so temporary rate limiting, exhausted prepaid credits, and organization/project usage or spend limits were all collapsed into one vague message. The panel's earlier “consent is blocking them” diagnosis was also misleading because `frames_not_consented` is cumulative and included frames received before the user opted in.
22. Provider direction changed by user: remove OpenAI completely and use the Google Gemini API key already configured in Discloud as `GEMINI_API_KEY`. Current Google documentation lists `gemini-3.5-transcribe` as the dedicated GA speech-to-text model with Free Tier input/output pricing, and `gemini-3.5-flash-lite` as a Free Tier audio-capable model. Recent Google developer reports show successful-but-empty `gemini-3.5-transcribe` responses on some Free Tier projects, so Dank Shield uses the dedicated model first and retries the same in-memory WAV once with Flash-Lite only when the primary response is empty. No OpenAI fallback remains. Gemini does not provide the old OpenAI token-logprob confidence field, so the engine no longer invents confidence for Gemini output; confidence-only retry/unclear logic runs only when a provider supplies a real numeric confidence.
23. The first Gemini-only live soak proved transcription and Discord publish, but all **4/4** successful captions required the unary Flash-Lite fallback and the AI Studio Free Tier then returned **HTTP 429 RESOURCE_EXHAUSTED** with **7** later segments skipped. The user requires all supported languages plus optional English conversion without resending audio or doubling speech requests. Official Gemini documentation supports `gemini-3.5-transcribe-live` over the Live WebSocket API with automatic detection across 85+ supported languages, code-switching when language hints are empty, final BCP-47 `languageCode`, and a 10-minute session maximum. This makes persistent per-speaker Live sessions the correct quota/latency direction instead of unary audio request + unary audio fallback per utterance.
24. The first deployed Gemini Live soak reached the new multilingual UI/runtime but failed before transcription with **"Gemini Live transcription did not complete its session setup."** The setup payload matches Google's current raw WebSocket examples (v1beta BidiGenerateContent, `models/gemini-3.5-transcribe-live`, TEXT response modality, empty languageCodes for auto detection, manual VAD). Root cause is the client frame parser: it decoded only aiohttp TEXT frames and silently ignored BINARY frames. Google's own Python GenAI SDK reads the first Live WebSocket response as raw bytes and JSON-decodes it, so a binary `setupComplete` could be discarded until timeout. The fix parses UTF-8 JSON from both TEXT and BINARY frames; this applies to setup, errors, goAway, and final/interim transcript events.
25. The first successful post-#348 Gemini Live publish proved the WebSocket path but exposed unacceptable recognition quality: the user spoke only English while finalized captions contained unrelated English phrases plus Spanish/Hindi text. Google documents `inputTranscription` as the finalized transcript, so this was not an interim-transcript publishing bug. The active path had clear accuracy risks in its crude three-frame box-average 48 kHz→16 kHz decimator and unconstrained automatic language detection when a speaker already knew their language. Manual VAD was also suspected at that point, so a hybrid-VAD experiment was tried. Later production evidence disproved that part of the diagnosis: hybrid VAD timed out with zero finalized transcripts, while the restored documented manual `activityStart/activityEnd` contract finalized deterministically. The retained accuracy repair is therefore the FIR resampler plus per-speaker **My Language** hints/fail-closed mismatch handling, with manual VAD preserved.
26. The deployed accuracy build exposed **My Language** through a free-form Discord modal. The user explicitly requires dropdown selection instead. Discord's current component reference caps one String Select at **25 options**, so the language UX is now two-stage: first **Auto · All Supported Languages** or a language group, then a group-specific language dropdown. The union of those second-stage dropdowns exactly matches every unique BCP-47 code in Google's current Gemini 3.5 Transcribe supported-language table, and regression coverage enforces both exact coverage and Discord's 25-option limit.
27. The first soak after the accuracy/dropdown deployment showed a new failure before Gemini: **Raw UDP 3**, **sink PCM 0**, **Reader listening**, **DAVE ready**, and **2 mapped SSRCs** while the opted-in user was speaking. The same PR #58 receive path had previously delivered large sustained RTP/PCM volumes, so the resampler/language changes cannot explain zero incoming media. This is a split voice state: Discord's control plane remains alive while the UDP media plane is stalled. Dank Shield now uses the voice gateway's non-zero SPEAKING signal from an opted-in user as the safe expectation trigger. If no PCM follows within 2.5 seconds while DAVE and SSRC mapping remain ready, only the Discord voice receive transport is rebuilt. The existing caption engine/Gemini speaker state remains alive, the exact opted-in user IDs are restored to a fresh bridge, and automatic recovery is bounded by a 20-second cooldown plus two attempts per rolling minute.
28. The first soak after the receive-recovery deployment proved the Discord path was healthy again (**Raw UDP 829**, **sink PCM 676**, **routed 648**, reader listening, DAVE active, healthy **-16.9 dBFS** speaker level) and the new English hint was active, but Gemini Live produced **0 finalized transcripts**, **0 publishes**, and two timeouts despite **3 successful Live connections**. Root cause is now at the provider boundary: production still accumulated each multi-second utterance in the local segmenter and only then burst-sent its 40 ms chunks to the Live WebSocket. Google's Live API best-practice contract is continuous small-chunk streaming and explicitly warns against significant client buffering. The repair moves Gemini submission into the per-frame engine path: each isolated Discord PCM frame is statefully resampled and sent as it arrives; the local segmenter remains only to decide when to send manual-VAD `activityEnd`. Each opted-in speaker's Gemini socket is pre-opened before PCM admission, `activityStart` precedes the first streamed chunk, and a dedicated receive task consumes interim/final events concurrently.
29. The same soak confirmed a UX regression: the user had to repeatedly choose **My Language** and press **Caption My Voice** after caption/session restarts. The intended product is now explicit **remembered per-server consent**, not silent universal recording: a member enables **Auto-Caption My Voice** once, and Dank Shield stores that boolean plus the optional language hint in the existing per-guild member settings JSON. Future caption starts and target-VC joins restore only members who explicitly enabled it. Leaving/stopping clears active audio immediately; turning the preference off persists revocation and purges buffered/queued/in-flight audio. Raw audio/PCM is never persisted.
30. Current `main` at `c0550646254d1f39b90b13921b25bfa5e25111dd` restored deterministic Gemini finalization with documented manual VAD. PR #352 was still based on `e6cd12b6bfde00e1bd57ca3133c4b220094f43c3`, so it was one main commit behind and non-mergeable while also reintroducing hybrid VAD/`audioStreamEnd`. The reconciliation keeps #352's frame-by-frame streaming, persistent per-speaker receiver, FIR resampler, remembered consent/language preference, and telemetry while restoring manual VAD: automatic activity detection disabled, `activityStart` before the first live audio chunk, and `activityEnd` at the existing 0.75-second local boundary.
31. Reconciliation exposed a second provider-boundary ordering bug in the realtime engine: it streamed the current Discord frame before calling `segmenter.feed(frame)`. When that frame arrived after a >=0.75-second packet gap, the segmenter only then discovered the previous utterance boundary, so the first frame of the new utterance had already been attached to the old Gemini activity. The repair now classifies the boundary before provider submission. A previous-gap segment is sealed with `activityEnd` before the post-gap frame can start the next activity; a max-duration segment that includes the current frame is sealed after that frame is streamed. Sealing returns a dedicated per-turn future, so final `inputTranscription` is awaited asynchronously while later audio keeps streaming. Final results are resolved FIFO to their exact turn waiter, preventing concurrent segment tasks from consuming each other's transcripts.\n32. Session-liveness inspection found another reliability gap: a Gemini WebSocket could remain technically open after its dedicated receive task had exited. The old `_ensure_connected()` checked only socket/age/rotation state, so a later utterance could be sent into a connection with no active response consumer and then wait until finalization timeout. Receiver-task health is now part of connection health; a missing/dead receiver forces a per-speaker reconnect before additional PCM is admitted, with regression coverage for the open-socket/dead-reader case.
33. Post-#352 accuracy inspection found two remaining turn-boundary hazards inherited from the old buffered design. First, the local 8-second PCM cap still forced `activityEnd` during uninterrupted realtime speech, which could split a word/phrase even though Gemini Live supports much longer continuous sessions. Realtime sessions now compact already-streamed local PCM at the memory cap instead of ending the provider turn, keeping RAM bounded without sacrificing linguistic context. Second, packet-gap detection used host arrival time alone; a delayed but RTP-contiguous Discord packet could therefore look like speaker silence. Gap classification now cross-checks the stable-SSRC 48 kHz RTP media clock when a new frame arrives, and realtime idle-only finalization adds a 250 ms grace beyond the 0.75-second base threshold so ordinary transport jitter has time to deliver that evidence before `activityEnd` is committed.
34. Live production feedback after PR #354 deployment reported recognition/reliability becoming worse again. Because #354 changed turn-boundary semantics without positive live-soak evidence, it is treated as a regression rather than a new baseline. The repair branch restores the exact #352 caption engine/test/docs behavior for the risky #354 changes: arrival-time 0.75-second gap classification, the original bounded max-duration segment flush, and zero extra idle-grace. All unrelated #352 streaming, manual VAD, receiver-liveness, per-speaker language, and remembered preference fixes remain intact. PR #355 is not deployed and must not be stacked on top of the regressed #354 runtime until the known-better #352 boundary behavior is restored and revalidated.
35. The first live soak after rollback #356 shows the restored baseline is substantially functional: **Raw UDP 6582**, **sink PCM 6318**, **routed 605**, **queue 0**, DAVE ready, reader listening, **605 Gemini audio chunks**, **7 activity starts / 6 ends**, **14 interim events**, **4 final events**, **4 transcribed / 4 published / 0 empty / 0 unclear**, followed by one finalized-transcript timeout. This narrows the remaining reliability problem to an occasional missing authoritative final rather than a broken audio path. The provider already emitted multiple speculative interims for successful speech but the runtime discarded their text. The new recovery keeps a bounded per-turn interim history and, only when the exact sealed turn times out, may publish a stable interim if at least two recent hypotheses strongly agree and their language families do not conflict. Any authoritative final still wins; unstable/single/cross-language interims fail closed. The same soak also shows the tester's personal language remained **Auto**; Google's current Transcribe guidance states a known language hint maximizes accuracy, so the panel now explicitly recommends **My Language** for mostly single-language speakers while preserving Auto for multilingual/code-switching users.
36. The same soak exposed a second concrete accuracy loss before Gemini: **105 corrupt Opus packets were dropped**. The existing router survival patch correctly prevented one bad frame from killing the reader, but a drop still creates a hole in the speech stream. The pinned upstream decoder already uses native libopus packet-loss concealment (`Decoder.decode(None)`) for known-missing frames. Dank Shield now applies that same codec-native PLC path to one isolated corrupt **real** Opus frame before the router-drop fallback. Recovery is deliberately bounded: only one consecutive corrupt real frame is concealed; another corrupt frame is dropped until a real frame decodes successfully. Telemetry now separates **PLC recovered** from **dropped** corrupt Opus frames so the next soak can prove whether speech continuity improves.
37. After PR #357 deployed successfully, live use reached roughly **70% recognition accuracy**: materially usable but still below the standard needed for dependable moderation. Current Google Live Transcribe guidance specifies raw 16-bit 16 kHz PCM and recommends roughly **100 ms audio chunks**. Dank Shield already sends the correct 16 kHz mono format, but still emitted one provider WebSocket audio message per ~20 ms Discord frame. The new accuracy pass keeps per-frame receive, boundary detection, FIR state, consent, and manual VAD unchanged while adding only a tiny per-speaker provider transport buffer: five ordinary Discord frames become one ~100 ms Gemini chunk, and any shorter remainder is flushed before `activityEnd`. This preserves every sample and realtime behavior while aligning provider message granularity with the documented Live Transcribe path.
38. Extended pre-#358 production logs show the remaining failure is not just recognition quality: multiple speakers repeatedly hit `Gemini Live transcription timed out waiting for a finalized transcript` while Discord receive stayed alive. Google's current Live API reference states `RealtimeInputConfig.activityHandling` defaults to `START_OF_ACTIVITY_INTERRUPTS`; Live server content is interruptible when a later activity starts. Dank Shield intentionally allows a new Discord turn to begin while the previous turn's authoritative `inputTranscription` future is still pending, so the default can cut off the prior server response and strand that waiter. The current accuracy branch therefore also pins `activityHandling: NO_INTERRUPTION` and counts any server `interrupted` events in soak telemetry. This does **not** restore PR #354's reverted gap/idle-boundary experiment and does not change manual-VAD ownership.


## Execution path

Fresh private Hub click:

`/dank home -> Community Hub -> discord.py ViewStore-owned private view -> callback acknowledges -> Hub action`

Stale private Hub click after timeout/restart:

`ephemeral Hub message remains visible -> ViewStore has no owner -> shared component runtime proves owner_state=False -> Community Hub-specific safe refresh claims interaction immediately -> stale action is NOT replayed -> fresh Community Hub replaces stale panel`

Durable public session click:

`public session card -> globally registered CommunitySessionPublicView(timeout=None) -> normal callback -> acknowledge first -> durable session mutation`

Public session controls must never be routed through private stale recovery.

## Changes

PR #332 merged stale Community Hub interaction recovery.
PR #333 merged the Open to Play → Find Players → Quick Match loop as `5600b50110bc5a27e88a088a50291d2990e1b53d`.

PR #334 merged HubLink into `main` as `addd2580ddff320b8e64c6d8df50dc848f4e5b65`. Post-merge Dank Shield CI and the Supabase migration deployment both completed successfully.

PR #335 merged into `main` as `93f41dc4845f78b7858c4a89a633c31be3830e0e` from final head `47fc75c52f692065a18ccd5cbd8ad469ff61d085`. Its exact-head required workflows were green, post-merge Community Hub CI and Dank Shield CI were green, Supabase migration deployment was green, and Discloud reported `discloud/commit: success`.

PR #336 merged into `main` as `cee40147ae64d547bbb9c93a790620a2c80bfbc6` from validated head `272fee22f680b8a8fef3748b1d60106ff91051a7`. Exact-head checks and post-merge Community Hub CI, Dank Shield CI, Ticket Owner Emergency Override, Supabase deployment, and Discloud deployment all completed successfully.

Current branch: `fix/live-captions-streaming-persistence-20260927`
Current slice: **general server Live Captions using the same hardened per-speaker DAVE runtime**

Implemented in this slice:
- removed the staff-facing raw **Partner server ID** modal from the new Partner Network path;
- added short-lived human HubLink codes formatted as `DANK-XXXX-XXXX`, using an ambiguity-resistant alphabet;
- plaintext codes exist only in the source admin's in-memory response; PostgreSQL stores only SHA-256 `code_hash` plus a short non-secret hint;
- one pending code per source server, 15-minute expiry, explicit Cancel HubLink, replacement revocation, bounded expiry cleanup, and a 10/hour source creation limit;
- source and target guild IDs come only from trusted Discord interaction context;
- target modal submit re-authorizes owner/admin/manage-guild authority before any lookup or mutation;
- target admin reviews the source server by name and must explicitly confirm **Connect Servers**;
- redemption is atomic/idempotent with advisory + row locks, rejects self-linking, rejects wrong-server replay, and safely replays only for the same target;
- successful HubLink connection activates the canonical partner pair with public session discovery on and aggregate/live activity sharing off by default;
- existing active link sharing choices are preserved on safe replay/reconnection;
- all user-facing partner fallbacks hide raw guild IDs;
- source creator receives a best-effort confirmation DM after successful redemption;
- **Add Dank Shield** uses a Community Hub-only non-Administrator OAuth permission set rather than the bot's broader moderation permission bundle;
- Community Hub install permissions cover View Channels, Send Messages, Send Messages in Threads, Embed Links, Read Message History, Manage Threads, Manage Channels, and View Audit Log; they explicitly exclude Administrator, Kick, Ban, Moderate Members, Manage Messages, and Manage Roles;
- guild-pinned **Reauthorize Dank Shield** opens the current server directly when server-level Hub permissions are missing;
- readiness checks cover server-level Hub permissions, effective configured Hub-channel permissions, deleted/missing Hub channel, configured/fallback temporary voice category, and category-level Manage Channels;
- readiness instructions include mobile navigation plus **Open Access Repair** and **Check Again**;
- runtime retention expires stale pending HubLinks without affecting ordinary Community Hub paths during schema rollout;
- legacy partner link review/revoke remains available for pre-HubLink records;
- Community Hub CI applies the base, matchmaking, and HubLink migrations twice and exercises create/replacement/redeem/replay/wrong-target/self-link/expiry/privacy/privilege invariants.

Implemented in the current voice-caption slice:
- changed the pinned Discord dependency to `discord.py[voice]==2.7.1`, installing the PyNaCl + davey voice dependencies that production logs previously reported missing;
- the first implementation pinned inbound-DAVE PR #62 head `bec048127f4148fd147afa3182c3771b6955dc08`; the first real soak produced zero sink frames, so it moved to the audited PR #54 soft fork; the second real soak proved that reader can still fail-stop on corrupt Opus, so the dependency is now pinned to upstream PR #58 head `03dd1e2dafe85522cc458441cd5b143b136ac836`, whose DAVE decrypt path guards session readiness, protocol version, SSRC warm-up, and passthrough frames;
- added a Dank Shield receive boundary that requires the voice receiver's SSRC→user mapping to agree with the source user before PCM may enter captions;
- added memory-only per-speaker consent so non-consenting users are dropped before PCM enters the caption queue;
- that original runtime-only consent model is now superseded by explicit **per-server remembered auto-caption consent**: only the preference boolean and optional BCP-47 language hint persist; live admission/audio remain ephemeral and are restored only while the member is in an active captioned VC;
- added counters for unknown speakers, identity mismatches, malformed PCM, queue overflow, and callback failures;
- added speech-preserving segmentation that splits isolated speakers by packet gaps/max duration without a destructive noise gate;
- the first transcription pass uses the untouched isolated PCM; low-confidence speech may receive a second amplitude-normalized pass that preserves every sample and timing;
- conflicting low-confidence transcriptions resolve to `[unclear audio]` rather than fabricated speech;
- the original provider was OpenAI `/v1/audio/transcriptions`; after the Discord/DAVE/Opus path was proven end-to-end, live testing hit provider HTTP 429 and the user chose to remove OpenAI entirely in favor of Gemini AI Studio Free Tier;
- added host/co-host/staff **Live Captions** control plus participant **Caption My Voice** self-consent;
- only one caption receiver may own a guild voice connection at a time;
- ending/cleaning a Community Hub session shuts the receiver down and clears speaker consent;
- no Chat Link API or message behavior is guessed or duplicated; cross-server text remains an external integration boundary.
- Live Captions are default-off behind `DANK_COMMUNITY_LIVE_CAPTIONS_ENABLED`; code may deploy without exposing un-soaked DAVE receive to users;
- caption startup fails closed if the required privacy notice cannot be posted;
- consent/start copy now states that opted-in audio is sent to Google Gemini's transcription API, that this deployment uses Gemini Free Tier where Google states submitted content may be used to improve its products, and that Dank Shield itself does not save the audio;
- caption shutdown cancels in-flight transcription tasks and discards queued/buffered audio so a stopped session cannot publish late captions;
- a missing/zero transcription confidence is treated as uncertain, never as implicitly trustworthy.
- process-wide Live Captions scale is bounded by configurable active-guild, speakers-per-session, and global transcription concurrency limits so one public deployment cannot fan out unbounded API/RAM load;
- `.env.example` documents the default-off feature gate, `GEMINI_API_KEY`, Gemini primary/fallback models, optional BCP-47 language hint, and scale limits without containing any real secret.
- PR #336 imports the caption runtime/receive names used by the UI callbacks, adds a discoverable Live Captions entry/overview from Community Hub home, exposes self-consent from private session details as well as the public card, and keeps the transcription gate disabled until the real DAVE soak test passes.
- PR #336 consent-status rendering uses the actual interaction user ID rather than assuming `list_user_sessions()` embeds a user ID that it does not return.
- general Live Captions now have a first-class `/dank home → Live Captions` path and do not require a Community Hub gaming session;
- the general mode reuses the same `CommunityVoiceCaptionManager`, guild receiver lock, per-speaker bridge, transcription engine, consent boundary, and feature gate instead of creating a competing receive implementation;
- server owner/admin/Manage Server starts or stops general captions for the ordinary voice channel they are currently in;
- general captions use durable per-guild setup for a dedicated output text channel rather than the text channel where staff happened to open the panel;
- owners can select an existing caption output or let Dank Shield create/reuse a read-only **#live-captions** channel, with bot write permissions validated before the setting is saved;
- owners can allow all ordinary VCs, select individual existing VCs, select whole existing voice categories such as gaming/squad-room categories, and explicitly exclude VCs; exclusions win;
- the ordinary-server transcript identifies its exact source voice channel, and only one caption receiver can own a guild at a time, so users in other VCs cannot leak into or start a competing caption stream;
- **/captions** is a first-class normal-user doorway while **/dank home → Live Captions** remains available, and **/dank setup → Live Captions** owns server configuration;
- every participant controls only their own **Caption My Voice** consent and must be in the captioned voice channel before opting in;
- Community Hub and general captions cannot run competing receivers in the same guild because both use the same `_guild_owner` lock;
- general caption state/consent remains memory-only and is not written to Supabase;
- opting out now blocks future frames, discards that speaker's segment buffer and queued frames, cancels their in-flight transcription tasks, and prevents scheduled pre-revocation frame delivery from publishing afterward;
- the receive bridge now stamps accepted frames with the speaker's consent generation; a frame scheduled under an older generation is rejected even if the same user opts back in before its callback runs;
- the default-off real-DAVE soak gate remains in place for both Community Hub and general server use.
- post-merge live testing found and fixed the caption-output picker rejecting discord.py `AppCommandChannel` partial values; the shared picker now resolves the partial through its resolver or the interaction guild cache before applying the normal GuildChannel contract.
- the global `DANK_COMMUNITY_LIVE_CAPTIONS_ENABLED` lock remains off, but the recognized Dank Shield bot owner can now use the ordinary-server Start/Stop control to launch a controlled DAVE soak session in one server; Community Hub and non-owner callers cannot bypass the validation gate;
- soak sessions use the same configured output, VC/category eligibility, one-receiver-per-guild lock, consent boundary, Gemini provider, stop/cleanup path, and privacy notice as normal captions rather than a separate test implementation;
- the Live Captions panel exposes soak telemetry while that controlled session is active: frames seen/routed/not-consented/unknown/mismatched/malformed plus transcription/unclear/failure counters;
- the soak panel now diagnoses the pipeline stage instead of silently failing: no decoded DAVE frames, identity/consent rejection, routed audio awaiting segmentation, Gemini/provider failure, empty transcript, Gemini fallback use, or successful publish;
- transcription/provider failures retain only a safe user-facing diagnosis in runtime state while full exceptions are logged server-side; Gemini HTTP/API status failures are translated into actionable messages without exposing GEMINI_API_KEY or raw provider response;
- caption engine telemetry now distinguishes transcribed, published, and empty segments, and self-consent tells the tester to speak for 2–5 seconds, pause about one second, then refresh;
- the DAVE capability probe now recognizes the hardened decoder-stage receive implementation instead of PR #62's removed `AudioReader._dave_decrypt` method;
- the soak path now counts raw UDP packets before the receive dependency and reports DAVE session presence/readiness/status/protocol/epoch, mapped SSRC count, and reader-listening state so a future zero-frame result can be localized below the sink;
- Dank Shield first attempts native libopus PLC for one isolated corrupt real Opus frame, then retains the narrow PR #57-compatible router survival drop as the final fallback; consecutive synthetic recovery is bounded and unexpected exceptions retain upstream fail-stop behavior;
- voice-receive startup now registers an `after` callback that records a sanitized reader-stop reason and increments a reader-failure counter, so the soak panel can show the actual receive-worker error instead of only `Reader: stopped`;
- the soak panel now reports corrupt Opus PLC recoveries separately from unrecoverable drops, plus reader failures alongside raw UDP, DAVE, SSRC, PCM, routing, and transcription telemetry;
- PR #343 temporarily added Discloud's `ffmpeg` APT option in an attempt to obtain libopus; live production disproved that assumption, so this slice removes that unrelated host dependency again;
- `opuslib-next-bundled==0.1.1` is pinned as Dank Shield's deterministic native Opus source; its Linux/macOS/Windows wheels contain the shared Xiph Opus library inside `opuslib_next/_native`;
- Live Captions resolves the bundled library path without importing the binding package, then passes that exact full path to documented `discord.opus.load_opus()`; an explicit `DANK_OPUS_LIBRARY` remains the first operator override and system discovery is only a final compatibility fallback;
- `voice_receive_capability()` still fails closed with a specific native-Opus reason instead of letting the packet reader crash later with `OpusNotLoaded`;
- CI now starts a fresh Python process, resolves the pinned bundled library, explicitly loads it into discord.py, verifies `discord.opus.is_loaded()`, and constructs a real `discord.opus.Decoder()` at 48 kHz stereo; this validates the actual ABI/symbol path rather than mocking the load;
- OpenAI transcription errors now parse the provider's structured error `code` / `type` and distinguish temporary `rate_limit_exceeded` from `credit_balance_exhausted`, organization/project spend limits, organization usage limits, and generic `insufficient_quota`;
- terminal API-key/permission/billing/quota failures block further transcription requests for that caption session so subsequent speech is dropped in memory instead of repeatedly hammering an unavailable provider; restarting the caption session after the account issue is fixed clears that provider block;
- the soak panel exposes provider-skipped segments and reports the exact safe provider block reason;
- the consent diagnosis now treats `frames_not_consented` as cumulative: if a speaker is currently opted in, pre-opt-in drops are identified as historical instead of claiming consent is still broken.
- the unary `gemini-3.5-transcribe` + audio fallback path is replaced by `gemini-3.5-transcribe-live`; each opted-in Discord speaker owns a separate persistent Gemini Live WebSocket, preserving the existing per-speaker privacy boundary while avoiding a new HTTP transcription request for every utterance;
- isolated Discord PCM is converted from 48 kHz stereo signed-16 to 16 kHz mono signed-16 before Live API submission;
- empty language hints are now intentional **Auto / all supported languages** behavior, giving Gemini automatic detection across its supported locales plus code-switching; finalized transcript results retain Gemini's BCP-47 `languageCode`;
- server owners configure a single shared caption text policy used by both ordinary-server and Community Hub sessions: **Original**, **English**, or **Original + English**;
- English conversion uses `gemini-3.1-flash-lite` on finalized transcript text only; audio is never submitted again for translation, and detected `en-*` captions skip the translation request entirely;
- translation failure degrades to the original caption instead of blocking transcription; translation requests/skips/cache hits/failures are separately observable;
- Gemini Live sessions proactively rotate before the documented 10-minute maximum and honor `goAway` by reconnecting before the next utterance;
- the setup UI exposes **Language & Translation** without forcing owners to maintain an 85-language picker; auto-detection remains the safe default.
- accuracy repair replaces the box-average downsampler with a 31-tap Hamming-windowed sinc low-pass FIR before 3:1 decimation, preserving speech-band audio while suppressing >8 kHz alias fold-back;
- Gemini Live uses documented manual VAD with automatic activity detection disabled; `activityStart` is sent immediately before the first real-time PCM chunk and `activityEnd` only at the local speech-gap boundary; the old max-duration cap is now a realtime memory rollover rather than a provider turn boundary, and `audioStreamEnd` is not used in this mode;
- `/captions → My Language` is a memory-only per-speaker recognition hint; Auto remains the all-supported-language default, while a known language such as English maps to a supported BCP-47 hint and reconnects only that speaker's Gemini session;
- the same personal language hint automatically applies when that user opts into Community Hub captions, without changing Community Hub routing or another participant's recognition;
- soak telemetry now retains each opted-in speaker's last RMS level, Gemini-detected language, and explicit-language mismatch count without persisting raw audio;
- when an explicit language hint conflicts with Gemini's returned primary language family, Dank Shield publishes `[unclear audio]` instead of unrelated-language text.
- **My Language** no longer opens a text-input modal; it opens a dropdown-only flow with **Auto · All Supported Languages** plus grouped language choices;
- every unique BCP-47 code in Google's current Gemini 3.5 Transcribe supported-language table appears exactly once in the dropdown groups, and every select remains within Discord's 25-option limit;
- choosing Auto or a language updates only that user's memory-only hint and reconnects only that speaker's Gemini Live session when necessary.
- the receive boundary records Discord voice gateway speaking signals for opted-in users only; zero/stop speaking states and non-consenting users cannot trigger recovery;
- each opted-in speaking signal snapshots receive counters and waits 2.5 seconds; any PCM progress proves the media path is alive and no reconnect occurs;
- if the reader still reports listening, DAVE is ready, an SSRC is mapped, but no PCM progresses, the runtime classifies the media transport as stalled instead of treating control-plane flags as media health;
- stalled recovery fully replaces the old VoiceRecvClient and per-speaker bridge while keeping the existing CaptionEngine/Gemini state alive;
- the fresh bridge restores exactly the same previously opted-in user IDs; old sink cleanup only clears the discarded old bridge, preventing a cleanup race from erasing recovered consent;
- automatic receive recovery has a 20-second cooldown and at most two attempts per rolling minute, so normal silence never causes reconnect churn;
- soak telemetry now reports gateway speaking signals, receive recoveries/failures, and the last recovery reason.

Still deferred after HubLink:
- session privacy / incomplete `invite_only` behavior;
- staff event edit/cancel/delete;
- partner live-activity redesign and scale-safe caching beyond the link/connect foundation.

## Validation / results

Evidence obtained during PR #334 development:
- HubLink migration already passed Community Hub PostgreSQL smoke on an earlier implementation head, including two-pass migration replay and all one-time-code invariants;
- Schema Authority SQL passed on an earlier HubLink head;
- Python compilation passed before the first focused-test run;
- the first focused-test failure identified two removed privacy-copy guarantees, and the implementation restored the guarantees rather than weakening tests;
- no raw `Partner server ID` modal or `Server {other_id}` fallback remains in the user-facing Community Hub partner code;
- install/reauthorization has been narrowed to Community Hub-only non-Administrator permissions;
- readiness was traced against the actual thread/voice provisioning path and now checks contextual channel/category permissions.

PR #335 final-head validation completed green for Community Hub CI, Dank Shield CI, Profile Runtime Diagnostics, Dank Design Regression CI, Ticket Owner Emergency Override, and Application Command Size Diagnostics. The merge commit also completed Community Hub CI, Dank Shield CI, Ticket Owner Emergency Override, Supabase migration deployment, and Discloud deployment successfully.

PR #336 final head `272fee22f680b8a8fef3748b1d60106ff91051a7` completed Community Hub CI, Dank Shield CI, Profile Runtime Diagnostics, Dank Design Regression CI, Ticket Owner Emergency Override, and Application Command Size Diagnostics successfully. After merge, Community Hub CI, Dank Shield CI, Ticket Owner Emergency Override, Supabase deployment, and Discloud deployment also completed successfully.

PR #352 (`fix/live-captions-streaming-persistence-20260927`) contains the provider-boundary repair for the live soak shown on 2026-09-27: Discord/DAVE/Opus remained healthy while Gemini Live timed out waiting for finalized transcripts because production still buffered a whole local utterance before sending it. The branch now streams isolated PCM frames to Gemini as they arrive, prewarms each opted-in speaker socket, keeps a dedicated receive loop, and preserves remembered per-server auto-caption/language preferences. After reconciling current main, it also preserves c055's deterministic manual-VAD contract: `activityStart` before the first streamed PCM and `activityEnd` at the local segment boundary. Telemetry now reports audio chunks plus manual-VAD starts/ends and interim/final events.

The first PR #352 exact-head CI run reached **2156 passed / 1 failed** in the full unit suite and **124 passed / 1 failed** in Community Hub focused CI. Both failures were the same static privacy-contract regression: the rewritten Auto-Caption UI had dropped the existing disclosure that English output translates only finalized transcript text and never resubmits audio. The implementation restored that truthful disclosure rather than weakening the contract. Final-head CI is required again after that correction.

After reconciling PR #352 onto main `c0550646254d1f39b90b13921b25bfa5e25111dd`, reconciled head `b3ce99c71f0d3f98bcd26955348e44fac8b117c9` became mergeable and 0 commits behind main. Its first Community Hub CI compiled the caption stack successfully and passed **124 tests**, with exactly **1 failure** caused by a regression test matching an obsolete explanatory comment string rather than behavior. Commit `8888f1aa591908aa0a6182fcbf983ea7c394ac5b` replaced that prose assertion with the structural invariant that `await stream_frame(frame)` occurs before `self.segmenter.feed(frame)`. Exact-head CI must rerun after the task-record update.

Deeper execution-path inspection then showed that the literal `stream_frame -> segmenter.feed` ordering was itself unsafe at a pause boundary because it could send the first post-gap frame into the previous Gemini activity. Commits `215bb88aa14abcb37310b8776e7eeaedced434bc`, `4d510c0bec4c54ac2025b8fe2b41bcfb35117517`, and `24352cccfc38220b96daec9f7529217897c3dfbe` replace that race with pre-classified boundary sealing, per-turn final futures, FIFO result mapping, post-gap ordering regression coverage, and failed-session reset. Exact-head CI is required for the resulting head.

PR #354 / branch `fix/live-captions-accuracy-boundaries-20260927` is based directly on production merge `d7f70bd0f6f1bd22e9088c84627e84a77f89272a`. It adds RTP-aware gap classification, a bounded realtime idle-jitter grace, and realtime memory rollover that no longer forces an 8-second provider boundary, with focused regression tests for all three behaviors. Exact-head CI and live soak evidence are still required before merge/completion claims.

Rollback branch `fix/live-captions-revert-354-regression-20260927` starts from deployed PR #354 main `011f718eb9ffd38a7d9b326a95a9e1a1fcd50f9a` and restores the exact PR #352 versions of `community_voice_captions.py`, its focused tests, and the voice-stack protocol documentation. This deliberately removes only the unproven #354 boundary experiment before any further Gemini finalization change is considered.

Current follow-up branch `fix/live-captions-stable-interim-recovery-20260927` is based directly on deployed rollback main `af56f303728cd2952d5883ff85e53fb7a1383b8d`. It does not alter the restored #352 speech-boundary behavior. It adds exact-turn stable-interim timeout recovery, recovery telemetry, an Auto-language accuracy hint, bounded native Opus PLC recovery for isolated corrupt real frames, and regression coverage proving that unstable interims never publish, authoritative finals always beat fallback candidates, and consecutive corrupt frames cannot become an unbounded synthetic run. Exact-head CI and a fresh English soak are required before completion claims.

Current accuracy branch `fix/live-captions-100ms-provider-chunks-20260928` is based directly on deployed PR #357 main `501efb62dd4162541d55c1a52744a9d7b2e47c99`. It changes Gemini transport chunk granularity plus one provider-session finalization setting: source frames still enter immediately, the existing speech segmenter still owns boundaries, short buffered remainders flush before `activityEnd`, and `activityHandling` is explicitly `NO_INTERRUPTION` so a new Discord turn cannot cut off the prior finalized transcript response. Regression coverage must prove five 20 ms Discord frames become one 100 ms provider chunk, a short utterance still sends its final remainder before ending the activity, and the Gemini setup contract keeps non-interrupting activity handling.

Required for the current general Live Captions slice:
- focused general/Community Hub caption contracts green;
- full Dank Shield CI and every other workflow triggered by the exact head green;
- final diff/mergeability review against current `main`;
- no second receive/transcription owner introduced;
- no re-enabling of `DANK_COMMUNITY_LIVE_CAPTIONS_ENABLED` before the real Discord DAVE soak test.

## Cleanup / conflicts

The fix must not add a second Community Hub business handler or replay stale actions. The shared component runtime remains the only stale-private-panel recovery owner. CommunitySessionPublicView remains the only durable public session-button owner.

## Blockers / risks

- Google developer reports from August/September 2026 describe an intermittent `gemini-3.5-transcribe-live` backend stall where a WebSocket can remain connected and accept audio while server responses stop. PR #352 already closes the speaker session on finalized-transcript timeout so the next utterance reconnects, but the production soak must verify the new audio-chunk/final-event telemetry before the global gate can be lifted.
No production log excerpt for the latest intermittent Community Hub failure is available yet, so current code inspection can prove the stale-panel timing gap but cannot claim every fresh-panel failure has the same cause. Existing component-runtime diagnostics must remain intact so any remaining fresh-panel failure produces actionable evidence instead of guesswork.

## Backlog inside this same active task

After interaction reliability is validated:
- finish the intended partner live-activity experience using explicit per-partner authorization/privacy controls;
- finish member-facing Hub UX/polish and remove awkward/dead-end flows;
- verify event/session/notification discoverability from mobile;
- run a full promised-vs-implemented Community Hub feature audit and close each real gap without creating duplicate ownership.

## Next step

Validate `fix/live-captions-streaming-persistence-20260927` on its exact reconciled head. Required regression proof: real-time frame streaming reaches Gemini before local segment finalization; manual VAD sends exactly `activityStart → audio… → activityEnd` for an utterance without replaying the segment or sending `audioStreamEnd`; the persistent per-server language/auto-caption preference preserves unrelated member settings; remembered members are restored at caption-session start and on voice join; opt-out immediately revokes/purges runtime audio; ordinary-server and Community Hub paths share the same consent. Merge only when every triggered CI gate is green. After Discloud deploys the exact merge, run one fresh bot-owner soak: the member should need to enable **Auto-Caption My Voice** and choose **English (US)** only once. During speech the panel must show Gemini **audio chunks increasing before the pause**, **manual VAD starts >= 1**, then **manual VAD ends >= 1**, **final events >= 1**, **transcribed/published >= 1**, and **failures 0**. Keep `DANK_COMMUNITY_LIVE_CAPTIONS_ENABLED` off until this streaming/persistence soak plus non-English/Auto/code-switching and Community Hub validation pass.