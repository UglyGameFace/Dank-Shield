# Dank Cinema separation: Discloud cutover and verification

**Scope:** Dank Shield app `stoneyverify` (`TYPE=site`, `MAIN=main.py`). Cinema source was preserved in the separate `UglyGameFace/Dank-Cinema` repository, stage-1 PR #2. This plan removes Cinema execution from Shield; **it does not activate or deploy Cinema elsewhere**.

## 0. Incident safety before code rollout

- Discloud reported movie-torrent activity on 2026-10-25. Confirm the notice with Discloud support and request exact application, dates and material.
- Stop any potentially unauthorized downloading/seeding using Discloud's application controls. GitHub changes do not stop already-running processes.
- Preserve only relevant incident/diagnostic metadata securely. Do not continue or disguise activity through a VPN or alternate host.

## 1. Release gate (GitHub)

- **Source extraction is merged:** Shield PRs #729–#732 removed Cinema startup,
  routes, packaging, four Cinema-specific workflows and archived Python/assets.
  Source head `9a0594664e896ccb56dd2fc55f2ce770d580d5e9` passed the full
  [Dank Shield CI run #40248955089](https://github.com/UglyGameFace/Dank-Shield/actions/runs/40248955089).
  That success **does not** establish the deployed Discloud SHA or live process state.
- For each later Shield release, require its own successful exact-head CI run
  and `tools/audit_cinema_discloud_package.py` scan, not the historic PR check.
- Confirm the new `share_router_public_server.py` is the only Site listener started by both `command_runtime.setup_hook` and `app.on_ready`. Both must be idempotent.
- Verify `api_new/server`, `share_router_runtime`, public command registration, and lifecycle hooks import none of the archived Cinema/torrent modules.
- Verify `requirements.txt` no longer installs `libtorrent` and that `.discloudignore` excludes every legacy Cinema filename (bare name as well as historical relative path).
- Cinema [PR #11](https://github.com/UglyGameFace/Dank-Cinema/pull/11) also
  preserved the final unreferenced `media_metadata.py` parser and its tests,
  with matching original Git blob SHA and a passing independent CI run.
  The Shield source/test copies have now been retired; shared Share Router
  media code is intentionally untouched.
- Archive PRs #2–#4 are merged in the independent Cinema repository;
  the two formerly missing non-runtime files were subsequently preserved.
  Historic Shield SQL migration history remains deliberately in place, because
  deleting migration versions can break database version bookkeeping. The
  Cinema-only tables must not be silently dropped while retention or migration
  ownership is unresolved.

## 2. Production cutover (Android Discloud dashboard)

- Deploy the **merged Shield main SHA**, not the extraction feature branch or an old ZIP. Verify Discloud reports a fresh process start on that deployment.
- Set `DANK_SHARE_VIDEO_PUBLIC_BASE_URL` to the current trusted HTTPS Shield Site URL, if configured. The legacy `DANK_MEDIA_PUBLIC_BASE_URL` is accepted as a transition fallback for ordinary Share Router videos, not Cinema.
- The port remains 8080 by default and the Site app remains `TYPE=site`. Do not change its public URL or Discord bot token just to extract Cinema.
- Inspect Discloud application logs from the new process start. Expect `Share Router public server listening on ...`; **do not accept** `Torrent media server started`, `Cinema feed refresh worker started`, or other Cinema startup messages.
- Confirm old transfers and downloader/torrent/rolling gateway processes are stopped and there are no orphan scheduled jobs, stale cache downloads, or seeding. Discloud process/file inspection must occur through authorized administrative access; logs alone are insufficient.
- Remove unused Cinema-specific secrets/environment settings only after confirming that Share Router is using its own service settings. Preserve necessary diagnostic records; do not delete shared Discord guild settings or unrelated data.

## 3. HTTP and Discord acceptance

- `GET https://<SHIELD_SITE>/health` should be HTTP 200 JSON with `service: "dank_share_router_public"` and `cinema: "detached"`.
- `GET https://<SHIELD_SITE>/` returns a Share Router message, not the Cinema website or a redirect.
- `GET https://<SHIELD_SITE>/cinema` and `GET https://<SHIELD_SITE>/media/torrent/stream/not-a-token` must both return **404**, never video data, a player, or a torrent session.
- Confirm new `/cinema` and `/movie` commands do **not** appear in the current Discord bot tree after global sync. Stale old controls should say Cinema is now separate and must **not** call Cinema internals.
- Exercise `/dank`, `/verify`, `/ticket`, `/toke`, and ordinary Share Router: one standard link forward and one ordinary video-share route. Check host CPU/RAM and Discord API pacing remains reasonable.
- Check each configured guild uses the same saved settings and no permissions, roles, ticket, verification or member data was changed.

## 4. Rollback and escalation

- If Shield health or unrelated features fail, pause the deployment and inspect error logs/config; do **not** blindly roll back to an old build that restarts prohibited torrent activity.
- A safe rollback branch must retain the Cinema-off startup guards, disabled torrent path and restricted deployment packaging.
- Confirm remediation with Discloud support. A passed CI run, code merge, VPN, repository split or app rename does not itself satisfy a copyright notice.

## 5. Standalone Cinema status

- `UglyGameFace/Dank-Cinema` preserves the full historical source and has
  independently tested offline bootstrap, signing, storage and Discord identity
  components. It is not a deployed standalone media application.
- Independence is incomplete: code still depends on Shield's Discord bot, permissions, guild config, database adapters and storage. A separate backend, own app credentials, safe media-source policy, and independent tests are required **before any standalone deployment**.
- Do not run archived Cinema code on another server until its operation and media sources meet hosting rules and applicable licenses.
