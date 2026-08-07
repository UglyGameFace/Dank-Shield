-- ============================================================
-- DS-TICKET-CAT-022 — repair historical managed ticket categories.
--
-- Guarantees:
-- - CATEGORY SETUP v2 selections remain authoritative and are not reset.
-- - Managed catalog shape has its own version (v3) independent of setup v2.
-- - A managed row whose stored key disagrees with its canonical slug is repaired
--   to the slug's reserved catalog key; a correct canonical slug keeps its key
--   even when an old visible label is wrong.
-- - Non-managed rows are adopted only when their *slug* is an exact reserved
--   Dank Shield alias. Unknown custom slugs are never adopted from display name.
-- - Enabled/selected state is preserved for completed v2 guilds.
-- - True managed/reserved legacy duplicates are removed idempotently.
-- - Every existing guild is reconciled once by this migration.
-- ============================================================

create or replace function public.dank_ticket_category_catalog()
returns table(
    category_key text, slug text, name text, description text,
    intake_type text, match_keywords jsonb, sort_order integer,
    is_default boolean, catalog_version integer
)
language sql
immutable
as $$ values
('verification','verification_issue','Verification','Help with verification or approval issues.','verification','["verification","verify","unverified","secure upload","vc verify","selfie","approval"]'::jsonb,10,false,3),
('account-access','account_access','Account / Access','Account access, login, hacked account, email, password, and 2FA issues.','account','["account","login","email","password","2fa","locked out","hacked","compromised"]'::jsonb,20,false,3),
('payments-refunds','payments_refunds','Payments / Refunds','Payments, orders, receipts, invoices, refunds, and chargebacks.','purchase','["payment","purchase","refund","chargeback","receipt","invoice","order"]'::jsonb,30,false,3),
('appeal','appeal','Appeal','Appeal a moderation action or access restriction.','appeal','["appeal","ban appeal","unban","kick appeal","timeout appeal","warn appeal"]'::jsonb,40,false,3),
('report','report','Report a Member','Report a member or server issue.','report','["report","scam","abuse","harassment","threat","raid","spam","rule break"]'::jsonb,50,false,3),
('staff-complaint','staff_complaint','Staff Complaint','Complaints or escalation requests involving staff or moderator behavior.','report','["staff complaint","staff issue","staff abuse","moderator report","admin report"]'::jsonb,60,false,3),
('bug','technical_support','Bug / Technical Support','Site bugs, panel problems, bot issues, broken flows, and technical failures.','bug','["bug","broken","not working","error","glitch","failed","technical support"]'::jsonb,70,false,3),
('cod-services','cod_services','COD Services','Call of Duty, Warzone, Zombies, lobby, account, unlock, or service questions.','cod_services','["cod","call of duty","lobby","warzone","black ops","modern warfare","zombies","unlock"]'::jsonb,80,false,3),
('game-services','game_services','Game Services','Route game-related service questions to the right staff.','game_services','["game services","game help","account help","lobby help","platform support"]'::jsonb,90,false,3),
('service-request','service_request','Service Requests','General service requests, carries, boosts, recoveries, and fulfillment questions.','custom','["service","boost","carry","recovery service","unlock service","rank help"]'::jsonb,100,false,3),
('vouch-referral','vouch_referral','Vouch / Invite / Referral','Invite credit, referral rewards, vouch issues, and who-invited-who questions.','custom','["vouch","invite","invite credit","referral","referrer","invite reward"]'::jsonb,110,false,3),
('giveaway-reward','giveaway_reward','Giveaway / Reward Issues','Giveaway prizes, missing rewards, winner disputes, and reward claims.','custom','["giveaway","reward","prize","claim prize","missing prize","winner issue"]'::jsonb,120,false,3),
('content-media','content_media','Content / Media Requests','Graphics, thumbnails, banners, content requests, media edits, and promo assets.','custom','["content","media","graphic","design","editing","video","thumbnail","banner"]'::jsonb,130,false,3),
('partnership','partnership','Partnerships','Partnerships, sponsorships, collaborations, and promotions.','partnership','["partnership","partner","collab","collaboration","sponsor","promotion"]'::jsonb,140,false,3),
('question','question','Other Question','Ask something that does not fit the other options.','question','["question","questions","how to","how do i"]'::jsonb,150,false,3),
('support','support','Support','General help from staff.','general','["support","help","general support","assistance"]'::jsonb,999,true,3);
$$;

create or replace function public.dank_ticket_category_repair_key(
    p_managed boolean,
    p_managed_key text,
    p_slug text,
    p_name text,
    p_button_label text default null
)
returns text
language plpgsql
immutable
set search_path = public
as $$
declare
    stored_key text;
    slug_key text;
    name_key text;
begin
    stored_key := trim(both '-' from regexp_replace(
        lower(btrim(coalesce(p_managed_key, ''))),
        '[^a-z0-9]+', '-', 'g'
    ));
    slug_key := public.dank_ticket_category_key(p_slug, null);
    name_key := public.dank_ticket_category_key(
        null,
        coalesce(nullif(btrim(p_button_label), ''), p_name)
    );

    if coalesce(p_managed, false) then
        if exists (
            select 1
            from public.dank_ticket_category_catalog() catalog
            where catalog.category_key = stored_key
        ) then
            -- A reserved slug is stronger evidence than a stale stored key.
            -- If the slug still belongs to the stored key, the key wins and the
            -- visible name/button label will simply be rewritten canonically.
            if slug_key is not null and slug_key <> stored_key then
                return slug_key;
            end if;
            return stored_key;
        end if;
        return coalesce(slug_key, name_key);
    end if;

    -- Owner-created rows with unknown/custom slugs remain custom even when their
    -- display name resembles a built-in label. Only exact reserved legacy slugs
    -- are eligible for adoption.
    return slug_key;
end;
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
    candidate_n integer;
    any_enabled boolean;
    desired_enabled boolean;
    guild_had_rows boolean;
    cfg_version integer;
    cfg_required boolean;
    cfg_selected_json jsonb;
    cfg_selected text[];
    use_saved_selection boolean;
    default_key text;
    custom_default_exists boolean;
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
        cfg_version := 0;
        cfg_required := true;
        cfg_selected_json := '[]'::jsonb;
        cfg_selected := array[]::text[];

        select exists (
            select 1
            from public.ticket_categories existing_row
            where existing_row.guild_id::text = g.guild_id
        ) into guild_had_rows;

        if to_regclass('public.guild_configs') is not null then
            select coalesce(gc.ticket_category_setup_version, 0),
                   coalesce(gc.ticket_category_setup_required, true),
                   coalesce(gc.ticket_category_setup_selected_keys, '[]'::jsonb)
              into cfg_version, cfg_required, cfg_selected_json
              from public.guild_configs gc
             where gc.guild_id::text = g.guild_id
             limit 1;

            if not found then
                cfg_version := 0;
                cfg_required := true;
                cfg_selected_json := '[]'::jsonb;
            end if;
        end if;

        select coalesce(array_agg(chosen.value), array[]::text[])
          into cfg_selected
          from jsonb_array_elements_text(cfg_selected_json) as chosen(value)
         where nullif(btrim(chosen.value), '') is not null;

        use_saved_selection := cfg_version >= 2 and cfg_required = false;

        for c in select * from public.dank_ticket_category_catalog() loop
            winner_id := null;
            candidate_n := 0;
            any_enabled := false;

            select count(*), coalesce(bool_or(tc.is_enabled), false)
              into candidate_n, any_enabled
              from public.ticket_categories tc
             where tc.guild_id::text = g.guild_id
               and public.dank_ticket_category_repair_key(
                    tc.managed_by_dank,
                    tc.managed_category_key,
                    tc.slug,
                    tc.name,
                    tc.button_label
               ) = c.category_key;

            select tc.id::text
              into winner_id
              from public.ticket_categories tc
             where tc.guild_id::text = g.guild_id
               and public.dank_ticket_category_repair_key(
                    tc.managed_by_dank,
                    tc.managed_category_key,
                    tc.slug,
                    tc.name,
                    tc.button_label
               ) = c.category_key
             order by
                    case when tc.managed_by_dank = true then 0 else 1 end,
                    case when tc.managed_category_key = c.category_key then 0 else 1 end,
                    case when tc.slug = c.slug then 0 else 1 end,
                    coalesce(tc.sort_order, 9999),
                    tc.created_at nulls last,
                    tc.id
             limit 1;

            if use_saved_selection then
                desired_enabled := c.category_key = any(cfg_selected);
            elsif candidate_n > 0 then
                desired_enabled := any_enabled;
            else
                desired_enabled := (not guild_had_rows)
                    and c.category_key in ('report', 'appeal', 'support');
            end if;

            if winner_id is not null then
                delete from public.ticket_categories duplicate_row
                 where duplicate_row.guild_id::text = g.guild_id
                   and duplicate_row.id::text <> winner_id
                   and public.dank_ticket_category_repair_key(
                        duplicate_row.managed_by_dank,
                        duplicate_row.managed_category_key,
                        duplicate_row.slug,
                        duplicate_row.name,
                        duplicate_row.button_label
                   ) = c.category_key;
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
                       is_default = false,
                       is_enabled = desired_enabled,
                       managed_by_dank = true,
                       managed_catalog_version = c.catalog_version,
                       managed_category_key = c.category_key,
                       updated_at = now()
                 where managed_row.id::text = winner_id
                   and (managed_row.slug, managed_row.name, managed_row.button_label,
                        managed_row.description, managed_row.intake_type,
                        managed_row.match_keywords, managed_row.sort_order,
                        managed_row.is_default, managed_row.is_enabled,
                        managed_row.managed_by_dank, managed_row.managed_catalog_version,
                        managed_row.managed_category_key)
                       is distinct from
                       (c.slug, c.name, c.name, c.description, c.intake_type,
                        c.match_keywords, c.sort_order, false, desired_enabled,
                        true, c.catalog_version, c.category_key);
                get diagnostics affected = row_count;
                updated_n := updated_n + affected;
            else
                insert into public.ticket_categories(
                    guild_id, slug, name, button_label, description, intake_type,
                    match_keywords, sort_order, is_default, is_enabled,
                    managed_by_dank, managed_catalog_version, managed_category_key
                ) values (
                    g.guild_id, c.slug, c.name, c.name, c.description,
                    c.intake_type, c.match_keywords, c.sort_order, false,
                    desired_enabled, true, c.catalog_version, c.category_key
                );
                inserted_n := inserted_n + 1;
            end if;
        end loop;

        -- Completed setup v2 selections are the source of truth. This preserves
        -- the owner's choices while repairing every managed row around them.
        if use_saved_selection then
            update public.ticket_categories managed_row
               set is_enabled = managed_row.managed_category_key = any(cfg_selected),
                   is_default = false,
                   updated_at = now()
             where managed_row.guild_id::text = g.guild_id
               and managed_row.managed_by_dank = true
               and (
                   managed_row.is_enabled is distinct from (managed_row.managed_category_key = any(cfg_selected))
                   or managed_row.is_default = true
               );

            if coalesce(array_length(cfg_selected, 1), 0) > 0 then
                if 'support' = any(cfg_selected) then
                    default_key := 'support';
                else
                    select catalog.category_key
                      into default_key
                      from public.dank_ticket_category_catalog() catalog
                     where catalog.category_key = any(cfg_selected)
                     order by catalog.sort_order, catalog.category_key
                     limit 1;
                end if;

                update public.ticket_categories managed_row
                   set is_default = managed_row.managed_category_key = default_key,
                       updated_at = now()
                 where managed_row.guild_id::text = g.guild_id
                   and managed_row.managed_by_dank = true
                   and managed_row.is_enabled = true;

                update public.ticket_categories custom_row
                   set is_default = false,
                       updated_at = now()
                 where custom_row.guild_id::text = g.guild_id
                   and custom_row.managed_by_dank = false
                   and custom_row.is_default = true;
            end if;
        else
            -- In an unfinished/legacy setup, keep current enablement but ensure
            -- only one fallback default when custom rows do not already own it.
            select exists (
                select 1
                from public.ticket_categories custom_row
                where custom_row.guild_id::text = g.guild_id
                  and custom_row.managed_by_dank = false
                  and custom_row.is_enabled = true
                  and custom_row.is_default = true
            ) into custom_default_exists;

            update public.ticket_categories managed_row
               set is_default = false,
                   updated_at = now()
             where managed_row.guild_id::text = g.guild_id
               and managed_row.managed_by_dank = true
               and managed_row.is_default = true;

            if not custom_default_exists then
                select case
                    when exists (
                        select 1 from public.ticket_categories managed_row
                        where managed_row.guild_id::text = g.guild_id
                          and managed_row.managed_by_dank = true
                          and managed_row.managed_category_key = 'support'
                          and managed_row.is_enabled = true
                    ) then 'support'
                    else (
                        select managed_row.managed_category_key
                        from public.ticket_categories managed_row
                        where managed_row.guild_id::text = g.guild_id
                          and managed_row.managed_by_dank = true
                          and managed_row.is_enabled = true
                        order by managed_row.sort_order, managed_row.managed_category_key
                        limit 1
                    )
                end into default_key;

                if default_key is not null then
                    update public.ticket_categories managed_row
                       set is_default = managed_row.managed_category_key = default_key,
                           updated_at = now()
                     where managed_row.guild_id::text = g.guild_id
                       and managed_row.managed_by_dank = true
                       and managed_row.is_enabled = true;
                end if;
            end if;
        end if;

        guild_id := g.guild_id;
        inserted_count := inserted_n;
        updated_count := updated_n;
        deleted_duplicate_count := deleted_n;
        return next;
    end loop;
end;
$$;

-- Repair every existing guild immediately. This is intentionally safe to rerun.
select * from public.reconcile_dank_ticket_categories(null);

-- Re-apply the v2 safety policy only to guilds that never completed v2. A
-- completed v2 selection is never invalidated by this catalog repair.
do $$
declare
    g record;
    managed_enabled integer;
    custom_enabled integer;
begin
    if to_regclass('public.guild_configs') is null then
        return;
    end if;

    for g in
        select gc.guild_id::text as guild_id
        from public.guild_configs gc
        where coalesce(gc.ticket_category_setup_version, 0) < 2
    loop
        select count(*) filter (where tc.managed_by_dank = true and tc.is_enabled = true),
               count(*) filter (where tc.managed_by_dank = false and tc.is_enabled = true)
          into managed_enabled, custom_enabled
          from public.ticket_categories tc
         where tc.guild_id::text = g.guild_id;

        if custom_enabled > 0 then
            perform public.require_dank_ticket_category_setup(
                g.guild_id,
                'Your custom ticket choices were preserved. Confirm whether this server also wants any built-in Dank Shield choices.',
                true
            );
        elsif managed_enabled >= 10 then
            perform public.require_dank_ticket_category_setup(
                g.guild_id,
                'Historical duplicate or excessive built-in ticket choices were repaired. Choose only what this server needs.',
                true
            );
        end if;
    end loop;
end;
$$;

-- Rebuild the managed-key uniqueness invariant after repair. Custom rows stay
-- outside this index by design.
drop index if exists public.ticket_categories_guild_managed_key_uidx;
create unique index ticket_categories_guild_managed_key_uidx
on public.ticket_categories(guild_id, managed_category_key)
where managed_by_dank = true and managed_category_key is not null;

revoke all on function public.dank_ticket_category_repair_key(boolean, text, text, text, text)
    from public, anon, authenticated;
revoke all on function public.reconcile_dank_ticket_categories(text)
    from public, anon, authenticated;
grant execute on function public.reconcile_dank_ticket_categories(text) to service_role;
