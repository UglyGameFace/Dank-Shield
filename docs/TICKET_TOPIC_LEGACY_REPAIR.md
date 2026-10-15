# Member-visible legacy ticket topic cleanup

PR #577 stopped new tickets from exposing their owner ID, ticket number,
category, and ghost flag in the Discord channel topic. Old topics still exist
and require a separate, deliberate repair operation.

## Mobile-friendly entry point

After this change is merged **and deployed**, use the existing
`/dank home` navigation → **Tickets & Support** → **Public Panel** →
**Review Old Topics**. This does not introduce an unregistered slash command.
Only staff with Manage Server or Administrator can preview/approve cleanup.

The preview is read-only. It looks in the guild's saved active/archive ticket
categories and checks at most 25 matching legacy topics against real ticket
database rows. It offers to clean at most 10 verified topics per confirmation.
A second private button, **Clean Verified Topics**, must be pressed explicitly.

The action rereads the ticket row and Discord topic and rechecks: guild ID,
channel ID, requester ID, ticket sequence, category, ghost flag, allowed
ticket status and Manage Channels permission. Unknown topics and conflicting,
missing, deleted, or partially identified records are skipped, not altered.

No database rows, channel names, roles, overwrites, messages, or unrelated
topics are changed. Edits are paced at 0.5 seconds between attempts.
There is no startup sweep, background automation, global owner setting,
server-specific hardcoding, migration or deployment in this PR.

## Limitations and acceptance

This is intentionally stricter than a bulk `replace all` command:
renamed/modified topics, ambiguous old schema, missing rows, or tickets outside
the configured ticket categories must be inspected separately. Skips are
expected safety behavior, not authorization to retry destructively.

Before using a live cleanup, first verify #577 is running in production,
review a dry-run result in one controlled test guild, confirm a ticket can be
created, claimed, reopened and closed normally, and preserve a genuine
off-Discloud backup plus recovery procedure. Apply only to a small batch,
check that only the intended topics changed, then repeat if needed.

The GitHub repository's old linked Discloud branch is not proof of the running
commit. CI passing does not deploy the code or grant release authorization.
