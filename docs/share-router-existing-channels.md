# Share Router: existing channels and proxy sources

Share Router now has two deliberately separate, per-server source modes.
Both use the **existing Search-Safe channel picker**, including styled names,
old aliases, channel mentions, and IDs. All routing rules remain in the
existing per-guild Share Router route file; this adds no database or bot
slash command.

## Use Existing Channel (Keep Original)

Staff with Manage Server, Manage Channels, or Administrator can open
**/dank → Community Tools → Share Router → Use Existing Channel**, then
choose an **ordinary existing text channel** and a destination. Choose
**Search Destination** or **Browse Channels**. A successful selection
saves a `delete_source: false` route.

- Every qualifying, human-authored post in the source can be forwarded to
  the destination. This is **not** a selective per-message share button.
  The original message and all its attachments remain in the source.
  No channel is created, renamed, hidden, or deleted.
- The human sender must have their ordinary Discord View Channel and
  Send Messages permission in the destination. Dank Shield needs
  View Channel and Read Message History in the source, and View Channel
  and Send Messages in the destination. **Manage Messages is not required**
  for this keep-original option.
- An age-restricted source can only forward to another age-restricted
  destination. Both selection and every incoming message recheck this rule.
  A normal public channel does **not** need proxy-only hidden and
  non-age-restricted restrictions.
- The destination must not be a Share Router managed proxy source, and the
  source/destination must be different channels in the same guild.
  Saving is revalidated under the existing per-guild lock.
- Existing deduplication means the same content forwarded repeatedly in a
  short interval may appear only once at the destination, but its **original
  posts are never deleted**. A failed target send leaves its source intact.
- The usual member-facing route failure notice and staff modlog are still
  used. Route health distinguishes public existing channels from temporary
  proxy sources. Remove Route disables routing, not the channels.

## Create Proxy + Add / Change Route (temporary drop-off)

Staff can continue using **Create / Repair Hub**, **Create Proxy**, then
**Add / Change Route**. The managed `share-...` channel is a hidden,
non-age-restricted mobile share-sheet drop-off. Successfully forwarded
source posts **are deleted from that proxy** as before, including the
existing bounded cleanup mechanism when a delivered cleanup initially fails.
Its safety/permission rules are unchanged.

**Do not choose a managed proxy as an existing source.** The UI and runtime
reject that combination rather than silently convert it to non-destructive
mode. The existing Add / Change Route selector still operates on registered
sources, and automatically selects non-destructive mode for historical
ordinary sources outside the managed Share Router hub.

## Limitations and validation

This option makes normal text-channel forwarding possible; it does not turn
Share Router into a per-message consent mechanism, cross-guild bridge, or
unlimited video download service. Existing provider limits and Discord
upload constraints still apply. The implementation adds focused tests for
mode selection, guild isolation, target permissions, age boundaries,
successful/failed sends, deduplication, and original-message preservation.
