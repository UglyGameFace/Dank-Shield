-- Emergency workaround for PGRST002: PostgREST still references the dropped idle schema.
-- This adds no objects, grants, application data, triggers, or RLS policy.
-- After Data API settings no longer expose idle, the empty schema can be retired.
CREATE SCHEMA IF NOT EXISTS idle;
