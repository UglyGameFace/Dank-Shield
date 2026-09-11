-- ============================================================
-- DS-TICKET-CAT-037 — restore rich ticket choices through review.
--
-- Fixes two production regressions:
-- 1. guilds put into category review could be stranded on the three starter
--    rows even though a prior owner-confirmed selection was preserved;
-- 2. historical rich category intent drifted into generic labels, notably
--    COD Services (expanded to modern titles) and Staff Complaint.
--
-- This migration never invents a per-guild selection.  It first trusts an
-- already-preserved guild_configs selection.  If that evidence was destroyed
-- by the older reset function, it recovers the last ticket-category snapshot
-- from before that exact destructive-reset transaction.  Guild isolation
-- remains strict throughout.
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
('verification','verification_issue','Verification','Help with verification or approval issues.','verification','["verification","verify","unverified","secure upload","vc verify","selfie","approval"]'::jsonb,10,false,4),
('account-access','account_access','Account / Access','Account access, login, hacked account, email, password, and 2FA issues.','account','["account","login","email","password","2fa","locked out","hacked","compromised"]'::jsonb,20,false,4),
('payments-refunds','payments_refunds','Payments / Refunds','Payments, orders, receipts, invoices, refunds, and chargebacks.','purchase','["payment","purchase","refund","chargeback","receipt","invoice","order"]'::jsonb,30,false,4),
('appeal','appeal','Appeal','Appeal a moderation action or access restriction.','appeal','["appeal","ban appeal","unban","kick appeal","timeout appeal","warn appeal"]'::jsonb,40,false,4),
('report','report','Report a Member','Report a member, scam, harassment, abuse, spam, raid, or rule violation.','report','["report","scam","abuse","harassment","threat","raid","spam","rule break"]'::jsonb,50,false,4),
('staff-complaint','staff_complaint','Report Staff','Report or escalate staff, moderator, or administrator behavior.','report','["report staff","staff complaint","staff issue","staff abuse","moderator report","admin report"]'::jsonb,60,false,4),
('bug','technical_support','Bug / Technical Support','Site bugs, panel problems, bot issues, broken flows, and technical failures.','bug','["bug","broken","not working","error","glitch","failed","technical support"]'::jsonb,70,false,4),
('cod-services','cod_services','COD Modding Services','Legacy Call of Duty modding services for older titles: modded/challenge lobbies, unlocks, Zombies, recoveries, RGH/JTAG, and related help.','cod_services','["cod modding","legacy cod","bo1","bo2","bo3","waw","mw2","mw3","ghosts","zombies","modded lobby","challenge lobby","unlock all","recovery","rgh","jtag"]'::jsonb,80,false,4),
('game-services','game_services','Game Services','Route general or current-game service questions to the right staff.','game_services','["game services","game help","account help","lobby help","platform support"]'::jsonb,90,false,4),
('service-request','service_request','Service Requests','General service requests, carries, boosts, recoveries, and fulfillment questions.','custom','["service","boost","carry","recovery service","unlock service","rank help"]'::jsonb,100,false,4),
('vouch-referral','vouch_referral','Vouch / Invite / Referral','Invite credit, referral rewards, vouch issues, and who-invited-who questions.','custom','["vouch","invite","invite credit","referral","referrer","invite reward"]'::jsonb,110,false,4),
('giveaway-reward','giveaway_reward','Giveaway / Reward Issues','Giveaway prizes, missing rewards, winner disputes, and reward claims.','custom','["giveaway","reward","prize","claim prize","missing prize","winner issue"]'::jsonb,120,false,4),
('content-media','content_media','Content / Media Requests','Graphics, thumbnails, banners, content requests, media edits, and promo assets.','custom','["content","media","graphic","design","editing","video","thumbnail","banner"]'::jsonb,130,false,4),
('partnership','partnership','Partnerships','Partnerships, sponsorships, collaborations, and promotions.','partnership','["partnership","partner","collab","collaboration","sponsor","promotion"]'::jsonb,140,false,4),
('question','question','Other Question','Ask something that does not fit the other options.','question','["question","questions","how to","how do i"]'::jsonb,150,false,4),
('support','support','Support','General help from staff.','general','["support","help","general support","assistance"]'::jsonb,999,true,4);
$$;

-- The v3 reconciler reads the catalog dynamically, so changing the catalog
-- function is sufficient to materialize the v4 labels/keywords without adding
-- another competing reconciliation owner.
select * from public.reconcile_dank_ticket_categories(null);

create or replace function public.recover_dank_ticket_category_selection_from_history(
    p_guild_id text
)
returns text[]
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    current_selected jsonb := '[]'::jsonb;
    historical_rows jsonb;
    destructive_reset_at timestamptz;
    recovered text[] := array[]::text[];
    default_key text;
begin
    if nullif(btrim(p_guild_id), '') is null then
        raise exception 'guild id is required';
    end if;

    select coalesce(gc.ticket_category_setup_selected_keys, '[]'::jsonb)
      into current_selected
      from public.guild_configs gc
     where gc.guild_id::text = btrim(p_guild_id)
       and coalesce(gc.ticket_category_setup_required, false) = true
     limit 1;

    if not found then
        return recovered;
    end if;

    -- Never overwrite surviving owner evidence.  Recovery exists only for
    -- guilds whose older safety reset already erased the selection field.
    if jsonb_typeof(current_selected) = 'array'
       and jsonb_array_length(current_selected) > 0 then
        return recovered;
    end if;

    if to_regclass('public.guild_config_versions') is null then
        return recovered;
    end if;

    -- The old reset updated ticket rows and then wrote guild_configs in the
    -- same transaction.  PostgreSQL now() is transaction-stable, so every
    -- intermediate row-trigger snapshot from that destructive reset shares the
    -- reset timestamp.  Recover the last ticket snapshot strictly *before* it,
    -- not an intermediate partially-reset snapshot.
    select v.created_at
      into destructive_reset_at
      from public.guild_config_versions v
     where v.guild_id = btrim(p_guild_id)
       and v.config_table in ('guild_configs', 'guild_config')
       and coalesce((v.snapshot ->> 'ticket_category_setup_required')::boolean, false) = true
       and coalesce(v.snapshot ->> 'ticket_category_setup_required_reason', '')
           ilike '%previous setup enabled duplicate or excessive%'
     order by v.created_at desc, v.version_id desc
     limit 1;

    if destructive_reset_at is null then
        return recovered;
    end if;

    select v.snapshot -> 'rows'
      into historical_rows
      from public.guild_config_versions v
     where v.guild_id = btrim(p_guild_id)
       and v.config_table = 'ticket_categories'
       and v.created_at < destructive_reset_at
     order by v.created_at desc, v.version_id desc
     limit 1;

    if historical_rows is null then
        return recovered;
    end if;

    select coalesce(array_agg(distinct resolved.category_key order by resolved.category_key), array[]::text[])
      into recovered
      from jsonb_array_elements(coalesce(historical_rows, '[]'::jsonb)) item
     cross join lateral (
        select public.dank_ticket_category_key(item ->> 'slug', null) as category_key
     ) resolved
     where coalesce((item ->> 'is_enabled')::boolean, true) = true
       and resolved.category_key is not null
       and exists (
            select 1
              from public.dank_ticket_category_catalog() catalog
             where catalog.category_key = resolved.category_key
       );

    if coalesce(array_length(recovered, 1), 0) < 1 then
        return array[]::text[];
    end if;

    update public.guild_configs gc
       set ticket_category_setup_selected_keys = to_jsonb(recovered),
           updated_at = now()
     where gc.guild_id::text = btrim(p_guild_id)
       and coalesce(gc.ticket_category_setup_required, false) = true
       and coalesce(gc.ticket_category_setup_selected_keys, '[]'::jsonb) = '[]'::jsonb;

    update public.ticket_categories tc
       set is_enabled = tc.managed_category_key = any(recovered),
           is_default = false,
           updated_at = now()
     where tc.guild_id::text = btrim(p_guild_id)
       and tc.managed_by_dank = true;

    if 'support' = any(recovered) then
        default_key := 'support';
    else
        select catalog.category_key
          into default_key
          from public.dank_ticket_category_catalog() catalog
         where catalog.category_key = any(recovered)
         order by catalog.sort_order, catalog.category_key
         limit 1;
    end if;

    if default_key is not null then
        update public.ticket_categories tc
           set is_default = tc.managed_category_key = default_key,
               updated_at = now()
         where tc.guild_id::text = btrim(p_guild_id)
           and tc.managed_by_dank = true
           and tc.is_enabled = true;
    end if;

    return recovered;
end;
$$;

revoke all on function public.recover_dank_ticket_category_selection_from_history(text)
    from public, anon, authenticated;
grant execute on function public.recover_dank_ticket_category_selection_from_history(text)
    to service_role;

-- Repair already-stranded guilds once.  The function refuses to overwrite any
-- surviving selection, so this is safe and idempotent on repeated migration runs.
select public.recover_dank_ticket_category_selection_from_history(gc.guild_id::text)
  from public.guild_configs gc
 where coalesce(gc.ticket_category_setup_required, false) = true;

-- Future forced reviews keep a surviving selection visible instead of
-- re-collapsing the row flags to the starter trio.
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
    preserved_keys text[] := array[]::text[];
    preserved_default text;
begin
    if nullif(btrim(p_guild_id), '') is null then
        raise exception 'guild id is required';
    end if;

    perform * from public.reconcile_dank_ticket_categories(btrim(p_guild_id));

    select coalesce(array_agg(chosen.value order by chosen.value), array[]::text[])
      into preserved_keys
      from public.guild_configs gc
      cross join lateral jsonb_array_elements_text(
        coalesce(gc.ticket_category_setup_selected_keys, '[]'::jsonb)
      ) chosen(value)
     where gc.guild_id::text = btrim(p_guild_id)
       and exists (
            select 1 from public.dank_ticket_category_catalog() catalog
             where catalog.category_key = chosen.value
       );

    if p_reset_to_starter then
        select exists (
            select 1
              from public.ticket_categories custom_row
             where custom_row.guild_id::text = btrim(p_guild_id)
               and custom_row.managed_by_dank = false
               and custom_row.is_enabled = true
        ) into custom_exists;

        if coalesce(array_length(preserved_keys, 1), 0) > 0 then
            update public.ticket_categories managed_row
               set is_enabled = managed_row.managed_category_key = any(preserved_keys),
                   is_default = false,
                   updated_at = now()
             where managed_row.guild_id::text = btrim(p_guild_id)
               and managed_row.managed_by_dank = true;

            if 'support' = any(preserved_keys) then
                preserved_default := 'support';
            else
                select catalog.category_key
                  into preserved_default
                  from public.dank_ticket_category_catalog() catalog
                 where catalog.category_key = any(preserved_keys)
                 order by catalog.sort_order, catalog.category_key
                 limit 1;
            end if;

            if preserved_default is not null then
                update public.ticket_categories managed_row
                   set is_default = managed_row.managed_category_key = preserved_default,
                       updated_at = now()
                 where managed_row.guild_id::text = btrim(p_guild_id)
                   and managed_row.managed_by_dank = true
                   and managed_row.is_enabled = true;

                update public.ticket_categories custom_row
                   set is_default = false,
                       updated_at = now()
                 where custom_row.guild_id::text = btrim(p_guild_id)
                   and custom_row.managed_by_dank = false
                   and custom_row.is_default = true;
            end if;
        else
            update public.ticket_categories managed_row
               set is_enabled = (not custom_exists)
                                and managed_row.managed_category_key in ('report', 'appeal', 'support'),
                   is_default = (not custom_exists)
                                and managed_row.managed_category_key = 'support',
                   updated_at = now()
             where managed_row.guild_id::text = btrim(p_guild_id)
               and managed_row.managed_by_dank = true;
        end if;

        if custom_exists and coalesce(array_length(preserved_keys, 1), 0) = 0 then
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
        elsif not custom_exists then
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
           ticket_category_setup_selected_keys = gc.ticket_category_setup_selected_keys,
           setup_completed = false,
           setup_completion_invalidated_at = now(),
           setup_completion_invalidated_reason = 'Ticket menu setup requires confirmation.',
           updated_at = now()
     where gc.guild_id::text = btrim(p_guild_id);
end;
$$;

revoke all on function public.require_dank_ticket_category_setup(text, text, boolean)
    from public, anon, authenticated;
grant execute on function public.require_dank_ticket_category_setup(text, text, boolean)
    to service_role;
