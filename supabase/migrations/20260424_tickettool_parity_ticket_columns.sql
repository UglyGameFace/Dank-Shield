-- TicketTool parity ticket metadata columns.
--
-- This repository can run without the optional legacy `public.tickets` table;
-- the active ticket service has runtime schema-compatibility handling. Supabase
-- preview branches replay every migration on a fresh database, so this legacy
-- enhancement must become a safe no-op when that optional table is absent.

DO $ticket_metadata$
BEGIN
    IF to_regclass('public.tickets') IS NOT NULL THEN
        EXECUTE $sql$
            ALTER TABLE public.tickets
            ADD COLUMN IF NOT EXISTS panel_message_id text,
            ADD COLUMN IF NOT EXISTS webhook_url text,
            ADD COLUMN IF NOT EXISTS webhook_id text,
            ADD COLUMN IF NOT EXISTS reopened_by text,
            ADD COLUMN IF NOT EXISTS reopened_by_name text,
            ADD COLUMN IF NOT EXISTS reopen_reason text,
            ADD COLUMN IF NOT EXISTS close_reason text,
            ADD COLUMN IF NOT EXISTS delete_reason text,
            ADD COLUMN IF NOT EXISTS owner_id text,
            ADD COLUMN IF NOT EXISTS owner_name text,
            ADD COLUMN IF NOT EXISTS requester_id text,
            ADD COLUMN IF NOT EXISTS requester_name text,
            ADD COLUMN IF NOT EXISTS claimed_by_name text,
            ADD COLUMN IF NOT EXISTS assigned_to_name text,
            ADD COLUMN IF NOT EXISTS closed_by_name text,
            ADD COLUMN IF NOT EXISTS deleted_by_name text
        $sql$;

        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_tickets_owner_id ON public.tickets(owner_id)';
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_tickets_requester_id ON public.tickets(requester_id)';
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_tickets_reopened_by ON public.tickets(reopened_by)';
    ELSE
        RAISE NOTICE 'Skipping optional TicketTool metadata migration: public.tickets does not exist.';
    END IF;
END
$ticket_metadata$;
