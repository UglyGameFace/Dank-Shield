# SEARCH-SAFE-MASTER-AUDIT-018 — Final 100-Point Ledger

Scope: Search-Safe Naming, shared guild configuration, Dank Design integration, Role Editor integration, Member Setup coexistence, production-facing footer/UI cleanup, scale/concurrency, and validation evidence.

Status meanings:
- **PASS** — implementation and source/regression evidence support the requirement.
- **NEEDS HARDENING** — not a current merge-blocking correctness defect unless explicitly stated; remaining evidence, observability, product clarity, or post-merge validation is still required.
- **N/A** — intentionally outside the product contract.

## A. Branch, ancestry, ownership, and task integrity

1. **PASS** — PR #362 ancestry was reconciled against merged PR #361 rather than replacing Member Setup wholesale.
2. **PASS** — remediation branch is based on production merge commit 7bf1bf0799ffaf70cb4b207da9e42750797123cf.
3. **PASS** — branch has remained 0 behind production main at validation checkpoints.
4. **PASS** — PR #363 is the single remediation PR for this master audit.
5. **PASS** — ACTIVE_TASK.md contains one active master task rather than parallel implementation tasks.
6. **PASS** — Search-Safe naming remains owned by the naming identity/Search-Safe services rather than duplicated across feature modules.
7. **PASS** — Dank Design remains the Unicode font-map/style owner.
8. **PASS** — Role Editor remains the canonical user-facing role mutation owner.
9. **PASS** — Member Setup retains its own revision/access-gate owner and stored IDs.
10. **PASS** — final changed-file review found suspicious-looking files were either footer-only edits or required atomic-config/validation work, not unrelated feature development.

## B. Canonical guild configuration and persistence

11. **PASS** — canonical guild config no longer relies on client-side whole-settings replacement for normal writes.
12. **PASS** — PostgreSQL sparse JSON patch RPC preserves unrelated sibling config keys under concurrent writes.
13. **PASS** — explicit clear operations use the same canonical database-side mutation boundary.
14. **PASS** — concurrent Naming Identity and Member Setup writes have dedicated regression coverage.
15. **PASS** — concurrent clear/write behavior has dedicated regression coverage.
16. **PASS** — real PostgreSQL CI smoke coverage exercises concurrent sparse writes and clears.
17. **PASS** — full config-history restore routes through canonical sparse writers instead of stale whole-row replacement.
18. **PASS** — selective config-history restore routes through canonical sparse writers.
19. **PASS** — nested shared feature blobs can use compare-and-swap expectations.
20. **PASS** — stale nested CAS writers are rejected without overwriting the winning value.
21. **PASS** — Naming Identity replays its pure transform against the winning state after a CAS conflict.
22. **PASS** — protected setup keys cannot use CAS as a bypass around canonical write-safety controls.
23. **PASS** — future Naming Identity schema versions cannot be silently downgraded by an older worker.
24. **PASS** — unsupported future schema reads fail closed to Preserve/no-alias behavior.
25. **PASS** — malformed persisted policy booleans such as string false are parsed explicitly instead of Python truthiness.

## C. Permissions, authority, and hierarchy

26. **PASS** — Search-Safe reviewed role repair binds the initiating Discord actor.
27. **PASS** — human actor-vs-target hierarchy uses the same shared authority primitive as Role Editor.
28. **PASS** — server owner/Administrator/Manage Roles rules remain explicit.
29. **PASS** — bot Manage Roles authority is checked separately from human authority.
30. **PASS** — bot top-role hierarchy is checked before role mutation.
31. **PASS** — human top-role hierarchy is rechecked before reviewed role mutation.
32. **PASS** — actor authority is re-resolved from the live guild member when available.
33. **PASS** — authority is rechecked inside the resource lock immediately before the PATCH.
34. **PASS** — @everyone remains non-editable.
35. **PASS** — integration/managed roles remain non-editable.
36. **PASS** — automatic policy enforcement remains explicitly actorless instead of inventing a fake human actor.
37. **PASS** — automatic actorless enforcement still checks bot authority.
38. **PASS** — reviewed channel repair checks bot Manage Channels authority.
39. **PASS** — Share Router reserved infrastructure remains excluded from Search-Safe design mutation.
40. **PASS** — /role autocomplete filters results before Discord returns them: role managers see manageable roles; normal members see only approved self-service roles.

## D. Semantic naming and Unicode behavior

41. **PASS** — Preserve remains the default naming mode.
42. **PASS** — Search-Safe remains opt-in per guild.
43. **PASS** — roles and channels can be Search-Safe independently of categories.
44. **PASS** — categories remain visually styled by default unless explicitly enabled for Search-Safe.
45. **PASS** — semantic lookup decodes known styled letters for matching.
46. **PASS** — previous names are stored by resource identity rather than treated as free-floating aliases.
47. **PASS** — alias history is bounded per resource.
48. **PASS** — tracked naming resources are bounded per guild.
49. **PASS** — process naming-state cache is bounded.
50. **PASS** — rapid rename aliases preserve event chronology instead of set/alphabetical order.
51. **PASS** — exact saved aliases outrank unrelated partial live-name matches.
52. **PASS** — exact live-name matches retain a no-database autocomplete fast path.
53. **PASS** — duplicate semantic role matches remain ambiguous rather than guessing.
54. **PASS** — Search-Safe live rewriting reuses Dank Design's canonical known-font map.
55. **PASS** — reverse font mapping is cached instead of rebuilt for every glyph.
56. **PASS** — live rewriting is limited to known glyphs decoding to one ASCII alphabetic letter.
57. **PASS** — unrelated compatibility symbols such as trademark/service marks are preserved.
58. **PASS** — compatibility temperature symbols are preserved.
59. **PASS** — ligatures unrelated to the known styled-letter map are preserved.
60. **PASS** — decorative digits such as circled digits are preserved.

## E. Reviewed Search-Safe repair transaction

61. **PASS** — repair previews freeze the exact bounded resource identities the admin reviewed.
62. **PASS** — Apply mutates only reviewed IDs rather than rescanning and choosing a different first batch.
63. **PASS** — reviewed current name is revalidated before mutation.
64. **PASS** — reviewed Search-Safe output is re-derived and revalidated before mutation.
65. **PASS** — permission/hierarchy blockers are revalidated before mutation.
66. **PASS** — resource disappearance after preview fails closed.
67. **PASS** — resource rename after preview fails closed and requires a new preview.
68. **PASS** — resources added after preview cannot silently enter the reviewed batch.
69. **PASS** — repair batch remains capped at 25.
70. **PASS** — Preview Next 25 creates another reviewed preview instead of immediately mutating a fresh rescan.
71. **PASS** — Search-Safe role/channel PATCHes funnel through one edit helper.
72. **PASS** — reviewed/automatic name PATCHes share the process-wide Discord REST budget.
73. **PASS** — name PATCHes use the canonical retry helper for retryable Discord/API failures.
74. **PASS** — Search-Safe no longer contains alternate direct name PATCH paths bypassing the pacing helper.
75. **PASS** — per-resource locks use weak lifecycle ownership and cannot split queued waiters onto a second lock.

## F. Dank Design and internal mutation integration

76. **PASS** — reviewed Dank Design previews show Search-Safe-effective final names.
77. **PASS** — every active reviewed Design preview stores the naming-policy snapshot used to render it.
78. **PASS** — Design Apply fails closed if naming policy changed after Preview.
79. **PASS** — direct Dank Design Rename applies Search-Safe before Discord mutation.
80. **PASS** — direct Rename persists the actual effective live name instead of a contradictory styled exact-name rule.
81. **PASS** — Dank Design Undo previews Search-Safe-effective restore names.
82. **PASS** — Undo rechecks naming policy before mutation.
83. **PASS** — Dank Design and Search-Safe share one per-guild naming mutation lock.
84. **PASS** — automatic Search-Safe enforcement waits for in-flight Design Apply/Undo transactions.
85. **PASS** — /role create/edit/duplicate pre-adjust names before Discord mutation.
86. **PASS** — Channel Builder uses one fresh naming-policy snapshot per batch and pre-adjusts arbitrary create/rename names.
87. **PASS** — Setup Assistant custom role/category/text/voice names are policy-adjusted before lookup/create.
88. **PASS** — Server Stats Design-synced names are policy-adjusted before create/refresh and legacy/effective names remain discoverable.
89. **PASS** — gateway enforcement is now a safety net for manual/external edits rather than the normal second PATCH for known internal paths.
90. **PASS** — naming mutation/review boundaries force a fresh policy read rather than trusting a long-lived process cache.

## G. Cache, debounce, shutdown, Member Setup, and UI

91. **PASS** — naming cache TTL was reduced and an expired naming cache bypasses the guild-config cache, preventing stacked stale TTLs.
92. **PASS** — failed alias/delete persistence requeues claimed changes instead of silently dropping them.
93. **PASS** — cancellation after a flush claimed events requeues those events.
94. **PASS** — bot shutdown cancels debounce timers and performs a bounded immediate naming-state flush before the Discord loop closes.
95. **REOPENED / IN REMEDIATION** — the semantic distinction is correct, but the live admin selectors spawned separate ephemeral native-picker panels without shared Search-Safe lookup or consistent Back/Close behavior. The current continuation consolidates these onto the shared resource browser and single-message lifecycle.
96. **REOPENED / IN REMEDIATION** — the rule-chain copy is correct, but the original audit did not validate the manager/picker interaction lifecycle. Selection, search, save, Back, and Close now require single-message regression coverage before this item returns to PASS.
97. **PASS** — PR #365 completed the consumer-level footer re-audit: escaped developer/process commentary was replaced with user guidance, helper-generated ticket footer refresh was kept compatible, expanded footer regressions passed exact-head CI, and the PR merged as `93cab4fdbe693f91808a99deab8fe32b560cbc47`.
98. **PASS** — persistent footer/runtime compatibility is retained for old Welcome, Verify, tickets/transcripts, live-profile, Spam Guard panel, and Spam Guard incident messages.
99. **PASS** — stale identity records for resources deleted while the bot was offline are opportunistically CAS-pruned only when an authorized admin opens Search-Safe Naming; there is still no startup/global guild sweep. Fixed-cardinality naming metrics, automatic failure visibility, normalized-name collision warnings, one-to-one preview/live name-length behavior, searchability-vs-mentionability guidance, and removal of the unused channel autocomplete surface are all regression-covered.
100. **NEEDS HARDENING** — production closure still requires the final exact-head CI suite, post-merge Supabase migration deployment, Discloud startup, mobile /role canary, Search-Safe/Design/Member Setup live canary, and soak evidence.

## Post-merge escaped-defect addendum — 2026-09-29

The live Android Share Router canary after PR #363 merged exposed a consumer-integration gap that the original ledger did not prove.

- Item 45 remains true for the canonical Naming Identity semantic engine: known styled letters decode to the same semantic key.
- The original audit also proved that `/role` autocomplete consumes that semantic/alias-aware engine.
- It did **not** prove that every Dank Shield-owned role/channel/category picker consumed the same identity layer.
- `DankGuildResourceBrowserView.build_resource_candidates()` still performed literal current-name/ID/mention matching, so Share Router could miss a styled destination when the admin searched its plain semantic name.
- This is a reopened **P1 Search-Safe consumer-integration defect**, not a new unrelated task.
- The continuation branch `fix/search-safe-resource-browser-integration-20260929` wires the shared resource browser to semantic live-name matching plus the existing bounded previous-name alias index and adds focused regressions.
- Consumer inventory found one additional live duplicate: Fix Access had its own `DankPickerView`-based channel/category discovery/search/paging implementation with the same literal-name filtering. It is consolidated onto `DankGuildResourceBrowserView` while retaining the Fix Access permission, visibility, mutation, audit, navigation, and error boundaries.
- Normal public setup is already bound to `DankGuildResourceBrowserView`; the separate `/dank setup-find` implementation is listed in `PUBLIC_HIDDEN_DANK_CHILDREN`, so it is not treated as a normal public consumer requiring a second authority.
- The literal custom role/channel/category search sweep now has one shared live search owner for these picker paths rather than two competing browser engines.
- A separate selector inventory found remaining feature-owned Discord-native `RoleSelect`/`ChannelSelect` surfaces. They are not hidden-alias-aware because Discord owns their discovery. This addendum does not falsely classify them as shared-browser consumers or silently migrate unrelated feature contracts.
- The corrected claim for this remediation is: Dank Shield-owned **searchable resource-browser** paths use the canonical semantic/alias-aware browser; `/role` remains a separate semantic/alias-aware text query. It is not a claim that every native Discord selector in the repository supports saved aliases.
- Closure now additionally requires exact-head CI and a fresh Android canary proving a plain query such as `general news` finds the styled destination in Share Router.

## Footer-hygiene escaped-defect addendum — 2026-09-29

The post-merge consumer audit reopened item 97. Direct footer scanning successfully removed raw IDs and known encoded/runtime markers, but several production footers still exposed implementation language without matching the blacklist.

Escaped examples: webhook-secret/persistent-replacement implementation order, “canonical” lifecycle sender ownership, “provider-backed” feature availability, “health evidence,” Share Router “reserved infrastructure,” consolidated command-entry implementation notes, the ticket-panel `category-menu` internal label, and permission-repair “baseline” terminology.

The continuation branch `fix/footer-hygiene-consumer-audit-20260929` replaces these with user-facing guidance and expands the regression contract to forbid the escaped phrases. Ticket-panel footer refresh behavior remains intact and now refreshes to the human-readable footer.

PR #365 passed every exact-head workflow and merged to production `main` as `93cab4fdbe693f91808a99deab8fe32b560cbc47`, restoring item 97 to PASS.

## Member Setup lifecycle escaped-defect addendum — 2026-09-29

Items 95–96 were originally validated as copy/semantic requirements, but the mobile interaction lifecycle was not tested. Production still opened four Member Setup resource choices as new ephemeral native-selector messages, provided no shared Back/Close/Search-Safe browser contract, and used a thinking defer before returning a fresh manager panel. The shared Search modal also created a new ephemeral result panel.

The continuation branch `fix/member-setup-picker-lifecycle-20260929` moves Member Setup admin resource discovery onto `DankGuildResourceBrowserView`, keeps selection/search/save/back/close on the same manager message, preserves the public member flow’s separate private response contract, adds explicit manager dismissal, and removes raw panel message-ID/debug-style status copy.

The re-audit also found a semantic safety edge: saved Access Role and Eligibility Prerequisite could be the same role. That produces a dependency deadlock because the member would need the Access role before Dank Shield is allowed to grant that Access role. The continuation hides the conflicting role in the picker and revalidates the invariant in picker save, **Create Member Access** reuse, and `gate_health()`.

## Merge-blocker conclusion

The earlier Search-Safe shared-browser consumer blocker was remediated and merged by PR #364. Footer item 97 was remediated and merged by PR #365. The current known implementation blocker is items 95–96: PR #366 must pass exact-head validation and merge before Member Setup lifecycle can return to PASS.

Item 100 remains the final production-evidence gate. Even after PR #366 merges, closure still requires canonical `main` CI, deployment acceptance on that same `main` SHA, and the deferred mobile/live canaries. It must not be represented as production-complete early.
