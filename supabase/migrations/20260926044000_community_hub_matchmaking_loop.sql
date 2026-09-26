-- Community Hub matchmaking completion.
-- Adds explicit auto-match consent to Open to Play and an idempotent Quick Match
-- path that can form a new session when no existing public group is available.

alter table public.dank_community_availability
    add column if not exists auto_match boolean not null default false;

create index if not exists idx_dank_community_availability_auto_match
    on public.dank_community_availability (guild_id, game_key, updated_at)
    where auto_match = true;

create or replace function public.community_hub_quick_match(
    p_guild_id text,
    p_user_id text,
    p_game_name text,
    p_idempotency_key text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_session_id uuid;
    v_result jsonb;
    v_settings public.dank_community_hub_settings%rowtype;
    v_candidate public.dank_community_availability%rowtype;
    v_candidate_active integer;
    v_existing_match_user_id text;
    v_session jsonb;
    v_capacity integer;
begin
    if btrim(coalesce(p_guild_id,'')) = '' or btrim(coalesce(p_user_id,'')) = '' then
        raise exception 'guild and user are required';
    end if;
    if char_length(btrim(coalesce(p_game_name,''))) < 1 or char_length(btrim(p_game_name)) > 80 then
        raise exception 'game name must be between 1 and 80 characters';
    end if;
    if btrim(coalesce(p_idempotency_key,'')) = '' then
        raise exception 'quick match idempotency key is required';
    end if;

    -- Recover a previously created Quick Match session before evaluating any
    -- current availability or quota state. This makes a lost Discord response
    -- safe to replay with the same interaction-derived key.
    select s.id, to_jsonb(s)
      into v_session_id, v_session
    from public.dank_community_sessions s
    where s.guild_id=p_guild_id
      and s.idempotency_key=p_idempotency_key;

    if v_session_id is not null then
        if coalesce(v_session->>'host_id','') <> p_user_id then
            raise exception 'idempotency key belongs to another session owner';
        end if;
        select m.user_id
          into v_existing_match_user_id
        from public.dank_community_session_members m
        where m.session_id=v_session_id
          and m.user_id <> p_user_id
          and m.left_at is null
          and m.role <> 'waitlist'
        order by m.joined_at
        limit 1;
        return jsonb_build_object(
            'session',v_session,
            'role','host',
            'created_match',true,
            'match_user_id',v_existing_match_user_id,
            'replayed',true
        );
    end if;

    -- Existing public groups remain the preferred path.
    select s.id
      into v_session_id
    from public.dank_community_sessions s
    where s.guild_id=p_guild_id
      and lower(btrim(s.game_name))=lower(btrim(p_game_name))
      and s.privacy='public'
      and s.state in ('open','forming','ready','active')
      and not exists (
          select 1
          from public.dank_community_session_members own
          where own.session_id=s.id
            and own.user_id=p_user_id
            and own.left_at is null
      )
      and not exists (
          select 1
          from public.dank_community_session_members m
          join public.dank_community_user_blocks b
            on b.guild_id=p_guild_id
           and (
                (b.blocker_user_id=p_user_id and b.blocked_user_id=m.user_id)
                or
                (b.blocked_user_id=p_user_id and b.blocker_user_id=m.user_id)
           )
          where m.session_id=s.id
            and m.left_at is null
            and m.role <> 'waitlist'
      )
      and (
          select count(*)
          from public.dank_community_session_members active_member
          where active_member.session_id=s.id
            and active_member.left_at is null
            and active_member.role <> 'waitlist'
      ) < s.capacity
    order by (
        select count(*)
        from public.dank_community_session_members active_member
        where active_member.session_id=s.id
          and active_member.left_at is null
          and active_member.role <> 'waitlist'
    ) desc,
    s.last_activity_at desc
    limit 1
    for update of s skip locked;

    if v_session_id is not null then
        v_result := public.community_hub_join_session(v_session_id,p_guild_id,p_user_id);
        return v_result || jsonb_build_object('created_match',false);
    end if;

    -- Session creation already serializes on this guild. Acquire the same lock
    -- before selecting an opted-in availability row so two Quick Match requests
    -- cannot claim the same person and create competing groups.
    perform pg_advisory_xact_lock(hashtext(p_guild_id));

    -- Another creator may have published a matching group while this request
    -- waited for the guild creation lock. Prefer that group instead.
    select s.id
      into v_session_id
    from public.dank_community_sessions s
    where s.guild_id=p_guild_id
      and lower(btrim(s.game_name))=lower(btrim(p_game_name))
      and s.privacy='public'
      and s.state in ('open','forming','ready','active')
      and not exists (
          select 1
          from public.dank_community_session_members own
          where own.session_id=s.id
            and own.user_id=p_user_id
            and own.left_at is null
      )
      and not exists (
          select 1
          from public.dank_community_session_members m
          join public.dank_community_user_blocks b
            on b.guild_id=p_guild_id
           and (
                (b.blocker_user_id=p_user_id and b.blocked_user_id=m.user_id)
                or
                (b.blocked_user_id=p_user_id and b.blocker_user_id=m.user_id)
           )
          where m.session_id=s.id
            and m.left_at is null
            and m.role <> 'waitlist'
      )
      and (
          select count(*)
          from public.dank_community_session_members active_member
          where active_member.session_id=s.id
            and active_member.left_at is null
            and active_member.role <> 'waitlist'
      ) < s.capacity
    order by (
        select count(*)
        from public.dank_community_session_members active_member
        where active_member.session_id=s.id
          and active_member.left_at is null
          and active_member.role <> 'waitlist'
    ) desc,
    s.last_activity_at desc
    limit 1
    for update of s;

    if v_session_id is not null then
        v_result := public.community_hub_join_session(v_session_id,p_guild_id,p_user_id);
        return v_result || jsonb_build_object('created_match',false);
    end if;

    insert into public.dank_community_hub_settings(guild_id)
    values (p_guild_id)
    on conflict (guild_id) do nothing;

    select * into v_settings
    from public.dank_community_hub_settings
    where guild_id=p_guild_id
    for update;

    if not found or not v_settings.enabled or v_settings.maintenance_mode then
        raise exception 'community hub is not accepting quick matches';
    end if;

    select a.*
      into v_candidate
    from public.dank_community_availability a
    where a.guild_id=p_guild_id
      and lower(btrim(a.game_name))=lower(btrim(p_game_name))
      and a.user_id <> p_user_id
      and a.auto_match=true
      and a.expires_at > now()
      and not exists (
          select 1
          from public.dank_community_user_blocks b
          where b.guild_id=p_guild_id
            and (
                (b.blocker_user_id=p_user_id and b.blocked_user_id=a.user_id)
                or
                (b.blocked_user_id=p_user_id and b.blocker_user_id=a.user_id)
            )
      )
      and not exists (
          select 1
          from public.dank_community_session_members m
          join public.dank_community_sessions s on s.id=m.session_id
          where m.guild_id=p_guild_id
            and m.user_id=a.user_id
            and m.left_at is null
            and m.role <> 'waitlist'
            and lower(btrim(s.game_name))=lower(btrim(p_game_name))
            and s.state in ('creating','open','forming','ready','active','paused','ending','interrupted','recovering')
      )
    order by a.updated_at, a.user_id
    limit 1
    for update of a skip locked;

    if not found then
        return null;
    end if;

    -- Claim auto-match eligibility before creating the session. The availability
    -- row remains discoverable until Python confirms Discord provisioning, which
    -- allows safe restoration if publishing fails.
    update public.dank_community_availability
       set auto_match=false,
           updated_at=now()
     where guild_id=v_candidate.guild_id
       and user_id=v_candidate.user_id
       and game_key=v_candidate.game_key;

    v_capacity := greatest(2, least(6, v_settings.max_session_capacity));

    v_session := public.community_hub_create_session(
        p_guild_id,
        p_user_id,
        p_idempotency_key,
        p_game_name,
        'Created by Quick Match.',
        v_capacity,
        v_candidate.mic_preference,
        v_candidate.play_style,
        'public'
    );
    v_session_id := (v_session->>'id')::uuid;

    -- Recheck the candidate at the final mutation boundary. If they filled
    -- their active-session quota concurrently, abort the whole transaction so
    -- neither the session nor the auto-match claim is committed.
    select count(*) into v_candidate_active
    from public.dank_community_session_members m
    join public.dank_community_sessions s on s.id=m.session_id
    where m.guild_id=p_guild_id
      and m.user_id=v_candidate.user_id
      and m.left_at is null
      and m.role <> 'waitlist'
      and lower(btrim(s.game_name))=lower(btrim(p_game_name))
      and s.id <> v_session_id
      and s.state in ('creating','open','forming','ready','active','paused','ending','interrupted','recovering');

    if v_candidate_active > 0 then
        raise exception 'quick match candidate became unavailable';
    end if;

    if exists (
        select 1
        from public.dank_community_user_blocks b
        where b.guild_id=p_guild_id
          and (
              (b.blocker_user_id=p_user_id and b.blocked_user_id=v_candidate.user_id)
              or
              (b.blocked_user_id=p_user_id and b.blocker_user_id=v_candidate.user_id)
          )
    ) then
        raise exception 'match safety exclusion prevents this match';
    end if;

    insert into public.dank_community_session_members(
        session_id,guild_id,user_id,role,ready,ready_at,left_at,last_seen_at
    ) values (
        v_session_id,p_guild_id,v_candidate.user_id,'member',false,null,null,now()
    )
    on conflict (session_id,user_id) do update
       set role='member',
           ready=false,
           ready_at=null,
           left_at=null,
           last_seen_at=now();

    insert into public.dank_community_session_events(
        guild_id,session_id,event_type,actor_id,subject_user_id,metadata
    ) values (
        p_guild_id,
        v_session_id,
        'session.quick_match_formed',
        p_user_id,
        v_candidate.user_id,
        jsonb_build_object(
            'game_key',v_candidate.game_key,
            'play_style',v_candidate.play_style,
            'mic_preference',v_candidate.mic_preference
        )
    );

    return jsonb_build_object(
        'session',v_session,
        'role','host',
        'created_match',true,
        'match_user_id',v_candidate.user_id,
        'match_availability',jsonb_build_object(
            'game_name',v_candidate.game_name,
            'play_style',v_candidate.play_style,
            'mic_preference',v_candidate.mic_preference,
            'note',v_candidate.note,
            'expires_at',v_candidate.expires_at
        )
    );
end;
$$;

create or replace function public.community_hub_normalize_formation(
    p_session_id uuid,
    p_guild_id text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_session public.dank_community_sessions%rowtype;
    v_member_count integer;
begin
    select * into v_session
    from public.dank_community_sessions
    where id=p_session_id and guild_id=p_guild_id
    for update;

    if not found then
        raise exception 'session not found';
    end if;

    if v_session.state <> 'open' then
        return to_jsonb(v_session);
    end if;

    select count(*) into v_member_count
    from public.dank_community_session_members
    where session_id=p_session_id
      and left_at is null
      and role <> 'waitlist';

    if v_member_count < 2 then
        return to_jsonb(v_session);
    end if;

    update public.dank_community_sessions
       set state='forming',
           last_activity_at=now(),
           updated_at=now(),
           version=version+1
     where id=p_session_id
     returning * into v_session;

    insert into public.dank_community_session_events(
        guild_id,session_id,event_type,metadata
    ) values (
        p_guild_id,p_session_id,'session.forming',
        jsonb_build_object('source','member_count_normalization','active_members',v_member_count)
    );

    return to_jsonb(v_session);
end;
$$;

revoke all on function public.community_hub_quick_match(text,text,text,text)
    from public, anon, authenticated;
revoke all on function public.community_hub_normalize_formation(uuid,text)
    from public, anon, authenticated;

grant execute on function public.community_hub_quick_match(text,text,text,text) to service_role;
grant execute on function public.community_hub_normalize_formation(uuid,text) to service_role;
