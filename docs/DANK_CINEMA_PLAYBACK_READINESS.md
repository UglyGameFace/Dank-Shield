# Dank Cinema playback readiness and torrent throughput evidence

## Production evidence (2026-10-08 Android canary)

Player for *Lanterns S01E02* showed a valid host-only standalone session, "Resumed from 1:10", "Stream Health: Excellent", torrent progress 14.2%, live connected seeds/peers 121/135, download rate 6.1 MiB/s, 20s buffer target, server startup first wait 9.76s for 9.0 MiB, first byte 11.0s, browser metadata/canplay around 10.5s and first frame 34.7s. User reported Play did not begin. A torrent's global completion percentage is **not** the browser's playable buffer at the selected position; a healthy seed count is **not** a demonstrated media start. The screenshot is not a host bandwidth benchmark and cannot establish the absolute speed limit.

## Confirmed code defect and focused PR scope

- The host click handler previously awaited `primeAudiblePlaybackGesture()`; that function awaited an HTMLMediaElement `play()` promise before submitting the authoritative `hostAction("resume")`. Per [MDN](https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/play), `play()` is asynchronous and only fulfills after playback actually begins, so waiting for it can block the Resume request when media has not buffered. Its temporary play/pause also risked emitting unnecessary host events.
- Host click must make audio/video play attempts **during the user gesture but without awaiting media readiness**, send the canonical host Resume immediately, and suppress duplicate play/pause event actions during the explicit gesture. Actual media readiness remains the browser's own state, not the torrent download count.
- Saved-position restoration only means the canonical seek was accepted, not that playback started. Don't display "Resumed" at seek acceptance.
- The health label must not advertise "Excellent" purely from seeds/download rate. Use browser `readyState` and media errors, distinct from torrent swarm health; the latter remains under Advanced Stream Details.
- Keep signed access, session authority, party/private/standalone separation, AA(C) compatibility, viewer Tap to Sync, torrent byte-range correctness, and Discord startup guards unchanged.

## Existing torrent transport implementation (reviewed; no speculative change)

- `stoney_verify/torrent_streaming.py` defaults: `DANK_TORRENT_CONNECTION_LIMIT=200` (max 300), `DANK_TORRENT_CONNECTION_SPEED=80` (max 200), `DANK_TORRENT_CONNECT_BOOST=80`, `DANK_TORRENT_DOWNLOAD_RATE_BYTES=67108864` (64 MiB/s aggregate cap), initial time-critical deadline 500ms +350ms/piece, adaptive readahead with capped memory/session use.
- An old production environment override can lower those values. Check **effective environment**, not source defaults; do not disclose secrets.
- `stoney_verify/api_new/torrent_stream_routes.py` uses 1 MiB HTTP transfer chunks. For 206 byte-range requests it waits only on the first chunk; for non-Range 200 it currently gates on a wider startup corridor. This preserves the prior correctness contracts for 200/206. The screenshot's 9 MiB first wait is compatible with a non-Range request (or another recorded consumer), **not yet proven**. Add a bounded, privacy-safe first-request method/Range/offset/ready-bytes diagnostic in a separate follow-up if needed before considering altering the 200 behavior. Do not log signed URLs/cookies or bearer tokens.
- The observed 6.1 MiB/s may be below the configured ceiling for many reasons: peer upload capacity, availability of precisely needed pieces, network congestion, origin I/O, hashing/CPU or disk, and multiple concurrent sessions. A large number of seeds does not independently prove any of these; measure them.
- Follow [libtorrent 2.x handle priority/deadline reference](https://libtorrent.org/reference-Torrent_Handle.html), [settings reference](https://libtorrent.org/reference-Settings.html), and [MDN media readiness](https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/readyState); don't turn on global sequential download as a fake streaming optimization. Avoid unbounded connections, disk cache, process RAM, and upload bandwidth.

## Read-only production acceptance checklist

1. Verify the exact branch/commit actually deployed; current Discloud Auto Deploy follows `aaa-cinema-pre-pregateway-bind`, NOT main. Preserve backup `backup/stoneyverify-stable-7fdc331-20261008`.
2. Read (don't change) the active `DANK_TORRENT_CONNECTION_LIMIT`, `DANK_TORRENT_CONNECTION_SPEED`, `DANK_TORRENT_CONNECT_BOOST`, `DANK_TORRENT_DOWNLOAD_RATE_BYTES`, `DANK_TORRENT_TARGET_BUFFER_SECONDS`, and `DANK_TORRENT_BUFFER_WAIT_SECONDS` values or their absence. Avoid posting secret environment values.
3. On host Play after saved position, verify action acknowledged without first waiting for browser `play()` resolution; confirm the UI says **position set** before rendering a real frame.
4. Verify browser `canplay`, `playing`, and first-video-frame timing on legal/test media. Verify a paused but well-buffered source says ready, and 100+ seeds with insufficient browser data says preparing.
5. Run a controlled same-title cold start with actual first-request Range/200 classification, source/metadata/first-byte/first-frame timings and live download rate; compare two runs under the same environment, not different seeds/title sizes.
6. Check host/viewer silence and Tap to Sync, transient background recovery, Private Session access, queue, site Home and Discord interactions after merge and controlled Discloud promotion.
7. Don't declare full-speed optimization proven unless actual host network, disk, client and swarm measurements demonstrate what is limiting throughput.

## Status

This patch fixes an observed host Play ordering flaw and misleading readiness UI. It is **not** a benchmark and does not claim the swarm is now faster. Network/peer performance work should be a separate measured change only if runtime evidence warrants it.
