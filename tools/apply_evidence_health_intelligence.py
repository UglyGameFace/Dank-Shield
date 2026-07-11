from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODLOG = ROOT / "stoney_verify/modlog.py"
TEST = ROOT / "tools/test_evidence_health_intelligence_static.py"


def insert_before(text: str, marker: str, block: str, token: str) -> str:
    if token in text:
        return text
    pos = text.find(marker)
    if pos < 0:
        raise SystemExit(f"Could not find marker: {marker}")
    return text[:pos] + block.rstrip() + "\n\n\n" + text[pos:]


def patch_helper(text: str) -> str:
    block = r'''
def _evidence_health_value(
    merged_risk: Dict[str, Any],
    latest_join: Dict[str, Any],
    guild_member: Dict[str, Any],
    truth_context: Dict[str, Any],
    source_reputation: Dict[str, Any] | None = None,
    *,
    warn_count: int = 0,
) -> str:
    merged = dict(merged_risk or {})
    latest = dict(latest_join or {})
    member_row = dict(guild_member or {})
    truth = dict(truth_context or {})
    source_rep = dict(source_reputation or {})

    is_bot = _safe_bool(merged.get("is_bot_account"), False)
    score = _safe_int(merged.get("risk_score"), _safe_int(merged.get("score"), 0))
    tier = _safe_str(merged.get("evidence_tier"), "clear").replace("_", " ").upper()
    level = _safe_str(merged.get("risk_level") or merged.get("level"), "low").upper()

    entry_method = _safe_str(merged.get("entry_method") or latest.get("entry_method") or member_row.get("entry_method"), "unknown")
    join_source = _safe_str(merged.get("join_source") or latest.get("join_source") or member_row.get("join_source"), "unknown")
    entry_quality = _safe_str(merged.get("entry_truth_quality") or latest.get("entry_truth_quality") or member_row.get("entry_truth_quality"), "unknown")
    entry_confidence = _safe_int(merged.get("entry_confidence") or latest.get("entry_confidence") or member_row.get("entry_confidence"), 0)

    identity_matches = max(
        _safe_int(merged.get("identity_proof_match_count"), 0),
        len(list(truth.get("proof_matches") or [])),
    )
    manual_confirmed = max(
        _safe_int(merged.get("manual_confirmed_match_count"), 0),
        len(list(truth.get("manual_confirmed") or [])),
    )
    manual_likely = max(
        _safe_int(merged.get("manual_likely_match_count"), 0),
        len(list(truth.get("manual_likely") or [])),
    )

    fp_count = _safe_int(merged.get("same_fingerprint_count"), 0)
    name_count = _safe_int(merged.get("similar_name_count"), 0)
    burst_count = _safe_int(merged.get("burst_join_count") or merged.get("burst_count"), 0)
    flags = _extract_flags_from_profile_like(merged)

    source_sample = _safe_int(source_rep.get("sample_size"), 0)
    source_risky = _safe_int(source_rep.get("risky_count"), 0)
    source_strong = _safe_int(source_rep.get("strong_or_confirmed_count"), 0)
    source_low_conf = _safe_int(source_rep.get("low_confidence_count"), 0)

    source_unknown = (
        entry_method in {"", "unknown", "unknown_join", "invite_unresolved", "invite_cache_warming", "invite_tracking_unavailable"}
        or join_source in {"", "unknown", "unknown_join", "invite_unresolved", "invite_cache_warming", "invite_tracking_unavailable"}
        or entry_confidence < 50
    )

    strengths: List[str] = []
    gaps: List[str] = []

    if is_bot:
        strengths.append("Discord marks this as an official bot account.")
        gaps.append("Bot permission review is separate from human alt/raid scoring.")
        verdict = "BOT REVIEW"
    else:
        if entry_confidence >= 85:
            strengths.append("Join source confidence is high.")
        elif source_unknown:
            gaps.append("Invite/source is unresolved or low-confidence.")

        if identity_matches > 0 or manual_confirmed > 0:
            strengths.append("Hard identity/alt evidence exists.")
        else:
            gaps.append("No verified identity-proof or manually confirmed alt link.")

        if manual_likely > 0:
            strengths.append("Staff marked likely identity linkage.")

        if fp_count > 0 or name_count > 0 or burst_count > 1:
            strengths.append("Recent cluster/burst evidence exists.")
        else:
            gaps.append("No meaningful recent cluster evidence yet.")

        if flags:
            strengths.append("Join-time warning flags exist.")
        else:
            gaps.append("No join-time warning flags yet.")

        if warn_count > 0:
            strengths.append(f"User has {warn_count} prior warning(s).")
        else:
            gaps.append("No prior warns on record.")

        if source_sample >= 5 and source_risky == 0 and source_strong == 0:
            strengths.append("Same-source history has no recent risk pattern.")
        elif source_strong > 0 or source_risky >= 3:
            gaps.append("Same source has risky or confirmed-linked join history.")
        elif source_sample > 0 and source_low_conf >= max(3, source_sample // 2):
            gaps.append("Same-source history has repeated low-confidence attribution.")

        gaps.append("DM/userbot behavior cannot be proven from join alone; it needs message behavior, staff reports, or DM reports.")

        if identity_matches > 0 or manual_confirmed > 0 or score >= 65 or source_strong > 0:
            verdict = "HIGH EVIDENCE"
        elif source_unknown or score >= 20 or source_risky > 0 or source_low_conf >= 3 or flags:
            verdict = "WATCHLIST"
        else:
            verdict = "LOW EVIDENCE"

    lines = [
        f"Verdict: **{verdict}**",
        f"Alt/Raid: **{tier}** • **{level} / {score}/100**",
        f"Join source: **{entry_quality or 'unknown'}** / **{entry_confidence}/100**",
    ]

    if source_sample > 0:
        lines.append(f"Source history: sample={source_sample}, risky={source_risky}, strong={source_strong}, low-confidence={source_low_conf}")

    if strengths:
        lines.append("Evidence strength: " + " • ".join(strengths[:4]))

    if gaps:
        lines.append("Evidence gaps: " + " • ".join(gaps[:5]))

    return _chunk_lines(lines, 1000)
'''
    return insert_before(text, "\ndef _risk_summary_header(", block, "def _evidence_health_value(")


def patch_field(text: str) -> str:
    if 'fields.append(("Evidence Health"' in text:
        return text

    marker = '''    fields.append(("Risk Context", _chunk_lines(base_lines, 1000), False))
'''
    if marker not in text:
        raise SystemExit("Could not find Risk Context field insertion point")

    block = '''    evidence_health = _evidence_health_value(
        merged_risk,
        latest_join,
        guild_member,
        truth_context,
        locals().get("source_reputation", {}),
        warn_count=warn_count,
    )
    if evidence_health:
        fields.append(("Evidence Health", evidence_health, False))

'''
    return text.replace(marker, marker + "\n" + block, 1)


def patch_modlog() -> None:
    text = MODLOG.read_text(encoding="utf-8")
    text = patch_helper(text)
    text = patch_field(text)

    required = (
        "def _evidence_health_value(",
        "Evidence Health",
        "Evidence strength:",
        "Evidence gaps:",
        "DM/userbot behavior cannot be proven from join alone",
        "WATCHLIST",
        "LOW EVIDENCE",
        "HIGH EVIDENCE",
    )
    missing = [token for token in required if token not in text]
    if missing:
        raise SystemExit("Missing evidence health tokens: " + ", ".join(missing))

    MODLOG.write_text(text, encoding="utf-8")
    print("✅ Evidence Health intelligence added")


def write_test() -> None:
    TEST.write_text(
        '''from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODLOG = (ROOT / "stoney_verify/modlog.py").read_text(encoding="utf-8")


def test_evidence_health_helper_exists() -> None:
    assert "def _evidence_health_value(" in MODLOG
    assert "Evidence strength:" in MODLOG
    assert "Evidence gaps:" in MODLOG
    assert "DM/userbot behavior cannot be proven from join alone" in MODLOG


def test_evidence_health_field_is_added() -> None:
    assert 'fields.append(("Evidence Health"' in MODLOG
    assert "Verdict:" in MODLOG
    assert "LOW EVIDENCE" in MODLOG
    assert "WATCHLIST" in MODLOG
    assert "HIGH EVIDENCE" in MODLOG


def test_evidence_health_uses_source_reputation_when_available() -> None:
    assert "source_risky" in MODLOG
    assert "source_strong" in MODLOG
    assert "source_low_conf" in MODLOG
    assert 'locals().get("source_reputation", {})' in MODLOG


if __name__ == "__main__":
    for test in (
        test_evidence_health_helper_exists,
        test_evidence_health_field_is_added,
        test_evidence_health_uses_source_reputation_when_available,
    ):
        test()
        print(f"PASS {test.__name__}")
''',
        encoding="utf-8",
    )
    print("✅ wrote Evidence Health static test")


def main() -> None:
    patch_modlog()
    write_test()


if __name__ == "__main__":
    main()
