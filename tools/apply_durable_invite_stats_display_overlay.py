from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


durable = ROOT / "stoney_verify" / "durable_invite_stats.py"
replace_once(
    durable,
    '''def _read_durable_count_sync(guild_id: int) -> Optional[int]:\n    sb = get_supabase()\n    if sb is None:\n        return None\n    response = (\n        sb.table(STATS_TABLE)\n        .select("invites_blocked")\n        .eq("guild_id", str(guild_id))\n        .limit(1)\n        .execute()\n    )\n    rows = _rows(response)\n    if not rows:\n        return None\n    return max(0, _safe_int(rows[0].get("invites_blocked"), 0))\n\n\nasync def _legacy_invite_count(guild_id: int) -> int:\n''',
    '''def _read_durable_count_sync(guild_id: int) -> Optional[int]:\n    sb = get_supabase()\n    if sb is None:\n        return None\n    response = (\n        sb.table(STATS_TABLE)\n        .select("invites_blocked")\n        .eq("guild_id", str(guild_id))\n        .limit(1)\n        .execute()\n    )\n    rows = _rows(response)\n    if not rows:\n        return None\n    return max(0, _safe_int(rows[0].get("invites_blocked"), 0))\n\n\nasync def read_invites_blocked(guild_id: int) -> Optional[int]:\n    """Read the dedicated durable total for the visible stats display."""\n\n    gid = int(guild_id)\n    if gid <= 0:\n        return None\n    try:\n        return await asyncio.to_thread(\n            _execute_with_retry,\n            "read durable invite count",\n            lambda: _read_durable_count_sync(gid),\n            3,\n        )\n    except Exception as exc:\n        if not _rpc_or_table_missing(exc):\n            _warn(\n                f"durable count read failed guild={gid} "\n                f"error={type(exc).__name__}: {str(exc)[:180]}"\n            )\n        return None\n\n\nasync def _legacy_invite_count(guild_id: int) -> int:\n''',
    "durable public count reader",
)
replace_once(
    durable,
    '''    "reconcile_guild",\n    "record_deleted_invite_decision",\n]''',
    '''    "read_invites_blocked",\n    "reconcile_guild",\n    "record_deleted_invite_decision",\n]''',
    "durable public export",
)

security = ROOT / "stoney_verify" / "security_stats.py"
replace_once(
    security,
    '''    return _display_names(\n        spam_guard_enabled=spam_enabled,\n        counts=counts,\n        member_count=_guild_member_count(guild),\n        ticket_counts=tickets,\n    )\n''',
    '''    display_counts = normalize_security_stats(counts)\n    try:\n        from .durable_invite_stats import read_invites_blocked\n\n        durable_invites = await read_invites_blocked(gid)\n        if durable_invites is not None:\n            display_counts["invites_blocked"] = max(\n                int(display_counts["invites_blocked"]),\n                int(durable_invites),\n            )\n    except Exception as exc:\n        print(\n            f"⚠️ security_stats durable invite count read failed guild={gid} "\n            f"error={type(exc).__name__}"\n        )\n\n    return _display_names(\n        spam_guard_enabled=spam_enabled,\n        counts=display_counts,\n        member_count=_guild_member_count(guild),\n        ticket_counts=tickets,\n    )\n''',
    "visible durable invite stats overlay",
)

(ROOT / "tools" / "apply_durable_invite_stats_display_overlay.py").unlink(missing_ok=True)
(ROOT / ".github" / "workflows" / "apply-durable-invite-stats-display-overlay.yml").unlink(missing_ok=True)
