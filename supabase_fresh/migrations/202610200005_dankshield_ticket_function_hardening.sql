-- Dedicated new DankShield Supabase only: viamriafkbqhquvrsnua
-- Fix security linter function_search_path_mutable on imported audit functions.
-- pg_catalog first; trusted public functions explicitly qualified in body;
-- pg_temp last to prevent search-path object shadowing.
alter function public._afe_jsonb_text(jsonb) set search_path = pg_catalog, public, pg_temp;
alter function public._afe_build_search_text(text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,jsonb) set search_path = pg_catalog, public, pg_temp;
alter function public.activity_feed_log(text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,jsonb,timestamptz) set search_path = pg_catalog, public, pg_temp;
alter function public.set_current_timestamp_updated_at() set search_path = pg_catalog, public, pg_temp;
alter function public.set_ticket_categories_updated_at() set search_path = pg_catalog, public, pg_temp;
alter function public.sync_ticket_compat_columns() set search_path = pg_catalog, public, pg_temp;
alter function public.trg_ticket_messages_to_activity_feed() set search_path = pg_catalog, public, pg_temp;
alter function public.trg_ticket_notes_to_activity_feed() set search_path = pg_catalog, public, pg_temp;
alter function public.trg_tickets_insert_to_activity_feed() set search_path = pg_catalog, public, pg_temp;
alter function public.trg_tickets_update_to_activity_feed() set search_path = pg_catalog, public, pg_temp;
