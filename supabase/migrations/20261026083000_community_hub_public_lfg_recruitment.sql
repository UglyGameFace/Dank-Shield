-- Explicit opt-in outward recruiting. Existing guilds and sessions stay private.
-- The bot's Community Hub is the only owner of session state.
alter table public.dank_community_hub_settings
  add column if not exists public_lfg_enabled boolean not null default false;
alter table public.dank_community_hub_settings
  add column if not exists public_lfg_invite_url text;
-- Staff selects an explicitly public, human-readable host community label.
alter table public.dank_community_hub_settings
  add column if not exists public_lfg_display_name text;
alter table public.dank_community_sessions
  add column if not exists public_lfg_opt_in boolean not null default false;

create index if not exists idx_dank_community_public_lfg_sessions
  on public.dank_community_sessions (last_activity_at desc)
  where public_lfg_opt_in = true and privacy = 'public'
    and state in ('open', 'forming', 'ready', 'active');

-- Return only display-safe, intentionally published fields.
-- No guild/member IDs, notes, session tokens, Discord channel IDs, or auth data.
-- Fail closed when settings are disabled, server is in maintenance, session
-- is no longer public/active, or its configured idle/hard lifetime elapsed.
create or replace function public.community_hub_public_lfg_directory(
  p_limit integer default 40
)
returns table (
  game_name text,
  capacity integer,
  invite_url text,
  community_name text,
  activity_at timestamptz
)
language sql
stable
security invoker
set search_path = ''
as $$
  select left(s.game_name, 80), s.capacity, h.public_lfg_invite_url,
    left(h.public_lfg_display_name, 60), s.last_activity_at
  from public.dank_community_sessions s
  join public.dank_community_hub_settings h on h.guild_id = s.guild_id
  where h.enabled is true and h.maintenance_mode is false
    and h.public_lfg_enabled is true
    and h.public_lfg_display_name is not null
    and length(btrim(h.public_lfg_display_name)) between 2 and 60
    and h.public_lfg_invite_url is not null
    and h.public_lfg_invite_url ~ '^https://(discord[.]gg/|discord[.]com/invite/)[A-Za-z0-9-]{2,64}$'
    and s.public_lfg_opt_in is true
    and s.privacy = 'public'
    and s.state in ('open', 'forming', 'ready', 'active')
    and s.created_at + (h.max_session_lifetime_seconds * interval '1 second') > now()
    and s.last_activity_at + (h.idle_timeout_seconds * interval '1 second') > now()
  order by s.last_activity_at desc, s.id desc
  limit least(100, greatest(1, coalesce(p_limit, 40)));
$$;

revoke all on function public.community_hub_public_lfg_directory(integer)
  from public, anon, authenticated;
grant execute on function public.community_hub_public_lfg_directory(integer)
  to service_role;
