from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAID = ROOT / "stoney_verify/raidguard.py"
MODLOG = ROOT / "stoney_verify/modlog.py"
TEST = ROOT / "tools/test_smart_join_intelligence_static.py"


def patch_raidguard() -> None:
    text = RAID.read_text(encoding="utf-8")

    text = text.replace("DM Raider Report Risk:", "DM Raider Risk:")
    text = text.replace("DM Raider Report Risk", "DM Raider Risk")

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

    required = ("Official Bot: Yes", "Official Bot: No", "Alt/Raid Risk:", "DM Raider Risk:")
    missing = [token for token in required if token not in text]
    if missing:
        raise SystemExit("raidguard missing required smart wording: " + ", ".join(missing))

    RAID.write_text(text, encoding="utf-8")
    print("✅ raidguard wording normalized")


def insert_smart_function(text: str) -> str:
    if "def _smart_join_intelligence_value(" in text:
        return text

    marker = "\nasync def _build_member_context_fields("
    pos = text.find(marker)
    if pos < 0:
        raise SystemExit("Could not find _build_member_context_fields insertion point")

    function = r'''

def _smart_join_intelligence_value(
    merged_risk: Dict[str, Any],
    latest_join: Dict[str, Any],
    guild_member: Dict[str, Any],
    *,
    warn_count: int = 0,
) -> str:
    merged = dict(merged_risk or {})
    latest = dict(latest_join or {})
    member_row = dict(guild_member or {})

    is_bot = _safe_bool(merged.get("is_bot_account"), False)
    tier = _safe_str(merged.get("evidence_tier"), "clear").replace("_", " ").upper()
    score = _safe_int(merged.get("risk_score"), _safe_int(merged.get("score"), 0))
    level = _safe_str(merged.get("risk_level") or merged.get("level"), "low").upper()

    entry_method = _safe_str(merged.get("entry_method") or latest.get("entry_method") or member_row.get("entry_method"), "unknown")
    join_source = _safe_str(merged.get("join_source") or latest.get("join_source") or member_row.get("join_source"), "unknown")
    entry_quality = _safe_str(merged.get("entry_truth_quality") or latest.get("entry_truth_quality") or member_row.get("entry_truth_quality"), "unknown")
    entry_confidence = _safe_int(merged.get("entry_confidence") or latest.get("entry_confidence") or member_row.get("entry_confidence"), 0)
    entry_reason = _safe_str(merged.get("entry_quality_reason") or latest.get("entry_quality_reason") or member_row.get("entry_quality_reason"))

    flags = _extract_flags_from_profile_like(merged)
    pretty_flags = [_pretty_flag_label(flag) for flag in flags if _pretty_flag_label(flag)]

    source_unknown = (
        entry_method in {"", "unknown", "unknown_join", "invite_unresolved", "invite_cache_warming", "invite_tracking_unavailable"}
        or join_source in {"", "unknown", "unknown_join", "invite_unresolved", "invite_cache_warming", "invite_tracking_unavailable"}
        or entry_confidence < 50
    )

    lines: List[str] = []

    if is_bot:
        lines.append("Official bot: **Yes**")
        lines.append("Alt/raid evidence: excluded from human alt/raid scoring")
        lines.append("Human automation/userbot risk: not applicable; this is a Discord bot account")
        lines.append("Recommended action: review who added it and whether its permissions are safe.")
        return _chunk_lines(lines, 1000)

    lines.append("Official bot: **No**")
    lines.append(f"Alt/raid evidence: **{tier}** • **{level} / {score}/100**")
    lines.append("Human automation/userbot risk: not proven from join alone; needs message behavior, staff reports, or DM reports.")
    lines.append(f"Invite/source confidence: **{entry_quality or 'unknown'}** / **{entry_confidence}/100**")

    if source_unknown:
        lines.append("Context gap: invite/source is unresolved or low-confidence; treat as watchlist context, not proof.")
    elif entry_reason:
        lines.append(f"Source reason: {_truncate(entry_reason, 180)}")

    if pretty_flags:
        lines.append("Signals: " + ", ".join(pretty_flags[:8]))
    else:
        lines.append("Signals: no strong recent link evidence yet")

    if warn_count > 0:
        lines.append(f"History: {warn_count} warning(s) on record")

    if tier in {"CONFIRMED DUPLICATE", "STRONGLY LINKED"} or score >= 65:
        action = "Restrict and review immediately."
    elif score >= 20 or source_unknown or warn_count > 0:
        action = "Watch verification; keep unverified containment until source/behavior looks clean."
    else:
        action = "Normal verification; keep logging for new behavior, reports, or source changes."

    lines.append(f"Recommended action: {action}")
    return _chunk_lines(lines, 1000)
'''
    return text[:pos] + function + text[pos:]


def patch_join_source_if_missing(text: str) -> str:
    if 'fields.append(("Join Source"' in text:
        return text

    target = '    fields.append(("Risk Context", _chunk_lines(base_lines, 1000), False))\n\n    truth_value = _context_truth_value(guild, truth_context, merged_risk)\n'
    if target not in text:
        raise SystemExit("Could not add Join Source field")

    replacement = '''    fields.append(("Risk Context", _chunk_lines(base_lines, 1000), False))

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
    return text.replace(target, replacement)


def patch_smart_field_if_missing(text: str) -> str:
    if 'fields.append(("Smart Join Intelligence"' in text:
        return text

    target = '    fields.append(("Risk Context", _chunk_lines(base_lines, 1000), False))\n'
    if target not in text:
        raise SystemExit("Could not find Risk Context append")

    replacement = '''    fields.append(("Risk Context", _chunk_lines(base_lines, 1000), False))

    smart_value = _smart_join_intelligence_value(merged_risk, latest_join, guild_member, warn_count=warn_count)
    if smart_value:
        fields.append(("Smart Join Intelligence", smart_value, False))
'''
    return text.replace(target, replacement, 1)


def patch_modlog() -> None:
    text = MODLOG.read_text(encoding="utf-8")

    text = text.replace("DM Raider Report Risk:", "DM Raider Risk:")
    text = text.replace("DM Raider Report Risk", "DM Raider Risk")

    text = insert_smart_function(text)
    text = patch_join_source_if_missing(text)
    text = patch_smart_field_if_missing(text)

    required = (
        "def _smart_join_intelligence_value(",
        "Smart Join Intelligence",
        "Official bot:",
        "Human automation/userbot risk:",
        "Invite/source confidence:",
        "Context gap:",
        "Recommended action:",
    )
    missing = [token for token in required if token not in text]
    if missing:
        raise SystemExit("modlog missing smart intelligence tokens: " + ", ".join(missing))

    MODLOG.write_text(text, encoding="utf-8")
    print("✅ modlog smart join intelligence added")


def write_test() -> None:
    TEST.write_text(
        '''from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAID = (ROOT / "stoney_verify/raidguard.py").read_text(encoding="utf-8")
MODLOG = (ROOT / "stoney_verify/modlog.py").read_text(encoding="utf-8")


def test_official_bot_and_human_risk_are_separate() -> None:
    assert "Official Bot: Yes" in RAID
    assert "Official Bot: No" in RAID
    assert "Alt/Raid Risk:" in RAID
    assert "DM Raider Risk:" in RAID
    assert "DM Raider Report Risk" not in RAID


def test_modlog_has_smart_join_intelligence() -> None:
    assert "def _smart_join_intelligence_value(" in MODLOG
    assert "Smart Join Intelligence" in MODLOG
    assert "Human automation/userbot risk:" in MODLOG
    assert "Invite/source confidence:" in MODLOG
    assert "Context gap:" in MODLOG
    assert "Recommended action:" in MODLOG


def test_join_source_truth_is_visible() -> None:
    assert 'fields.append(("Join Source"' in MODLOG
    assert "Entry method:" in MODLOG
    assert "Confidence:" in MODLOG
    assert "Why:" in MODLOG


if __name__ == "__main__":
    for test in (
        test_official_bot_and_human_risk_are_separate,
        test_modlog_has_smart_join_intelligence,
        test_join_source_truth_is_visible,
    ):
        test()
        print(f"PASS {test.__name__}")
''',
        encoding="utf-8",
    )
    print("✅ wrote smart join intelligence static test")


def main() -> None:
    patch_raidguard()
    patch_modlog()
    write_test()


if __name__ == "__main__":
    main()
