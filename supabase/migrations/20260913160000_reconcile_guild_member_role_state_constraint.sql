-- Reconcile the guild_members.role_state constraint after the canonical runtime
-- schema migration creates/enriches public.guild_members.
--
-- The historical 20260429 migration deliberately skips when guild_members or
-- role_state does not yet exist. Fresh databases therefore need this post-base
-- reconciliation so they converge to the same contract as established ones.
--
-- Existing invalid legacy rows are preserved. A NOT VALID check constraint still
-- protects new writes, and is validated immediately when historical data already
-- satisfies the current short snake_case contract.

begin;

do $$
begin
    if to_regclass('public.guild_members') is null then
        raise notice 'Skipping guild_members role_state reconciliation because public.guild_members does not exist.';
        return;
    end if;

    if not exists (
        select 1
        from information_schema.columns
        where table_schema = 'public'
          and table_name = 'guild_members'
          and column_name = 'role_state'
    ) then
        raise notice 'Skipping guild_members role_state reconciliation because role_state does not exist.';
        return;
    end if;

    alter table public.guild_members
        drop constraint if exists guild_members_role_state_check;

    alter table public.guild_members
        add constraint guild_members_role_state_check
        check (
            role_state is null
            or role_state ~ '^[a-z][a-z0-9_]{0,63}$'
        ) not valid;

    if not exists (
        select 1
        from public.guild_members
        where role_state is not null
          and role_state !~ '^[a-z][a-z0-9_]{0,63}$'
        limit 1
    ) then
        alter table public.guild_members
            validate constraint guild_members_role_state_check;
    else
        raise notice 'guild_members_role_state_check left NOT VALID because legacy rows contain nonconforming role_state values; new writes remain constrained.';
    end if;

    comment on constraint guild_members_role_state_check on public.guild_members is
        'Allows short snake_case role-state labels such as unknown, bot_ok, staff_ok, staff_conflict, verified_ok, verified_conflict, unverified_only, cosmetic_only, and missing_unverified.';
end
$$;

commit;
