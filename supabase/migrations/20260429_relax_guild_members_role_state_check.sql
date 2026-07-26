-- Relax guild_members.role_state so the bot can evolve role-state labels
-- without breaking member sync every time a new safe state is introduced.
--
-- Old deployments may only allow a small fixed set and reject newer values
-- like `cosmetic_only`. This keeps the column constrained to short snake_case
-- labels while avoiding repeated migrations for every future role-state value.
--
-- The legacy guild_members table is not created by every fresh deployment.
-- Alter it only when both the table and its role_state column are present.

do $$
begin
    if to_regclass('public.guild_members') is null then
        raise notice 'Skipping guild_members role_state relaxation because public.guild_members does not exist.';
    elsif not exists (
        select 1
        from information_schema.columns
        where table_schema = 'public'
          and table_name = 'guild_members'
          and column_name = 'role_state'
    ) then
        raise notice 'Skipping guild_members role_state relaxation because role_state does not exist.';
    else
        execute 'alter table public.guild_members drop constraint if exists guild_members_role_state_check';
        execute $ddl$
            alter table public.guild_members
            add constraint guild_members_role_state_check
            check (
                role_state is null
                or role_state ~ '^[a-z][a-z0-9_]{0,63}$'
            )
        $ddl$;
        execute $comment$
            comment on constraint guild_members_role_state_check on public.guild_members is
            'Allows short snake_case role-state labels such as unknown, bot_ok, staff_ok, staff_conflict, verified_ok, verified_conflict, unverified_only, cosmetic_only, and missing_unverified.'
        $comment$;
    end if;
end
$$;
