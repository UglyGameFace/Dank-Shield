-- Ensure the category-selection RPC can invalidate a previously finished setup
-- even on older guild_configs schemas that predate setup completion metadata.

alter table public.guild_configs
    add column if not exists setup_completed boolean not null default false,
    add column if not exists setup_completion_invalidated_at timestamptz,
    add column if not exists setup_completion_invalidated_reason text;
