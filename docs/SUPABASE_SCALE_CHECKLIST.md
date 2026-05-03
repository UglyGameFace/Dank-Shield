# Supabase Scale Checklist for Stoney Verify

This checklist tracks the database requirements for running Stoney Verify as a public multi-guild Discord bot.

## Hard rules

1. Every customer/guild-owned row must include `guild_id`.
2. Any lookup using Discord IDs should include `guild_id` whenever possible.
3. All destructive operations must be idempotent.
4. Startup/backfill jobs must tolerate transient network/protocol errors.
5. No table should depend on a globally unique channel ID unless it also validates guild ownership.

---

## Required tables / constraints

### `guild_configs`

Purpose: per-guild setup state and channel/role/category IDs.

Required columns:

- `guild_id text primary key`
- `modlog_channel_id text null`
- `transcripts_channel_id text null`
- `ticket_category_id text null`
- `ticket_archive_category_id text null`
- `verify_channel_id text null`
- `vc_verify_channel_id text null`
- `vc_verify_queue_channel_id text null`
- `unverified_role_id text null`
- `verified_role_id text null`
- `resident_role_id text null`
- `staff_role_id text null`
- `setup_completed boolean default false`
- `created_at timestamptz default now()`
- `updated_at timestamptz default now()`

Required constraint:

```sql
alter table guild_configs
  add constraint guild_configs_guild_id_key unique (guild_id);
```

Recommended index:

```sql
create index if not exists idx_guild_configs_guild_id
  on guild_configs (guild_id);
```

---

### `tickets`

Required indexes:

```sql
create index if not exists idx_tickets_guild_channel
  on tickets (guild_id, channel_id);

create index if not exists idx_tickets_guild_thread
  on tickets (guild_id, discord_thread_id);

create index if not exists idx_tickets_guild_user_status
  on tickets (guild_id, user_id, status);

create index if not exists idx_tickets_guild_status_updated
  on tickets (guild_id, status, updated_at desc);

create index if not exists idx_tickets_guild_ticket_number
  on tickets (guild_id, ticket_number);
```

Recommended uniqueness:

```sql
create unique index if not exists uq_tickets_guild_ticket_number
  on tickets (guild_id, ticket_number)
  where ticket_number is not null;
```

---

### `verification_tokens`

Required indexes:

```sql
create index if not exists idx_verification_tokens_guild_token
  on verification_tokens (guild_id, token);

create index if not exists idx_verification_tokens_guild_user_used_expires
  on verification_tokens (guild_id, user_id, used, expires_at);

create index if not exists idx_verification_tokens_expires
  on verification_tokens (expires_at);
```

Recommended uniqueness:

```sql
create unique index if not exists uq_verification_tokens_token
  on verification_tokens (token);
```

---

### `guild_members`

Required indexes / uniqueness:

```sql
create unique index if not exists uq_guild_members_guild_user
  on guild_members (guild_id, user_id);

create index if not exists idx_guild_members_guild_role_state
  on guild_members (guild_id, role_state);

create index if not exists idx_guild_members_guild_updated
  on guild_members (guild_id, updated_at desc);
```

---

### `member_joins`

Required indexes:

```sql
create index if not exists idx_member_joins_guild_user_joined
  on member_joins (guild_id, user_id, joined_at desc);

create index if not exists idx_member_joins_guild_joined
  on member_joins (guild_id, joined_at desc);

create index if not exists idx_member_joins_guild_invite
  on member_joins (guild_id, invite_code);
```

---

### `panels` / ticket panels table

Required indexes:

```sql
create index if not exists idx_panels_guild_channel_message
  on panels (guild_id, channel_id, message_id);

create index if not exists idx_panels_guild_kind
  on panels (guild_id, panel_type);
```

Adjust table/column names if the current project uses a different panel table.

---

### `activity_feed_events` / mod events

Required indexes:

```sql
create index if not exists idx_activity_feed_events_guild_created
  on activity_feed_events (guild_id, created_at desc);

create index if not exists idx_activity_feed_events_guild_user_created
  on activity_feed_events (guild_id, user_id, created_at desc);

create index if not exists idx_activity_feed_events_guild_type_created
  on activity_feed_events (guild_id, event_type, created_at desc);
```

---

## Operational guidance

### Supabase client handling

- Treat `RemoteProtocolError`, `LocalProtocolError`, broken pipe, connection reset, and read errors as retryable.
- Call `reset_supabase()` before retrying protocol/connection errors.
- Use bounded retries with jitter/backoff.
- Do not let one failed guild poison all guild startup work.

### Backfills

Backfills must be:

- idempotent;
- chunked;
- resumable;
- timeout-bounded;
- per-guild isolated.

### Startup jobs

Startup must not depend on all database maintenance succeeding. The bot should be usable even if optional startup syncs partially fail.

---

## Migration order

1. Create/verify `guild_configs`.
2. Backfill guild config rows for existing three guilds.
3. Add ticket indexes.
4. Add token indexes.
5. Add member indexes.
6. Add panel indexes.
7. Add activity/modlog indexes.
8. Migrate code paths from env globals to shared guild config resolver.
9. Remove customer-server dependence on global env IDs.
