-- Community Hub HubLink connection flow.
-- Human-facing partner setup no longer requires raw Discord guild IDs.
-- Codes are short-lived, one-time, and only a SHA-256 digest is persisted.

create table if not exists public.dank_community_hub_link_codes (
    id uuid primary key default gen_random_uuid(),
    source_guild_id text not null,
    created_by_user_id text not null,
    code_hash text not null,
    code_hint text not null,
    state text not null default 'pending'
        check (state in ('pending','redeemed','revoked','expired')),
    expires_at timestamptz not null,
    redeemed_at timestamptz,
    redeemed_by_guild_id text,
    redeemed_by_user_id text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (char_length(code_hash) = 64),
    check (code_hash ~ '^[0-9a-f]{64}$'),
    check (char_length(code_hint) between 2 and 8),
    check (expires_at > created_at),
    check (
        state <> 'redeemed'
        or (
            redeemed_at is not null
            and redeemed_by_guild_id is not null
            and redeemed_by_user_id is not null
        )
    )
);

create unique index if not exists uq_dank_community_hublink_code_hash
    on public.dank_community_hub_link_codes (code_hash);

create unique index if not exists uq_dank_community_hublink_pending_source
    on public.dank_community_hub_link_codes (source_guild_id)
    where state='pending';

create index if not exists idx_dank_community_hublink_expiry
    on public.dank_community_hub_link_codes (state, expires_at);

alter table public.dank_community_hub_link_codes enable row level security;
revoke all on table public.dank_community_hub_link_codes from public, anon, authenticated;
grant select, insert, update, delete on table public.dank_community_hub_link_codes to service_role;

create or replace function public.community_hub_create_link_code(
    p_source_guild_id text,
    p_actor_id text,
    p_code_hash text,
    p_code_hint text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_row public.dank_community_hub_link_codes%rowtype;
    v_recent integer;
begin
    if btrim(coalesce(p_source_guild_id,'')) = ''
       or btrim(coalesce(p_actor_id,'')) = '' then
        raise exception 'HubLink source server and creator are required';
    end if;
    if coalesce(p_code_hash,'') !~ '^[0-9a-f]{64}$' then
        raise exception 'HubLink code digest is invalid';
    end if;
    if char_length(btrim(coalesce(p_code_hint,''))) not between 2 and 8 then
        raise exception 'HubLink code hint is invalid';
    end if;

    perform pg_advisory_xact_lock(hashtext(p_source_guild_id || ':hublink:create'));

    insert into public.dank_community_hub_settings(guild_id)
    values (p_source_guild_id)
    on conflict (guild_id) do nothing;

    if exists (
        select 1
        from public.dank_community_hub_settings
        where guild_id=p_source_guild_id
          and (enabled=false or maintenance_mode=true)
    ) then
        raise exception 'Community Hub is not accepting HubLink connections';
    end if;

    select count(*) into v_recent
    from public.dank_community_hub_link_codes
    where source_guild_id=p_source_guild_id
      and created_at >= now() - interval '1 hour';

    if v_recent >= 10 then
        raise exception 'HubLink creation limit reached for this server';
    end if;

    update public.dank_community_hub_link_codes
       set state=case when expires_at <= now() then 'expired' else 'revoked' end,
           updated_at=now()
     where source_guild_id=p_source_guild_id
       and state='pending';

    update public.dank_community_hub_settings
       set partner_discovery_enabled=true,
           updated_by=p_actor_id,
           updated_at=now()
     where guild_id=p_source_guild_id;

    insert into public.dank_community_hub_link_codes(
        source_guild_id,
        created_by_user_id,
        code_hash,
        code_hint,
        state,
        expires_at
    ) values (
        p_source_guild_id,
        p_actor_id,
        p_code_hash,
        upper(btrim(p_code_hint)),
        'pending',
        now() + interval '15 minutes'
    )
    returning * into v_row;

    return jsonb_build_object(
        'id',v_row.id,
        'source_guild_id',v_row.source_guild_id,
        'created_by_user_id',v_row.created_by_user_id,
        'code_hint',v_row.code_hint,
        'state',v_row.state,
        'expires_at',v_row.expires_at,
        'created_at',v_row.created_at
    );
end;
$$;

create or replace function public.community_hub_redeem_link_code(
    p_code_hash text,
    p_target_guild_id text,
    p_actor_id text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_code public.dank_community_hub_link_codes%rowtype;
    v_link public.dank_community_partner_links%rowtype;
    v_a text;
    v_b text;
    v_replayed boolean := false;
begin
    if coalesce(p_code_hash,'') !~ '^[0-9a-f]{64}$' then
        raise exception 'HubLink code is invalid';
    end if;
    if btrim(coalesce(p_target_guild_id,'')) = ''
       or btrim(coalesce(p_actor_id,'')) = '' then
        raise exception 'HubLink target server and actor are required';
    end if;

    perform pg_advisory_xact_lock(hashtext('hublink:' || p_code_hash));

    select * into v_code
    from public.dank_community_hub_link_codes
    where code_hash=p_code_hash
    for update;

    if not found then
        raise exception 'HubLink code is invalid or no longer available';
    end if;

    if v_code.state='redeemed' then
        if v_code.redeemed_by_guild_id <> p_target_guild_id then
            raise exception 'HubLink code was already redeemed by another server';
        end if;
        v_replayed := true;
    elsif v_code.state <> 'pending' then
        raise exception 'HubLink code is no longer available';
    elsif v_code.expires_at <= now() then
        raise exception 'HubLink code has expired';
    end if;

    if v_code.source_guild_id = p_target_guild_id then
        raise exception 'A server cannot HubLink to itself';
    end if;

    if not exists (
        select 1
        from public.dank_community_hub_settings
        where guild_id=v_code.source_guild_id
          and enabled=true
          and maintenance_mode=false
          and partner_discovery_enabled=true
    ) then
        raise exception 'The source server is no longer accepting HubLink connections';
    end if;

    insert into public.dank_community_hub_settings(guild_id)
    values (p_target_guild_id)
    on conflict (guild_id) do nothing;

    if exists (
        select 1
        from public.dank_community_hub_settings
        where guild_id=p_target_guild_id
          and (enabled=false or maintenance_mode=true)
    ) then
        raise exception 'This server is not accepting HubLink connections';
    end if;

    update public.dank_community_hub_settings
       set partner_discovery_enabled=true,
           updated_by=p_actor_id,
           updated_at=now()
     where guild_id=p_target_guild_id;

    v_a := least(v_code.source_guild_id,p_target_guild_id);
    v_b := greatest(v_code.source_guild_id,p_target_guild_id);
    perform pg_advisory_xact_lock(hashtext(v_a || ':partner:' || v_b));

    select * into v_link
    from public.dank_community_partner_links
    where guild_a_id=v_a and guild_b_id=v_b
    for update;

    if v_replayed then
        if not found or v_link.state <> 'active' then
            raise exception 'HubLink code was already consumed and the partner link is no longer active';
        end if;
        -- A lost Discord response may replay the same successful transaction.
        -- Replay is idempotent only while the relationship it created remains
        -- active; an explicit later revoke permanently kills this code.
        null;
    elsif found and v_link.state='active' then
        -- Preserve any sharing choices already made on an established link.
        null;
    elsif found then
        update public.dank_community_partner_links
           set state='active',
               requested_by_guild_id=v_code.source_guild_id,
               requested_by_user_id=v_code.created_by_user_id,
               aggregate_activity_shared=false,
               session_discovery_shared=true,
               updated_at=now()
         where id=v_link.id
         returning * into v_link;
    else
        insert into public.dank_community_partner_links(
            guild_a_id,
            guild_b_id,
            state,
            requested_by_guild_id,
            requested_by_user_id,
            aggregate_activity_shared,
            session_discovery_shared
        ) values (
            v_a,
            v_b,
            'active',
            v_code.source_guild_id,
            v_code.created_by_user_id,
            false,
            true
        )
        returning * into v_link;
    end if;

    if not v_replayed then
        update public.dank_community_hub_link_codes
           set state='redeemed',
               redeemed_at=now(),
               redeemed_by_guild_id=p_target_guild_id,
               redeemed_by_user_id=p_actor_id,
               updated_at=now()
         where id=v_code.id;
    end if;

    return jsonb_build_object(
        'link',to_jsonb(v_link),
        'source_guild_id',v_code.source_guild_id,
        'target_guild_id',p_target_guild_id,
        'replayed',v_replayed
    );
end;
$$;

create or replace function public.community_hub_expire_link_codes(
    p_limit integer default 500
) returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    v_count integer := 0;
begin
    with expired as (
        select id
        from public.dank_community_hub_link_codes
        where state='pending'
          and expires_at <= now()
        order by expires_at
        limit greatest(1,least(coalesce(p_limit,500),5000))
        for update skip locked
    )
    update public.dank_community_hub_link_codes c
       set state='expired',
           updated_at=now()
      from expired e
     where c.id=e.id;

    get diagnostics v_count = row_count;
    return v_count;
end;
$$;

revoke all on function public.community_hub_create_link_code(text,text,text,text)
    from public, anon, authenticated;
revoke all on function public.community_hub_redeem_link_code(text,text,text)
    from public, anon, authenticated;
revoke all on function public.community_hub_expire_link_codes(integer)
    from public, anon, authenticated;

grant execute on function public.community_hub_create_link_code(text,text,text,text)
    to service_role;
grant execute on function public.community_hub_redeem_link_code(text,text,text)
    to service_role;
grant execute on function public.community_hub_expire_link_codes(integer)
    to service_role;
