from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODLOG = ROOT / "stoney_verify/modlog.py"
TEST = ROOT / "tools/test_source_reputation_intelligence_static.py"


def insert_before(text: str, marker: str, block: str, token: str) -> str:
    if token in text:
        return text
    pos = text.find(marker)
    if pos < 0:
        raise SystemExit(f"Could not find marker: {marker}")
    return text[:pos] + block.rstrip() + "\n\n\n" + text[pos:]


def patch_db_helpers(text: str) -> str:
    block = r'''
def _source_key_from_join_rows(latest_join: Dict[str, Any], guild_member: Dict[str, Any]) -> Tuple[str, str]:
    latest = dict(latest_join or {})
    member_row = dict(guild_member or {})

    invite_code = _safe_str(latest.get("invite_code") or member_row.get("invite_code"))
    if invite_code and invite_code.lower() not in {"unknown", "none", "null"}:
        return ("invite_code", invite_code)

    join_source = _safe_str(latest.get("join_source") or member_row.get("join_source"))
    if join_source and join_source.lower() not in {"unknown", "unknown_join", "none", "null"}:
        return ("join_source", join_source)

    entry_method = _safe_str(latest.get("entry_method") or member_row.get("entry_method"))
    if entry_method and entry_method.lower() not in {"unknown", "unknown_join", "none", "null"}:
        return ("entry_method", entry_method)

    return ("", "")


def _sb_select_source_reputation_sync(
    guild_id: int,
    user_id: int,
    latest_join: Dict[str, Any],
    guild_member: Dict[str, Any],
) -> Dict[str, Any]:
    try:
        field, value = _source_key_from_join_rows(latest_join, guild_member)
        if not field or not value:
            return {}

        sb = get_supabase()
        if not sb:
            return {}

        res = (
            sb.table("member_joins")
            .select(
                "user_id,username,joined_at,invite_code,join_source,entry_method,entry_truth_quality,entry_confidence,risk_score,risk_level,evidence_tier",
                count="exact",
            )
            .eq("guild_id", str(int(guild_id)))
            .eq(field, value)
            .order("joined_at", desc=True)
            .limit(50)
            .execute()
        )

        rows = [dict(r) for r in (getattr(res, "data", None) or []) if isinstance(r, dict)]
        total = int(getattr(res, "count", 0) or len(rows) or 0)

        risky = 0
        strong_or_confirmed = 0
        low_confidence = 0
        unique_users: Set[str] = set()

        for row in rows:
            uid = _safe_str(row.get("user_id"))
            if uid:
                unique_users.add(uid)

            score = _safe_int(row.get("risk_score"), 0)
            level = _safe_str(row.get("risk_level")).lower()
            tier = _safe_str(row.get("evidence_tier")).lower()
            confidence = _safe_int(row.get("entry_confidence"), 0)

            if score >= 45 or level in {"medium", "high", "critical"}:
                risky += 1
            if tier in {"strongly_linked", "confirmed_duplicate"}:
                strong_or_confirmed += 1
            if confidence and confidence < 50:
                low_confidence += 1

        return {
            "source_field": field,
            "source_value": value,
            "sample_size": len(rows),
            "total_count": total,
            "unique_users": len(unique_users),
            "risky_count": risky,
            "strong_or_confirmed_count": strong_or_confirmed,
            "low_confidence_count": low_confidence,
        }
    except Exception as e:
        print("⚠️ _sb_select_source_reputation_sync failed:", repr(e))
        return {}


def _source_reputation_value(reputation: Dict[str, Any]) -> str:
    rep = dict(reputation or {})
    source_field = _safe_str(rep.get("source_field"))
    source_value = _safe_str(rep.get("source_value"))

    if not source_field or not source_value:
        return ""

    sample_size = _safe_int(rep.get("sample_size"), 0)
    total_count = _safe_int(rep.get("total_count"), sample_size)
    unique_users = _safe_int(rep.get("unique_users"), 0)
    risky_count = _safe_int(rep.get("risky_count"), 0)
    strong_or_confirmed = _safe_int(rep.get("strong_or_confirmed_count"), 0)
    low_confidence = _safe_int(rep.get("low_confidence_count"), 0)

    if strong_or_confirmed > 0 or risky_count >= 3:
        verdict = "⚠️ Source needs staff review."
    elif low_confidence >= max(3, sample_size // 2):
        verdict = "🟡 Source attribution is weak; watch new joins from this source."
    elif sample_size >= 5 and risky_count == 0 and strong_or_confirmed == 0:
        verdict = "✅ No recent risk pattern from this source."
    else:
        verdict = "ℹ️ Not enough history for a strong source verdict yet."

    lines = [
        f"Source key: `{source_field}` = `{source_value}`",
        f"Recent sample: **{sample_size}** join row(s) / **{unique_users}** user(s)",
        f"Risky from same source: **{risky_count}**",
        f"Strong/confirmed alt evidence: **{strong_or_confirmed}**",
        f"Low-confidence attribution rows: **{low_confidence}**",
        verdict,
    ]

    if total_count > sample_size:
        lines.append(f"More history exists: **{total_count}** total matching row(s).")

    return _chunk_lines(lines, 1000)
'''
    return insert_before(text, "\ndef _extract_flags_from_profile_like", block, "def _sb_select_source_reputation_sync(")


def patch_context_fetch(text: str) -> str:
    old = '''        warn_count = await _run_blocking_db(_sb_select_warn_count_sync, guild.id, member_or_user.id)
        truth_context = await _run_blocking_db(_sb_get_identity_truth_context_sync, guild.id, member_or_user.id) or {}
    except Exception:
        guild_member = {}
        latest_join = {}
        warn_count = 0
        truth_context = {}
'''
    new = '''        warn_count = await _run_blocking_db(_sb_select_warn_count_sync, guild.id, member_or_user.id)
        truth_context = await _run_blocking_db(_sb_get_identity_truth_context_sync, guild.id, member_or_user.id) or {}
        source_reputation = await _run_blocking_db(
            _sb_select_source_reputation_sync,
            guild.id,
            member_or_user.id,
            latest_join if isinstance(latest_join, dict) else {},
            guild_member if isinstance(guild_member, dict) else {},
        ) or {}
    except Exception:
        guild_member = {}
        latest_join = {}
        warn_count = 0
        truth_context = {}
        source_reputation = {}
'''
    if "source_reputation = await _run_blocking_db(" in text:
        return text
    if old not in text:
        raise SystemExit("Could not patch context fetch block")
    return text.replace(old, new)


def patch_smart_function_signature(text: str) -> str:
    if "source_reputation: Dict[str, Any] | None = None" in text:
        return text

    text = text.replace(
        "    *,\n    warn_count: int = 0,\n) -> str:",
        "    *,\n    warn_count: int = 0,\n    source_reputation: Dict[str, Any] | None = None,\n) -> str:",
        1,
    )

    text = text.replace(
        "    merged = dict(merged_risk or {})\n    latest = dict(latest_join or {})\n    member_row = dict(guild_member or {})",
        "    merged = dict(merged_risk or {})\n    latest = dict(latest_join or {})\n    member_row = dict(guild_member or {})\n    source_rep = dict(source_reputation or {})",
        1,
    )

    text = text.replace(
        "    if pretty_flags:\n        lines.append(\"Signals: \" + \", \".join(pretty_flags[:8]))\n    else:\n        lines.append(\"Signals: no strong recent link evidence yet\")",
        "    if pretty_flags:\n        lines.append(\"Signals: \" + \", \".join(pretty_flags[:8]))\n    else:\n        lines.append(\"Signals: no strong recent link evidence yet\")\n\n    source_risky = _safe_int(source_rep.get(\"risky_count\"), 0)\n    source_strong = _safe_int(source_rep.get(\"strong_or_confirmed_count\"), 0)\n    source_low_conf = _safe_int(source_rep.get(\"low_confidence_count\"), 0)\n    if source_rep:\n        lines.append(f\"Source pattern: risky={source_risky}, strong/confirmed={source_strong}, low-confidence={source_low_conf}\")",
        1,
    )

    text = text.replace(
        "    if tier in {\"CONFIRMED DUPLICATE\", \"STRONGLY LINKED\"} or score >= 65:\n        action = \"Restrict and review immediately.\"\n    elif score >= 20 or source_unknown or warn_count > 0:\n        action = \"Watch verification; keep unverified containment until source/behavior looks clean.\"\n    else:\n        action = \"Normal verification; keep logging for new behavior, reports, or source changes.\"",
        "    if tier in {\"CONFIRMED DUPLICATE\", \"STRONGLY LINKED\"} or score >= 65:\n        action = \"Restrict and review immediately.\"\n    elif source_strong > 0 or source_risky >= 3:\n        action = \"Watch this source closely; same source has risky join history.\"\n    elif score >= 20 or source_unknown or source_low_conf >= 3 or warn_count > 0:\n        action = \"Watch verification; keep unverified containment until source/behavior looks clean.\"\n    else:\n        action = \"Normal verification; keep logging for new behavior, reports, or source changes.\"",
        1,
    )

    return text


def patch_fields(text: str) -> str:
    if 'fields.append(("Source Reputation"' not in text:
        marker = '''    truth_value = _context_truth_value(guild, truth_context, merged_risk)
'''
        block = '''    source_reputation_value = _source_reputation_value(source_reputation)
    if source_reputation_value:
        fields.append(("Source Reputation", source_reputation_value, False))

'''
        if marker not in text:
            raise SystemExit("Could not insert Source Reputation field")
        text = text.replace(marker, block + marker, 1)

    old_call = '''    smart_value = _smart_join_intelligence_value(merged_risk, latest_join, guild_member, warn_count=warn_count)
'''
    new_call = '''    smart_value = _smart_join_intelligence_value(
        merged_risk,
        latest_join,
        guild_member,
        warn_count=warn_count,
        source_reputation=source_reputation,
    )
'''
    if old_call in text:
        text = text.replace(old_call, new_call, 1)
    elif "source_reputation=source_reputation" not in text and "_smart_join_intelligence_value(" in text:
        raise SystemExit("Smart Join Intelligence call exists but was not patched with source reputation")

    return text


def patch_modlog() -> None:
    text = MODLOG.read_text(encoding="utf-8")

    if "def _smart_join_intelligence_value(" not in text:
        raise SystemExit(
            "Smart Join Intelligence is not installed yet. Run tools/apply_smart_join_intelligence.py first."
        )

    text = patch_db_helpers(text)
    text = patch_context_fetch(text)
    text = patch_smart_function_signature(text)
    text = patch_fields(text)

    required = (
        "def _sb_select_source_reputation_sync(",
        "def _source_reputation_value(",
        "Source Reputation",
        "Source pattern:",
        "same source has risky join history",
        "source_reputation=source_reputation",
    )
    missing = [token for token in required if token not in text]
    if missing:
        raise SystemExit("Missing source reputation tokens: " + ", ".join(missing))

    MODLOG.write_text(text, encoding="utf-8")
    print("✅ source reputation intelligence added to modlog")


def write_test() -> None:
    TEST.write_text(
        '''from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODLOG = (ROOT / "stoney_verify/modlog.py").read_text(encoding="utf-8")


def test_source_reputation_db_helper_exists() -> None:
    assert "def _sb_select_source_reputation_sync(" in MODLOG
    assert "member_joins" in MODLOG
    assert "source_field" in MODLOG
    assert "risky_count" in MODLOG
    assert "strong_or_confirmed_count" in MODLOG


def test_source_reputation_field_is_added() -> None:
    assert "def _source_reputation_value(" in MODLOG
    assert 'fields.append(("Source Reputation"' in MODLOG
    assert "Source key:" in MODLOG
    assert "Risky from same source:" in MODLOG


def test_smart_action_uses_source_pattern() -> None:
    assert "source_reputation=source_reputation" in MODLOG
    assert "Source pattern:" in MODLOG
    assert "same source has risky join history" in MODLOG
    assert "source_low_conf >= 3" in MODLOG


if __name__ == "__main__":
    for test in (
        test_source_reputation_db_helper_exists,
        test_source_reputation_field_is_added,
        test_smart_action_uses_source_pattern,
    ):
        test()
        print(f"PASS {test.__name__}")
''',
        encoding="utf-8",
    )
    print("✅ wrote source reputation intelligence static test")


def main() -> None:
    patch_modlog()
    write_test()


if __name__ == "__main__":
    main()
