from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAID = ROOT / "stoney_verify/raidguard.py"
MODLOG = ROOT / "stoney_verify/modlog.py"
TEST = ROOT / "tools/test_bot_detection_stats_clarity_static.py"


def patch_raidguard() -> None:
    text = RAID.read_text(encoding="utf-8")

    text = text.replace(
        'return "BOT ACCOUNT • excluded from raid/alt scoring"',
        'return "Official Bot: Yes\\nAlt/Raid Risk: excluded from human alt/raid scoring\\nDM Raider Risk: report-based only; bots cannot read private DMs"',
    )

    text = text.replace(
        '"Discord marks this account as a bot; excluded from raid/alt scoring."',
        '"Official Discord bot account: excluded from human alt/raid scoring. Review bot permissions separately."',
    )

    old_parts = '''    parts: List[str] = [
        f"{tier} ({level} / {score}/100)",
        f"Account age: {age_human}",
    ]'''
    new_parts = '''    parts: List[str] = [
        "Official Bot: No",
        f"Alt/Raid Risk: {tier} ({level} / {score}/100)",
        "DM Raider Risk: no DM report evidence attached to this join",
        f"Account age: {age_human}",
    ]'''

    if old_parts in text:
        text = text.replace(old_parts, new_parts)
    elif "Official Bot: No" not in text:
        raise SystemExit("Could not patch raidguard alt summary parts")

    RAID.write_text(text, encoding="utf-8")
    print("✅ patched raidguard official-bot vs human-risk wording")


def patch_modlog() -> None:
    text = MODLOG.read_text(encoding="utf-8")

    old_header = '''def _risk_summary_header(source: Dict[str, Any], warn_count: int = 0) -> str:
    if _safe_bool(source.get("is_bot_account"), False):
        return "BOT ACCOUNT • Excluded from alt-risk scoring"

    tier = _safe_str(source.get("evidence_tier"), "clear").replace("_", " ").upper()
    score = _safe_int(source.get("risk_score"), _safe_int(source.get("score"), 0))
    level = _safe_str(source.get("risk_level") or source.get("level"), "low").upper()
    age_human = _safe_str(source.get("account_age_human"))
    age_days = _safe_int(source.get("account_age_days"), 0)
    if not age_human:
        age_human = f"{age_days} day(s)"

    parts = [f"{tier}", f"{level} / {score}/100", f"Account age: {age_human}"]
    if warn_count > 0:
        parts.append(f"Warns: {warn_count}")
    return " • ".join(parts)
'''
    new_header = '''def _risk_summary_header(source: Dict[str, Any], warn_count: int = 0) -> str:
    if _safe_bool(source.get("is_bot_account"), False):
        return "Official Bot: Yes • Alt/Raid Risk: excluded from human scoring • Review bot permissions separately"

    tier = _safe_str(source.get("evidence_tier"), "clear").replace("_", " ").upper()
    score = _safe_int(source.get("risk_score"), _safe_int(source.get("score"), 0))
    level = _safe_str(source.get("risk_level") or source.get("level"), "low").upper()
    age_human = _safe_str(source.get("account_age_human"))
    age_days = _safe_int(source.get("account_age_days"), 0)
    if not age_human:
        age_human = f"{age_days} day(s)"

    parts = ["Official Bot: No", f"Alt/Raid Risk: {tier}", f"{level} / {score}/100", f"Account age: {age_human}"]
    if warn_count > 0:
        parts.append(f"Warns: {warn_count}")
    return " • ".join(parts)
'''

    if old_header in text:
        text = text.replace(old_header, new_header)
    elif "Official Bot: No" not in text or "Alt/Raid Risk:" not in text:
        raise SystemExit("Could not patch modlog risk summary header")

    old_field = '''    fields.append(("Risk Context", _chunk_lines(base_lines, 1000), False))

    truth_value = _context_truth_value(guild, truth_context, merged_risk)
'''
    new_field = '''    if not _safe_bool(merged_risk.get("is_bot_account"), False):
        base_lines.append("DM Raider Risk: no DM report evidence attached to this join")

    fields.append(("Risk Context", _chunk_lines(base_lines, 1000), False))

    entry_method = _safe_str(merged_risk.get("entry_method") or latest_join.get("entry_method") or guild_member.get("entry_method"), "unknown")
    join_source = _safe_str(merged_risk.get("join_source") or latest_join.get("join_source") or guild_member.get("join_source"), "unknown")
    invite_code = _safe_str(merged_risk.get("invite_code") or latest_join.get("invite_code") or guild_member.get("invite_code"), "unknown")
    entry_quality = _safe_str(merged_risk.get("entry_truth_quality") or latest_join.get("entry_truth_quality") or guild_member.get("entry_truth_quality"), "unknown")
    entry_confidence = _safe_int(merged_risk.get("entry_confidence") or latest_join.get("entry_confidence") or guild_member.get("entry_confidence"), 0)
    entry_reason = _safe_str(merged_risk.get("entry_quality_reason") or latest_join.get("entry_quality_reason") or guild_member.get("entry_quality_reason"))

    join_source_lines = [
        f"Entry method: `{entry_method or 'unknown'}`",
        f"Source: `{join_source or 'unknown'}`",
        f"Invite: `{invite_code or 'unknown'}`",
        f"Confidence: `{entry_quality or 'unknown'}` / `{entry_confidence}/100`",
    ]
    if entry_reason:
        join_source_lines.append(f"Why: {_truncate(entry_reason, 180)}")

    fields.append(("Join Source", _chunk_lines(join_source_lines, 1000), False))

    truth_value = _context_truth_value(guild, truth_context, merged_risk)
'''

    if old_field in text:
        text = text.replace(old_field, new_field)
    elif 'fields.append(("Join Source"' not in text:
        raise SystemExit("Could not add Join Source field")

    MODLOG.write_text(text, encoding="utf-8")
    print("✅ patched modlog risk/source clarity")


def write_test() -> None:
    TEST.write_text(
        '''from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAID = (ROOT / "stoney_verify/raidguard.py").read_text(encoding="utf-8")
MODLOG = (ROOT / "stoney_verify/modlog.py").read_text(encoding="utf-8")


def test_raidguard_separates_official_bot_from_human_risk() -> None:
    assert "Official Bot: Yes" in RAID
    assert "Official Bot: No" in RAID
    assert "Alt/Raid Risk:" in RAID
    assert "DM Raider Risk:" in RAID
    assert "BOT ACCOUNT • excluded from raid/alt scoring" not in RAID


def test_modlog_risk_context_is_not_vague_clear_only() -> None:
    assert "Official Bot: No" in MODLOG
    assert "Official Bot: Yes" in MODLOG
    assert "Alt/Raid Risk:" in MODLOG
    assert "DM Raider Risk: no DM report evidence attached to this join" in MODLOG
    assert "BOT ACCOUNT • Excluded from alt-risk scoring" not in MODLOG


def test_modlog_shows_join_source_truth_reason() -> None:
    assert 'fields.append(("Join Source"' in MODLOG
    assert "entry_quality_reason" in MODLOG
    assert "Entry method:" in MODLOG
    assert "Confidence:" in MODLOG
    assert "Why:" in MODLOG


if __name__ == "__main__":
    for test in (
        test_raidguard_separates_official_bot_from_human_risk,
        test_modlog_risk_context_is_not_vague_clear_only,
        test_modlog_shows_join_source_truth_reason,
    ):
        test()
        print(f"PASS {test.__name__}")
''',
        encoding="utf-8",
    )
    print("✅ wrote bot detection stats clarity test")


def main() -> None:
    patch_raidguard()
    patch_modlog()
    write_test()


if __name__ == "__main__":
    main()
