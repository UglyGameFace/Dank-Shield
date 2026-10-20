-- Target ONLY new DankShield Supabase project viamriafkbqhquvrsnua.
-- Attach runtime bookkeeping and guild-scoped join auditing *after* data import,
-- to avoid inventing 165 events for old member-join snapshots.
-- Preserve runtime behavior with hardened function search paths.

CREATE OR REPLACE FUNCTION public.set_updated_at()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
begin
    new.updated_at = now();
    return new;
end;
$function$;
alter function public.set_updated_at() set search_path = pg_catalog, public, pg_temp;
revoke all on function public.set_updated_at() from public, anon, authenticated;
grant execute on function public.set_updated_at() to service_role;

CREATE OR REPLACE FUNCTION public.sync_vc_verify_sessions_updated_at()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
begin
  new.updated_at := now();
  new.meta := coalesce(new.meta, '{}'::jsonb);
  return new;
end;
$function$;
alter function public.sync_vc_verify_sessions_updated_at() set search_path = pg_catalog, public, pg_temp;
revoke all on function public.sync_vc_verify_sessions_updated_at() from public, anon, authenticated;
grant execute on function public.sync_vc_verify_sessions_updated_at() to service_role;

CREATE OR REPLACE FUNCTION public.trg_member_joins_to_activity_feed()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
begin
  perform public.activity_feed_log(
    p_guild_id         := new.guild_id,
    p_event_family     := 'member',
    p_event_type       := 'member_join',
    p_source           := 'discord',
    p_actor_user_id    := null,
    p_actor_name       := null,
    p_target_user_id   := new.user_id,
    p_target_name      := coalesce(new.display_name, new.username, new.user_id),
    p_related_table    := 'member_joins',
    p_related_id       := new.id::text,
    p_title            := 'Member Joined',
    p_description      := coalesce(new.display_name, new.username, 'Unknown Member') || ' joined the server',
    p_reason           := new.join_note,
    p_metadata         := jsonb_build_object(
      'username', new.username,
      'display_name', new.display_name,
      'avatar_url', new.avatar_url,
      'joined_at', new.joined_at,
      'invited_by', new.invited_by,
      'invited_by_name', new.invited_by_name,
      'invite_code', new.invite_code,
      'entry_method', new.entry_method,
      'verification_source', new.verification_source,
      'vouched_by', new.vouched_by,
      'vouched_by_name', new.vouched_by_name,
      'approved_by', new.approved_by,
      'approved_by_name', new.approved_by_name,
      'source_ticket_id', new.source_ticket_id,
      'join_note', new.join_note
    ),
    p_created_at       := coalesce(new.joined_at, new.created_at, now())
  );
  return new;
end;
$function$;
alter function public.trg_member_joins_to_activity_feed() set search_path = pg_catalog, public, pg_temp;
revoke all on function public.trg_member_joins_to_activity_feed() from public, anon, authenticated;
grant execute on function public.trg_member_joins_to_activity_feed() to service_role;
create trigger trg_identity_proofs_updated_at before update on public.identity_proofs for each row execute function public.set_updated_at();
create trigger trg_manual_alt_links_updated_at before update on public.manual_alt_links for each row execute function public.set_updated_at();
create trigger trg_verification_kick_timers_updated_at before update on public.verification_kick_timers for each row execute function public.set_updated_at();
create trigger trg_verification_tokens_updated_at before update on public.verification_tokens for each row execute function public.set_updated_at();
create trigger trg_sync_vc_verify_sessions_updated_at before insert or update on public.vc_verify_sessions for each row execute function public.sync_vc_verify_sessions_updated_at();
create trigger member_joins_to_activity_feed_ai after insert on public.member_joins for each row execute function public.trg_member_joins_to_activity_feed();
