# Resource-pressure stabilization (2026-11-16)

Scope: memory retention and CPU pressure on the single 1,000 MB Discloud process.

- Bounded Discord join timestamp history to the most recent 4,096 joins per guild and prune entries outside the configured raid window when evaluating bursts. Counts beyond this cap are intentionally capped for anti-raid decisions, not treated as zero.
- Member join processing locks are weakly held. An active or waiting coroutine retains a strong reference while using a lock; idle locks are collected.
- Recent invite/join context is bounded to 2,048 entries and 180 seconds; member leave explicitly releases it.
- Hard-identity proof cache is bounded to 2,048 records with TTL eviction; recent risk profiles and per-name alert timestamps are also bounded.
- Role/staff metric writes retain their existing signatures and schedules. Role member counts are computed from the cached guild members in one pass rather than scanning all members per role.
- Profile PNG cache retains at most 24 MiB in addition to the existing item and TTL limits.
- Share Router yt-dlp extraction adds a thread-lifetime semaphore and 12-request admission cap. A timed-out asynchronous caller cannot allow unlimited continuing extractor threads.
- Full-member synchronization reuses its existing authoritative tuple without making another full-guild list.

No new daemon, deletion of persistent member evidence, changes to moderation policy, database migration, change to Discloud RAM, or production deploy. The 1,000 MB figure is a configured limit, not a measured RSS. Verify live memory/CPU/loop lag and real Discloud commit separately after gated release.

The process-health heartbeat now reports CPU consumption as a percentage of one CPU core, plus resource-pressure cardinalities (join windows, proof cache, recent context, activity buffer, inflight extraction, cached PNG bytes). This is read-only, imports no new feature modules, and supports before/after performance verification without private member data.

Optimization continuation: existing-member rows are prefetched in groups of 50 during full sync, with a one-failure fallback to the prior exact query and unchanged join/ticket proof reads. The authoritative Discord member snapshot now avoids its redundant list; activity flushing uses recent insertion order rather than sorting the entire buffer. Activity memory defaults to 10,000 distinct keys with a hard 50,000 ceiling; overflow is explicitly marked as lost coverage and inactivity cleanup fails closed.
