# Approval source truth quality patch

Issue: #2
Prepared branch: `fix/approval-source-truth-quality`

This patch continues the join-source truth work by making verification staff decisions carry the same trust fields added by PR #53.

## Runtime target

- `stoney_verify/verification_new/service.py`

## Why

`_sync_member_verification_context()` already writes staff ID, staff name, reason text, source ticket ID, and verification source.

The missing piece is that this explicit staff/ticket source should be marked as confirmed, high-confidence attribution so dashboards can distinguish it from partial invite attribution.

## Intended runtime behavior

- Staff/ticket verification updates write confirmed truth quality.
- Source ticket IDs stay linked to the member and latest join row.
- `member_events.metadata` includes the same truth fields even when optional DB columns are not available.
- If optional truth-quality columns are missing from older tables, the update falls back without those optional fields instead of losing the whole context update.

## Validation checklist

- [ ] Approve from a verification ticket.
- [ ] Confirm `guild_members.approved_by` and `approved_by_name` update.
- [ ] Confirm source ticket ID stays linked.
- [ ] Confirm entry truth quality is confirmed with high confidence when schema supports it.
- [ ] Confirm member event metadata includes the same truth fields.
- [ ] Confirm older schemas without optional truth columns do not lose the basic approval context update.

## Do not do

- Do not overwrite confirmed invite attribution with weaker data.
- Do not require optional columns before preserving core approval context.
- Do not close #2 from this patch alone; dashboard display and conflict handling still remain.
