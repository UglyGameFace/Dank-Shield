# Dank Shield Active Task

## CURRENT ACTIVE TASK — CINEMA-AUTO-SOURCE-RESULT-WINDOW-20261010

**Outcome:** Ensure high-seed HEVC/MKV results do not crowd lower-seed, potentially browser-compatible releases out of the existing per-provider selection window for Dank Cinema automatic playback.

**Branch:** `fix/cinema-auto-source-compatible-results-20261010` from `aaa-cinema-pre-pregateway-bind@86d1a4f1dcee575b17d61597903fdf4d2e200a2e` (PR #492 merged). No production change or Discloud deployment.

**Status:** Implementation and focused regression tests committed on isolated branch. Exact-head CI, full relevant validation, live candidate evidence and signed playback/first-frame acceptance pending.

### Root cause / execution
- Provider JSON normalization cut to the first 25 raw entries in `media_source_resolver._search_one`, before Cinema knew video risk. RSS extraction also stopped at 25.
- An index sorted by reported seeds can thus provide 25 HEVC/MKV variants while a potentially compatible H264 result in the same response is never passed to the existing automatic chooser. Site Play/Resume then receives only risk-2 candidates and PR #490's correct preflight refuses all instead of starting known-risk video.
- Actual Discloud running SHA, real Carrie source metadata, and existence of a compatible release have not been independently confirmed. No claim a suitable source always exists.

### Scope and changes
- Keep per-source output 25, total output 100, bounded response bytes, SSRF-safe provider fetching, exact title/episode identity, adult rules, source preferences, lease/host authority and manual override unchanged.
- Inspect at most 100 already returned items per provider, prioritize risk-0/risk-1 entries ahead of risk-2 using the *same* helper as room ranking, maintain original order within risk class.
- Move existing risk scoring from `MovieSourceVariant` to `media_metadata.browser_video_risk_key` with a delegating method. This prevents duplicate conflicting codec policies.
- Expand RSS iteration lookahead to the existing 100-result bound without adding HTTP retries or extra provider requests.
- Regression tests: candidate after slot 25 survives in JSON; stable ordering and upper bound; RSS candidate after slot 25 survives; existing source preflight and source normalization suites remain authoritative.

### Validation / blockers / next step
- Source editing done through GitHub connector; local checkout unavailable (container has no GitHub network route).
- Await exact-head CI and diff review, including existing media metadata, room ranking, source resolver, Cinema and browser playback suites.
- Retest automatic episode and movie selection against actual provider data. Manual source choice and prior known-risk HEVC fail-closed semantics must remain unchanged. Verify on Android and a second browser to first frame/audio before claiming end-to-end playback.
- This task does not enable HEVC transcode, guarantee a compatible source exists, change RSS provider configuration, or fix unrelated A/V or website failures.
- Next: Open scoped PR into actual Discloud auto-deploy branch, run checks, review diff. Do not merge/deploy before validation.

## PRIOR CHECKPOINT — CINEMA-LEGACY-PLAYBACK-GLITCH-TRUTH-20261009

**Production baseline (verified):** Discloud auto-deploy branch aaa-cinema-pre-pregateway-bind at 48f4053ed329e84ec18b45728241c6ca6a844ccf; PR #490/#491 merged and deployed. Forked fix/492-cinema-playback-state-legacy-loop-20261009 from this exact SHA. No change to production until PR CI and real device validation.

**User evidence:** Oct 9 ~11:24 PM Eastern, Samsung Internet The Theater / Mad Max: Fury Road. Torrent 100%, 0 B/s, seeds/leechers labeled "reported" 421/49, media verified H264 + MP4, browser canPlayType says probably, JS health says "Video playing" while timing says "Video paused • embedded audio" and scene is black/glitchy. Browser startup metadata 358ms, playing event 9.09s, initial frame callback 9.34s. Server first request and first byte **both** around 282.7 seconds after session origin: does not prove 282 seconds of download latency; browser request occurred late. 100%/0B/s can be a completed torrent, not failure. Reported seeds are provider counts, not live connections.

**Confirmed old-code conflicts:**
- streamHealthLabel is based on s.state intent + video.readyState + past first_frame, and pause events refresh some controls but not Stream Health. "Video playing" may be a stale label while the HTML video is paused.
- Browser without requestVideoFrameCallback could mark first_frame simply because currentTime increased, falsely claiming actual rendered video.
- applyState host polls every 2 sec and awaits video.play() while a promise can remain pending during buffering; multiple state responses may overlap rather than coalesce.
- Ordinary HTML video play/pause events call hostAction(resume/pause) even when caused by player recovery, remoteApply timing races or involuntary media events. The custom buttons already handle explicit user actions; PiP/native fullscreen interactions need separate treatment.

**Fix:** Only call host play once while pending; never await it in state reconciliation; keep explicit Play user gesture and native PiP/fullscreen host controls; guard involuntary media events from mutating shared host state; base health on actual paused/readyState and track ongoing requestVideoFrameCallback frames, including frozen playback after a 5s gap; timeupdate without frame callback reports clock advancement, not a confirmed video frame. Preserve signed source, torrent byte routes, cast, FFmpeg AAC, watch-party clock, role permissions and per-guild user audio.

**Validation:** New Node regression executes rendered Theater JS with unresolved Promise, multiple simulated polls, media pause/play event forwarding and active vs stalled video health. Existing stale tests updated to assert removal of old echo. CI, source/security checks, and actual Samsung Browser (first frame, 2min continuous video + audible embedded/AAC audio, seek 1min, pause/play, background restore) required. No claim of universal browser compatibility until real testing. Examine whether overlapping viewer play promises and CDN source range performance remain concerns if tests show glitches.

**Single active task only:** Do not switch to Basic Verify, Dr STONE, or unrelated Dank Shield projects without explicit FORCE SWITCH.

## PRIOR CHECKPOINT — CINEMA-UNSUPPORTED-AUTO-SOURCE-PREFLIGHT-20261009

### Browser-settings resilience, same active Cinema task, stacked companion PR #491

User approved handling browser dark/forced colors, accessibility and text sizing, Data Saver, reduced motion, mobile/desktop-mode layout, background suspension, network reconnect, audio permissions, PiP/fullscreen and strict browser privacy. Opera/Opera GX, Chrome, Edge, Firefox, Safari, Samsung Internet, Brave, Vivaldi and other modern browsers are targets, not all certified by real playback.

Existing correct code already implements signed video/audio, per-viewer language, prefers-reduced-motion, Auto/High/Standard/Lite effects, Data Saver and hardware hints, ResizeObserver/viewport/orientation recovery, fullscreen/PiP capability detection, codec advisories, user-gesture audio recovery, offline/page-wake, background-gated polls and private no-store authenticated HTML. Preserve it, don't duplicate it.

Companion branch fix/491-cinema-browser-settings-resilience-20261009 is based on PR #490. It adds forced-color and higher-contrast support without recoloring actual video/poster imagery; observes local non-identifying forced contrast, reduced motion, Save-Data, network and visibility changes and re-evaluates decorative Auto quality only; gives Advanced Details an environment readout; and debounces pageshow/visibilitychange/online recovery bursts to prevent repeated media restarts. Never read the user's extensions, fingerprints, cookies, DRM, hardware acceleration or battery health. Cannot bypass actual autoplay/security rules. New rendered-browser JavaScript tests exercise the actual functions. Existing #490 source-risk test fixture was also updated in both branches after exact-head CI identified one obsolete mock contract.

No codec transcode changes, permissions, torrent, host/viewer, source/ranking, signing, database or Discord changes in this companion. Keep both PRs draft until CI and actual cross-browser A/V tests. An HTML page opening in Chromium is not validation of Opera GX, Safari, Firefox or real video/audio decode.

**Reason:** At 8:32 PM Eastern, Mad Max: Fury Road still had a black video element after automatic source selection. Advanced Stream Details now positively identify `hevc / video/matroska` and Samsung Browser's `canPlayType` reports no native decoder. User asks why Cinema auto-selected the release and why FFmpeg cannot support it.

**Verified production baseline:** `aaa-cinema-pre-pregateway-bind@09affbf295a0642605aa7eb4c0532925507268cd`. PR #489 merged and Discloud status success Oct 10 00:16:18 UTC. New branch `fix/490-cinema-unsupported-source-preflight-20261009` forks this exact production SHA; do not overwrite other production features.

### Root cause and focused prevention

- `cinema_site` materializes provider results and calls `select_preferred_variant` **before torrent starts**. `MovieSourceVariant.browser_video_risk_key` added in #489 only used `metadata.verified.video`; provider search metadata deliberately sets `source_reported_verified=False`, and FFprobe runs asynchronously *after* torrent pieces arrive. Thus most releases were scored `1 = unknown` before launch even if `release_name.video_tags` contained HEVC/x265. A remembered preferred provider could override the ranking entirely. Later browser detection only warns after the source was committed.
- New ranking rejects only **high-confidence negative release-name/container clues** (HEVC, x265/H265, .mkv, .avi, .mpeg/.mpg). Such clues never prove that another source is safe. Unmarked releases still remain unknown until a real probe, and Android-specific capabilities cannot be inferred from server-side filename alone.
- `select_preferred_variant` excludes risk-2 variants from **automatic** site and next-episode selection, including a dangerous preferred provider. If all releases are risky, it fails with a useful compatibility error rather than knowingly starting a black screen. Explicit host-picked variants and original room authority/signatures are unchanged.
- Targeted regression tests cover HEVC/x265, unsafe containers, unknown-versus-verified risk, preferred-provider override, only-unsupported-source fail-closed.
- **This is not a universal video conversion claim.** HEVC-to-H264 requires decode/re-encode; changing MKV to MP4 without conversion cannot make an unsupported HEVC decoder work. Discloud shows 1.46 vCPU, 1.46 GB RAM with peaks of 80% CPU and 85.9% RAM; enabling unbounded FFmpeg video workers endangers Discord bot stability. Do not activate transcoding without measured bounded concurrency and real browser evidence.

### Acceptance

- New exact-head CI, source-choice tests, final security/diff review; no unrelated changes.
- On Android test auto source ranking for multiple candidates, all-risky sources refusal, explicit manual behavior, per-guild language and AAC sync unchanged. When provider metadata hides codec, later probe/browser diagnostic must still surface issue; no false universal-playability claim.
- **Still open:** reliable audio synchronization and source-specific HEVC video fallback. Do not mark Cinema broadly fixed solely on source preflight or CI. Keep prior verification/Dr STONE work suspended.

### Prior history

## HISTORICAL CHECKPOINT — CINEMA-AUDIO-LANGUAGE-AUTOPLAY-TRUTH-20261009

**Scope:** Continue active Dank Cinema AAC audio stability task after Oct 9 7:12–7:13 PM Eastern user screenshots. Issues: incorrect movie heading ("Portuguese 5.1" for Mad Max artwork), English 7.1 track switching, distracting Audio permission button, black video with "Video playing" badge, automatic release selection, and language preference per user per guild. No FORCE SWITCH to another project.

**Confirmed Discloud baseline:** aaa-cinema-pre-pregateway-bind @ e8773215fb29654decdc7baf8c3dfbf8481eb174. PR #488 merged and deployed successfully at 23:08:44 UTC Oct 9 before screenshots. Branch fix/489-cinema-audio-language-title-truth-20261009 is based on this production SHA.

### Confirmed code issues and scoped changes

- _state_payload serialized room candidate title despite canonical catalog movie_metadata. Prefer canonical movie title to release tags.
- Verified audio track metadata has language and title. Existing sidecar selection didn't persist preferred language (only native audio selections saved GLOBAL default_audio_language). Add per-user/per-guild preference in existing Cinema user profile JSON, bounded by authenticated room guild ID. Respect viewer-only selection; never change shared room media just to honor one viewer's language.
- Start selected AAC track within real user gesture; browser may still require gesture on restricted autoplay, so show recovery only when genuine playback block is reported.
- Stream health said "Video playing" before already-tracked first_frame video callback. Display honest waiting-for-frame status and limited, safe video/AAC clock/readiness diagnostics in Advanced Stream Details to enable empirical live debugging.
- MovieSourceVariant browser_audio_risk_key marked mixed AAC/DTS audio safe just because any stream was AAC, whereas actual browser_audio_compatibility requires sidecar if any codec is unsupported. Align ranking; add deterministic mixed-codec test. Do not change seed/quality weights blindly.
- User reports audio skipping even with healthy torrent download speed. Screenshots do not prove exact FFmpeg/decoder/sync fault. The new readout allows checking video/audio clocks, stalls and AAC error without exposing signed media URL. No claim of audible sound recovery without live multi-minute tests.

### Acceptance and safety

- Exact-head GitHub CI pending, tests for per-guild isolation and language matching, mixed-codec ranking, title truth and audio recovery only on browser failure.
- Before merge: examine complete diff; validate correct title, English selection, no permission prompt during normal Play, correct first frame, audio/video synchronization after seek and buffering, Android plus another browser, host and viewer. No torrent engine/room permissions/auth changes.
- Stay on this single Cinema audio task. Dr. STONE HTML-JSON issue and Dank Shield self-verification remain suspended with prior checkpoints below. Preserve PR #487 and #488 already deployed; do not merge main blindly.

---


## HISTORICAL CHECKPOINT — CINEMA-AAC-AUDIO-STABILITY-20261009

### October 9 continuation: AAC skipping / time drift after PR #487 deployment

**Status:** Investigating and validating. PR #487 merged as `e4d0846a65b7e65fae776842f319ef3c6d9b8d7b` and Discloud `discloud/commit` confirmed successful Oct 9 22:03 UTC. New audio drift/skipping complaint is on that running production baseline. The previously fixed audio recovery UI is still part of the active task. No FORCE SWITCH was issued.

**Branch:** `fix/478-cinema-sidecar-av-sync-20261009`, forked directly from confirmed Discloud branch `aaa-cinema-pre-pregateway-bind@e4d0846a65b7e65fae776842f319ef3c6d9b8d7b`. Production remains untouched.

**Concrete code path and reproducible defect:** The HTML5 video emits `waiting` or `stalled` while `video.paused` may be false; the deployed handlers merely changed the notice text and did not pause the independent AAC sidecar. Its `currentTime` could therefore advance while video was buffering. The deployed `syncCompatAudio` then interpreted resulting drift greater than 2.5 seconds as a reason to discard the AAC source and launch a new FFmpeg transcode every eight seconds. Those source replacements can produce audible jumps. This is grounded in code, but the specific user's live media timing and FFmpeg output have not yet been inspected.

**Scoped proposed behavior:** Treat video readiness, seeking, and buffering as authoritative for advancing the sidecar. Pause AAC on video `waiting`, `stalled` and `seeking`; resume only with a playable video clock. Eliminate periodic FFmpeg-source reloads for ordinary drift and use bounded audio playback-rate corrections. Preserve explicit seek re-anchor and per-viewer gesture recovery. Keep torrent provider, permissions, session signing, and audio-track routes unchanged.

**No speculative throughput change:** An experimental change from FFmpeg `-re` to `-readrate 2` was immediately reverted before PR creation because no session evidence indicates faster-than-normal playback. Final branch should have no changes in the audio transcode backend.

**Regression validation:** New `tests/test_cinema_av_clock_sync.py` executes actual rendered Theater handlers under Node to reproduce unpaused video stalls, prevent audio advancement, verify video recovery without source reload, test drift correction and one explicit seek re-anchor. Exact-head CI and live Android/desktop audible first-frame, stable speech sync over multiple minutes, seek and buffering, host/viewer are still required. Do not claim real playback fixed from mock tests alone.

**Next:** Open scoped draft PR, run exact-head CI. Acquire live signed-session reproduction or safe browser diagnostics (video/audio clock, readyState, stalls, FFmpeg errors) before merging/deploying. If actual timing differs from the code-proven failure, revise the solution rather than layering timeouts.

**Outcome:** Restore usable AAC compatibility audio and consistent per-viewer recovery controls without disrupting video, room synchronization, privacy, or existing source selection. Reported Carrie S01E01 video plays while audio is silent; the Enable audio control appears/disappears between adjacent browser screenshots.

**Actual production branch baseline:** `aaa-cinema-pre-pregateway-bind@70356d1560e8047ddc1dc3dd9f698ee323f5c2ce`, confirmed Discloud successful commit status on Oct 9 UTC. Source differs from `main`: PR #483 added source-aware audio tracks and the Enable audio button to the production branch, not main. Do not substitute main or overwrite unrelated production work.

**Implementation branch:** `fix/478-cinema-aac-audio-stability-20261009` based on that actual deployment branch.

### Investigation / execution path / root causes

- Movie Night `_state_payload` publishes verified audio tracks and per-viewer signed AAC compat URL. Audio sidecar runs `ffmpeg` from the signed torrent stream via `/media/torrent/audio/{token}/{filename}`. Theater `applyCompatAudioState` loads a hidden audio element while keeping canonical video clock; `startCompatAudioFromGesture` requests playback on a user click.
- PR #483's `refreshAudioPermissionControl` tied visibility to transient `compatAudio.paused` and `video.paused`, not durable audio permission state. So the recovery row flickers during background playback transitions even while its browser-blocked message remains.
- `syncCompatAudio` continually checked video-versus-audio drift and reloaded the AAC stream after a 750 ms gap, including while its audio `play()` was still pending for buffer or permission. That can interrupt play promises, cause repeated FFmpeg work, and prevent stable audio start. The original sidecar-error path cleared the signed URL and mode, so the next state poll could recreate a broken stream.
- Current screenshots prove video first frame and audio warning, but do not prove the underlying FFmpeg emitted decodable AAC or actual browser permission state. Live signed browser/Discloud test remains required.

### Scoped changes

- Keep the AAC recovery button visibly available while that audio path is active and the viewer has not intentionally muted; label it Enable audio when paused/blocked and Restart audio otherwise.
- Track in-flight audio starts and actual gesture recovery so polling never interrupts startup or repeatedly retries a blocked `play()`. Avoid destroying a loaded audio source on each user click. Retire outdated async completion paths via sequence identity.
- Require the audio element to have playable data and a meaningful drift before automatic restart, paced by an existing-start timestamp.
- If an original AAC sidecar reports `error`, preserve the signed mode and require explicit user retry instead of restarting on every state poll; preserve verified alternate-track fallback to original.
- New `tests/test_cinema_aac_recovery.py` runs the rendered Theater's JS behavior under Node and checks the original-error path; existing Movie Night and torrent tests still apply.

### Validation and remaining blockers

- CI and targeted suite: pending exact-head pull-request workflow.
- Live audible result for Carrie S01E01, another movie/episode, host and viewer, Android Samsung Browser and at least one other engine: **not verified**. Do not equate `Video playing` or an AAC `play()` Promise resolving with audible output.
- No source search, provider registry, catalog, bot verification, Discord interactions, DNS, auth, storage, or room-mode code touched.
- Before merge: exact-head CI, inspect regression failures, compile/static checks, cleanup and final diff. Production deployment only to the confirmed branch after checks; user must validate real sound.

### Suspended tasks / backlog

- **Dr. STONE episode playback HTML-vs-JSON:** PR #486 merged to `main@9a7b543abc0a5e205db46616cf12b50a8c6053fd` with all six CI families green. Discloud was not confirmed running that commit because it deploys a different branch. Live authenticated exact-episode first frame and HTTP response status/origin remain unverified. Suspended by exact FORCE SWITCH on Oct 9; do not use an unrelated branch merge to import it.
- **Dank Shield self-verification:** investigation suspended earlier. Basic Verify immediate-ack vs advanced verification-ticket mode and the currently saved guild configuration require log-based production validation; no fixes were made.
- Separate poster/anime metadata, feed discovery, general torrent throughput and episode provider compatibility are not part of this AAC fix.

**Next step:** Run browser-behavior regression and full exact-head CI, inspect final diff, then canary production on the existing Discloud branch. If audio remains silent with controls working, collect signed AAC response status/bytes, FFmpeg output and browser media errors to diagnose the separate server/codec path without guessing.

---


## HISTORICAL CHECKPOINT — DANK-SHIELD-GLOBAL-INTERACTION-OUTAGE

**Outcome:** Restore Dank Shield process stability and Discord responsiveness across all guilds. Cinema feature work remains inactive until the shared production process is stable.

**Status:** Current evidence points to a post-ready Discloud aggregate Discord REST shutdown during restart recovery. A focused timing mitigation is in validation; production acceptance is not yet passed.

**Current production evidence:**
- The Oct 7 01:53 boot loads the Discord token and Supabase, binds the site, connects Gateway shard 0, registers commands/views, and reaches healthy on_ready at about 176 MB RSS.
- Departed-member recovery completes across all nine guilds.
- The latest exported runtime log then stops at the first `activity_restart_reconcile` slot acquisition at 01:54:09. The log was downloaded roughly twelve minutes later, so the absence of the normal process-health heartbeat after that boundary is consistent with a hard host termination.
- The repository has a prior confirmed Discloud incident (Sep 21, fix commit `6e6f2bc728027518906b05fa878b4eb41b40f986`) where startup Discord REST recovery caused `App shutdown - Rate limit exceeded: 301/300 req/30s`.
- Current activity-history recovery still defaults to starting only 20 seconds after on_ready, inside the same 30-second provider window as panel/member/invite/startup REST work.
- PRs #471/#472 removed eager libtorrent initialization from bare site startup. Production still went offline afterward, so Cinema/libtorrent startup was a real defect but not the complete outage cause.
- One earlier rebuild also lacked `DISCORD_TOKEN`/Supabase environment values; later boots load them correctly, so that separate deployment-env failure is not sufficient to explain the current post-ready crash loop.

**Active branch:** `fix/defer-activity-recovery-outside-discloud-startup-window-20261007`

**Focused change:**
- Keep Gateway, slash commands, components, live activity listeners, moderation, and the website immediately available.
- Move restart-gap Discord history reconstruction from the 20-second default to 75 seconds after on_ready, outside Discloud's initial 30-second aggregate REST window.
- Keep the existing bounded/single-flight recovery budget and fail-closed activity semantics.
- Document the production value in `.env.example` and regress the startup delay contract.

**Validation gate:** Exact-head CI must be green before merge. After deployment, production must stay online beyond the previous 20–30 second crash boundary, emit process-health heartbeats, then run activity recovery without a Discloud crash. Only after that should fresh `/dank home` and persistent panels be canaried.

**Do not:** rotate the Discord token again, restore the removed custom domain, add duplicate interaction callbacks, or resume unrelated Cinema feature work while this incident is active.

**Next step:** Open the focused PR, run exact-head CI, merge only if green, then inspect the same-SHA Discloud boot through the 75-second recovery boundary.

---

## Historical task notes (retained)

## Active task / outcome

**DANK-CINEMA-CUSTOM-DOMAIN-ORIGIN — keep the Discloud Site origin reachable before Discord ready**

Production baseline:
`main@7fdc3312f16dae869e1ccbe36ef2c66383b0e2c4` (PR #467 merged).

Active branch:
`fix/cinema-site-pre-gateway-bind`

Outcome:
`stoneyverify` must bind the canonical Dank Cinema public listener on `0.0.0.0:8080` during Discord's native `setup_hook`, before gateway/on_ready work can delay the process. This keeps both `stoneyverify.discloud.dev` and the verified custom domain `cinema.the420lobby.com` backed by a live origin while preserving the single shared Dank Shield/Cinema process.

## Scope

- Preserve the existing single `TYPE=site` deployment and canonical media/Cinema runtime.
- Do not create a second Discord bot process or a duplicate Cinema backend.
- Start only the existing `torrent_media_server` earlier in the same event loop.
- Preserve the current `on_ready` path as a retry/fallback.
- Do not change DNS, source selection, playback startup, audio, feeds, or unrelated Cinema behavior.

## Status

**Root cause confirmed from production logs; implementation and regression test are on the branch. Exact-head CI and Discloud canary remain.**

## Findings / root cause

1. Production successfully loads the Discord token and connects shard 0 to the Discord gateway.
2. The expected `🎞️ Torrent media server started on 0.0.0.0:8080` line never appears.
3. `stoney_verify.app.on_ready` is the only production caller of `_start_torrent_media_server_once()`.
4. Therefore the Discloud `TYPE=site` listener is gated behind Discord `on_ready`.
5. Discloud presents the origin as maintenance/offline while port 8080 is unbound, and the process can be recycled before the web listener ever becomes healthy.
6. DNS for `cinema.the420lobby.com` has already verified successfully, so DNS is not the active failure.

## Execution path

Before:
`main.py -> bot.run() -> Discord login/gateway -> on_ready -> start_torrent_media_server() -> 0.0.0.0:8080`

After:
`main.py -> bot.run() -> native setup_hook -> start_torrent_media_server() -> 0.0.0.0:8080 -> command cleanup/gateway -> on_ready fallback`

## Changes

- `stoney_verify.command_runtime._DankCommandOwnerMixin.setup_hook` now starts the existing Cinema/media listener immediately on the Discord client's live event loop, before stale command cleanup and gateway readiness.
- Failures in the early start are logged but do not block Discord startup; the existing `on_ready` path can retry.
- `stoney_verify.app._start_torrent_media_server_once` recognizes an already-ready pre-gateway listener and does not start a duplicate server.
- Added a regression test asserting media bind happens before command cleanup in `setup_hook`.

## Validation / results

- Static execution-path inspection confirms the only prior production start occurred in `on_ready`.
- Production logs confirm Discord gateway connection without any media-listener startup line.
- Regression test added on branch.
- Exact-head GitHub Actions: pending.
- Live Discloud `/health`, custom-domain root, OAuth callback/login, and Android browser canary: pending deployment.

## Cleanup / conflicts

- No second HTTP server, room model, provider stack, Discord client, retry loop, compatibility shim, or duplicate deployment path was added.
- Existing `on_ready` ownership remains only as an idempotent fallback.
- DNS/custom-domain records are intentionally unchanged.

## Blockers / risks

- The branch is not complete until exact-head CI passes and Discloud proves the origin stays online.
- If port 8080 still fails to bind, the next evidence must come from the new pre-gateway startup log rather than another DNS change.

## Backlog

- Blank Audio Track dropdown remains separate.
- Remaining healthy-swarm startup-speed work remains separate.
- Feed artwork enrichment remains separate.
- Transient Cloudflare/origin 502 remains separate unless the same early-origin root cause reproduces it.

## Next step

Open a focused PR, run exact-head CI, repair only evidence-backed failures, then deploy and verify:
1. `https://stoneyverify.discloud.dev/health`
2. `https://cinema.the420lobby.com/health`
3. `https://cinema.the420lobby.com`
4. standalone Discord OAuth login and the existing Discord **Open Dank Cinema** shortcut.

---

## Previous active task / outcome


## Active task / outcome

**DANK-CINEMA-HOME-RESUME — make the Home hero Resume button actually resume the exact saved movie/episode**

Production baseline:
`main@38ad43110e1c1b122332a82ba2b03c0262b97b02` (PR #466 merged).

Active branch:
`fix/cinema-home-resume`

Outcome:
When Cinema Home promotes a partially watched title in the hero, pressing **Resume** must start the canonical standalone playback for that exact saved movie or TV episode and let the Theater restore saved progress. It must not silently route back to Home or require the user to reopen Details first.

## Status

**Root cause is confirmed and fixed on the branch. Focused regressions are added; exact-head CI and production canary remain.**

## Exact root cause

1. The Home backend labeled the hero action **Resume**, but encoded it as:
   `{"kind":"details","media_type":<saved media type>,"tmdb_id":<saved tmdb id>}`.
2. For the production reproduction, the saved item was **The Office S01E02**, whose library media type is `episode`.
3. The client therefore attempted to navigate to `#details/episode/<episode tmdb id>`.
4. The router intentionally accepts Details only for `movie` and `tv`:
   `if (view === "details" && ["movie", "tv"].includes(parts[1]) ...)`.
5. The invalid `details/episode/... ` route immediately fell through to `go("home")`.
6. Because the user was already on Home, this appeared exactly as reported: pressing **Resume did nothing**.
7. The existing `playOnSite()` path already supports `movie` and exact `episode` playback and is the correct canonical path. Episode playback requires `series_id`, season, episode number, and episode TMDB id; progress records already preserve that series identity in metadata.

## Fix

- Home Resume actions are now **direct-play actions**, not mislabeled Details actions.
- Movie Resume carries:
  - `media_type=movie`
  - exact movie `tmdb_id`.
- Episode Resume carries:
  - `media_type=episode`
  - exact episode `tmdb_id`
  - canonical `series_id`
  - season number
  - episode number.
- The client hero handles `action.kind === "play"` by calling the existing `playOnSite(action, resumeButton)` path.
- No duplicate playback API or room path was added.
- Legacy/malformed episode progress that lacks a canonical `series_id` no longer emits a knowingly invalid Resume action.
- Cinema JS asset version is bumped from v16 to v17 so browsers do not keep the broken hero handler cached.

## Validation added

Focused regressions verify:
- movie hero Resume becomes a direct-play action;
- exact TV episode Resume preserves series/season/episode/TMDB identity;
- malformed episode rows do not build an invalid `details/episode/... ` action;
- the client hero routes play actions through `playOnSite()`;
- the updated JS asset version is served.

## Scope / compatibility

Dank Cinema remains cross-browser. This fix uses the same existing website API and normal button/navigation code on Chromium, Samsung Internet, Firefox-family browsers, Safari/WebKit, mobile, tablet, and desktop.

No source-selection, torrent startup, reconnect, audio, feed-artwork, or unrelated Cinema behavior is changed here.

## Backlog / preserved work

- Audio-track selector blank dropdown.
- From Your Feeds canonical TMDB artwork enrichment.
- Transient Cloudflare/origin 502 if it recurs independently.
- Any remaining startup-speed work continues from the already merged first-byte/startup work; this Resume task does not rewrite it.

## Next step

Open a focused draft PR, run exact-head CI, repair only evidence-backed failures, then production-canary the exact Home **Resume** reproduction for a partially watched TV episode and a movie.

---

## Previous completed task / outcome

**DANK-SHIELD-405-FOLLOWUP — eliminate slow-swarm play/stop glitching and invalid partial stream bodies**

Production:
PR #427 merged as `fe5e7b70616eefe401126b19597086cf6621f70e`.

Validation:
Exact-head `ae13ae720b950908565ff61d2b7a146c6eb2099f` passed all six required workflow families. The user then confirmed the production host/viewer canary continues playing correctly. Issue #405 was closed completed.



**DANK-SHIELD-425 — Dank Cinema master audit: setup, UX, lifecycle, playback, providers, and capacity**

Production baseline:
`main@3c8989e6bfa70fd463e1914bfa96d60e9f3a6233` (PR #424 merged; Discord menu presence/Rejoin follow-up exact-head CI green).

Active branch:
`audit/425-cinema-master-audit`

Issue:
**#425 — Dank Cinema master audit: simplify setup, navigation, lifecycle, and playback**

Status:
**Full end-to-end audit is active. The audit covers the real Discord, room, Watch, provider, torrent, persistence, capacity, and test paths. Evidence-backed defects are being repaired on this branch while known environment/architecture limits are documented instead of being mislabeled as fixed. Exact-head CI and live mobile/Discloud canary remain pending.**

## Audit contract

Primary viewer path stays:
`Cinema Home -> Find Movie -> Choose Movie -> Choose Release -> Watch`.

Rules:
- preserve feature depth;
- show contextual controls only when actionable;
- keep rare/admin/technical controls under More / Cinema Settings;
- auto-detect or safely repair setup where possible;
- optional features must not block core playback;
- every room-scoped error must preserve useful Cinema navigation;
- no second provider stack, torrent runtime, media server, or room model;
- regression-test every production bug class discovered from #408 forward.

Detailed audit record:
`docs/DANK_CINEMA_MASTER_AUDIT.md`.

## Architecture verified

Discord:
`/movie -> MovieNightHubView -> search/catalog -> candidate -> release -> Watch`.

Authority:
`MovieNightManager -> MovieNightRoom -> host/viewers/votes/queue/playback clock`.

Watch:
`signed per-user URL -> state/heartbeat/action -> canonical room + torrent runtime`.

Torrent:
`TorrentMediaManager -> shared identity/session -> room leases -> signed byte-range route`.

Providers:
`TMDB/JustWatch + Internet Archive + direct magnet/.torrent + generic JSON/RSS/Atom/Torznab resolver`.

Persistence:
- guild provider/preferences/config persist;
- live Movie Night room authority is currently in memory.

## Findings and current repairs

### Critical — abandoned rooms could retain leased media indefinitely

A room previously had no inactivity expiry, while #423 correctly prevents leased torrents from ordinary torrent idle cleanup. With no viewers, that combination could hold a tracked torrent lease until explicit End Movie Night or process restart.

Repair in progress/implemented:
- `DANK_MOVIE_NIGHT_EMPTY_ROOM_TTL_SECONDS` defaults to **1800 / 30 minutes**;
- short **35-second** live-viewer TTL remains unchanged for sync/quorum;
- room expiry uses last real room presence;
- active viewers prevent expiry;
- cleanup rechecks eligibility;
- room is synchronously ended before external lease cleanup;
- canonical termination releases media and retires room state;
- cleanup worker starts with Movie Night public routes.

### High — optional notifications blocked Watch Party launch

Notification role, pingability, and Manage Roles were product-enhancement concerns but were treated as core launch blockers.

Repair:
- missing/unpingable notification role is a warning;
- missing Manage Roles only warns that the optional role cannot be created;
- public Watch Party still posts a channel announcement without a role;
- configured valid role is still pinged;
- Setup action is **Repair Notifications**.

### High — media-server process readiness was only a warning

A configured URL/secret could report launch-ready even when the actual Watch media server was not running.

Repair:
- live media-server readiness is a launch blocker.

### Medium — PyAV metadata probing blocked playback

PyAV is used for verified codec/audio metadata, and probe failures already fail soft.

Repair:
- PyAV is now an optional warning;
- libtorrent remains required;
- playback can launch without verified metadata probing.

### Medium — one-person Watch Party exposed pointless voting UI

Repair:
- solo candidate/release vote buttons hidden;
- solo queue action says **Add to Queue**;
- host says **Play This Release**;
- collaborative controls return when another active viewer joins;
- non-host action says **Request This Release**.

### Medium — direct-media failures could lose room context

Repair:
- magnet/.torrent startup and signed-stream errors keep the active room embed/controls;
- setup errors point to the real **More -> Cinema Settings -> Setup & Diagnostics** path.

### Medium — torrent process-memory fallback exceeded checked-in host RAM

Repair:
- code fallback changed from 1536 MB to the checked-in Discloud allocation of **1495 MB**.

### Medium — Setup repeated Settings navigation

Repair:
- Setup & Diagnostics now focuses on **Repair Notifications**, **Test Media Endpoint**, Refresh, Back, Close;
- Provider Deck and Notifications remain first-class Cinema Settings destinations instead of being duplicated inside Setup.

### Medium — lifecycle copy contradicted lease hardening

Repair:
- lifecycle text now separately explains live-viewer expiry, 30-minute empty-room expiry, 6-hour signed Watch links, media lease ownership, and process-restart behavior.

## Preserved behavior

- Watch Party announcements and collaborative voting;
- Private Viewing owner-only isolation;
- Pass Host and host-away playback fallback;
- queue;
- progressive-disclosure Home/More/Settings UI;
- Rejoin after short Discord/Watch presence expiry;
- TMDB/JustWatch;
- generic structured providers;
- direct magnet/.torrent;
- adult-content guild setting;
- first-release mobile selection fix;
- refresh/reconnect/session-aware sync;
- adaptive group buffering;
- shared torrent identities and per-room leases;
- dynamic memory/disk admission;
- invalid libtorrent-handle containment;
- canonical End Movie Night cleanup.

## Validation added/updated

Manager:
- empty-room timeout starts from last presence;
- active viewer prevents room expiry.

Session lifecycle:
- inactive room cleanup releases media and retires only the stale room;
- cleanup worker registration is part of Movie Night public-route startup.

UI:
- notification role is optional for launch;
- public no-role announcement still posts;
- media server required but PyAV optional;
- solo/public/private action labels and vote visibility;
- simplified Setup buttons;
- accurate viewer/empty-room/link/restart lifecycle text.

Capacity:
- `.env.example` contains empty-room TTL;
- torrent memory fallback must match 1495 MB deployment allocation.

## Known limits / not falsely claimed fixed

- live room authority is in memory and does not survive process restart;
- each Watch viewer receives a separate outbound HTTP stream, so high viewer counts require real load testing;
- arbitrary direct magnet/.torrent media cannot be reliably adult-classified without trustworthy metadata;
- external provider availability/metadata quality remains outside Dank Shield's control;
- `public_movie_night.py` is a maintainability hotspot, but a large mechanical module split is deferred because it would add regression risk during a correctness audit.

## Blockers / remaining work

1. finish static/diff inspection for the final audit branch;
2. update all expectations affected by simplified Setup/notification/lifecycle semantics;
3. run targeted Movie Night/provider/torrent tests through repository CI;
4. patch only evidence-backed failures;
5. verify Discord component IDs/rows/emoji remain valid;
6. exact-head full workflow set must be green;
7. mobile/production canary from the audit document remains operator validation, not an automated success claim.

## Backlog after #425

- optional persistent room snapshot/recovery across process restart;
- practical 20-viewer load/capacity validation on the actual hosting/network path;
- optional majority-approved Claim Host fallback when the host disappears without passing control;
- separate Movie Ready notification refinement if a second notification beyond room-start announcement is still desired.

## Next step

Complete test expectation cleanup, open the focused #425 audit PR, run exact-head CI, repair any evidence-backed failures, then merge only after the final head is clean and green.


---

## Previous completed Share Router direct-memes follow-up

**DANK-SHIELD-397 — Share Router parity for direct memes-channel video posts**

PR #399 merged to production `main` as:
`e647876844f9766c119b62534bd20e43af0dd8aa`.

Final exact-head `02e886fd84d0a5d04471a3c6e35e0945ef3e1390` passed:
- Dank Shield CI #3538;
- Profile Runtime Diagnostics #2084;
- Dank Design Regression CI #1616;
- Application Command Size Diagnostics #2395;
- Ticket Owner Emergency Override #2109.

That baseline keeps one Share Router listener/media stack, resolves the configured memes destination from the canonical `share-memes` route, reuses the native-video upload owner, preserves proxy behavior, and leaves direct memes posts non-destructive.

---

## Previous completed Dank Cinema provider follow-up

**DANK-SHIELD-MOVIE-NIGHT-EASY-SOURCES / dual provider modes**

PR #398 merged to production `main` as:
`d0b7b9cc34ab4725bccf7c12526409bb75539d8e`.

That merged baseline includes:
- zero-setup TMDB catalog matching and JustWatch availability;
- built-in Internet Archive Feature Films playable search;
- direct magnet/.torrent playback;
- JSON Provider and external Search Link modes;
- acknowledgement-before-persistence on Search Link setup;
- Discord link-button URL-budget validation;
- bounded Provider Deck field rendering and dual-mode guidance.

At the #397 task transition, post-merge main CI #3533 was still running. Same-SHA production/mobile Dank Cinema canary remains an operator acceptance item and is not represented as completed evidence here.

---

## Previous completed Movie Night follow-up

**DANK-SHIELD-MOVIE-NIGHT-FOLLOWUP — simple custom-source UX + complete session termination**

PR #395 merged to production main as:
`d8a3f2ddd0ece13dfe2164176076ecc5c3988cc1`.

That baseline includes:
- host-confirmed and viewer-voted complete Movie Night termination;
- canonical shared torrent lease cleanup;
- web-player termination convergence;
- internal source-ID hiding/generation;
- first-class custom-source edit / enable / disable / remove.

---


## Previous completed baseline

**DANK-SHIELD-MOVIE-NIGHT-393 — dynamic media capacity + shared torrent reuse**

PR #392 / issue #391 are merged to production main as:
`151076e83ec49a8a09a1856fbedd5dd5a61a62df`.

Issue #393 was completed and merged as PR #394 into:
`7d09c89c15ed48067cb97ee32bc900931e1833dd`.

Previous implementation branch:
`feat/movie-night-dynamic-capacity`

Current host facts supplied by the owner:
- allocated RAM: **1.46 GB / ~1495 MiB**;
- ordinary Dank Shield RSS observed before Movie Night load: **~340–390 MB**;
- current free disk reported by Movie Night setup: **~1.17 TiB**;
- Discloud plan supports Site deployment, so the same `main.py` process can keep Discord ownership while exposing the media-only HTTP surface.

## Current task contract

1. Keep one canonical torrent runtime; do not create a second media stack.
2. Protect the core Discord bot with dynamic memory admission before starting a new **unique** torrent.
3. Start conservatively at 2 unique torrents on this host; keep a configurable hard ceiling of 4 while burst admission remains disabled by default.
4. Reuse an existing torrent session/file cache when multiple Movie Night rooms choose the same canonical torrent identity.
5. Track per-room leases so one guild switching/ending cannot delete a torrent still used by another room.
6. Preserve independent Movie Night clocks/viewers even when rooms share one torrent session.
7. Raise the practical disk-backed movie ceiling to 25 GiB, 50 GiB declared torrent budget, and a 64 GiB free-disk safety reserve.
8. Expose current RSS, protected memory reserve, unique torrent count, leases/shared sessions, admission slots, disk reserve, committed media, and free disk in Movie Night Setup.
9. Convert the checked-in Discloud profile to the Site-capable 1.46 GB deployment target.
10. Document the exact production `.env` values and protect secrets.
11. Add regression coverage for memory rejection, disk rejection, identity reuse, shared lease release, replacement, and capacity telemetry.
12. The 300,000+ guild scale target is based on concurrent unique media sessions, not installed guild count; the public `/movie` UX must remain compatible with a future external media-worker pool.

## Scope

Build the real torrent media runtime, not a decorative command:

- accept user-supplied magnet links;
- accept user-supplied `.torrent` attachments;
- resolve metadata;
- select a playable video file;
- progressively prioritize pieces for startup and seeks;
- expose the selected file through a signed HTTP byte-range stream;
- integrate ingestion into the existing Share Router route owner;
- enforce strict session, disk, bandwidth, metadata, peer, timeout, and cleanup limits;
- keep the structured bot/admin API private;
- do not add torrent indexing/search/discovery.

Use is limited to lawful, public-domain, or otherwise user-authorized media.

## Movie Night release selection + custom sources

Expanded requirements now implemented in the active branch:

- release variants carry live `seeds`, `leechers`, total peers, seed/leech ratio, and a human swarm-health label;
- default release ordering is availability-first when votes are tied: live seeds → seed/leech balance → leech count → verified quality/codec efficiency → file size;
- viewer votes remain authoritative once the room starts choosing between variants;
- zero-seed/dead variants sort behind live alternatives by default;
- one movie candidate can hold multiple quality/release variants instead of one opaque source;
- variants retain source ID/display label provenance;
- verified media metadata and release-name inference remain separate truth levels;
- guilds have a revisioned custom-media-source registry persisted through canonical guild-config CAS;
- custom sources can be added, updated, enabled/disabled, and removed;
- custom source URLs require HTTPS and reject embedded credentials plus obvious local/private/reserved literal addresses;
- the future resolver must revalidate DNS/network destinations at request time before fetching;
- custom-source results will merge into the same Movie Night variant list and voting/queue model;
- no temporary duplicate source-config command is being added; the first canonical Movie Night manager owns Sources → Add / Enable / Disable / Remove.

New regression coverage:
- seed/leech/peer status exposure;
- seed-first default variant ordering;
- source provenance retention;
- custom source parse/add/update/disable/remove round-trip;
- unsafe custom source URL rejection;
- atomic guild-config CAS ownership.

## Community & Pings Movie Night role integration

Movie Night role ownership now uses the existing generic Community & Pings system:

- canonical capability: `movie_night_notify`;
- runtime role lookup is capability-based, not name-based or hard-coded-ID-based;
- direct Community & Pings manager control: **Movie Night Notify**;
- direct mapping is limited to enabled safe notification options;
- Movie Night role registration helper creates/normalizes a notification option and assigns the capability uniquely;
- existing prerequisite/exclusivity/removability/group/presentation/unrelated capability state is preserved when mapping an existing role;
- /toke start/notify capabilities are preserved;
- role rename/styling after setup is safe;
- setup can call `movie_night_registration_blocker()` before creating a Discord role so the 25-option cap cannot leave an avoidable orphan role;
- member opt-in remains owned by the existing Community & Pings picker and per-member lock;
- no second Movie Night role table/config key is introduced.

Regression coverage now checks capability payload round-trip, unique reassignment, preservation of existing rules and /toke capabilities, direct manager mapping, notification-only filtering, and option-capacity preflight.

## Public Movie Night command + complete setup

The feature now has a real public doorway and setup surface instead of backend-only state.

Public entry:
- `/movie` opens the canonical Movie Night hub;
- `/movie magnet:<link>` attaches an authorized magnet to the active/current-channel room;
- `/movie torrent:<file>` accepts an uploaded .torrent metadata file;
- Dank Home → Community & Engagement → **Movie Night** routes to the same owner;
- no duplicate `/movienight`, `/movie-search`, or setup command tree is introduced.

Hub controls:
- Start / Join;
- Search / Vote;
- Queue;
- Vote Yes / Vote No;
- Sources;
- Setup;
- Community & Pings;
- Refresh;
- Close.

Complete setup checks:
1. Movie Night notification role exists and maps through Community & Pings capability `movie_night_notify`;
2. role notification ping is actually usable;
3. current channel has View Channel / Send Messages / Embed Links and reports Attach Files status;
4. libtorrent runtime is installed;
5. PyAV/FFmpeg metadata runtime is installed;
6. `DANK_MEDIA_PUBLIC_BASE_URL` exists;
7. dedicated `DANK_TORRENT_STREAM_SECRET` exists;
8. externally-addressed media uses an externally reachable bind host (Discloud: `0.0.0.0`);
9. the dedicated media server reports started;
10. custom source counts/enabled state are visible;
11. **Test Media Endpoint** performs a real health request after deferring the Discord interaction.

Role setup behavior:
- **Create / Repair Role** preflights Community & Pings capacity before creating Discord state;
- new role is created as a ping-ready Movie Night notification role;
- role is registered atomically into the existing Community & Pings config;
- if persistence loses a CAS race/fails, the newly-created Discord role is deleted as rollback;
- repairing an existing mapped role preserves the capability model and can make it mentionable when the bot otherwise cannot ping it safely;
- no second Movie Night role config authority exists.

Source setup behavior:
- **Sources** lists guild-owned custom sources with provenance and revision;
- **Add / Update Source** persists through the canonical CAS registry;
- **Manage Source** supports Enable / Disable / Remove;
- source setup remains staff-only;
- normal members can still open Movie Night and their own Community & Pings choices.

Command-surface contract:
- intentional final public surface is now 10 items including `/movie`;
- attachment doorways are intentionally limited to `/dank upload:file`, `/toke upload:`, and `/movie torrent:`;
- navigation registry and compact command audits are updated to fail closed on drift.

Regression coverage includes:
- `/movie` schema and optional attachment type;
- hub/setup/source component labels and Discord component limits;
- navigation aliases such as `movie night`, `watch party`, `group streaming`, and `torrent streaming`;
- external-media bind readiness;
- complete ready-state evaluation;
- media health test acknowledgement before network I/O.

## Historical #391 hosting constraint (superseded by active #393)

At the time #391 was implemented, production `discloud.config` was `TYPE=bot`.
Issue #393 intentionally changes the checked-in deployment target to `TYPE=site`
with `RAM=1495` now that the owner confirmed a Site-capable Diamond plan.

Discloud Bot deployments do not expose an external HTTP port. Externally reachable
web/API/bot-with-web-interface deployments use `TYPE=site` and Discloud proxies
traffic to `0.0.0.0:8080`.

Therefore:

- the torrent engine can run under the current bot process;
- actual external playback needs a public media endpoint;
- the implementation uses a **separate media-only server** on the media port;
- the structured admin API stays on `127.0.0.1:8081`;
- production `discloud.config` is intentionally not changed automatically because
  Site hosting depends on the account plan/subdomain.

## Architecture

### `stoney_verify/torrent_streaming.py`

Canonical torrent runtime owner:

- pinned `libtorrent==2.1.1`;
- live-session cap with one-session default for the current 512 MB host;
- magnet and `.torrent` ingestion;
- normalized BTIH identity across hex/base32 magnets;
- metadata timeout and input limits;
- torrent total-size and selected-file limits;
- largest supported playable video selection;
- non-selected files priority 0;
- startup piece priority 7;
- bounded readahead priority 6;
- tail priority for MP4-style end metadata;
- seek reprioritization from HTTP byte ranges;
- piece-availability wait before sparse-file reads;
- signed temporary stream URLs;
- periodic idle cleanup and file deletion.

### `stoney_verify/api_new/torrent_stream_routes.py`

- public byte-range stream handler;
- correct `Accept-Ranges` / `206 Partial Content` behavior;
- signed URL validation;
- buffering `503` when the requested first chunk is not ready;
- internal-auth status/cancel routes.

### `stoney_verify/torrent_media_server.py`

Dedicated public media-only server:

- no ticket/member/admin endpoints;
- public stream URL must be HTTPS outside localhost development;
- own bind host/port;
- requires dedicated `DANK_TORRENT_STREAM_SECRET`;
- starts through native `app.py` lifecycle.

### Share Router

- detects a magnet in the source message;
- detects `.torrent` attachments;
- refuses to join a swarm before public media/signing configuration exists;
- dedupes recent torrent sources before starting duplicate magnet sessions;
- creates one canonical torrent session;
- posts the temporary playback URL and media details;
- deletes the proxy source only after successful stream creation;
- leaves failed/unconfigured sources intact and logs the blocked route.

## Historical #391 resource defaults (superseded by active #393)

The original #391 conservative defaults were:

- live sessions: 1;
- metadata: 4 MiB;
- selected video: 2 GiB;
- total torrent declared size: 4 GiB;
- startup window: 8 MiB;
- tail probe: 4 MiB;
- readahead: 16 MiB;
- metadata wait: 30 s;
- buffering wait: 20 s;
- idle TTL: 30 min;
- peers/connections: 80;
- download cap: 8 MiB/s;
- upload cap: 512 KiB/s.

All are generic environment settings, not guild hardcoding.

## Regression coverage

New `tests/test_torrent_streaming.py` covers:

- magnet and `.torrent` detection;
- base32/hex BTIH normalization;
- supported video formats/content types;
- normal/open/suffix HTTP range parsing;
- invalid/multi-range rejection;
- largest playable file selection;
- selected-file-only priorities;
- startup/tail priority behavior;
- seek + readahead piece math;
- requested-piece availability waits;
- live-session capacity;
- signed stream URL validation/tamper rejection;
- HTTPS-only public media policy;
- isolated public media server ownership;
- Share Router magnet / `.torrent` integration markers.

## Validation required

Before merge:

- compile/import;
- pinned libtorrent installs successfully on CI Python 3.11;
- focused torrent streaming tests;
- focused Share Router tests;
- structured API security tests;
- full Dank Shield pytest/CI;
- application command diagnostics unchanged;
- final diff/reference audit;
- branch remains 0 behind production main;
- no public admin API exposure;
- no arbitrary torrent index/search feature.

After merge/deploy:

1. first deploy under existing `TYPE=bot` should keep the media server disabled unless configured;
2. confirm the Discord bot and private structured API remain healthy;
3. when a Discloud Site/subdomain is intentionally configured, expose only the media server on `0.0.0.0:8080`;
4. test a known legal/public-domain magnet;
5. confirm playback starts before the entire selected file completes;
6. seek forward and confirm piece reprioritization/buffering recovers;
7. test a legal `.torrent` attachment;
8. verify duplicate magnet share does not start a second session;
9. verify idle cleanup removes the handle and files;
10. verify tampered/expired stream URLs fail.

## Suspended work

Issue #388 / PR #389 — **Share Router native video + X/Twitter dedupe** — merged as
`585f06121ca3c20759aac0bd43f506b82c9cb7f0`; exact-head workflows passed.
Its production Android canary is suspended by this FORCE SWITCH.

Issue #390 — **Universal Share Router media resolver and stream playback** remains queued.
Torrent support is being built first by explicit FORCE SWITCH and should later plug into that
broader resolver instead of being reimplemented.

Issue #386 / PR #387 — **Toke media** — merged previously; post-merge Android canary remains suspended.

Issue #380 — **True master runtime ownership + production-path audit** remains queued.

Issue #384 — **Audit repeated Discord 429s during/after activity recovery** remains backlogged.

## Suspended Protection task record

## Production evidence

After PR #376 deployed successfully on Discloud:
- the original red Discord timeout changed behavior;
- pressing **Home → Safety & Moderation → Protection** can now appear to do nothing;
- PR #376 already defers before persisted reads and edits the original panel after loading.

The failed canary proves acknowledgement alone was insufficient.

## Root cause found after PR #376

The merged refresh owner acknowledges the interaction and then waits on:

- fresh guild configuration load;
- Spam Guard settings load.

Those reads were joined without any bounded completion deadline or degraded fallback.
If either read stalls after Discord has already acknowledged the click, mobile no longer shows a timeout error; it simply leaves the user staring at an unchanged/deferred interaction.

Additionally, Spam Guard can return an `unavailable:...` source without throwing, so exception-only degradation would be incomplete.

A second canary/CI pass exposed one more pre-ack hole: public Protection entry routes still called `_require_setup_permission()` before the shared refresh owner. For delegated staff, that permission path can consult configured control-role state and may synchronously fetch configuration on a cold cache. The protection refresh itself could therefore be perfectly deferred while the button still stalled before it ever reached that refresh.

## Current fix

The shared Protection owner now:

1. uses one idempotent Protection-entry acknowledgement helper **before authorization and backend I/O** across the slash command, categorized navigation, legacy home button, setup button, advanced setup route, service entry, and Invite Shield return path;
2. immediately edits the original interaction to a visible **Opening Protection Center** loading state;
3. loads guild config and Spam Guard behind bounded, independent read-only waits;
4. uses the existing cached guild config path when a fresh config refresh is slow/unavailable;
5. falls back to explicit unknown/default-safe Spam Guard state when that source cannot load;
6. treats returned `unavailable:...` Spam Guard state as degraded even without an exception;
7. still renders the Protection Center in degraded mode instead of leaving the button visually dead;
8. disables all mutating controls while live state is incomplete;
9. leaves only **Retry Live State** and **Close** enabled in degraded mode;
10. shows an explicit warning that no protection setting was changed by the fallback;
11. keeps live security-stat repair after the panel is already visible;
12. uses one generic runtime-configurable load budget:
    `DANK_PROTECTION_PANEL_LOAD_TIMEOUT_SECONDS`.

## No hardcoding contract

This fix contains:
- no guild IDs;
- no role IDs;
- no channel IDs;
- no server names;
- no owner-specific exceptions;
- no per-server timeout branch.

The load budget is process configuration and applies generically to every guild.

## Safety

- no guessed protection state may authorize mutations;
- incomplete live state fails closed;
- cached state may be displayed with an explicit degraded warning, but mutation remains locked;
- existing AntiNuke owner-only controls remain owner-only;
- no changes to AntiNuke hostile-reputation semantics from PR #376;
- no global guild sweep;
- no database migration;
- no new persistence authority.

## Validation required

Before merge:
- regression coverage that every public Protection entry acknowledges before its permission check;
- focused runtime test that acknowledgement is idempotent;
- focused runtime test for visible loading before slow reads;
- focused runtime tests for a hung config read;
- focused runtime tests for a hung Spam Guard read;
- cached-config fallback test;
- returned `unavailable:...` Spam Guard degradation test;
- degraded view enables only Retry + Close;
- static check that loading UI appears before backend reads;
- exact-head full CI;
- exact-head Protection/AntiNuke focused checks;
- branch 0 behind main;
- diff hygiene.

After merge:
- exact merge SHA Discloud success;
- canonical post-merge CI;
- Supabase workflow if triggered;
- Android canary:
  1. open Safety & Moderation;
  2. press Protection;
  3. confirm immediate loading state;
  4. confirm final Protection Center opens;
  5. press Refresh;
  6. confirm it remains responsive;
  7. if backend is degraded, confirm Retry/Close only and no settings mutation.

## CI failure already resolved

Exact head `3fea2335e2309f3318072ff6e591ea8b66dbec53` failed Dank Shield CI only because the old acknowledgement regression expected the first panel edit to occur after config/spam reads. The new visible-loading contract intentionally performs a loading edit before those reads. The test now verifies the correct order: defer → loading edit → config/spam → final edit → stats.

That review also exposed and fixed the remaining pre-ack permission-check hole described above.

Exact head `7bf5c3edca7960e708df4825f581d6f84453d47f` then failed only on two stale test contracts:
- a static test still required the literal `interaction.response.defer` inside `_refresh_panel()` even though acknowledgement is now deliberately centralized in `_ack_protection_entry()`;
- an advanced-setup test used a minimal fake response that never modeled acknowledgement because the old route performed permission checking first.

Those tests now validate the new architecture directly: the shared ack helper owns Discord defer, and the setup route order is **ack → permission → Protection refresh**.


## Protection runtime-ownership correction

The latest CI failure exposed a deeper architecture defect in the setup compatibility layer:

- `stoney_verify/commands_ext/public_setup_compact.py` was still monkey-patching `setup._open_protection_options` at import/runtime;
- that wrapper performed setup authorization before the canonical Protection acknowledgement owner;
- the permanent setup audit and runtime-integrity test incorrectly required that monkey patch to exist, so the audit could pass while violating the stated native-ownership goal.

Correction on this branch:

- retired the Protection-specific runtime reassignment and saved-original-function attribute;
- Protection setup navigation now calls the native `public_setup_recommend._open_protection_options` directly;
- changed runtime-integrity coverage to require the native function and absence of `_DANK_SETUP_ORIGINAL_OPEN_PROTECTION_OPTIONS`;
- changed `tools/audit_setup_safety.py` to fail if the retired Protection wrapper, saved-original marker, or reassignment returns.

This correction is part of the same Protection incident because the runtime replacement directly caused the pre-ack permission regression. Other unrelated setup compatibility wrappers remain outside this active task unless evidence shows they affect Protection.

## Next step

Validate PR #377 on the new exact head. Patch only evidence-backed failures. If CI is green, complete final diff/branch hygiene, mark ready, merge with the exact expected head, verify post-merge CI/Supabase/Discloud, then rerun the Android Protection canary. Do not close issue #375 until that live canary passes.


## Post-merge Android canary failure — permanent loading card

Production merge SHA: `1fac08e87c534328ee94263156bcbd1b3d33c3e7`.

Android canary evidence after PR #377:
- Home → Protection acknowledges successfully;
- the original private response is replaced with **Loading Protection Center…**;
- the loading card can remain indefinitely and the final controls never appear.

Execution-path finding:
- `_refresh_panel()` bounded only guild-config and Spam Guard reads;
- after those reads, the real AntiNuke readiness/embed construction, Protection view construction, and final Discord edit were outside the terminal-state guarantee;
- Home/navigation callers invoke the canonical refresh directly, so an exception after the loading edit can leave the loading card as the permanent UI;
- existing tests stubbed `_protection_embed` and `ProtectionCenterView`, so they did not exercise the production render/send boundary;
- the AntiNuke Permission health field could exceed Discord's 1,024-character embed field-value limit because multiple verbose hierarchy/channel blockers were joined without truncation. A Discord 400 on the final edit then retried the same invalid payload and could leave the loading card unchanged.

Current hotfix branch: `fix/protection-loading-terminal-state`.

Hotfix behavior:
- cap the dynamic AntiNuke field to Discord's field-value limit;
- make the canonical refresh owner catch unexpected state-load and panel-render failures;
- bound the final Discord edit;
- if the final panel cannot be rendered/sent, replace the loading card with a plain terminal error containing an Error ID;
- never resend the same rejected full panel as the fallback;
- add regression tests for overlong AntiNuke health content, render failure after loading, and final-edit rejection after loading.

Issue #375 remains open. Do not claim the Protection incident resolved until exact-head CI, merge/deploy, and Android canary all pass.


## PR #378 Android canary — actual render crash and stale setup navigation

The post-merge Android canary on production `0ce2b74856262be1b74ef3066426d85b89136d9f` produced terminal Protection errors instead of hanging, which exposed the exact remaining runtime conflict.

Production diagnostics/logs recorded both navigation routes failing at the same renderer boundary:

- Error IDs `DANK-653ABC6A` and `DANK-50BEDB33`;
- stage `protection_panel_render_failed`;
- `TypeError: _patch_ui.<locals>.embed() got an unexpected keyword argument 'channel'`;
- callers included `dank:navigation:feature:protection:v1` and `dank_setup_security:protection`.

Root cause:
- `anti_nuke_product_policy_runtime._patch_ui()` still replaced the canonical Protection embed, view class, and AntiNuke UI callbacks after import;
- its replacement embed retained the old four-argument signature while the canonical Protection renderer now supplies `channel=` and `load_warning=`;
- this was a live runtime monkey patch that the prior ownership audit did not prohibit.

Related navigation evidence from the same canary:
- `/dank home` already uses `navigation_registry.CATEGORIES`;
- `/dank setup` still used a separate hard-coded nine-item `FEATURE_AREAS` list;
- tests explicitly required those stale labels, so CI preserved the divergence.

Current correction:
- Strict Lockdown UI/state/toggles now live natively in `commands_ext/public_protection_center.py`;
- canonical Protection accesses AntiNuke through the module service owner so post-import policy/readiness behavior is not bypassed by stale captured aliases;
- `anti_nuke_product_policy_runtime.py` retains engine/policy behavior but no longer replaces Protection UI functions/classes;
- obsolete self-applying Protection presentation guards are deleted after reference verification;
- setup's feature picker derives from `navigation_registry.CATEGORIES` and routes through the canonical category owner;
- unit/static tests and `tools/audit_setup_safety.py` now reject Protection UI rebindings, retired patch files, and a standalone setup taxonomy.

Validation is still required. Do not claim issue #375 resolved until exact-head CI passes, the branch is merged/deployed, and Android canary confirms both Protection entry routes and the setup category picker.


## PR #379 CI failure correction

First exact-head CI on `f431928dc1de0efed617c2b5958d3fabef77a968` completed with **2390 passed / 9 failed** in Dank Shield CI. All other workflows passed.

The 9 failures were stale regression contracts, not a new production runtime failure:

- 2 AntiNuke trust tests still monkey-patched removed `public_protection_center.get_antinuke_settings` / `save_antinuke_settings` aliases even though Protection now intentionally calls `anti_nuke_service` as the authoritative runtime owner.
- 7 setup picker tests still required the retired `core/tickets/verification/security/logs/design/history` route map. The new picker routes canonical `navigation_registry.CATEGORIES` keys through `public_command_surface_v2._open_category`; the old FakeResponse then failed on the real Discord `response.is_done()` contract.

Correction:
- AntiNuke trust tests now patch `protection.anti_nuke_service`, matching the actual execution path.
- the setup picker regression test now asserts every canonical category routes through `public_command_surface_v2._open_category`; it no longer preserves the retired route map.

Current validation head: `71bb1c5375233a7595171d4153561062da20dfd3`. Dank Shield CI run #3346 and the companion workflows are in progress. Do not merge until this exact head passes.


## PR #382 CI correction

Exact-head Dank Shield CI on `17e4e74a259502582559431b08429377c1d39a86` finished with **2394 passed / 1 failed**. All companion workflows passed.

The single failure was a stale navigation regression contract in `tests/test_navigation_registry_runtime.py`. It still required `public_toke.py` to own `if replace_message:`, `_defer_update`, and `_defer_private` even though this task intentionally retired the duplicate Toke setup owner and delegates setup to `public_community_pings.py`.

Correction:
- the test now requires the compatibility entrypoints to delegate `replace_message` into the canonical Community & Pings owner;
- it explicitly rejects the retired local defer helpers from returning to `public_toke.py`;
- the canonical `public_community_pings.py` replace-message acknowledgement contract remains required.

Current validation head: `ca3299e7216474a7c961648c3c603d4f723a749c`. Do not merge until exact-head CI passes.


## PR #382 second CI correction

Exact-head Dank Shield CI on `f87431cb52060ceab58a9a47414c392869cb0704` again finished with **2394 passed / 1 failed**. Companion workflows all passed.

The single failure came from an overbroad negative assertion added in the previous correction. It banned `await _defer_private(interaction)` from the entire `public_toke.py` module even though the real member-facing `open_toke_command` legitimately uses that defer before config reads and message send work.

Correction:
- the negative assertion is now scoped only to the retired setup compatibility block between `open_toke_preset_setup` and `TokeCheersView`;
- the test explicitly confirms the live `open_toke_command` still owns its valid private defer.

PR #382 subsequently passed exact-head CI and merged. The active follow-up is PR #383; validate its exact head before merge.


## Android canary follow-up — role mappings hidden

The large-server channel picker now reaches and saves the expected `#general` channel. Android canary then exposed the next blocker in the same /toke setup flow:

- manager shows `Starter: Not configured`;
- manager shows `Notify: Not configured`;
- preferred channel is correctly configured;
- `/toke` rejects execution because both required role capabilities are absent;
- the only configured option in the reported server is `Stoner`.

Root cause:
- `toke_start` / `toke_notify` are capabilities on Community & Pings options;
- setup existed only inside the generic **Edit Option** editor via **Toke Starter** / **Toke Notify** buttons;
- the manager exposed status but no direct role-mapping control, making the required setup effectively undiscoverable.

Correction on the active branch:
- Community & Pings Manager now exposes direct **Toke Starter** and **Toke Notify** controls beside the Toke channel controls;
- each direct control picks from enabled, safe existing Community & Pings options;
- selecting an option assigns that capability to exactly one option and removes the same capability from any previous option;
- one option may own both capabilities, so an existing Stoner option can be both starter and notification role;
- persistence remains the existing `community_pings_v2` model via `_save()`; no legacy duplicate role-ID settings are reintroduced;
- manager text tells admins to use **Add Option** first if the desired role is not already a Community & Pings option;
- regression coverage verifies exclusive capability reassignment and preservation of the other /toke capability.

Do not close #381 until exact-head CI passes, PR #382 is merged/deployed, and Android verifies Starter + Notify + Channel all show configured and `/toke` successfully posts.


## Backlog — Discord REST 429 pressure

Issue #384 — **Audit repeated Discord 429s during/after activity recovery**.

Production logs on 2026-10-01 show deliberate `discord_api_safety` recovery pacing during large activity-reconciliation passes plus separate raw `discord.http` 429 responses for repeated single-message GETs later in runtime. This is not on the /toke path and is intentionally backlogged under the single-task lock.

Do not investigate #384 until #381 / PR #383 is complete unless the user explicitly FORCE SWITCHes.


## Post-merge /toke persistence failure — capability ID round trip

PR #383 merged to production main as `ecad2b00295cd4b10f01e070e0e755560fe719d5`.

Android canary after deployment showed the new **Toke Starter** / **Toke Notify** controls could be pressed, but after refreshing the manager the mappings still displayed **Not configured**.

Root cause is in `community_pings_service._option_from_raw()`:

- canonical runtime capability IDs are `toke_start` and `toke_notify`;
- `CommunityPingOption.to_payload()` correctly persists those underscore identifiers;
- reload parsing incorrectly reused the generic human-facing `_slug()` helper;
- `_slug()` converts underscores to hyphens, so persisted `toke_start` / `toke_notify` reloaded as `toke-start` / `toke-notify`;
- `toke_role_ids()` checks for the canonical underscore constants, so it returned `(0, 0)` after refresh even though the database write succeeded.

Correction:
- capability identifiers now use a dedicated machine-ID normalizer that preserves underscores;
- hyphenated values produced by the historical parser bug are normalized back to the canonical underscore form for compatibility;
- service regression coverage now proves `to_payload() -> parse_community_pings() -> toke_role_ids()` preserves both mappings;
- UI-path regression coverage now proves direct Starter/Notify assignment survives the same save/reload round trip.

Active branch: `fix/toke-capability-roundtrip`.

Do not close #381 until this exact-head fix passes CI, merges/deploys, and Android confirms both role mappings remain configured after Refresh and `/toke` posts successfully.


## Swarm-health release ranking

- Movie Night voting/search/queue state is centralized under one canonical room owner.
- Torrent-backed movie results support multiple release/quality variants for the same title.
- Each variant carries live swarm health: seeds, leechers, total peers, seed/leech ratio, and a health label.
- Default variant ordering is swarm-first: user votes, then non-zero seed availability, highest seed count, seed/leech balance, verified quality/codec/source, and size.
- Zero-seed variants are retained for visibility but demoted below playable swarms.
- Live torrent status exposes seeds, leechers, peers, distributed copies, and seed/leech ratio for playback diagnostics.


## Late-join synchronization

Movie Night late joiners no longer enter the group buffering quorum immediately.

Contract:
- a viewer joining after playback has already started is marked `joining`;
- they remain an active room participant and can vote immediately;
- they do not influence shared buffer holds until actually synchronized;
- their browser seeks to the current room timestamp and buffers around that position;
- torrent piece priority is shifted to the current room position for the joining viewer;
- adaptive late-join readiness uses an 8–15 second target buffer depending on the torrent buffer plan;
- once position drift is within tolerance and the target buffer is available, the viewer becomes `synced`;
- only synchronized viewers join the group-buffer quorum;
- existing viewers continue playing while a newcomer catches up;
- switching to a new movie resets non-host viewers back to unsynced for the new stream;
- the host remains immediately authoritative/eligible;
- the Watch page visibly reports **Joining…** versus **Synced Viewer**;
- a late viewer with a poor connection cannot repeatedly freeze the room before synchronization;
- after synchronization, normal bounded group buffering applies to that viewer.

Regression coverage proves:
- late joiners remain outside buffer quorum;
- weak initial late-join buffer does not pause the room;
- adaptive buffer completion graduates the viewer;
- a graduated viewer can later participate in group buffering;
- media replacement requalifies non-host viewers for the new stream.

## PR #426 exact-head CI failure and repair

The latest master-audit head reached the full unit suite and failed only two Movie Night UI tests:

1. `_release_embed()` referenced `collaborative` without defining it after the solo-voting simplification.
2. The provider-browser safety regression still expected the old always-visible voting controls, even though this audit intentionally hides meaningless solo voting and relabels queueing to **Add to Queue**.

Repair:
- derive release-detail collaborative state from the already computed active-viewer set;
- keep release-vote counts hidden for solo/private rooms and visible only for true multi-viewer shared rooms;
- update the provider-safety regression to assert the current solo controls and explicitly verify no primary candidate button carries an external URL.

No provider runtime, torrent runtime, queue semantics, or collaborative multi-viewer voting behavior was weakened.

## Post-#442 canary defect — missing Discord /cinema command surface

Production baseline:
`main@d004d4ea1cdab1dffbbb59f289e4e84419b401bf` (PR #442 merged; post-merge Dank Shield CI, Cinema SQL, Ticket Owner Override, and Supabase migration workflows green).

Android/Discord canary evidence:
- the user cannot see a `/cinema` command or the planned Cinema shortcut commands;
- current production exposes only the legacy `/movie` Cinema doorway.

Root cause:
- the website `/cinema` route was implemented, but the Discord public registrar still intentionally compacted the global application-command tree to the old 10-item contract;
- `public_command_surface_v2` rejected any root outside `captions/dank/mod/movie/role/ticket/tickets/toke/verify`;
- the planned branded Discord command group therefore never reached global sync.

Active correction branch:
`fix/cinema-discord-command-surface`

Correction contract:
- keep `/movie` unchanged for backward compatibility and its optional magnet/.torrent attachment fields;
- expose one branded `/cinema` application-command group;
- expose `/cinema home`, `start`, `private`, `join`, `leave`, `queue`, `info`, and `vote`;
- all shortcuts reuse the existing MovieNightManager, room lookup, queue, vote, status, sync, and lifecycle owners;
- do not create duplicate room state, queue state, voting, persistence, or playback authority;
- `/cinema join` never creates a room;
- `/cinema leave` never ends a room and preserves the existing host-away lifecycle;
- private-session access remains fail-closed.

Validation required:
- command-contract and command-friction audits;
- command group regression proving all eight children and Yes/No vote choices;
- existing `/movie` parameter regression remains green;
- full Dank Shield CI and all triggered companion workflows on the exact PR head;
- branch 0 behind current main and final diff hygiene;
- after merge/deploy, Discord global command sync must visibly expose `/cinema` and all children while `/movie` still works.

Do not claim this canary defect resolved until the exact-head CI, merge/deploy, and live Discord autocomplete check pass.


## PR #443 first exact-head CI failure and correction

Exact head `7d22c069632e74927b73beaa8a5a40a843a55707` reached the complete unit suite and finished with **2842 passed / 2 failed**.

Both failures were stale final-command-tree expectations:
- `tests/test_command_ux_024_reassertion.py` still required the pre-Cinema root set;
- `tests/test_welcome_card_live_command_tree.py` still required the pre-Cinema root set.

The actual final command tree was correct in both failures and contained:
`captions, cinema, dank, mod, movie, role, ticket, tickets, toke, verify`
plus the `View Dank Profile` context command.

Correction:
- update both stale root-set expectations to include `cinema`;
- strengthen the live command-tree regression to require `/cinema` to be an `app_commands.Group`;
- require exactly the intended children: `home/start/private/join/leave/queue/info/vote`;
- preserve the existing assertion that all legacy fast doorways remain standalone commands.

No runtime Cinema implementation changed in this correction. Validate the new exact head before marking PR #443 ready.


## PR #443 command-contract sweep after first CI failure

The first CI failure stopped before standalone tool/audit steps, so the same command-surface root cause was checked across remaining validation/runtime documentation.

Additional stale pre-Cinema contracts found and corrected:
- `tools/test_dank_command_payload.py` still rejected any final root set containing `cinema`; it now expects the canonical Cinema root.
- `public_help_group.BORING_PUBLIC_TARGET` did not include `cinema`, which would have falsely labeled the new canonical command as unexpected in the command audit UI.
- the compact Home help embed did not advertise `/cinema`.
- `CLAUDE.md` and `docs/COMMAND_NATIVE_OWNERSHIP_AUDIT.md` still described the old ten-item public surface.

These are not unrelated cleanups. They are the same command-surface contract that caused the first #443 CI failure and are required so runtime diagnostics, standalone validation, and architecture documentation agree with the registrar.

Next step:
- validate the new exact head through full Dank Shield CI and all companion workflows;
- patch only evidence-backed failures;
- if green, verify branch is 0 behind main, final diff hygiene, then mark PR #443 ready for review;
- after merge/deploy, verify Discord autocomplete exposes `/cinema` and all eight children while `/movie` remains available.


## PR #443 production canary evidence — standalone Cinema Home + mobile header

Samsung Browser production screenshot after the post-#442 deployment showed two remaining Cinema defects while opening Dank Cinema without hosting a session:

1. the full Cinema shell loaded, but Home failed with `Dank Cinema requires membership in this Discord server.`;
2. mobile header controls used placeholder-style glyphs, including a literal diamond notification icon and a `?` profile fallback.

Root cause — standalone Home:
- standalone Discord OAuth already requests `identify guilds` and receives the user's current guild list;
- that exact guild list is signed into the short-lived `dank_cinema_guilds` cookie;
- despite that fresh Discord proof, `_site_identity` and `cinema_open_guild` immediately required another bot-side `guild.fetch_member()` check;
- a transient/cache/REST failure therefore rejected valid standalone browsing even though Discord OAuth had just proven the user shares the guild with Dank Shield.

Correction:
- accept the signed short-lived OAuth guild list as standalone browsing membership proof only for the exact guild and only while Dank Shield still shares that guild;
- if no valid OAuth guild proof exists, retain the live bot membership fetch and fail closed;
- targeted OAuth login with a current guild no longer performs a redundant second member REST lookup immediately after Discord's own `/users/@me/guilds` result;
- signed watch links/session flows without fresh OAuth guild proof still retain the live membership check;
- preserve exact-guild scoping and add regressions for both allowed and wrong-guild cases.

Root cause — header:
- `cinema_site.js` literally defined `bell: "♢"` and old text glyphs for navigation;
- when Home failed before Discord profile payload arrived, the avatar fallback rendered `?`.

Correction:
- replace header/mobile navigation glyphs with local inline SVG icons;
- use a real bell icon, real profile fallback, and real bottom-nav icons;
- strengthen icon contrast/borders on mobile;
- bump Cinema CSS/JS asset query versions to `v=3` so Samsung Browser does not reuse the old five-minute cached assets.

Acceptance:
- direct standalone `/cinema` login can browse Home/Search/My Stuff/Feeds/Profile with no active room;
- exact server membership remains required;
- no active Movie Night/Private Session is required merely to browse Cinema;
- header shows recognizable notification/profile icons, never a diamond or question-mark placeholder;
- full exact-head CI and companion workflows must pass before merge/deploy.


## Active follow-up PR after #443 merged

PR #443 merged before the Samsung standalone-Home screenshot fixes were added, so those post-merge commits were moved onto a clean branch from current production `main@21f910444979eae5a92a0026736a01ba96d478b1`.

Active branch:
`fix/cinema-standalone-home-mobile-header`

Active PR:
`#444 — Fix standalone Dank Cinema Home and mobile header icons`

PR #444 contains only the screenshot-backed canary correction:
- standalone OAuth guild proof reuse for browsing without an active room;
- exact-guild denial retained;
- live membership fallback retained when fresh OAuth guild proof is absent;
- real SVG header/mobile-nav icons;
- no diamond notification glyph;
- no question-mark profile placeholder;
- stronger mobile icon visibility;
- Cinema site CSS/JS cache-bust to `v=3`;
- focused regressions.

Do not merge #444 until exact-head CI and every companion workflow are green and the branch remains 0 behind main.


## Production canary follow-up — /cinema home signed link does not establish browser login

Observed after merged PR #444:
- user runs `/cinema home`;
- presses **Open Dank Cinema**;
- the Discord button URL is correctly generated as a signed guild/user deep link;
- Samsung Browser reaches Cinema but is not established as signed in and falls back into the membership/sign-in failure path.

Root cause:
- `MovieNightHubView` already calls `cinema_site_url(guild_id, user_id)` and includes `uid/exp/sig`;
- `_site_identity` validates that signature successfully;
- before returning the first HTML response and minting the HttpOnly session/identity/guild cookies, it still required `_fetch_site_member()`;
- a member REST/cache miss therefore rejected the valid signed Discord deep link before the browser-session exchange could complete.

Active correction:
- valid signed Discord Cinema links may perform the one-time initial browser-session exchange without a second member REST lookup;
- Dank Shield must still currently share the exact target guild;
- after the initial page response, normal HttpOnly session, identity, and short-lived guild proof cookies are issued;
- unsigned/expired links do not bypass normal auth/membership checks;
- existing OAuth exact-guild proof behavior remains unchanged.

Regression requirements:
- `/cinema home` Open Dank Cinema button must contain exact guild/user `uid/exp/sig`;
- valid signed entry must return the Cinema page and set session, identity, and guild proof cookies even when member REST is unavailable;
- signed entry must fail if Dank Shield no longer shares the target guild;
- full exact-head CI and companion workflows must pass before merge/deploy.

## Production canary follow-up — Cinema APIs eject valid sessions after guild proof expiry

Observed after merged PR #445 on Samsung Browser:
- `/cinema home -> Open Dank Cinema` successfully reaches the new Cinema shell and real icons;
- after the short-lived guild proof ages out, Home/Profile APIs return `Dank Cinema requires membership in this Discord server`;
- the exact-guild browser session cookie itself is still valid.

Root cause:
- `CINEMA_GUILDS_TTL_SECONDS` is 15 minutes;
- once that signed guild-list proof expires, every Cinema API request calls the bot-side member lookup again;
- the old member lookup collapsed every failure mode into `None`;
- Discord `NotFound` (definitely not a member), rate limiting, forbidden member REST, timeouts, and transient HTTP failures were therefore all treated as the same "member left" result;
- the browser was ejected from a valid exact-guild Cinema session because Discord REST was unavailable, not because Discord confirmed membership ended.

Active correction branch:
`fix/cinema-membership-session-resilience`

Correction contract:
- membership checks become tri-state: `present`, `absent`, or `unavailable`;
- only definitive `discord.NotFound` or the canonical `on_member_remove` event means absent;
- temporary REST/API failures are availability problems, never proof of departure;
- an already-valid exact-guild Cinema session survives temporary membership REST unavailability;
- identity-only access without an exact-guild session still fails closed when membership cannot be proven;
- canonical `on_member_remove` immediately revokes Cinema access for that guild/user;
- canonical `on_member_join` restores access for a rejoined member;
- fresh signed Discord Cinema links and fresh OAuth guild proofs refresh the positive membership state;
- successful membership verification is cached for five minutes and temporary-unavailable state for one minute to avoid hammering Discord REST from Home/Profile/Feeds/Search loading in parallel.

Acceptance:
- Home, Profile, Search, My Stuff, and Feeds remain usable for the active exact-guild Cinema browser session after the 15-minute OAuth guild proof expires;
- temporary Discord REST failure does not produce a false `requires membership` error;
- definitive member absence still returns 403;
- member-remove immediately revokes an existing session and rejoin restores it;
- wrong-guild sessions/proofs remain rejected;
- exact-head CI and all companion workflows must pass before merge/deploy.

## Evidence-backed follow-up — verified Cinema session is the authorization artifact

Evidence from the 2026-10-05 10:49 Samsung/Discord screenshots:
- `/cinema home` produces a signed URL containing the exact guild ID, user ID, expiry, and HMAC signature;
- the URL expiry shown in Discord is 2026-10-05 20:49:41 UTC, so it was valid at screenshot time;
- Samsung Browser loads the Cinema HTML shell and the deployed real SVG icons;
- the first Home API call then returns `Dank Cinema requires membership in this Discord server.`

What is proven from current main:
- that error text can only come from session revocation, session membership state `absent`, or identity membership state `absent`;
- screenshots alone do not distinguish those server-side branches, so no branch-specific claim is justified without Discloud request logs;
- every `dank_cinema_session` cookie is minted only after an authoritative entry proof succeeds: signed Discord guild/user link, Discord OAuth shared-guild proof, or verified guild-open flow.

Correction contract:
- a valid exact-guild Cinema session cookie is the authorization artifact for Cinema API browsing;
- Home/Profile/Search/My Stuff/Feeds do not call Discord member REST again while that session remains valid;
- the bot must still share the exact guild;
- canonical `on_member_remove` remains the immediate in-process revocation owner;
- REST `NotFound` may deny an identity-only/open-guild request but no longer creates a process-lifetime session revocation;
- identity-only access still requires fresh membership proof and remains fail-closed;
- Cinema exact-guild session TTL is bounded to six hours, matching the signed Discord entry window, instead of seven days;
- old longer-lived session cookies are rejected by the stricter validator after deployment;
- `/health` exposes `cinema_auth_contract: signed-session-v3` so production deployment can be proven directly.

Do not claim fixed live until exact-head CI is green, the PR is merged/deployed, `/health` reports `signed-session-v3`, and `/cinema home -> Open Dank Cinema` loads Home plus Profile on Samsung without a membership error.

## PR #447 first exact-head CI failure

Exact head `18df68c13dfb128b857c96bff41864ade1b69612` compiled successfully and ran the full unit suite, finishing with `2850 passed / 3 failed`.

Evidence-backed failures:
- `test_cinema_session_is_bounded_to_signed_entry_window` exposed a real implementation miss: `cinema_session_value()` still clamped caller TTL to 30 days even though the validator/constant had been changed to six hours.
- `test_exact_guild_session_survives_temporary_membership_api_failure` and `test_member_remove_revokes_existing_cinema_session_and_rejoin_restores_it` omitted a `_bot_guild()` fixture, so the newly intentional exact-guild bot-presence guard rejected the synthetic guild.

Correction:
- clamp `cinema_session_value()` directly to `CINEMA_SESSION_TTL_SECONDS` with a five-minute minimum;
- add the missing exact-guild bot fixture only to those two tests;
- do not change the signed-session-v3 authorization contract.

Re-run exact-head CI before marking #447 ready.

## Production canary follow-up — signed entry proof was discarded before Cinema API calls

Evidence from the 2026-10-05 11:33 Samsung screenshots and repository history:
- Cinema HTML shell renders and every tab first shows its loading skeleton;
- every tab then fails with the same membership error;
- API routes are correctly nested under `/cinema/{guild_id}/api/...`, so the cookie Path shape is not the mismatch;
- JavaScript fetches use `credentials: "same-origin"`, so credentials are not explicitly omitted;
- commit `af8c6e286acf5211af87e2d7cfff6ca728e80d4a` changed `AUTH_QUERY = window.location.search` to stripping `uid/exp/sig` and forcing `AUTH_QUERY = ""` after the page load.

Root cause:
- the top-level signed Discord link authenticates the HTML request;
- the SPA then deliberately deletes the signed guild/user proof from the visible URL and from its API transport;
- if Samsung Browser does not present the newly minted HttpOnly Cinema cookie on the immediate SPA API request, every Home/Profile/Search/My Stuff/Feeds call becomes identity-only and falls into the membership gate;
- this explains the identical skeleton-then-membership-failure pattern across every menu surface.

Active correction branch:
`fix/cinema-samsung-signed-api-fallback`

Correction contract:
- capture `uid/exp/sig` in JavaScript memory before stripping them from the visible address bar;
- continue removing the bearer values from browser history immediately;
- reuse that signed proof only for same-origin `/cinema/{guild}/api/...` calls during the current page lifetime;
- keep `credentials: same-origin` so the normal HttpOnly session remains the preferred path;
- signed fallback must obey canonical member-remove revocation and bot-guild presence;
- bump `site.js` to `v=4` to defeat Samsung Browser asset caching;
- expose `/health` marker `cinema_auth_contract: signed-session-v4` so production deployment can be proven directly.

Acceptance:
- `/cinema home -> Open Dank Cinema` loads Home on Samsung without a membership error;
- Home, Search, My Stuff, Feeds, Profile, and Notifications all use the same authenticated API transport;
- visible browser URL no longer contains `uid`, `exp`, or `sig` after page boot;
- signed fallback cannot bypass a real member-remove event;
- exact-head CI and companion workflows pass before merge/deploy.

## Evidence phase — browser-neutral Cinema auth observability

Why this phase exists:
- repeated production screenshots prove the Cinema shell loads but API-backed Home/Search/My Stuff/Feeds/Profile can still fail with the same membership message;
- browser standards do not support the previous guesses: same-origin fetches send cookies, the session Path covers deeper Cinema API paths, SameSite=Lax is valid for same-site/top-level navigation, and history.replaceState does not reload the document;
- current tooling cannot reach the public Discloud hostname from the coding environment, so live request headers/cookies cannot be inspected remotely from here.

Active branch:
`fix/cinema-auth-observability`

Contract:
- do not change authorization decisions in this phase;
- add `/cinema/{guild_id}/api/auth-debug` that reports only safe status labels/booleans, never raw cookie values, signatures, Discord IDs, or bearer tokens;
- distinguish signed query missing/invalid/valid;
- distinguish Cinema session cookie missing/invalid/valid;
- distinguish identity cookie missing/invalid/valid;
- distinguish guild-proof cookie missing/invalid/valid;
- report selected auth source, bot-guild presence, canonical member-revocation state, whether any Cookie header reached the server, request secure flag, and forwarded protocol;
- on any Cinema SPA API failure, automatically fetch this safe diagnostic state and append it to the visible error card;
- log the same non-secret diagnostic summary server-side for Discloud logs;
- bump client asset cache to `site.js?v=5` and health contract to `signed-session-v5-observable` so deployment can be proven.

Acceptance:
- exact-head CI and companion workflows green;
- after deploy, `/health` reports `signed-session-v5-observable`;
- reproduce the failure once in any browser/device and capture the displayed `Diagnostic:` line or matching Discloud `cinema_auth_debug` log;
- only then modify auth behavior according to the observed state.

## Evidence-backed root cause — Cinema used guild cache as installation authority

Production diagnostics from `signed-session-v5-observable` reported:
- `signed=invalid`;
- `session=missing`;
- `identity=valid`;
- `guildProof=invalid`;
- `source=identity`;
- `botGuild=no`;
- `revoked=no`;
- `cookieHeader=yes`.

What this proves:
- browser cookies are reaching aiohttp (`cookieHeader=yes`);
- the identity cookie validates with the current server secret;
- the canonical member lifecycle did not revoke the user;
- the server-side guild gate fails because `cinema_site._bot_guild()` calls only `bot.get_guild(guild_id)` and treats a gateway cache miss as proof that Dank Shield is not in the guild.

Discord.py behavior relevant to the fix:
- `Client.get_guild()` is cache-backed;
- Discord REST guild/member fetches are the authoritative fallback when cache state is missing;
- a cache miss must not be treated as guild absence.

Active correction branch:
`fix/cinema-guild-rest-resolution`

Correction contract:
- add `_resolve_bot_guild()` with cache -> Discord REST fallback;
- resolver returns `present`, `absent`, or `unavailable`;
- Discord REST NotFound/Forbidden means bot genuinely absent;
- HTTP/rate-limit/timeout/transport failure means temporarily unavailable, never absent;
- positive REST guild resolution is cached for five minutes, absent for one minute, unavailable for 30 seconds;
- Cinema member verification uses the resolved Guild object and can then call `fetch_member()` normally;
- signed links, exact-guild sessions, targeted OAuth, and identity-only access all use REST-confirmed guild presence instead of cache-only presence;
- targeted OAuth distinguishes `user is not in guild` from `Dank Shield is not installed in guild`;
- user-facing errors stop blaming member status when the bot itself is absent;
- diagnostics report `botGuildRest=present|absent|unavailable` separately from `botGuildCache=yes|no`;
- `/health` marker becomes `signed-session-v6-guild-rest`.

Acceptance:
- exact-head CI and companion workflows green;
- after deploy `/health` reports `signed-session-v6-guild-rest`;
- reproduce `/cinema home -> Open Dank Cinema`;
- if gateway cache still misses but REST confirms the guild, Home/Search/My Stuff/Feeds/Profile must load;
- if REST confirms the bot is absent, Cinema must explicitly report that Dank Shield is not installed instead of `requires membership`.

## PR #450 first exact-head CI failure

Exact head `c442acac070002834da445d109daa08832d84bd9` compiled successfully and ran the full suite, finishing with `2857 passed / 1 failed`.

Failure:
- `test_discord_signed_cinema_link_still_requires_bot_to_share_target_guild` still mocked only the retired cache-only `_bot_guild()` helper, while the production path now uses `_resolve_bot_guild()`;
- the new positive REST guild cache also revealed a real lifecycle edge case: without explicit invalidation, a recently removed bot could remain REST-confirmed in memory for up to five minutes.

Correction:
- canonical `on_guild_remove` now calls `note_cinema_guild_remove()` to drop positive REST guild state immediately and mark the guild absent;
- canonical `on_guild_join` calls `note_cinema_guild_join()` to clear absence and seed positive guild state;
- member verification caches for that guild are cleared on bot removal;
- the signed-link regression now mocks the new tri-state resolver instead of the retired cache-only helper;
- a focused regression proves guild removal invalidates positive REST cache and guild rejoin restores it.

Do not merge #450 until the new exact head completes green.

## Evidence-backed follow-up — /cinema must be guild-installed, not user-installed

Production evidence after merged PR #450:
- Cinema diagnostics report `botGuildRest=absent` and `botGuildCache=no` for the guild route;
- Discord simultaneously allows `/cinema home` to execute in that server;
- browser transport is not the blocker (`cookieHeader=yes`, identity cookie valid, revocation false).

Discord install-context evidence:
- Discord supports apps installed to a user account; those apps can run commands inside servers without the bot being installed as a server member;
- server-installed apps appear as server members and have guild integration context;
- discord.py 2.7 exposes `Interaction.is_guild_integration()` / `is_user_integration()` and `AppInstallationType` on command groups.

Repository root cause:
- `build_cinema_command_group()` did not set `allowed_installs` or `allowed_contexts`, so `/cinema` inherited the Discord application's install defaults;
- if the application allows user installs, Discord can expose `/cinema` through that personal install even though Cinema requires the server-installed bot;
- Cinema then correctly fails REST guild verification because the bot user behind that integration is not a member of the target server.

Active branch:
`fix/cinema-guild-install-context`

Correction contract:
- `/cinema` root group sets `allowed_installs=AppInstallationType(guild=True, user=False)`;
- `/cinema` root group sets guild-only allowed contexts (no DM/private-channel contexts);
- every `/cinema` child has a runtime guard using `Interaction.is_guild_integration()` so stale user-install command copies fail before generating a website link;
- runtime logs record only safe booleans: guild context, guild install, user install, and application/client identity match;
- command payload/fingerprint changes because discord.py serializes integration types, forcing a global command resync;
- `/health` marker becomes `signed-session-v7-guild-install`.

Acceptance:
- exact-head CI and companion workflows green;
- after deploy `/health` reports `signed-session-v7-guild-install`;
- `/cinema` must be offered only through the server-installed app;
- stale/personal `/cinema` invocation must show the explicit server-install requirement instead of opening the site;
- a guild-installed `/cinema home` interaction must generate Open Dank Cinema and the web runtime must resolve that guild through Discord REST.

## Evidence-backed root cause — Discord snowflake rounded by JavaScript Number

Production logs on 2026-10-05 exposed the exact guild-ID corruption:
- real Discord guild from the `/cinema home` signed link and recovery logs: `1514374173517152418`;
- Cinema API/auth logs requested guild: `1514374173517152500`;
- the web runtime then received Discord REST `NotFound`, making `botGuildRest=absent` truthful for the wrong rounded guild ID.

Root cause in current main:
- `_site_html()` serialized `guildId` and `userId` as JSON numbers via `int(...)`;
- `cinema_site.js` declared `CinemaBoot` as `{guildId:number,userId:number}`;
- Discord snowflakes exceed JavaScript's IEEE-754 safe integer range;
- parsing the boot payload as JavaScript Number rounded the last digits;
- `API_BASE = /cinema/${BOOT.guildId}/api` therefore called every SPA API under the wrong guild route.

Why this explains every diagnostic:
- signed query becomes invalid because the signature was issued for the exact guild ID, not the rounded one;
- exact-guild session cookie is missing because its cookie Path is scoped to the exact guild route;
- guild proof is invalid because the API request uses the wrong guild;
- Discord REST returns NotFound because the rounded guild ID does not exist;
- identity cookie remains valid because it is user-only and not guild-route scoped;
- cookieHeader remains yes because cookies in general still reached aiohttp.

Active correction branch:
`fix/cinema-snowflake-string-ids`

Correction contract:
- serialize Discord `guildId` and `userId` as decimal strings in the Cinema boot payload;
- declare both fields as strings in the client;
- build `API_BASE` from the exact string with no Number/parseInt conversion;
- leave ordinary numeric values such as TMDB IDs, seasons, ratings, durations, and hardware counts unchanged;
- client audit confirms the only other Discord-shaped client identifier is `room_id`, already handled with `String(...)`;
- bump Cinema JS asset to `site.js?v=7`;
- unify health and in-page diagnostics at `signed-session-v8-snowflake-safe`.

Regression:
- use exact production-sized IDs `1514374173517152418` and `629459300854661120`;
- assert `_site_html()` emits quoted string values and never numeric snowflake literals;
- assert client typedef/default/API base keep the IDs as strings;
- assert no `guildId:number` or `userId:number` remains.

Acceptance after deploy:
- `/health` reports `signed-session-v8-snowflake-safe`;
- a fresh `/cinema home` link for guild `1514374173517152418` must cause API/log routes to use that exact same ID, never `1514374173517152500`;
- Home/Search/My Stuff/Feeds/Profile must no longer fail from the rounded-guild membership error.

## Production follow-up — Cinema responsive layout and mobile scale

Observed after the snowflake/auth fix succeeded:
- Cinema details now load successfully, proving the auth chain is working;
- on a phone, the whole SPA can render visually shrunken, with tiny header/nav/details content that resembles a desktop page scaled down;
- the mobile bottom navigation is visible, proving the mobile breakpoint is active even while the visual scale is too small.

Scope:
- layout and viewport only;
- do not change auth, playback, Watch Party/Private Session logic, providers, queues, or persistence.

Responsive correction contract:
- keep desktop content capped at 1560px and preserve the desktop multi-column experience;
- preserve tablet breakpoints at 1199px/820px and phone breakpoints at 620px/390px;
- strengthen viewport meta with `initial-scale=1` and `minimum-scale=1` while leaving zoom-in available;
- add `interactive-widget=resizes-content` for supporting browsers so the keyboard can resize layout content cleanly;
- normalize text-size adjustment across mobile browsers;
- guarantee shell/page/grid children can shrink without horizontal min-content blowouts;
- collapse phone details into a readable stacked hero with controlled backdrop height;
- make cast and media rails touch-scroll cleanly;
- make source/feed/error copy wrap anywhere instead of expanding the page;
- make bottom navigation use equal minmax tracks with larger touch targets;
- use a two-column phone result grid and a one-column phone source/feed layout;
- cache-bust Cinema CSS to `site.css?v=4`.

Cross-browser intent:
- same CSS/HTML path for Chrome/Chromium, Samsung Internet, Firefox Android, Safari/iOS, desktop Chrome/Edge/Firefox/Safari;
- no user-agent sniffing and no Samsung-only rules;
- real desktops remain governed by viewport width and the existing 1560px content cap.

Acceptance:
- phone portrait/landscape opens at readable scale and does not look like a miniaturized desktop page;
- header logo/actions and bottom navigation remain thumb-readable;
- details title/poster/overview/actions stack without horizontal overflow;
- Cast and Similar Titles scroll horizontally instead of forcing page width;
- source rows wrap safely;
- tablet layout remains balanced;
- desktop and ultrawide retain intentional multi-column spacing and do not inherit phone stacking;
- exact-head CI and companion workflows green before merge.

## Feed Center results — source manager was not exposing discoveries

Production observation after responsive layout #453:
- Feed Center renders configured source cards, health, Refresh/Edit/Disable/Delete controls;
- the page does not render actual entries discovered by those feeds;
- EzTV can show Online/last refresh while the user has no visible result list.

Repository evidence:
- `refresh_feed()` already resolves real structured/RSS entries and records them through `record_feed_discoveries()`;
- discoveries are already persisted in `dank_cinema_feed_discoveries` with title, source, category, TMDB identity when resolved, poster/backdrop metadata, first_seen_at, and last_seen_at;
- `list_recent_discoveries()` already exists;
- `/api/feeds` previously returned only source definitions plus source-local `newly_discovered` title strings;
- `renderFeeds()` rendered only source-management cards.

Active branch:
`fix/cinema-feed-results`

Correction contract:
- `/api/feeds` returns up to 36 real recent discovery rows under `results`;
- merge freshly refreshed in-memory results ahead of durable results so Refresh updates the page immediately;
- durable results survive restarts through the existing discovery table;
- canonical TMDB-matched results expose title/media type/TMDB ID/poster/backdrop/year/rating/overview;
- unresolved releases still expose real release title/source/category and real seed/file-size stats when available in the current runtime;
- raw playable refs/magnets/torrent URLs are NOT returned in the Feed Center result payload;
- Feed Center renders `Latest Feed Results` above `Sources`;
- TMDB-matched result opens in-app Cinema Details;
- unresolved result routes to in-app Cinema Search instead of an external browser;
- empty state explicitly instructs the user to Refresh an enabled RSS/structured source;
- CSS uses compact responsive result cards: 3 columns desktop, 2 tablet, 1 phone;
- cache-bust site JS to v8 and CSS to v5.

Acceptance:
- refreshed EzTV/RSS/structured source visibly produces result cards in Feed Center;
- previously persisted feed discoveries appear after restart/redeploy;
- result cards show source/category and canonical poster/details when TMDB matching succeeds;
- unresolved releases stay usable through in-app Search;
- source management remains below the results section;
- exact-head CI and companion workflows green before merge.

## Evidence-backed Feed Center defect — static RSS refresh was filtering for `movie`

Production evidence:
- Feed Center results UI is deployed and visible;
- EzTV reports Online and a fresh timestamp but `Latest Feed Results` remains empty.

Code-path proof:
- EzTV is configured as a static RSS/Atom source;
- `refresh_feed()` defaulted Custom-category refreshes to the literal query `movie`;
- `_search_url(..., static_feed=True)` fetched the static feed URL itself;
- `_read_structured_items_limited()` passed the same `movie` query into `_extract_feed_items()`;
- `_extract_feed_items()` rejected every RSS item whose title did not match `movie`;
- zero matches returned `variants=()` with no error, so Feed Center marked the source Online while recording zero discoveries.

External documentation check:
- EZTV documents `https://myrss.org/eztv` as an RSS feed intended for clients to automatically download all published items;
- the resolver already supports EZTV-style magnet URI and `.torrent` references.

Active branch:
`fix/cinema-feed-refresh-unfiltered-rss`

Correction contract:
- static RSS/Atom Feed Center refresh with no explicit query fetches the endpoint unchanged and ingests all playable entries;
- blank query means no title filter for RSS/Atom extraction, while a real explicit query still filters titles;
- structured JSON search sources keep their category-based default query behavior (`movie`, `tv`, `anime`, `documentary`);
- source search during movie/details discovery remains query-filtered and unchanged;
- Feed Center source cards expose exact playable-result count for the most recent refresh;
- if a feed is reachable but contains zero playable magnet/.torrent entries, show that explicit warning instead of only `Online`;
- cache-bust Cinema JS to `site.js?v=9`.

Regression:
- static feed URL accepts blank refresh query;
- a two-entry RSS fixture with titles that do not contain `movie` returns both entries for blank refresh and zero for explicit `movie` filter;
- Feed Center RSS refresh passes an empty query;
- JSON Movies refresh still passes `movie`;
- UI renders result count and discovery warning.

Acceptance after deploy:
- Refresh EzTV;
- source card must report a truthful playable-result count;
- when playable feed entries exist, Latest Feed Results must populate;
- if count is zero, the card must explicitly say the reachable feed returned no playable magnet/.torrent items, which moves the next investigation to feed payload compatibility rather than pretending discovery succeeded.

## Feed Center usability follow-up — search, pagination, and poster enrichment

Production observation after #455:
- Feed Center now ingests and displays EzTV RSS results correctly;
- result list has no Feed Center search UI;
- result list has no pagination and grows as one continuous page;
- TMDB-matched titles such as CIA/FBI show posters, while unresolved releases such as `Collision 2026 S01E21 ...` render without artwork and fall back to Search in Cinema.

Evidence:
- `search_discoveries()` already exists and general Cinema search already uses feed discoveries, but Feed Center never exposed it;
- Feed Center API previously loaded a bounded recent batch only, with no page/offset/count contract;
- `Collision (2026)` is a real TV title with TMDB identity 331033, so at least some blank Collision cards are enrichment misses rather than unavailable artwork;
- episode-style release names with a year were searched as e.g. `collision 2026`; the resolver did not force TV for Custom-category `SxxExx` releases or retry a year-stripped title.

Active branch:
`fix/cinema-feed-search-pagination-enrichment`

Current status:
- PR #456 is the single active implementation task and remains draft;
- exact-head CI previously reached 2870 passed / 1 failed;
- the only failure was the Feed Center provenance copy regression;
- current head restores the original real-discovery provenance sentence while preserving the new result count/search/pagination;
- branch is 0 behind main and mergeable;
- PR #457 (EZTV/provider identity routing) is suspended until #456 reaches its Definition of Done.

Expanded RSS / Feed Center contract:
- Latest Feed Results groups duplicate releases by canonical title/episode and compares source, quality, codec, seeds, size, release group, and first/last-seen history;
- My Feed combines personal Feed Rules, watchlist matches, private-feed discoveries, quality upgrades, new episodes, and queue suggestions;
- personal Feed Rules support followed titles, actors/creators, genres, studios, saved searches, filters, collections, routing, and alert modes;
- guild-scoped Collection/Route rules require Manage Server; personal rules remain owner-only;
- user-private RSS/Atom and structured JSON sources reuse the existing safe source validator/resolver and persist only to service-role-only private discovery storage;
- private discoveries never enter the guild-wide discovery table;
- user preferences persist playable-only behavior, minimum seeds, preferred resolutions/codecs/languages, feed alert mode, and queue-suggestion preference;
- new-episode/feed-match alerts use the existing Cinema inbox with dedupe and instant/daily/off behavior rather than unsolicited DMs;
- source trust is derived from durable refresh success/failure and playable-result history;
- Add to Queue uses the canonical MovieNightRoom queue and exact source resolver, and only succeeds for the current signed-in host of an active room;
- Feed Rules include title, franchise, actor/creator, genre, studio, saved search, filter, collection, routing, and private-source matching;
- release filters include playable-only, minimum seeds/peers, resolution, codec, language, HDR, subtitles, excluded terms, source/category, and minimum/maximum file size;
- shared and private structured feeds participate in a durable bounded auto-refresh scheduler (15-minute default, 5-minute floor, paced requests, bounded batches); private refresh remains owner-isolated;
- raw magnet/torrent/source refs remain excluded from Feed Center and personalization payloads;
- a new service-role-only migration owns Feed Rules, private discoveries, and durable source health; Cinema SQL CI replays and audits both Cinema migrations.

Correction contract:
- database-backed Feed Center pagination uses PostgREST exact count plus range/offset, default 8 results per page and max 24;
- search covers both canonical discovery title and `metadata.release_title`, so `FBI`, `Collision`, or `S01E21` can match;
- `/api/feeds` accepts `q`, `page`, and `page_size` and returns a pagination object with total/pages/previous/next;
- Feed Center renders a search field, Search/Clear controls, total result count, and Previous/Page/Next controls;
- episode-looking releases (`SxxExx` or `NxM`) force TV matching even when source category is Custom;
- episode discovery tries a year-stripped query first (e.g. `collision`) and then the original cleaned query (`collision 2026`);
- unresolved persisted discoveries receive a bounded TMDB enrichment retry (max 4 per page, 30-minute cooldown), and successful poster/title/TMDB matches are persisted;
- genuinely unresolved cards receive an intentional branded placeholder instead of an empty image hole;
- cache-bust Cinema JS to v10 and CSS to v6.

Acceptance:
- Feed Center search can find by canonical title and release token;
- eight-result pages expose truthful total and navigation;
- page navigation queries storage instead of client-slicing a fixed recent batch;
- Collision-style episode releases can resolve as TV and gain TMDB poster/details when metadata exists;
- existing general Cinema search, source refresh, playback refs, and source management remain unchanged;
- exact-head CI and companion workflows green before merge.


## ACTIVE — Cinema Library Intelligence / bot-native Trakt-style experience

Production baseline:
`main @ 3e86f4ffd701fc1801bc492331393c23d84b8bd3` (PR #456 merged)

Active branch:
`rebuild/cinema-library-intelligence-clean`

Active PR:
`#459 — Build bot-native Cinema Library intelligence`

Isolation note:
- PR #458 was closed after a separate standalone website-playback task was interleaved onto its branch.
- The full mixed state is preserved at `backup/cinema-library-standalone-interleaved-20261006`.
- PR #459 contains only the Library intelligence task.

Single active task:
Build a bot-native Cinema Library intelligence layer. This is intentionally scoped to activity that happens inside Dank Cinema/Dank Shield; no cross-platform scrobbling or external Trakt-style account synchronization.

Ownership contract:
- Cinema Library owns durable user state: watch history, progress, watched/unwatched, watchlist, favorites, ratings, custom lists, rewatch counts, stats, and recommendation signals.
- Dank Theater remains the only playback/progress authority and writes Library activity through one service path.
- TMDB remains canonical identity/metadata and supplies seasons, episode dates, genres, cast, studios, franchises, and recommendation candidates.
- Feed Center remains availability/release intelligence and overlays whether a Library/recommendation/upcoming item currently has playable discoveries.
- My Feed continues to own feed-driven notifications; Library may supply watchlist/history/rating/favorite signals to personalize those matches.
- Notifications remain inside the existing Cinema inbox; no unsolicited DM behavior is introduced.

Implementation goals:
- durable first-watch / last-watch / completion / rewatch tracking without writing a history row on every heartbeat;
- favorites and 1–10 ratings on canonical movie/TV items;
- custom personal lists with ordered items;
- manual mark watched / unwatched while preserving canonical progress behavior;
- richer My Stuff views: Continue Watching, Watchlist, Favorites, History, Watch Again, Lists, Stats;
- Because You Watched and Recommended For You rails derived from Dank Cinema activity only;
- upcoming / next-episode intelligence from TMDB for followed/watchlisted/in-progress TV;
- Feed availability overlay for watchlist, recommendations, and upcoming episodes;
- new-episode availability stays feed-driven, not air-date-driven;
- watch-party/private-session context recorded in watch events for stats;
- group recommendation support must be derived from active room participants without exposing one user's private Library to another user;
- mobile-first UI remains usable on Samsung Browser while desktop/tablet/ultrawide remain first-class.

Definition of Done:
- migration is idempotent and service-role-only;
- existing progress/watchlist contracts remain backward compatible;
- exact-head CI + Cinema SQL + companion workflows green;
- no raw playback/source refs added to Library payloads;
- existing Feed Center and Theater ownership boundaries remain intact.
