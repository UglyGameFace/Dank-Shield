# Dank Cinema media compatibility contract

## Principle

A torrent containing a recognized video filename is **not** proof that a browser can
decode its video or play its audio. Native playback capability varies by browser,
device, video codec, audio codec, codec profile, bit depth, and container.

The current Cinema runtime uses the original, authenticated byte-range video
stream and a separately authenticated FFmpeg AAC audio sidecar when needed.
**There is no general video-transcoding or container-remux fallback.**
Do not present a source as universally playable, or assert audible playback
merely because HTTP returns 200, a video element fires `playing`, or a torrent
has available seeds.

### Implemented compatibility paths

| Input/container or audio | Current route | Guarantee/limitation |
| --- | --- | --- |
| .mp4 / .m4v | Original video byte-range stream | Likely broad support for verified H.264/8-bit with AAC, but the browser must confirm decoder capability and render a frame |
| .mov | Original video stream | Browser-dependent container and profile; no automatic remux |
| .webm | Original video stream | Browser-dependent support for VP8/VP9/AV1 and Opus/Vorbis |
| .mkv | Original video stream | Filename recognized; many browsers cannot natively demux Matroska |
| .avi | Original video stream | Filename recognized; commonly unsupported by browser video elements |
| .mpeg / .mpg | Original video stream | Filename recognized; decoder support varies |
| Native AAC / MP3 audio | Embedded soundtrack if browser supports it | Actual browser, container, stream layout and profile govern support |
| Native Opus / Vorbis audio | Embedded if supported | Not portable across every container/browser pairing |
| AC-3 / E-AC-3 / DTS-DCA / TrueHD-MLP / PCM and other FFmpeg-decodable audio | Existing signed `/media/torrent/audio/` sidecar converted to AAC-LC stereo, 48 kHz | Requires FFmpeg on host, supported input decoding, consistent stream timestamps, and client AAC/MP4 support |
| Verified multiple audio streams | Existing FFmpeg `-map 0:a:<index>` selector, max index 7 | Stream index validated by verified probe; output is stereo, **not 5.1/7.1 passthrough** |
| Unsupported video codecs or containers | Native browser probe, compatibility warning and alternate-source guidance | No universal CPU-intensive video transcoder |
| Encrypted/DRM streams or arbitrary audio-only files | No general playback path | Do not claim support |

### Selecting a source

1. Existing host votes remain authoritative in shared rooms.
2. A source with zero seeds remains below seeded sources, regardless of its codec.
3. When verified metadata is available, prefer H.264/8-bit MP4 over releases
   with a video codec/container that will commonly require video transcoding.
   Unknown/unprobed video stays **unknown**, never described as guaranteed safe.
4. Audio risk is determined across all verified audio tracks, not the existence
   of one AAC track hidden among incompatible soundtracks.
5. Existing swarm health, seeds, source quality, source-size and user-selected
   source preferences remain intact. Do not infer actual throughput from a
   source's published seed count.
6. The browser's own `canPlayType` supplies **advisory** local decode evidence.
   A `probably` result is not a promise. Rendered first-frame evidence and
   actual audio playback remain independent acceptance checks.
7. Never silently change a shared room's source solely for one member's audio
   language choice.

### Viewer language and playback

- The existing authenticated Cinema user-profile preferences store per-guild
  audio language overrides keyed by the guild ID **from the authorized room**,
  never an untrusted submitted guild ID.
- Native browser audio tracks and the FFmpeg sidecar must both match the same
  verified track language codes. Absent language means original track, not an
  unverified language guess.
- The user's selection is local to their player. Do not modify another
  viewer's track, host permissions, or the shared room source.
- Normal user Play/Unmute/track changes request audio directly. Display the
  audio recovery button only after a real browser-side playback failure;
  browser autoplay permission policy cannot be bypassed.
- The video clock is authoritative for the AAC sidecar, including buffering and
  seeks. The Theater's Advanced Stream Details show non-sensitive clock drift
  and decoder status; these do not expose signed media URLs.

### Acceptance matrix before calling universal media support

For Android browsers and representative desktop browsers, test H.264/AAC in
MP4; H.264/E-AC-3 in MP4; H.264/DTS and HEVC/TrueHD in MKV; VP9/Opus in WebM;
and supported AV1 combinations. Include both a single and multiple audio-track
release, language changes, seeking, buffering and host/viewer behavior.

**A format is not certified until:** the correct content is selected,
the browser renders a first frame, the chosen language is audibly present,
sound stays synchronized for multiple minutes, seeks and stalls recover,
and the source does not cause uncontrolled FFmpeg restarts. No current CI run
constitutes this live compatibility certification.

Video transcoding is a separate performance-sensitive engineering change,
requiring explicit Discloud resource and 20-viewer concurrency acceptance.
Do not add unbounded FFmpeg video workers as a fallback.
