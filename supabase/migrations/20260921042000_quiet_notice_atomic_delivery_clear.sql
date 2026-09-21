-- Quiet Server Notice: atomic runtime transitions for activity and delivery state.
-- database still points at the message the caller deleted.
-- Service-role only. Discord users never call this function directly.

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
as $
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
$;

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
