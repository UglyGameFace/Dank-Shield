# ACTIVE TASK

## SG-STATS-001 — Live Discord SpamGuard stats channels

**Status:** IN PROGRESS

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
- Runtime hooks into Spam Guard, invite policy, and `/dank protection` are pending application.

### Validation
- Targeted tests: PENDING
- Full unit suite: PENDING
- Python compile check: PENDING
- Standalone `tools/test_*.py`: PENDING
- Public/setup/safety/role/event audits: PENDING
- Conflict inspection: PENDING

### Cleanup
- Temporary patch/workflow files: NOT YET CREATED
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
- [ ] Runtime event hooks applied
- [ ] `/dank protection` activation UI applied
- [ ] Targeted tests pass
- [ ] Full regression suite passes
- [ ] Compile/static validation passes
- [ ] Cleanup complete
- [ ] Conflict inspection complete
