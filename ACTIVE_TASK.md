# ACTIVE TASK

## SG-STATS-001 — Live Discord SpamGuard stats channels

**Status:** IN PROGRESS — behavioral test cleanup
**Merged feature PR:** #97

### Scope
Create a real Discord server-stats display for Dank Shield using locked voice channels, not an image or fake counters.

Visible counters:
- `🛡️ SpamGuard: ONLINE/OFFLINE`
- `🚫 Spam Blocked: <count>`
- `🔗 Invites Blocked: <count>`
- `⏱️ Timeouts Issued: <count>`
- `🔒 Quarantined: <count>`

Only actions Dank Shield can actually prove happened may increment a counter.

### Findings
- Spam Guard already returns the actual number of messages successfully deleted during cleanup.
- Spam Guard action results distinguish successful timeout and quarantine actions.
- `invite_policy_engine.delete_message_if_allowed()` is the authoritative successful Discord-invite deletion path.
- Per-guild `guild_config` storage can persist nested stats/display metadata without adding a new database table.
- `/dank protection` is the correct existing owner-facing surface; no new top-level slash command is needed.
- Channel-name refreshes must be throttled instead of renaming a channel for every moderation event.
- Repository guardrails require behavioral tests rather than new source-shape assertions; the feature's two source-text checks are being replaced with executable behavior tests.

### Changes
- Added native `stoney_verify/security_stats.py` service.
- Added compact counter formatting and guild-scoped durable counters.
- Added locked voice-channel display creation/repair and a 10-minute refresh loop.
- Runtime hooks are applied to Spam Guard, the authoritative invite-delete path, and `/dank protection`.
- Replaced feature-added source-shape assertions with behavioral persistence and locked-channel creation tests.

### Validation
- Behavioral cleanup targeted tests: PENDING
- Full unit suite rerun: PENDING
- Python compile check rerun: PENDING
- Standalone `tools/test_*.py` rerun: PENDING
- Public/setup/safety/role/event audits rerun: PENDING
- Conflict inspection rerun: PENDING

### Cleanup
- Temporary patch/workflow files: removed
- Redundant implementations: none added
- Startup-guard monkey patches: none added
- Feature-added source-shape checks: removal implemented, validation pending

### Blockers
None currently.

### Backlog
- No separate backlog item accepted while this task is active.

### Definition of Done
- [x] Root cause/execution paths inspected
- [x] Native central service selected
- [x] Guild-scoped persistence designed
- [x] Runtime event hooks applied
- [x] `/dank protection` activation UI applied
- [ ] Behavioral test cleanup passes
- [ ] Full regression suite passes after cleanup
- [ ] Compile/static validation passes after cleanup
- [ ] Cleanup complete
- [ ] Conflict inspection complete
