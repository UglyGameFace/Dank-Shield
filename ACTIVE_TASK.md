# ACTIVE TASK

## Active task / desired outcome

**P0-EXIT-FONT-001 — Cross-guild Exit Card font/Unicode consistency**

Prove and repair the exact reason an Exit Card could render decorative Unicode
correctly in one guild but not another while both guilds use the same deployed
Dank Shield runtime.

## Status

**INVESTIGATION REPRODUCTION SYNCED TO CURRENT MAIN — exact-head validation pending**

Branch: `fix/exit-card-font-cross-guild-20260923`

Base after sync: `main@e6010631ac554c5342a6d1f2642e561f29376c94`

## Previous task closed

PR #289 — Server Stats repair/customization — was merged by the owner before this
task resumed.

## Reported production symptom

A previous lifecycle-card Unicode/font repair appeared correct in one guild but
an Exit Card in another guild using the same bot did not show the expected
font/characters.

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
- Exit Cards and Welcome Cards share the Unicode-aware typography engine;
- dynamic member names preserve exact Unicode rather than normalizing decorative
  characters away;
- per-grapheme fallback is available through bundled and registered fallback fonts.

## Main-sync review

The branch was 47 commits behind current main.

The exact production rendering path and `tests/test_exit_card_renderer.py` were
compared against the branch's original base. None changed on main during those 47
commits. Therefore the sync preserves current main wholesale and reapplies only
this task's renderer-level reproduction test plus this task record.

No production runtime code is changed yet because the renderer failure has not
been reproduced.

## Per-guild state that can legitimately differ

These values are intentionally guild-scoped:

- `exit_card_font_style`;
- `welcome_card_font_style`;
- `welcome_card_custom_font_b64` and custom-font metadata;
- Exit Card theme/colors/background/shuffle settings.

An explicit Exit Card style overrides that guild's Welcome Card style. Uploaded
custom fonts are shared between Welcome and Exit only inside the same guild.

Those settings explain visual differences but should not disable fallback for
unsupported glyphs.

## Test gap / reproduction

The existing live-runtime regression proves Unicode reaches `exit_card_file`
unchanged, but it monkeypatches the renderer.

The branch adds real-renderer coverage for:

- `ᗩ ᗰ ᒪ`;
- `PΛMELA`;
- `𝓔𝔂𝓮𝔃 𝓞𝓯 𝓑𝓸𝓫`;

across multiple built-in styles, and separately disables registered/system
fallback discovery to require the repository-bundled
`NotoSansCanadianAboriginal-VF.ttf` path for the known long-tail glyph case.

## Root-cause status

Not yet claiming a production trigger.

If the exact-head bundled-font-only renderer test passes, the global renderer is
deterministic on the deployed code path. The remaining production explanations
are then outside renderer ownership:

1. the observed card was generated before the Unicode fallback deployment and is
   an immutable older PNG;
2. the affected guild selected a different explicit Exit/custom font and the
   complaint is visual-style consistency rather than missing-glyph fallback;
3. the production input contains a different unsupported Unicode sequence than
   the known reproduced names;
4. the running deployment is not actually on the commit assumed by the report.

Do not add another font shim unless one of those is disproved and the canonical
renderer itself reproduces the failure.

## Scope

In scope:

- real Exit Card renderer Unicode reproduction;
- bundled fallback discovery;
- built-in style coverage;
- per-guild font/config execution-path review;
- smallest canonical repair only if reproduced;
- regression coverage and final integration validation.

Out of scope:

- partner-server live activity;
- Presence Intent work;
- broad lifecycle redesign;
- unrelated Welcome/Exit Studio changes.

## Validation required

- exact-head renderer Unicode tests;
- Welcome fallback regressions;
- Exit runtime behavior tests;
- lifecycle text tests;
- Python compile;
- full test suite;
- standalone repository checks/audits;
- GitHub workflow gates;
- final diff/review-thread/mergeability inspection.

## Blockers / risks

A test-only PR is not a production fix. If exact-head reproduction stays green,
the task must not be misrepresented as fixing a renderer bug that was never
reproduced.

## Backlog

- quiet-server partnered-guild live activity panel;
- verify startup explicitly requests enabled Presence, Server Members, and
  Message Content gateway intents as part of that later partner-activity task.

## Next step

Run the exact synchronized renderer/fallback reproduction. If it passes, inspect
the remaining per-guild/deployment evidence path rather than modifying the
canonical font engine without a failing case.
