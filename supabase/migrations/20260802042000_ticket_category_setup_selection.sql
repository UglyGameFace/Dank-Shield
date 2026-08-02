-- ============================================================
-- Ticket category setup selection v2
--
-- One durable migration owns the final behavior:
-- - keep the full Dank Shield category catalog available globally,
-- - show members only categories enabled for their guild,
-- - collapse exact managed/legacy duplicates without touching unknown custom rows,
-- - repair the old deployment that enabled nearly every category everywhere,
-- - preserve custom owner-built menus,
-- - require existing guilds to confirm their category selection,
-- - give new/empty guilds a small usable starter menu,
-- - allow an explicit custom-only selection,
-- - remain safe to run more than once.
-- ============================================================

alter table public.ticket_categories
    add column if not exists managed_by_dank boolean not null default false,
    add column if not exists managed_catalog_version integer,
    add column if not exists managed_category_key text,
    add column if not exists is_enabled boolean not null default true;

alter table public.guild_configs
    add column if not exists ticket_category_setup_required boolean not null default false,
    add column if not exists ticket_category_setup_required_reason text,
    add column if not exists ticket_category_setup_version integer not null default 0,
    add column if not exists ticket_category_setup_selected_keys jsonb not null default '[]'::jsonb,
    add column if not exists ticket_category_setup_completed_at timestamptz,
    add column if not exists ticket_category_setup_completed_by_id text,
    add column if not exists ticket_category_setup_completed_by_name text,
    add column if not exists setup_completed boolean not null default false,
    add column if not exists setup_completion_invalidated_at timestamptz,
    add column if not exists setup_completion_invalidated_reason text;

create or replace function public.dank_ticket_category_key(p_slug text, p_name text default null)
returns text
language sql
immutable
as $$
with normalized as (
    select
        trim(both '-' from regexp_replace(lower(btrim(coalesce(p_slug, ''))), '[^a-z0-9]+', '-', 'g')) as slug_key,
        trim(both '-' from regexp_replace(lower(btrim(coalesce(p_name, ''))), '[^a-z0-9]+', '-', 'g')) as name_key
)
select case
    when slug_key in ('verification', 'verification-help', 'verification-issue', 'verify')
      or name_key in ('verification', 'verification-help', 'verification-issue', 'verify', 'verify-help')
        then 'verification'
    when slug_key in ('account-access', 'account-help')
      or name_key in ('account-access', 'account-help', 'account-access-help')
        then 'account-access'
    when slug_key in ('payments-refunds', 'payment-refund', 'purchase-refund')
      or name_key in ('payments-refunds', 'payment-refund', 'purchase-refund', 'payments-and-refunds')
        then 'payments-refunds'
    when slug_key in ('appeal', 'appeals')
      or name_key in ('appeal', 'appeals')
        then 'appeal'
    when slug_key in ('report', 'reports', 'user-report')
      or name_key in ('report', 'reports', 'report-a-member', 'user-report', 'user-reports')
        then 'report'
    when slug_key in ('staff-complaint', 'staff-report')
      or name_key in ('staff-complaint', 'staff-complaints', 'staff-report')
        then 'staff-complaint'
    when slug_key in ('bug', 'bug-report', 'technical-support', 'bug-technical-support')
      or name_key in ('bug', 'bug-report', 'technical-support', 'bug-technical-support')
        then 'bug'
    when slug_key in ('cod-services', 'cod-service')
      or name_key in ('cod-services', 'cod-service', 'call-of-duty', 'call-of-duty-services')
        then 'cod-services'
    when slug_key in ('game-services', 'game-service', 'gaming-services')
      or name_key in ('game-services', 'game-service', 'gaming-services')
        then 'game-services'
    when slug_key in ('service-request', 'service-requests')
      or name_key in ('service-request', 'service-requests')
        then 'service-request'
    when slug_key in ('vouch-referral', 'vouch-invite-referral')
      or name_key in ('vouch-referral', 'vouch-invite-referral', 'vouch-invite-referral-issues')
        then 'vouch-referral'
    when slug_key in ('giveaway-reward', 'giveaway-rewards')
      or name_key in ('giveaway-reward', 'giveaway-rewards', 'giveaway-reward-issues')
        then 'giveaway-reward'
    when slug_key in ('content-media', 'content-media-request')
      or name_key in ('content-media', 'content-media-request', 'content-media-requests')
        then 'content-media'
    when slug_key in ('partnership', 'partnerships')
      or name_key in ('partnership', 'partnerships')
        then 'partnership'
    when slug_key in ('question', 'questions')
      or name_key in ('question', 'questions', 'other-question')
        then 'question'
    when slug_key in ('custom', 'other', 'general', 'general-support', 'support')
      or name_key in ('custom', 'other', 'general', 'general-support', 'support')
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
('verification','verification_issue','Verification','Help with verification or approval issues.','verification','["verification","verify","unverified","secure upload","vc verify","selfie","approval"]'::jsonb,10,false,2),
('account-access','account_access','Account / Access','Account access, login, hacked account, email, password, and 2FA issues.','account','["account","login","email","password","2fa","locked out","hacked","compromised"]'::jsonb,20,false,2),
('payments-refunds','payments_refunds','Payments / Refunds','Payments, orders, receipts, invoices, refunds, and chargebacks.','purchase','["payment","purchase","refund","chargeback","receipt","invoice","order"]'::jsonb,30,false,2),
('appeal','appeal','Appeal','Appeal a moderation action or access restriction.','appeal','["appeal","ban appeal","unban","kick appeal","timeout appeal","warn appeal"]'::jsonb,40,false,2),
('report','report','Report a Member','Report a member or server issue.','report','["report","scam","abuse","harassment","threat","raid","spam","rule break"]'::jsonb,50,false,2),
('staff-complaint','staff_complaint','Staff Complaint','Complaints or escalation requests involving staff or moderator behavior.','report','["staff complaint","staff issue","staff abuse","moderator report","admin report"]'::jsonb,60,false,2),
('bug','technical_support','Bug / Technical Support','Site bugs, panel problems, bot issues, broken flows, and technical failures.','bug','["bug","broken","not working","error","glitch","failed","technical support"]'::jsonb,70,false,2),
('cod-services','cod_services','COD Services','Call of Duty, Warzone, Zombies, lobby, account, unlock, or service questions.','cod_services','["cod","call of duty","lobby","warzone","black ops","modern warfare","zombies","unlock"]'::jsonb,80,false,2),
('game-services','game_services','Game Services','Route game-related service questions to the right staff.','game_services','["game services","game help","account help","lobby help","platform support"]'::jsonb,90,false,2),
('service-request','service_request','Service Requests','General service requests, carries, boosts, recoveries, and fulfillment questions.','custom','["service","boost","carry","recovery service","unlock service","rank help"]'::jsonb,100,false,2),
('vouch-referral','vouch_referral','Vouch / Invite / Referral','Invite credit, referral rewards, vouch issues, and who-invited-who questions.','custom','["vouch","invite","invite credit","referral","referrer","invite reward"]'::jsonb,110,false,2),
('giveaway-reward','giveaway_reward','Giveaway / Reward Issues','Giveaway prizes, missing rewards, winner disputes, and reward claims.','custom','["giveaway","reward","prize","claim prize","missing prize","winner issue"]'::jsonb,120,false,2),
('content-media','content_media','Content / Media Requests','Graphics, thumbnails, banners, content requests, media edits, and promo assets.','custom','["content","media","graphic","design","editing","video","thumbnail","banner"]'::jsonb,130,false,2),
('partnership','partnership','Partnerships','Partnerships, sponsorships, collaborations, and promotions.','partnership','["partnership","partner","collab","collaboration","sponsor","promotion"]'::jsonb,140,false,2),
('question','question','Other Question','Ask something that does not fit the other options.','question','["question","questions","how to","how do i"]'::jsonb,150,false,2),
('support','support','Support','General help from staff.','general','["support","help","general support","assistance"]'::jsonb,999,true,2);
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
    guild_had_rows boolean;
    starter_enabled boolean;
    make_default boolean;
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

        insert into pg_temp.dank_reconcile_guilds(guild_id)
        select distinct gc.guild_id::text
        from public.guild_configs gc
        where nullif(btrim(gc.guild_id::text), '') is not null
        on conflict do nothing;
    end if;

    for g in select rg.guild_id from pg_temp.dank_reconcile_guilds rg order by rg.guild_id loop
        inserted_n := 0;
        updated_n := 0;
        deleted_n := 0;

        select exists (
            select 1
            from public.ticket_categories existing_row
            where existing_row.guild_id::text = g.guild_id
        ) into guild_had_rows;

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
                delete from public.ticket_categories duplicate_row
                 where duplicate_row.guild_id::text = g.guild_id
                   and duplicate_row.id::text <> winner_id
                   and (
                        (duplicate_row.managed_by_dank = true
                         and duplicate_row.managed_category_key = c.category_key)
                        or
                        (duplicate_row.managed_by_dank = false
                         and public.dank_ticket_category_key(duplicate_row.slug, duplicate_row.name) = c.category_key)
                   );
                get diagnostics affected = row_count;
                deleted_n := deleted_n + affected;

                update public.ticket_categories managed_row
                   set slug = c.slug,
                       name = c.name,
                       button_label = c.name,
                       description = c.description,
                       intake_type = c.intake_type,
                       match_keywords = c.match_keywords,
                       sort_order = c.sort_order,
                       managed_by_dank = true,
                       managed_catalog_version = c.catalog_version,
                       managed_category_key = c.category_key,
                       updated_at = now()
                 where managed_row.id::text = winner_id
                   and (managed_row.slug, managed_row.name, managed_row.button_label,
                        managed_row.description, managed_row.intake_type,
                        managed_row.match_keywords, managed_row.sort_order,
                        managed_row.managed_by_dank, managed_row.managed_catalog_version,
                        managed_row.managed_category_key)
                       is distinct from
                       (c.slug, c.name, c.name, c.description, c.intake_type,
                        c.match_keywords, c.sort_order, true, c.catalog_version,
                        c.category_key);
                get diagnostics affected = row_count;
                updated_n := updated_n + affected;
            else
                starter_enabled := (not guild_had_rows)
                    and c.category_key in ('report', 'appeal', 'support');

                select starter_enabled
                   and c.category_key = 'support'
                   and not exists (
                       select 1
                       from public.ticket_categories existing_default
                       where existing_default.guild_id::text = g.guild_id
                         and existing_default.is_default = true
                   )
                  into make_default;

                insert into public.ticket_categories(
                    guild_id, slug, name, button_label, description, intake_type,
                    match_keywords, sort_order, is_default, is_enabled,
                    managed_by_dank, managed_catalog_version, managed_category_key
                ) values (
                    g.guild_id, c.slug, c.name, c.name, c.description,
                    c.intake_type, c.match_keywords, c.sort_order, make_default,
                    starter_enabled, true, c.catalog_version, c.category_key
                );
                inserted_n := inserted_n + 1;
            end if;
        end loop;

        guild_id := g.guild_id;
        inserted_count := inserted_n;
        updated_count := updated_n;
        deleted_duplicate_count := deleted_n;
        return next;
    end loop;
end;
$$;

create or replace function public.require_dank_ticket_category_setup(
    p_guild_id text,
    p_reason text default 'Choose the ticket options this server actually uses.',
    p_reset_to_starter boolean default true
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    custom_exists boolean;
    custom_default_id text;
begin
    if nullif(btrim(p_guild_id), '') is null then
        raise exception 'guild id is required';
    end if;

    perform * from public.reconcile_dank_ticket_categories(btrim(p_guild_id));

    if p_reset_to_starter then
        select exists (
            select 1
            from public.ticket_categories custom_row
            where custom_row.guild_id::text = btrim(p_guild_id)
              and custom_row.managed_by_dank = false
              and custom_row.is_enabled = true
        ) into custom_exists;

        update public.ticket_categories managed_row
           set is_enabled = (not custom_exists)
                            and managed_row.managed_category_key in ('report', 'appeal', 'support'),
               is_default = (not custom_exists)
                            and managed_row.managed_category_key = 'support',
               updated_at = now()
         where managed_row.guild_id::text = btrim(p_guild_id)
           and managed_row.managed_by_dank = true;

        if custom_exists then
            select custom_row.id::text
              into custom_default_id
              from public.ticket_categories custom_row
             where custom_row.guild_id::text = btrim(p_guild_id)
               and custom_row.managed_by_dank = false
               and custom_row.is_enabled = true
             order by
               case when custom_row.is_default then 0 else 1 end,
               coalesce(custom_row.sort_order, 9999),
               custom_row.created_at,
               custom_row.id
             limit 1;

            update public.ticket_categories custom_row
               set is_default = custom_row.id::text = custom_default_id,
                   updated_at = now()
             where custom_row.guild_id::text = btrim(p_guild_id)
               and custom_row.managed_by_dank = false;
        else
            update public.ticket_categories custom_row
               set is_default = false,
                   updated_at = now()
             where custom_row.guild_id::text = btrim(p_guild_id)
               and custom_row.managed_by_dank = false
               and custom_row.is_default = true;
        end if;
    end if;

    update public.guild_configs gc
       set ticket_category_setup_required = true,
           ticket_category_setup_required_reason = left(
               coalesce(nullif(btrim(p_reason), ''),
                        'Choose the ticket options this server actually uses.'),
               500
           ),
           ticket_category_setup_version = 0,
           ticket_category_setup_selected_keys = case
               when p_reset_to_starter then '[]'::jsonb
               else gc.ticket_category_setup_selected_keys
           end,
           setup_completed = false,
           setup_completion_invalidated_at = now(),
           setup_completion_invalidated_reason = 'Ticket menu setup requires confirmation.',
           updated_at = now()
     where gc.guild_id::text = btrim(p_guild_id);
end;
$$;

create or replace function public.save_dank_ticket_category_selection(
    p_guild_id text,
    p_selected_keys text[],
    p_actor_id text default null,
    p_actor_name text default null
)
returns table(category_key text, slug text, name text, is_enabled boolean, is_default boolean)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    selected_keys text[];
    invalid_keys text[];
    default_key text;
    custom_default_id text;
begin
    if nullif(btrim(p_guild_id), '') is null then
        raise exception 'guild id is required';
    end if;

    select coalesce(array_agg(distinct btrim(chosen.value)), array[]::text[])
      into selected_keys
      from unnest(coalesce(p_selected_keys, array[]::text[])) as chosen(value)
     where nullif(btrim(chosen.value), '') is not null;

    if coalesce(array_length(selected_keys, 1), 0) = 0
       and not exists (
           select 1
             from public.ticket_categories custom_row
            where custom_row.guild_id::text = btrim(p_guild_id)
              and custom_row.managed_by_dank = false
              and custom_row.is_enabled = true
       ) then
        raise exception 'choose at least one ticket category';
    end if;

    select coalesce(array_agg(chosen.value), array[]::text[])
      into invalid_keys
      from unnest(selected_keys) as chosen(value)
     where not exists (
        select 1
          from public.dank_ticket_category_catalog() catalog
         where catalog.category_key = chosen.value
     );

    if coalesce(array_length(invalid_keys, 1), 0) > 0 then
        raise exception 'unknown managed ticket category key(s): %', invalid_keys;
    end if;

    perform * from public.reconcile_dank_ticket_categories(btrim(p_guild_id));

    update public.ticket_categories managed_row
       set is_enabled = managed_row.managed_category_key = any(selected_keys),
           is_default = false,
           updated_at = now()
     where managed_row.guild_id::text = btrim(p_guild_id)
       and managed_row.managed_by_dank = true;

    default_key := null;
    if coalesce(array_length(selected_keys, 1), 0) > 0 then
        if 'support' = any(selected_keys) then
            default_key := 'support';
        else
            select catalog.category_key
              into default_key
              from public.dank_ticket_category_catalog() catalog
             where catalog.category_key = any(selected_keys)
             order by catalog.sort_order, catalog.category_key
             limit 1;
        end if;

        update public.ticket_categories managed_row
           set is_default = managed_row.managed_category_key = default_key,
               updated_at = now()
         where managed_row.guild_id::text = btrim(p_guild_id)
           and managed_row.managed_by_dank = true;

        update public.ticket_categories custom_row
           set is_default = false,
               updated_at = now()
         where custom_row.guild_id::text = btrim(p_guild_id)
           and custom_row.managed_by_dank = false
           and custom_row.is_default = true;
    else
        select custom_row.id::text
          into custom_default_id
          from public.ticket_categories custom_row
         where custom_row.guild_id::text = btrim(p_guild_id)
           and custom_row.managed_by_dank = false
           and custom_row.is_enabled = true
         order by
           case when custom_row.is_default then 0 else 1 end,
           coalesce(custom_row.sort_order, 9999),
           custom_row.created_at,
           custom_row.id
         limit 1;

        update public.ticket_categories custom_row
           set is_default = custom_row.id::text = custom_default_id,
               updated_at = now()
         where custom_row.guild_id::text = btrim(p_guild_id)
           and custom_row.managed_by_dank = false;
    end if;

    update public.guild_configs gc
       set ticket_category_setup_required = false,
           ticket_category_setup_required_reason = null,
           ticket_category_setup_version = 2,
           ticket_category_setup_selected_keys = to_jsonb(selected_keys),
           ticket_category_setup_completed_at = now(),
           ticket_category_setup_completed_by_id = nullif(btrim(coalesce(p_actor_id, '')), ''),
           ticket_category_setup_completed_by_name = nullif(btrim(coalesce(p_actor_name, '')), ''),
           setup_completed = false,
           setup_completion_invalidated_at = now(),
           setup_completion_invalidated_reason = 'Ticket menu choices changed.',
           updated_at = now()
     where gc.guild_id::text = btrim(p_guild_id);

    return query
    select managed_row.managed_category_key,
           managed_row.slug,
           managed_row.name,
           managed_row.is_enabled,
           managed_row.is_default
      from public.ticket_categories managed_row
     where managed_row.guild_id::text = btrim(p_guild_id)
       and managed_row.managed_by_dank = true
       and managed_row.is_enabled = true
     order by managed_row.sort_order, managed_row.name;
end;
$$;

-- Install/update the full catalog first. Existing enablement is preserved; rows
-- added to an already configured/custom guild start disabled.
select * from public.reconcile_dank_ticket_categories(null);

-- Existing guilds must confirm the v2 menu. Objectively broken all-enabled
-- managed menus are reduced to the small starter set. Custom menus are kept live
-- by disabling managed choices until the owner explicitly selects any.
do $$
declare
    g record;
    managed_total integer;
    managed_enabled integer;
    custom_total integer;
    current_version integer;
begin
    for g in select gc.guild_id::text as guild_id from public.guild_configs gc loop
        select
            count(*) filter (where tc.managed_by_dank = true),
            count(*) filter (where tc.managed_by_dank = true and tc.is_enabled = true),
            count(*) filter (where tc.managed_by_dank = false)
          into managed_total, managed_enabled, custom_total
          from public.ticket_categories tc
         where tc.guild_id::text = g.guild_id;

        select coalesce(gc.ticket_category_setup_version, 0)
          into current_version
          from public.guild_configs gc
         where gc.guild_id::text = g.guild_id;

        if current_version >= 2 then
            continue;
        end if;

        if custom_total > 0 then
            perform public.require_dank_ticket_category_setup(
                g.guild_id,
                'Your custom ticket choices were preserved. Confirm whether this server also wants any built-in Dank Shield choices.',
                true
            );
        elsif managed_enabled >= 10 then
            perform public.require_dank_ticket_category_setup(
                g.guild_id,
                'The previous setup enabled duplicate or excessive built-in ticket choices. Choose only what this server needs.',
                true
            );
        elsif managed_total > 0 then
            perform public.require_dank_ticket_category_setup(
                g.guild_id,
                'Confirm which built-in ticket choices this server should show.',
                false
            );
        end if;
    end loop;
end;
$$;

create or replace function public.sync_dank_ticket_categories_for_new_guild()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    perform * from public.reconcile_dank_ticket_categories(new.guild_id::text);
    update public.guild_configs gc
       set ticket_category_setup_required = true,
           ticket_category_setup_required_reason = coalesce(
               nullif(gc.ticket_category_setup_required_reason, ''),
               'Choose the ticket options this server should show.'
           ),
           ticket_category_setup_version = 0,
           setup_completed = false,
           setup_completion_invalidated_at = now(),
           setup_completion_invalidated_reason = 'Ticket menu setup is required.'
     where gc.guild_id::text = new.guild_id::text;
    return new;
end;
$$;

drop trigger if exists guild_configs_sync_dank_ticket_categories
    on public.guild_configs;
create trigger guild_configs_sync_dank_ticket_categories
after insert on public.guild_configs
for each row
execute function public.sync_dank_ticket_categories_for_new_guild();

alter table public.guild_configs
    alter column ticket_category_setup_required set default true;

drop index if exists public.ticket_categories_guild_managed_key_uidx;
create unique index ticket_categories_guild_managed_key_uidx
on public.ticket_categories(guild_id, managed_category_key)
where managed_by_dank = true and managed_category_key is not null;

revoke all on function public.reconcile_dank_ticket_categories(text) from public, anon, authenticated;
revoke all on function public.require_dank_ticket_category_setup(text, text, boolean) from public, anon, authenticated;
revoke all on function public.save_dank_ticket_category_selection(text, text[], text, text) from public, anon, authenticated;
grant execute on function public.reconcile_dank_ticket_categories(text) to service_role;
grant execute on function public.require_dank_ticket_category_setup(text, text, boolean) to service_role;
grant execute on function public.save_dank_ticket_category_selection(text, text[], text, text) to service_role;
