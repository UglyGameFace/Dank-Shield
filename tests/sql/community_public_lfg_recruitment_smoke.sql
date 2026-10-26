-- Disposable PG16 CI fixture. Must never run against production.
begin;

insert into public.dank_community_hub_settings (
  guild_id, enabled, maintenance_mode, public_lfg_enabled,
  public_lfg_invite_url, public_lfg_display_name, idle_timeout_seconds, max_session_lifetime_seconds
) values (
  'public-demo', true, false, false,
  'https://discord.gg/validinvite', 'The 420 Lobby', 1800, 43200
);

insert into public.dank_community_sessions (
  guild_id, idempotency_key, game_name, privacy, state,
  capacity, public_lfg_opt_in, notes, host_id
) values (
  'public-demo', 'pub-1', 'Minecraft', 'public', 'open',
  6, true, 'NEVER-PUBLISH-SECRET', 'PRIVATE-HOST-ID'
);

do $$
begin
  if exists (select 1 from public.community_hub_public_lfg_directory(100)) then
    raise exception 'guild consent off leaked recruitment';
  end if;
end;
$$;

update public.dank_community_hub_settings
  set public_lfg_enabled = true where guild_id = 'public-demo';

do $$
declare item record;
begin
  select * into item from public.community_hub_public_lfg_directory(100);
  if item.game_name <> 'Minecraft'
    or item.capacity <> 6
    or item.invite_url <> 'https://discord.gg/validinvite'
    or item.community_name <> 'The 420 Lobby'
  then
    raise exception 'approved public recruitment omitted or mutated row';
  end if;
  if (select count(*) from public.community_hub_public_lfg_directory(100)) <> 1 then
    raise exception 'approved directory should have exactly one row';
  end if;
end;
$$;

-- Even an enabled guild must explicitly provide a publishable identity.
update public.dank_community_hub_settings set public_lfg_display_name = null
  where guild_id = 'public-demo';
do $$
begin
  if exists (select 1 from public.community_hub_public_lfg_directory(100)) then
    raise exception 'missing public community label leaked recruitment';
  end if;
end;
$$;
update public.dank_community_hub_settings set public_lfg_display_name = 'The 420 Lobby'
  where guild_id = 'public-demo';

update public.dank_community_sessions set privacy='locked'
  where guild_id = 'public-demo';
do $$
begin
  if exists (select 1 from public.community_hub_public_lfg_directory(100)) then
    raise exception 'locked session leaked recruitment';
  end if;
end;
$$;
update public.dank_community_sessions set privacy='public', public_lfg_opt_in=false
  where guild_id = 'public-demo';
do $$
begin
  if exists (select 1 from public.community_hub_public_lfg_directory(100)) then
    raise exception 'revoked host consent leaked recruitment';
  end if;
end;
$$;
update public.dank_community_sessions set public_lfg_opt_in=true
  where guild_id='public-demo';
update public.dank_community_hub_settings set maintenance_mode=true
  where guild_id='public-demo';
do $$
begin
  if exists (select 1 from public.community_hub_public_lfg_directory(100)) then
    raise exception 'maintenance-mode session leaked recruitment';
  end if;
end;
$$;
update public.dank_community_hub_settings set maintenance_mode=false
  where guild_id='public-demo';
update public.dank_community_sessions set
  last_activity_at=now() - interval '2 hours'
  where guild_id='public-demo';
do $$
begin
  if exists (select 1 from public.community_hub_public_lfg_directory(100)) then
    raise exception 'expired session leaked recruitment';
  end if;
end;
$$;

rollback;
