# Dank Shield universal Discord UI overhaul (mobile and desktop)

## Objective

Every Discord feature center, settings page, picker, list, inspection card and
confirmation must be readable and easy to navigate on **both desktop and mobile**.
Discord renders the same interaction components on both clients, so one
focused shared layout is the product requirement, not a narrow-phone workaround. Do not replace a
laundry-list embed with a laundry-list of buttons or with a menu that no longer
explains how to use the feature. Preserve every supported control, actual
permission check, existing configuration, persistence owner, and confirmation
boundary.

This is an active **repository-wide implementation goal**, not a claim that
the latest PR finishes every view.

## Design contract

- **One job per screen.** Overview shows the status and a clear next action.
  Additional settings are opened on focused tabs, selects, or separate cards.
- **Visible budget:** target <=9 components per interactive screen, <=4 fields
  per normal overview, <=600 characters for overview descriptions. Diagnostic,
  audit, and transcript details may exceed overview limits only with explicit
  page/scope selection and bounded output.
- **Keep functionality:** move, do not delete, action handlers. Every original
  action should map to one discoverable section, with the same permissions,
  staff/owner hierarchy, fail-closed state, and confirmation behavior.
- **No silent partial state:** if a data source cannot be checked, show a
  relevant status and keep sensitive actions disabled. Do not invent values.
- **One navigation language:** Choose area; inspect; edit; preview/confirm;
  back. Avoid repeated descriptive fields that merely name visible buttons.
- **Desktop, mobile and privacy:** use the same focused layouts everywhere;
  don't expand into a wall of buttons just because desktop has extra space.
  Keep cards ephemeral/owner-locked where previously
  private. Avoid spawning duplicate response messages on every navigation
  click or exposing private staff metadata.
- **Accessible pickers:** use Search-Safe resources and show blocked options
  with reasons when the workflow permits, not name-based secret exclusions.
- **Compatibility:** old slash commands and live feature routes continue to
  reach the same implementation; no new schema or global feature toggle.

## Verified inventory baseline

The October 31 CI mobile UX inventory scanned **614 Python modules** and
identified **736 view/embed surfaces**, with **179 static density signals**
for review. These are candidates, not 179 independently confirmed unreadable
live screens. Conditional embed branches and views that already page controls
can trigger false positives. The complete JSON inventory is uploaded as a
`mobile-panel-inventory` GitHub Actions artifact. Re-run:

```sh
python tools/audit_discord_mobile_panels.py --strict-parse --json-out mobile-panels.json
```

## Current cohesive change / PR #773

- Protection Center: six small panels (overview, links, filters, spam,
  AntiNuke, tools), every original handler preserved and degraded state locked.
- Member Command Center, Verification, Current Ticket, Design Studio and
  Server Stats: remove repeated directory text from entry screens.
- Member Setup Manager: 17 controls divided into role/channel, categories,
  publication and Strict Gate pages; its admin embed condensed.
- Community Hub home: 16 controls divided into play, discovery, preferences,
  and staff pages.
- Community Hub settings: 16 controls divided into general, rooms, limits,
  notifications pages. Each shows only the selected section's status.
- Community & Pings: 15 controls divided into options, groups, toke, movie
  integration. Individual choices remain inspectable in the existing editor.
- Automated checks: menu/section action coverage and max controls, partial
  outage safety, field limits, whole-repo density inventory, Design regression.

- Welcome & Exit: focused static message, live join, live exit, and other tools.
- Join Card Studio and Exit Card Studio: delivery, design, and artwork pages;
  refresh and navigation preserve working handlers and confirmation boundaries.
- Advanced Settings: core features, appearance, and operations instead of nine
  unrelated instruction fields.
- Trusted Advertising Bots: approval and retention/preview pages, with exact
  bot/channel identity and 24h+ retention safeguards still enforced.
- Action & Reminder Center: member tasks vs staff settings, no hidden scheduler.
- The legacy /dank home fallback: personal tools vs server tools, with the
  same owner check and feature dispatch; canonical v2 navigation remains intact.

## Not finished / remaining integration

Evaluate the rest of the inventory before claiming whole-bot coverage, with
priority on heavy *rendered* screens: advanced setup, ticket administration,
welcome/exit card studios, session actions, staff dashboard, server logs,
member action pages, reminders, detailed diagnostics, and nested editors.

The Share Router channel-picker fix remains separately tracked by PR #772.
Do not overwrite that branch. Reconcile any overlapping UI changes when
integrating.

**Release gate:** all exact-head checks green, conflict/review resolution,
merged main verified, protected Discloud release with backup validation, then
Android **and desktop Discord** smoke tests for different roles and for unavailable dependencies.
The automatic component-count tests cover shared layout contracts, not screenshot-level visual acceptance.
Successful code CI is not live UI acceptance.
