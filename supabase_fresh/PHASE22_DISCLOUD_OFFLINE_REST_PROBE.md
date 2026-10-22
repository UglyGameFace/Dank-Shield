# Discloud-origin candidate Supabase REST proof WITHOUT Discord startup

**Single active task:** Dank Shield Supabase production cutover. **No code
deployment, environment change or production switch is authorized by this PR.**

## Why a new mode is needed

The prior `DANK_CANDIDATE_REST_SHADOW_PROBE` runs only **after Discord ready**.
Starting the actual bot at this point would restart workers and modify the old
source database, invalidating the frozen 34-table parity audit. Discloud
documents an app console, but console availability for a deliberately stopped
app is not reliably established. Do not depend on it for cutover.

This change provides a special opt-in `main.py` entrypoint. It runs **before
any** process health, bot, event, AntiNuke, ticket, worker, Cinema or Discord
app imports. The process checks the five previously-reviewed candidate Supabase
REST endpoints exactly once, using a separate candidate `service_role` key,
and then remains alive **inert** so the existing Discloud app
`AUTORESTART=true` does not fall through into the bot startup path.

The original bot-wide `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` must
remain unchanged while running the probe. The independently pinned candidate
connection reads one tiny row at most from each of:

- `guild_configs`
- `tickets`
- `member_activity_ledger`
- `staff_action_logs`
- `activity_feed_events`

No SQL writes, RPC calls, Discord gateway, worker startup, credential swaps,
bot commands, or filesystem backups are performed. The checker prints
only fixed safe outcomes and never prints API keys, response data or record IDs.

## Owner-controlled use AFTER merging full CI, not before

1. Keep `stoneyverify` **stopped**. Verify the latest dedicated database
   encrypted backup was restored successfully in the isolated recovery drill,
   downloaded outside GitHub and its passphrase is kept separately.
2. Back up the existing **Discloud app itself**, including `data/`/SQLite and
   all dashboard-managed secret **values**, privately off Discloud. A database
   ZIP is NOT an app backup; a downloaded app ZIP alone is not a proven restore.
3. While still stopped, configure the following **additional** values in the
   private Discloud `stoneyverify` environment:

   ```env
   DANK_OFFLINE_CANDIDATE_REST_PROBE_ONLY=I_AUTHORIZE_OFFLINE_REST_PROBE_NO_DISCORD
   DANK_CANDIDATE_REST_SHADOW_PROBE=true
   DANK_CANDIDATE_SUPABASE_URL=https://viamriafkbqhquvrsnua.supabase.co
   DANK_CANDIDATE_SUPABASE_SERVICE_ROLE_KEY=<private new-project service-role key>
   ```

   **Do not change** existing source `SUPABASE_URL` or
   `SUPABASE_SERVICE_ROLE_KEY` and NEVER put any key in GitHub PR inputs,
   chat, screenshot or source. Do not manually start the existing, old
   deployed code: it may not yet know about the early startup gate.

4. Review that the actual Discloud dashboard saved the probe-only setting
   **before deployment**. Following normal owner safety approval, use the
   existing guarded **EXISTING APP CODE UPDATE** workflow on exact GitHub
   `main`, with its independent off-host Discloud-app backup and environment
   recovery requirements. It can restart `stoneyverify`; the new entrypoint
   must already have the probe-only environment flag in place before any
   restart. Do NOT use the obsolete dashboard-linked GitHub branch, delete
   the app, or use an alternate deployment path.

5. Read private Discloud logs. A success marker is exactly:
   `DANK_OFFLINE_REST_PROBE: pass checks=5 discord_started=false
   bot_workers_started=false source_writes=0 candidate_writes=0
   production_switched=false`.
   Any `DANK_OFFLINE_REST_PROBE_FAILED: <fixed_code>` means **NOT ready**.
   `DANK_OFFLINE_REST_PROBE_HOLD: discord_disabled=true
   awaiting_explicit_owner_stop` confirms the process is intentionally
   inert after either outcome (the website may not serve HTTP).
6. STOP `stoneyverify` again after reading the result. Do not remove the
   probe-only flag and restart the bot against the old source; that would
   invalidate the verified freeze and require a new Stage/audit. Before any
   approved final production start, the migration cutover plan must remove
   this opt-in and put the **new** production URL and secret key in place.
   Preserve old credentials and old source database privately for rollback.

### What success would prove

Exactly five candidate REST SELECT calls succeeded **from the actual Discloud
process**, without any bot workers running. It would not prove new-database
write paths, Discord slash commands, invite enforcement, database backups,
realtime/Storage, a true guild canary, or production recovery. Later cutover
requires a verified fallback/rollback procedure, owner-approved credentials,
and functional acceptance under controlled traffic.

A database preflight or GitHub-hosted API request does NOT substitute for this
Discloud-origin proof. If the private env cannot be verified before a guarded
code commit, leave the existing bot stopped and do not deploy.
