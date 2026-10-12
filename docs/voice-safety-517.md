# Dank Shield Voice Safety #517: consented staff-review pilot

Status: **implementation under review; not deployed, not validated on live Discord/Discloud, and not claimed to outperform VoicePatrol.**

## Architecture and safety

Voice Safety is a new mode for the existing CommunityVoiceCaptionManager, **not a second Discord listener**. One guild runs at most one of Live Captions, Community Hub captions, or Voice Safety. Existing DAVE/MLS VoiceRecvClient, SSRC/member isolation, per-speaker frame admission, bounded audio, Gemini Live transcription and recovery remain authoritative.

The public /dank protection menu includes Voice Safety. Staff select one voice room to review, an existing **private staff-alert text channel** hidden from @everyone, and an existing **member-visible public text channel** for disclosure and consent. Staff explicitly enable setup and start review. A notice is posted with persistent **Opt In: Review My Voice** and **Opt Out** controls. Consent is explicitly granted **per active session**, separate from Live Captions consent, and stays in memory only. No member consent is written to Supabase or a new table. Leaving the monitored VC, pressing Opt Out, or stopping the session revokes admission, clears context and purges pending audio as supported by the existing engine. A new session always requires a new Opt In.

Only consented speakers inside that VC are transcribed. Google Gemini Free Tier receives audio for speech-to-text, then a second bounded Gemini text request classifies possible conduct. Staff receive **advisory incident notices**, never automatic punishment or verbatim transcript storage. Staff must investigate. Incidents remain as Discord messages in the chosen private channel. The classifier validates output, abides by rate limits, and ignores ambiguous/failed results. No raw audio or transcripts are stored by the bot to disk or database.

**Important limitation:** an offender who declines to opt in is not monitored; this does not prevent spoken words from being heard. The pilot reviews only one voice room per guild and shares caption session and resource caps. This is **not** complete, server-wide voice moderation. Human moderators must still intervene.

## Host activation, only after live DAVE checks

1. Review applicable monitoring/consent law, Discord voice policies, and Google Gemini Free Tier data processing; disclose the service to members. Configure GEMINI_API_KEY securely on Discloud.
2. Complete the real DAVE receive tests in docs/LIVE_CAPTIONS_VOICE_STACK.md in a volunteer guild. Do not assume passing unit tests proves audio capture.
3. Only after the soak succeeds, enable BOTH DANK_COMMUNITY_LIVE_CAPTIONS_ENABLED=true and DANK_VOICE_SAFETY_ENABLED=true in Discloud environment and restart. Independent fail-closed gates.
4. In /dank protection > Voice Safety, select the monitored VC, private staff alert channel and public disclosure channel. Verify Discord channel permissions and press Enable Setup, then Start Review.
5. Volunteers join the chosen VC, then press Opt In from the published notice. Test actual allowed and prohibited example sentences, speaker identity, mid-utterance Opt Out, leaving/rejoining the VC, receive reconnect and restart. Verify staff alerts, revocation, Stop, and the notice marking itself ended.
6. Disable either host gate to suspend new sessions. Consent and monitoring are per-session only; neither auto-restarts.

## Validation needed before production

- Targeted pytest tests/test_voice_safety_policy.py, existing caption/receive regressions, full CI, compilation and diff hygiene.
- Mocked tests for protected identity conversation, sexually explicit descriptions, quotations, educational discussion, targeted remarks, threats, unclear/noisy audio, speaker isolation, interruption, opt-out mid-request, repeated alerts, provider errors, persistence failures, public alert misconfiguration, and existing captions compatibility.
- Live DAVE checks with explicit volunteer consent: 2+ speakers, overlapping speech, voice reconnect, MLS epoch transition, model API quota, Discord rate limits, channel deletion/permission loss, bot restart, same-guild captions conflict, and bounded sessions.
- Measure per-category precision/recall, false accusation rate, speaker attribution errors, P50/P95 detection-to-alert latency, CPU/RAM per VC, and cost per voice-hour. Compare to VoicePatrol only with ethically sourced equivalent data. No superiority claims without benchmarks.

## Known constraints

AI triage is **advisory**, not calibrated legal or factual evidence. Do not automatically mute/ban based on it. Identity, LGBTQ+ status, orientation, and ordinary nonexplicit conversation are not violations. Transcription uses Gemini rather than local Whisper in this pilot. Runtime session limits and Discloud/Google quotas apply, with no guarantee of 10,000 active monitored guilds.
