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
- Gemini audio transcription: https://ai.google.dev/gemini-api/docs/transcribe
- Gemini Live Transcribe: https://ai.google.dev/gemini-api/docs/live-api/live-transcribe
- Gemini Live API reference: https://ai.google.dev/api/live
- Gemini API rate limits: https://ai.google.dev/gemini-api/docs/rate-limits

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
  -> that user's explicit remembered Auto-Caption consent + active VC admission
  -> stateful per-speaker 48 kHz stereo -> 16 kHz mono FIR conversion
  -> each Discord PCM frame streams immediately to that speaker's persistent Gemini Live Transcribe WebSocket
  -> local per-speaker silence boundary sends activityEnd
  -> finalized inputTranscription + detected BCP-47 language code
  -> optional text-only English translation
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

PR #57's narrow packet-router survival behavior is applied by Dank Shield: a single `discord.opus.OpusError` can no longer terminate the whole receive reader. Before the final drop path, Dank Shield now patches the pinned PacketDecoder decode boundary so one isolated corrupt real Opus frame uses the same native libopus packet-loss concealment mechanism upstream already uses for a known-missing frame. Consecutive corrupt real frames are not endlessly synthesized: after one PLC replacement, further corruption falls through to the counted router drop until a real frame decodes successfully. Other exception classes remain fatal and visible, and soak telemetry separately reports PLC recoveries versus unrecoverable drops.

PR #56 is a larger experimental receive rewrite with additional media-kind filtering and buffering. It is not adopted implicitly. Camera/video/screen-share traffic must be part of the real soak matrix before the current smaller PR #58 path is called production-ready.

## Opus contract

Dank Shield needs PCM for speech segmentation and Gemini Live Transcribe input. Therefore the receive sink returns `wants_opus() == False`, which makes `discord-ext-voice-recv` create one `discord.opus.Decoder` per SSRC.

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

### Voice control-plane vs media-plane recovery

A Discord voice session can report DAVE active, mapped SSRCs, and a listening reader while the UDP media path is no longer delivering usable audio. Those control-plane flags are not treated as proof that voice is flowing.

Dank Shield listens to the voice gateway's speaking-state event for opted-in users. After a non-zero speaking signal, it snapshots receive counters and waits 2.5 seconds. If no PCM reaches the hardened sink while the reader is still listening, DAVE is ready, and a remote SSRC is mapped, the session is classified as a stalled receive transport.

Recovery replaces only the Discord voice receive transport and `PerSpeakerFrameBridge`; the existing `CaptionEngine` and Gemini speaker session remain alive. The fresh bridge restores exactly the same opted-in user IDs. Old sink cleanup only touches the discarded bridge, so asynchronous cleanup cannot revoke consent on the recovered transport.

Automatic recovery has a 20-second cooldown and is capped at two attempts per rolling minute. Quiet voice channels never trigger recovery by themselves.

## Speaker identity boundary

A PCM frame is eligible for captioning only when all of these are true:

1. the receive library supplies a Discord source user;
2. the RTP packet has a non-zero SSRC;
3. the voice client's current SSRC -> Discord user mapping resolves;
4. the source user ID equals the SSRC-mapped user ID;
5. that same user has explicitly opted in;
6. PCM is non-empty and aligned to signed-16-bit stereo samples.

Unknown or mismatched identity is dropped. Dank Shield does not guess a speaker from timing, display name, speaking events, channel order, or another user's nearby packets.

## Consent, remembered preference, and audio lifetime

Live Captions requires explicit per-member consent. A member may enable **Auto-Caption My Voice** once for a Discord server. That boolean preference and the member's optional language hint are persisted in the existing per-guild member settings record so they survive caption restarts and bot redeploys.

Remembered consent is not active audio capture by itself. Dank Shield only admits that member's voice when a caption session is running and the member is in that session's target voice channel. On join/start, the remembered preference is restored automatically. Leaving the target VC or stopping the caption session immediately ends runtime audio admission.

Turning **Auto-Caption My Voice** off:
- persists the revocation for that server;
- blocks future frames immediately;
- advances the user's consent generation so callbacks scheduled under older consent are invalid;
- removes buffered and queued segments for that user;
- cancels in-flight transcription tasks for that user;
- closes that speaker's Gemini Live session.

Stopping a session clears runtime admission, queued audio, segment buffers, and in-flight transcription work. It does **not** silently erase the member's remembered per-server preference.

Dank Shield itself does not persist raw audio or PCM buffers.

## Guild / voice-channel isolation

Discord permits one active voice connection for this bot identity per guild. Dank Shield therefore owns at most one caption receiver per guild.

General server captions and Community Hub captions share the same guild receiver lock. They cannot run independent competing receivers in different voice channels of the same server.

A normal-server caption transcript includes its source voice-channel identity. Community Hub captions remain scoped to their Community Hub discussion/thread.

## Transcription boundary

Only isolated, opted-in PCM crosses the transcription boundary. Discord receive produces 48 kHz stereo signed-16 PCM. Dank Shield first downmixes stereo, applies a deterministic low-pass FIR below the 16 kHz target Nyquist limit, then decimates by three to 16 kHz mono signed-16 PCM. This avoids folding high-frequency mic/game noise back into the speech band during resampling.

Primary model: `gemini-3.5-transcribe-live`. Each opted-in Discord speaker owns a separate persistent WebSocket. Dank Shield uses Google's documented **manual VAD** contract because Discord has already isolated one speaker and Dank Shield owns the utterance boundary. Automatic activity detection is disabled; `activityStart` is sent immediately before that speaker's first streamed PCM chunk and `activityEnd` is sent when the local silence/max-duration boundary closes. `audioStreamEnd` is not used in manual-VAD mode.

The production path submits decoded Discord audio **as frames arrive** but does not send one WebSocket audio message per Discord frame. After stateful FIR resampling, each speaker's session micro-batches approximately 100 ms of 16 kHz mono PCM per provider audio message, matching Google's current Live Transcribe chunk guidance while avoiding the old multi-second utterance buffering failure. Any shorter remainder is flushed before manual `activityEnd`, so no speech tail is discarded. The Gemini socket is prepared before the speaker is admitted so the first spoken audio is not queued behind the WebSocket handshake. The segmenter remains only to identify local utterance boundaries and bound cleanup/privacy state. When the first frame after a packet gap proves that the previous utterance ended, Dank Shield sends the previous turn's `activityEnd` **before** that post-gap frame can open the next `activityStart`; otherwise the new utterance's first audio would be incorrectly attached to the prior turn. Final transcript waits use FIFO per-turn futures, so the next turn can keep streaming without waiting for the prior `inputTranscription` and concurrent results cannot be consumed by the wrong segment task. The default 0.75-second packet-gap boundary is intentionally within Google's current 500–800 ms manual-VAD guidance, avoiding the aggressive sub-500 ms cutoff that can fragment natural pauses.

Language behavior defaults to **Auto / all supported languages**. An empty `languageCodes` list lets Gemini detect across its supported transcription locales and handle code-switching. A participant may optionally set **/captions → My Language** through Discord dropdowns. The first select offers **Auto · All Supported Languages** or a language group; the second select offers every BCP-47 code in Google's current Gemini 3.5 Transcribe supported-language table. No free-form language modal is used. The chosen hint is remembered per server, applies only to that Discord user's provider session, and reconnects only that speaker when changed. Auto remains available for multilingual/code-switching speakers.

Live Transcribe distinguishes speculative `interimInputTranscription` from finalized `inputTranscription`. Dank Shield normally publishes only finalized `inputTranscription`. The sole recovery exception is a provider-finalization timeout: if the exact sealed turn produced at least two recent, near-matching interim hypotheses with a consistent language family, that stable interim may rescue the timed-out turn after the provider socket is closed. A single, short-changing, conflicting, or cross-language interim is never published as a fallback, and an authoritative final always wins when it arrives. Final results include a BCP-47 `languageCode`, which is retained for diagnostics and translation decisions. When a participant gave an explicit language hint and Gemini reports a different primary language family, Dank Shield fails that utterance closed as `[unclear audio]` instead of publishing confident-looking text in an unrelated language.

Gemini documents a 10-minute maximum Live Transcribe session. Dank Shield proactively rotates a speaker's session before that limit and also honors `goAway` by reconnecting before the next utterance. WebSocket openness alone is not treated as session health: if the dedicated receive task is missing or has exited while the socket still appears open, the speaker session reconnects before accepting more PCM so audio cannot be sent into an unread connection.

The server owner chooses one text-output mode:

1. **Original language** — publish the finalized transcript as spoken.
2. **English** — translate only finalized non-English transcript text to English.
3. **Original + English** — publish the original transcript plus the English translation.

English conversion never resends the audio. It uses `gemini-3.1-flash-lite` on finalized text only. If Live Transcribe reports an `en-*` language code, Dank Shield skips the translation request entirely. Translation failure does not stop the original caption pipeline.

Gemini Live Transcribe does not provide the old OpenAI token-logprob confidence used by the previous provider. Dank Shield therefore does not invent a confidence score. Confidence-only retry/unclear logic runs only when a provider actually supplies numeric confidence.

Provider failures, Live session reconnects, transcription quota blocks, translation requests/skips/failures, and publishing failures remain separately observable.

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
- Gemini Live connection/reconnect counts;
- realtime audio chunks sent, audio-stream-end count, interim transcription events, and finalized transcription events;
- Discord voice gateway speaking-signal count;
- automatic receive-transport recovery count/failures and last recovery reason;
- configured server output mode plus each opted-in speaker's remembered language hint and auto-caption preference;
- last per-speaker audio RMS level and Gemini-detected language code;
- explicit-language mismatch count;
- translation request/skip/failure counts;
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
15. Gemini authentication/permission/RESOURCE_EXHAUSTED/provider failure diagnostics;
16. output-channel permission loss and recovery;
17. Community Hub start/end cleanup using the same receiver owner;
18. general-server stop cleanup with no late caption publication;
19. automatic language detection in multiple supported languages;
20. mid-conversation code-switching;
21. Original, English, and Original + English output modes;
22. detected English skipping the translation request;
23. non-English finalized text translation without a second audio submission;
24. Gemini Live session rotation / `goAway` reconnect without cross-speaker state leakage;
25. translation quota/provider failure degrading to original captions instead of stopping transcription;
26. English-only speech with **My Language = English (US)** across several sentence lengths, verifying detected `en-*` and zero language-hint mismatches;
27. deliberately conflicting explicit hint vs spoken language, verifying the utterance fails closed instead of publishing the wrong-language transcript;
28. Auto mode with multiple supported languages and genuine code-switching after the explicit-hint accuracy pass;
29. a stalled media-plane case where Discord emits an opted-in speaking signal but no PCM follows, verifying one bounded automatic transport rebuild preserves the same consent and caption engine;
30. repeated receive stalls, verifying no more than two automatic rebuild attempts occur in one rolling minute and the panel exposes the recovery state instead of treating control-plane flags as media health.

Success means the reader remains alive, speaker identity never crosses users, non-consenting audio never reaches transcription, and captions continue through expected voice/DAVE transitions.

## Known limitation

If one participant's physical microphone captures another person, a television, game audio, or speaker output in the room, Discord has already encoded that sound as part of that participant's source stream. Dank Shield cannot perfectly separate those physical sounds after the fact and must not claim otherwise.
