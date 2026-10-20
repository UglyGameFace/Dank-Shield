-- Dedicated NEW DankShield project ONLY. Requires Phase 3 to be applied.
-- Do not install in the original shared Supabase or cut over Discloud.
-- Manual restore points are intentional; only automatic history is capped.
create schema if not exists dank_maintenance;
revoke all on schema dank_maintenance from public, anon, authenticated;
grant usage on schema dank_maintenance to service_role;

create or replace function dank_maintenance.prune_nonmanual_config_versions(
  p_guild_id text, p_config_table text
) returns integer
language plpgsql security invoker set search_path = pg_catalog, public, pg_temp
as $prune$
declare n integer;
begin
  with stale as (
    select v.version_id from public.guild_config_versions v
    where v.guild_id=p_guild_id and v.config_table=p_config_table and v.is_manual=false
    order by v.created_at desc,v.version_id desc offset 50
  ), removed as (
    delete from public.guild_config_versions v using stale s
    where v.version_id=s.version_id returning v.version_id
  ) select count(*) into n from removed;
  return n;
end $prune$;
revoke all on function dank_maintenance.prune_nonmanual_config_versions(text,text) from public,anon,authenticated;
grant execute on function dank_maintenance.prune_nonmanual_config_versions(text,text) to service_role;

-- Keep existing config comparison and snapshot semantics; replace only the
-- two pruning blocks, so manual backups never enter the 50-auto cap.
CREATE OR REPLACE FUNCTION public.capture_guild_config_version()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
declare
    payload jsonb;
    previous_payload jsonb;
    latest_snapshot jsonb;
    settings_payload jsonb;
    metadata_payload jsonb;
    resolved_guild_id text;
    resolved_source text;
    resolved_mode text;
    resolved_actor_id text;
    resolved_reason text;
begin
    payload := to_jsonb(new);
    resolved_guild_id := nullif(payload ->> 'guild_id', '');

    if resolved_guild_id is null then
        return new;
    end if;

    if tg_op = 'UPDATE' then
        previous_payload := to_jsonb(old);

        if public.normalize_guild_config_snapshot(payload)
           = public.normalize_guild_config_snapshot(previous_payload) then
            return new;
        end if;
    end if;

    select v.snapshot
      into latest_snapshot
      from public.guild_config_versions v
     where v.guild_id = resolved_guild_id
       and v.config_table = tg_table_name
     order by v.created_at desc, v.version_id desc
     limit 1;

    if latest_snapshot is not null
       and public.normalize_guild_config_snapshot(latest_snapshot)
           = public.normalize_guild_config_snapshot(payload) then
        return new;
    end if;

    settings_payload := coalesce(payload -> 'settings', '{}'::jsonb);
    metadata_payload := coalesce(payload -> 'metadata', '{}'::jsonb);

    resolved_source := coalesce(
        nullif(settings_payload ->> 'config_last_write_source', ''),
        nullif(metadata_payload ->> 'config_last_write_source', ''),
        'database'
    );
    resolved_mode := coalesce(
        nullif(settings_payload ->> 'config_last_write_mode', ''),
        nullif(metadata_payload ->> 'config_last_write_mode', '')
    );
    resolved_actor_id := coalesce(
        nullif(settings_payload ->> 'config_last_write_actor_id', ''),
        nullif(metadata_payload ->> 'config_last_write_actor_id', '')
    );
    resolved_reason := coalesce(
        nullif(settings_payload ->> 'config_last_write_reason', ''),
        nullif(metadata_payload ->> 'config_last_write_reason', '')
    );

    insert into public.guild_config_versions (
        guild_id,
        config_table,
        snapshot,
        source,
        mode,
        actor_id,
        reason,
        is_manual
    ) values (
        resolved_guild_id,
        tg_table_name,
        payload,
        resolved_source,
        resolved_mode,
        resolved_actor_id,
        resolved_reason,
        false
    );

    perform dank_maintenance.prune_nonmanual_config_versions(resolved_guild_id, tg_table_name);

    return new;
end;
$function$;
alter function public.capture_guild_config_version() set search_path = pg_catalog, public, pg_temp;
revoke all on function public.capture_guild_config_version() from public,anon,authenticated;
grant execute on function public.capture_guild_config_version() to service_role;

CREATE OR REPLACE FUNCTION public.capture_ticket_category_version()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
declare
    resolved_guild_id text;
    payload jsonb;
    category_rows jsonb;
    latest_snapshot jsonb;
begin
    -- Atomic restore RPC temporarily suppresses row-level intermediate versions.
    if coalesce(current_setting('dank_shield.skip_config_history', true), '') = '1' then
        if tg_op = 'DELETE' then
            return old;
        end if;
        return new;
    end if;

    if tg_op = 'DELETE' then
        resolved_guild_id := nullif(to_jsonb(old) ->> 'guild_id', '');
    else
        resolved_guild_id := nullif(to_jsonb(new) ->> 'guild_id', '');
    end if;

    if resolved_guild_id is null then
        if tg_op = 'DELETE' then
            return old;
        end if;
        return new;
    end if;

    select coalesce(
        jsonb_agg(
            to_jsonb(tc)
            order by
                coalesce((to_jsonb(tc) ->> 'sort_order')::integer, 999),
                coalesce(to_jsonb(tc) ->> 'slug', ''),
                coalesce(to_jsonb(tc) ->> 'id', '')
        ),
        '[]'::jsonb
    )
      into category_rows
      from public.ticket_categories tc
     where tc.guild_id = resolved_guild_id;

    payload := jsonb_build_object(
        'guild_id', resolved_guild_id,
        'rows', category_rows
    );

    select v.snapshot
      into latest_snapshot
      from public.guild_config_versions v
     where v.guild_id = resolved_guild_id
       and v.config_table = 'ticket_categories'
     order by v.created_at desc, v.version_id desc
     limit 1;

    if latest_snapshot is not null
       and public.normalize_ticket_category_snapshot(latest_snapshot)
           = public.normalize_ticket_category_snapshot(payload) then
        if tg_op = 'DELETE' then
            return old;
        end if;
        return new;
    end if;

    insert into public.guild_config_versions (
        guild_id,
        config_table,
        snapshot,
        source,
        mode,
        is_manual
    ) values (
        resolved_guild_id,
        'ticket_categories',
        payload,
        'ticket_categories',
        lower(tg_op),
        false
    );

    perform dank_maintenance.prune_nonmanual_config_versions(resolved_guild_id, 'ticket_categories');

    if tg_op = 'DELETE' then
        return old;
    end if;
    return new;
end;
$function$;
alter function public.capture_ticket_category_version() set search_path = pg_catalog, public, pg_temp;
revoke all on function public.capture_ticket_category_version() from public,anon,authenticated;
grant execute on function public.capture_ticket_category_version() to service_role;

-- Original (guild_id,category_id) index is still required for guild queries;
-- the new category_id-leading index covers FK reverse lookup without replacing it.
create index if not exists idx_dank_tickets_category_id_fkey on public.tickets(category_id);
create index if not exists idx_dank_ticket_form_responses_ticket_fkey on public.ticket_form_responses(ticket_id);

-- Token retention applies to used and unused tokens, so replace the
-- unused-only expiry index with one covering both states.
create index if not exists idx_dank_verify_expiry_all on public.verification_tokens(expires_at);
drop index if exists public.idx_dank_verify_expiry;

-- No auth-bearing token is removed until it has been expired for >72 hours.
-- This is a bounded, idempotent action, not a global age-based table purge.
create or replace function dank_maintenance.purge_expired_verification_tokens(
 p_batch_size integer default 500
) returns integer
language plpgsql security invoker set search_path = pg_catalog, public, pg_temp
as $purge$
declare n integer;
begin
  if p_batch_size is null or p_batch_size < 1 or p_batch_size > 1000 then
    raise exception 'Batch size must be between 1 and 1000';
  end if;
  with stale as (
    select token from public.verification_tokens
    where expires_at < now() - interval '72 hours'
    order by expires_at, token
    limit p_batch_size for update skip locked
  ), removed as (
    delete from public.verification_tokens v using stale s
    where v.token=s.token returning v.token
  ) select count(*) into n from removed;
  return n;
end $purge$;
revoke all on function dank_maintenance.purge_expired_verification_tokens(integer) from public,anon,authenticated;
grant execute on function dank_maintenance.purge_expired_verification_tokens(integer) to service_role;
