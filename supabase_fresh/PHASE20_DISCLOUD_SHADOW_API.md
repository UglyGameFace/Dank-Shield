# Phase 20: opt-in Discloud-origin candidate REST connectivity probe

**Status: source production stays active. No deployment, Discord canary, or Supabase credential cutover authorized.**

## Why this is necessary

The dedicated Supabase GitHub read-only PostgreSQL preflight checks project identity, all 75 public table permissions, eight required RPCs, and execution queues. It is a database contract check **from GitHub Actions**, not proof the existing Discloud bot can connect to the new Supabase REST API.

Dank Shield's runtime uses `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` as *process-wide* credentials. Replacing those values would immediately redirect all guild operations and background workers. It is unsafe to call that a one-guild canary.

## Independent opt-in shadow probe

PR code introduces `stoney_verify/candidate_supabase_shadow_probe.py`, called once after Discord ready only if `DANK_CANDIDATE_REST_SHADOW_PROBE=true`. It is scheduled on an async background task, bounded by a 30-second wait, and never holds the gateway event loop.

It refuses to run unless the **existing** `SUPABASE_URL` host is exactly `hyncpbvqyjojxhjzcdzg.supabase.co` with a nonempty original service key. It independently validates the new candidate `DANK_CANDIDATE_SUPABASE_URL` host is exactly `https://viamriafkbqhquvrsnua.supabase.co`, requires a distinct candidate service-role key, and uses its **own** Supabase client. The live bot's `globals.get_supabase()`, environment variables, connection pool, guild configurations and worker queues stay untouched.

Five tiny REST `SELECT ... LIMIT 1` probes test actual candidate table access from within Discloud: `guild_configs`, `tickets`, `member_activity_ledger`, `staff_action_logs`, and `activity_feed_events`. Empty tables are acceptable. All failures are reported by fixed text with no HTTP raw errors, SQL, response data, Discord IDs, keys or DSNs. This does not invoke an RPC, write data, or use the new DB for guild operations.

## Deliberate, **separate** owner steps for runtime test

**DO NOT deploy/restart the Discloud bot merely because PR CI passes.** The production app `stoneyverify` has a separate existing-app deploy/backup safety gate documented in `docs/DISCLOUD_EXISTING_APP_MAIN_CUTOVER.md`, including host-file preservation and environment recovery. A code commit may restart the bot, so preserve Discloud's off-host application backup and environment values privately before applying an owner-authorized release.

After merging PR and approving a guarded production code release, set these *additional* variables in the existing Discloud bot's private environment (not GitHub PR inputs):

```env
# Keep SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY UNCHANGED!
DANK_CANDIDATE_REST_SHADOW_PROBE=true
DANK_CANDIDATE_SUPABASE_URL=https://viamriafkbqhquvrsnua.supabase.co
DANK_CANDIDATE_SUPABASE_SERVICE_ROLE_KEY=<privately retrieved service-role key for new project>
```

Never paste the key into ChatGPT, GitHub logs, Discord or the repository. After the guarded app update/restart, check private Discloud app logs for:

- `DANK_CANDIDATE_REST_SHADOW_PROBE: pass checks=5 production_switched=false source_writes=0 candidate_writes=0` means candidate REST/auth/permissions *from Discloud* worked for these five reads. It does **not** authorize a live guild canary or switching any existing keys.
- `DANK_CANDIDATE_REST_SHADOW_PROBE_FAILED: <fixed code>` means the shadow check did not pass. The production source connection remains unchanged; investigate its fixed code.
- No message means the toggle was not enabled, the updated code isn't deployed, or startup wasn't reached.

Disable `DANK_CANDIDATE_REST_SHADOW_PROBE` and remove the *secondary* candidate key from Discloud when finished if not needed. Keep original `SUPABASE_*` untouched until explicit owner-authorized cutover.

## Still before 100%

A passing shadow probe only proves **read-only** Supabase REST reachability and candidate key access from the current Discloud process. It does not prove actual writes, Discord command behavior, webhook and realtime integrations, the 34-table hash parity while writers are frozen, monitored one-guild canary, rollback restoration, runtime resource capacity or a production switch. A single guild cannot be canaried by rotating process-wide `SUPABASE_URL`.

We still need a controlled writer pause with fresh final parity, a carefully isolated/reversible functional canary with owner approval, validated app/environment backup and rollback, then separate owner approval to rotate the active credentials.

**This PR only prepares the diagnostics. Production and both databases remain unchanged.**
