-- NEW dedicated Supabase only: viamriafkbqhquvrsnua.
-- Apply only AFTER 24 most-recent, exact source versions have been inserted
-- and the identity sequence advanced beyond their max version_id.
-- Never enable before snapshots are imported: no synthetic history desired.
create trigger trg_capture_guild_config_version
after insert or update on public.guild_configs for each row
execute function public.capture_guild_config_version();
create trigger trg_capture_ticket_category_version
after insert or update or delete on public.ticket_categories for each row
execute function public.capture_ticket_category_version();
