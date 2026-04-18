# Health Check Guide

## Endpoint

`GET /health`

This endpoint is intentionally the only route that should remain open without API auth. It is used for liveness checks and operator monitoring.

## Expected response

A healthy bot API should return JSON similar to:

```json
{
  "ok": true,
  "status": "online",
  "guild_count": 1,
  "api": "structured_bot_api"
}
```

## Operator checklist

### Good
- `ok=true`
- `status=online`
- guild count is nonzero when the bot is connected

### Bad
- connection refused
- non-200 response
- empty or malformed JSON
- non-health routes accepting requests without auth

## Release checklist

Before public release, confirm:

- health endpoint responds successfully
- unauthenticated non-health routes return `401`
- `BOT_API_SHARED_SECRET` is configured
- bind host and port match the intended deployment
- logs show the API started in secure mode

## Suggested monitoring behavior

- poll `/health` from your hosting monitor
- alert on repeated failures
- alert if bot process is up but guild count unexpectedly drops to zero
