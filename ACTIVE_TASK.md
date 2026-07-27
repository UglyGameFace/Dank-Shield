# ACTIVE TASK

## DS-PROFILE-STUDIO-LIVE-010 — Instant, burst-safe live signatures at public scale

**Status:** IMPLEMENTATION / EXACT-HEAD CI REQUIRED
**Branch:** `fix/live-signature-instant-burst-runtime`
**Base:** current `main` after PR #138

## Single Active Task Lock

Do not switch to unrelated implementation work until live signatures respond immediately after channel inactivity, rapid messages collapse without signature spam, warm traffic avoids per-message database/state reads, exact-head validation passes, and the deployed Discord smoke confirms the timing.

## Confirmed findings

- The deployed runtime intentionally defaulted to a 4-second debounce, a 30-second different-speaker replacement cooldown, and a 180-second same-speaker suppression window.
- Those values directly explain the reported behavior: one delayed signature followed by minutes where the same user appeared to receive nothing.
- Every eligible message forced `get_guild_config(..., refresh=True)`, bypassing the existing 60-second per-guild cache and creating a Supabase read on the message hot path.
- Every replacement re-read durable card state and fetched the stored Discord message even after the process already knew the current card.
- `on_ready()` launched a global persisted-state reconciliation and up-to-100-message history scan per configured channel, which is not a safe reconnect path for 1,000+ guilds.
- The compact image renderer re-downloaded/re-rendered an unchanged member signature for every replacement.
- The required UX is leading-edge immediate delivery after inactivity, with rapid back-to-back messages coalesced so the same speaker does not create repeated visible signatures and only the latest changed speaker receives one trailing replacement.

## Scope

- Remove intentional leading delay: the first eligible message after channel inactivity schedules immediately.
- Use a short burst quiet window only for trailing coalescing.
- Keep one visible signature for same-speaker message bursts; do not retain a signature after every message.
- Collapse rapid speaker changes to the latest speaker instead of replaying every intermediate speaker.
- Reposition the same speaker again only after the channel has been quiet long enough to begin a new burst.
- Read guild configuration through the existing cache instead of forcing Supabase on every message.
- Keep warm current-card ownership in process memory and use durable state only for cold recovery/restart safety.
- Cache rendered signature bytes by the effective avatar/style/privacy/content fingerprint with bounded TTL and size.
- Skip global history scans on ready/reconnect; recover ownership lazily per active channel.
- Keep explicit deep reconciliation available for maintenance and small development installs while bounding public setup behavior.
- Preserve privacy precedence, one bot-owned visible card per channel, durable state safety, failure cleanup, and member/server disable controls.
- Add latency diagnostics (`render_ms`, `total_ms`, leading/trailing source) to every successful post.

## Validation

- [x] Focused six-test scheduler/cache harness passes locally with stubbed Discord/storage boundaries.
- [ ] Legacy 4/30/180 timing values migrate to the responsive runtime policy.
- [ ] First message after inactivity posts without an intentional timer sleep.
- [ ] Message hot path does not request a forced guild-config refresh.
- [ ] Same-speaker back-to-back messages keep one visible signature.
- [ ] Rapid alternating speakers collapse to the latest trailing speaker.
- [ ] Warm channel replacements do not reread durable card state.
- [ ] Unchanged effective signatures reuse bounded rendered-image cache entries.
- [ ] Existing delivery, cleanup, privacy, and reconciliation regression tests pass.
- [ ] Full unit suite, standalone checks, compilation, whitespace, and every repository audit pass on the exact head.
- [ ] Deployed smoke shows a warm first-after-idle signature without multi-second delay and no repeated visible signature spam during a message burst.

## Cleanup

- [ ] No duplicate scheduler, legacy long-cooldown path, unbounded cache, reconnect history storm, or temporary workflow remains.
- [ ] Diff inspected for conflicts with PR #138 setup activation and PR #139 durable delivery repair.

## Backlog

- Fix departed-member reconciliation async-generator handling.
- Review contradictory worker startup wording.
- Enable automatic sharding before the public bot approaches Discord's recommended shard threshold.
