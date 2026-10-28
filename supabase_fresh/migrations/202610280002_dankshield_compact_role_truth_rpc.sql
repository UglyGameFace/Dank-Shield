-- Read-only, role-specific configuration endpoint for sync member-event paths.
-- Unlike select('*'), only these nine role keys cross the HTTP boundary.
-- Flat, non-null columns override legacy JSON; precedence matches
-- stoney_verify.role_truth.merge_config_row on all four containers.
create or replace function public.get_dank_guild_role_config(
    p_guild_id text
)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
  with scoped as (
    select g.guild_id,
      coalesce(g.settings,'{}'::jsonb)
      || coalesce(g.config,'{}'::jsonb)
      || coalesce(g.metadata,'{}'::jsonb)
      || coalesce(g.meta,'{}'::jsonb) as nested,
      to_jsonb(g) as raw
    from public.guild_configs as g
    where g.guild_id = p_guild_id
    limit 1
  )
  select jsonb_build_object('guild_id', s.guild_id)
    || coalesce((
      select jsonb_object_agg(r.role_key,
        case
          when s.raw -> r.role_key is not null
           and s.raw -> r.role_key <> 'null'::jsonb
          then s.raw -> r.role_key
          else s.nested -> r.role_key
        end
      )
      from unnest(array[
        'unverified_role_id', 'staff_role_id', 'vc_staff_role_id',
        'resident_role_id', 'member_role_id', 'stoner_role_id',
        'drunken_role_id', 'verified_role_id'
      ]::text[]) as r(role_key)
      where (s.raw -> r.role_key is not null
        and s.raw -> r.role_key <> 'null'::jsonb)
        or s.nested ? r.role_key
    ), '{}'::jsonb)
  from scoped as s;
$$;

revoke all on function public.get_dank_guild_role_config(text)
  from public, anon, authenticated;
grant execute on function public.get_dank_guild_role_config(text)
  to service_role;
comment on function public.get_dank_guild_role_config(text)
  is 'Guild-isolated role-only config read, preserves four-container JSON precedence and flat-column overrides; SECURITY INVOKER.';
