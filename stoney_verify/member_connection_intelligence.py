from __future__ import annotations

"""Guild-scoped, evidence-only member connection investigation.

This is not a Discord friendship/DM/other-server discovery service. It reads
only records Dank Shield already owns, and never treats a social connection as
proof of misconduct or an alternative account.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any


_RESULT_LIMIT = 12
_QUERY_LIMIT = 40


def _id(value: Any) -> str:
    value = str(value or "").strip()
    return value if value.isascii() and value.isdecimal() and int(value) > 0 else ""


def _text(value: Any, limit: int = 90) -> str:
    return str(value or "").strip()[:limit]


def _recorded_join_event(row: dict[str, Any]) -> dict[str, Any] | None:
    """Accept only original, timestamp-consistent guild-scoped join events.

    A retained event proves that an attribution was recorded at that join,
    never that users are friends, alternate identities or current members.
    """
    metadata = row.get("metadata")
    if row.get("event_type") != "member_joined" or not isinstance(metadata, dict):
        return None
    joined_at = metadata.get("joined_at")
    created_at = row.get("created_at")
    if not joined_at or not created_at:
        return None
    try:
        joined = datetime.fromisoformat(str(joined_at).replace("Z", "+00:00"))
        created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        if joined.tzinfo is None:
            joined = joined.replace(tzinfo=timezone.utc)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if abs((created - joined).total_seconds()) > 60:
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    user_id = _id(row.get("user_id"))
    if not user_id:
        return None
    return {
        "user_id": user_id,
        "invited_by": _id(metadata.get("invited_by")),
        "vouched_by": _id(metadata.get("vouched_by")),
        "entry_truth_quality": _text(metadata.get("entry_truth_quality")),
        # Missing/conflicting truth cannot be promoted into confirmed evidence.
        "entry_conflict": metadata.get("entry_conflict") is not False,
    }


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
        inviter = row.get("invited_by")
        if _id(inviter):
            quality = _text(row.get("entry_truth_quality")).lower()
            if row.get("entry_conflict") or quality not in {
                "confirmed", "verified", "high", "high_confidence", "exact", "trusted"
            }:
                record(inviter, "Possible inviter attribution (not confirmed)", "weak", "member join history")
            else:
                record(inviter, "This user's join was attributed to that inviter", "social", "member join history")
        record(row.get("vouched_by"), "That user vouched for this user's entry", "social", "member join history")

    # Recover recorded historical relationships when member_joins insertion
    # failed. These are historical join events, not present-tense claims.
    for row in (
        source.get("event_target", [])
        + source.get("event_invited", [])
        + source.get("event_vouched", [])
        + source.get("event_related", [])  # preserve the initial report-builder contract
    ):
        member_id = _id(row.get("user_id"))
        if not member_id:
            continue
        quality = _text(row.get("entry_truth_quality")).lower()
        confirmed = (not row.get("entry_conflict") and quality in {
            "confirmed", "verified", "high", "high_confidence", "exact", "trusted"
        })
        inviter = _id(row.get("invited_by"))
        voucher = _id(row.get("vouched_by"))
        if member_id == uid:
            if inviter:
                record(
                    inviter,
                    "Historical join event: recorded inviter" if confirmed else
                    "Historical join event: possible inviter (unconfirmed)",
                    "social" if confirmed else "weak", "original member_joined event",
                )
            if voucher:
                record(voucher, "Historical join event: recorded vouch", "social",
                       "original member_joined event")
        else:
            if inviter == uid:
                record(
                    member_id,
                    "Historical join event: this user was recorded as inviter" if confirmed else
                    "Historical join event: possible inviter (unconfirmed)",
                    "social" if confirmed else "weak", "original member_joined event",
                )
            if voucher == uid:
                record(member_id, "Historical join event: this user vouched for entry",
                       "social", "original member_joined event")

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
        "coverage": "partial" if problems or any(
            len(rows) >= _QUERY_LIMIT for rows in source.values()
        ) else "available",
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
    # Read original join relationships only on a staff request. actor_id
    # cannot be trusted as an inviter/voucher: it can also be an approver.
    # Query the exact retained JSON metadata for both directions instead.
    event_count = 0
    recovered_count = 0
    event_truncated = False
    observed: set[tuple[str, ...]] = set()
    event_requests = (
        ("event_target", "user_id"),
        ("event_invited", "metadata->>invited_by"),
        ("event_vouched", "metadata->>vouched_by"),
    )
    for key, field in event_requests:
        source[key] = []
        try:
            response = (
                db.table("member_events")
                .select("id,user_id,event_type,metadata,created_at")
                .eq("guild_id", gid)
                .eq("event_type", "member_joined")
                .eq(field, uid)
                .limit(_QUERY_LIMIT)
                .execute()
            )
            rows = getattr(response, "data", None) or []
            event_truncated |= len(rows) >= _QUERY_LIMIT
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                event = _recorded_join_event(raw)
                if not event:
                    continue
                if key == "event_target":
                    if event["user_id"] != uid:
                        continue
                elif key == "event_invited":
                    if event["invited_by"] != uid:
                        continue
                elif event["vouched_by"] != uid:
                    continue
                # One event may be in several lookup results. Count and
                # label its evidence only once without needing a new table.
                metadata = raw["metadata"]
                token = (
                    str(raw.get("id") or ""),
                    event["user_id"],
                    str(raw.get("created_at") or ""),
                    str(metadata.get("joined_at") or ""),
                    event["invited_by"],
                    event["vouched_by"],
                    event["entry_truth_quality"],
                    str(event["entry_conflict"]),
                )
                if token in observed:
                    continue
                observed.add(token)
                source[key].append(event)
                event_count += 1
                if event["invited_by"] or event["vouched_by"]:
                    recovered_count += 1
        except Exception as exc:
            errors.append(f"{key} join-event evidence unavailable ({type(exc).__name__}).")

    report = build_connection_report(target_id=uid, source=source, errors=errors)
    report["history_events"] = event_count
    report["recovered_join_events"] = recovered_count
    report["history_status"] = (
        "member_record_found" if source.get("target") or source.get("join_target")
        else "events_only" if source.get("event_target") else "unknown"
    )
    if event_truncated:
        report["truncated"] = True
        report["coverage"] = "partial"
    if not source.get("join_target") and report["history_status"] != "unknown":
        report["history_gap"] = (
            "No join-history row for this account; only individually recorded "
            "historical join events can support recovered relationships."
        )
        report["coverage"] = "partial"
    if not report["matches"]:
        report["history_gap"] = report.get("history_gap") or (
            "No recorded relationship evidence was recovered. Unrecorded "
            "friendships and alternate accounts cannot be inferred."
        )
        report["coverage"] = "partial"
    return report


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

async def enrich_connection_ban_status(guild: Any, report: dict[str, Any]) -> dict[str, Any]:
    """Independently verify each recorded related account's guild ban.

    A ban is a status, never evidence that two accounts share an owner.
    Errors remain unknown rather than becoming false negatives.
    """
    import discord

    for row in report.get("matches", []):
        row["ban_status"] = "unknown"
        uid = _id(row.get("user_id"))
        if not uid:
            continue
        try:
            ban = await guild.fetch_ban(discord.Object(id=int(uid)))
        except discord.NotFound:
            row["ban_status"] = "not_banned"
        except (discord.Forbidden, discord.HTTPException):
            row["ban_status"] = "unavailable"
            report["coverage"] = "partial"
        except Exception as exc:
            # A transient adapter or transport failure must not hide the
            # historical evidence that was successfully collected.
            row["ban_status"] = "unavailable"
            report["coverage"] = "partial"
            report.setdefault("errors", []).append(
                f"Discord ban lookup unavailable ({type(exc).__name__})."
            )
        else:
            if getattr(ban, "user", None) is not None:
                row["ban_status"] = "banned"
            else:
                row["ban_status"] = "unavailable"
                report["coverage"] = "partial"
    return report
