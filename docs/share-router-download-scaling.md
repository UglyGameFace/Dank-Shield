# Share Router download queue: scale boundary

This is an incremental step toward a public bot with 10,000+ Discord guilds.
It does **not** certify 10,000-guild gateway operation or unlimited concurrent
downloads.

## Shipped in this slice

- Incoming Download-channel messages are validated and recorded immediately
  as **Discord IDs only** in a bounded on-disk SQLite queue. No video URLs,
  signed queries, message body, cookies or media bytes are stored in the queue.
- Up to **256 jobs total**, **10 jobs per guild**, and **one outstanding request
  per guild member**. Admission is transactional. Each guild runs at most one
  active download, so one server cannot occupy both available workers.
- **Two media download workers** process the queue fairly across guilds. These
  are still the physical CPU/egress budget of the existing bot process.
  Signed-URL hosting still permits **four active HTTP transfers**.
- Jobs survive an ordinary process restart **only if the data directory is
  persistent**. Jobs already running when a process stops are discarded on
  startup; they cannot be safely replayed automatically, because Discord may
  have received the DM before the crash. Original source posts are retained
  unless successful delivery already cleaned them. Members can resubmit.
- Unstarted queue jobs expire after six hours. Expired jobs do not trigger
  stale or unexpectedly late downloads. The intake reaction is best-effort
  acknowledgement, not a guaranteed delivery receipt.
- At processing time the worker refetches the original message and resolves
  the current guild Member / saved download channel. Deleted posts, missing
  members, changed channel settings or permissions do not cause a DM.
  The canonical download handler still owns SSRF checks, media size/time
  limits, 19 MiB DM attachment cap, staging, expiring links and cleanup.
- High-volume guild route lookups use a single read-only JSON index cached by
  file identity/version, invalidated on writes. This avoids reparsing all
  registered guilds on every Discord event while preserving the existing
  route config and admin operations.

## Not yet 10,000-guild / multi-instance ready

The SQLite queue and signed-video files both live under the Discloud
application's **local filesystem**. Do not run multiple independent containers
and assume they share a durable queue or video files. If Discloud replaces
local storage on redeploy, queued jobs and existing signed videos disappear.
A larger RAM/CPU subscription does not increase the hard-coded 2 worker / 4
HTTP limit by itself.

A truly horizontally scalable public deployment still requires:

1. Discord's documented gateway sharding appropriate to 10,000 guilds.
2. An independently provisioned shared job broker or durable PostgreSQL queue,
   with atomic leases, per-job delivery outcomes and careful no-duplicate DM
   recovery. Do not repurpose the private **verification reminder** database
   implicitly for large video/media payloads.
3. Separate bounded download processors and durable object storage with
   CDN/edge delivery and egress usage budgets.
4. Metrics for queue wait time, refusal, downloads/second, success/failure,
   p95 extraction/DM latency, storage pressure and per-provider rate limits.
5. Real Discloud/Discord canaries and stress tests, including restarts,
   shard moves, stale message/member permissions and 10,000-guild equivalents.

## Regression acceptance

Run `tests/test_share_router_download_queue.py` plus the existing Share Router
suite; verify transactional queue admission, fair server rotation, one active
job/guild, no replay after crash, privacy (no source URLs in SQLite), cached
read visibility after route admin changes, and a genuine Discord DM via a
deployed test server. Confirm Discloud file persistence before relying on
restart durability. Do not claim 10,000-guild certification from unit tests.
