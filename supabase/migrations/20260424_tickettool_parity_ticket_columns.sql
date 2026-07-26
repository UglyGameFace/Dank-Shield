-- TicketTool parity ticket metadata columns
-- Run this in Supabase SQL editor when you are ready to keep the richer metadata
-- instead of relying on the runtime compatibility guard.
--
-- Some fresh Supabase preview databases do not contain the legacy tickets table
-- because its original schema predates the committed migration history. Keep this
-- historical migration replay-safe: enrich tickets when it exists, but never
-- invent a partial replacement table when it does not.

do $$
begin
    if to_regclass('public.tickets') is null then
        raise notice 'Skipping TicketTool parity columns because public.tickets does not exist.';
    else
        execute $ddl$
            alter table public.tickets
            add column if not exists panel_message_id text,
            add column if not exists webhook_url text,
            add column if not exists webhook_id text,
            add column if not exists reopened_by text,
            add column if not exists reopened_by_name text,
            add column if not exists reopen_reason text,
            add column if not exists close_reason text,
            add column if not exists delete_reason text,
            add column if not exists owner_id text,
            add column if not exists owner_name text,
            add column if not exists requester_id text,
            add column if not exists requester_name text,
            add column if not exists claimed_by_name text,
            add column if not exists assigned_to_name text,
            add column if not exists closed_by_name text,
            add column if not exists deleted_by_name text
        $ddl$;

        execute 'create index if not exists idx_tickets_owner_id on public.tickets(owner_id)';
        execute 'create index if not exists idx_tickets_requester_id on public.tickets(requester_id)';
        execute 'create index if not exists idx_tickets_reopened_by on public.tickets(reopened_by)';
    end if;
end
$$;
