-- ============================================================
-- Durable managed ticket-category catalog and reconciliation.
--
-- Safety guarantees:
-- - Creates the required ticket_categories table when it is absent.
-- - Adopts only exact, reserved Dank Shield aliases; broad substring matching
--   is intentionally forbidden so owner-created custom categories survive.
-- - Deletes only duplicate rows that are already managed or exact reserved
--   legacy aliases for the same managed catalog key.
-- - The uniqueness rule applies only to rows explicitly managed by Dank Shield.
-- - Safe to rerun.
-- ============================================================

create extension if not exists pgcrypto;

create table if not exists public.ticket_categories (
    id uuid primary key default gen_random_uuid(),
    guild_id text not null,
    slug text not null,
    name text not null,
    description text,
    intake_type text not null default 'general',
    match_keywords jsonb not null default '[]'::jsonb,
    button_label text,
    sort_order integer not null default 999,
    is_default boolean not null default false,
    is_enabled boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- CREATE TABLE IF NOT EXISTS does not reconcile an older, smaller table.
alter table public.ticket_categories
    add column if not exists slug text,
    add column if not exists name text,
    add column if not exists description text,
    add column if not exists intake_type text not null default 'general',
    add column if not exists match_keywords jsonb not null default '[]'::jsonb,
    add column if not exists button_label text,
    add column if not exists sort_order integer not null default 999,
    add column if not exists is_default boolean not null default false,
    add column if not exists is_enabled boolean not null default true,
    add column if not exists created_at timestamptz not null default now(),
    add column if not exists updated_at timestamptz not null default now(),
    add column if not exists managed_by_dank boolean not null default false,
    add column if not exists managed_catalog_version integer,
    add column if not exists managed_category_key text;

create index if not exists idx_ticket_categories_guild_sort
    on public.ticket_categories (guild_id, sort_order);

create or replace function public.dank_ticket_category_key(p_slug text, p_name text default null)
returns text
language sql
immutable
as $$
with normalized as (
    select
        trim(both '-' from regexp_replace(lower(btrim(coalesce(p_slug, ''))), '[^a-z0-9]+', '-', 'g')) as slug_key,
        btrim(regexp_replace(lower(btrim(coalesce(p_name, ''))), '[^a-z0-9]+', ' ', 'g')) as name_key
)
select case
    when slug_key in ('verification', 'verification-help', 'verification-issue', 'verify')
      or name_key in ('verification', 'verification help', 'verification issue', 'verify', 'verify help')
        then 'verification'
    when slug_key in ('account-access', 'account-help')
      or name_key in ('account access', 'account help', 'account access help')
        then 'account-access'
    when slug_key in ('payments-refunds', 'payment-refund', 'purchase-refund')
      or name_key in ('payments refunds', 'payment refund', 'purchase refund', 'payments and refunds')
        then 'payments-refunds'
    when slug_key in ('appeal', 'appeals')
      or name_key in ('appeal', 'appeals')
        then 'appeal'
    when slug_key in ('report', 'reports', 'user-report')
      or name_key in ('report', 'reports', 'user report', 'user reports')
        then 'report'
    when slug_key in ('staff-complaint', 'staff-report')
      or name_key in ('staff complaint', 'staff complaints', 'staff report')
        then 'staff-complaint'
    when slug_key in ('bug', 'bug-report', 'technical-support', 'bug-technical-support')
      or name_key in ('bug', 'bug report', 'technical support', 'bug technical support')
        then 'bug'
    when slug_key in ('cod-services', 'cod-service')
      or name_key in ('cod services', 'cod service', 'call of duty', 'call of duty services')
        then 'cod-services'
    when slug_key in ('service-request', 'service-requests')
      or name_key in ('service request', 'service requests')
        then 'service-request'
    when slug_key in ('vouch-referral', 'vouch-invite-referral')
      or name_key in ('vouch referral', 'vouch invite referral', 'vouch invite referral issues')
        then 'vouch-referral'
    when slug_key in ('giveaway-reward', 'giveaway-rewards')
      or name_key in ('giveaway reward', 'giveaway rewards', 'giveaway reward issues')
        then 'giveaway-reward'
    when slug_key in ('content-media', 'content-media-request')
      or name_key in ('content media', 'content media request', 'content media requests')
        then 'content-media'
    when slug_key in ('partnership', 'partnerships')
      or name_key in ('partnership', 'partnerships')
        then 'partnership'
    when slug_key in ('question', 'questions')
      or name_key in ('question', 'questions')
        then 'question'
    when slug_key in ('custom', 'other', 'general', 'general-support', 'support')
      or name_key in ('custom', 'other', 'general', 'general support', 'support')
        then 'support'
    else null
end
from normalized;
$$;

create or replace function public.dank_ticket_category_catalog()
returns table(
    category_key text, slug text, name text, description text,
    intake_type text, match_keywords jsonb, sort_order integer,
    is_default boolean, catalog_version integer
)
language sql
immutable
as $$ values
('verification','verification_issue','Verification','Verification help, secure upload, VC verify, selfie, or approval issues.','verification','["verification","verify","unverified","secure upload","vc verify","selfie","approval"]'::jsonb,1,false,1),
('account-access','account_access','Account / Access','Account access, login, hacked account, email, password, and 2FA issues.','account','["account","login","email","password","2fa","locked out","hacked","compromised"]'::jsonb,2,false,1),
('payments-refunds','payments_refunds','Payments / Refunds','Payments, orders, receipts, invoices, refunds, and chargebacks.','purchase','["payment","purchase","refund","chargeback","receipt","invoice","order"]'::jsonb,3,false,1),
('appeal','appeal','Appeals','Appeals for bans, kicks, blacklists, warns, timeouts, and punishments.','appeal','["appeal","ban appeal","unban","kick appeal","timeout appeal","warn appeal"]'::jsonb,4,false,1),
('report','report','Reports','User reports, scams, abuse, threats, harassment, raids, and rulebreaking.','report','["report","scam","abuse","harassment","threat","raid","spam","rule break"]'::jsonb,5,false,1),
('staff-complaint','staff_complaint','Staff Complaint','Complaints or escalation requests involving staff or moderator behavior.','report','["staff complaint","staff issue","staff abuse","moderator report","admin report"]'::jsonb,6,false,1),
('bug','technical_support','Bug / Technical Support','Site bugs, panel problems, bot issues, broken flows, and technical failures.','bug','["bug","broken","not working","error","glitch","failed","technical support"]'::jsonb,7,false,1),
('cod-services','cod_services','COD Services','Call of Duty lobbies, recoveries, unlock all, zombies rank, and related service requests.','custom','["cod","call of duty","lobby","recovery","unlock all","warzone","black ops","modern warfare","zombies"]'::jsonb,8,false,1),
('service-request','service_request','Service Requests','General service requests, carries, boosts, recoveries, and fulfillment questions.','custom','["service","boost","carry","recovery service","unlock service","rank help"]'::jsonb,9,false,1),
('vouch-referral','vouch_referral','Vouch / Invite / Referral','Invite credit, referral rewards, vouch issues, and who-invited-who questions.','custom','["vouch","invite","invite credit","referral","referrer","invite reward"]'::jsonb,10,false,1),
('giveaway-reward','giveaway_reward','Giveaway / Reward Issues','Giveaway prizes, missing rewards, winner disputes, and reward claims.','custom','["giveaway","reward","prize","claim prize","missing prize","winner issue"]'::jsonb,11,false,1),
('content-media','content_media','Content / Media Requests','Graphics, thumbnails, banners, content requests, media edits, and promo assets.','custom','["content","media","graphic","design","editing","video","thumbnail","banner"]'::jsonb,12,false,1),
('partnership','partnership','Partnerships','Partnerships, sponsorships, collaborations, and promotions.','partnership','["partnership","partner","collab","collaboration","sponsor","promotion"]'::jsonb,13,false,1),
('question','question','Questions','General questions and how-to requests.','question','["question","questions","how to","how do i"]'::jsonb,14,false,1),
('support','support','Support','General support fallback for anything that does not fit a more specific category.','general','["support","help","general support","assistance"]'::jsonb,999,true,1);
$$;

create or replace function public.reconcile_dank_ticket_categories(p_guild_id text default null)
returns table(guild_id text, inserted_count integer, updated_count integer, deleted_duplicate_count integer)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    g record;
    c record;
    winner_id text;
    affected integer;
    inserted_n integer;
    updated_n integer;
    deleted_n integer;
begin
    create temporary table if not exists pg_temp.dank_reconcile_guilds (
        guild_id text primary key
    ) on commit drop;
    truncate table pg_temp.dank_reconcile_guilds;

    if nullif(btrim(p_guild_id), '') is not null then
        insert into pg_temp.dank_reconcile_guilds(guild_id)
        values (btrim(p_guild_id))
        on conflict do nothing;
    else
        insert into pg_temp.dank_reconcile_guilds(guild_id)
        select distinct tc.guild_id::text
        from public.ticket_categories tc
        where nullif(btrim(tc.guild_id::text), '') is not null
        on conflict do nothing;

        if to_regclass('public.guild_configs') is not null then
            execute $guilds$
                insert into pg_temp.dank_reconcile_guilds(guild_id)
                select distinct gc.guild_id::text
                from public.guild_configs gc
                where nullif(btrim(gc.guild_id::text), '') is not null
                on conflict do nothing
            $guilds$;
        end if;
    end if;

    for g in select rg.guild_id from pg_temp.dank_reconcile_guilds rg order by rg.guild_id loop
        inserted_n := 0;
        updated_n := 0;
        deleted_n := 0;

        for c in select * from public.dank_ticket_category_catalog() loop
            winner_id := null;

            select tc.id::text
              into winner_id
              from public.ticket_categories tc
             where tc.guild_id::text = g.guild_id
               and (
                    (tc.managed_by_dank = true and tc.managed_category_key = c.category_key)
                    or
                    (tc.managed_by_dank = false
                     and public.dank_ticket_category_key(tc.slug, tc.name) = c.category_key)
               )
             order by
                    case when tc.managed_by_dank then 0 else 1 end,
                    case when tc.slug = c.slug then 0 else 1 end,
                    coalesce(tc.sort_order, 9999),
                    tc.created_at nulls last,
                    tc.id
             limit 1;

            if winner_id is not null then
                -- Remove only managed duplicates or exact reserved legacy aliases.
                -- Unknown custom rows return NULL from dank_ticket_category_key()
                -- and can never enter this delete path.
                delete from public.ticket_categories tc
                 where tc.guild_id::text = g.guild_id
                   and tc.id::text <> winner_id
                   and (
                        (tc.managed_by_dank = true and tc.managed_category_key = c.category_key)
                        or
                        (tc.managed_by_dank = false
                         and public.dank_ticket_category_key(tc.slug, tc.name) = c.category_key)
                   );
                get diagnostics affected = row_count;
                deleted_n := deleted_n + affected;

                update public.ticket_categories tc
                   set slug = c.slug,
                       name = c.name,
                       description = c.description,
                       intake_type = c.intake_type,
                       match_keywords = c.match_keywords,
                       sort_order = c.sort_order,
                       is_default = c.is_default,
                       is_enabled = true,
                       managed_by_dank = true,
                       managed_catalog_version = c.catalog_version,
                       managed_category_key = c.category_key,
                       updated_at = now()
                 where tc.id::text = winner_id
                   and (tc.slug, tc.name, tc.description, tc.intake_type, tc.match_keywords,
                        tc.sort_order, tc.is_default, tc.is_enabled, tc.managed_by_dank,
                        tc.managed_catalog_version, tc.managed_category_key)
                       is distinct from
                       (c.slug, c.name, c.description, c.intake_type, c.match_keywords,
                        c.sort_order, c.is_default, true, true, c.catalog_version,
                        c.category_key);
                get diagnostics affected = row_count;
                updated_n := updated_n + affected;
            else
                insert into public.ticket_categories(
                    guild_id, slug, name, description, intake_type, match_keywords,
                    sort_order, is_default, is_enabled, managed_by_dank,
                    managed_catalog_version, managed_category_key
                ) values (
                    g.guild_id, c.slug, c.name, c.description, c.intake_type,
                    c.match_keywords, c.sort_order, c.is_default, true, true,
                    c.catalog_version, c.category_key
                );
                inserted_n := inserted_n + 1;
            end if;
        end loop;

        update public.ticket_categories tc
           set is_default = (tc.managed_category_key = 'support')
         where tc.guild_id::text = g.guild_id
           and tc.managed_by_dank = true
           and tc.is_default is distinct from (tc.managed_category_key = 'support');

        guild_id := g.guild_id;
        inserted_count := inserted_n;
        updated_count := updated_n;
        deleted_duplicate_count := deleted_n;
        return next;
    end loop;
end;
$$;

select * from public.reconcile_dank_ticket_categories(null);

-- Replace any unsafe earlier form of this index. Custom rows are deliberately
-- outside the uniqueness predicate.
drop index if exists public.ticket_categories_guild_managed_key_uidx;
create unique index ticket_categories_guild_managed_key_uidx
on public.ticket_categories(guild_id, managed_category_key)
where managed_by_dank = true and managed_category_key is not null;

revoke all on function public.reconcile_dank_ticket_categories(text) from public, anon, authenticated;
grant execute on function public.reconcile_dank_ticket_categories(text) to service_role;
