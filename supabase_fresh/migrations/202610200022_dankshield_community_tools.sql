-- Dedicated offline Dank Shield candidate ONLY. Source schema and RPCs
-- come from the four canonical migrations listed below (2026-10-20).
-- Applies only on new viamriafkbqhquvrsnua; never on old shared database.
-- Does NOT backfill sticky posts, change Discord messages or switch Discloud.
-- CANONICAL SOURCE: supabase/migrations/20260811122504_community_tools.sql
-- DS-STICKY-026: persistent StickyBot-style Community Tools.
-- Service-role only: Discord users never access these tables directly.

create table if not exists public.dank_stickies (
    channel_id bigint primary key,
    guild_id bigint not null,
    enabled boolean not null default true,
    content text not null default '',
    mode text not null default 'plain'
        check (mode in ('plain', 'embed', 'poll')),
    title text,
    color integer not null default 5793266
        check (color between 0 and 16777215),
    image_url text,
    thumbnail_url text,
    interval_seconds integer not null default 15
        check (interval_seconds between 15 and 3600),
    message_threshold integer not null default 5
        check (message_threshold between 1 and 100),
    use_webhook boolean not null default false,
    sender_name text,
    sender_avatar_url text,
    last_message_id bigint,
    last_sent_at timestamptz,
    updated_by bigint,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (char_length(content) <= 1900),
    check (title is null or char_length(title) <= 256),
    check (sender_name is null or char_length(sender_name) <= 80),
    check (mode <> 'poll' or use_webhook = false)
);

create index if not exists dank_stickies_guild_id_idx
    on public.dank_stickies (guild_id);

create table if not exists public.dank_sticky_polls (
    channel_id bigint primary key
        references public.dank_stickies(channel_id) on delete cascade,
    guild_id bigint not null,
    question text not null
        check (char_length(question) between 1 and 300),
    options jsonb not null default '[]'::jsonb,
    votes jsonb not null default '{}'::jsonb,
    state text not null default 'active'
        check (state in ('active', 'paused', 'ended')),
    updated_by bigint,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (jsonb_typeof(options) = 'array'),
    check (jsonb_array_length(options) between 2 and 7),
    check (jsonb_typeof(votes) = 'object')
);

create index if not exists dank_sticky_polls_guild_id_idx
    on public.dank_sticky_polls (guild_id);

alter table public.dank_stickies enable row level security;
alter table public.dank_sticky_polls enable row level security;

revoke all on table public.dank_stickies from anon, authenticated;
revoke all on table public.dank_sticky_polls from anon, authenticated;

grant all on table public.dank_stickies to service_role;
grant all on table public.dank_sticky_polls to service_role;

comment on table public.dank_stickies is
    'Dank Shield persistent channel sticky configuration; raw webhook URLs/tokens are intentionally not stored.';
comment on table public.dank_sticky_polls is
    'Dank Shield sticky-poll state and one-choice-per-user vote map.';


-- CANONICAL SOURCE: supabase/migrations/20260812224000_smart_sticky_quiet_notices.sql
-- DS-STICKY-029: server-wide quiet-activity notices for Community Tools.
-- Service-role only: Discord users never access this table directly.

create table if not exists public.dank_quiet_notices (
    guild_id bigint primary key,
    channel_id bigint not null,
    enabled boolean not null default true,
    content text not null,
    inactivity_seconds integer not null default 7200
        check (inactivity_seconds between 300 and 604800),
    partner_name text,
    partner_url text,
    auto_clear boolean not null default true,
    last_activity_at timestamptz,
    last_notice_message_id bigint,
    last_notice_sent_at timestamptz,
    updated_by bigint,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (char_length(content) between 1 and 1800),
    check (partner_name is null or char_length(partner_name) <= 100),
    check (partner_url is null or char_length(partner_url) <= 1000)
);

create index if not exists dank_quiet_notices_channel_id_idx
    on public.dank_quiet_notices (channel_id);

alter table public.dank_quiet_notices enable row level security;

revoke all on table public.dank_quiet_notices from anon, authenticated;
grant all on table public.dank_quiet_notices to service_role;

comment on table public.dank_quiet_notices is
    'Dank Shield server-wide inactivity notices; one configurable quiet notice per guild.';


-- CANONICAL SOURCE: supabase/migrations/20260905121500_community_tools_hardening.sql
-- DS-COMMUNITY-031: durable atomic Community Tools state transitions.
-- Service-role only. Discord users never call this function directly.

create or replace function public.save_dank_sticky_bundle(
    p_sticky jsonb,
    p_poll jsonb default null
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_sticky public.dank_stickies%rowtype;
    v_poll public.dank_sticky_polls%rowtype;
    v_poll_json jsonb := null;
    v_channel_id bigint;
    v_guild_id bigint;
    v_mode text;
begin
    if p_sticky is null or jsonb_typeof(p_sticky) <> 'object' then
        raise exception 'p_sticky must be a JSON object';
    end if;

    v_channel_id := (p_sticky->>'channel_id')::bigint;
    v_guild_id := (p_sticky->>'guild_id')::bigint;
    v_mode := lower(coalesce(p_sticky->>'mode', 'plain'));

    if v_channel_id <= 0 or v_guild_id <= 0 then
        raise exception 'sticky guild/channel ids must be positive';
    end if;

    if v_mode not in ('plain', 'embed', 'poll') then
        raise exception 'unsupported sticky mode %', v_mode;
    end if;

    if v_mode = 'poll' then
        if p_poll is null or jsonb_typeof(p_poll) <> 'object' then
            raise exception 'poll mode requires p_poll';
        end if;
        if (p_poll->>'channel_id')::bigint <> v_channel_id
           or (p_poll->>'guild_id')::bigint <> v_guild_id then
            raise exception 'sticky/poll guild or channel mismatch';
        end if;
    elsif p_poll is not null then
        raise exception 'p_poll is only valid for poll mode';
    end if;

    insert into public.dank_stickies (
        guild_id,
        channel_id,
        enabled,
        content,
        mode,
        title,
        color,
        image_url,
        thumbnail_url,
        interval_seconds,
        message_threshold,
        use_webhook,
        sender_name,
        sender_avatar_url,
        last_message_id,
        last_sent_at,
        updated_by,
        updated_at
    ) values (
        v_guild_id,
        v_channel_id,
        coalesce((p_sticky->>'enabled')::boolean, true),
        coalesce(p_sticky->>'content', ''),
        v_mode,
        nullif(p_sticky->>'title', ''),
        coalesce((p_sticky->>'color')::integer, 5793266),
        nullif(p_sticky->>'image_url', ''),
        nullif(p_sticky->>'thumbnail_url', ''),
        coalesce((p_sticky->>'interval_seconds')::integer, 15),
        coalesce((p_sticky->>'message_threshold')::integer, 5),
        coalesce((p_sticky->>'use_webhook')::boolean, false),
        nullif(p_sticky->>'sender_name', ''),
        nullif(p_sticky->>'sender_avatar_url', ''),
        nullif(p_sticky->>'last_message_id', '')::bigint,
        nullif(p_sticky->>'last_sent_at', '')::timestamptz,
        nullif(p_sticky->>'updated_by', '')::bigint,
        coalesce(nullif(p_sticky->>'updated_at', '')::timestamptz, now())
    )
    on conflict (channel_id) do update set
        guild_id = excluded.guild_id,
        enabled = excluded.enabled,
        content = excluded.content,
        mode = excluded.mode,
        title = excluded.title,
        color = excluded.color,
        image_url = excluded.image_url,
        thumbnail_url = excluded.thumbnail_url,
        interval_seconds = excluded.interval_seconds,
        message_threshold = excluded.message_threshold,
        use_webhook = excluded.use_webhook,
        sender_name = excluded.sender_name,
        sender_avatar_url = excluded.sender_avatar_url,
        last_message_id = excluded.last_message_id,
        last_sent_at = excluded.last_sent_at,
        updated_by = excluded.updated_by,
        updated_at = excluded.updated_at
    returning * into v_sticky;

    if v_mode = 'poll' then
        insert into public.dank_sticky_polls (
            guild_id,
            channel_id,
            question,
            options,
            votes,
            state,
            updated_by,
            updated_at
        ) values (
            v_guild_id,
            v_channel_id,
            p_poll->>'question',
            coalesce(p_poll->'options', '[]'::jsonb),
            coalesce(p_poll->'votes', '{}'::jsonb),
            coalesce(p_poll->>'state', 'active'),
            nullif(p_poll->>'updated_by', '')::bigint,
            coalesce(nullif(p_poll->>'updated_at', '')::timestamptz, now())
        )
        on conflict (channel_id) do update set
            guild_id = excluded.guild_id,
            question = excluded.question,
            options = excluded.options,
            votes = excluded.votes,
            state = excluded.state,
            updated_by = excluded.updated_by,
            updated_at = excluded.updated_at
        returning * into v_poll;

        v_poll_json := to_jsonb(v_poll);
    else
        delete from public.dank_sticky_polls where channel_id = v_channel_id;
    end if;

    return jsonb_build_object(
        'sticky', to_jsonb(v_sticky),
        'poll', v_poll_json
    );
end;
$$;

revoke all on function public.save_dank_sticky_bundle(jsonb, jsonb) from public;
revoke all on function public.save_dank_sticky_bundle(jsonb, jsonb) from anon, authenticated;
grant execute on function public.save_dank_sticky_bundle(jsonb, jsonb) to service_role;

comment on function public.save_dank_sticky_bundle(jsonb, jsonb) is
    'Atomically saves a Dank Shield sticky and optional sticky-poll state, deleting stale poll state when leaving poll mode.';


-- CANONICAL SOURCE: supabase/migrations/20260921042000_quiet_notice_atomic_delivery_clear.sql
-- Quiet Server Notice: atomic runtime transitions for activity and delivery state.
-- Activity writes preserve unrelated state; delivery clears require exact message identity.
-- Service-role only. Discord users never call these functions directly.

-- Record activity without read-modify-writing the rest of the notice row. This
-- prevents an overlapping worker from resurrecting stale delivery/config state.
create or replace function public.record_dank_quiet_notice_activity(
    p_guild_id bigint,
    p_activity_at timestamptz
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_notice public.dank_quiet_notices%rowtype;
    v_activity_at timestamptz := coalesce(p_activity_at, now());
begin
    if p_guild_id is null or p_guild_id <= 0 then
        raise exception 'quiet notice guild id must be positive';
    end if;

    update public.dank_quiet_notices
       set last_activity_at = case
               when last_activity_at is null or last_activity_at < v_activity_at then v_activity_at
               else last_activity_at
           end,
           updated_at = now()
     where guild_id = p_guild_id
     returning * into v_notice;

    if not found then
        return null;
    end if;

    return to_jsonb(v_notice);
end;
$$;

revoke all on function public.record_dank_quiet_notice_activity(bigint, timestamptz) from public;
revoke all on function public.record_dank_quiet_notice_activity(bigint, timestamptz) from anon, authenticated;
grant execute on function public.record_dank_quiet_notice_activity(bigint, timestamptz) to service_role;

comment on function public.record_dank_quiet_notice_activity(bigint, timestamptz) is
    'Atomically records monotonic quiet-notice activity without overwriting unrelated delivery or admin configuration state.';

create or replace function public.clear_dank_quiet_notice_delivery(
    p_guild_id bigint,
    p_expected_message_id bigint default null
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_notice public.dank_quiet_notices%rowtype;
begin
    if p_guild_id is null or p_guild_id <= 0 then
        raise exception 'quiet notice guild id must be positive';
    end if;
    if p_expected_message_id is not null and p_expected_message_id <= 0 then
        raise exception 'expected quiet notice message id must be positive';
    end if;

    select *
      into v_notice
      from public.dank_quiet_notices
     where guild_id = p_guild_id
     for update;

    if not found then
        return null;
    end if;

    if p_expected_message_id is not null
       and v_notice.last_notice_message_id is distinct from p_expected_message_id then
        return to_jsonb(v_notice);
    end if;

    update public.dank_quiet_notices
       set last_notice_message_id = null,
           last_notice_sent_at = null,
           updated_at = now()
     where guild_id = p_guild_id
     returning * into v_notice;

    return to_jsonb(v_notice);
end;
$$;

revoke all on function public.clear_dank_quiet_notice_delivery(bigint, bigint) from public;
revoke all on function public.clear_dank_quiet_notice_delivery(bigint, bigint) from anon, authenticated;
grant execute on function public.clear_dank_quiet_notice_delivery(bigint, bigint) to service_role;

comment on function public.clear_dank_quiet_notice_delivery(bigint, bigint) is
    'Atomically clears a quiet-notice delivery only when the tracked message id still matches the caller expectation.';

-- Enforce private backend-only access in the offline candidate.
-- Grant only the four runtime CRUD privileges, not TRUNCATE or REFERENCES.
revoke all on table public.dank_stickies, public.dank_sticky_polls,
  public.dank_quiet_notices from public,anon,authenticated,service_role;
grant select,insert,update,delete on table public.dank_stickies,
  public.dank_sticky_polls, public.dank_quiet_notices to service_role;
