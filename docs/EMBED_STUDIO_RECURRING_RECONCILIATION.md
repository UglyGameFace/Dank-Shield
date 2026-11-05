# Recurring Embed Studio branch reconciliation (2026-11-05)

Original source: `feat/embed-studio-recurring-campaigns-20261024` at `905d93b570`.
The original branch introduced `202610200034_dankshield_embed_studio_campaigns.sql`, but current `main` already owns migration version `202610200034` for artwork budgets. This integration preserves the source SQL as a distinct, additive `202611050001` migration instead of overwriting or renaming the deployed artwork-budget history.

**Not yet a live feature:** Current Embed Studio schedules are one-shot; no recurring UI, enqueue/cancel APIs, delivery worker or deployment flag is added here. Do not apply this schema to production until its runtime, authorization, delivery-boundary checks, timezone/DST tests, disposable PostgreSQL 17 migration smoke and explicit release gates are implemented and verified. This PR is a reviewable preservation step, not proof that recurring campaigns work. The source branch must remain until the final feature integration is validated and archived.

The campaign SQL uses a service-role-only table, RLS, atomic shard-owned claims, bounded claim batches, and at-most-once occurrence accounting. Future integration must check recurring records for forbidden forms/role actions at enqueue and dispatch, revalidate author and channel permissions at send time, handle DST gaps/folds, and ensure only a proven owner of each Discord shard claims jobs. The current one-shot scheduling implementation remains authoritative until then.

**Release:** No credentials, live Supabase, Discloud, or rollback source modified. Intended primary is dedicated Supabase `viamriafkbqhquvrsnua`; preserve IdleGrowOp rollback and keep Discloud PostgreSQL isolated as a backup candidate. Exact-main CI, independently verified off-Discloud backup, risk approval, canary and tested rollback are still mandatory for production.
