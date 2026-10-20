-- All synthetic test rows roll back, never import source queues.
begin;
do $guard$
declare tab text; op text;
begin
foreach tab in array array['bot_commands','bot_operation_jobs'] loop
 if to_regclass('public.'||tab) is null then raise exception 'Missing queue';end if;
 if not (select relrowsecurity from pg_class where oid=to_regclass('public.'||tab))
 then raise exception 'Queue RLS disabled';end if;
 foreach op in array array['SELECT','INSERT','UPDATE','DELETE'] loop
  if has_table_privilege('anon','public.'||tab,op)
   or has_table_privilege('authenticated','public.'||tab,op)
   or not has_table_privilege('service_role','public.'||tab,op)
  then raise exception 'Queue grants leak or missing';end if;
 end loop;
 if has_table_privilege('service_role','public.'||tab,'TRUNCATE')
 then raise exception 'Backend truncate must be denied';end if;
end loop;
if (select count(*) from information_schema.columns where table_schema='public' and table_name='bot_commands')<>11
or (select count(*) from information_schema.columns where table_schema='public' and table_name='bot_operation_jobs')<>19
then raise exception 'Old runtime column parity mismatch';end if;
if exists(select 1 from public.bot_commands limit 1)
or exists(select 1 from public.bot_operation_jobs limit 1)
then raise exception 'Historical queue replay risk';end if;
end $guard$;
set local role service_role;
insert into public.bot_commands(guild_id,action)
values('990000000000000000','sync_members'),('990000000000000001','sync_members');
do $claim$
declare task_id uuid;
begin
select id into task_id from public.bot_commands
where guild_id='990000000000000000' and status='pending'
order by created_at asc limit 1;
if task_id is null then raise exception 'Pending command lookup broken';end if;
update public.bot_commands set status='processing',picked_up_at=now()
where id=task_id and status='pending';
update public.bot_commands set status='completed',completed_at=now()
where id=task_id and status='processing';
if (select count(*) from public.bot_commands where guild_id='990000000000000000' and status='completed')<>1
or (select count(*) from public.bot_commands where guild_id='990000000000000001' and status='pending')<>1
then raise exception 'Per-guild command execution isolation broken';end if;
end $claim$;
insert into public.bot_operation_jobs
 (guild_id,operation_type,risk_level,source,idempotency_key,payload_hash,status)
values ('990000000000000000','test','safe','system','dedup-test','hash','queued');
do $idempotency$
declare unique_rejected boolean:=false;
begin
begin
 insert into public.bot_operation_jobs
 (guild_id,operation_type,risk_level,source,idempotency_key,payload_hash,status)
 values ('990000000000000000','test','safe','system','dedup-test','hash','queued');
exception when unique_violation then unique_rejected:=true;
end;
if not unique_rejected then raise exception 'Operation idempotency failed';end if;
update public.bot_operation_jobs set status='succeeded',finished_at=now()
where guild_id='990000000000000000' and idempotency_key='dedup-test';
if (select count(*) from public.bot_operation_jobs where status='succeeded')<>1
then raise exception 'Operation completion transition broken';end if;
end $idempotency$;
reset role;
rollback;
