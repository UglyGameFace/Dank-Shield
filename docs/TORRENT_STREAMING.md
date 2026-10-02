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

The current production repository is configured as:

```
TYPE=bot
```

Discloud Bot deployments do not expose an external HTTP port. The torrent
engine can run there, but a member's browser/Discord client cannot reach a
localhost-only media stream.

For externally reachable playback, Discloud documents web/API/bot-with-web-
interface deployments as `TYPE=site`, with traffic proxied to:

```
DANK_MEDIA_BIND_HOST=0.0.0.0
DANK_MEDIA_PORT=8080
```

A Site deployment also requires the applicable Discloud plan and a subdomain.
Do not change the production `discloud.config` until those account-side
requirements are confirmed.

The intended final Site-style layout is:

- public Discloud proxy -> media-only server on `0.0.0.0:8080`;
- internal structured bot API -> `127.0.0.1:8081`;
- Discord bot client -> same Python process.

The public media server exposes only a health endpoint and temporary signed
torrent byte streams. It does not expose ticket/member/admin actions.

## Resource defaults

The current conservative defaults are designed around the existing 512 MB bot
allocation:

- live torrent sessions: 1
- torrent metadata file: 4 MiB
- selected video file: 2 GiB
- total torrent declared size: 4 GiB
- readahead: 16 MiB
- startup priority window: 8 MiB
- tail priority window: 4 MiB
- buffering wait: 20 seconds
- metadata wait: 30 seconds
- idle cleanup: 30 minutes
- peer connection limit: 80
- download cap: 8 MiB/s
- upload cap: 512 KiB/s

Raise these only after measuring production RAM, disk, network, and peer load.

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
- one-session default is respected;
- bot admin API remains private and authenticated.
