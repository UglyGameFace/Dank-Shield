-- Embed Studio v2: durable, row-addressable private storage.
-- Apply to the new DankShield Supabase candidate only after staging CI.
-- No automatic cutover, deletion or rewrite of embed_studio_v1 in guild_configs.
create table if not exists public.dank_embed_studio_libraries (
    guild_id text primary key check (guild_id ~ '^[0-9]{1,20}$'),
    revision bigint not null default 0 check (revision >= 0),
    legacy_snapshot jsonb not null default '{}'::jsonb
        check (jsonb_typeof(legacy_snapshot) = 'object'),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create table if not exists public.dank_embed_studio_items (
    guild_id text not null references public.dank_embed_studio_libraries(guild_id) on delete restrict,
    section text not null check (section in ('drafts','templates','themes','media','published')),
    item_id text not null check (char_length(item_id) between 1 and 100),
    payload jsonb not null check (jsonb_typeof(payload) = 'object'
                                 and pg_column_size(payload) <= 65536),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (guild_id, section, item_id)
);
create index if not exists dank_studio_items_section_idx
    on public.dank_embed_studio_items(guild_id, section, updated_at desc);

alter table public.dank_embed_studio_libraries enable row level security;
alter table public.dank_embed_studio_items enable row level security;
revoke all on public.dank_embed_studio_libraries from public, anon, authenticated;
revoke all on public.dank_embed_studio_items from public, anon, authenticated;
grant select,insert,update on public.dank_embed_studio_libraries to service_role;
grant select,insert,update,delete on public.dank_embed_studio_items to service_role;

-- Caller sends the validated sections. This is a single atomic transaction:
-- the versioned guild lock protects the whole logical editor session, while
-- INSERT/UPDATE only changes individual records whose payloads actually differ.
-- No public RPC privileges; SECURITY INVOKER respects database grants/RLS.
create or replace function public.dank_embed_studio_write(
    p_guild_id text,
    p_expected_revision bigint,
    p_sections jsonb,
    p_legacy_snapshot jsonb default '{}'::jsonb
) returns jsonb
language plpgsql security invoker set search_path = public, pg_temp
as $$
declare
    current_rev bigint;
    part text;
    item record;
    count_items integer := 0;
begin
    if p_guild_id is null or p_guild_id !~ '^[0-9]{1,20}$'
        or p_expected_revision < -1
        or p_sections is null or jsonb_typeof(p_sections) <> 'object'
        or p_legacy_snapshot is null or jsonb_typeof(p_legacy_snapshot) <> 'object'
        or pg_column_size(p_sections) > 4194304 then
        raise exception 'Invalid Embed Studio v2 payload';
    end if;
    if (select count(*) from jsonb_object_keys(p_sections)) <> 5
        or not (p_sections ?& array['drafts','templates','themes','media','published']) then
        raise exception 'Embed Studio v2 requires exactly five known sections';
    end if;
    foreach part in array array['drafts','templates','themes','media','published'] loop
        if jsonb_typeof(p_sections->part) <> 'object' then
            raise exception 'Invalid Embed Studio section';
        end if;
    end loop;
    -- Locked read. Insert is permitted only for first-time expected revision -1.
    select revision into current_rev from public.dank_embed_studio_libraries
        where guild_id = p_guild_id for update;
    if not found then
        if p_expected_revision <> -1 then
            return jsonb_build_object('applied',false,'reason','missing');
        end if;
        insert into public.dank_embed_studio_libraries(guild_id, revision, legacy_snapshot)
            values(p_guild_id,0,p_legacy_snapshot)
            on conflict (guild_id) do nothing;
        select revision into current_rev from public.dank_embed_studio_libraries
            where guild_id = p_guild_id for update;
        if current_rev <> 0 then
            return jsonb_build_object('applied',false,'reason','concurrent_create');
        end if;
    elsif current_rev <> p_expected_revision then
        return jsonb_build_object('applied',false,'reason','stale_revision');
    elsif p_expected_revision = -1 then
        return jsonb_build_object('applied',false,'reason','already_exists');
    end if;

    foreach part in array array['drafts','templates','themes','media','published'] loop
        for item in select key,value from jsonb_each(p_sections->part) loop
            count_items := count_items + 1;
            if count_items > 200 or length(item.key) > 100 or length(item.key) < 1
                or jsonb_typeof(item.value) <> 'object'
                or pg_column_size(item.value) > 65536 then
                raise exception 'Embed Studio item exceeds record safety limits';
            end if;
            insert into public.dank_embed_studio_items(guild_id,section,item_id,payload)
                values (p_guild_id,part,item.key,item.value)
            on conflict (guild_id,section,item_id) do update
                set payload=excluded.payload, updated_at=now()
                where public.dank_embed_studio_items.payload is distinct from excluded.payload;
        end loop;
    end loop;
    delete from public.dank_embed_studio_items item
    where item.guild_id=p_guild_id and not exists (
        select 1 from jsonb_each(p_sections->item.section) entry
        where entry.key=item.item_id
    );
    update public.dank_embed_studio_libraries set revision=current_rev+1,updated_at=now()
        where guild_id=p_guild_id;
    return jsonb_build_object('applied',true,'revision',current_rev+1,'item_count',count_items);
end;
$$;
revoke all on function public.dank_embed_studio_write(text,bigint,jsonb,jsonb)
    from public, anon, authenticated;
grant execute on function public.dank_embed_studio_write(text,bigint,jsonb,jsonb)
    to service_role;
comment on table public.dank_embed_studio_items is
    'Service-role only. Dedicated, independently updated Embed Studio metadata records. Raw images are not stored here.';
