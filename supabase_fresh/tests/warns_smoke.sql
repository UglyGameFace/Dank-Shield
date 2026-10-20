-- Synthetic warning records, rollback only, offline dedicated candidate.
begin;
do $contract$
begin
 if to_regclass('public.warns') is null then raise exception 'Missing source warns table';end if;
 if not (select relrowsecurity from pg_class where oid='public.warns'::regclass)
 then raise exception 'Warns RLS disabled';end if;
 if (select pg_get_constraintdef(oid) from pg_constraint
  where conrelid='public.warns'::regclass and contype='p') <> 'PRIMARY KEY (id)' then
  raise exception 'Wrong UUID warning primary key';end if;
 if (select count(*) from information_schema.columns
  where table_schema='public' and table_name='warns')<>7 then
  raise exception 'Wrong number of source warning columns';end if;
 if (select count(*) from information_schema.columns
  where table_schema='public' and table_name='warns'
    and column_name in ('id','guild_id','user_id','username',
                        'reason','source_message','created_at'))<>7 then
  raise exception 'Missing source warning field';end if;
 if exists(select 1 from information_schema.columns
  where table_schema='public' and table_name='warns'
  and column_name in ('username','source_message','created_at')
  and is_nullable='NO') then
  raise exception 'Optional warning column made mandatory';end if;
 if (select count(*) from information_schema.columns
  where table_schema='public' and table_name='warns'
  and column_name in ('guild_id','user_id','reason') and is_nullable='NO')<>3
 then raise exception 'Required warning columns altered';end if;
 if has_table_privilege('anon','public.warns','SELECT')
 or has_table_privilege('authenticated','public.warns','INSERT')
 or has_table_privilege('service_role','public.warns','TRUNCATE')
 or not has_table_privilege('service_role','public.warns','INSERT')
 then raise exception 'Warning grants leaked';end if;
 if not exists(select 1 from pg_indexes where schemaname='public'
  and tablename='warns' and indexname='warns_guild_user_created_idx')
 then raise exception 'Warning count index missing';end if;
end $contract$;
set local role service_role;
insert into public.warns(guild_id,user_id,reason,username) values
 ('990000000000000000','990000000000000010','synthetic only',null),
 ('990000000000000000','990000000000000010','synthetic only 2','Test'),
 ('990000000000000001','990000000000000010','other guild',null);
do $isolation$
declare n integer;m integer;
begin
 select count(*) into n from public.warns
 where guild_id='990000000000000000' and user_id='990000000000000010';
 select count(*) into m from public.warns
 where guild_id='990000000000000001' and user_id='990000000000000010';
 if n<>2 or m<>1 then raise exception 'Wrong isolated modlog warning counts';end if;
end $isolation$;
reset role;
rollback;
