# Managed Embed Studio image/GIF uploads

This feature extends **/dank → Design & Branding → Embed Studio → Media Library**.

## Exactly what moderators can do
- The existing **Upload Image/GIF** button opens Discord's native file-upload
  modal on mobile. No desktop, separate website, third-party login or new
  global slash command is required.
- Accept PNG/JPEG/WebP/GIF only. The backend verifies actual image bytes
  with Pillow, dimensions, GIF frame counts, and 6 MiB maximum upload size.
- The image is uploaded to a bot-owned Supabase Storage bucket with a
  cryptographically random, guild-prefixed object key. Postgres only receives
  its metadata, not raw bytes or base64.
- A **permanent public** HTTPS URL is saved into the same reusable Media
  Library. It works for large images, thumbnails, author icons and footer
  icons in multiple channels. No Discord message is posted by uploading.
- If the library save fails, the bot attempts to remove its *new, unique*
  upload rather than deleting another moderator's asset. An unlikely crash
  can leave an orphaned object; review orphan metrics before enabling uploads
  broadly. Removing a saved Media Library link intentionally does **not**
  delete a stored object, because it may still appear in published posts.

## Why public assets?
Discord embeds must be able to fetch the image long after publishing.
A private Supabase Storage signed URL expires and would break past embeds.
Only files that a staff member intentionally uploads here are public.
The service-role credential is **never** sent to Discord members.

## Storage cost and rollout
- This code is OFF by default under `DANK_EMBED_STUDIO_UPLOADS_ENABLED`.
- It requires the separate bucket migration
  `supabase_fresh/migrations/202610200029_dankshield_embed_studio_assets.sql`
  and new `viamriafkbqhquvrsnua` project pinned by the Storage v2 adapter.
- Do not silently assume another Cloudflare R2 or Discloud disk has capacity.
  Supabase Free storage is limited and all guilds share that project's quota.
  Run a live bucket-usage/headroom check and test uploads, deletion recovery
  and URLs in Discord Android before enabling any public canary.
- Keep this feature disabled if image-storage budgets, access policies or
  deployment secrets are uncertain. The regular link-based Media Library
  continues to work unchanged.

## Acceptance
Check file type spoofing, malformed images, giant dimensions, GIF frames,
two competing moderator saves, 50-item capacity, CAS conflicts, failed network
or metadata writes, no username/credential leaks, public URL stability, RLS,
idempotent bucket migration, existing previews and tracked role-post edits.
