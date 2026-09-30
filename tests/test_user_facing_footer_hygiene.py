from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "stoney_verify"

# These patterns describe implementation/debug metadata that must never be
# rendered in a normal Discord embed footer. Footer text may still contain
# useful user guidance, counts, pagination, confirmation warnings, and product
# labels.
_FORBIDDEN_SOURCE_PATTERNS = (
    "guild.id",
    ".guild.id",
    "channel.id",
    "user.id",
    "member.id",
    "config source",
    "canonical live runtime",
    "per-process",
    "dank_shield:",
    "stoney_verify:",
    "modlog_channel_id",
    "runtime:v",
    "schema:v",
    "persistent replacements are recorded",
    "one canonical join sender",
    "provider-backed features",
    "grouped from existing health evidence",
    "reserved infrastructure",
    "redundant command entry points",
    "category-menu",
)


def _set_footer_calls(source: str, path: Path):
    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "set_footer":
            continue
        segment = ast.get_source_segment(source, node) or ""
        yield node.lineno, segment


def test_user_facing_footers_do_not_expose_debug_or_internal_metadata() -> None:
    failures: list[str] = []

    for path in sorted(PACKAGE.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for line_no, segment in _set_footer_calls(source, path):
            lowered = segment.casefold()
            for pattern in _FORBIDDEN_SOURCE_PATTERNS:
                if pattern.casefold() in lowered:
                    rel = path.relative_to(ROOT)
                    failures.append(
                        f"{rel}:{line_no} exposes forbidden footer metadata {pattern!r}: "
                        f"{segment.strip()}"
                    )

    assert failures == [], "\n".join(failures)


def test_footer_runtime_markers_are_legacy_detection_only() -> None:
    spam = (PACKAGE / "spam_guard.py").read_text(encoding="utf-8")
    profile = (PACKAGE / "profile_card_runtime_core.py").read_text(encoding="utf-8")
    transcripts = (PACKAGE / "transcripts.py").read_text(encoding="utf-8")
    verify = (PACKAGE / "verification_new/basic_verify.py").read_text(encoding="utf-8")

    assert 'return "Spam Guard incident • restore available"' in spam
    assert 'return LIVE_CARD_FOOTER_PREFIX' in profile
    assert '_LEGACY_LIVE_CARD_FOOTER_RE' in profile
    assert '_LEGACY_MARKERS' in transcripts
    assert 'LEGACY_BASIC_VERIFY_FOOTERS' in verify
