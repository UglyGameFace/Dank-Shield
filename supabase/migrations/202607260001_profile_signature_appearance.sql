-- ============================================================
-- 202607260001_profile_signature_appearance.sql
-- ------------------------------------------------------------
-- Member-owned appearance for compact profile signatures.
--
-- This is intentionally separate from guild-owned welcome-card settings.
-- The existing profile table remains service-role-only with RLS enabled.
-- Safe to run more than once.
-- ============================================================

alter table if exists public.dank_profile_users
    add column if not exists appearance jsonb not null default '{}'::jsonb;

do $$
begin
    if to_regclass('public.dank_profile_users') is not null then
        if not exists (
            select 1
            from pg_constraint
            where conname = 'dank_profile_users_appearance_object'
              and conrelid = 'public.dank_profile_users'::regclass
        ) then
            alter table public.dank_profile_users
                add constraint dank_profile_users_appearance_object
                check (jsonb_typeof(appearance) = 'object');
        end if;

        alter table public.dank_profile_users enable row level security;
        revoke all on table public.dank_profile_users from anon, authenticated;

        comment on column public.dank_profile_users.appearance is
            'Service-role-only member-owned compact profile signature appearance; separate from guild welcome cards.';
    end if;
end
$$;
