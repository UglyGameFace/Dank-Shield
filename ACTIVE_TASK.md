# ACTIVE TASK

## DS-FIX-ANTINUKE-AUTHORIZED-BOT-TRUST — Separate inviter trust from bot-target authorization

**Outcome:** AntiNuke uses one canonical bot-add authorization policy, treats delegated inviter trust separately from pre-approved bot target IDs, keeps hostile reputation authoritative, exposes both concepts clearly in Protection Center, and preserves legitimate bot permissions instead of first-strike containing operational bots.

**Status:** REOPENED AFTER LIVE PERMISSION DAMAGE REPORT. PR #236 merged the bot-add ownership split, but live behavior showed AntiNuke can still strip/kick legitimate bot actors through first-strike and panic containment paths. Repository closure is suspended until that same trust boundary is repaired and exact-head CI passes.

**Merged implementation:** PR #236 — `Fix AntiNuke authorized bot trust ownership`
**Merged SHA:** `eb94e6e46d0c0cfdef2657eb302d0538a6e804fa`

## Active repair

- keep unknown/new bot additions behind canonical bot-add authorization
- treat already-operational bot actors as delegated actors for destructive thresholds rather than first-strike humans
- prevent strict structural overrides and panic peer containment from stripping legitimate bots on a single attributed action
- preserve durable hostile-reputation enforcement so known-hostile bots remain containable
- prevent gateway-fast role/permission rollback from undoing legitimate bot setup grants
- add regressions for bot actor thresholds, bot permission preservation, panic behavior, and bot-add authorization isolation

## Root cause confirmed

1. Native `_process_claimed_destructive_event()` classifies every bot except Dank Shield itself as untrusted, giving it first-strike thresholds.
2. Lockdown's structural policy can force `threshold_override=1` even for operational bots.
3. `_contain_actor()` kicks a proven actor first and, if that fails, strips every manageable role, not only dangerous roles.
4. Guardian panic peer containment can apply that same containment to multiple observed actors.
5. Gateway-fast dangerous-role/member-role paths can roll back permissions before canonical threshold processing.
6. Bot-add authorization itself is separate and must remain strict so automatically treating operational bots as delegated does not let arbitrary bot inviters authorize new bot installs.

## Next step

Repair the canonical/lockdown trust boundary, validate the focused bot-permission regressions plus the full repository suite on the exact repair head, then merge the repair before closing this task record.
