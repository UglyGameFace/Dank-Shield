# VC expired-session refresh patch

Issue: #3
Prepared branch: `fix/vc-expired-session-refresh`

This file documents the exact surgical code patch required for VC session expiry handling.

Do **not** treat this as the runtime fix by itself. The runtime fix must be applied to owner files only:

- `stoney_verify/vc_sessions.py`
- `stoney_verify/vc_verify.py`

No startup guards. No runtime monkey patches.

---

## 1. Patch `stoney_verify/vc_sessions.py::ensure_session()`

Inside `ensure_session()`, after this line:

```py
desired_access_minutes = int(access_minutes or row.get("access_minutes") or _access_minutes())
```

add:

```py
        status = _normalize_status(row.get("status"))
        expired_or_terminal = _row_is_expired(row) or status in {"EXPIRED", "COMPLETED", "CANCELED"}
        if expired_or_terminal:
            now_iso = _utc_iso()
            refresh_meta = _merge_meta(
                row.get("meta"),
                {
                    "owner_confirmed": False,
                    "staff_confirmed": False,
                    "unlocked": False,
                    "last_action": "refresh_expired_session",
                    "last_action_at": now_iso,
                    "expired_refresh_from_status": status,
                    "expired_refresh_previous_revoke_at": row.get("revoke_at"),
                },
            )
            patch.update(
                {
                    "status": "PENDING",
                    "ticket_channel_id": desired_ticket_channel_id,
                    "requester_id": desired_requester_id,
                    "owner_id": desired_owner_id,
                    "vc_channel_id": desired_vc_channel_id,
                    "queue_channel_id": desired_queue_channel_id,
                    "queue_message_id": None,
                    "accepted_at": None,
                    "accepted_by": None,
                    "started_at": None,
                    "completed_at": None,
                    "canceled_at": None,
                    "canceled_by": None,
                    "access_minutes": desired_access_minutes,
                    "revoke_at": _utc_iso(_utcnow() + timedelta(minutes=desired_access_minutes)),
                    "last_watchdog_at": None,
                    "meta": refresh_meta,
                }
            )
```

### Expected behavior

- Expired/terminal rows refresh into `PENDING`.
- `revoke_at` becomes fresh.
- stale UI state is cleared by resetting `queue_message_id`.
- DB and memory are updated through the existing `_update_local()` / `_db_update()` path.

---

## 2. Patch `stoney_verify/vc_verify.py::_ensure_session_backing()`

Replace this early return:

```py
    row = _get_session_row(tok)
    if row:
        return row
```

with:

```py
    row = _get_session_row(tok)
    if row:
        status = str(row.get("status") or "").upper().strip()
        if status not in {"EXPIRED", "COMPLETED", "DONE", "CANCELED", "CANCELLED"}:
            return row
```

### Expected behavior

- Active rows still return immediately.
- Expired/terminal rows fall through to `vc_sessions.ensure_session(...)` and get refreshed.

---

## Required validation

- [ ] Create VC session and let `revoke_at` expire.
- [ ] Request VC verify again with same token/path.
- [ ] Confirm returned row is `PENDING`, not `EXPIRED`.
- [ ] Confirm `revoke_at` is fresh.
- [ ] Confirm `queue_message_id` resets so stale UI is not reused.
- [ ] Confirm active non-expired sessions do not reset.
- [ ] Confirm sessions with live users are extended by sweeper, not killed.
- [ ] Confirm empty expired sessions relock and transition to `EXPIRED`.

---

## Why this patch is safe

- Owner files only.
- No startup guards.
- No runtime monkey patches.
- Does not touch Discord channel permissions directly.
- Uses existing memory/DB update flow.
