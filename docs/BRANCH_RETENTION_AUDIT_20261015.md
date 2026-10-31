# Dank Shield branch hygiene: reviewed historical retirement

Status: first six owner-run cleanup waves completed; the merged seventh-wave Unicode reconciliation still requires owner preview and separate confirmed branch deletion. Eighth-wave safe retirement for two finished cleanup PR heads is prepared for CI. No automatic deletion. Scope remains GitHub branch references only, never the Discloud app, main, or runtime data.

## Verified checkpoint

- Original inventory: **517** after first cleanup PR branch was created.
- First owner-authorized run removed **222**, second removed **137**.
- After both executions, GitHub had **159 branches**: 74 exact-head merged into `main`, 8 exact-head merged into another base, 19 closed-but-unmerged PR heads, 15 changed-after-merge PR heads, 42 without a matching PR, and `main`.
- There were **no open PRs** at that checkpoint. "No open PR" is *not* proof a branch's work is complete.
- The reviewed retirement manifest in the existing `guarded-merged-branch-cleanup.yml` has **17 frozen branch/SHA pairs**: 13 old exact-head merged-to-`main` PRs and 4 closed PRs explicitly superseded by a merged PR or used only for validation.
- The workflow still requires an owner-dispatched **preview-reviewed**, then a separate **delete-reviewed** action with the exact confirmation. Merge does not delete branches. The actual run must recheck refs before deletion.

## Closed-but-unmerged PR disposition (19)

| PR | Branch | Retention decision |
| --- | --- | --- |
| #93 | `fix/startup-spamguard-command-activity-health` | Reviewed retirement; body identifies merged #94 as replacement |
| #95 | `fix/runtime-startup-hygiene-spamguard-default` | Reviewed retirement; body identifies merged #94 as replacement |
| #172 | `ci/ds-setup-020-macos-trigger` | Reviewed retirement; documented temporary validation for merged #171 |
| #173 | `ci/ds-setup-020-final-validation` | Reviewed retirement; documented temporary validation for merged #171 |
| #9 | `feature/setup-picker-wizard` | Reported superseded by `main`; hold pending file/scope comparison |
| #10 | `scale/production-readiness-10k` | Unsafe legacy monkey patches; **do not merge**; preserve until historical requirements are triaged |
| #45 | `hotfix/quiet-member-update-modlog` | Fourth-wave reviewed retirement: PR #46 merged the intended listener fix; PR #45 explicitly rejects its obsolete monkey patches |
| #114 | `feat/native-welcome-cards` | Superseded but stacked historical PR #113 changes; hold until preservation confirmed |
| #217 | `chore/finalize-ds-sec-045-record` | Fourth-wave reviewed retirement: bookkeeping-only closeout; functional implementation #214 merged |
| #219 | `chore/align-active-task-base-pr216` | Fourth-wave reviewed retirement: one-file bookkeeping alignment superseded by merged #216 |
| #231 | `chore/close-interaction-ownership-audit` | Fourth-wave reviewed retirement: bookkeeping-only closure; canonical implementation #229 merged |
| #237 | `chore/close-antinuke-authorized-bot-trust` | Explicitly suspended closure after ongoing AntiNuke failures; do not close task by deleting branch |
| #263 | `fix/welcome-card-long-tail-unicode` | Hold: validation incomplete for Unicode/font rendering |
| #285 | `fix/quiet-notice-auto-clear-recovery-20260921` | Hold: production quiet-notice behavior needs validation |
| #310 | `fix/ticket-panel-legacy-interaction-recovery-20260924` | Hold: exact live legacy panel path and replacement coverage need verification |
| #355 | `fix/live-captions-finalization-no-interrupt-20260927` | Hold: later merged #356/#357 exist, but direct equivalence not established |
| #458 | `feat/cinema-library-intelligence` | **Hold:** mixed branch is **five commits ahead** of `backup/cinema-library-standalone-interleaved-20261006`. Backup alone does *not* preserve entire head; clean #459 is merged but remaining commits need comparison |
| #544 | `fix/prod-shard-guild-loops-no-batching-20261013` | Hold: production-branch-specific backport; inspect deployment/reunification evidence |
| #555 | `fix/source-backup-large-toc-stream-20261013` | Hold: merged #557 fixed TOC pipe handling, but equivalence and source backup correctness remain unverified |

**Do not bulk-merge any of these branches to close them.** Closed/old does not imply their unique commits are wanted on the current `main`. Feature fixes are separate future implementation work and do not bypass single-task locking.

## Additional retained refs

- **Discloud selector:** `aaa-cinema-pre-pregateway-bind` is documented as the currently linked application branch. Keep until independent Discloud cutover verification.
- **Explicit recovery:** `backup/stoneyverify-stable-7fdc331-20261008` is a documented rollback checkpoint. Other `aaa-*`, `archive/*`, `backup/*`, `recovery/*`, `integration/*`, `reconcile/*` and deployment/migration branch refs remain outside this reviewed batch.
- **15 changed-after-merge heads:** do not prune merely from PR's merged status because the current head SHA differs from the merged PR head. Some branches are dozens of commits or more ahead of `main`.
- **42 no-PR refs:** do not infer disposability from their name or absence of a PR. Several are unique-commit histories or checkpoints.
- **Recent merged PR branches:** retain temporarily during Discloud/Android acceptance and database cutover. Their merged code in `main` does not itself prove production deployment.

## Validation / completion gate

- The reviewed manifest requires the exact documented head SHA; no glob or date-based widening.
- For the 13 merged entries, the exact PR head SHA and merged-into-`main` status are checked.
- For the 4 retired unmerged entries, the original PR must still be closed/unmerged and its documented successor PR must still be merged into `main`. This is **manual retirement**, not a generic rule for unmerged branches.
- Open PR references, protected refs, `main`, and changed SHAs are excluded, with a second head/protection check just before deletion.
- Preview and invalid-confirmation simulations must pass, and repo CI must be green; actual deletion and post-run counts remain to be verified separately.

**Historical third-wave instruction (completed):** This records the process for the earlier 17-candidate retirement. The current live checkpoint and next steps are in the latest section below.


## October 15 fourth-wave checkpoint (after PR #584 execution)

- GitHub branch listing now has **143** refs (517 original, 374 removed in three owner-executed waves).
- Full inventory matches: 62 exact-head merged into main, 8 exact-head merged into other bases, 15 closed-unmerged, 15 changed-after-merge, 42 with no PR and main.
- **Nine further exact-SHA branches** are manually reviewed for retirement using the existing `preview-reviewed`/`delete-reviewed` path. Prior 17 names stay recorded but no longer exist:
  - Exact-head PRs merged into main: #582, #583, #584, #293. Their review diffs and merge commits remain available.
  - Closed but unmerged: #45 (explicitly replaced by merged #46; discarded runtime monkey patches), #217 (bookkeeping-only, functional #214 merged), #219 (bookkeeping-only, #216 merged), #231 (bookkeeping-only, #229 merged). No product code is being merged as part of branch cleanup.
  - No-PR: `chore/close-release-governance-009` at `9112528e42e77ec348abe69d9207e37a64294380`, independently confirmed a Git ancestor of `main` with zero ahead commits and no matching PR.
- Every deletion requires branch head SHA to match the frozen manifest, non-protected ref, no open PR reference, appropriate closed/merged PR status and verified replacement, or real no-PR Git ancestry. GitHub rechecks SHA/protection immediately before deletion.
- The linked Discloud branch and all backups, recoveries, diverged branches, and outstanding product implementation branches remain excluded. Five production-base merged PR heads are ancestors of main, but their operational acceptance remains uncertain, so they are deliberately **not** in this wave.
- **PR #9** has a matching setup picker on current main but remains held pending deeper functional equivalence review. #10, #114, #237, #263, #285, #310, #355, #458, #544 and #555 are not silently retired. The oldest unmerged bookkeeping or topic branches were reviewed individually; no direct production code is changed.
- Operational acceptance: no new bot deploy, CI and actual deletion are separate checks. After this PR passes CI and merges, dispatch **preview-reviewed** first and inspect the nine candidate names, then separately **delete-reviewed** with `DELETE_REVIEWED_HISTORICAL_BRANCHES`. Recheck resulting branch count and all protected names.


## October 15 fifth-wave checkpoint (after PR #585 owner runs)

The last two owner-dispatched guarded workflow runs (on PR #585 merge SHA `f3c231e7d9b854263f6f5f427d1d1559a0288624`) succeeded. Exact GitHub listing after both: **135** remaining branches. All nine fourth-wave names were absent.

Remaining inventory after fourth-wave execution: 59 exact-tip merged-to-main PR refs, 8 exact-tip merged to another base, 11 closed-but-unmerged PR refs, 15 heads changed since merger, 41 refs with no matching PR, and main. This inventory is not a recommendation to bulk-delete 135 refs.

**Fifth wave is limited to four exact head/SHA pairs:**

| Branch | Merged PR | Basis for individually reviewed retirement |
| --- | ---: | --- |
| `chore/verify-supabase-sync` | #154 | July 31 temporary read-only history/dry-run verifier; merged to main, followed by removal PR #155 |
| `chore/remove-supabase-sync-verifier` | #155 | July 31 cleanup of that temporary workflow, already merged to main |
| `chore/verify-ticket-counter-production` | #159 | July 31 temporary read-only ticket-counter proof workflow, already merged to main |
| `chore/guarded-branch-cleanup-fourth-wave-20261015` | #585 | Fourth-wave owner cleanup already executed successfully on merged main, no longer a working change branch |

All four were confirmed on GitHub as **closed and merged into main with exact current head SHA unchanged** and no open PR references. These are retired branch refs only; the merged commits, PR records and migration/test history stay in GitHub. The script independently re-verifies before deletion.

A direct Git comparison shows some historical squash-merged verifier heads remain graph-diverged from main. Their exact GitHub merged-to-main PR record, frozen SHA and owner preview are therefore the evidence used for these four, not a false claim of Git ancestry.

**Explicit retention:** `aaa-cinema-pre-pregateway-bind`, `main`, backups, archive/recovery/reconcile/integration refs, current Discloud cutover and migrations, 11 closed-unmerged product PR branches, 15 modified-after-merge heads and no-PR diverged branches. Among remaining merged PR refs, schema migrations, AntiNuke recovery, Live Captions rollback, Invite Shield repair and critical Discloud recovery are retained for now pending operator acceptance. Two clean no-PR refs are already ancestors (`audit-000-cinema-pr436-known-good` and `fix/discloud-rest-window-recovery-current-main-20261007`) but are purposefully held as named known-good/recovery checkpoints.

**Gate and next action:** After fifth-wave PR exact-head CI is green, merge into `main`. Owner dispatches `preview-reviewed` to see exactly four candidates and zero deletions, inspects the list, and only then separately dispatches `delete-reviewed` with `DELETE_REVIEWED_HISTORICAL_BRANCHES`. Expect **132** branches after deleting four from the 135 checkpoint plus this PR branch (assuming no other branches are created). Verify current inventory and protected refs. No automatic cleanup, bot deploy, Discloud changes or database writes.


## October 16 sixth-wave checkpoint: expose main in Discloud's limited branch picker

- Fresh GitHub listing: **141 branches**, with `main` at **alphabetical position 120** (1-based) and no open pull requests.
- Before writing this sixth wave, every one of the 29 proposed PR branch refs was confirmed to match its merged PR's **exact head SHA**. All merged-to-main PRs have `main` as their base.
- Five extra older PRs (#499, #501, #504, #511, #528) originally merged into the still-linked `aaa-cinema-pre-pregateway-bind`. Each branch was independently verified as a **zero-ahead ancestor of BOTH** the linked production branch and current `main`. Both containing branch refs remain protected from cleanup.
- This owner-reviewed wave adds **24 merged-to-main** and **five dual-ancestor production PR** exact name/SHA records to the existing manual workflow. Prior 30 records stay frozen. Only 29 presently eligible refs were added.
- Of these 29, **23 sort alphabetically before main**. If no other branches change and the new sixth-wave PR branch is retained, deleting all 29 would bring the total to approximately **113**, with `main` near position **98**. Discloud's own picker behavior is not guaranteed; this should improve visibility if it truncates at 100.
- The latest AntiNuke, Cinema, member, ticket, and Toke fixes are all already committed in `main`; deleting an unchanged PR branch does not roll back the merged code. The GitHub PR number and exact head SHA remain recorded in the reviewed manifest and GitHub PR records.

### Retention and no-go list

Preserve `main`, `aaa-cinema-pre-pregateway-bind`, all backups, recovery/archive/integration/reconcile refs, open PR branches, unmerged/changed-head branches, and ongoing Discloud/PostgreSQL migration implementation refs. Do not automatically delete any branch by name pattern or age. The fifth-wave audit's unfinished-work holds remain in force.

### One-time execution

After this PR's exact-head CI succeeds and it is merged, the repository owner uses **Actions → Guarded merged-branch cleanup**: run `preview-reviewed` and confirm the **29 named candidates** (older retired names should no longer exist). Separately run `delete-reviewed` with `DELETE_REVIEWED_HISTORICAL_BRANCHES`. The workflow rechecks merged PR evidence, exact head SHA, open PR usage, protected status, dual ancestry for the five historical production branches, and unchanged current head before deletion. Merging the PR does not delete anything. Verify the surviving `main` and linked production refs and actual Discloud picker afterward.


## October 16 branch reconciliation #263 (issue #597)

- The old unmerged PR #263 `fix/welcome-card-long-tail-unicode` is closed at pinned head `25047b33bd51f7f066992802ef466276ceee7056`, **17 ahead of current main** due to old diverged history. It is *not* merged wholesale.
- Merged PR #264 instead bundles `stoney_verify/assets/fonts/NotoSansCanadianAboriginal-VF.ttf` plus the SIL license and registers it in `unicode_font_fallback.py`. The tracked font's exact Git blob SHA `b20ba0705bfac6e3c1655ea854522c041bd00a69` was verified in GitHub and matches the pinned upstream face in #263.
- Existing main tests already check bundled UCAS coverage and whole-card rendering with `ᗩ ᗰ ᒪ`. A new regression protects the exact original live name `PᗩᗰƐᒪᗩ`, including fallbacks under disabled system/registry discovery; card-rendering sample coverage also includes that exact string.
- PR #263's additional downloading of 11MB+ Unifont resources and Discloud `BUILD` override were *not* carried over. The license-covered bundled Noto already fixes the verified UCAS bug without adding build-time external network dependence or overwriting modern `discloud.config` (RAM/VLAN/ffmpeg).
- This is **not** a claim of all-script Unicode font coverage or deployed production validation. Any separately proven missing glyphs remain a new scoped bug, not grounds to merge the obsolete #263 wholesale.
- Seventh-wave safe retirement adds one exact-name/head-SHA `superseded` record: closed-unmerged #263, replacement merged #264. The existing guarded workflow will recheck both PR records, branch protection, current head SHA, and open PR references. Deletion is **manual after merge and preview**.
- Until the owner explicitly runs the verified delete workflow, the old branch remains intact; `main`, `aaa-cinema-pre-pregateway-bind`, backups, recovery, migration, and all other open/reconciliation branches remain untouched.


## October 16 eighth-wave exact-head cleanup: merged PRs #596 and #598

- Current GitHub inventory after owner-run sixth-wave retirement: **114 branches**, with `main` at alphabetical position **98**; **zero open PRs**. GitHub still holds the previous seventh-wave candidate `fix/welcome-card-long-tail-unicode` (closed-unmerged PR #263, exact unchanged head) because merging #598 alone does not delete it.
- Verified **unchanged, unprotected, merged-to-main** PR heads for two completed reconciliation branches:
  - PR #596 `chore/guarded-branch-cleanup-sixth-wave-20261015` at `84bf0510ff971f8708c56328b5eaec62734bbbfb` (the sixth-wave cleanup code is already merged; its branch is no longer the source of truth).
  - PR #598 `chore/reconcile-unicode-263-20261016` at `48662fdb88addadb5ace05606fda23113976c328` (the original Unicode regression and safe cleanup guard are merged; retiring the work branch does not affect the bundled font).
- The **same existing owner-only** reviewed cleanup manifest gains exactly these two frozen entries. It must recheck merged PR identity, exact branch SHA, protection, and open PR use. No new deletion path, no runtime/bot/DB edits.
- The old PR #9 `feature/setup-picker-wizard` remains on **hold**: the legacy `/stoney` picker and current `/dank` picker have the same 18 saved configuration keys and corresponding validation/interaction sections, and initial picker commits `1d3551d96d6e5071f5ea8b0945d4df04698e499a` and `70243f7ac52c9d648c2c98964bd242dbe98e801e` are ancestors of `main`. However the original draft branch is diverged, and an individually verified retirement record has not yet been admitted. Do not merge or delete it in this slice.
- **Next gate:** after this draft PR passes exact-head CI and merges, owner runs **Actions → Guarded merged-branch cleanup → preview-reviewed** and inspects the expected *up to three* new eligible candidates (#263, #596, #598). Then separately run **delete-reviewed** with `DELETE_REVIEWED_HISTORICAL_BRANCHES`. Expect 112 branches if this PR contributes one new branch and the three eligible candidates are all retired with no concurrent changes. `main`, the linked `aaa-cinema-pre-pregateway-bind`, backups, migrations, and unfinished heads remain preserved. Verify the actual Discloud branch picker after deletion; branch reduction alone does not prove `main` is selectable.


## October 16 ninth-wave reconciliation: closed #9 setup picker + merged #599

- Latest GitHub inventory: **112** refs, `main` 97th alphabetically, no open PRs. Owner's previous `preview-reviewed` / `delete-reviewed` successfully retired exactly three previously frozen refs (#263, #596, #598). The existing linked production branch and named recovery/backup/migration refs survived; Discloud dashboard `main` selection **has not been verified**.
- **Closed-unmerged #9**, `feature/setup-picker-wizard`, is unchanged at `a79ec6441a66de41bdab5110c5ebac2a9ab0fd81` (unprotected). Its two unique old-branch commits only introduce the `/stoney setup-picker` module/registration, with one added picker file and one `commands_ext/__init__.py` change. The older picker is a 663-line legacy counterpart to current main's 533-line `/dank setup-picker`; both carry the same 18 persisted fields, four save flows for tickets/verification/VC/logs, native role/channel choices, owner interaction lock, validation through `_validate_ticket_setup`, `_validate_verify_setup`, `_validate_log_setup`, and canonical upsert/invalidations. Existing current `public_setup_solid.py` owns the primary guided `/dank setup` flows; `commands_ext/__init__.py` registers the legacy-shaped picker as an explicit admin fallback, rather than indiscriminately exposing extra old commands on the public profile. Merged #200 hardened the current setup execution lifecycle; merged #241 replaces public setup's native picker discovery with the shared Dank resource browser. The old PR body explicitly calls itself superseded by `main`. Its old code must not be merged or reintroduced. This is **source and test-surface equivalence**, not a claim of new live Android production testing.
- **Merged #599** retained its exact unprotected head `16a7292cecfb8ff73aab2ab50c108287280da940` on `chore/guarded-branch-cleanup-eighth-wave-20261016`. All of that cleanup work is already on `main`; no pending PR references it.
- Adds **two** exact frozen reviewed-cleanup records: #9 as `superseded` by the merged #241 public setup owner and #599 as an exact-head `merged` PR. The existing owner-only workflow rechecks original closed PR #9 and its SHA, replacement merged #241, exact merged-to-main #599, branch protection, open PR use, and unchanged branch head before any deletion. Changes are only workflow manifest, focused tests and this audit; no bot runtime, database, Discloud settings, or auto-delete code.
- Gate: draft PR must pass exact-head CI and the diff review before owner marks ready and merges. Then owner runs **Actions → Guarded merged-branch cleanup → `preview-reviewed`**, confirms *exactly these two* surviving candidates, then separately `delete-reviewed` with `DELETE_REVIEWED_HISTORICAL_BRANCHES`. If no other refs change, adding this PR branch and retiring two existing names should leave **111** branches. Verify `main`, Discloud's linked ref, and selector visibility independently; never delete/recreate the app.


## October 16 tenth-wave archive-before-retire consolidation

- GitHub inventory: 111 branches; `main` = `4c79c224e6ecffc277e02324dee6141ad7b925a5`, no open PRs. Even after verified owner cleanup from 517 branches, the owner still cannot select `main` in Discloud. Discloud picker ordering and limits remain unverified; do not require provider intervention or delete the existing app.
- Audited 42 currently unchanged exact merged-to-main PR heads; 37 become SHA+PR-pinned reviewed candidates, while 5 explicitly held migration/recovery exceptions remain excluded. An additional 6 no-PR historical heads were separately confirmed as ancestors of `main` (zero commits ahead) and are SHA-pinned as `ancestor-only`. This is 43 total new candidates, no unique/unmerged branch heads.
- **Tag preservation before each deletion:** `refs/tags/branch-retirement-20261016/<branch-name>` is created at the exact candidate SHA (or reused only if it already matches), reread, and verified as a commit with matching SHA. Any tagging/read-back failure aborts that particular branch deletion, leaving the branch untouched. Tags preserve the original commit and branch recovery information outside Discloud's branch list; branch contents can be restored into a new branch from that tag.
- Current `main`, still-linked `aaa-cinema-pre-pregateway-bind`, `backup/*`, `recovery/*`, all unmerged/unique-commit branches and the 5 explicitly held merged migration checkpoints stay intact. No production code/database/Discloud changes or deletions on PR merge.
- Manual gate: verify **exact-head CI** for this PR, owner merge, run `preview-reviewed` first, inspect 43 new candidates and zero deletes, then separately `delete-reviewed` with `DELETE_REVIEWED_HISTORICAL_BRANCHES`. GitHub Actions creates/verifies a lightweight tag before each actual delete. With one added PR branch and 43 successful retirements, expected count is 111 + 1 - 43 = **69**; verify live inventory and archived tags, then separately test the Discloud branch picker. It is not established that any threshold will make `main` visible.


## October 16 eleventh-wave master-audit completed heads (issue #597, master #602)

- **Observed baseline:** 72 GitHub branches at `main@26f71ab32d5304795ee6d44a8f1e6b3acac7480c`, main listed alphabetically #66; no open PRs. This includes three successfully merged and exact-head-green master-audit PR branches (#603, #604, #605), and the merged tag-first cleanup PR branch #601. All four heads are unchanged, unprotected, and match the merged GitHub PR head SHA into `main`.
- Add exactly **four** SHA+PR-pinned, owner-reviewed `merged` records. Reuse the existing manually gated `preview-reviewed` → `delete-reviewed` mode, with exact confirmation `DELETE_REVIEWED_HISTORICAL_BRANCHES`, open-PR/protection/unchanged-SHA checks and archive-tag creation/read-back before each deletion. Do not broad-match prefix patterns or auto-delete every merged PR branch.
- The prior **43** tag-first archives were independently verified. Each of the four newly retired refs must get its own `refs/tags/branch-retirement-20261016/<name>` at the exact former branch SHA before the corresponding `refs/heads/<name>` removal. GitHub PR records and merged code on main remain available. Failure to tag/verify must leave the branch in place.
- **Explicit hold:** the Discloud-linked `aaa-cinema-pre-pregateway-bind`, `main`, 9 named `backup/*` branches, all snapshot and `recovery/*` refs, five historical merged-but-release-sensitive schema/migration/recovery branches, and unique/ahead/unmerged histories stay active until separate feature equivalence and recovery obligations are proven. A PR head matching a *different* merged commit is not automatically redundant; 20+ manually compared historical branches have extra commits ahead of main. No forced stale merge.
- **Projected branch count:** 72 before new cleanup PR; 73 with its own development branch; owner retirement of four unchanged archived refs returns to **69**. Current cleanup PR branch must itself later be retired or managed through a separately reviewed branch-lifecycle policy. `main` cannot safely be the *only repository ref* while Discloud still points at another branch; goal is one *active development main*, not deletion of the deployed source or rollback ability. No Discloud app, database or config operations.
- **Next reconciliation:** compare each remaining unique-ahead branch's exact file diff against current authoritative source and CI tests, decide to port a needed feature onto fresh main or archive it as explicitly superseded. Prefer owner-reviewed batches only after proof. Do not ask Discloud to fix repository branch debt.


## October 16 twelfth-wave reconciliation: obsolete Stoney launch check

- **Fresh verified baseline**: `main@6101f34286c8a7d61be3e50663b77505af955500`; **70 GitHub branch refs**, no open PRs. The two completed review heads (#606 at `55c56012680fe9f73f655f0d0583b36bcf631612` and #607 at `773e0a5f4eddc936eba660de821e4fe2b09d4a71`) are unchanged, unprotected, and exactly match their merged-to-main PR head commits.
- **New third eligible record**: `claude/readiness-audit-dank-branding@375a4dbcbc1604f3b23d09d33ab5f9fa8ef9eb4c`, a historical June 2026 branch with **no PR**. GitHub commit inspection shows a single nonmerge commit changing exactly one file, `tools/audit_public_launch_readiness.py`: it converted nine old `STONEY_*` required environment values into `DANK_*`, and removed the old `/stoney` banned-entry check. Those changes were superseded by merged #607, which touched that same script but now derives slash commands and /dank children from the canonical public contract, requires `DANK_*` env keys, and has tests rejecting obsolete commands/missing env. Do NOT merge the original June branch because it still hardcodes deleted `/ticket-panel` and `/ticket-category` roots. The original full commit is recoverable via its exact lightweight retirement tag.
- For the one `superseded-no-pr` exception, the existing owner-only workflow now **rechecks** no associated closed PRs, unchanged pinned old SHA, original single-parent/single-modified-file commit, merged replacement #607 with an exact pinned head SHA into main, and actual matching modified file in the replacement PR. All normal protection/open-PR/head/archived-tag safeguards still apply. No generic new delete rules for other ahead/diverged branches.
- **Hold** `audit/protection-picker-migration`. It only edits `ACTIVE_TASK.md`, but that document contains a task plan for uncompleted native Protection picker ownership; its feature still needs acceptance and must not be mistaken for a finished code migration. Keep the live Discloud-linked `aaa-cinema-pre-pregateway-bind`, all nine `backup/*`, recovery refs, five schema/release-sensitive merged heads, and unique-feature histories.
- Owner gate: merge the narrowly reviewed PR only after exact-head CI green. On `main`, manually run **`preview-reviewed`** expecting **three** eligible and zero deletions, then **`delete-reviewed`** only with exact `DELETE_REVIEWED_HISTORICAL_BRANCHES`. The script tags and verifies each original SHA before retiring the branch. With 70 baseline refs + one new PR branch - three reviewed retired refs, expect **68** remaining if no concurrent branch changes. This does **not** rebind or restart Discloud and does not mean one literal Git branch yet.


## October 16: automatically close the loop on newly merged temporary PR heads

**Problem:** The reviewed historical cleanup workflow is intentionally manual (archive, preview, owner-confirm, delete). It has preserved 50 Git commit recovery tags while reducing a peak of 517 branches to 68, but each new merged PR left another ephemeral branch requiring an owner to dispatch two separate cleanup jobs. At this checkpoint 70 branches remain after merged #609 and #610, plus the active P0 #611 PR branch when created, and both merged PR heads still exist. **Seven new AntiNuke regression tests are seven test functions/files within one PR branch, not seven branches.**

**Fix:** Add a separate `auto-archive-merged-pr-branches.yml` workflow triggered on `push` to `main`. It will retire only recent (`merged_at >= 2026-10-15`) **exact merged-to-main** temporary PR heads in `audit/`, `hotfix/`, `chore/`. Before deleting, it checks the same repository, correct default branch, PR merge time, original head SHA exactly equal to current ref SHA, no open PR using the branch, no protection, and a small capped batch. It first creates or verifies a lightweight `branch-retirement-auto/<name>` Git tag at that exact SHA. It re-fetches branch and open PR state immediately before deletion; a mismatch holds the branch. Only the exact archived branch ref is deleted. A failed archive leaves the branch intact. A manually audited exception allows the automation PR itself to be retired after its owner-reviewed merge.

**Holds:** Never auto-delete Discloud's known linked `aaa-cinema-pre-pregateway-bind`, any `backup/`, `recovery/`, `archive/`, `integration/`, deployment, staging, database/schema-migration/release or other sensitive names, protected refs, or anything with unmerged/unknown/head-changed commits. Especially hold `audit/protection-picker-migration` pending its wider feature-acceptance proof. `fix/`, `feat/`, and `feature/` are deliberately excluded from this immediate automatic policy, since production/recovery-sensitive histories exist there. The previous owner-confirmed historical cleanup workflow remains **manual** and retains its fixed manifest. No Discord runtime, app deployment, Supabase/Discloud code, data, or guild settings changed.

**Operational limitation:** This automation becomes effective only after its PR is successfully reviewed and merged into `main`, and if GitHub Actions runs with sufficient `contents: write` permission. Its own first main push can reconcile recent completed #609/#610 branch heads; #611 remains open and therefore ineligible. Verify the workflow run summary and new exact-SHA tags before reporting a smaller live branch count. GitHub source merge and branch lifecycle do **not** rebind the live Discloud app to main. The long-term goal is **one active development branch**, but preserving linked deployment and needed recoverability is nonnegotiable until a verified cutover.


## 2026-10-31 consolidation: single automatic deletion authority

The original separate `auto-archive-merged-pr-branches.yml` workflow is retired in favor of `retire-merged-temporary-branches.yml`. Both previously ran on pushes to `main` and could delete overlapping audit/chore branches. More importantly, the former did not require confirmation of the external Discloud deployment branch. The consolidated workflow retains its push and five-minute schedule, serialized concurrency, archive-first exact-SHA checks, zero-unique-commit proof, merged-PR evidence, active-PR checks, and bounded historical batches. The former hotfix namespace is now included, but only under these stronger guards.

**Deletion gate:** The historical automatic path remains archive-only until `DANK_DISCLOUD_DEPLOY_BRANCH` identifies the *independently verified actual* Discloud deployment branch and resolves to an existing repository ref. A plausible name or simply setting the variable to `main` is not proof of what Discloud runs. No branch may be deleted merely to meet a count target. Manual historical cleanup remains owner-reviewed.

**Run evidence before consolidation:** `main@78bacb0b23d533f7112ad5d7770871fc231cd2ba` passed full CI, but cleanup run 41199763254 reported an empty deployment variable and archive-only operation. The live repository held 107 branch refs at inspection; the planned consolidation itself does not authorize any deletion. Supabase migration workflow run 41200915989 failed at read-only `supabase migration fetch --linked` with PostgreSQL `28P01`; database credentials and deployment are out of scope for this branch-retirement change.
