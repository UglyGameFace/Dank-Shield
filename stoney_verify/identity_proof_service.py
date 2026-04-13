from __future__ import annotations

import time
import random
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .globals import get_supabase, reset_supabase


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def _safe_str(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _safe_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _safe_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe_json(v) for v in value]
    try:
        return str(value)
    except Exception:
        return None


def _is_retryable_db_error(error: Exception) -> bool:
    text = repr(error).lower()
    markers = (
        "remoteprotocolerror",
        "server disconnected",
        "connection reset",
        "connection aborted",
        "temporarily unavailable",
        "timeout",
        "timed out",
        "eof",
        "network",
        "closed connection",
        "connection refused",
        "httpcore",
        "httpx",
        "connection terminated",
        "stream closed",
        "broken pipe",
        "pool",
        "too many requests",
    )
    return any(marker in text for marker in markers)


def _sleep_backoff(attempt: int) -> None:
    base = min(0.35 * (2 ** max(0, attempt - 1)), 3.0)
    jitter = random.uniform(0.05, 0.25)
    time.sleep(base + jitter)


def _execute_db_op(op_name: str, executor: Callable[[], Any], max_attempts: int = 5):
    last_error: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            return executor()
        except Exception as e:
            last_error = e
            if _is_retryable_db_error(e) and attempt < max_attempts:
                try:
                    reset_supabase()
                except Exception:
                    pass
                print(
                    f"⚠️ {op_name}: transient DB error on attempt "
                    f"{attempt}/{max_attempts}: {repr(e)}"
                )
                _sleep_backoff(attempt)
                continue
            raise

    raise last_error if last_error is not None else RuntimeError(f"{op_name} failed")


def normalize_user_pair(user_a_id: Any, user_b_id: Any) -> Tuple[str, str]:
    a = _safe_int(user_a_id, 0)
    b = _safe_int(user_b_id, 0)

    if a <= 0 or b <= 0:
        raise ValueError("Both user IDs must be valid positive integers.")
    if a == b:
        raise ValueError("A manual alt link cannot target the same user twice.")

    if a < b:
        return (str(a), str(b))
    return (str(b), str(a))


def get_identity_proofs_for_user(
    *,
    guild_id: Any,
    user_id: Any,
    active_only: bool = True,
) -> List[Dict[str, Any]]:
    guild_text = _safe_str(guild_id)
    user_text = _safe_str(user_id)

    if not guild_text or not user_text:
        return []

    def _read() -> List[Dict[str, Any]]:
        sb = get_supabase()
        if sb is None:
            return []

        q = (
            sb.table("identity_proofs")
            .select("*")
            .eq("guild_id", guild_text)
            .eq("user_id", user_text)
            .order("created_at", desc=True)
            .limit(50)
        )
        if active_only:
            q = q.eq("status", "active")

        res = q.execute()
        rows = getattr(res, "data", None) or []
        return [dict(r) for r in rows if isinstance(r, dict)]

    try:
        return _execute_db_op("get identity proofs for user", _read)
    except Exception as e:
        print("⚠️ get_identity_proofs_for_user failed:", repr(e))
        return []


def create_identity_proof(
    *,
    guild_id: Any,
    user_id: Any,
    identity_fingerprint: str,
    source: str,
    created_by: Optional[str] = None,
    fingerprint_version: str = "v1",
    confidence: int = 100,
    notes: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    guild_text = _safe_str(guild_id)
    user_text = _safe_str(user_id)
    fingerprint_text = _safe_str(identity_fingerprint)
    source_text = _safe_str(source)
    version_text = _safe_str(fingerprint_version) or "v1"

    if not guild_text:
        raise ValueError("guild_id is required")
    if not user_text:
        raise ValueError("user_id is required")
    if not fingerprint_text:
        raise ValueError("identity_fingerprint is required")
    if not source_text:
        raise ValueError("source is required")

    payload = {
        "guild_id": guild_text,
        "user_id": user_text,
        "identity_fingerprint": fingerprint_text,
        "fingerprint_version": version_text,
        "source": source_text,
        "confidence": max(0, min(int(confidence or 100), 100)),
        "status": "active",
        "created_by": _safe_str(created_by) or None,
        "notes": _safe_str(notes) or None,
        "evidence": _safe_json(evidence or {}),
    }

    def _write() -> Dict[str, Any]:
        sb = get_supabase()
        if sb is None:
            raise RuntimeError("Supabase is not configured.")

        existing_res = (
            sb.table("identity_proofs")
            .select("*")
            .eq("guild_id", guild_text)
            .eq("user_id", user_text)
            .eq("identity_fingerprint", fingerprint_text)
            .eq("source", source_text)
            .eq("fingerprint_version", version_text)
            .eq("status", "active")
            .limit(1)
            .execute()
        )
        existing_rows = getattr(existing_res, "data", None) or []
        if existing_rows:
            existing = dict(existing_rows[0])
            proof_id = existing.get("id")
            update_payload = {
                "confidence": payload["confidence"],
                "created_by": payload["created_by"] or existing.get("created_by"),
                "notes": payload["notes"] or existing.get("notes"),
                "evidence": payload["evidence"] if payload["evidence"] != {} else existing.get("evidence") or {},
            }
            (
                sb.table("identity_proofs")
                .update(update_payload)
                .eq("id", proof_id)
                .execute()
            )
            reread = (
                sb.table("identity_proofs")
                .select("*")
                .eq("id", proof_id)
                .limit(1)
                .execute()
            )
            reread_rows = getattr(reread, "data", None) or []
            return dict(reread_rows[0]) if reread_rows else existing

        sb.table("identity_proofs").insert(payload).execute()

        reread = (
            sb.table("identity_proofs")
            .select("*")
            .eq("guild_id", guild_text)
            .eq("user_id", user_text)
            .eq("identity_fingerprint", fingerprint_text)
            .eq("source", source_text)
            .eq("fingerprint_version", version_text)
            .eq("status", "active")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = getattr(reread, "data", None) or []
        if not rows:
            raise RuntimeError("Identity proof insert could not be re-read.")
        return dict(rows[0])

    return _execute_db_op("create identity proof", _write)


def revoke_identity_proof(
    *,
    proof_id: Any,
    revoked_by: Optional[str] = None,
    notes: Optional[str] = None,
) -> bool:
    proof_id_int = _safe_int(proof_id, 0)
    if proof_id_int <= 0:
        return False

    def _write() -> bool:
        sb = get_supabase()
        if sb is None:
            return False

        (
            sb.table("identity_proofs")
            .update(
                {
                    "status": "revoked",
                    "revoked_at": now_iso(),
                    "revoked_by": _safe_str(revoked_by) or None,
                    "notes": _safe_str(notes) or None,
                }
            )
            .eq("id", proof_id_int)
            .eq("status", "active")
            .execute()
        )
        return True

    try:
        return bool(_execute_db_op("revoke identity proof", _write))
    except Exception as e:
        print("⚠️ revoke_identity_proof failed:", repr(e))
        return False


def get_manual_links_for_user(
    *,
    guild_id: Any,
    user_id: Any,
    active_only: bool = True,
) -> List[Dict[str, Any]]:
    guild_text = _safe_str(guild_id)
    user_text = _safe_str(user_id)

    if not guild_text or not user_text:
        return []

    def _read() -> List[Dict[str, Any]]:
        sb = get_supabase()
        if sb is None:
            return []

        out: List[Dict[str, Any]] = []
        seen: Set[str] = set()

        for field in ("user_a_id", "user_b_id"):
            q = (
                sb.table("manual_alt_links")
                .select("*")
                .eq("guild_id", guild_text)
                .eq(field, user_text)
                .order("created_at", desc=True)
                .limit(50)
            )
            if active_only:
                q = q.eq("status", "active")

            res = q.execute()
            for row in (getattr(res, "data", None) or []):
                if not isinstance(row, dict):
                    continue
                key = _safe_str(row.get("id")) or repr(sorted(row.items()))
                if key in seen:
                    continue
                seen.add(key)
                out.append(dict(row))

        return out

    try:
        return _execute_db_op("get manual links for user", _read)
    except Exception as e:
        print("⚠️ get_manual_links_for_user failed:", repr(e))
        return []


def set_manual_alt_link(
    *,
    guild_id: Any,
    user_a_id: Any,
    user_b_id: Any,
    link_type: str,
    created_by: str,
    reason: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    guild_text = _safe_str(guild_id)
    if not guild_text:
        raise ValueError("guild_id is required")

    pair_a, pair_b = normalize_user_pair(user_a_id, user_b_id)
    link_type_text = _safe_str(link_type).lower()
    created_by_text = _safe_str(created_by)

    if link_type_text not in {"confirmed_duplicate", "same_person_likely", "not_linked"}:
        raise ValueError("link_type must be confirmed_duplicate, same_person_likely, or not_linked")
    if not created_by_text:
        raise ValueError("created_by is required")

    payload = {
        "guild_id": guild_text,
        "user_a_id": pair_a,
        "user_b_id": pair_b,
        "link_type": link_type_text,
        "status": "active",
        "created_by": created_by_text,
        "reason": _safe_str(reason) or None,
        "evidence": _safe_json(evidence or {}),
    }

    def _write() -> Dict[str, Any]:
        sb = get_supabase()
        if sb is None:
            raise RuntimeError("Supabase is not configured.")

        existing_res = (
            sb.table("manual_alt_links")
            .select("*")
            .eq("guild_id", guild_text)
            .eq("user_a_id", pair_a)
            .eq("user_b_id", pair_b)
            .eq("status", "active")
            .limit(1)
            .execute()
        )
        existing_rows = getattr(existing_res, "data", None) or []

        if existing_rows:
            existing = dict(existing_rows[0])
            link_id = existing.get("id")

            (
                sb.table("manual_alt_links")
                .update(
                    {
                        "link_type": link_type_text,
                        "created_by": created_by_text,
                        "reason": payload["reason"],
                        "evidence": payload["evidence"],
                    }
                )
                .eq("id", link_id)
                .execute()
            )

            reread = (
                sb.table("manual_alt_links")
                .select("*")
                .eq("id", link_id)
                .limit(1)
                .execute()
            )
            rows = getattr(reread, "data", None) or []
            return dict(rows[0]) if rows else existing

        sb.table("manual_alt_links").insert(payload).execute()

        reread = (
            sb.table("manual_alt_links")
            .select("*")
            .eq("guild_id", guild_text)
            .eq("user_a_id", pair_a)
            .eq("user_b_id", pair_b)
            .eq("status", "active")
            .limit(1)
            .execute()
        )
        rows = getattr(reread, "data", None) or []
        if not rows:
            raise RuntimeError("Manual alt link insert could not be re-read.")
        return dict(rows[0])

    return _execute_db_op("set manual alt link", _write)


def revoke_manual_alt_link(
    *,
    link_id: Any,
    revoked_by: Optional[str] = None,
) -> bool:
    link_id_int = _safe_int(link_id, 0)
    if link_id_int <= 0:
        return False

    def _write() -> bool:
        sb = get_supabase()
        if sb is None:
            return False

        (
            sb.table("manual_alt_links")
            .update(
                {
                    "status": "revoked",
                    "revoked_at": now_iso(),
                    "revoked_by": _safe_str(revoked_by) or None,
                }
            )
            .eq("id", link_id_int)
            .eq("status", "active")
            .execute()
        )
        return True

    try:
        return bool(_execute_db_op("revoke manual alt link", _write))
    except Exception as e:
        print("⚠️ revoke_manual_alt_link failed:", repr(e))
        return False


def confirm_duplicate_users(
    *,
    guild_id: Any,
    user_a_id: Any,
    user_b_id: Any,
    created_by: str,
    reason: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return set_manual_alt_link(
        guild_id=guild_id,
        user_a_id=user_a_id,
        user_b_id=user_b_id,
        link_type="confirmed_duplicate",
        created_by=created_by,
        reason=reason,
        evidence=evidence,
    )


def mark_users_likely_same_person(
    *,
    guild_id: Any,
    user_a_id: Any,
    user_b_id: Any,
    created_by: str,
    reason: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return set_manual_alt_link(
        guild_id=guild_id,
        user_a_id=user_a_id,
        user_b_id=user_b_id,
        link_type="same_person_likely",
        created_by=created_by,
        reason=reason,
        evidence=evidence,
    )


def mark_users_not_linked(
    *,
    guild_id: Any,
    user_a_id: Any,
    user_b_id: Any,
    created_by: str,
    reason: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return set_manual_alt_link(
        guild_id=guild_id,
        user_a_id=user_a_id,
        user_b_id=user_b_id,
        link_type="not_linked",
        created_by=created_by,
        reason=reason,
        evidence=evidence,
    )


def record_verified_identity_for_user(
    *,
    guild_id: Any,
    user_id: Any,
    identity_fingerprint: str,
    source: str,
    created_by: Optional[str] = None,
    fingerprint_version: str = "v1",
    confidence: int = 100,
    notes: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return create_identity_proof(
        guild_id=guild_id,
        user_id=user_id,
        identity_fingerprint=identity_fingerprint,
        source=source,
        created_by=created_by,
        fingerprint_version=fingerprint_version,
        confidence=confidence,
        notes=notes,
        evidence=evidence,
    )


def get_identity_truth_context(
    *,
    guild_id: Any,
    user_id: Any,
) -> Dict[str, Any]:
    guild_text = _safe_str(guild_id)
    user_text = _safe_str(user_id)

    if not guild_text or not user_text:
        return {
            "proof_matches": [],
            "manual_confirmed": [],
            "manual_likely": [],
            "manual_not_linked": [],
        }

    def _read() -> Dict[str, Any]:
        sb = get_supabase()
        if sb is None:
            return {
                "proof_matches": [],
                "manual_confirmed": [],
                "manual_likely": [],
                "manual_not_linked": [],
            }

        proof_res = (
            sb.table("identity_proof_matches")
            .select("*")
            .eq("guild_id", guild_text)
            .eq("user_id", user_text)
            .limit(25)
            .execute()
        )
        proof_rows = [dict(r) for r in (getattr(proof_res, "data", None) or []) if isinstance(r, dict)]

        manual_rows = get_manual_links_for_user(
            guild_id=guild_text,
            user_id=user_text,
            active_only=True,
        )

        manual_confirmed: List[Dict[str, Any]] = []
        manual_likely: List[Dict[str, Any]] = []
        manual_not_linked: List[Dict[str, Any]] = []

        for row in manual_rows:
            link_type = _safe_str(row.get("link_type")).lower()
            a_id = _safe_str(row.get("user_a_id"))
            b_id = _safe_str(row.get("user_b_id"))
            other_id = b_id if a_id == user_text else a_id

            normalized = {
                "id": row.get("id"),
                "other_user_id": other_id,
                "link_type": link_type,
                "reason": row.get("reason"),
                "created_by": row.get("created_by"),
                "created_at": row.get("created_at"),
            }

            if link_type == "confirmed_duplicate":
                manual_confirmed.append(normalized)
            elif link_type == "same_person_likely":
                manual_likely.append(normalized)
            elif link_type == "not_linked":
                manual_not_linked.append(normalized)

        return {
            "proof_matches": proof_rows,
            "manual_confirmed": manual_confirmed,
            "manual_likely": manual_likely,
            "manual_not_linked": manual_not_linked,
        }

    try:
        return _execute_db_op("get identity truth context", _read)
    except Exception as e:
        print("⚠️ get_identity_truth_context failed:", repr(e))
        return {
            "proof_matches": [],
            "manual_confirmed": [],
            "manual_likely": [],
            "manual_not_linked": [],
        }
