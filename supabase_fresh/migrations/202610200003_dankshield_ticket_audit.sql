-- Separate NEW Supabase project only: viamriafkbqhquvrsnua
-- Audit contracts must be available before enabling ticket audit triggers.
-- No old audit events are imported. All new audit events remain guild-scoped.
create table public.activity_feed_events (
  id uuid not null default gen_random_uuid(),
  guild_id text not null,
  event_family text not null,
  event_type text not null,
  source text not null default 'system'::text,
  actor_user_id text,
  actor_name text,
  target_user_id text,
  target_name text,
  channel_id text,
  channel_name text,
  ticket_id text,
  ticket_message_id text,
  related_table text,
  related_id text,
  title text,
  description text,
  reason text,
  search_text text not null default ''::text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  meta jsonb not null default '{}'::jsonb,
  actor_id text,
  target_id text,
  message text,
  constraint activity_feed_events_pkey primary key (id)
);
alter table public.activity_feed_events enable row level security;
revoke all on public.activity_feed_events from public, anon, authenticated;
grant select, insert, update, delete on public.activity_feed_events to service_role;
create index idx_dank_audit_feed_guild_created on public.activity_feed_events(guild_id,created_at desc);
create index idx_dank_audit_feed_guild_type on public.activity_feed_events(guild_id,event_type,created_at desc);
create index idx_dank_audit_feed_ticket_created on public.activity_feed_events(ticket_id,created_at desc) where ticket_id is not null;

CREATE OR REPLACE FUNCTION public._afe_jsonb_text(v jsonb)
 RETURNS text
 LANGUAGE sql
 IMMUTABLE
AS $function$
  select case
    when v is null then ''
    else regexp_replace(v::text, '[\{\}\[\]":,]+', ' ', 'g')
  end;
$function$;
CREATE OR REPLACE FUNCTION public._afe_build_search_text(p_event_family text, p_event_type text, p_source text, p_actor_user_id text, p_actor_name text, p_target_user_id text, p_target_name text, p_channel_id text, p_channel_name text, p_ticket_id text, p_ticket_message_id text, p_related_table text, p_related_id text, p_title text, p_description text, p_reason text, p_metadata jsonb)
 RETURNS text
 LANGUAGE sql
 IMMUTABLE
AS $function$
  select trim(
    regexp_replace(
      concat_ws(
        ' ',
        coalesce(p_event_family, ''),
        coalesce(p_event_type, ''),
        coalesce(p_source, ''),
        coalesce(p_actor_user_id, ''),
        coalesce(p_actor_name, ''),
        coalesce(p_target_user_id, ''),
        coalesce(p_target_name, ''),
        coalesce(p_channel_id, ''),
        coalesce(p_channel_name, ''),
        coalesce(p_ticket_id, ''),
        coalesce(p_ticket_message_id, ''),
        coalesce(p_related_table, ''),
        coalesce(p_related_id, ''),
        coalesce(p_title, ''),
        coalesce(p_description, ''),
        coalesce(p_reason, ''),
        coalesce(public._afe_jsonb_text(p_metadata), '')
      ),
      '\s+',
      ' ',
      'g'
    )
  );
$function$;
CREATE OR REPLACE FUNCTION public.activity_feed_log(p_guild_id text, p_event_family text, p_event_type text, p_source text DEFAULT 'system'::text, p_actor_user_id text DEFAULT NULL::text, p_actor_name text DEFAULT NULL::text, p_target_user_id text DEFAULT NULL::text, p_target_name text DEFAULT NULL::text, p_channel_id text DEFAULT NULL::text, p_channel_name text DEFAULT NULL::text, p_ticket_id text DEFAULT NULL::text, p_ticket_message_id text DEFAULT NULL::text, p_related_table text DEFAULT NULL::text, p_related_id text DEFAULT NULL::text, p_title text DEFAULT NULL::text, p_description text DEFAULT NULL::text, p_reason text DEFAULT NULL::text, p_metadata jsonb DEFAULT '{}'::jsonb, p_created_at timestamp with time zone DEFAULT now())
 RETURNS uuid
 LANGUAGE plpgsql
AS $function$
declare
  v_id uuid;
  v_search_text text;
begin
  v_search_text := public._afe_build_search_text(
    p_event_family,
    p_event_type,
    p_source,
    p_actor_user_id,
    p_actor_name,
    p_target_user_id,
    p_target_name,
    p_channel_id,
    p_channel_name,
    p_ticket_id,
    p_ticket_message_id,
    p_related_table,
    p_related_id,
    p_title,
    p_description,
    p_reason,
    coalesce(p_metadata, '{}'::jsonb)
  );

  insert into public.activity_feed_events (
    guild_id,
    event_family,
    event_type,
    source,
    actor_user_id,
    actor_name,
    target_user_id,
    target_name,
    channel_id,
    channel_name,
    ticket_id,
    ticket_message_id,
    related_table,
    related_id,
    title,
    description,
    reason,
    search_text,
    metadata,
    created_at
  )
  values (
    p_guild_id,
    p_event_family,
    p_event_type,
    coalesce(p_source, 'system'),
    p_actor_user_id,
    p_actor_name,
    p_target_user_id,
    p_target_name,
    p_channel_id,
    p_channel_name,
    p_ticket_id,
    p_ticket_message_id,
    p_related_table,
    p_related_id,
    p_title,
    p_description,
    p_reason,
    coalesce(v_search_text, ''),
    coalesce(p_metadata, '{}'::jsonb),
    coalesce(p_created_at, now())
  )
  returning id into v_id;

  return v_id;
end;
$function$;
CREATE OR REPLACE FUNCTION public.set_current_timestamp_updated_at()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
begin
  new.updated_at = now();
  return new;
end;
$function$;
CREATE OR REPLACE FUNCTION public.set_ticket_categories_updated_at()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
begin
  new.updated_at = now();
  return new;
end;
$function$;
CREATE OR REPLACE FUNCTION public.sync_ticket_compat_columns()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
begin
  new.owner_id := coalesce(nullif(new.owner_id, ''), new.user_id);
  new.requester_id := coalesce(nullif(new.requester_id, ''), new.user_id);

  new.owner_name := coalesce(nullif(new.owner_name, ''), new.username);
  new.requester_name := coalesce(nullif(new.requester_name, ''), new.username);

  new.close_reason := coalesce(nullif(new.close_reason, ''), new.closed_reason);
  new.closed_reason := coalesce(nullif(new.closed_reason, ''), new.close_reason);

  new.updated_at := now();
  return new;
end;
$function$;
CREATE OR REPLACE FUNCTION public.trg_ticket_messages_to_activity_feed()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
declare
  v_guild_id text;
  v_channel_id text;
  v_channel_name text;
  v_target_user_id text;
  v_target_name text;
  v_event_type text;
begin
  select
    t.guild_id,
    t.channel_id,
    t.channel_name,
    t.user_id,
    coalesce(t.username, t.user_id)
  into
    v_guild_id,
    v_channel_id,
    v_channel_name,
    v_target_user_id,
    v_target_name
  from public.tickets t
  where t.id = new.ticket_id;

  v_event_type := case
    when lower(coalesce(new.message_type, '')) = 'staff' then 'ticket_staff_reply'
    when lower(coalesce(new.message_type, '')) = 'member' then 'ticket_member_reply'
    else 'ticket_message'
  end;

  perform public.activity_feed_log(
    p_guild_id         := coalesce(v_guild_id, ''),
    p_event_family     := 'ticket',
    p_event_type       := v_event_type,
    p_source           := coalesce(new.source, 'discord'),
    p_actor_user_id    := new.author_id,
    p_actor_name       := new.author_name,
    p_target_user_id   := v_target_user_id,
    p_target_name      := v_target_name,
    p_channel_id       := v_channel_id,
    p_channel_name     := v_channel_name,
    p_ticket_id        := new.ticket_id::text,
    p_ticket_message_id:= new.id::text,
    p_related_table    := 'ticket_messages',
    p_related_id       := new.id::text,
    p_title            := 'Ticket Message',
    p_description      := left(coalesce(new.content, ''), 300),
    p_reason           := null,
    p_metadata         := jsonb_build_object(
      'message_type', new.message_type,
      'attachments', coalesce(new.attachments, '[]'::jsonb),
      'source', new.source
    ),
    p_created_at       := new.created_at
  );
  return new;
end;
$function$;
CREATE OR REPLACE FUNCTION public.trg_ticket_notes_to_activity_feed()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
declare
  v_guild_id text;
  v_channel_id text;
  v_channel_name text;
  v_target_user_id text;
  v_target_name text;
begin
  select
    t.guild_id,
    t.channel_id,
    t.channel_name,
    t.user_id,
    coalesce(t.username, t.user_id)
  into
    v_guild_id,
    v_channel_id,
    v_channel_name,
    v_target_user_id,
    v_target_name
  from public.tickets t
  where t.id = new.ticket_id;

  perform public.activity_feed_log(
    p_guild_id         := coalesce(v_guild_id, ''),
    p_event_family     := 'ticket',
    p_event_type       := 'ticket_note',
    p_source           := 'dashboard',
    p_actor_user_id    := new.staff_id,
    p_actor_name       := new.staff_name,
    p_target_user_id   := v_target_user_id,
    p_target_name      := v_target_name,
    p_channel_id       := v_channel_id,
    p_channel_name     := v_channel_name,
    p_ticket_id        := new.ticket_id::text,
    p_related_table    := 'ticket_notes',
    p_related_id       := new.id::text,
    p_title            := 'Ticket Note',
    p_description      := left(coalesce(new.content, ''), 300),
    p_reason           := null,
    p_metadata         := jsonb_build_object(),
    p_created_at       := new.created_at
  );
  return new;
end;
$function$;
CREATE OR REPLACE FUNCTION public.trg_tickets_insert_to_activity_feed()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
begin
  perform public.activity_feed_log(
    p_guild_id         := new.guild_id,
    p_event_family     := 'ticket',
    p_event_type       := 'ticket_created',
    p_source           := coalesce(new.source, 'discord'),
    p_actor_user_id    := new.user_id,
    p_actor_name       := new.username,
    p_target_user_id   := new.user_id,
    p_target_name      := coalesce(new.username, new.user_id),
    p_channel_id       := new.channel_id,
    p_channel_name     := new.channel_name,
    p_ticket_id        := new.id::text,
    p_related_table    := 'tickets',
    p_related_id       := new.id::text,
    p_title            := 'Ticket Created',
    p_description      := coalesce(new.title, 'Ticket created'),
    p_reason           := null,
    p_metadata         := jsonb_build_object(
      'category', new.category,
      'priority', new.priority,
      'status', new.status,
      'is_ghost', new.is_ghost,
      'ticket_number', new.ticket_number,
      'matched_category_id', new.matched_category_id,
      'matched_category_name', new.matched_category_name,
      'matched_category_slug', new.matched_category_slug,
      'matched_intake_type', new.matched_intake_type
    ),
    p_created_at       := new.created_at
  );
  return new;
end;
$function$;
CREATE OR REPLACE FUNCTION public.trg_tickets_update_to_activity_feed()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
begin
  if coalesce(old.claimed_by, '') is distinct from coalesce(new.claimed_by, '')
     and new.claimed_by is not null then
    perform public.activity_feed_log(
      p_guild_id         := new.guild_id,
      p_event_family     := 'ticket',
      p_event_type       := 'ticket_claimed',
      p_source           := coalesce(new.source, 'dashboard'),
      p_actor_user_id    := new.claimed_by,
      p_actor_name       := new.claimed_by,
      p_target_user_id   := new.user_id,
      p_target_name      := coalesce(new.username, new.user_id),
      p_channel_id       := new.channel_id,
      p_channel_name     := new.channel_name,
      p_ticket_id        := new.id::text,
      p_related_table    := 'tickets',
      p_related_id       := new.id::text,
      p_title            := 'Ticket Claimed',
      p_description      := coalesce(new.title, 'Ticket claimed'),
      p_reason           := null,
      p_metadata         := jsonb_build_object(
        'assigned_to', new.assigned_to,
        'status', new.status
      ),
      p_created_at       := coalesce(new.updated_at, now())
    );
  end if;

  if coalesce(old.status, '') is distinct from coalesce(new.status, '') and lower(coalesce(new.status, '')) = 'closed' then
    perform public.activity_feed_log(
      p_guild_id         := new.guild_id,
      p_event_family     := 'ticket',
      p_event_type       := 'ticket_closed',
      p_source           := coalesce(new.source, 'dashboard'),
      p_actor_user_id    := new.closed_by,
      p_actor_name       := new.closed_by,
      p_target_user_id   := new.user_id,
      p_target_name      := coalesce(new.username, new.user_id),
      p_channel_id       := new.channel_id,
      p_channel_name     := new.channel_name,
      p_ticket_id        := new.id::text,
      p_related_table    := 'tickets',
      p_related_id       := new.id::text,
      p_title            := 'Ticket Closed',
      p_description      := coalesce(new.title, 'Ticket closed'),
      p_reason           := new.closed_reason,
      p_metadata         := jsonb_build_object(
        'status', new.status,
        'closed_at', new.closed_at
      ),
      p_created_at       := coalesce(new.closed_at, new.updated_at, now())
    );
  end if;

  if coalesce(old.status, '') is distinct from coalesce(new.status, '') and lower(coalesce(new.status, '')) = 'open'
     and new.reopened_at is not null then
    perform public.activity_feed_log(
      p_guild_id         := new.guild_id,
      p_event_family     := 'ticket',
      p_event_type       := 'ticket_reopened',
      p_source           := coalesce(new.source, 'dashboard'),
      p_actor_user_id    := null,
      p_actor_name       := null,
      p_target_user_id   := new.user_id,
      p_target_name      := coalesce(new.username, new.user_id),
      p_channel_id       := new.channel_id,
      p_channel_name     := new.channel_name,
      p_ticket_id        := new.id::text,
      p_related_table    := 'tickets',
      p_related_id       := new.id::text,
      p_title            := 'Ticket Reopened',
      p_description      := coalesce(new.title, 'Ticket reopened'),
      p_reason           := null,
      p_metadata         := jsonb_build_object(
        'reopened_at', new.reopened_at
      ),
      p_created_at       := coalesce(new.reopened_at, new.updated_at, now())
    );
  end if;

  if old.deleted_at is distinct from new.deleted_at and new.deleted_at is not null then
    perform public.activity_feed_log(
      p_guild_id         := new.guild_id,
      p_event_family     := 'ticket',
      p_event_type       := 'ticket_deleted',
      p_source           := coalesce(new.source, 'dashboard'),
      p_actor_user_id    := new.deleted_by,
      p_actor_name       := new.deleted_by,
      p_target_user_id   := new.user_id,
      p_target_name      := coalesce(new.username, new.user_id),
      p_channel_id       := new.channel_id,
      p_channel_name     := new.channel_name,
      p_ticket_id        := new.id::text,
      p_related_table    := 'tickets',
      p_related_id       := new.id::text,
      p_title            := 'Ticket Deleted',
      p_description      := coalesce(new.title, 'Ticket deleted'),
      p_reason           := new.closed_reason,
      p_metadata         := jsonb_build_object(
        'deleted_at', new.deleted_at,
        'transcript_url', new.transcript_url,
        'transcript_message_id', new.transcript_message_id,
        'transcript_channel_id', new.transcript_channel_id
      ),
      p_created_at       := coalesce(new.deleted_at, new.updated_at, now())
    );
  end if;

  if coalesce(old.category_id::text, '') is distinct from coalesce(new.category_id::text, '')
     or coalesce(old.matched_category_id::text, '') is distinct from coalesce(new.matched_category_id::text, '') then
    perform public.activity_feed_log(
      p_guild_id         := new.guild_id,
      p_event_family     := 'category',
      p_event_type       := 'ticket_category_changed',
      p_source           := coalesce(new.source, 'dashboard'),
      p_actor_user_id    := new.category_set_by,
      p_actor_name       := new.category_set_by,
      p_target_user_id   := new.user_id,
      p_target_name      := coalesce(new.username, new.user_id),
      p_channel_id       := new.channel_id,
      p_channel_name     := new.channel_name,
      p_ticket_id        := new.id::text,
      p_related_table    := 'tickets',
      p_related_id       := new.id::text,
      p_title            := 'Ticket Category Changed',
      p_description      := coalesce(new.title, 'Ticket category updated'),
      p_reason           := new.matched_category_reason,
      p_metadata         := jsonb_build_object(
        'old_category_id', old.category_id,
        'new_category_id', new.category_id,
        'old_matched_category_id', old.matched_category_id,
        'new_matched_category_id', new.matched_category_id,
        'new_matched_category_name', new.matched_category_name,
        'new_matched_category_slug', new.matched_category_slug,
        'new_matched_intake_type', new.matched_intake_type,
        'category_override', new.category_override,
        'category_set_at', new.category_set_at
      ),
      p_created_at       := coalesce(new.category_set_at, new.updated_at, now())
    );
  end if;

  return new;
end;
$function$;
revoke all on function public._afe_jsonb_text(jsonb) from public, anon, authenticated;
grant execute on function public._afe_jsonb_text(jsonb) to service_role;
revoke all on function public._afe_build_search_text(text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,jsonb) from public, anon, authenticated;
grant execute on function public._afe_build_search_text(text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,jsonb) to service_role;
revoke all on function public.activity_feed_log(text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,jsonb,timestamptz) from public, anon, authenticated;
grant execute on function public.activity_feed_log(text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,jsonb,timestamptz) to service_role;
revoke all on function public.set_current_timestamp_updated_at() from public,anon,authenticated;
revoke all on function public.set_ticket_categories_updated_at() from public,anon,authenticated;
revoke all on function public.sync_ticket_compat_columns() from public,anon,authenticated;
revoke all on function public.trg_ticket_messages_to_activity_feed() from public,anon,authenticated;
revoke all on function public.trg_ticket_notes_to_activity_feed() from public,anon,authenticated;
revoke all on function public.trg_tickets_insert_to_activity_feed() from public,anon,authenticated;
revoke all on function public.trg_tickets_update_to_activity_feed() from public,anon,authenticated;
create trigger trg_ticket_categories_updated_at before update on public.ticket_categories for each row execute function public.set_ticket_categories_updated_at();
create trigger trg_sync_ticket_compat_columns before insert or update on public.tickets for each row execute function public.sync_ticket_compat_columns();
create trigger set_ticket_panels_updated_at before update on public.ticket_panels for each row execute function public.set_current_timestamp_updated_at();
create trigger set_ticket_panel_rules_updated_at before update on public.ticket_panel_rules for each row execute function public.set_current_timestamp_updated_at();
create trigger set_ticket_panel_presets_updated_at before update on public.ticket_panel_presets for each row execute function public.set_current_timestamp_updated_at();
create trigger set_ticket_panel_categories_updated_at before update on public.ticket_panel_categories for each row execute function public.set_current_timestamp_updated_at();
create trigger ticket_notes_to_activity_feed_ai after insert on public.ticket_notes for each row execute function public.trg_ticket_notes_to_activity_feed();
create trigger ticket_messages_to_activity_feed_ai after insert on public.ticket_messages for each row execute function public.trg_ticket_messages_to_activity_feed();
create trigger tickets_insert_to_activity_feed_ai after insert on public.tickets for each row execute function public.trg_tickets_insert_to_activity_feed();
create trigger tickets_update_to_activity_feed_au after update on public.tickets for each row execute function public.trg_tickets_update_to_activity_feed();

-- Category version-history trigger is intentionally deferred until the
-- audited config-version restore contract is deployed. Do not cut over yet.
