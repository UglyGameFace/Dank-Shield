-- Explicitly opt-in, read-only The100.io listings for Community Hub.
-- Existing servers and installations remain disabled; no token is stored here.
alter table public.dank_community_hub_settings
    add column if not exists external_the100_enabled boolean not null default false;
