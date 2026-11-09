# Voice Safety #517: private local Vosk + RivaL Intelligence

**Scope:** An optional staff-only, advisor-only voice-safety mode using Dank Shield's existing, *single* per-guild Discord DAVE receiver. Do not add a second voice receiver in Watch Yo Mouf. Watch is a private speech-recognition worker. It never joins the voice channel or sends Discord messages.

## Data path

1. Dank Shield's existing `CommunityVoiceCaptionManager` enforces one guild receiver, a visible notice, and per-speaker affirmative voice-specific agreement. Historic `server_membership_v2` records never authorize recording; joining a guild cannot create consent.
2. Only consented speaker frames reach `CaptionEngine`, which caps memory and segments by speaker. Local voice mode downsamples 48 kHz stereo PCM to 16 kHz mono and signs the bounded bytes with `DANK_WATCH_AUDIO_SECRET`.
3. Watch Yo Mouf's private `watchyomouf:8098/v1/voice-transcribe` verifies HMAC over the body, guild/user/channel, timestamp and one-use nonce. With `WATCH_YO_MOUF_STT_ENABLED=true` it transcribes through a local small Vosk model and returns only signed text. Offline, English only.
4. Dank Shield verifies the signed response. Its existing `VoiceSafetyAdvisor` sends bounded untrusted transcript text to RivaL Intelligence `rival-llm:8765/v1/moderate` using an independent voice-scoped token. Only high-label, context-relevant responses produce **staff review** alerts. No automated punishments; no raw audio/transcript database records.
5. Withdrawal, voice departure, or session stop invalidates pending work and must suppress any late alert.

## Deploy controls (default OFF)

Configure in the existing Discloud applications, never in GitHub commits or pasted chat:

| App | Name | Value |
|---|---|---|
| RivaL-Intelligence | `RIVAL_LLM_VOICE_ENABLED` | `true` |
| RivaL-Intelligence | `RIVAL_LLM_VOICE_TOKEN` | new 32+ random character voice-only token |
| Watch Yo Mouf | `WATCH_YO_MOUF_STT_ENABLED` | `true`, **only during an approved voice canary** |
| Watch Yo Mouf | `WATCH_YO_MOUF_AUDIO_SECRET` | new 32+ random-character audio-only secret |
| Watch Yo Mouf | `WATCH_YO_MOUF_STT_MODEL_PATH` | optional local Vosk model directory; leave empty for Vosk's cached English small model |
| Dank Shield | `DANK_COMMUNITY_LIVE_CAPTIONS_ENABLED` | `true` after real DAVE acceptance |
| Dank Shield | `DANK_VOICE_SAFETY_ENABLED` | `true` only with consenting test guild |
| Dank Shield | `DANK_VOICE_SAFETY_BACKEND` | `watch` |
| Dank Shield | `DANK_VOICE_SAFETY_LOCAL_ENABLED` | `true` only with a tested private worker |
| Dank Shield | `DANK_VOICE_SAFETY_ADULT_ONLY_CONFIRMED` | `true` only if deployment really qualifies |
| Dank Shield | `DANK_WATCH_AUDIO_SECRET` | identical to Watch's **audio** secret, never other tokens |
| Dank Shield | `DANK_RIVAL_VOICE_TOKEN` | identical to Intelligence's **voice** token |

All other caption endpoints keep the existing Gemini path and require `GEMINI_API_KEY`. Local Voice Safety explicitly does not require a paid Gemini account or key. Retain existing guild settings: selected VC, private staff-alert channel and public notice channel.

## Controlled acceptance

- Python 3.11/3.13 unit CI for both apps, wire-protocol vectors, replay/tamper, signed replies, 429/503 handling, interruption and consent-revocation tests.
- Confirm the Watch /watchyo worker state is ready **after** 768 MB host model warmup; verify RAM, CPU and restart behavior.
- Confirm a single active Dank Shield voice session; no Watch Discord voice connection. Confirm the source listener uses DAVE and Opus on a real call with member permission.
- Confirm the participant deliberately opts in, then revoke while audio is in flight. No post-revocation alert may publish. Historic automatic membership records are ineligible.
- Confirm staff channel is not accessible to @everyone. Ensure notices disclose local processing and no automated sanctions.
- Verify actual transcription on consenting test speech, then one local LFM3 response and staff advisory, handling 429 and overload safely.
- Measure real latency, false positives, and memory. Vosk small en-US is a starting point, not a proven multilingual safety recognizer.

**Unverified until real host canaries:** live DAVE decode/rekeys, Vosk accuracy, 768 MB Vosk memory budget, stream saturation, external service auth, and incident delivery. Keep all default-off gates disabled until those validations succeed.
