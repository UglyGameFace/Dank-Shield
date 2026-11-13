# Voice runtime incident, November 10, 2026

- Objective: restore the production gateway and verify voice receive through the separate Watch transcription worker without changing consent or provider configuration.
- Source: Dank Shield main `5e29bced`; last verified deployment submission `61bac6b` (run 42748146473); exact running build unavailable. Watch main `2f09b15`. No open repair PRs at the time of the check.
- Host evidence: diagnostic run 42825274944 confirmed Dank Shield offline, RAM-killed, exit 137, 756 MB live allocation, auto-restart off. Watch Yo Mouf was online at 768 MB and reported its local STT model ready. The recent Dank Shield log sample contained voice handshakes and Opus loss, without a matching application-initiated rebuild marker. The full voice-loop cause remains unverified.
- Recovery: run 42825665819 found only 530 MB available, so the 1,495 MB repository allocation could not be restored. Runs 42825787411 and 42825974137 received HTTP 409 from the 1,200 MB change. A fresh readback still showed offline at 756 MB. No successful host change, application start, merge or deployment occurred.
- Validation: 69 focused local tests and five subtests passed for captions, receive, Watch transcription and voice-safety policy. No live audio acceptance was possible while Dank Shield was offline.
- Cleanup: removed the temporary write-capable restore workflow; the separate read-only incident probe remains available on this diagnostic branch for follow-up. No production code changed.
- Next: inspect the signed-in Discloud dashboard RAM control, apply 1,200 MB within existing quota if the provider permits, start the existing app, and confirm uptime/RAM and a single consenting VC without unwanted disconnects.

## Recovery verified, 09:14 Eastern

- With the owner's approval, saved Watch Yo Mouf at **512 MB** and Dank Shield at **1,000 MB** in the existing Discloud apps. Both provider-triggered rebuilds completed and brought the apps online. No code or consent settings changed. The earlier 1,200 MB request was rejected by the provider before the approved split.
- Dank Shield logged a healthy metrics sync for all 10 guilds. No newer memory-kill event appeared after the 09:07 rebuild. The dashboard snapshot at 09:14 showed 144.38/1,000 MB for Dank Shield and 162.95/512 MB for Watch Yo Mouf; these are point-in-time readings, not peak capacity.
- Watch Yo Mouf logged local Vosk model readiness after rebuilding. From Dank Shield's Discloud container, the private worker health check passed, a synthetic silent PCM request returned HTTP 200 with a verified response signature, and the RivaL private synthetic text request returned HTTP 200 with advisory-only semantics. No member audio was used in those probes.
- The 09:07 Voice Safety auto-start reported only `VoiceReceiveUnavailable`; its exact reason was not recorded. A later check of the affected server's configured channels and bot permissions passed. Discord logged a completed voice handshake at 09:10, and a subsequent read of the bot's current voice state confirmed the configured VC. No new receive-rebuild marker was observed in the checked window. Long-running stability, live per-speaker transcription, and advisory delivery remain unverified.
- Temporary write-capable workflow was removed from the branch before dashboard recovery. The read-only workflow remains available for follow-up. Default branches and their source deployment manifests were unchanged.

## Historical evidence preservation (2026-11-13)

This document is a historical incident record imported from the diagnostic branch. The readings and deployment state above are point-in-time observations, **not** assertions about the current Discloud runtime. The read-only diagnostic workflow was integrated separately in PR #846. Do not use this record as authorization for new host, memory, credential, database, or deployment changes.
