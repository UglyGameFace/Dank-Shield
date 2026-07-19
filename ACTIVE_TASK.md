# ACTIVE TASK

## SG-STATS-001 — Live Discord SpamGuard stats channels

**Status:** COMPLETE — awaiting merge/deploy approval

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

### Changes
- Added native `stoney_verify/security_stats.py` service.
- Added compact counter formatting and guild-scoped durable counters.
- Added locked voice-channel display creation/repair and a 10-minute refresh loop.
- Added focused unit/static coverage in `tests/test_security_stats_channels.py`.
- Runtime hooks are applied to Spam Guard, the authoritative invite-delete path, and `/dank protection`.

### Validation
- Targeted tests: PASS
- Full unit suite: PASS
- Python compile check: PASS
- Standalone `tools/test_*.py`: PASS
- Public/setup/safety/role/event audits: PASS
- Conflict inspection: PASS

### Cleanup
- Temporary patch/workflow files: removed before final commit
- Redundant implementations: none added
- Startup-guard monkey patches: none added

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
- [x] Targeted tests pass
- [x] Full regression suite passes
- [x] Compile/static validation passes
- [x] Cleanup complete
- [x] Conflict inspection complete
