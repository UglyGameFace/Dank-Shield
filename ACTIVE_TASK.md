# ACTIVE TASK

## DS-CONFIG-HISTORY-002 — Understandable selective backup and restore

**Status:** FINAL HEAD VALIDATION PENDING
**Branch:** `feature/selective-config-backup-restore`
**PR:** #109, stacked on PR #108 until the localized-time fix merges

## Single Active Task Lock

Do not switch to unrelated implementation work until this task reaches Definition of Done or the owner explicitly force-switches tasks.

## Owner Request

Improve Backups & History so server owners can understand:

- what a backup contains;
- what a backup is useful for;
- what is not backed up;
- which configuration area they want to back up;
- whether they want to restore only missing items, manually chosen items, or every difference;
- exactly what will change before confirmation.

The key recovery case is a server that already has newer working configuration and only needs missing settings or ticket choices from an older backup. Restore must not replace unrelated newer values.

## Architecture Rules

- No monkey patches.
- No startup guards.
- No dashboard work.
- No new public command family.
- Existing live configuration tables remain authoritative.
- Existing `guild_config_versions` snapshots remain the only history source.
- No new migration is required.
- Every restore creates a full pre-restore safety backup.
- Ticket-choice writes continue through the existing atomic PostgreSQL restore RPC.
- Restore cannot cross guild or active config-table boundaries.
- Merge requires explicit owner approval.

## Delivered Behavior

### Clear backup explanation

The Backups & History screen now explains:

- **Core Settings** save feature switches, timers/rules, protection configuration, setup choices, welcome/log settings, and saved Discord role/channel/category references.
- **Ticket Choices** save the member-facing ticket menu plus compatible category-specific form configuration.
- Backups do **not** save Discord messages, members, live ticket conversations, actual roles/channels/categories, files, or server ownership.
- Restore changes Dank Shield's saved configuration only; it does not clone, delete, or rebuild the Discord server.

### Manual backup choice

Owners press **Choose Backup Contents** and select:

- Core Settings;
- Ticket Choices;
- or both.

This selection is intentionally domain-level. A backup remains a coherent snapshot of the chosen active configuration area rather than an ambiguous partial row. Fine-grained control is provided during restore, where it prevents unwanted overwrites.

### Selective restore modes

Each version explains what it contains, summarizes Core Settings sections or the Ticket Choices count, and compares the version with current configuration.

Owners can choose:

1. **Restore Missing Only**
   - restores saved items that are absent or blank now;
   - does not overwrite an existing configured value.
2. **Choose Exact Changes**
   - manually selects individual Core setting keys or ticket-choice slugs;
   - supports pagination beyond Discord's 25-option component limit;
   - selections persist while moving between pages.
3. **Restore All Differences**
   - restores every currently different item in that saved version.

All three paths open a separate preview showing the exact selected items. Only **Confirm Restore** performs a write.

### Native selective service

Added `stoney_verify/config_history_selective.py` as a substantive extension of the canonical history subsystem.

- Domain-scoped manual backup creation.
- Friendly Core setting labels and section grouping.
- Current-vs-snapshot restore planning.
- Missing-only item detection.
- Selected Core Settings merge:
  - applies only requested keys;
  - preserves unselected top-level and nested values;
  - adds restore audit metadata;
  - invalidates guild-config cache.
- Selected Ticket Choices reconciliation:
  - starts from the current complete category set;
  - replaces, adds, or removes only selected slugs according to the snapshot;
  - preserves every unselected current choice;
  - sends the resulting full target set through the existing atomic restore RPC;
  - writes one clean restored-state history entry.
- Every selective restore writes a complete pre-restore safety snapshot first.

## Behavioral Coverage

Service coverage proves:

- Core-only manual backup does not read or write Ticket Choices.
- Empty backup selection is refused.
- Restore planning identifies real differences and missing settings.
- Selected Core restore changes only the requested setting.
- Missing-only Core restore does not overwrite an existing value.
- Selected Ticket Choices restore preserves unselected current choices.
- Ticket reconciliation still uses the atomic RPC.
- Cross-guild snapshots are refused.

UI coverage proves:

- The screen explains what is and is not backed up.
- Backup-domain selection defaults to both but allows one domain.
- Only selected domains reach the backup service.
- Version details explain contents and all three restore modes.
- Missing-only and choose-exact buttons do not invoke restore directly.
- Exact-change selection is individual and paginated.
- Confirmation lists only items that will change.
- Only Confirm Restore invokes the selective restore service.
- Discord mobile rows remain compact.

## Validation

- Localization dependency PR #108 exact head `044b547b4c0ba1312ae01fabcaeae87d515f5fbc`:
  - Dank Shield CI run 495: SUCCESS.
- Initial selective backend/UI head:
  - compile passed;
  - CI run 498 exposed an underspecified fake ticket-state sequence in one behavioral fixture.
- Corrected selective head `012f215cefa88d782bca28c3b5601862d8d4c34f`:
  - Dank Shield CI run 499: SUCCESS;
  - compile, full unit suite, standalone checks, and all production audits passed.

## Cleanup / Conflict Inspection

- No migration added.
- No startup guard added or reactivated.
- No monkey patch added.
- No dashboard file changed.
- No public command added.
- Existing `config_history.py` remains the canonical full-history service.
- New selective logic does not create a second live configuration source.
- UI route remains `/dank setup` → More Options → Other Settings → Backups & History.

## Remaining Gate

This task-record commit is the final planned branch change. Its exact head must pass CI. Then:

- compare PR #109 against its stacked base;
- confirm it is not behind that base;
- inspect unresolved review threads;
- update the PR description with exact validation;
- mark PR #109 ready for review without merging.

PR #109 must remain stacked until PR #108 merges. After PR #108 merges, retarget PR #109 to `main` and run final exact-head validation against the new base before merge approval.

## Definition of Done

- [x] Backups screen explains Core Settings, Ticket Choices, intended use, and exclusions.
- [x] Owner can choose Core Settings, Ticket Choices, or both for manual backup.
- [x] Version detail explains exactly what the snapshot contains.
- [x] Missing-only restore preserves existing configured values.
- [x] Owner can manually choose individual settings or ticket choices to restore.
- [x] Exact-change selection supports pagination.
- [x] All-differences restore remains available.
- [x] Every restore shows an exact preview and requires separate confirmation.
- [x] Every restore creates a full safety backup first.
- [x] Selective Core restore preserves unselected newer values.
- [x] Selective Ticket restore preserves unselected current choices and remains atomic.
- [x] Cross-guild safety remains enforced.
- [x] Behavioral tests cover service and UI safety.
- [x] Implementation head passed full CI and production audits.
- [ ] This exact task-record head passes CI.
- [ ] Final stacked-base compare is clean and review threads are resolved.
- [ ] PR #109 retargets to `main` after PR #108 merges and passes the final merge-base gate.
- [ ] Merge requires explicit owner approval.
