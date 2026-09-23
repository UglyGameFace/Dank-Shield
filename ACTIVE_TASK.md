# ACTIVE TASK

## Active task / desired outcome

**P0-EXIT-FONT-001 — Cross-guild Exit Card font/Unicode consistency**

Ensure the canonical live Exit Card renders the same supported Unicode correctly in every guild using the same deployed Dank Shield runtime, while preserving intentional per-guild design choices such as selected style and uploaded custom font assets.

## Status

**INVESTIGATING — execution path and root cause narrowed**

Branch: `fix/exit-card-font-cross-guild-20260923`

Base: current `main` at `0935f80071723b878ba1fb85a3402608512d1aec`.

## Previous task closed

**P0-SETTINGS-001B — Spam Guard core settings + canonical setup persistence** is complete.

PR #297 merged as:

`0935f80071723b878ba1fb85a3402608512d1aec`

Exact implementation head:

`c3145d7aee34570fd99a26acc87bd10a0b597e9b`

Exact-head Ubuntu/Termux validation passed:

- diff integrity;
- Python compile;
- focused settings regressions: **47 passed, 1 warning, 0 failures**;
- Protection + Invite regressions;
- full Python test suite;
- standalone tool checks;
- public setup / command-surface / friction / invite-permission / setup-safety audits;
- Dank Design Smart Auto-Detect audit;
- role-truth audit;
- event-boundary audit;
- final exact-head/diff check.

Post-merge verification confirmed the Spam Guard registry/persistence ownership split on `main`.

## Reported production symptom

A prior lifecycle-card Unicode/font repair appears correct in one guild but an Exit Card in another guild using the same bot did not show the expected font/characters.

The previous repair landed on September 18 through these code-level changes:

- Unicode-preserving card fallback engine;
- comprehensive font-pack requirements;
- lifecycle-card rendering through that fallback engine;
- JustMyType 0.3 compatibility alignment.

Those changes are global code, so a guild-specific mismatch must be explained by runtime/config/input differences rather than separate guild code versions when both guilds are served by the same process.

## Execution path confirmed

Canonical live leave flow:

`member_lifecycle_router_guard.py`
→ `exit_card_runtime.send_live_exit_card`
→ `exit_card_service.exit_card_file`
→ `exit_card_renderer.render_exit_card`
→ `welcome_card_typography_engine._fitted_tile`
→ `unicode_font_fallback.render_text_mask`

Ownership findings:

- one canonical live Exit Card sender is reachable;
- the old lifecycle sender is not registered through the public command profile;
- Exit Card rendering uses the shared Unicode-aware typography engine;
- dynamic member names are preserved as exact Unicode rather than NFKC-normalized or case-rewritten;
- per-grapheme fallback is available through bundled/JustMyType fonts.

## Important per-guild behavior

These values are intentionally stored per guild:

- `exit_card_font_style`;
- `welcome_card_font_style`;
- `welcome_card_custom_font_b64` and related custom-font metadata;
- Exit Card theme/colors/background/shuffle settings.

Exit Card style resolution prefers an explicit guild `exit_card_font_style`; only when that value is absent does it inherit the guild's Welcome Card font style.

Uploaded custom fonts are shared between Welcome and Exit cards **inside the same guild**, not across every guild using Dank Shield.

This explains why visual font selection can legitimately differ between guilds, but it does **not** by itself explain a failure of the global Unicode fallback engine.

## Test-gap finding

Current runtime Unicode coverage proves the decorative Unicode display name reaches the Exit Card renderer unchanged, but it monkeypatches `exit_card_file`.

Current Exit Card renderer tests cover normal Latin names and long-name fitting, but they do not render representative decorative Unicode through the real font fallback stack.

Therefore the previous suite could pass while a real glyph-coverage/rendering regression remained.

## Scope

In scope:

- reproduce the real Exit Card renderer path with decorative/mathematical Unicode;
- verify installed/bundled fallback discovery used by production;
- distinguish intentional per-guild style/custom-font state from renderer failure;
- add renderer-level regression coverage that validates actual rendered output rather than only transport;
- repair the smallest canonical font/fallback/config issue proven by that reproduction;
- verify two distinct guild-style configurations use the same Unicode-capable renderer contract;
- preserve existing Welcome Card behavior and per-guild customization.

Out of scope:

- quiet-server partner live activity;
- Presence Intent feature work;
- redesigning Welcome/Exit Card Studio;
- moving unrelated guild settings;
- broad lifecycle-event refactors.

## Validation checkpoint

Exact-head Ubuntu/Termux reproduction on `c9d8b2a636ccae8ddaf3f1b94b1bd7b07936e7e2` passed:

- `tests/test_exit_card_renderer.py`
- `tests/test_welcome_card_unicode_fallback.py`
- **11 passed, 0 failures**

That run exercised the real Exit Card renderer for `ᗩ ᗰ ᒪ`, `PΛMELA`, and `𝓔𝔂𝓮𝔃 𝓞𝓯 𝓑𝓸𝓫` across multiple built-in font styles. The canonical renderer therefore does not reproduce the reported cross-guild failure in the validated Ubuntu environment.

A new regression on head `0e7ea2a6c5105913bb33c00b450df61bf22fef45` now disables registered/system fallback discovery entirely and requires the bundled `NotoSansCanadianAboriginal-VF.ttf` asset to render the known live failing name `ᗩ ᗰ ᒪ` through the real Exit Card renderer. This distinguishes deterministic repo-bundled coverage from host-specific fonts.

## Current findings / root-cause status

**Root cause narrowed, but not yet claiming the production trigger.**

What is ruled out:

- separate Exit Card implementations controlling different guilds;
- intentional NFKC rewriting of live card names;
- an obvious old lifecycle sender still registered alongside the canonical sender.

What remains to prove:

1. whether the bundled long-tail fallback alone succeeds with registered/system fonts disabled;
2. whether the affected production card was generated before the Unicode fallback deployment and is therefore an immutable old PNG;
3. whether the affected guild has an explicit per-guild Exit Card/custom-font selection or a different unsupported Unicode sequence.

## Backlog

- Quiet-server partnered-guild live activity panel using authorized cross-guild relationships, Presence/Member/Voice/message activity where appropriate, privacy controls, and rate-limit-safe refreshes.
- Verify the bot startup code explicitly requests the already-enabled Presence, Server Members, and Message Content gateway intents as part of that later partner-activity task.

## Next step

Validate head `0e7ea2a6c5105913bb33c00b450df61bf22fef45` with the focused Exit Card renderer test. If the bundled-font-only case passes, do not alter the canonical renderer; move to production/per-guild evidence because the global code path is proven deterministic.
