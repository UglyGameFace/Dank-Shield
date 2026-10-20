-- Dedicated DankShield project bootstrap, owner authorized 2026-10-20.
-- Do NOT put this file in supabase/migrations: the legacy project's automation
-- deploys that directory. Apply exclusively to the NEW project reference
-- viamriafkbqhquvrsnua using its dedicated linked account.
-- Empty tables only: never seed obsolete beta guild IDs, history, or credentials.
-- Phase 1 contracts: guild configs, Spam Guard, hostile-actor reputation.
-- All require service-role backend auth; no anon/authenticated privileges.

create table public.guild_configs (
    id uuid not null default gen_random_uuid(),
    guild_id text not null,
    settings jsonb not null default '{}'::jsonb,
    config jsonb not null default '{}'::jsonb,
    metadata jsonb not null default '{}'::jsonb,
    meta jsonb not null default '{}'::jsonb,
    ticket_category_id text,
    ticket_archive_category_id text,
    ticket_prefix text not null default 'ticket'::text,
    auto_delete_ticket_seconds integer not null default 0,
    transcripts_channel_id text,
    transcript_panel_name text not null default 'Support'::text,
    single_panel_mode boolean not null default true,
    verify_channel_id text,
    vc_verify_channel_id text,
    vc_verify_queue_channel_id text,
    token_ttl_minutes integer not null default 240,
    vc_request_ttl_minutes integer not null default 240,
    verify_kick_hours integer not null default 24,
    vc_request_cooldown_seconds integer not null default 60,
    unverified_role_id text,
    verified_role_id text,
    resident_role_id text,
    stoner_role_id text,
    drunken_role_id text,
    staff_role_id text,
    vc_staff_role_id text,
    modlog_channel_id text,
    raidlog_channel_id text,
    join_log_channel_id text,
    force_verify_log_channel_id text,
    enable_optional_role_prompt boolean not null default true,
    optional_role_auto_close_seconds integer not null default 0,
    configured_by_id text,
    configured_by_name text,
    configured_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    guild_name text,
    owner_id text,
    enabled boolean not null default true,
    public_beta_enabled boolean not null default false,
    ticket_category_setup_required boolean not null default true,
    ticket_category_setup_required_reason text,
    ticket_category_setup_version integer not null default 0,
    ticket_category_setup_selected_keys jsonb not null default '[]'::jsonb,
    ticket_category_setup_completed_at timestamptz,
    ticket_category_setup_completed_by_id text,
    ticket_category_setup_completed_by_name text,
    setup_completed boolean not null default false,
    setup_completion_invalidated_at timestamptz,
    setup_completion_invalidated_reason text,
    constraint guild_configs_pkey primary key (id),
    constraint guild_configs_guild_id_key unique (guild_id)
);

create table public.guild_security_settings (
    guild_id text not null,
    spam_blocker_enabled boolean not null default false,
    spam_mode text not null default 'timeout'::text,
    spam_apply_to_verified_users boolean not null default true,
    spam_block_external_invites_only boolean not null default true,
    spam_allow_server_invites boolean not null default true,
    spam_window_seconds integer not null default 12,
    spam_message_threshold integer not null default 5,
    spam_duplicate_threshold integer not null default 3,
    spam_invite_threshold integer not null default 2,
    spam_multi_invite_immediate integer not null default 2,
    spam_delete_history integer not null default 8,
    spam_timeout_minutes integer not null default 30,
    spam_cooldown_seconds integer not null default 20,
    spam_quarantine_role_id text,
    spam_exempt_role_ids jsonb not null default '[]'::jsonb,
    spam_invite_allowed_role_ids jsonb not null default '[]'::jsonb,
    spam_allowed_channel_ids jsonb not null default '[]'::jsonb,
    spam_exempt_user_ids jsonb not null default '[]'::jsonb,
    spam_allowed_invite_codes jsonb not null default '[]'::jsonb,
    updated_at timestamptz,
    updated_by text,
    updated_by_name text,
    constraint guild_security_settings_pkey primary key (guild_id)
);

create table public.guild_security_actor_reputation (
    guild_id bigint not null,
    user_id bigint not null,
    active boolean not null default true,
    classification text not null default 'confirmed_destructive_actor'::text,
    source text not null default 'antinuke'::text,
    is_bot boolean not null default false,
    incident_count integer not null default 1,
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    last_reason text,
    related_user_id bigint,
    cleared_at timestamptz,
    cleared_by bigint,
    clear_reason text,
    constraint guild_security_actor_reputation_pkey primary key (guild_id, user_id)
);

create index idx_dankshield_guild_configs_updated on public.guild_configs (updated_at desc);
create index idx_dankshield_guild_security_actor_active on public.guild_security_actor_reputation (guild_id, active, last_seen_at desc);
create index idx_dankshield_guild_security_actor_related on public.guild_security_actor_reputation (guild_id, related_user_id) where related_user_id is not null;

create function public.set_guild_configs_updated_at()
returns trigger language plpgsql set search_path = pg_catalog, public
as $trigger$
begin
    new.updated_at = now();
    return new;
end
$trigger$;
create trigger trg_guild_configs_updated_at
before update on public.guild_configs for each row
execute function public.set_guild_configs_updated_at();

-- Closed by default. Service-role only.
alter table public.guild_configs enable row level security;
alter table public.guild_security_settings enable row level security;
alter table public.guild_security_actor_reputation enable row level security;
revoke all on table public.guild_configs, public.guild_security_settings,
    public.guild_security_actor_reputation from public, anon, authenticated;
grant select, insert, update, delete on table public.guild_configs,
    public.guild_security_settings, public.guild_security_actor_reputation
    to service_role;
revoke all on function public.set_guild_configs_updated_at() from public, anon, authenticated;

-- Retain the deployed, atomic sparse merge and CAS contract. Do not replace
-- with client-side read/merge/write, which loses concurrent feature settings.
CREATE OR REPLACE FUNCTION public.patch_dank_guild_config(p_guild_id text, p_patch jsonb, p_clear_keys text[] DEFAULT ARRAY[]::text[], p_expected jsonb DEFAULT NULL::jsonb)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
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
$function$;
revoke all on function public.patch_dank_guild_config(text,jsonb,text[],jsonb)
    from public, anon, authenticated;
grant execute on function public.patch_dank_guild_config(text,jsonb,text[],jsonb)
    to service_role;

comment on table public.guild_configs is 'Per-guild Dank Shield runtime config; service-role only. No legacy beta seed.';
comment on table public.guild_security_settings is 'Per-guild durable Spam Guard settings; service-role only.';
comment on table public.guild_security_actor_reputation is 'Confirmed hostile actor evidence; no heuristic-only suspicions.';
