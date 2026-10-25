# Embed Studio Team Review

**Team Review** lives in the existing Discord/mobile Embed Studio and uses
the same guild/staff checks as the editor. It is an opt-in moderation handoff,
not automatic publishing or Google Docs-style simultaneous editing.

## Workflow
1. A moderator builds a draft, chooses a posting channel and opens
   **Team Review → Submit Current Draft**. The draft snapshot is saved as a
   private review record in the pinned DankShield Supabase project.
2. Another moderator opens **Team Review → Pending Reviews**, picks the design
   and chooses **Approve & Edit Copy** or **Reject Request**.
   The submitter cannot approve their own request, enforced by a SQL RPC.
3. Approve loads a validated **private copy** into the reviewer's editor.
   Their Undo history retains their previous draft. Approval does not send
   a Discord message, assign roles or modify another moderator's saved draft.
4. Approved designs are available under **Approved Designs** for authorized
   staff to load again. Any post still requires fresh channel/role checks and
   the normal **Preview & Publish** confirmation.

Staff can review up to 20 recent entries per queue screen. This is a canary
limit until indexed cursor paging, record expiry and retention audit are added.

## Deployment
Requires additive migration
`supabase_fresh/migrations/202610200031_dankshield_embed_studio_reviews.sql`.
Both `DANK_EMBED_STUDIO_V2_ENABLED=1` and
`DANK_EMBED_STUDIO_REVIEWS_ENABLED=1` must be deliberately enabled on a
verified new-project canary. Defaults OFF. Public/anon/authenticated SQL
permissions are revoked and RLS is enabled. No DB schema or flags are
automatically applied merely by merging the PR.

## Security / limitations
- Guild-scoped metadata reads, exact UUID identity, atomic pending-state
  review decisions, and SQL rejection of self-approval.
- The bot rechecks moderator permissions for each interaction.
- No DMs or public publication on a review submission/decision.
- No live concurrent editing yet; multiple editors get independent private
  copies, and published messages retain existing moderator confirmation.
- Staff should avoid placing secrets in public announcements; reviewers
  with Manage Server permission can read drafts submitted to their guild.
