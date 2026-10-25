-- Private, service-role-only moderation review queue for Embed Studio.
-- Shared approval, not real-time co-editing or unattended publishing.
create table if not exists public.dank_embed_studio_reviews (
    id uuid primary key default gen_random_uuid(),
    guild_id text not null check (guild_id ~ '^[0-9]{1,20}$'),
    submitted_by text not null check (submitted_by ~ '^[0-9]{1,20}$'),
    channel_id text not null check (channel_id ~ '^[0-9]{1,20}$'),
    title text not null check (char_length(title) between 1 and 80),
    project jsonb not null check (
        jsonb_typeof(project)='object' and pg_column_size(project) <= 45000
    ),
    state text not null default 'pending'
        check (state in ('pending','approved','rejected','cancelled')),
    decided_by text,
    created_at timestamptz not null default now(),
    decided_at timestamptz,
    updated_at timestamptz not null default now()
);
create index if not exists dank_studio_reviews_queue_idx
    on public.dank_embed_studio_reviews(guild_id, state, created_at desc);
alter table public.dank_embed_studio_reviews enable row level security;
revoke all on public.dank_embed_studio_reviews from public,anon,authenticated;
grant select,insert,update on public.dank_embed_studio_reviews to service_role;

create or replace function public.dank_embed_studio_review_decide(
    p_id uuid, p_guild_id text, p_actor text, p_action text
) returns setof public.dank_embed_studio_reviews
language plpgsql security invoker set search_path=public,pg_temp
as $$
begin
    if p_id is null or p_guild_id !~ '^[0-9]{1,20}$'
        or p_actor !~ '^[0-9]{1,20}$'
        or p_action not in ('approved','rejected') then
        raise exception 'Invalid Studio review decision';
    end if;
    return query
    update public.dank_embed_studio_reviews r
       set state=p_action,decided_by=p_actor,decided_at=now(),updated_at=now()
     where r.id=p_id and r.guild_id=p_guild_id and r.state='pending'
       and r.submitted_by <> p_actor
    returning r.*;
end;
$$;
revoke all on function public.dank_embed_studio_review_decide(uuid,text,text,text)
    from public,anon,authenticated;
grant execute on function public.dank_embed_studio_review_decide(uuid,text,text,text)
    to service_role;
comment on table public.dank_embed_studio_reviews is
    'Moderator-to-moderator design handoff. Approval never publishes content.';
