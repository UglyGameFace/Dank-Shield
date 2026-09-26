# Live Captions voice stack contract

This document is the engineering contract for Dank Shield Live Captions. It exists because Discord voice receive is no longer "get UDP, decode Opus": modern Discord voice adds DAVE end-to-end encryption between transport decryption and codec decode.

The public feature gate `DANK_COMMUNITY_LIVE_CAPTIONS_ENABLED` must remain off until the real Discord soak matrix at the end of this document passes.

## Authoritative references

- Discord DAVE overview: https://discord.com/blog/meet-dave-e2ee-for-audio-video
- Discord DAVE protocol/whitepaper: https://daveprotocol.com/
- discord.py Opus API: https://discordpy.readthedocs.io/en/latest/api.html#opus-library
- Xiph Opus decoder API: https://opus-codec.org/docs/opus_api-1.3.1/group__opus__decoder.html
- Discloud APT contract: https://docs.discloud.com/en/configurations/discloud.config/apt
- bundled libopus distribution: https://pypi.org/project/opuslib-next-bundled/
- discord-ext-voice-recv upstream: https://github.com/imayhaveborkedit/discord-ext-voice-recv

## Receive pipeline

For one Discord speaker, the supported path is:

```text
Discord voice gateway
  -> UDP/RTP packet
  -> Discord transport decryption
  -> RTP/depacketized encoded media frame
  -> DAVE decrypt for the mapped Discord sender
  -> Opus packet
  -> per-SSRC Opus decoder
  -> 48 kHz / stereo / signed 16-bit PCM
  -> Dank Shield SSRC + source-user agreement
  -> that user's explicit Caption My Voice consent
  -> per-speaker segment buffer
  -> in-memory WAV segment
  -> OpenAI transcription
  -> configured caption text channel/thread
```

DAVE is frame encryption, not a replacement for the Discord voice transport cipher. Discord's documented order is encode -> DAVE encrypt -> packetize when sending, and depacketize -> DAVE decrypt -> decode when receiving.

Dank Shield must never feed DAVE ciphertext into an Opus decoder and must never transcribe PCM before speaker identity and consent checks pass.

## Discord.py / DAVE ownership

Pinned Discord runtime:

```text
discord.py[voice]==2.7.1
```

The voice extra supplies the Discord voice dependencies, including `davey`. discord.py owns the voice connection and its DAVE/MLS session state.

Inbound receive is currently pinned to upstream `discord-ext-voice-recv` PR #58 at exact SHA:

```text
03dd1e2dafe85522cc458441cd5b143b136ac836
```

That patch reuses discord.py's ready DAVE session and the SSRC-mapped Discord user when decrypting an inbound audio frame immediately before Opus decode.

PR #57's narrow packet-router survival behavior is applied by Dank Shield: a single `discord.opus.OpusError` is counted/dropped instead of terminating the whole receive reader. Other exception classes remain fatal and visible.

PR #56 is a larger experimental receive rewrite with additional media-kind filtering and buffering. It is not adopted implicitly. Camera/video/screen-share traffic must be part of the real soak matrix before the current smaller PR #58 path is called production-ready.

## Opus contract

Dank Shield needs PCM for speech segmentation and WAV transcription. Therefore the receive sink returns `wants_opus() == False`, which makes `discord-ext-voice-recv` create one `discord.opus.Decoder` per SSRC.

discord.py documents that native libopus must be loaded for PCM voice work and that `discord.opus.is_loaded()` must be true.

Production must not depend on incidental shared libraries in the host image. Dank Shield pins:

```text
opuslib-next-bundled==0.1.1
```

Its platform wheels contain the Xiph Opus shared library under `opuslib_next/_native`. Dank Shield resolves that file without importing the binding package and supplies the full path to `discord.opus.load_opus()`.

Runtime load order is:

1. already-loaded discord.py Opus runtime;
2. explicit `DANK_OPUS_LIBRARY` operator override, when configured;
3. pinned bundled library from `opuslib-next-bundled`;
4. system `ctypes.util.find_library("opus")` compatibility fallback;
5. conventional native-library names as a final fallback.

The Discloud `ffmpeg` APT option is **not** an Opus-runtime contract. Discloud documents that option as installing the `ffmpeg` package. It is not used as a substitute for a standalone libopus loadable by discord.py.

CI must prove the real ABI, not just mock it: a fresh Python subprocess explicitly loads the bundled file with `discord.opus.load_opus()`, confirms `discord.opus.is_loaded()`, and constructs a real `discord.opus.Decoder()`.

## Audio format and stream state

discord.py's Opus decoder uses 48,000 Hz stereo output with 20 ms frames. Signed 16-bit stereo PCM is 3,840 bytes per normal 20 ms frame.

Opus decoding is stateful. Decoder state must remain isolated per encoded stream/SSRC. Dank Shield does not mix encoded packets from multiple users into one decoder.

Packet loss, FEC/PLC, sequence rollover, jitter, reconnects, and SSRC changes are receive-layer concerns. They must not cause audio from one Discord user to inherit another user's caption buffer.

## Speaker identity boundary

A PCM frame is eligible for captioning only when all of these are true:

1. the receive library supplies a Discord source user;
2. the RTP packet has a non-zero SSRC;
3. the voice client's current SSRC -> Discord user mapping resolves;
4. the source user ID equals the SSRC-mapped user ID;
5. that same user has explicitly opted in;
6. PCM is non-empty and aligned to signed-16-bit stereo samples.

Unknown or mismatched identity is dropped. Dank Shield does not guess a speaker from timing, display name, speaking events, channel order, or another user's nearby packets.

## Consent and memory lifetime

Consent is per Discord user and memory-only.

Opt-out:
- blocks future frames immediately;
- advances the user's consent generation so callbacks scheduled under older consent are invalid;
- removes buffered and queued segments for that user;
- cancels in-flight transcription tasks for that user.

Stopping a session clears all consent, queued audio, segment buffers, and in-flight transcription work.

Dank Shield itself does not persist raw audio.

## Guild / voice-channel isolation

Discord permits one active voice connection for this bot identity per guild. Dank Shield therefore owns at most one caption receiver per guild.

General server captions and Community Hub captions share the same guild receiver lock. They cannot run independent competing receivers in different voice channels of the same server.

A normal-server caption transcript includes its source voice-channel identity. Community Hub captions remain scoped to their Community Hub discussion/thread.

## Transcription boundary

Only isolated, opted-in PCM is converted to an in-memory WAV segment and sent to the configured OpenAI transcription API.

The first pass uses the original PCM samples. A low-confidence retry may amplitude-normalize the same samples; it must not use a destructive speech/noise gate that can silently remove words.

Provider failures, empty transcripts, unclear results, and publishing failures remain separately observable.

## Required observability

The bot-owner soak panel must expose enough state to localize a failure:

- raw UDP packets;
- DAVE session present/ready/status/protocol/epoch;
- reader listening state;
- mapped SSRC count;
- native Opus readiness/source;
- corrupt Opus drops;
- reader failures and sanitized reader stop reason;
- PCM frames reaching the hardened sink;
- identity/consent/malformed drops;
- routed frames and queue depth;
- transcribed/published/empty/unclear/failure counters.

A bot merely joining the voice channel is not evidence that receive, DAVE, Opus, or transcription works.

## Real Discord soak matrix before public enablement

The feature gate stays off until live testing covers at least:

1. one opted-in speaker, several sentences;
2. a non-consenting user speaking in the same VC;
3. two opted-in speakers talking separately;
4. two opted-in speakers overlapping;
5. mute/unmute and deaf/undeaf changes;
6. disconnect/reconnect and SSRC remapping;
7. move out of and back into the target voice channel;
8. stop/restart captions;
9. opt out while speaking, then opt back in;
10. DAVE epoch/key transition while the session remains running;
11. packet loss/jitter/out-of-order behavior without reader death;
12. camera/video enabled by another participant;
13. screen share / Go Live enabled by another participant;
14. sustained operation long enough to cross normal Discord voice keepalive and DAVE transitions;
15. OpenAI 401/403/429/provider failure diagnostics;
16. output-channel permission loss and recovery;
17. Community Hub start/end cleanup using the same receiver owner;
18. general-server stop cleanup with no late caption publication.

Success means the reader remains alive, speaker identity never crosses users, non-consenting audio never reaches transcription, and captions continue through expected voice/DAVE transitions.

## Known limitation

If one participant's physical microphone captures another person, a television, game audio, or speaker output in the room, Discord has already encoded that sound as part of that participant's source stream. Dank Shield cannot perfectly separate those physical sounds after the fact and must not claim otherwise.
