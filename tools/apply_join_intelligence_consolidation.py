from __future__ import annotations

"""Consolidate overlapping member-join intelligence into one canonical flow.

Final member context fields:
1. Join Intelligence
2. Evidence & Source
3. Identity Links — only when real identity context exists

This removes obsolete field emitters and old appliers that can reintroduce
duplicate Risk Context / Evidence Health / Smart Intelligence sections.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MODLOG = ROOT / "stoney_verify/modlog.py"
TEST = ROOT / "tools/test_join_intelligence_consolidation_static.py"

OBSOLETE_FILES = (
    "tools/apply_bot_detection_stats_clarity.py",
    "tools/test_bot_detection_stats_clarity_static.py",
    "tools/apply_smart_join_intelligence.py",
    "tools/test_smart_join_intelligence_static.py",
    "tools/apply_source_reputation_intelligence.py",
    "tools/test_source_reputation_intelligence_static.py",
    "tools/apply_evidence_health_intelligence.py",
    "tools/test_evidence_health_intelligence_static.py",
    "tools/apply_containment_posture_intelligence.py",
    "tools/test_containment_posture_intelligence_static.py",
)

NEW_HELPERS = r'''
def _member_role_ids(member_or_user: discord.abc.User) -> Set[int]:
    out: Set[int] = set()
    try:
        for role in getattr(member_or_user, "roles", []) or []:
            role_id = _safe_int(getattr(role, "id", 0), 0)
            if role_id > 0:
                out.add(role_id)
    except Exception:
        pass
    return out


def _configured_access_state(
    member_or_user: discord.abc.User,
    runtime_config: Dict[str, Any],
    *,
    is_bot: bool,
) -> Tuple[str, str]:
    if is_bot:
        return (
            "BOT ACCOUNT",
            "Review who added the bot and whether its permissions are appropriate.",
        )

    role_ids = _member_role_ids(member_or_user)

    unverified_id = _safe_int(runtime_config.get("unverified_role_id"), 0)
    verified_id = _safe_int(runtime_config.get("verified_role_id"), 0)
    resident_id = _safe_int(runtime_config.get("resident_role_id"), 0)
    staff_id = _safe_int(runtime_config.get("staff_role_id"), 0)
    vc_staff_id = _safe_int(runtime_config.get("vc_staff_role_id"), 0)

    configured_ids = {
        role_id
        for role_id in (
            unverified_id,
            verified_id,
            resident_id,
            staff_id,
            vc_staff_id,
        )
        if role_id > 0
    }

    if staff_id > 0 and staff_id in role_ids:
        return ("STAFF / PRIVILEGED", "Configured staff role is present.")
    if vc_staff_id > 0 and vc_staff_id in role_ids:
        return ("STAFF / PRIVILEGED", "Configured VC staff role is present.")

    has_unverified = unverified_id > 0 and unverified_id in role_ids
    has_verified = verified_id > 0 and verified_id in role_ids
    has_resident = resident_id > 0 and resident_id in role_ids

    if has_unverified:
        return (
            "UNVERIFIED / CONTAINED",
            "Configured unverified role is present.",
        )

    if has_verified or has_resident:
        return (
            "VERIFIED ACCESS",
            "Configured verified/resident access role is present.",
        )

    if not configured_ids:
        return (
            "ROLE CONFIG MISSING",
            "No authoritative access-role IDs are configured for this server.",
        )

    return (
        "NO ACCESS ROLE DETECTED",
        "Member has none of the configured unverified, verified, resident, or staff roles.",
    )


def _join_source_is_uncertain(
    entry_method: str,
    join_source: str,
    entry_confidence: int,
) -> bool:
    uncertain_values = {
        "",
        "unknown",
        "unknown_join",
        "invite_unresolved",
        "invite_cache_warming",
        "invite_tracking_unavailable",
    }
    return (
        str(entry_method or "").strip().lower() in uncertain_values
        or str(join_source or "").strip().lower() in uncertain_values
        or int(entry_confidence or 0) < 50
    )
'''

NEW_CONTEXT_FUNCTION = r'''
async def _build_member_context_fields(
    guild: discord.Guild,
    member_or_user: discord.abc.User,
) -> List[Tuple[str, str, bool]]:
    try:
        guild_member = await _run_blocking_db(
            _sb_select_guild_member_sync,
            guild.id,
            member_or_user.id,
        ) or {}
        latest_join = await _run_blocking_db(
            _sb_select_latest_join_sync,
            guild.id,
            member_or_user.id,
        ) or {}
        warn_count = await _run_blocking_db(
            _sb_select_warn_count_sync,
            guild.id,
            member_or_user.id,
        )
        truth_context = await _run_blocking_db(
            _sb_get_identity_truth_context_sync,
            guild.id,
            member_or_user.id,
        ) or {}
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

    try:
        runtime_config = dict(await get_guild_config(guild.id) or {})
    except Exception:
        runtime_config = {}

    risk_profile: Dict[str, Any] = {}
    try:
        if isinstance(member_or_user, discord.Member):
            risk_profile = build_member_risk_profile(member_or_user) or {}
    except Exception:
        risk_profile = {}

    merged_risk: Dict[str, Any] = {}
    merged_risk.update(guild_member if isinstance(guild_member, dict) else {})
    merged_risk.update(latest_join if isinstance(latest_join, dict) else {})
    merged_risk.update(risk_profile if isinstance(risk_profile, dict) else {})

    is_bot = (
        _safe_bool(merged_risk.get("is_bot_account"), False)
        or bool(getattr(member_or_user, "bot", False))
    )

    score = _safe_int(
        merged_risk.get("risk_score"),
        _safe_int(merged_risk.get("score"), 0),
    )
    level = _safe_str(
        merged_risk.get("risk_level") or merged_risk.get("level"),
        "low",
    ).upper()
    tier = _safe_str(
        merged_risk.get("evidence_tier"),
        "clear",
    ).replace("_", " ").upper()

    account_age = _account_age_human(member_or_user)
    _joined_gap_seconds, joined_gap_human = _join_after_creation_delta(
        member_or_user if isinstance(member_or_user, discord.Member) else None
    )

    entry_method = _safe_str(
        merged_risk.get("entry_method")
        or latest_join.get("entry_method")
        or guild_member.get("entry_method"),
        "unknown",
    )
    join_source = _safe_str(
        merged_risk.get("join_source")
        or latest_join.get("join_source")
        or guild_member.get("join_source"),
        "unknown",
    )
    invite_code = _safe_str(
        merged_risk.get("invite_code")
        or latest_join.get("invite_code")
        or guild_member.get("invite_code"),
        "unknown",
    )
    entry_quality = _safe_str(
        merged_risk.get("entry_truth_quality")
        or latest_join.get("entry_truth_quality")
        or guild_member.get("entry_truth_quality"),
        "unknown",
    )
    entry_confidence = _safe_int(
        merged_risk.get("entry_confidence")
        or latest_join.get("entry_confidence")
        or guild_member.get("entry_confidence"),
        0,
    )
    entry_reason = _safe_str(
        merged_risk.get("entry_quality_reason")
        or latest_join.get("entry_quality_reason")
        or guild_member.get("entry_quality_reason")
    )

    source_uncertain = _join_source_is_uncertain(
        entry_method,
        join_source,
        entry_confidence,
    )

    source_sample = _safe_int(source_reputation.get("sample_size"), 0)
    source_risky = _safe_int(source_reputation.get("risky_count"), 0)
    source_strong = _safe_int(
        source_reputation.get("strong_or_confirmed_count"),
        0,
    )
    source_low_conf = _safe_int(
        source_reputation.get("low_confidence_count"),
        0,
    )

    identity_matches = max(
        _safe_int(merged_risk.get("identity_proof_match_count"), 0),
        len(list(truth_context.get("proof_matches") or [])),
    )
    manual_confirmed = max(
        _safe_int(merged_risk.get("manual_confirmed_match_count"), 0),
        len(list(truth_context.get("manual_confirmed") or [])),
    )
    manual_likely = max(
        _safe_int(merged_risk.get("manual_likely_match_count"), 0),
        len(list(truth_context.get("manual_likely") or [])),
    )

    flags = _extract_flags_from_profile_like(merged_risk)
    pretty_flags = [
        _pretty_flag_label(flag)
        for flag in flags
        if _pretty_flag_label(flag)
    ]

    fingerprint_count = _safe_int(
        merged_risk.get("same_fingerprint_count"),
        0,
    )
    similar_name_count = _safe_int(
        merged_risk.get("similar_name_count"),
        0,
    )
    burst_count = _safe_int(
        merged_risk.get("burst_join_count")
        or merged_risk.get("burst_count"),
        0,
    )

    access_state, access_reason = _configured_access_state(
        member_or_user,
        runtime_config,
        is_bot=is_bot,
    )

    hard_evidence = (
        identity_matches > 0
        or manual_confirmed > 0
        or tier in {"CONFIRMED DUPLICATE", "STRONGLY LINKED"}
        or score >= 65
        or source_strong > 0
    )

    watchlist_evidence = (
        score >= 20
        or bool(pretty_flags)
        or source_uncertain
        or source_risky > 0
        or source_low_conf >= 3
        or warn_count > 0
        or fingerprint_count > 0
        or similar_name_count > 0
        or burst_count > 1
        or manual_likely > 0
    )

    if is_bot:
        verdict = "BOT REVIEW"
        action = "Review who added it and audit its permissions before trusting it."
    elif hard_evidence:
        verdict = "REVIEW NOW"
        action = "Keep contained and review the evidence immediately."
    elif watchlist_evidence:
        verdict = "WATCHLIST"
        action = "Keep on the normal verification path; do not treat CLEAR as proof of safety."
    else:
        verdict = "NO CURRENT RISK SIGNALS"
        action = "Continue normal verification and behavior logging."

    if access_state == "VERIFIED ACCESS" and verdict in {"REVIEW NOW", "WATCHLIST"}:
        action = "Review immediately because this member already has verified/resident access."
    elif access_state == "ROLE CONFIG MISSING":
        action = "Fix the server access-role configuration before relying on containment status."
    elif access_state == "NO ACCESS ROLE DETECTED":
        action = "Check setup and access routing; no configured containment/access role was found."

    intelligence_lines = [
        f"Verdict: **{verdict}**",
        f"Official bot: **{'Yes' if is_bot else 'No'}**",
        f"Alt/raid evidence: **{tier}** • **{level} / {score}/100**",
        f"Access state: **{access_state}** — {access_reason}",
        f"Recommended action: {action}",
    ]

    evidence_lines = [
        f"Account age: **{account_age}**",
        f"Entry: `{entry_method}` • Source: `{join_source}`",
        f"Invite: `{invite_code}`",
        f"Source confidence: **{entry_quality} / {entry_confidence}/100**",
    ]

    if joined_gap_human:
        evidence_lines.append(f"Created-to-join timing: **{joined_gap_human}**")

    if entry_reason:
        evidence_lines.append(f"Source explanation: {_truncate(entry_reason, 180)}")

    if pretty_flags:
        evidence_lines.append("Signals: " + " • ".join(pretty_flags[:7]))
    else:
        evidence_lines.append("Signals: no current join-time warning flags")

    cluster_bits: List[str] = []
    if fingerprint_count > 0:
        cluster_bits.append(f"shared fingerprints={fingerprint_count}")
    if similar_name_count > 0:
        cluster_bits.append(f"similar names={similar_name_count}")
    if burst_count > 0:
        cluster_bits.append(f"burst joins={burst_count}")
    if cluster_bits:
        evidence_lines.append("Recent cluster: " + " • ".join(cluster_bits))

    if source_sample > 0:
        evidence_lines.append(
            "Same-source history: "
            f"sample={source_sample} • risky={source_risky} • "
            f"strong/confirmed={source_strong} • "
            f"low-confidence={source_low_conf}"
        )
    else:
        evidence_lines.append("Same-source history: not enough matching history yet")

    if warn_count > 0:
        evidence_lines.append(f"Prior warnings: **{warn_count}**")

    evidence_lines.append(
        "DM/userbot scope: private member DMs are not visible to the bot; "
        "DM-spam findings require member/staff reports."
    )

    fields: List[Tuple[str, str, bool]] = [
        (
            "Join Intelligence",
            _chunk_lines(intelligence_lines, 1000),
            False,
        ),
        (
            "Evidence & Source",
            _chunk_lines(evidence_lines, 1000),
            False,
        ),
    ]

    truth_value = _context_truth_value(
        guild,
        truth_context,
        merged_risk,
    )
    if truth_value:
        fields.append(
            (
                "Identity Links",
                _truncate(truth_value, 1000),
                False,
            )
        )

    return fields
'''


def remove_top_level_function(text: str, name: str) -> str:
    pattern = re.compile(
        rf"(?ms)^def {re.escape(name)}\(.*?"
        rf"(?=^(?:def |async def |class |# ={{5,}})|\Z)"
    )
    match = pattern.search(text)
    if not match:
        return text
    return text[:match.start()] + text[match.end():]


def ensure_guild_config_import(text: str) -> str:
    if "from .guild_config import get_guild_config" in text:
        return text

    marker = "\n\n# ==========================================================\n# Small local helpers"
    if marker not in text:
        raise SystemExit("Could not find import insertion marker")

    block = '''
try:
    from .guild_config import get_guild_config
except Exception:
    async def get_guild_config(guild_id: Any, **kwargs) -> Dict[str, Any]:
        return {}
'''

    return text.replace(marker, "\n\n" + block.strip() + marker, 1)


def remove_alt_summary_import(text: str) -> str:
    text = text.replace(
        "from .raidguard import build_member_risk_profile, build_alt_detection_summary",
        "from .raidguard import build_member_risk_profile",
    )

    fallback = '''    def build_alt_detection_summary(member: discord.Member) -> str:
        return ""

'''
    text = text.replace(fallback, "")
    return text


def patch_modlog() -> None:
    text = MODLOG.read_text(encoding="utf-8")

    already_consolidated = (
        'fields.append(("Join Intelligence"' in text
        and 'fields.append(("Evidence & Source"' in text
        and "def _configured_access_state(" in text
    )

    if not already_consolidated:
        required_old_markers = (
            "async def _build_member_context_fields(",
            "def _member_role_name_set(",
            'fields.append(("Smart Join Intelligence"',
            'fields.append(("Evidence Health"',
            'fields.append(("Containment Posture"',
        )
        missing_old = [
            token
            for token in required_old_markers
            if token not in text
        ]
        if missing_old:
            raise SystemExit(
                "Join intelligence source is not in the expected pre-consolidation state: "
                + ", ".join(missing_old)
            )

        text = ensure_guild_config_import(text)
        text = remove_alt_summary_import(text)

        # Source reputation lookup remains useful, but its old standalone display
        # helper is replaced by the compact canonical evidence field.
        text = remove_top_level_function(
            text,
            "_source_reputation_value",
        )

        helpers_start = text.find("def _member_role_name_set(")
        context_start = text.find(
            "async def _build_member_context_fields(",
            helpers_start,
        )
        if helpers_start < 0 or context_start < 0:
            raise SystemExit("Could not find legacy intelligence helper range")

        text = (
            text[:helpers_start]
            + NEW_HELPERS.strip()
            + "\n\n\n"
            + text[context_start:]
        )

        context_start = text.find(
            "async def _build_member_context_fields("
        )
        public_marker = (
            "\n\n# ==========================================================\n"
            "# Public logging helpers"
        )
        context_end = text.find(public_marker, context_start)

        if context_start < 0 or context_end < 0:
            raise SystemExit("Could not replace member context function")

        text = (
            text[:context_start]
            + NEW_CONTEXT_FUNCTION.strip()
            + "\n\n"
            + text[context_end:]
        )

    forbidden_runtime_tokens = (
        'fields.append(("Risk Context"',
        'fields.append(("Evidence Health"',
        'fields.append(("Containment Posture"',
        'fields.append(("Smart Join Intelligence"',
        'fields.append(("Join Source"',
        'fields.append(("Source Reputation"',
        'fields.append(("Alt Summary"',
        "def _member_has_named_role(",
        "def _containment_posture_value(",
        "def _evidence_health_value(",
        "def _smart_join_intelligence_value(",
        "def _risk_summary_header(",
        "def _source_reputation_value(",
        "build_alt_detection_summary",
    )
    remaining = [
        token
        for token in forbidden_runtime_tokens
        if token in text
    ]
    if remaining:
        raise SystemExit(
            "Legacy join intelligence paths still remain: "
            + ", ".join(remaining)
        )

    required = (
        "from .guild_config import get_guild_config",
        "def _configured_access_state(",
        "unverified_role_id",
        "verified_role_id",
        "resident_role_id",
        "staff_role_id",
        "Join Intelligence",
        "Evidence & Source",
        "Identity Links",
        "DM/userbot scope:",
        "do not treat CLEAR as proof of safety",
    )
    missing = [token for token in required if token not in text]
    if missing:
        raise SystemExit(
            "Canonical join intelligence is incomplete: "
            + ", ".join(missing)
        )

    MODLOG.write_text(text, encoding="utf-8")
    print("✅ consolidated member join intelligence")


def remove_obsolete_files() -> None:
    for relative in OBSOLETE_FILES:
        path = ROOT / relative
        if path.exists():
            path.unlink()
            print(f"🗑️ removed obsolete {relative}")


def write_test() -> None:
    TEST.write_text(
        '''from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODLOG = (ROOT / "stoney_verify/modlog.py").read_text(encoding="utf-8")


def context_block() -> str:
    start = MODLOG.index("async def _build_member_context_fields(")
    end = MODLOG.index(
        "# ==========================================================\\n# Public logging helpers",
        start,
    )
    return MODLOG[start:end]


def test_only_canonical_join_fields_are_emitted() -> None:
    block = context_block()
    assert '"Join Intelligence"' in block
    assert '"Evidence & Source"' in block
    assert '"Identity Links"' in block

    for old in (
        '"Risk Context"',
        '"Evidence Health"',
        '"Containment Posture"',
        '"Smart Join Intelligence"',
        '"Join Source"',
        '"Source Reputation"',
        '"Alt Summary"',
    ):
        assert old not in block


def test_role_state_uses_per_guild_ids_not_name_guessing() -> None:
    assert "from .guild_config import get_guild_config" in MODLOG
    assert "unverified_role_id" in MODLOG
    assert "verified_role_id" in MODLOG
    assert "resident_role_id" in MODLOG
    assert "staff_role_id" in MODLOG
    assert "def _member_has_named_role(" not in MODLOG


def test_old_display_helpers_are_removed() -> None:
    for old in (
        "def _containment_posture_value(",
        "def _evidence_health_value(",
        "def _smart_join_intelligence_value(",
        "def _risk_summary_header(",
        "def _source_reputation_value(",
        "build_alt_detection_summary",
    ):
        assert old not in MODLOG


def test_canonical_summary_is_honest_and_actionable() -> None:
    assert "Official bot:" in MODLOG
    assert "Alt/raid evidence:" in MODLOG
    assert "Access state:" in MODLOG
    assert "Recommended action:" in MODLOG
    assert "Source confidence:" in MODLOG
    assert "Same-source history:" in MODLOG
    assert "DM/userbot scope:" in MODLOG
    assert "do not treat CLEAR as proof of safety" in MODLOG


if __name__ == "__main__":
    for test in (
        test_only_canonical_join_fields_are_emitted,
        test_role_state_uses_per_guild_ids_not_name_guessing,
        test_old_display_helpers_are_removed,
        test_canonical_summary_is_honest_and_actionable,
    ):
        test()
        print(f"PASS {test.__name__}")
''',
        encoding="utf-8",
    )
    print("✅ wrote canonical join intelligence test")


def main() -> None:
    patch_modlog()
    remove_obsolete_files()
    write_test()
    print("✅ join intelligence consolidation complete")


if __name__ == "__main__":
    main()
