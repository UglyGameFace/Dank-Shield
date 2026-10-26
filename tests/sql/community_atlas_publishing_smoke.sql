-- Disposable PG16 fixture: host consent, private edit credentials and revocation.
begin;
insert into public.dank_community_hub_settings (
  guild_id, enabled, maintenance_mode, public_lfg_enabled,
  public_lfg_invite_url, public_lfg_display_name
) values (
  '111111111111111111',true,false,false,
  'https://discord.gg/validcode','Sample Gaming Guild'
);
insert into public.dank_community_sessions (
  guild_id,idempotency_key,game_name,privacy,state,public_lfg_opt_in,host_id
) values (
  '111111111111111111','pub-atlas-1','Minecraft','public','open',
  false,'222222222222222222'
);
insert into public.dank_community_session_members(
  session_id,guild_id,user_id,role
) select id,guild_id,'222222222222222222','host'
from public.dank_community_sessions where idempotency_key='pub-atlas-1';

do $$
declare sid uuid;
begin
  select id into sid from public.dank_community_sessions
    where idempotency_key='pub-atlas-1';
  if exists(select 1 from public.dank_queue_atlas_publication(
    sid,'111111111111111111','222222222222222222','NA',2
  )) then raise exception 'published with both consent gates disabled'; end if;
end;
$$;
update public.dank_community_hub_settings
  set public_lfg_enabled=true where guild_id='111111111111111111';
update public.dank_community_sessions
  set public_lfg_opt_in=true where guild_id='111111111111111111';

do $$
declare sid uuid; count_first int;
begin
  select id into sid from public.dank_community_sessions
    where idempotency_key='pub-atlas-1';
  if exists(select 1 from public.dank_queue_atlas_publication(
    sid,'111111111111111111','333333333333333333','NA',2
  )) then raise exception 'stranger can publish another member session'; end if;
  select count(*) into count_first from public.dank_queue_atlas_publication(
    sid,'111111111111111111','222222222222222222','NA',2
  );
  if count_first <> 1 then raise exception 'host publication not queued'; end if;
  if exists(select 1 from public.dank_queue_atlas_publication(
    sid,'111111111111111111','222222222222222222','NA',2
  )) then raise exception 'duplicate queued an overlapping publication'; end if;
  if (select state from public.dank_claim_atlas_publications(2)) <> 'publishing'
    then raise exception 'queued publication not claimed'; end if;
  if exists(select 1 from public.dank_claim_atlas_publications(2))
    then raise exception 'publication claimed twice before lease expiry'; end if;
end;
$$;

-- Simulate a confirmed external post to test automatic revocation claim.
update public.dank_community_atlas_publications
  set state='posted', remote_id='remote-post-1234',
      edit_token='private-test-token-1234567',
      remote_url='https://lfgatlas.pages.dev/p/remote-post-1234',
      lease_until=null, claim_version=claim_version+1;
update public.dank_community_sessions
  set public_lfg_opt_in=false where guild_id='111111111111111111';

do $$
declare cnt int;
begin
  if has_table_privilege('anon','public.dank_community_atlas_publications','select')
    then raise exception 'anonymous role sees post credentials'; end if;
  if has_function_privilege('authenticated',
     'public.dank_queue_atlas_publication(uuid,text,text,text,integer)','execute')
    then raise exception 'member can bypass host checks by calling publish RPC'; end if;
  select count(*) into cnt from public.dank_claim_atlas_publications(2)
    where state='removing';
  if cnt <> 1 then raise exception 'revoked consent did not claim deletion'; end if;
  if exists(select 1 from public.dank_claim_atlas_publications(2))
    then raise exception 'removing post claimed twice'; end if;
end;
$$;

-- A crash during POST is intentionally marked unknown by the worker.
-- It MUST NOT be picked up for another publishing attempt.
update public.dank_community_atlas_publications
  set state='publishing', lease_until=now()-interval '1 minute';
do $$
begin
  if exists(select 1 from public.dank_claim_atlas_publications(2))
    then raise exception 'stale uncertain POST automatically repeated'; end if;
end;
$$;
rollback;
