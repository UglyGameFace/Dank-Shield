# Embed Studio: persistent optional role buttons and dropdowns

This continues PR #580 without a parallel editor, database table, or extra slash command.

## Mobile editor

Open /dank home → Community & Engagement → Community Tools → Embed Studio → Role Controls.

- Pick an existing server role. It must pass Dank Shield's existing
  cosmetic/Community & Pings self-role safety checks. Staff, verification,
  access, sensitive-permission, managed, or hierarchy-blocked roles are denied.
- Select a role to rename its display label, reorder it, or remove it.
- Switch between Buttons (five role + URL buttons combined) and Dropdown
  (ten selectable roles, plus separately valid HTTPS link buttons).
- Preview privately: role controls in the preview are disabled. Publishing
  requires the same exact-snapshot confirmation as the existing studio.

## Member experience and persistence

Members click one button or choose one dropdown option to toggle only their
own optional role. There is no role creation, staff grant, or automatic
verification.

One versioned component listener is installed at bot startup, not one View
per server/message. Every click reads the guild's authoritative saved
registry, verifies the opaque token and exact channel/message ID, confirms
this bot authored the message, revalidates the role and current server
access/staff/sensitive-permission guards, and fetches fresh member roles.
Per-member changes use the existing Community & Pings lock so operations
do not race each other.

Copied, stale, missing, or cross-guild controls do not authorize changes.
If storage is unhealthy, role actions fail closed. The publisher checks
storage before sending and attempts to remove unusable role controls if
the atomic tracking write fails after Discord accepts the message.
Edits rotate component tokens, so old controls are invalidated.

## Scope and acceptance

This is optional self-role assignment only. Not implemented by this slice:
verification bypass, role prerequisites/exclusion/caps, privileged role
grants, form components, moderation actions, or scheduled publishing.

CI + real Discord integration acceptance are required before calling it
production ready. Confirm server-manager role picker rejects staff/access
roles; preview buttons do not work; published actions survive bot restarts;
cross-guild and copied buttons fail; revoked roles stop working; repeated
clicks do not create race conflicts; editing a post preserves its existing
message ID and invalidates old role controls.

No live Discord messages, members, or database rows were mutated while
preparing this PR. The existing guarded Discloud rollout still applies.
