# Embed Studio: Server Brand Themes

Use **/dank → Design & Branding → Embed Studio → Brand Themes**. This is the existing
Embed Studio, not a separate editor or command.

- Pick a built-in color preset to recolor all embeds in the **current draft**.
  It does not clear existing author and footer text.
- **Save Current Style** saves the selected embed's color, author fields and footer
  as a shared, server-local theme. Up to eight named themes per guild.
- Choose a saved theme to apply its stored color, author and footer to every
  embed in your current draft. It does not replace titles, descriptions, fields,
  images, buttons, destinations, or roles.
- **Match All Embeds** copies the active embed's branding to every embed.
- **Remove Saved Theme** requires confirmation and does not alter drafts,
  already published messages or other saved templates.
- **Undo** reverses theme application while the editor is open. The normal
  validated autosave preserves drafts, and **Preview & Publish** still requires
  explicit moderator confirmation.
- Staff can use shared theme styles but cannot edit another staff member's
  private draft. Persist through the existing guild-scoped `embed_studio_v1`
  compare-and-swap config, never a new table or periodic worker.

URL fields must remain valid HTTPS; theme names are 1–60 printable characters.
If storage is offline, no theme is saved or deleted. Imported JSON files
still carry only the project, not the server's theme collection.

## Rollout / acceptance

Test both a new server without a themes key and an existing guild with
saved drafts, templates and tracked role posts. Check Android control layout,
concurrent moderator saves, backend outages, Undo, and preview before any
live public post. Do not advertise this feature as released until it is merged
and the deployed SHA and Discord UI are verified.
