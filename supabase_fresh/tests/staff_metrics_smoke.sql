-- Never use against the live old Supabase. All test rows roll back.
begin;
do $tests$
declare
    present_count integer;
    second_guild_count integer;
    newest text;
begin
    if exists (
       select 1 from public.staff_metrics where guild_id in ('990000000000000000','990000000000000001')
    ) then
        raise exception 'Synthetic guild IDs unexpectedly present';
    end if;
    insert into public.staff_metrics(guild_id,staff_id,staff_name)
       select '990000000000000000', g.n::text, 'Staff ' || g.n
       from generate_series(990000000000000000::bigint,990000000000000029::bigint) as g(n);
    insert into public.staff_metrics(guild_id,staff_id,staff_name)
       values ('990000000000000001','990000000000000000','Independent Guild Staff');

    -- Exact existing metrics worker upsert conflict contract.
    insert into public.staff_metrics(guild_id,staff_id,staff_name)
       values ('990000000000000000','990000000000000000','Renamed Staff')
       on conflict (guild_id,staff_id) do update set staff_name=excluded.staff_name;
    select count(*) into present_count from public.staff_metrics
       where guild_id='990000000000000000';
    select count(*) into second_guild_count from public.staff_metrics
       where guild_id='990000000000000001';
    select staff_name into newest from public.staff_metrics
       where guild_id='990000000000000000' and staff_id='990000000000000000';
    if present_count<>30 or second_guild_count<>1 or newest<>'Renamed Staff' then
        raise exception 'Cross guild isolation or worker upsert failed';
    end if;

    update public.staff_metrics set tickets_handled=3,approvals=2,denials=1,
      avg_response_minutes=15,last_active=now()
      where guild_id='990000000000000000' and staff_id='990000000000000000';
    if not exists (
       select 1 from public.staff_metrics where guild_id='990000000000000000'
       and staff_id='990000000000000000' and tickets_handled=3
       and approvals=2 and denials=1 and avg_response_minutes=15
       and last_active is not null
    ) then raise exception 'Ticket workspace staff report fields not persisted'; end if;

    if has_table_privilege('anon','public.staff_metrics','SELECT')
        or has_table_privilege('authenticated','public.staff_metrics','SELECT')
        or has_table_privilege('authenticated','public.staff_metrics','INSERT')
        or not has_table_privilege('service_role','public.staff_metrics','SELECT')
        or not has_table_privilege('service_role','public.staff_metrics','INSERT') then
        raise exception 'Service-role-only table access was violated';
    end if;
    if not (select relrowsecurity from pg_class where oid='public.staff_metrics'::regclass) then
        raise exception 'RLS missing on staff_metrics';
    end if;
end $tests$;
rollback;
