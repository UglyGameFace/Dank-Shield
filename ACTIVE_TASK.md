# ACTIVE TASK

## DS-PROFILE-STUDIO-LIVE-010 — Instant, burst-safe, legible live signatures at public scale

**Status:** MERGE READY / DEPLOYED SMOKE PENDING
**Branch:** `fix/live-signature-instant-burst-runtime`
**PR:** #140
**Base:** current `main` after PR #138
**Validated executable head:** `0284311201a571d43732c78b41a2d17d9efe3de8`

## Single Active Task Lock

Do not switch to unrelated implementation work until the deployed bot confirms immediate first delivery, burst coalescing, readable output, clickable saved social links, and no visible technical ownership footer.

## Confirmed findings

- The deployed runtime intentionally defaulted to a 4-second debounce, a 30-second different-speaker replacement cooldown, and a 180-second same-speaker suppression window.
- Those values directly explain the reported behavior: one delayed signature followed by minutes where the same user appeared to receive nothing.
- Every eligible message forced `get_guild_config(..., refresh=True)`, bypassing the existing 60-second per-guild cache and creating a Supabase read on the message hot path.
- Every replacement re-read durable card state and fetched the stored Discord message even after the process already knew the current card.
- `on_ready()` launched a global persisted-state reconciliation and up-to-100-message history scan per configured channel, which is not a safe reconnect path for 1,000+ guilds.
- The compact image renderer re-downloaded and re-rendered an unchanged member signature for every replacement.
- The required message UX is leading-edge immediate delivery after inactivity, with rapid back-to-back messages coalesced so the same speaker does not create repeated visible signatures and only the latest changed speaker receives one trailing replacement.
- Public social identities were drawn as plain image text while their saved official URLs were not clearly clickable in the card.
- The 1080×220 live image was too shallow to read comfortably on Discord mobile.
- Durable ownership was exposed as a visible `user/trigger` footer even though members do not need that internal data.

## Scope

- Remove intentional leading delay: the first eligible message after channel inactivity schedules immediately.
- Use a short burst quiet window only for trailing coalescing.
- Keep one visible signature after same-speaker message bursts; do not retain a signature after every message.
- Collapse rapid speaker changes to the latest speaker instead of replaying every intermediate speaker.
- Reposition the same speaker once after a burst settles so the signature ends below the latest message.
- Read guild configuration through the existing cache instead of forcing Supabase on every message.
- Keep warm current-card ownership in process memory and use durable state only for cold recovery and restart safety.
- Cache rendered signature bytes by the effective avatar/style/privacy/content fingerprint with bounded TTL and size.
- Skip global history scans on ready/reconnect; recover ownership lazily per active channel.
- Keep explicit deep reconciliation available for maintenance and small development installs while bounding public setup behavior.
- Render saved official social URLs as neat clickable links inside the same Discord embed.
- Keep username-only platforms visible without inventing unsafe or incorrect URLs, and clearly flag URL-capable entries that still need an official link.
- Increase the live signature canvas to 1080×300 with larger name text, chips, and avatar while retaining a compact horizontal shape.
- Move ownership metadata to invisible embed/attachment markers while retaining cleanup compatibility for existing footer-marked cards.
- Preserve privacy precedence, one bot-owned visible card per channel, durable state safety, failure cleanup, and member/server disable controls.
- Add latency diagnostics (`render_ms`, `total_ms`, leading/trailing source) to every successful post.

## Validation

- [x] Focused responsive scheduler/cache harness covers immediate leading delivery, same-speaker trailing reposition, latest-speaker collapse, leading-render concurrency, warm state reuse, and image cache reuse.
- [x] Visual/social harness covers 1080×300 output, clickable official links, missing-link warnings, username-only identities, invisible ownership markers, and legacy footer compatibility.
- [x] Legacy 4/30/180 timing values migrate to the responsive runtime policy.
- [x] First message after inactivity posts without an intentional timer sleep.
- [x] Message hot path does not request a forced guild-config refresh.
- [x] Same-speaker back-to-back messages leave one visible signature after the burst settles.
- [x] Rapid alternating speakers collapse to the latest trailing speaker.
- [x] Warm channel replacements do not reread durable card state.
- [x] Unchanged effective signatures reuse bounded rendered-image cache entries.
- [x] Clickable saved profile URLs appear in the embed and the visible technical footer is absent.
- [x] Existing footer-marked cards remain recognized for safe cleanup.
- [x] Existing delivery, cleanup, privacy, setup, lifecycle, and reconciliation regression tests pass.
- [x] Full suite passed: 747 tests, standalone checks, compilation, whitespace, and every repository audit.
- [x] Focused profile runtime diagnostics and application-command size diagnostics passed.
- [x] Branch is conflict-free, mergeable, and zero commits behind `main` after final executable validation.
- [ ] Deployed smoke shows low warm latency, one final visible signature after a rapid message burst, readable card text, working saved links, and no visible technical footer.

## Cleanup

- [x] No forced per-message config refresh, long-cooldown scheduler, unbounded render cache, reconnect history storm, or temporary workflow remains in the implementation.
- [x] Incoming Discord message/config references are released when workers finish.
- [x] Legacy footer ownership remains compatibility-only; newly posted cards do not display it.
- [x] Diff inspected for conflicts with PR #138 setup activation and PR #139 durable delivery repair.
- [x] Obsolete long-cooldown and startup-reconciliation test expectations were replaced with the responsive and lazy-recovery contracts.
- [x] Focused diagnostics workflow is permanent, path-scoped, and contains no self-modifying integration behavior.

## Backlog

- Fix departed-member reconciliation async-generator handling.
- Review contradictory worker startup wording.
- Enable automatic sharding before the public bot approaches Discord's recommended shard threshold.
