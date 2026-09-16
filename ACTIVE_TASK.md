# ACTIVE TASK

## DS-AUD-CONTEXTUAL-PERMISSION-REPAIR — Make configuration menus self-repairing

**Outcome target:** Any configuration surface that can detect a repairable Dank Shield access/setup failure must expose a same-screen repair action, apply every safe repair for that menu, re-audit after the change, and explain any blocker Discord will not let the bot repair automatically. The first acceptance surface is `/dank welcome join-leave`, which currently reports missing permissions without offering a repair path.

**Status:** IMPLEMENTATION

**Branch:** `audit/contextual-permission-repair-contract`
**Base main:** `59911d39d9c8181f12f29a59203eae8fb710467d`
**Previous integrated task:** PR #243 merged at `59911d39d9c8181f12f29a59203eae8fb710467d`

## Scope

- shared contextual permission-repair contract built on the existing canonical `permission_repair_core`
- same-screen `Fix Issues` / `Access Healthy` control contract
- safe repair of Dank Shield's own missing channel overwrite bits only
- exact post-repair re-audit before reporting success
- explicit manual blockers when Discord prevents automated repair
- `/dank welcome join-leave` integration for both selected Join and Leave channels
- regression coverage that prevents diagnosis-only configuration menus from silently regressing
- task/PR bookkeeping

Out of scope for the first implementation pass unless tracing proves a direct dependency:
- widening private/staff channels to members automatically
- changing role hierarchy automatically
- granting Administrator
- changing selected channels just to make a health check green
- unrelated product behavior in Protection, Tickets, Design, AntiNuke, or member moderation

## Root cause

1. `welcome_event_services._can_post()` detects missing View Channel, Send Messages, Embed Links, and Read Message History permissions and displays them in the Join/Leave menu.
2. The Join/Leave center has channel selectors, toggles, edit/preview/template/help controls, refresh, and close, but no repair button.
3. Dank Shield already owns canonical target-level repair machinery in `permission_repair_core`: target auditing, minimum feature permission profiles, safe overwrite repair, explicit-deny preservation, retry handling, post-repair audit, and undo/event recording.
4. The gap is therefore integration and contract ownership, not a lack of repair capability.
5. New-member visibility is a separate audience/configuration concern. A repair action must never expose a private staff channel merely to make a warning disappear.

## Implementation contract

- Menus that expose repairable access findings must include a contextual repair control on the same view.
- Healthy state renders as `✅ Access Healthy` and is disabled.
- Unhealthy state renders as `🛠️ Fix Issues` and repairs all safe targets owned by that menu in one action.
- Repairs must use canonical `permission_repair_core`, not duplicate ad-hoc `set_permissions` logic.
- Explicit denies remain preserved unless a separate explicit confirmation flow exists.
- After repair, the menu must reload config/state and re-audit before showing success.
- Remaining unsafe/manual blockers must be named precisely.
- Audience visibility issues may be reported but must not be repaired by making private staff channels public automatically.

## Validation gate

- exact final branch 0 behind `main`
- focused contextual-repair and Welcome menu tests pass
- full required GitHub Actions pass on the exact final head
- no review/thread issue ignored
- no merge until exact-head validation and scope review are clean

## Backlog after this task

- adopt the shared contextual repair control across remaining diagnosis-capable configuration menus in priority order: Setup/Verification, Tickets, Modlog/Member Logs, Profile/Self Roles, Protection, VC verification, Embed/Status surfaces
- `/dank protection` remaining non-invite picker/guard cleanup
- `/dank design` picker migration
- admin-only legacy setup picker cleanup

## Next step

Implement the shared contextual repair helper on top of `permission_repair_core`, wire it into `/dank welcome join-leave`, add regression tests for one-press multi-target repair/re-audit/manual blockers, then open a draft PR and validate the exact head.