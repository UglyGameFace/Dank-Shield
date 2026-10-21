-- Dedicated offline Dank Shield activity feed: restore the ORIGINAL source
-- synchronization trigger. Existing 4,756 original events are NOT included in
-- this schema change, and must be transferred with this trigger transaction-
-- locally disabled, then re-enabled BEFORE commit to preserve 1,825 original
-- rows where metadata and meta legitimately differ.
create or replace function public.sync_activity_feed_meta_columns()
returns trigger language plpgsql
set search_path=pg_catalog,public
as $sync$
begin
  if new.meta is null and new.metadata is not null then
    new.meta := new.metadata;
  elsif new.metadata is null and new.meta is not null then
    new.metadata := new.meta;
  elsif new.meta is null and new.metadata is null then
    new.meta := '{}'::jsonb;
    new.metadata := '{}'::jsonb;
  elsif new.meta is distinct from new.metadata then
    new.metadata := coalesce(new.metadata,new.meta,'{}'::jsonb);
    new.meta := new.metadata;
  end if;
  new.search_text := coalesce(new.search_text,'');
  return new;
end;
$sync$;
revoke all on function public.sync_activity_feed_meta_columns()
  from public,anon,authenticated;
grant execute on function public.sync_activity_feed_meta_columns()
  to service_role;
create trigger trg_sync_activity_feed_meta_columns
before insert or update on public.activity_feed_events
for each row execute function public.sync_activity_feed_meta_columns();
