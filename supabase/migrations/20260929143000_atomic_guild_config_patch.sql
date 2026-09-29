-- ============================================================
-- 20260929143000_atomic_guild_config_patch.sql
-- ------------------------------------------------------------
-- Atomic sparse updates for the canonical per-guild config row.
--
-- Multiple Dank Shield subsystems share guild_configs. Client-side
-- read/merge/write can lose an unrelated nested setting when two workers read
-- the same old row and then replace the JSON payload. This RPC merges only the
-- supplied keys inside PostgreSQL while the UPDATE owns the row lock, so
-- concurrent naming/member-setup/etc. writes preserve one another.
--
-- The function also updates matching real columns when a patch key has a flat
-- compatibility column. Unknown keys remain JSON-only.
-- ============================================================

create or replace function public.patch_dank_guild_config(
    p_guild_id text,
    p_patch jsonb,
    p_clear_keys text[] default array[]::text[],
    p_expected jsonb default null
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    clean_patch jsonb := coalesce(p_patch, '{}'::jsonb);
    clear_keys text[] := coalesce(p_clear_keys, array[]::text[]);
    expected_patch jsonb := p_expected;
    assignments text[] := array[]::text[];
    json_column text;
    compare_column text := null;
    compare_payload jsonb := '{}'::jsonb;
    expected_key text;
    expectation_matches boolean := true;
    column_row record;
    result jsonb;
begin
    if p_guild_id is null or btrim(p_guild_id) = '' then
        raise exception 'guild_id is required';
    end if;

    if jsonb_typeof(clean_patch) <> 'object' then
        raise exception 'guild config patch must be a JSON object';
    end if;

    if expected_patch is not null and jsonb_typeof(expected_patch) <> 'object' then
        raise exception 'guild config expected patch must be a JSON object';
    end if;

    -- These fields are database-owned and must never be client patched or cleared.
    clean_patch := clean_patch
        - 'id'
        - 'guild_id'
        - 'created_at'
        - 'updated_at'
        - 'settings'
        - 'config'
        - 'metadata'
        - 'meta';

    select coalesce(array_agg(distinct key_name), array[]::text[])
      into clear_keys
      from unnest(clear_keys) key_name
     where nullif(btrim(key_name), '') is not null
       and key_name not in (
           'id',
           'guild_id',
           'created_at',
           'updated_at',
           'settings',
           'config',
           'metadata',
           'meta'
       );

    insert into public.guild_configs (guild_id)
    values (btrim(p_guild_id))
    on conflict (guild_id) do nothing;

    -- Optional compare-and-swap boundary for callers that update one shared
    -- nested config value from multiple bot processes. Lock the row first,
    -- compare only the supplied expected keys, and return the current row
    -- without writing when another worker won the race.
    if expected_patch is not null then
        foreach json_column in array array['settings', 'config', 'metadata', 'meta']
        loop
            if exists (
                select 1
                  from pg_attribute a
                 where a.attrelid = 'public.guild_configs'::regclass
                   and a.attnum > 0
                   and not a.attisdropped
                   and a.attname = json_column
                   and format_type(a.atttypid, a.atttypmod) = 'jsonb'
            ) then
                compare_column := json_column;
                exit;
            end if;
        end loop;

        if compare_column is null then
            raise exception 'guild_configs has no JSON compatibility column for compare-and-swap';
        end if;

        execute format(
            'select coalesce(%I, ''{}''::jsonb) from public.guild_configs where guild_id = $1 for update',
            compare_column
        )
        using btrim(p_guild_id)
        into compare_payload;

        for expected_key in select jsonb_object_keys(expected_patch)
        loop
            if expected_patch -> expected_key = 'null'::jsonb then
                if compare_payload ? expected_key
                   and compare_payload -> expected_key <> 'null'::jsonb then
                    expectation_matches := false;
                    exit;
                end if;
            elsif compare_payload -> expected_key is distinct from expected_patch -> expected_key then
                expectation_matches := false;
                exit;
            end if;
        end loop;

        if not expectation_matches then
            select to_jsonb(gc)
              into result
              from public.guild_configs gc
             where gc.guild_id = btrim(p_guild_id);

            return jsonb_build_object(
                '__atomic_patch_applied', false,
                '__atomic_patch_row', coalesce(result, '{}'::jsonb)
            );
        end if;
    end if;

    -- Every compatibility JSON column receives the same sparse merge. The
    -- expression is evaluated against the row version locked by this UPDATE,
    -- so a concurrent writer cannot replace a sibling key from a stale read.
    foreach json_column in array array['settings', 'config', 'metadata', 'meta']
    loop
        if exists (
            select 1
              from pg_attribute a
             where a.attrelid = 'public.guild_configs'::regclass
               and a.attnum > 0
               and not a.attisdropped
               and a.attname = json_column
               and format_type(a.atttypid, a.atttypmod) = 'jsonb'
        ) then
            assignments := array_append(
                assignments,
                format(
                    '%I = (coalesce(%I, ''{}''::jsonb) - $3) || $2',
                    json_column,
                    json_column
                )
            );
        end if;
    end loop;

    -- Preserve existing flat-column compatibility without rebuilding the whole
    -- row. Only columns actually named by this sparse patch are touched.
    for column_row in
        select
            a.attname as column_name,
            format_type(a.atttypid, a.atttypmod) as type_name
        from pg_attribute a
        where a.attrelid = 'public.guild_configs'::regclass
          and a.attnum > 0
          and not a.attisdropped
          and coalesce(a.attgenerated, '') = ''
          and a.attname in (select jsonb_object_keys(clean_patch))
          and a.attname not in (
              'id',
              'guild_id',
              'created_at',
              'updated_at',
              'settings',
              'config',
              'metadata',
              'meta'
          )
    loop
        assignments := array_append(
            assignments,
            format(
                '%I = (($2 ->> %L)::%s)',
                column_row.column_name,
                column_row.column_name,
                column_row.type_name
            )
        );
    end loop;

    -- Clearing is equally sparse. Compatibility flat columns are nulled only
    -- when the caller explicitly names them; sibling columns and JSON keys
    -- remain untouched.
    for column_row in
        select a.attname as column_name
        from pg_attribute a
        where a.attrelid = 'public.guild_configs'::regclass
          and a.attnum > 0
          and not a.attisdropped
          and coalesce(a.attgenerated, '') = ''
          and a.attnotnull = false
          and a.attname = any(clear_keys)
          and a.attname not in (
              'id',
              'guild_id',
              'created_at',
              'updated_at',
              'settings',
              'config',
              'metadata',
              'meta'
          )
          and not (clean_patch ? a.attname)
    loop
        assignments := array_append(
            assignments,
            format('%I = null', column_row.column_name)
        );
    end loop;

    if exists (
        select 1
          from pg_attribute a
         where a.attrelid = 'public.guild_configs'::regclass
           and a.attnum > 0
           and not a.attisdropped
           and a.attname = 'updated_at'
    ) then
        assignments := array_append(assignments, 'updated_at = now()');
    end if;

    if coalesce(array_length(assignments, 1), 0) = 0 then
        select to_jsonb(gc)
          into result
          from public.guild_configs gc
         where gc.guild_id = btrim(p_guild_id);
        if expected_patch is not null then
            return jsonb_build_object(
                '__atomic_patch_applied', true,
                '__atomic_patch_row', coalesce(result, '{}'::jsonb)
            );
        end if;
        return coalesce(result, '{}'::jsonb);
    end if;

    execute format(
        'update public.guild_configs as gc set %s where gc.guild_id = $1 returning to_jsonb(gc)',
        array_to_string(assignments, ', ')
    )
    using btrim(p_guild_id), clean_patch, clear_keys
    into result;

    if expected_patch is not null then
        return jsonb_build_object(
            '__atomic_patch_applied', true,
            '__atomic_patch_row', coalesce(result, '{}'::jsonb)
        );
    end if;
    return coalesce(result, '{}'::jsonb);
end;
$;

revoke all on function public.patch_dank_guild_config(text, jsonb, text[], jsonb) from public;
revoke all on function public.patch_dank_guild_config(text, jsonb, text[], jsonb) from anon;
revoke all on function public.patch_dank_guild_config(text, jsonb, text[], jsonb) from authenticated;
grant execute on function public.patch_dank_guild_config(text, jsonb, text[], jsonb) to service_role;
