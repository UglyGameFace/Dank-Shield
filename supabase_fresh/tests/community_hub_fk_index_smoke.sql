-- Rollback-only FK index verification.
begin;
do $test$
declare missing integer;
begin
select count(*) into missing
from (values
 ('dank_community_managed_resources','dank_community_managed_resources_session_id_fkey'),
 ('dank_community_reports','dank_community_reports_session_id_fkey'),
 ('dank_community_sessions','dank_community_sessions_parent_session_id_fkey')
) as f(tab,constraint_name)
where not exists (
 select 1
 from pg_constraint c
 join pg_class rel on rel.oid=c.conrelid
 where rel.relname=f.tab and c.conname=f.constraint_name and c.contype='f'
);
if missing<>0 then raise exception 'Expected three real source FKs, missing %',missing;end if;

if not exists (select 1 from pg_indexes
 where schemaname='public' and tablename='dank_community_managed_resources'
 and indexname='dank_community_resources_session_idx' and indexdef like '%(session_id)%') then
 raise exception 'Resources FK index missing';end if;
if not exists (select 1 from pg_indexes
 where schemaname='public' and tablename='dank_community_reports'
 and indexname='dank_community_reports_session_idx' and indexdef like '%(session_id)%') then
 raise exception 'Reports FK index missing';end if;
if not exists (select 1 from pg_indexes
 where schemaname='public' and tablename='dank_community_sessions'
 and indexname='dank_community_sessions_parent_idx' and indexdef like '%(parent_session_id)%') then
 raise exception 'Parent session FK index missing';end if;
end $test$;
rollback;
