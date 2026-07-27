-- ============================================================
-- 202607270001_live_profile_cards_per_member.sql
-- ------------------------------------------------------------
-- Change live-profile ownership from one card per channel to one
-- card per member per channel. Existing channel rows are preserved.
--
-- Safe to run more than once. Service-role-only access and RLS from
-- the original profile migration remain unchanged.
-- ============================================================

do $migration$
declare
    current_primary_key text;
begin
    if to_regclass('public.dank_live_profile_cards') is null then
        raise notice 'Skipping per-member live profile migration: table does not exist.';
        return;
    end if;

    -- Rows created by the old runtime should always have a user id. Remove only
    -- unusable ownership rows before making the column part of the primary key.
    delete from public.dank_live_profile_cards
    where nullif(btrim(user_id), '') is null;

    alter table public.dank_live_profile_cards
        alter column user_id set not null;

    select conname
      into current_primary_key
      from pg_constraint
     where conrelid = 'public.dank_live_profile_cards'::regclass
       and contype = 'p'
     limit 1;

    if current_primary_key is not null then
        execute format(
            'alter table public.dank_live_profile_cards drop constraint %I',
            current_primary_key
        );
    end if;

    alter table public.dank_live_profile_cards
        add constraint dank_live_profile_cards_pkey
        primary key (guild_id, channel_id, user_id);

    create index if not exists idx_dank_live_profile_cards_channel
        on public.dank_live_profile_cards (guild_id, channel_id, updated_at desc);

    comment on table public.dank_live_profile_cards is
        'Service-role-only ownership state for one bot-authored live profile card per member per configured channel.';
end
$migration$;
