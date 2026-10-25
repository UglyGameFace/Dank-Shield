# Embed Studio: staff-owned feedback forms

This adds a configurable text form to the existing canonical Embed Studio.
It is not a separate Discord command or dashboard.

## Mobile editor
Open **/dank → Design & Branding → Embed Studio → Form Builder**.
Use **Set Up Form** to name it and label its button, then **Add Question**
up to five times. Each question may be short or paragraph text and can be
required or optional. Remove questions or clear the form from the current
private draft. Form buttons and self-role controls cannot share a post.
A form may be accompanied by up to four ordinary HTTPS link buttons.

Use the normal **Preview & Publish** screen. The preview form button is
disabled to prevent test submissions. Only the explicit Publish action creates
a persistent form on a bot-owned Discord message.

Members click the form button and fill the native Discord modal. Answers are
stored privately in `public.dank_embed_studio_form_responses`. Replies are
NOT posted in a public channel or announced by DM. Moderators with Manage
Server can use **Form Builder → Response Inbox** to read recent submissions.

## Safeguards
- `DANK_EMBED_STUDIO_V2_ENABLED=1` and
  `DANK_EMBED_STUDIO_FORMS_ENABLED=1` are both required; default OFF.
- Each click and submission rechecks guild, published message, permanent
  random form token, bot authorship and the authoritative Studio library.
- Inputs are bounded by Discord modal limits; saved answers are limited to
  4,000 total characters and at most five questions.
- The SQL uniqueness key allows one submission per user and form post per
  five-minute slot across restarts and shards. Failing database writes fail
  closed. No member-generated mentions are sent publicly.
- Response table has service-role-only privileges and RLS enabled.
- A bounded maintenance RPC removes answers older than 30 days in batches,
  but it does **not** run automatically: the owner must configure scheduled
  private cleanup before claiming guaranteed retention.
- Removing a form from an edited post invalidates its old token. Failed
  published-post tracking attempts remove unusable buttons.
- Like live self-role panels, active form posts cannot be evicted merely to
  make space for ordinary edit-history entries.

## Deployment and acceptance
Apply additive
`supabase_fresh/migrations/202610200032_dankshield_embed_studio_forms.sql`
to the verified NEW pinned Supabase project only, once parent migrations
#716–#719 are merged and canary validated. No secret values are printed.
Verify permission loss, deleted posts, stale tokens, form submissions,
duplicate prevention, bounded retention, failed inserts, 50-link UI limits,
mobile previews and saved post edits. Do not activate on all guilds from
a green unit-test result alone.
