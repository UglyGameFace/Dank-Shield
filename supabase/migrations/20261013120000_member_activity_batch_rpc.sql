-- Dank Shield member-activity batch write RPC.
--
-- The bot deduplicates per-member activity in memory and flushes periodically.
-- This function persists one flush window in a single round-trip instead of one
-- RPC per event. The upsert body mirrors public.record_member_activity exactly
-- (greatest()-based), plus p_event_count so batched windows keep exact counts.

-- Receipt and ledger updates are atomic. Replaying a stable batch ID after a
-- lost HTTP acknowledgement must not duplicate event_count.
create table if not exists public.member_activity_batch_receipts (
    batch_id uuid primary key,
    applied_at timestamptz not null default now()
);
alter table public.member_activity_batch_receipts enable row level security;
revoke all on table public.member_activity_batch_receipts
    from public, anon, authenticated;
grant select, insert on table public.member_activity_batch_receipts to service_role;

drop function if exists public.record_member_activity_batch(jsonb);
create or replace function public.record_member_activity_batch(
    p_batch_id uuid, p_events jsonb
)
returns void
language plpgsql
security definer
set search_path = pg_catalog, public
as $
declare
    e jsonb;
    v_guild_id text;
    v_user_id text;
    v_activity_type text;
    v_occurred_at timestamptz;
    v_channel_id text;
    v_process_id text;
    v_event_count bigint;
begin
    if p_batch_id is null then
        raise exception 'p_batch_id is required';
    end if;
    if p_events is null or jsonb_typeof(p_events) != 'array' then
        raise exception 'p_events must be a JSON array';
    end if;
    if jsonb_array_length(p_events) < 1
        or jsonb_array_length(p_events) > 500 then
        raise exception 'p_events must contain between 1 and 500 entries';
    end if;

    insert into public.member_activity_batch_receipts(batch_id)
    values (p_batch_id) on conflict do nothing;
    if not found then
        return;
    end if;

    for e in select * from jsonb_array_elements(p_events)
    loop
        v_guild_id := nullif(btrim(e->>'guild_id'), '');
        v_user_id := nullif(btrim(e->>'user_id'), '');
        v_activity_type := btrim(e->>'activity_type');
        v_occurred_at := (e->>'occurred_at')::timestamptz;
        v_channel_id := nullif(btrim(e->>'channel_id'), '');
        v_process_id := nullif(btrim(e->>'process_id'), '');
        v_event_count := greatest(coalesce((e->>'event_count')::bigint, 1), 1);

        if v_guild_id is null then
            raise exception 'guild_id is required';
        end if;

        if v_user_id is null then
            raise exception 'user_id is required';
        end if;

        if v_activity_type not in (
            'message',
            'reaction',
            'interaction',
            'ticket_message'
        ) then
            raise exception 'unsupported direct activity type: %', v_activity_type;
        end if;

        insert into public.member_activity_ledger (
            guild_id,
            user_id,
            last_activity_at,
            last_activity_type,
            last_message_at,
            last_reaction_at,
            last_interaction_at,
            last_ticket_message_at,
            last_channel_id,
            last_process_id,
            event_count,
            first_observed_at,
            updated_at
        )
        values (
            v_guild_id,
            v_user_id,
            v_occurred_at,
            v_activity_type,
            case when v_activity_type = 'message'
                then v_occurred_at end,
            case when v_activity_type = 'reaction'
                then v_occurred_at end,
            case when v_activity_type = 'interaction'
                then v_occurred_at end,
            case when v_activity_type = 'ticket_message'
                then v_occurred_at end,
            v_channel_id,
            v_process_id,
            v_event_count,
            coalesce((e->>'first_occurred_at')::timestamptz, v_occurred_at),
            v_occurred_at
        )
        on conflict (guild_id, user_id)
        do update set
            last_activity_at = greatest(
                public.member_activity_ledger.last_activity_at,
                excluded.last_activity_at
            ),
            last_activity_type = case
                when excluded.last_activity_at
                    >= public.member_activity_ledger.last_activity_at
                then excluded.last_activity_type
                else public.member_activity_ledger.last_activity_type
            end,
            last_message_at = case
                when v_activity_type = 'message'
                then greatest(
                    coalesce(
                        public.member_activity_ledger.last_message_at,
                        v_occurred_at
                    ),
                    v_occurred_at
                )
                else public.member_activity_ledger.last_message_at
            end,
            last_reaction_at = case
                when v_activity_type = 'reaction'
                then greatest(
                    coalesce(
                        public.member_activity_ledger.last_reaction_at,
                        v_occurred_at
                    ),
                    v_occurred_at
                )
                else public.member_activity_ledger.last_reaction_at
            end,
            last_interaction_at = case
                when v_activity_type = 'interaction'
                then greatest(
                    coalesce(
                        public.member_activity_ledger.last_interaction_at,
                        v_occurred_at
                    ),
                    v_occurred_at
                )
                else public.member_activity_ledger.last_interaction_at
            end,
            last_ticket_message_at = case
                when v_activity_type = 'ticket_message'
                then greatest(
                    coalesce(
                        public.member_activity_ledger.last_ticket_message_at,
                        v_occurred_at
                    ),
                    v_occurred_at
                )
                else public.member_activity_ledger.last_ticket_message_at
            end,
            last_channel_id = case
                when excluded.last_activity_at
                    >= public.member_activity_ledger.last_activity_at
                then excluded.last_channel_id
                else public.member_activity_ledger.last_channel_id
            end,
            last_process_id = excluded.last_process_id,
            event_count = public.member_activity_ledger.event_count
                + v_event_count,
            first_observed_at = least(
                public.member_activity_ledger.first_observed_at,
                excluded.first_observed_at
            ),
            updated_at = greatest(
                public.member_activity_ledger.updated_at,
                excluded.updated_at
            );
    end loop;
end;
$$;

revoke execute on function public.record_member_activity_batch(uuid, jsonb)
from public, anon, authenticated;
grant execute on function public.record_member_activity_batch(uuid, jsonb)
to service_role;

-- Confirmed batches no longer need receipts. Keep an indexed expiry path for
-- abandoned receipts from process death without forcing an extra write per event.
create index if not exists idx_member_activity_batch_receipts_applied_at
    on public.member_activity_batch_receipts(applied_at);

create or replace function public.ack_member_activity_batches(
    p_batch_ids uuid[]
) returns void
language plpgsql
security definer
set search_path = pg_catalog, public
as $
begin
    if p_batch_ids is null or array_length(p_batch_ids, 1) > 128 then
        raise exception 'expected up to 128 acknowledged batch IDs';
    end if;
    delete from public.member_activity_batch_receipts
    where batch_id = any(p_batch_ids)
       or applied_at < now() - interval '7 days';
end;
$;
revoke execute on function public.ack_member_activity_batches(uuid[])
from public, anon, authenticated;
grant execute on function public.ack_member_activity_batches(uuid[])
to service_role;