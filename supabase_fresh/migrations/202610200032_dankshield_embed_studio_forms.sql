-- Private staff-readable responses to bot-owned Embed Studio form buttons.
-- No anonymous database access; only configured Discord bot may write.
create table if not exists public.dank_embed_studio_form_responses (
    id uuid primary key default gen_random_uuid(),
    guild_id text not null check (guild_id ~ '^[0-9]{1,20}$'),
    channel_id text not null check (channel_id ~ '^[0-9]{1,20}$'),
    message_id text not null check (message_id ~ '^[0-9]{1,20}$'),
    user_id text not null check (user_id ~ '^[0-9]{1,20}$'),
    window_slot bigint not null check (window_slot > 0),
    answers jsonb not null check (
        jsonb_typeof(answers)='array'
        and jsonb_array_length(answers) between 1 and 5
        and pg_column_size(answers) <= 8192
    ),
    created_at timestamptz not null default now(),
    unique(guild_id,message_id,user_id,window_slot)
);
create index if not exists dank_studio_form_inbox_idx
    on public.dank_embed_studio_form_responses(guild_id,created_at desc);
alter table public.dank_embed_studio_form_responses enable row level security;
revoke all on public.dank_embed_studio_form_responses from public,anon,authenticated;
grant select,insert,delete on public.dank_embed_studio_form_responses to service_role;

-- Bounded, explicit retention (operator cron/manual), never a full-table purge.
create or replace function public.dank_embed_studio_purge_form_responses(
    p_limit integer default 100
) returns integer
language plpgsql security invoker set search_path=public,pg_temp
as $$
declare
    deleted_count integer;
begin
    if p_limit < 1 or p_limit > 200 then
        raise exception 'Invalid form retention batch';
    end if;
    with old as (
        select id from public.dank_embed_studio_form_responses
        where created_at < now()-interval '30 days'
        order by created_at,id for update skip locked limit p_limit
    )
    delete from public.dank_embed_studio_form_responses r
    using old where r.id=old.id;
    get diagnostics deleted_count=row_count;
    return deleted_count;
end;
$$;
revoke all on function public.dank_embed_studio_purge_form_responses(integer)
    from public,anon,authenticated;
grant execute on function public.dank_embed_studio_purge_form_responses(integer)
    to service_role;
comment on table public.dank_embed_studio_form_responses is
    'Private user-submitted Studio form answers. Service-role only; purge older than 30 days via bounded maintenance.';
