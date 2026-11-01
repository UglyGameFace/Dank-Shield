# Server Appearance: task-first Discord navigation

## Intent and canonical owners

This change makes the first Server Design screen answer four ordinary server-owner questions:

1. **Style All Names:** choose a server theme, font, strength, separator, category frame and icon behavior, then preview and apply a reviewed batch.
2. **Edit One Item:** explicitly choose a category or channel, then rename, restyle, set a saved exception, exclude it from styling, or reset its local overrides.
3. **Check & Fix:** scan saved design drift, prepare category-aware Smart Repair, review Ready / Needs Review / Protected / Blocked results, and confirm safe mutations.
4. **Advanced:** saved style exceptions and exclusions, Searchable Names, and History & Reset.

The canonical UI and reviewed Apply/Undo owner remains `stoney_verify/commands_ext/public_design_studio_v2.py`. The legacy `public_design_studio.py` is a compatibility backend, not a competing public entry. `server_design_plan_service.py` owns all batch plans, `server_design_apply_service.py` owns reviewed Discord edits and transaction preflight, `server_design_rule_service.py` owns saved-rule updates, and `search_safe_naming.py` owns the cross-feature naming policy. Keep these authorities singular.

## Removed duplicate UI paths

- Whole-server styling no longer displays Clean Redesign next to ordinary Preview. It lives under Advanced -> History & Reset, where it retains confirmation and only clears visual exceptions.
- Whole-server styling no longer offers a second arbitrary Preview Separator action next to full Preview. Preview Separator Only remains one level deeper with frame/icon details for owners intentionally making a targeted separator-only change.
- The home does not separately expose Saved Rules, Search-Safe, and Undo. They are reachable under Advanced, each dispatching to its existing authoritative service.
- Card Assets is a welcome/exit-card feature and now appears under Onboarding & Access as Welcome Card Assets. The existing upload and card-studio handlers are reused; nothing new is stored.
- The old public names remain discoverable by alias and `/dank design` continues to dispatch to the same Studio.

## Deliberately retained capabilities

Do not collapse "saved design scan" into Smart Repair without preserving the difference: the former evaluates saved rules, whereas the latter can infer local style with confidence gating. Do not delete exact-item formatting, global/category/channel precedence, direct rename, saved manual names, protected default names, font/emoji catalogs, Search-Safe Naming, batch preview, preflight, snapshot and Undo. No styling screen may write permissions, overwrites, channel order, verification, tickets, topics or server security state.

Appearance exclusions have **no relationship** to actual role/channel security permissions. Post Builder continues to own embed and artwork publication; Server Stats owns counters; Role Editor owns role hierarchy and permissions; Channel Builder owns channel creation; Welcome/Exit owns its cards. Appearance may provide style to other modules only through their existing opt-in adapters.

## Required tests and release validation

- Four home buttons and exactly one advanced navigation for each capability, no stale page buttons on the whole-server screen.
- All select and modal inputs still invoke the same persisted settings services.
- Preview and Apply remain two distinct interactions; category/channel direct Rename retains its explicitly immediate behavior.
- Separator-only preview preserves dimension scope. The whole-server preview still honors narrow saved overrides.
- Old/current saved config and snapshots are readable. Undo refuses stale snapshots, and Clean Redesign preserves protection.
- Search-Safe policy, bot access and Discord role hierarchy remain authoritative.
- Mobile and desktop fit within Discord's five-row and 25-option UI limits, including the category frame browser.
- Confirm `/dank design`, `/dank home` -> Design & Branding -> Server Appearance and the Setup shortcut all converge on one public owner.
- Run existing design, navigation, help and command-registration tests plus production Discord acceptance after deployment. CI passing alone does not prove production deployment.

## Status

Design/navigation implementation is on `feat/dank-server-appearance-task-first-20261101`; production acceptance must be recorded separately. Branch cleanup PR work is independent and must not be merged into this appearance scope.
