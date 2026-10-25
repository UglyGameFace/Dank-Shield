-- Optional, transaction-safe allocation guard for bot-managed public artwork.
-- Additive and service-role only. Older objects and published embeds are never
-- deleted, edited, or migrated. Configure explicit budgets before enabling.
create table if not exists public.dank_embed_studio_asset_holds (
    object_path text primary key
        check (char_length(object_path) between 35 and 180),
    guild_id text not null check (guild_id ~ '^[0-9]{1,20}$'),
    size_bytes bigint not null check (size_bytes between 1 and 6291456),
    created_at timestamptz not null default now(),
    check (object_path like guild_id || '/%')
);
create index if not exists dank_studio_asset_holds_guild_idx
    on public.dank_embed_studio_asset_holds(guild_id, created_at);
alter table public.dank_embed_studio_asset_holds enable row level security;
revoke all on public.dank_embed_studio_asset_holds from public, anon, authenticated;
grant select,insert,delete on public.dank_embed_studio_asset_holds to service_role;

-- One transaction-wide lock prevents competing guilds from passing a shared
-- global budget check concurrently. Counts include stale holds, because the
-- actual object could exist after a network timeout or crash. Never expire
-- uncertain reservations automatically.
create or replace function public.dank_embed_studio_asset_reserve(
    p_guild_id text, p_path text, p_bytes bigint,
    p_guild_budget bigint, p_global_budget bigint
) returns jsonb
language plpgsql security invoker set search_path=public,pg_temp
as $$
declare
    guild_used bigint;
    global_used bigint;
begin
    if p_guild_id is null or p_guild_id !~ '^[0-9]{1,20}$'
      or p_path is null or p_path not like p_guild_id || '/%'
      or char_length(p_path) not between 35 and 180
      or p_bytes is null or p_bytes not between 1 and 6291456
      or p_guild_budget is null or p_global_budget is null
      or p_guild_budget < 6291456 or p_global_budget < p_guild_budget
      or p_global_budget > 1099511627776 then
        raise exception 'Invalid Studio artwork quota request';
    end if;
    perform pg_advisory_xact_lock(517108430271::bigint);
    if exists(select 1 from public.dank_embed_studio_asset_holds where object_path=p_path)
       or exists(select 1 from public.dank_embed_studio_assets where object_path=p_path) then
        return jsonb_build_object('reserved',false,'reason','duplicate_path');
    end if;
    select
      coalesce((select sum(size_bytes) from public.dank_embed_studio_assets where guild_id=p_guild_id),0)
      + coalesce((select sum(size_bytes) from public.dank_embed_studio_asset_holds where guild_id=p_guild_id),0)
      into guild_used;
    select
      coalesce((select sum(size_bytes) from public.dank_embed_studio_assets),0)
      + coalesce((select sum(size_bytes) from public.dank_embed_studio_asset_holds),0)
      into global_used;
    if guild_used + p_bytes > p_guild_budget
       or global_used + p_bytes > p_global_budget then
        return jsonb_build_object('reserved',false,'reason','budget_full',
            'guild_used_bytes',guild_used,'global_used_bytes',global_used);
    end if;
    insert into public.dank_embed_studio_asset_holds(object_path,guild_id,size_bytes)
       values(p_path,p_guild_id,p_bytes);
    return jsonb_build_object('reserved',true,
        'guild_used_bytes',guild_used+p_bytes,
        'global_used_bytes',global_used+p_bytes);
end;
$$;
revoke all on function public.dank_embed_studio_asset_reserve(text,text,bigint,bigint,bigint)
    from public,anon,authenticated;
grant execute on function public.dank_embed_studio_asset_reserve(text,text,bigint,bigint,bigint)
    to service_role;

-- Finalize metadata only for a live, matching reservation. Both operations
-- commit atomically. Never infer file existence from the database alone.
create or replace function public.dank_embed_studio_asset_finalize(
    p_guild_id text, p_path text, p_sha256 text, p_name text,
    p_content_type text, p_bytes bigint
) returns jsonb
language plpgsql security invoker set search_path=public,pg_temp
as $$
declare
    reserved bigint;
begin
    if p_guild_id is null or p_guild_id !~ '^[0-9]{1,20}$'
       or p_path is null or p_path not like p_guild_id || '/%'
       or p_sha256 is null or p_sha256 !~ '^[0-9a-f]{64}$'
       or p_name is null or char_length(p_name) not between 1 and 60
       or p_content_type not in ('image/png','image/jpeg','image/webp','image/gif')
       or p_bytes is null or p_bytes not between 1 and 6291456 then
        raise exception 'Invalid Studio artwork metadata';
    end if;
    select size_bytes into reserved from public.dank_embed_studio_asset_holds
       where object_path=p_path and guild_id=p_guild_id for update;
    if not found or reserved <> p_bytes then
        return jsonb_build_object('finalized',false,'reason','missing_reservation');
    end if;
    insert into public.dank_embed_studio_assets
        (guild_id,object_path,image_sha256,name,content_type,size_bytes)
        values(p_guild_id,p_path,p_sha256,p_name,p_content_type,p_bytes);
    delete from public.dank_embed_studio_asset_holds where object_path=p_path;
    return jsonb_build_object('finalized',true);
end;
$$;
revoke all on function public.dank_embed_studio_asset_finalize(text,text,text,text,text,bigint)
    from public,anon,authenticated;
grant execute on function public.dank_embed_studio_asset_finalize(text,text,text,text,text,bigint)
    to service_role;

-- Only invoke after a confirmed successful Storage remove. A failed or
-- ambiguous remove keeps its reservation allocated for manual reconciliation.
create or replace function public.dank_embed_studio_asset_release(
    p_guild_id text,p_path text
) returns boolean
language plpgsql security invoker set search_path=public,pg_temp
as $$
begin
    if p_guild_id is null or p_guild_id !~ '^[0-9]{1,20}$'
       or p_path is null or p_path not like p_guild_id || '/%' then
       raise exception 'Invalid Studio artwork release';
    end if;
    delete from public.dank_embed_studio_asset_holds
      where guild_id=p_guild_id and object_path=p_path;
    return found;
end;
$$;
revoke all on function public.dank_embed_studio_asset_release(text,text)
    from public,anon,authenticated;
grant execute on function public.dank_embed_studio_asset_release(text,text)
    to service_role;

comment on table public.dank_embed_studio_asset_holds is
    'Conservative allocation holds; old uncertain reservations require human audit before release.';

-- One bounded-size JSON result, rather than transferring every object row
-- to the bot. Usage includes reservations, even when a process died mid-upload.
create or replace function public.dank_embed_studio_asset_usage(p_guild_id text)
returns jsonb
language plpgsql security invoker set search_path=public,pg_temp
as $$
declare
    total_files bigint;
    total_holds bigint;
    guild_files bigint;
    guild_holds bigint;
    files_count bigint;
    holds_count bigint;
    stale_count bigint;
begin
    if p_guild_id is null or p_guild_id !~ '^[0-9]{1,20}$' then
        raise exception 'Invalid Studio artwork usage guild';
    end if;
    select coalesce(sum(size_bytes),0),count(*) into total_files,files_count
        from public.dank_embed_studio_assets;
    select coalesce(sum(size_bytes),0),count(*) into total_holds,holds_count
        from public.dank_embed_studio_asset_holds;
    select coalesce(sum(size_bytes),0) into guild_files
        from public.dank_embed_studio_assets where guild_id=p_guild_id;
    select coalesce(sum(size_bytes),0) into guild_holds
        from public.dank_embed_studio_asset_holds where guild_id=p_guild_id;
    select count(*) into stale_count from public.dank_embed_studio_asset_holds
        where created_at < now()-interval '30 minutes';
    return jsonb_build_object(
        'guild_used',guild_files+guild_holds,
        'global_used',total_files+total_holds,
        'asset_count',files_count,'reservation_count',holds_count,
        'stale_reservations',stale_count
    );
end;
$$;
revoke all on function public.dank_embed_studio_asset_usage(text)
    from public,anon,authenticated;
grant execute on function public.dank_embed_studio_asset_usage(text) to service_role;
