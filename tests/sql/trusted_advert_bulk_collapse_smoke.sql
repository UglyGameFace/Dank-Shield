-- Disposable Postgres-16 job integration test. Never run on production.
begin;

do $$
begin
  if has_table_privilege(
    'anon', 'public.dank_trusted_advert_collapse_jobs', 'select'
  ) then
    raise exception 'anonymous role can read private collapse state';
  end if;
  if has_function_privilege(
    'authenticated',
    'public.dank_claim_trusted_embed_jobs(integer)', 'execute'
  ) then
    raise exception 'authenticated role can claim moderation jobs';
  end if;
end;
$$;

do $$
declare started integer;
begin
  select count(*) into started
  from public.dank_start_trusted_embed_job(
    '111111111111111111','222222222222222222',
    '333333333333333333','444444444444444444',
    '777777777777777778',false
  );
  if started <> 1 then
    raise exception 'first job did not start';
  end if;
  select count(*) into started
  from public.dank_start_trusted_embed_job(
    '111111111111111111','222222222222222222',
    '333333333333333333','444444444444444444',
    null,false
  );
  if started <> 0 then
    raise exception 'duplicate start reset a running job';
  end if;
end;
$$;

do $$
declare first_version integer;
begin
  select claim_version into first_version
  from public.dank_claim_trusted_embed_jobs(1);
  if first_version <> 1 then
    raise exception 'first claim should have version 1';
  end if;
  if exists(select 1 from public.dank_claim_trusted_embed_jobs(1)) then
    raise exception 'in-flight job claimed twice';
  end if;
  update public.dank_trusted_advert_collapse_jobs
    set scanned=25,collapsed=6,skipped=19,
        cursor_before='777777777777777750',
        lease_until=null
    where guild_id='111111111111111111'
      and channel_id='222222222222222222'
      and bot_id='333333333333333333' and claim_version=1;
  if (select claim_version from public.dank_claim_trusted_embed_jobs(1)) <> 2 then
    raise exception 'resume did not acquire new version';
  end if;
end;
$$;

update public.dank_trusted_advert_collapse_jobs
set status='cancelled', lease_until=null
where guild_id='111111111111111111'
  and channel_id='222222222222222222'
  and bot_id='333333333333333333';

do $$
declare row_count integer;
begin
  if exists(select 1 from public.dank_claim_trusted_embed_jobs(1)) then
    raise exception 'cancelled bulk job was reclaimed';
  end if;
  select count(*) into row_count from public.dank_start_trusted_embed_job(
    '111111111111111111','222222222222222222',
    '333333333333333333','444444444444444444',null,true
  );
  if row_count <> 1 then
    raise exception 'staff could not restart a cancelled job';
  end if;
  if (select scanned from public.dank_trusted_advert_collapse_jobs) <> 0 then
    raise exception 'new job kept stale counters';
  end if;
  if (select manual_only from public.dank_trusted_advert_collapse_jobs) is not true then
    raise exception 'explicit manual-only consent lost';
  end if;
end;
$$;

rollback;
