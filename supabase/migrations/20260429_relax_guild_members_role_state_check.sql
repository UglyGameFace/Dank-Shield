-- Relax guild_members.role_state so the bot can evolve role-state labels
-- without breaking member sync every time a new safe state is introduced.
--
-- Old deployments may only allow a small fixed set and reject newer values
-- like `cosmetic_only`. This keeps the column constrained to short snake_case
-- labels while avoiding repeated migrations for every future role-state value.

ALTER TABLE public.guild_members
DROP CONSTRAINT IF EXISTS guild_members_role_state_check;

ALTER TABLE public.guild_members
ADD CONSTRAINT guild_members_role_state_check
CHECK (
  role_state IS NULL
  OR role_state ~ '^[a-z][a-z0-9_]{0,63}$'
);

COMMENT ON CONSTRAINT guild_members_role_state_check ON public.guild_members IS
'Allows short snake_case role-state labels such as unknown, bot_ok, staff_ok, staff_conflict, verified_ok, verified_conflict, unverified_only, cosmetic_only, and missing_unverified.';
