# Dank Shield Torrent Streaming Runbook

## What this feature owns

Dank Shield can accept a user-supplied magnet link or `.torrent` attachment
through an existing Share Router source channel and create a temporary
progressive media stream for lawful, public-domain, or otherwise authorized
video content.

The runtime:

1. resolves torrent metadata;
2. rejects torrents outside configured metadata/total-size budgets;
3. selects the largest supported video file;
4. disables unrelated files;
5. prioritizes startup pieces and the end of the selected file;
6. reprioritizes requested and upcoming pieces on HTTP byte-range seeks;
7. serves the selected file through a temporary signed URL;
8. removes idle sessions and downloaded data after the configured TTL.

It does not search torrent indexes, recommend torrents, or discover content for
the user.

## Runtime ownership

- `stoney_verify/torrent_streaming.py`
  - libtorrent session
  - magnet / .torrent ingestion
  - playable-file selection
  - piece priorities and seek readahead
  - session limits
  - signed stream URLs
  - cleanup
- `stoney_verify/api_new/torrent_stream_routes.py`
  - byte-range streaming handler
  - internal status/cancel routes
- `stoney_verify/torrent_media_server.py`
  - media-only public aiohttp listener
- `stoney_verify/share_router_runtime.py`
  - detects user-supplied magnet / .torrent sources and starts the canonical
    torrent stream owner

There is no second torrent downloader or Share Router send path.

## Playback behavior

The HTTP endpoint supports `Range: bytes=...` requests and responds with
`206 Partial Content`. Before each chunk is read, the requested torrent pieces
are raised to priority 7 and a bounded readahead window is raised to priority 6.

The first part of the selected file is prioritized at session creation. A small
tail range is also prioritized because some MP4 files keep important metadata
near the end of the file.

If the requested pieces are not available within the buffering timeout, the
first request receives a temporary `503 buffering` response. Players can retry
the same range.

## Required environment

At minimum:

```
DANK_MEDIA_PUBLIC_BASE_URL=https://your-media-host.example
DANK_MEDIA_BIND_HOST=0.0.0.0
DANK_MEDIA_PORT=8080
DANK_TORRENT_STREAM_SECRET=<dedicated-random-secret>
```

Keep the structured bot API separate:

```
BOT_API_BIND_HOST=127.0.0.1
BOT_API_PORT=8081
BOT_API_REQUIRE_AUTH=true
```

Do not reuse `BOT_API_SHARED_SECRET` as the torrent stream secret.

## Discloud deployment

The production repository is now configured for the owner's Site-capable
Diamond deployment target:

```
TYPE=site
MAIN=main.py
RAM=1495
```

External Movie Night playback uses the media-only listener:

```
DANK_MEDIA_BIND_HOST=0.0.0.0
DANK_MEDIA_PORT=8080
```

The layout is:

- public Discloud proxy -> media-only server on `0.0.0.0:8080`;
- internal structured bot API -> `127.0.0.1:8081`;
- Discord bot client -> same Python process.

The public media server exposes only a health endpoint and temporary signed
torrent byte streams. It does not expose ticket/member/admin actions.

## Resource defaults

The current host target is approximately **1495 MiB RAM** with ordinary Dank
Shield RSS around **340–390 MiB** before Movie Night load.

Movie Night no longer treats the session count as the only safety check. New
**unique** torrents are admitted only while current RSS, the protected core-bot
reserve, configured session limits, and free disk are healthy.

Current production-oriented defaults:

- protected core-bot RAM reserve: 350 MiB
- initial estimated incremental RAM per unique torrent: 96 MiB, then adaptively learned from clean RSS deltas
- conservative unique-torrent soft limit: 2
- hard unique-torrent limit: 4
- per-guild unique-torrent limit: 1
- burst beyond the soft limit: disabled
- torrent metadata file: 4 MiB
- selected video file: 25 GiB
- total torrent declared size: 50 GiB
- protected free-disk reserve: 64 GiB
- readahead: 16 MiB
- startup priority window: 8 MiB
- tail priority window: 4 MiB
- buffering wait: 20 seconds
- metadata wait: 30 seconds
- idle cleanup: 30 minutes
- peer connection limit: 80
- download cap: 16 MiB/s
- upload cap: 512 KiB/s

### Shared torrent reuse

Torrent identity/info-hash is indexed process-wide. Multiple Movie Night rooms
choosing the same torrent reuse one libtorrent handle and one disk cache instead
of starting duplicate downloads.

Each Movie Night room owns a stable lease
(`movie:<guild_id>:<channel_id>`). A room switching or ending releases only its
lease. The underlying torrent is removed only when no tracked room still needs
it and there is no untracked Share Router hold, or when the normal idle cleanup
expires it.

Room clocks, votes, viewers, seek state, and synchronized playback remain
independent even when the torrent bytes are shared.

The Setup panel exposes current RSS, configured memory limit/reserve, unique
torrent count, room lease count, shared-session count, admission slots, free
disk, disk reserve, and committed selected-file bytes.

## Share Router usage

Once the public media endpoint is configured:

1. send a magnet link into a configured Share Router proxy channel, or attach a
   `.torrent` file there;
2. Dank Shield validates the normal Share Router source/target permissions;
3. the torrent runtime resolves metadata and chooses the playable video;
4. the destination receives a temporary signed playback URL and media details;
5. the proxy-source message is deleted only after a stream session was
   successfully created;
6. recent duplicate torrent sources do not start another session.

If public media configuration is missing, Share Router keeps the source message
and records a blocked-route reason instead of pretending the torrent was routed.

## Validation before production

Use only known legal/public-domain torrent fixtures.

Verify:

- magnet metadata resolution;
- .torrent attachment ingestion;
- multi-file playable selection;
- first playback range begins before full torrent completion;
- seeking causes new piece priorities near the requested byte range;
- duplicate magnet shares do not create duplicate sessions;
- signed URL tampering and expiry are rejected;
- range requests return correct `206` boundaries;
- disconnects do not keep writing indefinitely;
- idle cleanup removes torrent handles and files;
- dynamic memory/disk admission rejects unsafe new unique torrents;
- identical torrent identities reuse one shared session;
- releasing one Movie Night lease does not break another room using the same torrent;
- bot admin API remains private and authenticated.


Per-guild start reservations are counted before metadata resolution completes,
so two simultaneous requests from one guild cannot race through the fairness
limit. The initial 96 MiB next-session estimate is an admission prior, not a
permanent constant: clean non-overlapping unique-session starts update it with
a conservative moving average. Current process RSS and the 350 MiB protected
reserve remain authoritative even if the learned estimate is optimistic.
