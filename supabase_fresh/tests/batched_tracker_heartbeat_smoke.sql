-- New dedicated offline DankShield project only. All mutations ROLLBACK.
begin;
do $smoke$
declare
    gids text[];
    t0 timestamptz := now() - interval '10 minutes';
    affected integer;
    saved record;
begin
    gids := array(select gs::text from generate_series(
        990000000000000000::bigint, 990000000000000249::bigint
    ) as generator(gs));
    if exists(select 1 from public.member_activity_tracker_state where guild_id=any(gids)) then
        raise exception 'Synthetic IDs overlap real data';
    end if;
    affected := public.heartbeat_member_activity_trackers(gids,'process-A',t0,180);
    if affected<>250 then raise exception 'first batch affected % instead of 250',affected;end if;
    affected := public.heartbeat_member_activity_trackers(gids,'process-A',t0+interval '30 seconds',180);
    select process_id,continuous_since into saved from public.member_activity_tracker_state where guild_id=gids[1];
    if affected<>250 or saved.process_id<>'process-A' or saved.continuous_since<>t0 then
        raise exception 'uninterrupted coverage was not preserved';
    end if;
    affected := public.heartbeat_member_activity_trackers(gids,'process-B',t0+interval '60 seconds',180);
    select process_id,continuous_since into saved from public.member_activity_tracker_state where guild_id=gids[1];
    if affected<>250 or saved.process_id<>'process-B' or saved.continuous_since<>t0+interval '60 seconds' then
        raise exception 'handoff did not reset continuous coverage';
    end if;
    affected := public.heartbeat_member_activity_trackers(gids,'process-A',t0+interval '45 seconds',180);
    select process_id,last_heartbeat_at into saved from public.member_activity_tracker_state where guild_id=gids[1];
    if affected<>0 or saved.process_id<>'process-B' or saved.last_heartbeat_at<>t0+interval '60 seconds' then
        raise exception 'late heartbeat overwrote newer handoff';
    end if;
    affected := public.heartbeat_member_activity_trackers(gids,'process-B',t0+interval '5 minutes',180);
    select continuous_since into saved from public.member_activity_tracker_state where guild_id=gids[1];
    if affected<>250 or saved.continuous_since<>t0+interval '5 minutes' then
        raise exception 'long gap did not reset continuous coverage';
    end if;
    -- Small-server workloads must work without padding or full-sized batches.
    affected := public.heartbeat_member_activity_trackers(gids[1:1],'process-B',t0+interval '5 minutes 30 seconds',180);
    if affected<>1 then raise exception 'one-guild batch failed';end if;
    affected := public.heartbeat_member_activity_trackers(gids[2:6],'process-B',t0+interval '5 minutes 30 seconds',180);
    if affected<>5 then raise exception 'five-guild batch failed';end if;
    affected := public.heartbeat_member_activity_trackers(gids[7:36],'process-B',t0+interval '5 minutes 30 seconds',180);
    if affected<>30 then raise exception 'thirty-guild batch failed';end if;
    begin
        perform public.heartbeat_member_activity_trackers(array[gids[1],gids[1]],'p',now(),180);
        raise exception 'TEST duplicate accepted';
    exception when raise_exception then
        if sqlerrm='TEST duplicate accepted' then raise;end if;
    end;
    begin
        perform public.heartbeat_member_activity_trackers(
           array(select generator.gs::text from generate_series(1,251) as generator(gs)),
           'p',now(),180);
        raise exception 'TEST oversized batch accepted';
    exception when raise_exception then
        if sqlerrm='TEST oversized batch accepted' then raise;end if;
    end;
    begin
        perform public.heartbeat_member_activity_trackers(array['invalid-id'],'p',now(),180);
        raise exception 'TEST malformed ID accepted';
    exception when raise_exception then
        if sqlerrm='TEST malformed ID accepted' then raise;end if;
    end;
    if has_function_privilege('anon','public.heartbeat_member_activity_trackers(text[],text,timestamptz,integer)','EXECUTE')
       or has_function_privilege('authenticated','public.heartbeat_member_activity_trackers(text[],text,timestamptz,integer)','EXECUTE')
       or not has_function_privilege('service_role','public.heartbeat_member_activity_trackers(text[],text,timestamptz,integer)','EXECUTE') then
        raise exception 'Function privileges not restricted to backend service';
    end if;
end $smoke$;
rollback;
