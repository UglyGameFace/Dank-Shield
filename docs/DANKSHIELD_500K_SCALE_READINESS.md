# Dank Shield: prepare for 500,000 guilds (architecture, not certification)

**Scope / source of truth:** This is part of the sole active **dedicated Supabase migration**. The new candidate `viamriafkbqhquvrsnua` is offline. The shared old Supabase is still production. No cutover, Discloud env change, new parallel production bot, alternate provider, new Discord ID, or cross-feature rewrite is authorized.

## Verified starting point, Oct 20 2026

- Dedicated PostgreSQL 17 candidate: **34 tables**, 12 guild configs, 815 member records, 24 history restore points and ~16 MB allocated. Critically **not feature complete**: for example the existing `metrics_sync_worker` writes `staff_metrics`, absent from the candidate; Community Hub, Cinema, moderation and profiles still need feature schemas/RPC parity.
- Candidate settings when inspected: max_connections=60, shared_buffers=224 MB; a small candidate instance, **not 500K sizing**. Compute, PostgREST concurrency, poolers, egress, WAL growth and latency remain unbenchmarked.
- Default authoritative activity heartbeat = one guild RPC / 60 s, 180 s max gap; default metrics pass = each guild / 300 s. These are **per-guild work**, even with bounded worker concurrency. The 15-second activity ledger batch reduces HTTP calls, not database row writes.
- Existing token retention = 500 expired tokens per hour = 12,000/day maximum; once new arrivals exceed that level for sustained periods, cleanup backlog grows.
- Config history caps only **automatic** snapshots at 50 per guild/config type; intentional manual restore points are preserved. Staff audit, ticket evidence and actor reputation must never be blindly time-deleted.

### Arithmetic, not load-test results

At 500K guilds under default cadences: **500,000 heartbeat row writes/minute** (8,333/s, 720 million/day) and **500,000 metrics guild passes/5min** (1,667/s), before messages, tickets, role counts, moderation or media. The new candidate-only batch RPC accepts up to 250 IDs: at the hypothetical full fleet it drops heartbeat *network round trips* to ~2,000/min (~33/s), **but does not reduce the underlying 8,333 DB row writes/s**. No scaling throughput has been demonstrated.

Run the deterministic budget model with:
`python -m tools.dankshield_scale_capacity --guilds 500000`.

## Safe, incremental roadmap

**Gate A — complete the migration, before any switchover.** Restore source-valid feature schemas/RPCs and their privileges, late writes, strong rollback/restore, per-feature Discord acceptance and accurate client egress. Preserve authoritative tracker continuity and all security evidence.

**Gate B — replace per-guild polling.** Use tenant/shard-scoped **leased workers with database fencing tokens**, event-driven dirty queues and amortized batch operations. The prototype `heartbeat_member_activity_trackers` RPC is intentionally **not wired into the live runtime**. Before any runtime adoption, test exact continuity semantics under restarted processes, overlapping shard workers, stale messages, fail-closed inactivity decisions, server migrations and outages. A "shard heartbeat implies every guild is safe to clean" shortcut is forbidden: per-guild scope coverage can differ.

**Gate C — distributed gateway/worker fleet.** Discord recommends approximately one gateway shard per 1,000 guilds, so ~500 shards is *illustrative only*. Discord's `GET /gateway/bot` governs actual shard count, IDENTIFY rate/start limits, and at >150K the large-bot shard multiple. Never hard-code a 500-shard fleet. Run several independently supervised shard processes with a *single authoritative ownership map*; stagger reconnects, backpressure on outages and preserve one global command-sync owner. A single Discloud process is not an acceptable 500K target.

**Gate D — isolate costs and data.** Keep transactional control-plane data (config, roles, moderation, tickets) separate from high-volume event/analytics and CPU/bandwidth-heavy Cinema/Share Router/voice workloads. Limit unbounded JSONB snapshots, index by tenant/workflow/timestamp where query plans justify it, evaluate partitioned event data and archive with explicit guild-level policies. Partitioning/sharding by guild requires an actual multi-tenant key and measured hotspots; avoid speculative repartitioning or deleting historical evidence. Read replicas only alleviate eligible reads and have replication lag; they do not absorb primary writes. Pooling manages connections, not CPU or egress; service-role keys stay private.

**Gate E — measured promotion.** Exercise 1K -> 10K -> 100K -> 500K **synthetic guilds** in isolated testing, not by repeatedly hitting the production Discord API. Include uneven server sizes, cross-server adversarial joins, startup, 429s, source down, DB timeouts, partial worker failures and replay. Release gates: p95/p99 DB + Discord command latencies, per-role connection counts, sustained write TPS, WAL/IOPS and bloat, PostgREST bandwidth, response/HTTP bytes, memory and threads/shard, maintenance overrun, correctness of cleanup/leases, 24-hour soak and verified operator rollback. Resource tiers and infra bills are determined by these measurements, not invented upfront.

### Discord/platform references

- Discord Gateway sharding + large-bot requirements: https://discord.com/developers/docs/events/gateway
- Discord rate limits (dynamic, do not hard-code): https://discord.com/developers/docs/topics/rate-limits
- Supabase connection limits: https://supabase.com/docs/guides/database/connecting-to-postgres/pooling-and-limits
- Supabase replicas (reads only, asynchronous): https://supabase.com/docs/guides/platform/read-replicas

## Small-guild compatibility (5–30 servers)

The design does **not** switch on distributed infrastructure for small bots. A single gateway process, existing guild-scoped PostgREST client and guild-local workers remain the supported default; shards and external coordination must be enabled only through future explicit, measured rollout gates. The new batch RPC is optional and additive; legacy `heartbeat_member_activity_tracker` remains unchanged. Batches of **1, 5, and 30** are covered by rollback-only SQL tests, not padded to 250. The pure workload model also checks 5 and 30 guilds.

No production cutover or runtime batch activation is performed here. Existing guild activity continuity and fail-closed member-removal evidence must remain intact. The target is low baseline resource use plus horizontal growth later, not an unconditional claim of flawless performance.

## What this PR does and explicitly does not

**Does:** add a guarded, service-role-only, monotonic timestamp, bounded 250-guild heartbeat RPC to the *offline* candidate and rollback-only SQL regression covering 250 rows, process handoff, late heartbeat rejection, long gaps, input validation, and privilege boundaries. Provide source-controlled workload envelope model and 500K design plan.

**Does not:** activate the RPC in Python, replace per-guild continuity proofs, start a distributed fleet, provision machines, schedule migrations, declare 500K capacity, change existing heartbeat logic or delete any source data. The eventual performance target is **lower per-guild write amplification**, not merely a big batch size.

## Branch housekeeping and the single active task

**Current verified branch count (Oct 20 2026): 44 including main**. Historical owner-assisted cleanup has already reduced >500 branches. Keep `main`, the owner-reviewed historical cleanup workflow, backups, source staging, recovery and the possible Discloud-linked `aaa-cinema-pre-pregateway-bind` intact. Do not infer that unmerged, closed, no-PR, or changed-after-merge branches are disposable.

Use `work/dankshield-<short-slice>-YYYYMMDD` for **new temporary PR branches**. Existing `migration/dankshield-<short-slice>-YYYYMMDD` branches also opt into daily retirement. Do not create a second parallel branch while one Dank Shield task is active. Archive or preserve snapshots under non-opt-in prefixes, never `work/`. The daily cleanup in `.github/workflows/retire-merged-temporary-branches.yml` retires an opt-in branch *only* when:
- It belongs to this repository and is not a protected, deployment-linked or explicit reserve ref.
- Exactly one closed PR exists, was actually merged into `main`, and its original head SHA **equals the current branch SHA**.
- The ref is unchanged and is not used by an open PR; check both the branch and git ref immediately before deletion.
- At most 20 opt-in candidates are present. It never reaches into `feat/`, `fix/`, `backup/`, `archive/`, `aaa-*`, Aiven source branches or other older refs by guessing at names or age.

The daily job runs on GitHub `main`; the manual workflow button previews by default and permits owner-only deletion. GitHub repository variable `DANK_DISCLOUD_DEPLOY_BRANCH` may be set to the **verified** deployment branch to add an explicit protected ref, without any credentials. The previous guarded historical workflow remains separate and owner-confirmed.

If 75+ branches remain, the action fails with a visible alert, not a broad deletion. This threshold is a hygiene budget, not a safety justification to delete important branches. The weekly/historical review path is still needed for non-opt-in branches. A merge is not Discord/Discloud deployment acceptance: save any truly necessary pre-cutover checkpoints in permanent recovery refs before opting in. Keep commit/tag history and PR review record.
