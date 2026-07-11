from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODLOG = ROOT / "stoney_verify/modlog.py"
TEST = ROOT / "tools/test_containment_posture_intelligence_static.py"


def insert_before(text: str, marker: str, block: str, token: str) -> str:
    if token in text:
        return text
    pos = text.find(marker)
    if pos < 0:
        raise SystemExit(f"Could not find marker: {marker}")
    return text[:pos] + block.rstrip() + "\n\n\n" + text[pos:]


def patch_helper(text: str) -> str:
    block = r'''
def _member_role_name_set(member: discord.abc.User) -> Set[str]:
    names: Set[str] = set()
    try:
        for role in getattr(member, "roles", []) or []:
            name = _safe_str(getattr(role, "name", "")).lower()
            if name:
                names.add(name)
    except Exception:
        pass
    return names


def _member_has_named_role(member: discord.abc.User, *needles: str) -> bool:
    names = _member_role_name_set(member)
    clean_needles = [str(n or "").strip().lower() for n in needles if str(n or "").strip()]
    for name in names:
        for needle in clean_needles:
            if name == needle or needle in name:
                return True
    return False


def _containment_posture_value(
    member_or_user: discord.abc.User,
    merged_risk: Dict[str, Any],
    latest_join: Dict[str, Any],
    guild_member: Dict[str, Any],
    source_reputation: Dict[str, Any] | None = None,
    *,
    warn_count: int = 0,
) -> str:
    merged = dict(merged_risk or {})
    latest = dict(latest_join or {})
    member_row = dict(guild_member or {})
    source_rep = dict(source_reputation or {})

    is_bot = _safe_bool(merged.get("is_bot_account"), False) or bool(getattr(member_or_user, "bot", False))
    score = _safe_int(merged.get("risk_score"), _safe_int(merged.get("score"), 0))
    tier = _safe_str(merged.get("evidence_tier"), "clear").replace("_", " ").upper()

    entry_method = _safe_str(merged.get("entry_method") or latest.get("entry_method") or member_row.get("entry_method"), "unknown")
    join_source = _safe_str(merged.get("join_source") or latest.get("join_source") or member_row.get("join_source"), "unknown")
    entry_confidence = _safe_int(merged.get("entry_confidence") or latest.get("entry_confidence") or member_row.get("entry_confidence"), 0)

    source_risky = _safe_int(source_rep.get("risky_count"), 0)
    source_strong = _safe_int(source_rep.get("strong_or_confirmed_count"), 0)
    source_low_conf = _safe_int(source_rep.get("low_confidence_count"), 0)

    has_unverified = _member_has_named_role(member_or_user, "unverified")
    has_verified = _member_has_named_role(member_or_user, "verified") and not has_unverified
    has_resident = _member_has_named_role(member_or_user, "resident")
    has_staffish = _member_has_named_role(member_or_user, "staff", "mod", "admin", "owner")

    source_unknown = (
        entry_method in {"", "unknown", "unknown_join", "invite_unresolved", "invite_cache_warming", "invite_tracking_unavailable"}
        or join_source in {"", "unknown", "unknown_join", "invite_unresolved", "invite_cache_warming", "invite_tracking_unavailable"}
        or entry_confidence < 50
    )

    hold_reasons: List[str] = []
    if source_unknown:
        hold_reasons.append("invite/source unresolved or low-confidence")
    if score >= 20 or tier in {"SUSPICIOUS", "STRONGLY LINKED", "CONFIRMED DUPLICATE"}:
        hold_reasons.append(f"alt/raid risk {tier} {score}/100")
    if source_strong > 0 or source_risky >= 3:
        hold_reasons.append("same source has risky join history")
    if source_low_conf >= 3:
        hold_reasons.append("same source repeatedly has weak attribution")
    if warn_count > 0:
        hold_reasons.append(f"{warn_count} prior warning(s)")

    if is_bot:
        containment = "BOT ACCOUNT"
        access = "Bot permissions must be reviewed separately."
        action = "Review who added this bot and whether its permissions are safe."
    elif has_staffish:
        containment = "STAFF / PRIVILEGED"
        access = "Privileged role detected."
        action = "Staff/privileged accounts should bypass normal auto-risk only if intentionally trusted."
    elif has_verified or has_resident:
        containment = "VERIFIED ACCESS"
        access = "Verified/resident access detected."
        if hold_reasons:
            action = "Review immediately; this user already has access while watchlist reasons exist."
        else:
            action = "Normal access state; keep logging behavior."
    elif has_unverified:
        containment = "UNVERIFIED / CONTAINED"
        access = "No verified/resident access detected."
        if hold_reasons:
            action = "Keep contained until verification, source clarity, or staff review."
        else:
            action = "Normal verification path; containment is active."
    else:
        containment = "UNKNOWN ROLE STATE"
        access = "No clear unverified/verified/resident role detected."
        action = "Check setup roles; containment may not be applied correctly."

    lines = [
        f"Containment: **{containment}**",
        f"Access state: {access}",
    ]

    if hold_reasons:
        lines.append("Hold reasons: " + " • ".join(hold_reasons[:5]))
    else:
        lines.append("Hold reasons: none from current join evidence")

    lines.append(f"Recommended action: {action}")
    return _chunk_lines(lines, 1000)
'''
    marker = "\ndef _evidence_health_value("
    if marker not in text:
        marker = "\ndef _risk_summary_header("
    return insert_before(text, marker, block, "def _containment_posture_value(")


def patch_field(text: str) -> str:
    if 'fields.append(("Containment Posture"' in text:
        return text

    preferred = '''    if evidence_health:
        fields.append(("Evidence Health", evidence_health, False))

'''
    fallback = '''    fields.append(("Risk Context", _chunk_lines(base_lines, 1000), False))

'''

    block = '''    containment_posture = _containment_posture_value(
        member_or_user,
        merged_risk,
        latest_join,
        guild_member,
        locals().get("source_reputation", {}),
        warn_count=warn_count,
    )
    if containment_posture:
        fields.append(("Containment Posture", containment_posture, False))

'''

    if preferred in text:
        return text.replace(preferred, preferred + block, 1)

    if fallback in text:
        return text.replace(fallback, fallback + block, 1)

    raise SystemExit("Could not insert Containment Posture field")


def patch_modlog() -> None:
    text = MODLOG.read_text(encoding="utf-8")
    text = patch_helper(text)
    text = patch_field(text)

    required = (
        "def _containment_posture_value(",
        "Containment Posture",
        "UNVERIFIED / CONTAINED",
        "VERIFIED ACCESS",
        "UNKNOWN ROLE STATE",
        "Hold reasons:",
        "Recommended action:",
        "Keep contained until verification",
    )
    missing = [token for token in required if token not in text]
    if missing:
        raise SystemExit("Missing containment posture tokens: " + ", ".join(missing))

    MODLOG.write_text(text, encoding="utf-8")
    print("✅ Containment Posture intelligence added")


def write_test() -> None:
    TEST.write_text(
        '''from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODLOG = (ROOT / "stoney_verify/modlog.py").read_text(encoding="utf-8")


def test_containment_helper_exists() -> None:
    assert "def _containment_posture_value(" in MODLOG
    assert "def _member_has_named_role(" in MODLOG
    assert "UNVERIFIED / CONTAINED" in MODLOG
    assert "VERIFIED ACCESS" in MODLOG
    assert "UNKNOWN ROLE STATE" in MODLOG


def test_containment_field_added_to_member_context() -> None:
    assert 'fields.append(("Containment Posture"' in MODLOG
    assert "Hold reasons:" in MODLOG
    assert "Recommended action:" in MODLOG


def test_containment_uses_source_and_risk_context() -> None:
    assert "same source has risky join history" in MODLOG
    assert "invite/source unresolved or low-confidence" in MODLOG
    assert "Keep contained until verification" in MODLOG
    assert 'locals().get("source_reputation", {})' in MODLOG


if __name__ == "__main__":
    for test in (
        test_containment_helper_exists,
        test_containment_field_added_to_member_context,
        test_containment_uses_source_and_risk_context,
    ):
        test()
        print(f"PASS {test.__name__}")
''',
        encoding="utf-8",
    )
    print("✅ wrote Containment Posture static test")


def main() -> None:
    patch_modlog()
    write_test()


if __name__ == "__main__":
    main()
