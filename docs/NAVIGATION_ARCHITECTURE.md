# Dank Shield Navigation Architecture

## Purpose

Dank Shield uses one public navigation model across the product.

The canonical metadata owner is:

`stoney_verify/navigation_registry.py`

The canonical `/dank home` UI owner is:

`stoney_verify/commands_ext/public_command_surface_v2.py`

Feature modules continue to own their authorization, persistence, Discord mutations,
and business logic. Navigation must never become a second implementation of a feature.

## Rules

1. Every feature destination in the public navigation registry has exactly one category.
2. Every registered feature key must have a route in the canonical dispatcher.
3. Manager-only destinations remain visible. The canonical feature owner explains why
   the current actor cannot use it.
4. A feature may have shortcuts from other places, but shortcuts call the same owner.
5. Do not duplicate save logic, permission checks, role/channel mutation, or persistence
   inside the navigation layer.
6. New public destinations must be added to the registry and the reachability tests.
7. Home is category-first. Do not add another one-off feature button directly to Home.
8. Find a Feature should receive useful aliases when normal users may search by a
   different name than the product label.
9. All Features is the complete directory safety net. Simplifying Home must never make
   a capability disappear.
10. Private component navigation should replace the current message when the feature
    owner supports that mode. Public/persistent panels keep their feature-owned private
    response behavior.

## Home categories

Home is optimized for Discord mobile without renaming product areas.

The registry's canonical category label is the label users see in the Home section
picker and on the category page. Do not introduce shortened Home-only aliases such as
`Access` for `Onboarding & Access`.

Expected Home composition:

```text
🛡️ DANK SHIELD
CONTROL CENTER
━━━━━━━━━━━━━━━━━━━━
🧭 11 sections  •  ✨ 26 destinations

[ 🧭 Choose a section… ▾ ]
  ⚙️ Setup & Server Settings
  🚪 Onboarding & Access
  🛡️ Safety & Moderation
  👥 Members, Roles & Profiles
  🌿 Community & Engagement
  🎫 Tickets & Support
  🎨 Design & Branding
  🔊 Voice & Accessibility
  📊 Logs, Stats & Diagnostics
  👤 My Dank Shield
  🧰 Utilities & Help

[🔎 Find Feature] [📚 Directory] [❓ Help] [✖ Close]
```

The Home embed must not repeat the full category directory. Compactness comes from
using one full-name section selector plus one utility row, not from shortening category
names.

| Key | Category | Purpose |
| --- | --- | --- |
| `setup` | ⚙️ Setup & Server Settings | Initial setup, mappings, permissions, configuration |
| `access` | 🚪 Onboarding & Access | Verification, Member Setup, welcome/access workflows |
| `safety` | 🛡️ Safety & Moderation | Protection, moderation, cleanup, spam/invite safety |
| `people` | 👥 Members, Roles & Profiles | Server roles and staff profile configuration |
| `community` | 🌿 Community & Engagement | Community roles, pings, Hub, sharing, community tools |
| `tickets` | 🎫 Tickets & Support | Ticket queues, routing, categories, forms, panels |
| `design` | 🎨 Design & Branding | Server design, naming, cards, artwork, fonts |
| `voice` | 🔊 Voice & Accessibility | Live Captions and voice accessibility |
| `ops` | 📊 Logs, Stats & Diagnostics | Logs, counters, status, diagnostics |
| `my` | 👤 My Dank Shield | Member-owned profile/setup/community choices |
| `utilities` | 🧰 Utilities & Help | Help and cross-feature guidance |

## Category page contract

First-level category pages use:

- breadcrumb: `Home › Category`;
- short plain-language purpose;
- visible list of registered features;
- manager-lock guidance without hiding the feature;
- feature buttons;
- **Back**;
- **Home**;
- **Refresh**;
- **Close**.

Deep existing feature pages are migrated to the same contract incrementally when their
canonical owner is touched. Do not mass-rewrite unrelated feature modules in one PR.

## Find a Feature

Search is metadata-only. It searches:

- feature label;
- category label;
- feature key;
- explicit aliases.

Selecting a result calls the canonical route. Search must not contain feature-specific
permission or mutation logic.

Examples:

- `invite shield` → Protection
- `ping roles` → Community & Pings Manager
- `share router` → Share Router
- `member setup manager` → Member Setup Manager
- `captions` → Live Captions
- `channel fonts` → Server Design

## Adding a new feature destination

A PR adding a new public navigation destination must:

1. add one `NavigationFeature` entry;
2. choose exactly one existing category or deliberately add a reviewed new category;
3. add useful aliases;
4. add the feature key to the canonical route dispatcher;
5. route to the existing feature owner;
6. keep permission checks in the feature owner;
7. add/update focused reachability/search tests;
8. keep every view within Discord component/row limits.

The import-time route-key guard and CI reachability tests intentionally fail when the
registry and router drift apart.

## Issue #367 delivery boundary

This navigation foundation is slice 1 of issue #367.

It does **not** implement:

- Generic Community & Pings storage/model;
- Configurable Verification Framework;
- Action & Reminder Center;
- Activity & Reverification Lifecycle.

Those remain separate implementation slices so they can reuse this navigation contract
without turning one PR into a product-wide rewrite.
