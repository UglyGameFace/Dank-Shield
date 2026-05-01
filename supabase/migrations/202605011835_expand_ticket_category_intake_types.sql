-- ============================================================
-- Stoney Verify migration
-- 202605011835_expand_ticket_category_intake_types.sql
-- ------------------------------------------------------------
-- Purpose:
--   Make ticket category routing production-safe by allowing the real
--   intake types the bot uses instead of forcing everything into
--   general/custom.
--
-- Why:
--   Auto-build/panel bootstrap creates useful routing categories such as
--   account, purchase, and bug. Older databases may still have a narrow
--   ticket_categories_intake_type_check constraint that rejects those rows.
--
-- Safe to run multiple times:
--   Yes. It drops/recreates only the intake_type check constraint and creates
--   supporting indexes with IF NOT EXISTS.
-- ============================================================

BEGIN;

DO $$
DECLARE
    existing_constraint_name text;
    allowed_types_sql text := quote_literal('general') || ',' ||
                              quote_literal('support') || ',' ||
                              quote_literal('verification') || ',' ||
                              quote_literal('appeal') || ',' ||
                              quote_literal('report') || ',' ||
                              quote_literal('question') || ',' ||
                              quote_literal('partnership') || ',' ||
                              quote_literal('account') || ',' ||
                              quote_literal('purchase') || ',' ||
                              quote_literal('billing') || ',' ||
                              quote_literal('refund') || ',' ||
                              quote_literal('technical') || ',' ||
                              quote_literal('bug') || ',' ||
                              quote_literal('staff') || ',' ||
                              quote_literal('ghost') || ',' ||
                              quote_literal('custom') || ',' ||
                              quote_literal('other');
BEGIN
    IF to_regclass('public.ticket_categories') IS NULL THEN
        RAISE NOTICE 'Skipping migration: public.ticket_categories does not exist yet.';
        RETURN;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'ticket_categories'
          AND column_name = 'intake_type'
    ) THEN
        RAISE NOTICE 'Skipping intake_type constraint migration: public.ticket_categories.intake_type does not exist.';
        RETURN;
    END IF;

    -- Normalize older experimental values if they exist. These updates are
    -- intentionally conservative and do not touch unknown/custom values.
    UPDATE public.ticket_categories
       SET intake_type = 'technical'
     WHERE intake_type IN ('tech', 'technical_support', 'technical-support');

    UPDATE public.ticket_categories
       SET intake_type = 'purchase'
     WHERE intake_type IN ('payments', 'payment', 'payments_refunds', 'payments-refunds', 'orders');

    UPDATE public.ticket_categories
       SET intake_type = 'account'
     WHERE intake_type IN ('account_access', 'account-access', 'login', 'access');

    UPDATE public.ticket_categories
       SET intake_type = 'bug'
     WHERE intake_type IN ('bugs', 'bug_report', 'bug-report');

    SELECT c.conname
      INTO existing_constraint_name
      FROM pg_constraint c
      JOIN pg_class t ON t.oid = c.conrelid
      JOIN pg_namespace n ON n.oid = t.relnamespace
     WHERE n.nspname = 'public'
       AND t.relname = 'ticket_categories'
       AND c.conname = 'ticket_categories_intake_type_check'
     LIMIT 1;

    IF existing_constraint_name IS NOT NULL THEN
        EXECUTE format(
            'ALTER TABLE public.ticket_categories DROP CONSTRAINT %I',
            existing_constraint_name
        );
    END IF;

    EXECUTE format(
        'ALTER TABLE public.ticket_categories ADD CONSTRAINT ticket_categories_intake_type_check CHECK (intake_type IS NULL OR intake_type = ANY (ARRAY[%s]::text[]))',
        allowed_types_sql
    );

    RAISE NOTICE 'ticket_categories_intake_type_check now allows production routing types.';
END $$;

-- Basic production indexes. These are non-destructive and safe even if the
-- table already contains duplicate slugs from old runs. A future cleanup
-- migration can add a unique (guild_id, slug) index after duplicates are gone.
DO $$
BEGIN
    IF to_regclass('public.ticket_categories') IS NULL THEN
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'ticket_categories' AND column_name = 'guild_id'
    ) THEN
        CREATE INDEX IF NOT EXISTS idx_ticket_categories_guild_id
            ON public.ticket_categories (guild_id);
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'ticket_categories' AND column_name = 'guild_id'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'ticket_categories' AND column_name = 'slug'
    ) THEN
        CREATE INDEX IF NOT EXISTS idx_ticket_categories_guild_slug
            ON public.ticket_categories (guild_id, slug);
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'ticket_categories' AND column_name = 'guild_id'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'ticket_categories' AND column_name = 'intake_type'
    ) THEN
        CREATE INDEX IF NOT EXISTS idx_ticket_categories_guild_intake_type
            ON public.ticket_categories (guild_id, intake_type);
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'ticket_categories' AND column_name = 'guild_id'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'ticket_categories' AND column_name = 'is_default'
    ) THEN
        CREATE INDEX IF NOT EXISTS idx_ticket_categories_guild_default
            ON public.ticket_categories (guild_id, is_default)
            WHERE is_default = true;
    END IF;
END $$;

COMMIT;
