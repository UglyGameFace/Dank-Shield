from __future__ import annotations

"""Guild-scoped, evidence-only member connection investigation.

This is not a Discord friendship/DM/other-server discovery service. It reads
only records Dank Shield already owns, and never treats a social connection as
proof of misconduct or an alternative account.
"""

import asyncio
from typing import Any


_RESULT_LIMIT = 12
_QUERY_LIMIT = 40


def _id(value: Any) -> str:
    value = str(value or "").strip()
    return value if value.isascii() and value.isdecimal() and int(value) > 0 else ""


def _text(value: Any, limit: int = 90) -> str:
    return str(value or "").strip()[:limit]


def build_connection_report(
    *, target_id: str, source: dict[str, list[dict[str, Any]]],
    errors: list[str] | None = None,
) -> dict[str, Any]:
    """Deterministic, testable inference. All claims carry a precise source."""
    uid = _id(target_id)
    if not uid:
        raise ValueError("A valid target user ID is required.")
    found: dict[str, dict[str, Any]] = {}
    suppressed: set[str] = set()

    def record(other: Any, label: str, kind: str, source_name: str) -> None:
        oid = _id(other)
        if not oid or oid == uid:
            return
        entry = found.setdefault(oid, {"user_id": oid, "evidence": [], "identity_review": "unknown"})
        evidence = {"label": label, "kind": kind, "source": source_name}
        if evidence not in entry["evidence"]:
            entry["evidence"].append(evidence)

    for row in source.get("invited", []) + source.get("join_invited", []):
        if _id(row.get("invited_by")) == uid and not row.get("entry_conflict"):
            quality = _text(row.get("entry_truth_quality")).lower()
            if quality in {"confirmed", "verified", "high", "high_confidence", "exact", "trusted"}:
                record(row.get("user_id"), "Their join was attributed to this user's invite", "social", "guild_members")
            else:
                record(row.get("user_id"), "Possible inviter attribution (not confirmed)", "weak", "guild_members")

    for row in source.get("vouched", []) + source.get("join_vouched", []):
        if _id(row.get("vouched_by")) == uid:
            record(row.get("user_id"), "This user vouched for their entry", "social", "guild_members")

    for row in source.get("target", []) + source.get("join_target", []):
        for field, label in (
            ("invited_by", "Invited this user"),
            ("vouched_by", "Vouched for this user"),
        ):
            record(row.get(field), label, "social", "guild_members")

    for row in source.get("manual_a", []) + source.get("manual_b", []):
        a, b = _id(row.get("user_a_id")), _id(row.get("user_b_id"))
        other = b if a == uid else a if b == uid else ""
        kind = _text(row.get("link_type")).lower()
        if _text(row.get("status")).lower() != "active" or not other:
            continue
        if kind == "not_linked":
            suppressed.add(other)
        elif kind == "confirmed_duplicate":
            record(other, "Staff-confirmed duplicate account record", "reviewed_identity", "manual_alt_links")
        elif kind == "same_person_likely":
            record(other, "Staff marked possible duplicate account", "possible_identity", "manual_alt_links")

    ordered = []
    priority = {"reviewed_identity": 0, "possible_identity": 1, "social": 2, "weak": 3}
    for other, item in found.items():
        if other in suppressed:
            item["evidence"] = [e for e in item["evidence"] if e["kind"] not in
                                {"reviewed_identity", "possible_identity"}]
            item["identity_review"] = "staff_marked_not_same_person"
        if not item["evidence"]:
            continue
        kinds = {e["kind"] for e in item["evidence"]}
        item["category"] = (
            "reviewed identity record" if "reviewed_identity" in kinds else
            "possible identity link" if "possible_identity" in kinds else
            "documented server relationship" if "social" in kinds else
            "unverified join relationship"
        )
        item["priority"] = min(priority[k] for k in kinds)
        ordered.append(item)

    ordered.sort(key=lambda x: (x["priority"], int(x["user_id"])))
    problems = list(errors or [])
    return {
        "target_user_id": uid,
        "matches": ordered[:_RESULT_LIMIT],
        "total_matches": len(ordered),
        "truncated": len(ordered) > _RESULT_LIMIT or any(
            len(rows) >= _QUERY_LIMIT for rows in source.values()
        ),
        "coverage": "partial" if problems else "available",
        "errors": problems,
        "finding": "relationships_only_not_leak_proof",
    }


def _read_connection_report(guild_id: int, user_id: int) -> dict[str, Any]:
    from .globals import get_supabase

    gid, uid = _id(guild_id), _id(user_id)
    if not gid or not uid:
        raise ValueError("Guild and user IDs must be positive.")
    db = get_supabase()
    if db is None:
        return build_connection_report(
            target_id=uid, source={}, errors=["Member history storage is unavailable."]
        )

    source: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    # The dedicated primary Supabase has identity_proofs, not an
    # identity_proof_matches relationship table. Do not query a nonexistent
    # table or infer a second account from a single person's proof.
    requests = (
        ("target", "guild_members", "user_id", uid),
        ("invited", "guild_members", "invited_by", uid),
        ("vouched", "guild_members", "vouched_by", uid),
        ("join_target", "member_joins", "user_id", uid),
        ("join_invited", "member_joins", "invited_by", uid),
        ("join_vouched", "member_joins", "vouched_by", uid),
        ("manual_a", "manual_alt_links", "user_a_id", uid),
        ("manual_b", "manual_alt_links", "user_b_id", uid),
    )
    for key, table, column, value in requests:
        try:
            projection = {
                "guild_members": "user_id,invited_by,vouched_by,entry_truth_quality,entry_conflict",
                "member_joins": "user_id,invited_by,vouched_by,entry_truth_quality,entry_conflict",
                "manual_alt_links": "user_a_id,user_b_id,link_type,status",
            }[table]
            query = (db.table(table).select(projection).eq("guild_id", gid)
                     .eq(column, value).limit(_QUERY_LIMIT))
            if table == "manual_alt_links":
                query = query.eq("status", "active")
            result = query.execute()
            source[key] = [dict(row) for row in (getattr(result, "data", None) or [])
                           if isinstance(row, dict)]
        except Exception as exc:
            source[key] = []
            # No silent success on schema/permission/egress failure.
            errors.append(f"{key} could not be read ({type(exc).__name__}).")
    return build_connection_report(target_id=uid, source=source, errors=errors)


async def analyze_member_connections(guild_id: int, user_id: int) -> dict[str, Any]:
    """Run bounded synchronous DB reads off Discord's event loop."""
    return await asyncio.to_thread(_read_connection_report, guild_id, user_id)


async def verified_ban_connections(
    guild: Any, join_context: dict[str, Any], risk_profile: dict[str, Any]
) -> list[dict[str, str]]:
    """Check ONLY explicit linked accounts against Discord's actual ban list.

    Weak name/behavior clusters are deliberately excluded. Cache misses and
    permission errors never become a ban assertion.
    """
    ids: set[str] = set()
    invited_by = _id((join_context or {}).get("invited_by"))
    if invited_by:
        quality = _text((join_context or {}).get("entry_truth_quality")).lower()
        if quality in {"confirmed", "verified", "high", "high_confidence", "exact", "trusted"}:
            ids.add(invited_by)
    for row in (risk_profile or {}).get("cluster_members") or []:
        if isinstance(row, dict) and row.get("reason") in {"identity_fingerprint_match", "manual_alt_link"}:
            candidate = _id(row.get("user_id"))
            if candidate:
                ids.add(candidate)
    matched = []
    for uid in sorted(ids, key=int)[:5]:
        try:
            import discord

            ban = await guild.fetch_ban(discord.Object(id=int(uid)))
        except Exception:
            # Includes NotFound, Forbidden, HTTP errors; no fabricated match.
            continue
        if getattr(ban, "user", None) is not None:
            matched.append({"user_id": uid, "basis": "confirmed Discord ban plus existing link evidence"})
    return matched
