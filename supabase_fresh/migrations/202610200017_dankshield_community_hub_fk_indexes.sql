-- Additive Community Hub FK reverse lookup indexes. The canonical source
-- defines these FKs but does not index the referencing columns. Retain the
-- exact source schema and avoid speculative event-table partitioning.
create index if not exists dank_community_resources_session_idx
 on public.dank_community_managed_resources (session_id)
 where session_id is not null;

create index if not exists dank_community_reports_session_idx
 on public.dank_community_reports (session_id)
 where session_id is not null;

create index if not exists dank_community_sessions_parent_idx
 on public.dank_community_sessions (parent_session_id)
 where parent_session_id is not null;
