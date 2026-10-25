# Channel picker fallback: Use This Channel

## Purpose
Discord Android's native ChannelSelect sometimes shows no committed selection.
The former Unverified Member Reminders **Use Channel** button only changed the
delivery mode, then refused to proceed because the picker had not saved a
channel. The channel the moderator opened the menu from was already known.

**Use This Channel** now chooses that exact text channel. It does **not**
depend on a ChannelSelect value or ask the user to copy channel IDs.

## Shared rule for channel-setting features
- Prefer the canonical `DankGuildResourceBrowserView` for choosing *other*
  resources. Every text-capable instance inherits **Use This Channel**,
  including its Search-Safe names/aliases, pagination, owner lock, and the
  exact original `on_pick` callback/predicate.
- Native legacy screens that cannot be migrated immediately call the same
  `ui.current_channel.current_text_channel` resolver for their button, then
  call the feature's **existing validation + save action**. No new storage
  layer or substitute picker is introduced.
- The fallback uses only an actual guild-cached `discord.TextChannel`.
  It fails closed for DM, thread, deleted/foreign channel, lacking bot view
  or send rights; features needing embeds, image attachments or private
  ticket access enforce their existing additional requirements.
- The owner lock, staff/manage-guild requirements and feature-specific
  validation occur again at click time, not when the UI was created.
- Selecting a channel is not permission to send, ping, purge or publish.
  Public posts and destructive cleanups continue to require their own
  separate preview, confirmation and/or moderator authority.
- Shared browser role-only, voice, forum and category pickers **do not**
  display a text-channel shortcut. A text-only feature's shortcut cannot
  be used to choose an unrelated role or voice channel.

## Included screens
1. Unverified Member Reminders: tap **Use This Channel** while in the
   intended channel; save both reminder channel ID and delivery mode with
   one existing guild-config CAS. **Choose Other Channel** opens Search-Safe.
2. Post Builder: select current channel, keep preview confirmation, clear
   edit mode when changing destinations.
3. 4:20 Daily Announcements: share the same role/permission and save
   pathway as the normal Search-Safe picker.
4. Static Welcome, Join Card Studio, Exit Card Studio: save through
   the existing feature-specific functions; card activation remains explicit.
5. Live Profile Signatures: **add** current channel to the set already
   configured, rather than wiping older channels.
6. Ticket Center, Public Ticket Panel, Ticket Staff Action Panel:
   select the current channel only; posting and ticket operations remain
   separate actions.
7. Private Ticket Staff Alerts: enforces the existing requirement that
   @everyone cannot see the alert channel.
8. Channel Purge, User Purge, Invite Cleanup: shortcut only targets the
   channel, leaving the existing guarded preview and delete confirmations.
9. Shared Search-Safe text browser consumers such as Community & Pings,
   Share Router, 4:20, voice-safety text channels and other existing
   resource-browser integrations inherit this automatically.

## Known remaining surfaces to migrate in later verified batches
Unmigrated native selectors in the full Setup wizard, live captions,
legacy role-center, welcome recommendation, design/renaming tools and
ticket category-specific selectors still need their own equivalent
button or migration to the canonical browser. Do not assert complete
coverage of *every* historical selector until the explicit file-level
audit is finished and test coverage proves the remaining list empty.

## Canary on Discord Android
- Open the settings in the desired channel; tap **Use This Channel**.
- Confirm UI and persisted configuration show that channel.
- Reopen the menu after a bot restart to confirm durable save.
- Try a text channel without required permissions and verify rejection.
- Try the same button from another moderator, a thread and a DM.
- Test **Choose Other Channel** with decorated names using Search-Safe.
- Verify cleanup selection never deletes anything without confirmation.
