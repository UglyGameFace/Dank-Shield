# Dank Shield Public Launch Checklist

_Last updated: 2026-04-26_

> This checklist is for beta/public launch preparation. It uses competitor-style product patterns as benchmarks, not copied text, branding, code, UI, pricing, or private implementation details.

## Public launch rule

Do **not** publicly invite Dank Shield to outside servers until every **Blocker** item is complete.

Use this command in every test server before launch:

```txt
/dank health
```

A server is ready only when `/dank health` shows no blockers.

---

## 1. Competitor benchmark targets

These are the product standards Dank Shield should be measured against.

| Competitor-style reference | What users expect | Dank Shield launch target |
|---|---|---|
| Ticket Tool-style ticket bots | Fast ticket panels, category routing, staff actions, transcripts, close/reopen/archive flow | Ticket creation must be fast, reliable, per-server configurable, and easy for non-technical admins |
| MEE6/ProBot-style public bots | Simple onboarding, minimal command clutter, clear permissions, public docs | Public profile must stay under Discord command limits and setup must be guided |
| Dyno/Carl-bot-style moderation bots | Modlog reliability, permission checks, audit context, role hierarchy warnings | Moderation commands must refuse unsafe actions and explain missing permissions |
| Premium bot dashboards | Clear pricing tiers, server-level settings, billing transparency | Paid plans must be documented before charging anyone |
| Large verified bots | Privacy Policy, Terms, support server, status communication, abuse handling | Legal docs and support process must exist before public rollout |

Do not copy competitor branding, embeds, copywriting, designs, docs, code, or pricing tables directly. Use the category of feature as the benchmark and build original Dank Shield behavior.

---
