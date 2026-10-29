-- Wrap the canonical atomic writer without replacing or weakening it.
-- The original writer is still the sole authority for lock/CAS/merge semantics.
-- Only the HTTP response shape is compacted; no stored data is changed here.
create or replace function public.patch_dank_guild_config_compact(
    p_guild_id text,
    p_patch jsonb,
    p_clear_keys text[] default array[]::text[],
    p_expected jsonb default null
)
returns jsonb
language sql
volatile
security invoker
set search_path = ''
as $$
  with called as materialized (
    select public.patch_dank_guild_config(
      p_guild_id,p_patch,p_clear_keys,p_expected
    ) as envelope
  ),
  extracted as (
    select envelope,
      case when p_expected is null then envelope
           else envelope -> '__atomic_patch_row' end as source_row
    from called
  ),
  compacted as (
    select envelope,
      case when jsonb_typeof(source_row) = 'object'
                 and source_row ? 'guild_id'
           then (source_row - array['settings','config','metadata','meta']::text[])
              || jsonb_build_object(
                   'settings',
                     coalesce(source_row->'settings','{}'::jsonb)
                     || coalesce(source_row->'config','{}'::jsonb)
                     || coalesce(source_row->'metadata','{}'::jsonb)
                     || coalesce(source_row->'meta','{}'::jsonb)
                 )
           else source_row end as compact_row
    from extracted
  )
  select case
    when p_expected is null then compact_row
    else (envelope - '__atomic_patch_row')
      || jsonb_build_object(
           '__atomic_patch_row',coalesce(compact_row,'{}'::jsonb)
         )
  end
  from compacted;
$$;

revoke all on function public.patch_dank_guild_config_compact(text,jsonb,text[],jsonb)
  from public, anon, authenticated;
grant execute on function public.patch_dank_guild_config_compact(text,jsonb,text[],jsonb)
  to service_role;

comment on function public.patch_dank_guild_config_compact(text,jsonb,text[],jsonb)
  is 'Server-only response compacting wrapper around the authoritative atomic patch, preserving CAS and persisted data.';
