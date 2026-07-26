-- TicketTool parity ticket metadata columns
-- Run this in Supabase SQL editor when you are ready to keep the richer metadata
-- instead of relying on the runtime compatibility guard.

ALTER TABLE tickets
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
ADD COLUMN IF NOT EXISTS deleted_by_name text;

CREATE INDEX IF NOT EXISTS idx_tickets_owner_id ON tickets(owner_id);
CREATE INDEX IF NOT EXISTS idx_tickets_requester_id ON tickets(requester_id);
CREATE INDEX IF NOT EXISTS idx_tickets_reopened_by ON tickets(reopened_by);
