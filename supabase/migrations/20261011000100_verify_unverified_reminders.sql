-- Atomic, bounded Simple Verify reminder claims. Deploy before enabling the feature.
-- Service-role only: no public/member read or write to verification reminder history.
create table if not exists public.dank_verify_reminder_delivery (
    guild_id text not null,
    user_id text not null,
    joined_at timestamptz not null,
    last_claimed_at timestamptz not null default now(),
    attempts integer not null default 1 check (attempts between 1 and 5),
    primary key (guild_id, user_id)
);

alter table public.dank_verify_reminder_delivery enable row level security;
revoke all on public.dank_verify_reminder_delivery from anon, authenticated;
grant select, insert, update, delete on public.dank_verify_reminder_delivery to service_role;

create or replace function public.claim_dank_verify_reminder(
    p_guild_id text,
    p_user_id text,
    p_joined_at timestamptz,
    p_repeat_hours integer,
    p_max_reminders integer
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
    did_claim boolean := false;
begin
    if p_guild_id !~ '^[0-9]{1,20}$'
       or p_user_id !~ '^[0-9]{1,20}$'
       or p_joined_at is null
       or p_repeat_hours not between 12 and 720
       or p_max_reminders not between 1 and 5
    then
        return false;
    end if;

    insert into public.dank_verify_reminder_delivery
        (guild_id, user_id, joined_at, last_claimed_at, attempts)
    values (p_guild_id, p_user_id, p_joined_at, now(), 1)
    on conflict (guild_id, user_id) do update
       set joined_at = excluded.joined_at,
           last_claimed_at = now(),
           attempts = case
               when public.dank_verify_reminder_delivery.joined_at is distinct from excluded.joined_at
               then 1
               else public.dank_verify_reminder_delivery.attempts + 1
           end
     where public.dank_verify_reminder_delivery.joined_at is distinct from excluded.joined_at
        or (
            public.dank_verify_reminder_delivery.attempts < p_max_reminders
            and public.dank_verify_reminder_delivery.last_claimed_at <=
                now() - make_interval(hours => p_repeat_hours)
        )
    returning true into did_claim;

    return coalesce(did_claim, false);
end;
$$;

revoke all on function public.claim_dank_verify_reminder(text,text,timestamptz,integer,integer) from public, anon, authenticated;
grant execute on function public.claim_dank_verify_reminder(text,text,timestamptz,integer,integer) to service_role;
