# Post Builder: moderator quick guide

The feature previously labeled **Embed Studio** in the feature menu now appears as
**Post Builder** under **Design & Branding**. The underlying feature key is
still `embed_studio`, and searching "embed studio", "embed builder", or
"create announcement" still finds it. No second editor or new slash command
was created.

## A normal post in four steps

1. **Pick a Layout** if you'd like a starting point. For a blank announcement,
   skip this.
2. **Write Message**. Add a title, main text, and optional text above the
   decorated card.
3. **Choose a Channel** at the bottom. Nothing is sent yet.
4. **Preview & Post** to see the real post privately. Press **Post Now** to
   confirm. For an existing message, the confirmation says **Save Changes**
   and edits that same bot-owned message.

## Where did everything else go?

- **Look & Images**: Colors & Style, Add Pictures, Saved Pictures, Reuse Server
  Style. Use this to make things pretty; no upload or change goes public.
- **Add Buttons**: Website Buttons, Let Members Pick Roles, Collect Answers.
  Completely optional for ordinary announcements. Staff only sees the
  relevant tool after choosing a task.
- **Saved Designs**: Saved Post Layouts, Restore My Draft, Share With Staff,
  Import / Export. Drafts still autosave through the existing guarded store.
- **More Tools**: Extra Text Sections, Multiple Post Cards, Automatic Server
  Details, Upcoming Scheduled Posts. Advanced tools are accessible without
  crowding the basic workflow.
- **Edit Existing Post**: Load a tracked bot-owned message, review changes,
  then **Save Changes**. It never silently creates a new post.
- **Undo**: Revert the most recent edit to this private session.

## New-moderator example

To make an event announcement, open Post Builder, choose **Pick a Layout →
Event**, tap **Write Message**, add your event title and details, select the
announcement channel, then **Preview & Post → Post Now**. The Discord preview
is private until final confirmation.

## Safety and release

This is a **navigation and wording** change, not a new publishing backend.
No permissions, data storage, migrations, bot workers or Discord slash
commands change. Existing templates, uploads, self-role panels, forms,
schedules, staff reviews, JSON support and Live Text remain reachable.
The 6000-character, component-row, role safety and preview-before-publish
guards are retained. The **New/Updated** menu metadata changes in this same PR; it must only be visible to moderators after this PR is merged and deployed.

Recommended canary: give a younger moderator who has not used Studio before
one task ("make an announcement about a movie night"), and observe whether
they reach a private preview unassisted from their Android device. Verify
the grouped paths still permit all earlier feature actions.
