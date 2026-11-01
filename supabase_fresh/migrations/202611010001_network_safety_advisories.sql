-- Optional cross-server safety advisory. New dedicated schema only.
-- Never auto-import per-guild AntiNuke reputation or enable sharing by default.
-- Deploy after a reviewed release using the dedicated DankShield database connection.
CREATE TABLE IF NOT EXISTS public.network_safety_consent (
    guild_id text PRIMARY KEY,
    guild_label text NOT NULL DEFAULT '',
    publish_enabled boolean NOT NULL DEFAULT false,
    receive_enabled boolean NOT NULL DEFAULT false,
    owner_id text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.network_safety_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_key text NOT NULL UNIQUE,
    reporting_guild_id text NOT NULL REFERENCES public.network_safety_consent(guild_id),
    subject_user_id text NOT NULL,
    author_id text NOT NULL,
    action text NOT NULL CHECK (action IN ('incident','correction','withdrawal')),
    category text NOT NULL CHECK (category IN ('harassment','scam','spam','raid','threats','other')),
    reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 8 AND 500),
    parent_event_id uuid REFERENCES public.network_safety_events(id),
    evidence_level text NOT NULL DEFAULT 'owner_attested'
        CHECK (evidence_level IN ('owner_attested','verified_audit')),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT network_safety_action_parent CHECK (
        (action = 'incident' AND parent_event_id IS NULL) OR
        (action <> 'incident' AND parent_event_id IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS network_safety_events_subject_time
  ON public.network_safety_events(subject_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS network_safety_events_source_subject
  ON public.network_safety_events(reporting_guild_id, subject_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS network_safety_events_parent
  ON public.network_safety_events(parent_event_id, created_at DESC);

CREATE TABLE IF NOT EXISTS public.network_safety_alert_receipts (
    receiving_guild_id text NOT NULL REFERENCES public.network_safety_consent(guild_id),
    subject_user_id text NOT NULL,
    digest text NOT NULL,
    claim_at timestamptz NOT NULL DEFAULT now(),
    claim_token uuid NOT NULL,
    PRIMARY KEY (receiving_guild_id, subject_user_id)
);

-- Atomic join-alert reservation across workers and shards.
-- A new digest may alert only after a short cooldown; repeated joins are suppressed.
CREATE OR REPLACE FUNCTION public.claim_network_safety_alert(
    p_guild text, p_user text, p_digest text, p_token uuid
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp AS $$
DECLARE claimed boolean := false;
BEGIN
    IF p_guild !~ '^[0-9]{15,22}$' OR p_user !~ '^[0-9]{15,22}$'
       OR length(p_digest) <> 64 OR p_token IS NULL THEN
        RETURN false;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.network_safety_consent
                   WHERE guild_id = p_guild AND receive_enabled) THEN
        RETURN false;
    END IF;
    INSERT INTO public.network_safety_alert_receipts
        (receiving_guild_id, subject_user_id, digest, claim_token, claim_at)
    VALUES (p_guild, p_user, p_digest, p_token, now())
    ON CONFLICT (receiving_guild_id, subject_user_id) DO UPDATE
      SET digest = excluded.digest, claim_token = excluded.claim_token,
          claim_at = excluded.claim_at
    WHERE
      (public.network_safety_alert_receipts.digest = excluded.digest
       AND public.network_safety_alert_receipts.claim_at < now() - interval '24 hours')
      OR
      (public.network_safety_alert_receipts.digest <> excluded.digest
       AND public.network_safety_alert_receipts.claim_at < now() - interval '1 hour')
    RETURNING true INTO claimed;
    RETURN coalesce(claimed, false);
END;
$$;

-- Fail-closed RLS; service-role backend is the only client.
ALTER TABLE public.network_safety_consent ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.network_safety_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.network_safety_alert_receipts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.network_safety_consent,
  public.network_safety_events, public.network_safety_alert_receipts
  FROM public, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.network_safety_consent TO service_role;
GRANT SELECT, INSERT ON public.network_safety_events TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.network_safety_alert_receipts TO service_role;
REVOKE ALL ON FUNCTION public.claim_network_safety_alert(text,text,text,uuid)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_network_safety_alert(text,text,text,uuid)
  TO service_role;
