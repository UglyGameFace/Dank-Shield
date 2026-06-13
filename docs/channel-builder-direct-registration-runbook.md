# Channel Builder Direct Registration Runbook

This runbook tracks the safe path from temporary compatibility routing to direct structured Bot API routing.

## Current covered path

1. Channel Builder runtime logic lives in `stoney_verify/services/channel_builder_runtime.py`.
2. Channel Builder rollback logic lives in `stoney_verify/services/channel_builder_rollback_runtime.py`.
3. Structured Bot API routes live in `stoney_verify/api_new/channel_builder_routes.py`.
4. `tools/patch_channel_builder_server_routes.py` safely patches `stoney_verify/api_new/server.py`.
5. `.github/workflows/channel-builder-server-direct-registration.yml` can run that patch from GitHub Actions and commit only `server.py`.
6. `tools/audit_channel_builder_queue.py` verifies the route module, services, patcher, workflow, and removed obsolete bridge files.

## Manual workflow to run

Run the GitHub Actions workflow named:

`Channel Builder Server Direct Registration`

Expected result:

- `server.py` imports `register_channel_builder_routes`.
- `server.py` calls `register_channel_builder_routes(app, sys.modules[__name__])` inside `start_api`.
- The Channel Builder queue audit passes.
- A commit named `Register Channel Builder routes directly` is pushed if a change was needed.

## Final cleanup after the workflow succeeds

After direct registration is confirmed:

1. Remove `channel_builder_api_guard` from the integration guard list in `stoney_verify/startup_guards/guild_operation_queue_guard.py`.
2. Remove `stoney_verify/startup_guards/channel_builder_api_guard.py`.
3. Update `tools/audit_channel_builder_queue.py` so `channel_builder_api_guard.py` is listed as removed.
4. Remove the manual direct-registration workflow if it is no longer needed.
5. Keep `tools/patch_channel_builder_server_routes.py` only if future safety checks need it; otherwise remove it after direct registration is locked in.

## Safety rule

Do not remove the last compatibility shim until `server.py` has direct Channel Builder route registration and the audit passes.
