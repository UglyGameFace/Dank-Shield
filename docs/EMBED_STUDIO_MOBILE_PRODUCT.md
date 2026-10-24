# Dank Shield Embed Studio: unified mobile-first editor

The canonical editor is `stoney_verify.embed_studio_ui.open_embed_studio`.
It replaces the parallel one-shot Community Tools and Setup embed modals.
Existing `/dank embed draft` is a backward-compatible advanced shortcut, not
the main editor, and remains isolated from saved Studio projects.

## Find the Studio on Android

From `/dank home`, open **Community & Engagement → Community Tools →
Embed Studio**. The old `/dank setup` Feature Centers → Embed Builder entry
also opens this same editor. No new slash command needs to be remembered,
registered, or synced. A staff member needs **Manage Server** or Administrator.

## First use: six simple steps

1. Choose **Starter Layouts** (Announcement / Rules / Event / Info / FAQ /
   Feature Showcase), or keep the default editable draft.
2. Tap **Write** to set headline, full description and optional plain text.
   **Appearance** edits color, author, footer and title link;
   **Images** edits the large image, thumbnail and small icons.
3. **Fields** adds up to 25 labeled blocks per embed. Select to edit,
   move up/down or remove. **Link Buttons** adds up to five URL buttons with
   valid HTTPS destinations. They work even if the bot restarts.
4. **Embeds** navigates/adds/duplicates/removes separate embeds in one
   message, up to the Discord limit of ten and 6000 combined characters.
   It also offers a timestamp toggle. **Undo** reverses up to ten edits.
5. Choose a destination with Discord's normal channel picker, then
   **Preview & Publish**. The full multi-embed preview is private. The
   **Publish / Save Edit** button is the explicit send action. A separate
   button returns to the editor without publishing.
6. **Templates → Save Current Draft** saves a reusable server template in
   guild config with an atomic compare-and-swap, so one staff session
   cannot silently overwrite another. Templates can be loaded and deleted
   with a second confirmation. **Edit Posted** loads one of the last
   twenty tracked, bot-owned messages for private editing and explicit
   re-publishing to the same message.

## Storage, safety and limits

- Staff edits now autosave after successful validated editor mutations into the
  existing per-guild Studio config; reopening the Studio after a Discord timeout
  or bot restart restores that staff member's most recent saved draft. **Reload
  Saved** explicitly abandons the current unsaved local editor state to load
  the persisted version. Saving never sends a public message.
- Autosave is limited to eight staff drafts per server and the existing 100KB
  shared Studio library limit. Inactive other-member draft slots older than
  thirty days may be reclaimed. Each staff draft has an optimistic revision,
  so two open sessions cannot silently overwrite each other's work. If the
  database is offline, full, or a saved draft changed elsewhere, the editor
  displays a warning; the unsaved local draft remains usable but is **not**
  durable until autosave succeeds.
- Saved templates: maximum 12 per server, server-specific config blob
  limit 100KB, each project limited to 40KB, twenty tracked post
  references. A missing/unhealthy DB blocks saving rather than pretending
  a template was stored. No new tables or migration required.
- A cross-process CAS protects concurrent library writes. Public sends
  recheck staff permission, guild, destination bot permissions, and the
  existing Dank Shield safe-guild-action guard. Mentions are suppressed.
- Published message edits can target only a stored channel/message ID,
  only inside the same guild, only after fetching the actual Discord
  message and verifying its author is this bot. No arbitrary message IDs,
  deletes, channel sweeps, or moderation actions.
- Media/buttons require HTTPS URLs. We don't download arbitrary files
  or store remote media. Custom images supplied by valid URLs will appear
  at the size/placement Discord supports.

## What remains for the complete product

This PR delivers the stable rich-composition and editorial foundation, not
every promised advanced subsystem. Do **not** advertise the following as
implemented yet: role assignment/action buttons, persistent interactive
dropdowns, scheduled/campaign publishing, dynamic live variables and updates,
upload-file asset management, shared theme packs, raw JSON import/export, or
multi-admin draft collaboration. They need durable component routing,
guild-scoped permissions, persistent jobs and independent abuse/scale tests.
They should build on this exact Studio model rather than adding more
one-off embed builders.

## Deployment and acceptance

Source-only PR. No production Discloud or database mutations occur here.
After exact-head CI and merge, use the guarded existing-app release process
only when its backup/recovery gates have been satisfied. On Android test:

- Start from Community Tools and the existing Setup Feature Center; verify
  both reach exactly the same Studio.
- Pick a starter, add fields, images and links, undo, duplicate an embed.
- Confirm the private multi-embed preview has the expected image size.
- Save, reload and delete one test template; restart before reload if
  recovery is under test.
- Publish a test message then edit **that same message**, checking that
  no second message appears, buttons remain links, and permissions are
  re-evaluated if the target channel permissions change.
- Test cross-guild isolation, invalid URLs, no-permission users,
  bot missing Embed Links, CAS conflict, and a full storage quota.

## Draft recovery acceptance (new)

- Open the Studio, edit text/fields/role controls, and verify an autosaved draft
  persists after reopening the Studio without selecting Save Template.
- Confirm another staff member cannot load that private draft. Two active
  editor sessions for the same user must reject the stale writer; Reload Saved
  must explicitly recover the newer saved version.
- Simulate a storage outage, full shared 100KB quota, template update CAS race,
  session expiry and restart. No unavailable save may be reported as durable.
- When editing a tracked post, restore the edit target only while the tracked
  message record still exists; publishing still rechecks bot authorship,
  channel permissions and safe guild action guards before changing anything.
- Existing role actions, templates, and tracked posts must survive library
  migration unchanged. Real Android Discord and Discloud canaries remain
  required before describing the feature as production-verified.
