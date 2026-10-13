# Dank Shield: single-process sharding canary (2026-10-13)

Scope: existing `DankAutoShardedBot` plus bounded guild-maintenance loops. This
does **not** introduce multiple bot processes, Redis, extra identities, or a
new Discord command-sync owner.

## Preflight (no production flip until these are satisfied)

1. Verify the **separate** member-activity batching migration from merged PR
   #540 on the Dank Shield Supabase project. A merged Python change does not
   guarantee a deployed SQL function. Activity write failures must remain
   fail-closed; do not use the unrelated Supabase project.
2. Check exact-head CI on the sharding PR, and verify the four new workers
   are observable by log: `activity heartbeat cycle`, `metrics sync cycle`,
   `ticket automation cycle`, `startup settings guilds_total`.
3. Record initial Discloud RSS/peak RAM, CPU, gateway latency, shard count,
   unavailable guild count, command sync count, Supabase request/error rates,
   and Discord REST budget warnings, before changing any environment setting.
4. Only **one active production deployment for the same bot token**. Do not
   run a second competing bot against production under the guise of canary.

## Canary configuration (Discloud environment variables)

```env
DISCORD_AUTO_SHARD=true
DISCORD_SHARD_COUNT=2
DANK_HEARTBEAT_GUILD_CONCURRENCY=8
DANK_METRICS_GUILD_CONCURRENCY=4
DANK_TICKET_SWEEP_GUILD_CONCURRENCY=3
DANK_SPAM_WARM_GUILD_CONCURRENCY=3
```

Do not put Discord IDs, tokens, or Supabase credentials into committed files.
The 2-shard pin is temporary; one Discord process owns both shard connections.
Record the bot's **actual** guild distribution (some shards may have no guilds
in small canaries). The shard factory already exists in
`stoney_verify/command_runtime.py`; no new class is necessary.

## Acceptance

- Logs confirm `DankAutoShardedBot`, both shard connections READY, one
  aggregate `ready` startup execution, one global command sync, and
  persistent views functioning across guilds on either shard.
- At projected guild count, the heartbeat `elapsed_s` remains under 60
  seconds for repeated cycles, including DB-tail latency; no cycle overlap.
  If it overruns, report that result; do not claim the target was achieved.
- `metrics sync cycle` completes with bounded concurrency and accurate
  success/failure counts. Staff/role data persists as before.
- Ticket sweeps do not double-send reminders or auto-close a ticket twice
  during a manual pass; Discord REST budget and latency stay healthy.
- SpamGuard startup warming no longer delays `on_ready`; saved disabled and
  enabled settings continue to enforce correctly on the first message.
  Repeated database failures stop the warm sweep instead of generating
  thousands of startup requests.
- Simulate transient Supabase outages (isolated staging only): heartbeat
  coverage fails closed, no cleanup action is authorized on missing evidence,
  and the work loops recover without a request storm.
- Record RSS RAM per 1,000 guilds at steady state and during startup (actual
  measured values, not estimates). Compare CPU, gateway latency and
  Discloud shutdown/rate-limit events to baseline.

## Auto shard-count rollout

After passing the two-shard canary, **remove**
`DISCORD_SHARD_COUNT` entirely (not `0` or an empty literal). Leave
`DISCORD_AUTO_SHARD=true`, restart one instance during a maintenance window,
and verify `GET /gateway/bot` recommendation through the library's selected
shard count and readiness logs. Discord caps each gateway shard at 2,500
guilds; recommendation and IDENTIFY concurrency are Discord-controlled.

## Rollback

If command sync duplicates, guilds become inaccessible, Discloud reaches its
REST/RAM limit, or coverage unexpectedly becomes actionable after an outage,
restore the prior environment settings, restart **only one** bot instance, and
inspect the saved logs. Keep member inactivity actions fail-closed until
durable history/reconciliation is verified. Do not silently increase
concurrency or reset Supabase state to hide a failing canary.

No canary has been executed by this PR. Live Discloud access and production
Supabase migration acceptance must be supplied by the operator.
