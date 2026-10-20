-- Dedicated NEW DankShield project ONLY; apply after 011.
-- Operational scheduling is DB-side, not another bot worker or startup task.
create extension if not exists pg_cron with schema pg_catalog;
select cron.schedule(
  'dankshield-expired-verification-tokens',
  '17 * * * *',
  $$select dank_maintenance.purge_expired_verification_tokens(500);$$
);
-- pg_cron never expires its own job-run history automatically.
select cron.schedule(
  'dankshield-cron-run-log-retention',
  '33 3 * * 0',
  $$delete from cron.job_run_details where end_time < now() - interval '14 days';$$
);
