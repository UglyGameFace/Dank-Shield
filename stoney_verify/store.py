from __future__ import annotations

import os
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from .globals import *  # expects: get_supabase, now_utc, _parse_iso_datetime, VERIFY_SITE_URL, etc.


# ============================================================
# In-memory fallback (only used if Supabase is unavailable)
# ============================================================

# token -> row dict (mirrors Supabase fields)
_MEM_TOKENS: Dict[str, Dict[str, Any]] = {}

# whether we've warned once
_WARNED_NO_SUPABASE = False


# ============================================================
# Utils
# ============================================================

def _utcnow() -> datetime:
    """
    Project-preferred UTC clock with safe fallback.
    """
    try:
        return now_utc()  # type: ignore[name-defined]
    except Exception:
        return datetime.now(timezone.utc)


def _to_int(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return default
        return int(str(x).strip())
    except Exception:
        return default


def _to_str(x: Any) -> Optional[str]:
    try:
        if x is None:
            return None
        s = str(x).strip()
        return s if s else None
    except Exception:
        return None


def _parse_dt(value: Any) -> Optional[datetime]:
    """
    Accepts ISO strings or datetime. Returns aware UTC datetime where possible.
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        try:
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        except Exception:
            return value

    # Prefer your globals parser if present
    try:
        dt = _parse_iso_datetime(value)  # type: ignore[name-defined]
        if isinstance(dt, datetime):
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    except Exception:
        pass

    try:
        s = str(value).strip()
        if not s:
            return None
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


# ============================================================
# ✅ ISO normalization
#
# Fixes "instant expiry" if your Supabase column is `timestamp` (no tz),
# which returns strings like "2026-02-27T13:28:00" (naive). Elsewhere,
# the bot compares aware now_utc() vs naive expires_at and treats it as expired.
#
# We normalize timestamps to an ISO string with timezone (Z).
# ============================================================

_TZ_RE = re.compile(r"(Z|[+-][0-9][0-9]:[0-9][0-9])$")


def _normalize_iso_maybe(value: Any) -> Optional[str]:
    if value is None:
        return None

    # Supabase can return python datetime objects (depends on client)
    if isinstance(value, datetime):
        try:
            dt = value
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except Exception:
            return None

    s = str(value).strip()
    if not s:
        return None

    # Already has tz info → normalize formatting to Z where possible
    if _TZ_RE.search(s):
        try:
            dt = _parse_dt(s)
            if not dt:
                return s
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except Exception:
            return s

    # No tz info → assume UTC
    try:
        dt = _parse_dt(s)
        if not dt:
            return s
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return s


def _sb() -> Any:
    """
    Supabase client getter (project-defined in globals.py).
    """
    try:
        return get_supabase()
    except Exception:
        return None


def _token_table_name() -> str:
    """
    Resolve the token table name while preserving backward compatibility
    with multiple constant names.
    """
    try:
        t = str(VERIFY_TOKEN_TABLE).strip()  # type: ignore[name-defined]
        if t:
            return t
    except Exception:
        pass

    try:
        t = str(TOKEN_TABLE).strip()  # type: ignore[name-defined]
        if t:
            return t
    except Exception:
        pass

    return "verification_tokens"


def _warn_no_supabase_once(reason: str = "") -> None:
    global _WARNED_NO_SUPABASE
    if _WARNED_NO_SUPABASE:
        return
    _WARNED_NO_SUPABASE = True
    try:
        msg = "⚠️ Supabase is unavailable; token storage is using in-memory fallback (tokens will not survive restarts)."
        if reason:
            msg += f" ({reason})"
        print(msg)
    except Exception:
        pass


def _coerce_non_empty_webhook_url(webhook_url: Any, *, channel_id: Optional[int] = None) -> str:
    """
    LAST LINE OF DEFENSE for your Supabase constraint:
      verification_tokens_webhook_url_not_empty_chk

    We must never upsert webhook_url="".
    Prefer caller-provided webhook_url; otherwise store a non-empty postback-style string.
    """
    try:
        w = str(webhook_url or "").strip()
    except Exception:
        w = ""

    if w:
        return w

    try:
        cid = int(channel_id or 0)
    except Exception:
        cid = 0

    if cid > 0:
        return f"bot://channel/{cid}"

    return "bot://channel/0"


# ============================================================
# Token generator
# ============================================================

def gen_token() -> str:
    """
    Token generator.
    - URL-safe and unpredictable.
    - Keep length modest so it works in logs/embeds/etc.
    """
    try:
        return secrets.token_urlsafe(16)
    except Exception:
        return os.urandom(16).hex()


def make_token() -> str:
    """
    Compatibility alias used by some modules.
    """
    return gen_token()


# ============================================================
# Token object normalization
# ============================================================

def _normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensure the row has the fields your system expects and consistent types.
    Keeps extra fields (submitted_at / approved_user_id / ai_status) if present.
    """
    token = _to_str(row.get("token")) or ""
    guild_id = _to_str(row.get("guild_id"))
    channel_id = _to_str(row.get("channel_id"))
    requester_id = _to_str(row.get("requester_id"))
    user_id = _to_str(row.get("user_id"))  # backward compat
    decided_by = _to_str(row.get("decided_by"))
    decision = _to_str(row.get("decision")) or "PENDING"

    used = bool(row.get("used", False))
    submitted = bool(row.get("submitted", False))

    expires_at_s = _normalize_iso_maybe(row.get("expires_at")) or _to_str(row.get("expires_at"))
    decided_at_s = _normalize_iso_maybe(row.get("decided_at")) or _to_str(row.get("decided_at"))
    created_at_s = _normalize_iso_maybe(row.get("created_at")) or _to_str(row.get("created_at"))
    submitted_at_s = _normalize_iso_maybe(row.get("submitted_at")) or _to_str(row.get("submitted_at"))

    # Keep stored webhook_url as-is if present; insert/update path handles non-empty coercion.
    webhook_url = _to_str(row.get("webhook_url")) or ""

    approved_user_id = _to_str(row.get("approved_user_id"))
    ai_status = _to_str(row.get("ai_status"))

    return {
        "token": token,
        "guild_id": guild_id,
        "channel_id": channel_id,
        "requester_id": requester_id or user_id,
        "expires_at": expires_at_s,
        "used": used,
        "submitted": submitted,
        "submitted_at": submitted_at_s,
        "decision": decision,
        "decided_by": decided_by,
        "decided_at": decided_at_s,
        "created_at": created_at_s,
        "webhook_url": webhook_url,
        "approved_user_id": approved_user_id,
        "ai_status": ai_status,
    }


# ============================================================
# Scope / validity checks (STRICT REQUIREMENTS)
# ============================================================

def token_is_expired(token_info: Optional[Dict[str, Any]]) -> bool:
    if not token_info:
        return True

    exp = _parse_dt(token_info.get("expires_at"))
    if not exp:
        return True

    if exp.tzinfo is None:
        try:
            exp = exp.replace(tzinfo=timezone.utc)
        except Exception:
            return True

    return _utcnow() >= exp


def token_is_used(token_info: Optional[Dict[str, Any]]) -> bool:
    if not token_info:
        return True
    return bool(token_info.get("used", False))


def token_scope_ok(
    token_info: Optional[Dict[str, Any]],
    *,
    guild_id: Optional[int] = None,
    channel_id: Optional[int] = None,
    requester_id: Optional[int] = None,
) -> Tuple[bool, str]:
    """
    STRICT: Tokens must be scoped to:
      - specific channel
      - specific guild
      - specific requester
    If scope field is missing, treat as invalid.
    """
    if not token_info:
        return False, "Token not found."

    ti_ch = _to_int(token_info.get("channel_id"), 0)
    if not ti_ch:
        return False, "Token is missing channel scope."
    if channel_id is not None and int(channel_id) != int(ti_ch):
        return False, "Token does not belong to this ticket."

    ti_g = _to_int(token_info.get("guild_id"), 0)
    if not ti_g:
        return False, "Token is missing guild scope."
    if guild_id is not None and int(guild_id) != int(ti_g):
        return False, "Token does not belong to this server."

    ti_r = _to_int(token_info.get("requester_id"), 0)
    if not ti_r:
        return False, "Token is missing requester scope."
    if requester_id is not None and int(requester_id) != int(ti_r):
        return False, "Token does not belong to this user."

    return True, "OK"


def token_valid_for_use(
    token_info: Optional[Dict[str, Any]],
    *,
    guild_id: Optional[int] = None,
    channel_id: Optional[int] = None,
    requester_id: Optional[int] = None,
) -> Tuple[bool, str]:
    """
    Combined strict checks:
      - exists
      - scoped correctly
      - not expired
      - not used
    """
    ok, msg = token_scope_ok(
        token_info,
        guild_id=guild_id,
        channel_id=channel_id,
        requester_id=requester_id,
    )
    if not ok:
        return False, msg
    if token_is_expired(token_info):
        return False, "Token expired."
    if token_is_used(token_info):
        return False, "Token already used."
    return True, "OK"


# ============================================================
# CRUD: Supabase-backed with fallback in-memory
# ============================================================

def sb_insert_token(
    token: str,
    *,
    webhook_url: str,
    expires_at: datetime,
    guild_id: Optional[int] = None,
    channel_id: Optional[int] = None,
    requester_id: Optional[int] = None,
) -> bool:
    """
    Insert/upsert token row.

    Stored fields (per your spec):
      token, guild_id, channel_id, requester_id, expires_at,
      used, submitted, decision, decided_by, decided_at, submitted_at
    """
    token = (token or "").strip()
    if not token:
        return False

    exp = _parse_dt(expires_at) if not isinstance(expires_at, datetime) else _parse_dt(expires_at)
    exp = exp or _utcnow()
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)

    webhook_url_safe = _coerce_non_empty_webhook_url(webhook_url, channel_id=channel_id)

    payload: Dict[str, Any] = {
        "token": token,
        "webhook_url": str(webhook_url_safe),
        "expires_at": exp.isoformat(),
        "used": False,
        "submitted": False,
        "submitted_at": None,
        "decision": "PENDING",
        "decided_by": None,
        "decided_at": None,
        "guild_id": str(int(guild_id or 0)) if guild_id else None,
        "channel_id": str(int(channel_id or 0)) if channel_id else None,
        "requester_id": str(int(requester_id or 0)) if requester_id else None,
        # backward compat if some logic expects user_id
        "user_id": str(int(requester_id or 0)) if requester_id else None,
    }

    sb = _sb()
    if sb is None:
        _warn_no_supabase_once("insert_token")
        _MEM_TOKENS[token] = _normalize_row(payload)
        return True

    try:
        table = _token_table_name()
        sb.table(table).upsert(payload, on_conflict="token").execute()
        _MEM_TOKENS[token] = _normalize_row(payload)
        return True
    except Exception as e:
        _warn_no_supabase_once(str(e))
        _MEM_TOKENS[token] = _normalize_row(payload)
        return True


def sb_get_token_info(token: str) -> Optional[Dict[str, Any]]:
    token = (token or "").strip()
    if not token:
        return None

    sb = _sb()
    if sb is None:
        row = _MEM_TOKENS.get(token)
        return _normalize_row(row) if row else None

    try:
        table = _token_table_name()
        res = sb.table(table).select("*").eq("token", token).limit(1).execute()
        data = getattr(res, "data", None) or []
        if not data:
            row = _MEM_TOKENS.get(token)
            return _normalize_row(row) if row else None
        row = data[0]
        if not isinstance(row, dict):
            return None
        norm = _normalize_row(row)
        _MEM_TOKENS[token] = norm
        return norm
    except Exception:
        row = _MEM_TOKENS.get(token)
        return _normalize_row(row) if row else None


def sb_set_used(token: str, used: bool = True) -> bool:
    token = (token or "").strip()
    if not token:
        return False

    sb = _sb()
    if sb is None:
        row = _MEM_TOKENS.get(token)
        if not row:
            return False
        row["used"] = bool(used)
        _MEM_TOKENS[token] = _normalize_row(row)
        return True

    try:
        table = _token_table_name()
        sb.table(table).update({"used": bool(used)}).eq("token", token).execute()
        row = _MEM_TOKENS.get(token) or {"token": token}
        row["used"] = bool(used)
        _MEM_TOKENS[token] = _normalize_row(row)
        return True
    except Exception:
        row = _MEM_TOKENS.get(token)
        if not row:
            return False
        row["used"] = bool(used)
        _MEM_TOKENS[token] = _normalize_row(row)
        return True


def sb_set_submitted_at(token: str, submitted_at: Optional[datetime] = None) -> bool:
    """
    Optional helper used by commands.py (safe even if your DB column is missing).
    """
    token = (token or "").strip()
    if not token:
        return False

    dt = _parse_dt(submitted_at) if submitted_at is not None else _utcnow()
    if dt and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    iso = dt.isoformat() if dt else _utcnow().isoformat()

    sb = _sb()
    if sb is None:
        row = _MEM_TOKENS.get(token)
        if not row:
            return False
        row["submitted_at"] = iso
        _MEM_TOKENS[token] = _normalize_row(row)
        return True

    try:
        table = _token_table_name()
        sb.table(table).update({"submitted_at": iso}).eq("token", token).execute()
        row = _MEM_TOKENS.get(token) or {"token": token}
        row["submitted_at"] = iso
        _MEM_TOKENS[token] = _normalize_row(row)
        return True
    except Exception:
        row = _MEM_TOKENS.get(token)
        if not row:
            return False
        row["submitted_at"] = iso
        _MEM_TOKENS[token] = _normalize_row(row)
        return True


def sb_set_submitted(token: str, submitted: bool = True) -> bool:
    token = (token or "").strip()
    if not token:
        return False

    iso = _utcnow().isoformat() if submitted else None

    sb = _sb()
    if sb is None:
        row = _MEM_TOKENS.get(token)
        if not row:
            return False
        row["submitted"] = bool(submitted)
        row["submitted_at"] = iso
        _MEM_TOKENS[token] = _normalize_row(row)
        return True

    try:
        table = _token_table_name()
        update = {"submitted": bool(submitted), "submitted_at": iso}
        sb.table(table).update(update).eq("token", token).execute()
        row = _MEM_TOKENS.get(token) or {"token": token}
        row["submitted"] = bool(submitted)
        row["submitted_at"] = iso
        _MEM_TOKENS[token] = _normalize_row(row)
        return True
    except Exception:
        row = _MEM_TOKENS.get(token)
        if not row:
            return False
        row["submitted"] = bool(submitted)
        row["submitted_at"] = iso
        _MEM_TOKENS[token] = _normalize_row(row)
        return True


def sb_set_decided_by(token: str, staff_id: int) -> bool:
    """
    Optional helper used by commands.py (safe even if your DB column is missing).
    """
    token = (token or "").strip()
    if not token:
        return False

    sid = str(int(staff_id))

    sb = _sb()
    if sb is None:
        row = _MEM_TOKENS.get(token)
        if not row:
            return False
        row["decided_by"] = sid
        _MEM_TOKENS[token] = _normalize_row(row)
        return True

    try:
        table = _token_table_name()
        sb.table(table).update({"decided_by": sid}).eq("token", token).execute()
        row = _MEM_TOKENS.get(token) or {"token": token}
        row["decided_by"] = sid
        _MEM_TOKENS[token] = _normalize_row(row)
        return True
    except Exception:
        row = _MEM_TOKENS.get(token)
        if not row:
            return False
        row["decided_by"] = sid
        _MEM_TOKENS[token] = _normalize_row(row)
        return True


def sb_mark_decision(
    token: str,
    decision: str,
    decided_by: Optional[int],
    *,
    approved_user_id: Optional[int] = None,
) -> bool:
    """
    Store staff decision.
    Requirements:
      - decision
      - decided_by
      - decided_at
    Also commonly mark used=True when a final decision is made.
    """
    token = (token or "").strip()
    if not token:
        return False

    decision_str = (decision or "").strip() or "PENDING"
    dec_by = int(decided_by or 0) if decided_by else None
    decided_at = _utcnow().isoformat()

    update: Dict[str, Any] = {
        "decision": decision_str,
        "decided_by": str(dec_by) if dec_by else None,
        "decided_at": decided_at,
    }

    finalish = decision_str.upper().startswith(("APPROVED", "DENIED"))
    if finalish:
        update["used"] = True

    if approved_user_id:
        update["approved_user_id"] = str(int(approved_user_id))

    sb = _sb()
    if sb is None:
        row = _MEM_TOKENS.get(token)
        if not row:
            return False
        row.update(update)
        _MEM_TOKENS[token] = _normalize_row(row)
        return True

    try:
        table = _token_table_name()
        sb.table(table).update(update).eq("token", token).execute()
        row = _MEM_TOKENS.get(token) or {"token": token}
        row.update(update)
        _MEM_TOKENS[token] = _normalize_row(row)
        return True
    except Exception:
        row = _MEM_TOKENS.get(token)
        if not row:
            return False
        row.update(update)
        _MEM_TOKENS[token] = _normalize_row(row)
        return True


# ============================================================
# Link + token extraction helpers
# ============================================================

def build_verify_link(token: str) -> str:
    """
    Prefer globals.build_verify_link() (imported via from .globals import *).
    If not available, generate from VERIFY_SITE_URL.
    """
    try:
        fn = globals().get("build_verify_link")
        if callable(fn) and fn is not build_verify_link:
            return str(fn(token))
    except Exception:
        pass

    try:
        base = str(VERIFY_SITE_URL or "").rstrip("/")
    except Exception:
        base = ""

    if not base:
        return token

    return f"{base}/?token={token}"


def extract_token_from_text(text: str) -> Optional[str]:
    """
    Attempts to extract token from any message text.
    Supports:
      - "t:XXXX"
      - "?token=XXXX"
      - "token=XXXX"
    """
    if not text:
        return None

    s = str(text)

    m = re.search(r"\bt:([A-Za-z0-9_\-\.]{6,})\b", s)
    if m:
        return (m.group(1) or "").strip()

    m = re.search(r"[?&]token=([A-Za-z0-9_\-\.]{6,})", s)
    if m:
        return (m.group(1) or "").strip()

    m = re.search(r"\btoken=([A-Za-z0-9_\-\.]{6,})\b", s)
    if m:
        return (m.group(1) or "").strip()

    return None


def extract_token_from_message(message: Any) -> Optional[str]:
    """
    Wrapper used by your submission handler:
      - checks content + embeds
    """
    try:
        tok = extract_token_from_text(str(getattr(message, "content", "") or ""))
        if tok:
            return tok
    except Exception:
        pass

    try:
        embeds = getattr(message, "embeds", None) or []
        for e in embeds:
            fields = getattr(e, "fields", None) or []
            blob = " ".join(
                [
                    str(getattr(e, "title", "") or ""),
                    str(getattr(e, "description", "") or ""),
                    " ".join(
                        [
                            str(getattr(f, "name", "") or "") + " " + str(getattr(f, "value", "") or "")
                            for f in fields
                        ]
                    ),
                ]
            )
            tok = extract_token_from_text(blob)
            if tok:
                return tok
    except Exception:
        pass

    return None


# ============================================================
# ✅ VC staff token validation helper
# ============================================================

def token_valid_for_vc_staff_action(
    token_info: Optional[Dict[str, Any]],
    *,
    guild_id: Optional[int] = None,
    channel_id: Optional[int] = None,
) -> Tuple[bool, str]:
    """
    VC staff buttons should NOT require requester_id match and should NOT require used=False.

    We only require:
      - token exists
      - guild/channel scope matches
      - not expired
    """
    if not token_info:
        return False, "Token not found."

    ok, msg = token_scope_ok(
        token_info,
        guild_id=guild_id,
        channel_id=channel_id,
        requester_id=None,
    )
    if not ok:
        return False, msg

    if token_is_expired(token_info):
        return False, "Token expired."

    return True, "OK"