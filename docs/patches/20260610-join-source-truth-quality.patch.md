# Join source truth quality patch

Issue: #2
Prepared branch: `fix/join-source-truth-quality`

This patch makes join-source attribution safer for dashboards and staff views by recording whether attribution is confirmed, partial, or unknown.

## Runtime targets

- `stoney_verify/events.py`
- `stoney_verify/members_new/sync_service.py`

## Why

The bot already detects invite usage, vanity joins, cache-warming states, and unresolved joins. The missing piece is that weak states can be stored as normal-looking values without an explicit trust signal.

That makes dashboards more likely to silently trust weak or unresolved attribution.

## Patch helper

Run:

```bash
git checkout fix/join-source-truth-quality
python scripts/apply_join_source_truth_quality_fix.py
```

Then:

```bash
git diff -- stoney_verify/events.py stoney_verify/members_new/sync_service.py
python -m py_compile stoney_verify/events.py stoney_verify/members_new/sync_service.py
git add stoney_verify/events.py stoney_verify/members_new/sync_service.py
git commit -m "Track join source truth quality"
git push
```

## Intended runtime behavior

- Confirmed invite delta writes `entry_truth_quality=confirmed` and high confidence.
- Vanity invite writes confirmed attribution.
- Invite cache warming writes partial attribution with low/medium confidence.
- Invite unresolved writes partial attribution.
- Invite tracking unavailable writes unknown attribution.
- `guild_members`, `member_joins`, and `member_events.metadata` preserve the same truth quality values where schema allows.
- `members_new/sync_service.py` backfills/latest-row enrichment keeps truth quality instead of flattening everything into trusted-looking strings.

## Validation checklist

- [ ] Normal invite join stores confirmed quality and inviter/invite code.
- [ ] Vanity join stores confirmed vanity quality.
- [ ] Missing invite permissions stores unknown quality.
- [ ] First join during cache warming stores partial quality.
- [ ] Unresolved invite delta stores partial quality.
- [ ] Member dashboard can distinguish confirmed vs partial vs unknown attribution.
- [ ] Missing optional DB columns do not crash member sync.

## Do not do

- Do not overwrite confirmed invite attribution with weaker sync inference.
- Do not present unresolved/cache-warming attribution as confirmed.
- Do not block member joins because invite attribution is partial.
