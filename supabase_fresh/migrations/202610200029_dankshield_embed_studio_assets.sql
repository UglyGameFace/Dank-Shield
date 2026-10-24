-- Permanent Discord-facing Embed Studio images, dedicated DankShield Supabase only.
-- Public READ of explicitly uploaded images is required for durable Discord embeds.
-- The bot holds the service-role key; no anonymous/authenticated storage write policy.
-- Additive migration, never touches previous Studio JSON or other Storage buckets.

do $bucket$
begin
    if to_regclass('storage.buckets') is null then
        raise exception 'Supabase Storage schema unavailable; do not enable artwork uploads';
    end if;
    insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
    values ('dank-studio-artwork','dank-studio-artwork', true, 6291456,
            array['image/png','image/jpeg','image/webp','image/gif'])
    on conflict (id) do nothing;
    if not exists(
        select 1 from storage.buckets
        where id='dank-studio-artwork' and name='dank-studio-artwork'
          and public=true and file_size_limit=6291456
          and allowed_mime_types @> array['image/png','image/jpeg','image/webp','image/gif']::text[]
    ) then
        raise exception 'Existing Studio bucket has incompatible configuration';
    end if;
end;
$bucket$;

create table if not exists public.dank_embed_studio_assets (
    guild_id text not null check (guild_id ~ '^[0-9]{1,20}$'),
    object_path text primary key check (char_length(object_path) between 35 and 180),
    image_sha256 text not null check (image_sha256 ~ '^[0-9a-f]{64}$'),
    name text not null check (char_length(name) between 1 and 60),
    content_type text not null check (content_type in
        ('image/png','image/jpeg','image/webp','image/gif')),
    size_bytes integer not null check (size_bytes between 1 and 6291456),
    created_at timestamptz not null default now(),
    check (object_path like guild_id || '/%')
);
create index if not exists dank_studio_assets_guild_idx
    on public.dank_embed_studio_assets(guild_id, created_at desc);
alter table public.dank_embed_studio_assets enable row level security;
revoke all on table public.dank_embed_studio_assets from public,anon,authenticated;
grant select,insert,delete on public.dank_embed_studio_assets to service_role;

comment on table public.dank_embed_studio_assets is
    'Bot-managed public artwork catalog: metadata only; image bytes are in Supabase Storage. Delete manually only after auditing live embeds.';
