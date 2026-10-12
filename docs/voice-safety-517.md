# Dank Shield Voice Safety #517: consented staff-review pilot

Status: **implementation under review; not deployed, not validated on live Discord/Discloud, and not claimed to outperform VoicePatrol.**

## Architecture and safety

Voice Safety is a new mode for the existing CommunityVoiceCaptionManager, **not a second Discord listener**. One guild runs at most one of Live Captions, Community Hub captions, or Voice Safety. Existing DAVE/MLS VoiceRecvClient, SSRC/member isolation, per-speaker frame admission, bounded audio, Gemini Live transcription and recovery remain authoritative.

The public /dank protection menu includes Voice Safety. Staff choose one monitored VC, a private staff-alert text channel hidden from @everyone, and a public text channel for disclosure. Setup is disabled by default; an operator must pass the DAVE soak and enable both host gates. Once configured and host-enabled, Dank Shield will start the receiver when an occupied configured VC generates a join event, and will scan currently occupied voice rooms on bot ready for bounded recovery. Staff can also start it ahead of time with Start Review to avoid startup latency on first speech. Automatically started sessions release their slot when the last human leaves; manual sessions run until staff Stop.

**Once per guild, not once per VC session:** for Basic Verify, a newly refreshed panel clearly discloses AI voice processing and uses an **Agree & Verify** button. Only a successful interaction with that disclosed panel records a versioned agreement in the existing guild/member settings store. Staff-granted roles, old verification panels, and a rules page alone never silently create that record. Members verified earlier can use **Agree Once: Voice Safety** on the public notice. **Opt Out** withdraws that agreement; no new audio is admitted. A fresh stored consent check must succeed to auto-admit someone on VC entry or bot session start, otherwise they remain unmonitored. Leaving a VC removes active audio admission but does not erase the member's saved agreement. No new database table or voice receiver is introduced.

If consent storage is unavailable, admission fails closed. If an opt-out write fails, that guild's active review is stopped and staff must restore persistence before continuing. An earlier accepted record could still exist in storage until a successful withdrawal, so staff must not restart monitoring until the privacy failure is reconciled.

Only consented speakers inside that VC are transcribed. An operator-approved paid Gemini API project receives audio for speech-to-text, then a second bounded Gemini text request classifies possible conduct. Staff receive **advisory incident notices**, never automatic punishment or verbatim transcript storage. Staff must investigate. Incidents remain as Discord messages in the chosen private channel. The classifier validates output, abides by rate limits, and ignores ambiguous/failed results. No raw audio or transcripts are stored by the bot to disk or database.

**Important limitation:** a member who declines or has not yet acknowledged voice monitoring is not monitored; this does not prevent spoken words from being heard. The pilot reviews only one selected voice room per guild and shares caption session/resource caps; auto-start cannot provide zero-latency capture immediately on first join. This is **not** complete, server-wide voice moderation. Human moderators must still intervene.

## Diagnosing the "Host is validation-locked" message

The staff-only Voice Safety panel now shows five configuration indicators separately:
`DANK_COMMUNITY_LIVE_CAPTIONS_ENABLED` (DAVE captions operator switch),
`DANK_VOICE_SAFETY_ENABLED` (Voice Safety operator switch),
`DANK_VOICE_SAFETY_GEMINI_PAID_CONFIRMED` (operator reviewed paid API project),
`DANK_VOICE_SAFETY_ADULT_ONLY_CONFIRMED` (operator verified adult-only service),
and `GEMINI_API_KEY` (nonempty, without ever showing its value).
An unchecked indicator means that exact deployment setting is not true/present.
These indicators do **not** prove voice reception, paid Google billing, active
credits, account permissions, or age. Those require separate real-world checks.

Google AI Pro subscriptions and Discloud hosting tiers are not evidence of
a paid Gemini API project's data-processing terms. Check the API project's
billing tier in Google AI Studio. Voice Safety intentionally remains blocked on
the unpaid Gemini API tier for sensitive speech processing. Server rules or a
Discord Verified role cannot prove all members are adults.

After legitimate validation, set the applicable Discloud variables using its
secret/env settings and restart the deployed application. Do not post
`GEMINI_API_KEY` in Discord, GitHub or diagnostics. Then open a fresh
`/dank protection → Voice Safety` panel and check both the host status and
voice receive readiness before attempting Start Review.

## Host activation, only after live DAVE checks

1. Review applicable monitoring/consent law, Discord voice policies, and the Gemini paid API data-processing agreement and adult-only usage restrictions; disclose the service to members. Configure GEMINI_API_KEY securely on Discloud.
2. Complete the real DAVE receive tests in docs/LIVE_CAPTIONS_VOICE_STACK.md in a volunteer guild. Do not assume passing unit tests proves audio capture.
3. Only after the soak succeeds, enable DANK_COMMUNITY_LIVE_CAPTIONS_ENABLED=true and DANK_VOICE_SAFETY_ENABLED=true **plus** DANK_VOICE_SAFETY_GEMINI_PAID_CONFIRMED=true and DANK_VOICE_SAFETY_ADULT_ONLY_CONFIRMED=true in Discloud environment and restart. These independent fail-closed gates are operator confirmations, not an automatic billing/age verification. Do not set them merely because server rules mention monitoring.
4. In /dank protection > Voice Safety, press **Monitored Voice Channel**, **Private Staff Alerts**, or **Public Member Notice**. These open the existing **Dank Shield Search-Safe resource browser**, not Discord's native channel selector. Browse all text or voice channels in 25-item pages and use 🔎 Search for styled names, plain names, saved previous names, channel IDs or mentions. Only the original staff user can make a selection. When choosing a channel, Dank Shield rechecks channel type, @everyone visibility, staff visibility and bot permissions; unsafe picks are refused without changing stored settings. **Back** returns to the same private Voice Safety manager. Press Enable Setup only after selecting a monitored VC, private staff alert channel and public notice channel. Verify Discord channel permissions. **Refresh the existing Basic Verify panel** with /verify panel so its Agree & Verify disclosure is visible; older panels must not silently count as agreement. Staff may press Start Review immediately to preconnect, or leave it armed to auto-start when an occupied configured VC becomes active.
5. New test members accept the updated Verify panel once; already-verified volunteers use Agree Once on the notice. Join/rejoin the chosen VC without extra clicks and confirm admission only for acknowledged members. Test allowed and prohibited sentences, speaker attribution, mid-utterance Opt Out, receive reconnect, restart, staff alerts, Stop and ended notice.
6. Automatic startup resumes only configured, **occupied** VCs after a bot reconnect and respects the existing global caption/voice session caps (default 4) and DAVE/Gemini readiness. Pressing staff Stop also saves the guild setting as disabled so auto-start will not reactivate it. A config-write outage cannot guarantee a persistent disable, so disable the host gate when such an error is reported. Consent persists per guild until withdrawn.

## Validation needed before production

- Targeted pytest tests/test_voice_safety_policy.py, existing caption/receive regressions, full CI, compilation and diff hygiene.
- Mocked tests for protected identity conversation, sexually explicit descriptions, quotations, educational discussion, targeted remarks, threats, unclear/noisy audio, speaker isolation, interruption, opt-out mid-request, repeated alerts, provider errors, persistence failures, public alert misconfiguration, and existing captions compatibility.
- Live DAVE checks with explicit volunteer consent: 2+ speakers, overlapping speech, voice reconnect, MLS epoch transition, model API quota, Discord rate limits, channel deletion/permission loss, bot restart, same-guild captions conflict, and bounded sessions.
- Measure per-category precision/recall, false accusation rate, speaker attribution errors, P50/P95 detection-to-alert latency, CPU/RAM per VC, and cost per voice-hour. Compare to VoicePatrol only with ethically sourced equivalent data. No superiority claims without benchmarks.

## Known constraints

AI triage is **advisory**, not calibrated legal or factual evidence. Do not automatically mute/ban based on it. Identity, LGBTQ+ status, orientation, and ordinary nonexplicit conversation are not violations. Transcription uses Gemini rather than local Whisper in this pilot. Runtime session limits and Discloud/Google quotas apply, with no guarantee of 10,000 active monitored guilds.
