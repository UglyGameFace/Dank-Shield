# Ticket channel topic privacy

Discord channel topics are readable by anyone with access to that ticket.
Internal `owner_id`, `category`, `ghost` and `ticket_number` values
must stay in the canonical durable `tickets` repository, not in the topic.

## First bounded stabilization slice

- Canonical service, category panel, and verification ticket creations share
  one plain-language, member-safe topic.
- Legacy key=value topics remain readable. Do not silently lose the ability
  to recover existing tickets when old metadata is still present.
- Reconciliation prefers existing stored ticket owner, category, ghost flag
  and sequence number. Staff members with a channel overwrite are not inferred
  as the ticket owner when an authoritative DB owner already exists.
- Runtime event routing, candidate discovery and compatibility helpers
  recognize new public-only topics.
- This PR does not touch Discloud deployments, the database schema, tokens,
  channel permissions, or bot registration.

## Remaining historical cleanup (NOT included or authorized in this slice)

Existing member-visible Discord ticket topics may still disclose old
metadata. **Do not run a blind startup or background cleanup**. A future
bounded owner-triggered repair must first prove the stored ticket row matches
the same guild and Discord channel, preserve the historical fields in the
database, verify the bot has Manage Channels permission, protect staff and
member access, rate-limit edits, provide a dry-run and auditable report,
and skip unknown or conflicting tickets without mutating them.

CI and Discord integration tests must pass before this draft PR becomes
mergeable for review. A merge is not proof of production deployment or
safe historical-channel migration.
