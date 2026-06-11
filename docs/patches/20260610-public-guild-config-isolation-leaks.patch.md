# Public guild config isolation leak patch

Issue: #51
Prepared branch: `fix/public-guild-config-isolation-leaks`

This patch addresses a public multi-server isolation bug where a fresh guild can still see old/home-guild IDs in runtime verification and logging paths.

## Runtime targets

- `stoney_verify/events.py`
- `stoney_verify/modlog.py`

## Patch helper

Run:

```bash
git checkout fix/public-guild-config-isolation-leaks
python scripts/apply_public_guild_config_isolation_fix.py
```

Then:

```bash
git diff -- stoney_verify/events.py stoney_verify/modlog.py
python -m py_compile stoney_verify/events.py stoney_verify/modlog.py
git add stoney_verify/events.py stoney_verify/modlog.py
git commit -m "Fix public guild config isolation leaks"
git push
```

## Intended runtime behavior

- Fresh public guilds never inherit home-guild role IDs.
- Fresh public guilds never inherit home-guild modlog channel IDs.
- Join verification enforcement stays inactive when per-guild role config is incomplete.
- Modlog still falls back to same-guild channel-name discovery.
- Home guild may still use global env fallback if public isolation allows it.

## Validation checklist

- [ ] Add bot to a fresh test guild.
- [ ] Confirm isolated setup row is created.
- [ ] Confirm no warning about home-guild modlog channel in fresh guild.
- [ ] Confirm no repeated missing-role spam for home guild role IDs.
- [ ] Confirm join verification timer does not start if Unverified role is not configured for that guild.
- [ ] Confirm protected setup/admin/server-owner users are not acted on during incomplete setup.
- [ ] Finish setup and save roles/channels for that guild.
- [ ] Confirm join verification uses that guild's Unverified role.
- [ ] Confirm modlog posts to that guild's mod-log channel.

## Do not do

- No startup/runtime monkey patches.
- No role-name-only enforcement.
- No global ID fallback for random public guilds.
- Do not mark #51 complete until runtime files are patched and tested.
