# Voice runtime incident, November 10, 2026

- Objective: restore the production gateway and verify voice receive through the separate Watch transcription worker without changing consent or provider configuration.
- Source: Dank Shield main `5e29bced`; last verified deployment submission `61bac6b` (run 42748146473); exact running build unavailable. Watch main `2f09b15`. No open repair PRs at the time of the check.
- Host evidence: diagnostic run 42825274944 confirmed Dank Shield offline, RAM-killed, exit 137, 756 MB live allocation, auto-restart off. Watch Yo Mouf was online at 768 MB and reported its local STT model ready. The recent Dank Shield log sample contained voice handshakes and Opus loss, without a matching application-initiated rebuild marker. The full voice-loop cause remains unverified.
- Recovery: run 42825665819 found only 530 MB available, so the 1,495 MB repository allocation could not be restored. Runs 42825787411 and 42825974137 received HTTP 409 from the 1,200 MB change. A fresh readback still showed offline at 756 MB. No successful host change, application start, merge or deployment occurred.
- Validation: 69 focused local tests and five subtests passed for captions, receive, Watch transcription and voice-safety policy. No live audio acceptance was possible while Dank Shield was offline.
- Cleanup: removed the temporary write-capable restore workflow; the separate read-only incident probe remains available on this diagnostic branch for follow-up. No production code changed.
- Next: inspect the signed-in Discloud dashboard RAM control, apply 1,200 MB within existing quota if the provider permits, start the existing app, and confirm uptime/RAM and a single consenting VC without unwanted disconnects.
