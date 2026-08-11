-- ============================================================
-- Production preflight for ticket category setup selection v2.
--
-- The already-pending 20260802042000 migration introduces two managed
-- routing values, cod_services and game_services. Older production schemas
-- still enforce ticket_categories_intake_type_check from 202605011835,
-- which does not allow those values. That made reconciliation fail before
-- the migration could be recorded.
--
-- This migration intentionally sorts immediately before 20260802042000 so
-- production expands the constraint first. It is idempotent and preserves
-- every previously allowed intake type.
-- ============================================================

begin;

do $$
begin
    if to_regclass('public.ticket_categories') is null then
        raise notice 'Skipping intake constraint preflight: public.ticket_categories does not exist.';
        return;
    end if;

    if not exists (
        select 1
        from information_schema.columns
        where table_schema = 'public'
          and table_name = 'ticket_categories'
          and column_name = 'intake_type'
    ) then
        raise notice 'Skipping intake constraint preflight: intake_type does not exist.';
        return;
    end if;

    alter table public.ticket_categories
        drop constraint if exists ticket_categories_intake_type_check;

    alter table public.ticket_categories
        add constraint ticket_categories_intake_type_check
        check (
            intake_type is null
            or intake_type = any (array[
                'general', 'support', 'verification', 'appeal', 'report',
                'question', 'partnership', 'account', 'purchase', 'billing',
                'refund', 'technical', 'bug', 'staff', 'ghost', 'custom',
                'other', 'cod_services', 'game_services'
            ]::text[])
        );
end
$$;

commit;
