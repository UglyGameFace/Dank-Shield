-- Dedicated fresh DankShield Supabase, phase 2: ticket and panel contracts.
-- 2026-10-20. Run ONLY on the new project viamriafkbqhquvrsnua.
-- No production source modification, source data insertion, event history,
-- legacy hard-coded guild seeds, or runtime credential change.
-- Triggers that depend on activity_feed_log and config history are deferred
-- until their authoritative schema is deployed (not silently omitted at cutover).
-- This is the actual legacy source column/constraint contract, scrubbed of
-- legacy RLS-disabled/public grants and redundant historical index copies.

create table public.ticket_categories (
    "id" uuid not null default gen_random_uuid(),
    "guild_id" text not null,
    "name" text not null,
    "slug" text not null,
    "color" text default '#45d483'::text,
    "created_at" timestamptz default now(),
    "staff_role_ids" text[] default '{}'::text[],
    "staff_role_names" text[] default '{}'::text[],
    "description" text,
    "intake_type" text not null default 'general'::text,
    "match_keywords" jsonb not null default '[]'::jsonb,
    "button_label" text,
    "sort_order" integer,
    "is_default" boolean not null default false,
    "category_key" text,
    "display_name" text,
    "discord_category_id" text,
    "category_type" text not null default 'ticket'::text,
    "is_enabled" boolean not null default true,
    "metadata" jsonb not null default '{}'::jsonb,
    "updated_at" timestamptz not null default now(),
    "form_enabled" boolean not null default false,
    "form_questions" jsonb not null default '[]'::jsonb,
    "form_config" jsonb not null default '{}'::jsonb,
    "form_updated_at" timestamptz,
    "form_updated_by" text,
    "managed_by_dank" boolean not null default false,
    "managed_catalog_version" integer,
    "managed_category_key" text,
    constraint "ticket_categories_intake_type_check" CHECK (((intake_type IS NULL) OR (intake_type = ANY (ARRAY['general'::text, 'support'::text, 'verification'::text, 'appeal'::text, 'report'::text, 'question'::text, 'partnership'::text, 'account'::text, 'purchase'::text, 'billing'::text, 'refund'::text, 'technical'::text, 'bug'::text, 'staff'::text, 'ghost'::text, 'custom'::text, 'other'::text, 'cod_services'::text, 'game_services'::text])))),
    constraint "ticket_categories_pkey" PRIMARY KEY (id),
    constraint "ticket_categories_guild_id_slug_key" UNIQUE (guild_id, slug)
);
alter table public.ticket_categories enable row level security;
revoke all on public.ticket_categories from public, anon, authenticated;
grant select, insert, update, delete on public.ticket_categories to service_role;

create table public.ticket_panels (
    "id" uuid not null default gen_random_uuid(),
    "guild_id" text not null,
    "panel_key" text not null,
    "panel_name" text not null,
    "panel_channel_id" text,
    "panel_message_id" text,
    "panel_style" text not null default 'buttons'::text,
    "prompt_title" text,
    "prompt_description" text,
    "embed_title" text,
    "embed_description" text,
    "button_label" text,
    "menu_placeholder" text,
    "preset_key" text,
    "is_enabled" boolean not null default true,
    "sort_order" integer not null default 0,
    "created_at" timestamptz not null default now(),
    "updated_at" timestamptz not null default now(),
    constraint "ticket_panels_panel_key_not_blank" CHECK ((length(TRIM(BOTH FROM panel_key)) > 0)),
    constraint "ticket_panels_panel_name_not_blank" CHECK ((length(TRIM(BOTH FROM panel_name)) > 0)),
    constraint "ticket_panels_panel_style_check" CHECK ((panel_style = ANY (ARRAY['buttons'::text, 'select'::text, 'hybrid'::text, 'modal'::text]))),
    constraint "ticket_panels_pkey" PRIMARY KEY (id),
    constraint "ticket_panels_guild_panel_key_unique" UNIQUE (guild_id, panel_key)
);
alter table public.ticket_panels enable row level security;
revoke all on public.ticket_panels from public, anon, authenticated;
grant select, insert, update, delete on public.ticket_panels to service_role;

create table public.ticket_panel_presets (
    "id" uuid not null default gen_random_uuid(),
    "guild_id" text not null,
    "preset_key" text not null,
    "preset_name" text not null,
    "panel_style" text not null default 'buttons'::text,
    "default_prompt_title" text,
    "default_prompt_description" text,
    "default_embed_title" text,
    "default_embed_description" text,
    "default_button_label" text,
    "default_menu_placeholder" text,
    "default_rules_json" jsonb not null default '{}'::jsonb,
    "created_at" timestamptz not null default now(),
    "updated_at" timestamptz not null default now(),
    constraint "ticket_panel_presets_name_not_blank" CHECK ((length(TRIM(BOTH FROM preset_name)) > 0)),
    constraint "ticket_panel_presets_panel_style_check" CHECK ((panel_style = ANY (ARRAY['buttons'::text, 'select'::text, 'hybrid'::text, 'modal'::text]))),
    constraint "ticket_panel_presets_preset_key_not_blank" CHECK ((length(TRIM(BOTH FROM preset_key)) > 0)),
    constraint "ticket_panel_presets_pkey" PRIMARY KEY (id),
    constraint "ticket_panel_presets_unique" UNIQUE (guild_id, preset_key)
);
alter table public.ticket_panel_presets enable row level security;
revoke all on public.ticket_panel_presets from public, anon, authenticated;
grant select, insert, update, delete on public.ticket_panel_presets to service_role;

create table public.ticket_panel_rules (
    "id" uuid not null default gen_random_uuid(),
    "guild_id" text not null,
    "panel_key" text not null,
    "cooldown_seconds" integer not null default 0,
    "max_tickets_per_window" integer not null default 0,
    "window_minutes" integer not null default 0,
    "auto_close_enabled" boolean not null default false,
    "auto_close_minutes" integer not null default 1440,
    "inactivity_reminders_enabled" boolean not null default true,
    "inactivity_reminder_minutes" integer not null default 240,
    "staff_alert_channel_id" text,
    "allow_unverified" boolean not null default true,
    "allow_verified" boolean not null default true,
    "allow_resident" boolean not null default true,
    "allow_staff" boolean not null default true,
    "allow_unknown_members" boolean not null default true,
    "ghost_allowed" boolean not null default false,
    "transcript_mode" text not null default 'on_close'::text,
    "close_confirmation_required" boolean not null default true,
    "per_owner_open_limit" integer not null default 1,
    "created_at" timestamptz not null default now(),
    "updated_at" timestamptz not null default now(),
    constraint "ticket_panel_rules_auto_close_min" CHECK ((auto_close_minutes >= 5)),
    constraint "ticket_panel_rules_cooldown_nonnegative" CHECK ((cooldown_seconds >= 0)),
    constraint "ticket_panel_rules_inactivity_min" CHECK ((inactivity_reminder_minutes >= 1)),
    constraint "ticket_panel_rules_max_window_nonnegative" CHECK ((max_tickets_per_window >= 0)),
    constraint "ticket_panel_rules_owner_limit_min" CHECK ((per_owner_open_limit >= 1)),
    constraint "ticket_panel_rules_panel_key_not_blank" CHECK ((length(TRIM(BOTH FROM panel_key)) > 0)),
    constraint "ticket_panel_rules_transcript_mode_check" CHECK ((transcript_mode = ANY (ARRAY['always'::text, 'on_close'::text, 'manual'::text, 'disabled'::text]))),
    constraint "ticket_panel_rules_window_nonnegative" CHECK ((window_minutes >= 0)),
    constraint "ticket_panel_rules_pkey" PRIMARY KEY (id),
    constraint "ticket_panel_rules_unique" UNIQUE (guild_id, panel_key)
);
alter table public.ticket_panel_rules enable row level security;
revoke all on public.ticket_panel_rules from public, anon, authenticated;
grant select, insert, update, delete on public.ticket_panel_rules to service_role;

create table public.ticket_panel_categories (
    "id" uuid not null default gen_random_uuid(),
    "guild_id" text not null,
    "panel_key" text not null,
    "category_slug" text not null,
    "sort_order" integer not null default 0,
    "is_enabled" boolean not null default true,
    "created_at" timestamptz not null default now(),
    "updated_at" timestamptz not null default now(),
    constraint "ticket_panel_categories_panel_key_not_blank" CHECK ((length(TRIM(BOTH FROM panel_key)) > 0)),
    constraint "ticket_panel_categories_slug_not_blank" CHECK ((length(TRIM(BOTH FROM category_slug)) > 0)),
    constraint "ticket_panel_categories_pkey" PRIMARY KEY (id),
    constraint "ticket_panel_categories_unique" UNIQUE (guild_id, panel_key, category_slug)
);
alter table public.ticket_panel_categories enable row level security;
revoke all on public.ticket_panel_categories from public, anon, authenticated;
grant select, insert, update, delete on public.ticket_panel_categories to service_role;

create table public.ticket_counters (
    "guild_id" text not null,
    "last_ticket_number" bigint not null default 0,
    "updated_at" timestamptz default now(),
    constraint "ticket_counters_pkey" PRIMARY KEY (guild_id)
);
alter table public.ticket_counters enable row level security;
revoke all on public.ticket_counters from public, anon, authenticated;
grant select, insert, update, delete on public.ticket_counters to service_role;

create table public.ticket_macros (
    "id" uuid not null default gen_random_uuid(),
    "guild_id" text not null,
    "slug" text not null,
    "name" text not null,
    "body" text not null,
    "category" text,
    "is_active" boolean not null default true,
    "sort_order" integer not null default 0,
    "send_as_note" boolean not null default false,
    "created_by" text,
    "updated_by" text,
    "created_at" timestamptz not null default now(),
    "updated_at" timestamptz not null default now(),
    constraint "ticket_macros_pkey" PRIMARY KEY (id),
    constraint "ticket_macros_guild_id_slug_key" UNIQUE (guild_id, slug)
);
alter table public.ticket_macros enable row level security;
revoke all on public.ticket_macros from public, anon, authenticated;
grant select, insert, update, delete on public.ticket_macros to service_role;

create table public.ticket_automation_settings (
    "guild_id" text not null,
    "enabled" boolean not null default false,
    "sla_breach_alerts_enabled" boolean not null default true,
    "inactivity_reminders_enabled" boolean not null default true,
    "auto_close_enabled" boolean not null default false,
    "inactivity_reminder_minutes" integer not null default 240,
    "auto_close_minutes" integer not null default 1440,
    "staff_alert_channel_id" text,
    "updated_at" timestamptz not null default now(),
    constraint "ticket_automation_settings_pkey" PRIMARY KEY (guild_id)
);
alter table public.ticket_automation_settings enable row level security;
revoke all on public.ticket_automation_settings from public, anon, authenticated;
grant select, insert, update, delete on public.ticket_automation_settings to service_role;

create table public.ticket_automation_state (
    "guild_id" text not null,
    "channel_id" text not null,
    "sla_breach_alert_sent_at" timestamptz,
    "last_inactivity_reminder_at" timestamptz,
    "auto_closed_at" timestamptz,
    "updated_at" timestamptz not null default now(),
    constraint "ticket_automation_state_pkey" PRIMARY KEY (guild_id, channel_id)
);
alter table public.ticket_automation_state enable row level security;
revoke all on public.ticket_automation_state from public, anon, authenticated;
grant select, insert, update, delete on public.ticket_automation_state to service_role;

create table public.tickets (
    "id" uuid not null default gen_random_uuid(),
    "guild_id" text not null,
    "user_id" text not null,
    "username" text,
    "title" text not null,
    "category" text not null,
    "status" text not null default 'open'::text,
    "priority" text not null default 'medium'::text,
    "claimed_by" text,
    "closed_by" text,
    "closed_reason" text,
    "initial_message" text,
    "ai_category_confidence" numeric default 0,
    "mod_suggestion" text,
    "mod_suggestion_confidence" numeric default 0,
    "created_at" timestamptz default now(),
    "updated_at" timestamptz default now(),
    "closed_at" timestamptz,
    "discord_thread_id" text,
    "assigned_to" text,
    "reopened_at" timestamptz,
    "sla_deadline" timestamptz,
    "channel_id" text,
    "channel_name" text,
    "is_ghost" boolean not null default false,
    "deleted_at" timestamptz,
    "deleted_by" text,
    "transcript_url" text,
    "transcript_message_id" text,
    "transcript_channel_id" text,
    "source" text default 'discord'::text,
    "category_id" uuid,
    "category_override" boolean not null default false,
    "category_set_by" text,
    "category_set_at" timestamptz,
    "matched_category_id" uuid,
    "matched_category_name" text,
    "matched_category_slug" text,
    "matched_intake_type" text,
    "matched_category_reason" text,
    "matched_category_score" integer not null default 0,
    "ticket_number" integer,
    "decision" text,
    "last_activity_at" timestamptz,
    "last_message_id" text,
    "panel_message_id" text,
    "webhook_url" text,
    "webhook_id" text,
    "reopened_by" text,
    "reopen_reason" text,
    "close_reason" text,
    "delete_reason" text,
    "owner_id" text,
    "owner_name" text,
    "requester_id" text,
    "requester_name" text,
    "claimed_by_name" text,
    "assigned_to_name" text,
    "closed_by_name" text,
    "deleted_by_name" text,
    "reopened_by_name" text,
    "metadata" jsonb not null default '{}'::jsonb,
    "meta" jsonb not null default '{}'::jsonb,
    constraint "tickets_priority_check_v2" CHECK ((priority = ANY (ARRAY['low'::text, 'medium'::text, 'high'::text, 'urgent'::text]))),
    constraint "tickets_status_check_v2" CHECK ((status = ANY (ARRAY['open'::text, 'claimed'::text, 'closed'::text, 'deleted'::text]))),
    constraint "tickets_pkey" PRIMARY KEY (id)
);
alter table public.tickets enable row level security;
revoke all on public.tickets from public, anon, authenticated;
grant select, insert, update, delete on public.tickets to service_role;

create table public.ticket_notes (
    "id" uuid not null default gen_random_uuid(),
    "ticket_id" uuid not null,
    "staff_id" text not null,
    "staff_name" text,
    "content" text not null,
    "created_at" timestamptz default now(),
    "updated_at" timestamptz default now(),
    "is_pinned" boolean not null default false,
    "guild_id" text,
    "channel_id" text,
    "author_id" text,
    "author_name" text,
    "note" text,
    constraint "ticket_notes_pkey" PRIMARY KEY (id)
);
alter table public.ticket_notes enable row level security;
revoke all on public.ticket_notes from public, anon, authenticated;
grant select, insert, update, delete on public.ticket_notes to service_role;

create table public.ticket_messages (
    "id" uuid not null default gen_random_uuid(),
    "ticket_id" uuid not null,
    "author_id" text not null,
    "author_name" text,
    "content" text not null,
    "message_type" text not null default 'staff'::text,
    "created_at" timestamptz default now(),
    "attachments" jsonb default '[]'::jsonb,
    "source" text,
    "guild_id" text,
    "channel_id" text,
    "message_id" text,
    "metadata" jsonb not null default '{}'::jsonb,
    constraint "ticket_messages_pkey" PRIMARY KEY (id)
);
alter table public.ticket_messages enable row level security;
revoke all on public.ticket_messages from public, anon, authenticated;
grant select, insert, update, delete on public.ticket_messages to service_role;

create table public.ticket_form_responses (
    "id" uuid not null default gen_random_uuid(),
    "ticket_id" uuid,
    "guild_id" text not null,
    "channel_id" text,
    "user_id" text not null,
    "category_slug" text not null,
    "question_index" integer not null default 0,
    "question_label" text not null,
    "question_key" text,
    "answer" text not null default ''::text,
    "required" boolean not null default false,
    "created_at" timestamptz not null default now(),
    constraint "ticket_form_responses_pkey" PRIMARY KEY (id)
);
alter table public.ticket_form_responses enable row level security;
revoke all on public.ticket_form_responses from public, anon, authenticated;
grant select, insert, update, delete on public.ticket_form_responses to service_role;

-- Delayed FKs ensure all principal table types exist first.
alter table public.ticket_form_responses add constraint "ticket_form_responses_ticket_id_fkey" FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE SET NULL;
alter table public.ticket_messages add constraint "ticket_messages_ticket_id_fkey" FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE;
alter table public.ticket_notes add constraint "ticket_notes_ticket_id_fkey" FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE;
alter table public.ticket_panel_categories add constraint "ticket_panel_categories_panel_fk" FOREIGN KEY (guild_id, panel_key) REFERENCES ticket_panels(guild_id, panel_key) ON UPDATE CASCADE ON DELETE CASCADE;
alter table public.ticket_panel_rules add constraint "ticket_panel_rules_panel_fk" FOREIGN KEY (guild_id, panel_key) REFERENCES ticket_panels(guild_id, panel_key) ON UPDATE CASCADE ON DELETE CASCADE;
alter table public.tickets add constraint "tickets_category_id_fkey" FOREIGN KEY (category_id) REFERENCES ticket_categories(id) ON DELETE SET NULL;

-- Focused indexes: no duplicated indexes from years of incremental patches.
create index idx_dank_tickets_guild_status_created on public.tickets (guild_id,status,created_at desc);
create index idx_dank_tickets_guild_owner on public.tickets (guild_id,owner_id);
create index idx_dank_tickets_guild_category on public.tickets (guild_id,category_id);
create index idx_dank_tickets_ticket_number on public.tickets (guild_id,ticket_number desc) where ticket_number is not null;
create index idx_dank_tickets_channel_id on public.tickets (channel_id);
create index idx_dank_tickets_thread_id on public.tickets (discord_thread_id);
create index idx_dank_ticket_categories_guild_sort on public.ticket_categories (guild_id,sort_order);
create index idx_dank_ticket_panels_guild_sort on public.ticket_panels (guild_id,is_enabled,sort_order);
create index idx_dank_ticket_messages_created on public.ticket_messages(ticket_id,created_at);
create index idx_dank_ticket_notes_created on public.ticket_notes(ticket_id,created_at desc);
create index idx_dank_ticket_panel_rules_guild on public.ticket_panel_rules(guild_id,panel_key);
create index idx_dank_ticket_form_responses_guild on public.ticket_form_responses(guild_id,user_id,created_at desc);

comment on table public.tickets is 'Dank Shield ticket records. Backend-only; access by guild scoped service.';
comment on table public.ticket_categories is 'Per-guild ticket category settings; backend-only.';
