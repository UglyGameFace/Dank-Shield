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
