# Security Guide

This repository includes a structured bot API that can perform powerful ticket and member actions. Treat it like an internal admin surface, not a public toy endpoint.

## Required baseline

For public or production-style deployments:

- set `BOT_API_REQUIRE_AUTH=true`
- set a strong `BOT_API_SHARED_SECRET`
- keep `BOT_API_BIND_HOST=127.0.0.1` unless the API is behind a trusted reverse proxy
- do **not** expose the bot API directly to the public internet without auth and network controls

## Supported auth headers

For all non-health routes, send one of these headers with the shared secret:

- `Authorization: Bearer <secret>`
- `X-API-Key: <secret>`
- `X-Stoney-Internal-Auth: <secret>`

## Deployment recommendations

### Best option
Run the structured bot API on localhost only and let only trusted internal services talk to it.

### Acceptable option
Place it behind a reverse proxy that:

- restricts inbound access
- terminates TLS
- forwards only from trusted sources
- keeps logs for operator review

## Insecure local development mode

You may temporarily set:

- `BOT_API_ALLOW_INSECURE=true`
- `BOT_API_REQUIRE_AUTH=false`

Only do this for local development. Do not use insecure mode on a public host.

## Secrets

- never commit real secrets to GitHub
- rotate `BOT_API_SHARED_SECRET` if it was ever exposed
- keep Supabase service-role credentials private

## Operational checks

Before release, verify that:

- `/health` is reachable
- non-health routes reject unauthenticated requests
- the API does not bind wider than intended
- logs clearly state whether auth is enforced
