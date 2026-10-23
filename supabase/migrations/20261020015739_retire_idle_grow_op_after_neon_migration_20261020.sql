-- Owner-authorized removal of the abandoned Idle Grow Op data only.
-- Production Dank Shield remains in this shared Supabase project.
-- No CASCADE: reject any unexpected dependency rather than deleting other app objects.
do $guard$
declare
  expected_public text[] := array[
    'app_schema_migrations','global_accounts','guild_profiles',
    'guild_worlds','users','world'
  ];
  expected_idle text[] := array[
    'auctions','crews','user_cooldowns','users','world_state'
  ];
begin
  if (select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
       where n.nspname='public' and c.relkind in ('r','p')
       and c.relname=any(expected_public)) <> 6 then
    raise exception 'Idle Grow public table inventory changed; stop';
  end if;
  if (select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
       where n.nspname='idle' and c.relkind in ('r','p')
       and c.relname=any(expected_idle)) <> 5 then
    raise exception 'Idle Grow schema table inventory changed; stop';
  end if;
  if exists (
    select 1 from pg_class c join pg_namespace n on n.oid=c.relnamespace
    where n.nspname='idle' and c.relkind in ('r','p','v','m','S')
      and c.relname<>all(expected_idle)
  ) then
    raise exception 'Unknown Idle Grow schema object; stop';
  end if;
  if exists (
    with target as (
      select c.oid from pg_class c join pg_namespace n on c.relnamespace=n.oid
      where (n.nspname='idle' and c.relname=any(expected_idle))
       or (n.nspname='public' and c.relname=any(expected_public))
    )
    select 1 from pg_constraint co
    where co.contype='f' and co.confrelid in (select oid from target)
      and co.conrelid not in (select oid from target)
  ) then
    raise exception 'External FK references abandoned game table; stop';
  end if;
end
$guard$;

-- Explicitly named game-only RPC endpoints. Do not touch shared trigger functions.
drop function if exists public.idle_grow_list_notification_candidates(bigint[]) restrict;
drop function if exists public.idle_grow_save_scoped_records(jsonb,jsonb,jsonb) restrict;

-- Older game schema only, with no CASCADE.
drop table idle.auctions, idle.crews, idle.user_cooldowns,
  idle.users, idle.world_state restrict;
drop function if exists idle.touch_updated_at() restrict;
drop schema idle restrict;

-- Game-only tables from the legacy public-scoped game implementation.
drop table public.app_schema_migrations, public.global_accounts,
  public.guild_profiles, public.guild_worlds, public.users,
  public.world restrict;

