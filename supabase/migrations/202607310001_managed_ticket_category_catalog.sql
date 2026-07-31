-- ============================================================
-- 202607310001_managed_ticket_category_catalog.sql
-- ------------------------------------------------------------
-- Durable, idempotent reconciliation for Dank Shield-managed
-- ticket categories across every guild.
--
-- Guarantees:
--   * known legacy aliases collapse to one canonical category
--   * truly custom owner categories are preserved
--   * managed categories receive future catalog updates
--   * duplicate writes are blocked at the database layer
--   * rerunning reconciliation is safe
-- ============================================================

alter table if exists public.ticket_categories
    add column if not exists managed_by_dank boolean not null default false,
    add column if not exists managed_catalog_version integer,
    add column if not exists managed_category_key text;

create or replace function public.dank_ticket_category_key(
    p_slug text,
    p_name text default null
)
returns text
language sql
immutable
as $$
    select case
        when lower(coalesce(p_slug, '')) in ('verification', 'verification-help', 'verification_issue', 'verification-issue', 'verify')
          or lower(coalesce(p_name, '')) like '%verification%'
            then 'verification'
        when lower(coalesce(p_slug, '')) in ('bug', 'bug-report', 'technical_support', 'technical-support', 'bug-technical-support')
          or lower(coalesce(p_name, '')) like '%technical%'
          or lower(coalesce(p_name, '')) like '%bug%'
            then 'bug'
        when lower(coalesce(p_slug, '')) in ('custom', 'other', 'general', 'general-support', 'support')
          or lower(coalesce(p_name, '')) in ('other', 'general', 'general support', 'support')
            then 'support'
        when lower(coalesce(p_slug, '')) in ('cod_services', 'cod-services')
          or lower(coalesce(p_name, '')) like '%call of duty%'
          or lower(coalesce(p_name, '')) like '%cod services%'
            then 'cod-services'
        when lower(coalesce(p_slug, '')) in ('account_access', 'account-access')
            then 'account-access'
        when lower(coalesce(p_slug, '')) in ('payments_refunds', 'payments-refunds')
            then 'payments-refunds'
        when lower(coalesce(p_slug, '')) in ('staff_complaint', 'staff-complaint')
            then 'staff-complaint'
        when lower(coalesce(p_slug, '')) in ('service_request', 'service-request')
            then 'service-request'
        when lower(coalesce(p_slug, '')) in ('vouch_referral', 'vouch-referral')
            then 'vouch-referral'
        when lower(coalesce(p_slug, '')) in ('giveaway_reward', 'giveaway-reward')
            then 'giveaway-reward'
        when lower(coalesce(p_slug, '')) in ('content_media', 'content-media')
            then 'content-media'
        else trim(both '-' from regexp_replace(lower(coalesce(nullif(p_slug, ''), p_name, 'custom')), '[^a-z0-9]+', '-', 'g'))
    end;
$$;

create or replace function public.dank_ticket_category_catalog()
returns table (
    category_key text,
    slug text,
    name text,
    description text,
    intake_type text,
    match_keywords jsonb,
    sort_order integer,
    is_default boolean,
    catalog_version integer
)
language sql
immutable
as $$
    values
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
returns table (
    guild_id text,
    inserted_count integer,
    updated_count integer,
    deleted_duplicate_count integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
    g record;
    c record;
    winner record;
    duplicate_ids uuid[];
    inserted_n integer;
    updated_n integer;
    deleted_n integer;
begin
    for g in
        select distinct tc.guild_id::text as guild_id
        from public.ticket_categories tc
        where p_guild_id is null or tc.guild_id::text = p_guild_id
        union
        select gc.guild_id::text
        from public.guild_configs gc
        where p_guild_id is null or gc.guild_id::text = p_guild_id
    loop
        inserted_n := 0;
        updated_n := 0;
        deleted_n := 0;

        update public.ticket_categories tc
           set managed_category_key = public.dank_ticket_category_key(tc.slug, tc.name)
         where tc.guild_id::text = g.guild_id
           and tc.managed_category_key is distinct from public.dank_ticket_category_key(tc.slug, tc.name);

        for c in select * from public.dank_ticket_category_catalog() loop
            select tc.*
              into winner
              from public.ticket_categories tc
             where tc.guild_id::text = g.guild_id
               and public.dank_ticket_category_key(tc.slug, tc.name) = c.category_key
             order by
               case when tc.managed_by_dank then 0 else 1 end,
               case when tc.slug = c.slug then 0 else 1 end,
               coalesce(tc.sort_order, 9999),
               tc.created_at nulls last,
               tc.id
             limit 1;

            if found then
                select array_agg(tc.id)
                  into duplicate_ids
                  from public.ticket_categories tc
                 where tc.guild_id::text = g.guild_id
                   and public.dank_ticket_category_key(tc.slug, tc.name) = c.category_key
                   and tc.id <> winner.id;

                if duplicate_ids is not null then
                    delete from public.ticket_categories where id = any(duplicate_ids);
                    get diagnostics deleted_n = deleted_n + row_count;
                end if;

                -- Existing known built-ins become managed. Owner-created unknown
                -- categories are never touched because they do not match catalog keys.
                update public.ticket_categories
                   set slug = c.slug,
                       name = c.name,
                       description = c.description,
                       intake_type = c.intake_type,
                       match_keywords = c.match_keywords,
                       sort_order = c.sort_order,
                       is_default = c.is_default,
                       managed_by_dank = true,
                       managed_catalog_version = c.catalog_version,
                       managed_category_key = c.category_key,
                       updated_at = now()
                 where id = winner.id
                   and (
                        slug is distinct from c.slug
                     or name is distinct from c.name
                     or description is distinct from c.description
                     or intake_type is distinct from c.intake_type
                     or match_keywords is distinct from c.match_keywords
                     or sort_order is distinct from c.sort_order
                     or is_default is distinct from c.is_default
                     or managed_by_dank is distinct from true
                     or managed_catalog_version is distinct from c.catalog_version
                     or managed_category_key is distinct from c.category_key
                   );
                if found then updated_n := updated_n + 1; end if;
            else
                insert into public.ticket_categories (
                    guild_id, slug, name, description, intake_type,
                    match_keywords, sort_order, is_default,
                    managed_by_dank, managed_catalog_version, managed_category_key
                ) values (
                    g.guild_id, c.slug, c.name, c.description, c.intake_type,
                    c.match_keywords, c.sort_order, c.is_default,
                    true, c.catalog_version, c.category_key
                );
                inserted_n := inserted_n + 1;
            end if;
        end loop;

        -- Exactly one managed fallback must be default.
        update public.ticket_categories
           set is_default = (managed_category_key = 'support')
         where guild_id::text = g.guild_id
           and managed_by_dank = true;

        guild_id := g.guild_id;
        inserted_count := inserted_n;
        updated_count := updated_n;
        deleted_duplicate_count := deleted_n;
        return next;
    end loop;
end;
$$;

-- Clean current state before enforcing uniqueness.
select * from public.reconcile_dank_ticket_categories(null);

create unique index if not exists ticket_categories_guild_managed_key_uidx
    on public.ticket_categories (guild_id, managed_category_key)
    where managed_category_key is not null;

revoke all on function public.reconcile_dank_ticket_categories(text) from public;
revoke all on function public.reconcile_dank_ticket_categories(text) from anon;
revoke all on function public.reconcile_dank_ticket_categories(text) from authenticated;
grant execute on function public.reconcile_dank_ticket_categories(text) to service_role;
