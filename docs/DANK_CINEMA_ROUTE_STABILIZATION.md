# Production stabilization: Dank Cinema public API routing

**Scope:** Diagnose the reported `Cinema Home could not load` / `HTTP 200
text/html instead of JSON` on the existing Discloud `stoneyverify` app.

**Verified from repository:** `stoney_verify/torrent_media_server.py` registers
`register_cinema_site_routes`. That registrar maps
`GET /cinema/{guild_id}/api/home` to `cinema_home_api` (JSON on success) and
`GET /cinema/{guild_id}/api/auth-debug` to a JSON diagnostic handler.
`stoney_verify/assets/cinema_site.js` already fails closed if a successful
API request returns HTML. The observed HTML 200 is not a valid successful
Home API response, but without live response headers it is not proven whether
the browser, Discloud proxy or another intermediate layer supplied it.

**Production observations (Oct 15, 2026):** The user confirmed
`https://stoneyverify.discloud.dev/health` returns the
`dank_torrent_media` JSON contract; the old `.discloud.app` origin was
unreachable. The OAuth redirect reported by `/health` was corrected to
`https://stoneyverify.discloud.dev/cinema/auth/callback`. A post-correction
authenticated Home result has not yet been verified.

## Step 1: Public origin diagnosis (no server change)

After merging this diagnostic-only PR, open GitHub Actions:
`https://github.com/UglyGameFace/Dank-Shield/actions/workflows/dank-cinema-public-route-diagnostic.yml`

Select **Run workflow** on **main**, with:

- **origin**: `https://stoneyverify.discloud.dev`
- **guild_id**: a Discord guild snowflake where the bot is installed; **no user
  ID, cookie, or signed Cinema URL**.

The workflow executes only GETs, via
`tools/probe_cinema_public_routes.py`, to:

1. `/health`: expect HTTP 200 JSON identifying
   `service=dank_torrent_media`.
2. `/cinema/<guild_id>/api/auth-debug`: expect HTTP 200 JSON with
   `contract=signed-session-v8-snowflake-safe`. Does not authenticate a user.
3. `/cinema/<guild_id>/api/home` **without any cookies/signature**:
   expect HTTP 401. That proves this public route resolves as a protected API
   rather than returning an HTML shell with HTTP 200. It does *not* prove
   authorized Home is working.

The diagnostic refuses private origins and does not print response bodies,
signed query strings or credentials. Failed probes cause a nonzero job result;
they **do not** restart, rebuild, modify or redeploy the app.

## Step 2: Signed Android canary

Only if the public-route checks pass, open a freshly generated Dank Cinema
link through `/cinema` in Discord on Android. Try Home and Search.

- If Home loads JSON and Search works, record the real acceptance result,
  without claiming that full playback is solved.
- If Home still reports HTML 200, capture the visible error and Discloud log
  traceback for the same minute. The browser's existing error already reports
  the response media type and path without tokens. Do not post signed URLs or
  session cookies.
- If the anonymous origin probe fails, inspect Discloud public route/proxy
  configuration before changing Python auth or Cinema user data.
- If the probe passes but signed Home fails, investigate sign-in/session/cookie
  continuity and actual per-request network response/redirect chain, not
  random cache or RAM adjustments.

## Out of scope / preserved checkpoint

- A separate 11.40 GiB movie failed the explicit default 4 GiB torrent cache
  admission cap. This is a distinct storage architecture problem: do not
  delete user caches or enable the unprovisioned
  `DANK_CINEMA_ROLLING_ENABLED` worker.
- Ticket channel topics currently contain internal recovery metadata. Changing
  that needs a separate compat-aware ticket lifecycle task.
- No Supabase/PostgreSQL migrations, production secrets, backups, auto-deploy,
  Discloud rebuilds, or Discord application settings are modified here.

**Acceptance gate:** prove public routing first, then signed Android Home and
Search, then later address large-file streaming as its own controlled slice.
CI by itself is not proof of live acceptance.
