from __future__ import annotations

"""4D.4 private canary operator audit, explicitly migrated and never auto-run.

Only one approved owner/guild/member/revision may be attempted per manual
invocation. Writes contain IDs and status only: no Discord messages, tokens,
account secrets, or configuration blobs.
"""

from uuid import UUID

from .verification_reminder_postgres import _connect

FINAL_STATES = frozenset({
    "sent", "cancelled", "claim_denied", "blocked", "send_failed",
    "unavailable", "disabled", "stale", "excluded",
})


def audit_ready_sync() -> bool:
    try:
        with _connect() as db, db.cursor() as cur:
            cur.execute(
                "select operation_id,guild_id,target_user_id,operator_user_id,"
                "revision,requested_at,finished_at,status,claimed,sent "
                "from public.dank_action_center_canary_audit limit 0"
            )
            cur.execute(
                "select has_table_privilege(current_user,"
                "'public.dank_action_center_canary_audit','SELECT,INSERT,UPDATE')"
            )
            row = cur.fetchone()
            return bool(row and row[0] is True)
    except Exception:
        return False


def begin_canary_audit_sync(
    operation_id: UUID, guild_id: int, user_id: int, operator_id: int, revision: int,
) -> bool:
    if (
        not isinstance(operation_id, UUID)
        or operation_id.version != 4
        or any(type(x) is not int or x <= 0 for x in (guild_id, user_id, operator_id, revision))
    ):
        return False
    try:
        with _connect() as db, db.cursor() as cur:
            cur.execute(
                "insert into public.dank_action_center_canary_audit "
                "(operation_id,guild_id,target_user_id,operator_user_id,revision,status) "
                "values (%s,%s,%s,%s,%s,'requested') on conflict do nothing returning operation_id",
                (operation_id, str(guild_id), str(user_id), str(operator_id), revision),
            )
            return cur.fetchone() is not None
    except Exception:
        # Cannot prove a durable operator request -> no delivery.
        return False


def finish_canary_audit_sync(
    operation_id: UUID, *, status: str, claimed: bool, sent: bool,
) -> bool:
    if (
        not isinstance(operation_id, UUID)
        or operation_id.version != 4
        or status not in FINAL_STATES
        or type(claimed) is not bool or type(sent) is not bool
        or (sent and not claimed)
        or (status == "sent") != sent
    ):
        return False
    try:
        with _connect() as db, db.cursor() as cur:
            cur.execute(
                "update public.dank_action_center_canary_audit "
                "set status=%s, claimed=%s, sent=%s, finished_at=now() "
                "where operation_id=%s and status='requested' returning operation_id",
                (status, claimed, sent, operation_id),
            )
            return cur.fetchone() is not None
    except Exception:
        return False
