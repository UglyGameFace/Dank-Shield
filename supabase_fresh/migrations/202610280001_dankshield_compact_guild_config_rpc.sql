-- Reversible, read-only compact retrieval. Keep all existing JSON columns and
-- historical config rows intact. This RPC never changes stored data.
-- settings -> config -> metadata -> meta precedence matches
-- stoney_verify.guild_config._merge_row_settings; flat columns win afterwards.
create or replace function public.get_dank_guild_config_compact(
    p_guild_id text,
    p_known_updated_at timestamptz default null
)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
    select case
        when p_known_updated_at is not null
             and g.updated_at is not distinct from p_known_updated_at
        then jsonb_build_object('_not_modified', true)
        else
            (to_jsonb(g) - array['settings','config','metadata','meta']::text[])
            || jsonb_build_object(
                'settings',
                coalesce(g.settings, '{}'::jsonb)
                || coalesce(g.config, '{}'::jsonb)
                || coalesce(g.metadata, '{}'::jsonb)
                || coalesce(g.meta, '{}'::jsonb)
            )
        end
    from public.guild_configs as g
    where g.guild_id = p_guild_id
    limit 1
$$;

-- Only the server-side bot may call this function. No privilege escalation;
-- RLS still applies because the function is SECURITY INVOKER.
revoke all on function public.get_dank_guild_config_compact(text,timestamptz)
    from public, anon, authenticated;
grant execute on function public.get_dank_guild_config_compact(text,timestamptz)
    to service_role;

comment on function public.get_dank_guild_config_compact(text,timestamptz)
    is 'Lossless legacy-precedence compact config response, with optional revision short circuit. Does not modify persisted guild state.';
