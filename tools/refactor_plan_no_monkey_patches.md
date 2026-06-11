# No-monkey-patch refactor plan

This file should not stay long-term. It documents the active branch strategy for removing role-truth startup bridge behavior.

Target end state:

- `stoney_verify.role_truth` owns per-guild verification/member role truth.
- `stoney_verify.members_new.service` imports `role_truth` directly.
- `stoney_verify.members_new.sync_service` imports `role_truth` directly.
- `stoney_verify.events` imports `role_truth` directly.
- `stoney_verify.startup_guards.per_guild_role_truth_guard` is deleted.
- `stoney_verify.startup_guards.__init__` no longer loads the deleted bridge.
- CI fails if global deployment role IDs are used as role-truth source in member/event paths.
