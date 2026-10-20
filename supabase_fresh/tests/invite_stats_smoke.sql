-- New dedicated candidate ONLY; synthetic event data always rolled back.
begin;
do $smoke$
declare
 was_applied boolean;
 total bigint;
begin
 if exists(select 1 from public.dank_invite_block_stats
   where guild_id in ('990000000000000000','990000000000000001')) then
  raise exception 'Synthetic guild unexpectedly exists';
 end if;
 select applied,invites_blocked into was_applied,total
 from public.record_dank_invite_block_event(repeat('a',64),'990000000000000000',3,5,'create:test');
 if not was_applied or total <> 8 then raise exception 'first event should be 8';end if;
 select applied,invites_blocked into was_applied,total
 from public.record_dank_invite_block_event(repeat('a',64),'990000000000000000',3,5,'edit:test');
 if was_applied or total <> 8 then raise exception 'message replay double-counted';end if;
 select applied,invites_blocked into was_applied,total
 from public.record_dank_invite_block_event(repeat('a',64),'990000000000000001',3,7,'other:guild');
 if was_applied or total <> 7 then raise exception 'replayed event leaked into another guild';end if;
 select applied,invites_blocked into was_applied,total
 from public.record_dank_invite_block_event(repeat('b',64),'990000000000000001',2,0,'create:test');
 if not was_applied or total <> 9 then raise exception 'second guild should be 9';end if;
 select applied,invites_blocked into was_applied,total
 from public.record_dank_invite_block_event(repeat('c',64),'990000000000000000',2,0,'fallback:test');
 if not was_applied or total <> 10 then raise exception 'first guild should be 10';end if;
 if (select count(*) from public.dank_invite_block_events
   where guild_id in ('990000000000000000','990000000000000001')) <> 3 then
  raise exception 'event hash dedupe count incorrect';end if;
 if has_function_privilege('anon','public.record_dank_invite_block_event(text,text,integer,bigint,text)','EXECUTE')
   or has_function_privilege('authenticated','public.record_dank_invite_block_event(text,text,integer,bigint,text)','EXECUTE')
   or not has_function_privilege('service_role','public.record_dank_invite_block_event(text,text,integer,bigint,text)','EXECUTE')
   or has_table_privilege('anon','public.dank_invite_block_events','SELECT')
   or has_table_privilege('authenticated','public.dank_invite_block_stats','INSERT') then
   raise exception 'RPC/table exposure detected';
 end if;
 if not (select relrowsecurity from pg_class
   where oid='public.dank_invite_block_events'::regclass) then
   raise exception 'event ledger RLS missing';end if;
 begin
  perform public.record_dank_invite_block_event('not-hex','bad',1,0,'test');
  raise exception 'TEST invalid hash accepted';
 exception when raise_exception then
  if sqlerrm='TEST invalid hash accepted' then raise;end if;
 end;
 begin
  perform public.record_dank_invite_block_event(repeat('d',64),'990000000000000000',0,0,'test');
  raise exception 'TEST zero count accepted';
 exception when raise_exception then
  if sqlerrm='TEST zero count accepted' then raise;end if;
 end;
end $smoke$;
rollback;
