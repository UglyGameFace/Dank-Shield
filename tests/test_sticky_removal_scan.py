"""The old Community Tools Sticky Messages system is gone — pinned by scan.

Phase 2 of the Post Builder consolidation removed the Community Tools
sticky/poll system entirely (hub UI, service, runtime engine, preview
file). Quiet Server Notices, which shared the runtime module, must keep
working — the quiet suites pin that. This scan pins the other half: no
production code may reference the removed system again. Historical
prose (docstrings/comments explaining the removal) and the inert SQL
archive are out of scope; this scans Python identifiers and imports.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = ROOT / "stoney_verify"

FORBIDDEN = [
    r"\bStickyCenterView\b",
    r"\bStickySettingsView\b",
    r"\bStickyTypeView\b",
    r"\bStickyPollView\b",
    r"\bStickyVoteButton\b",
    r"\bStickyConfig\b",
    r"\bStickyPoll\b",
    r"\bget_sticky\b",
    r"\bget_sticky_poll\b",
    r"\bsave_sticky\b",
    r"\bsave_sticky_bundle\b",
    r"\bdelete_sticky\b",
    r"\blist_stickies\b",
    r"\bset_sticky_enabled\b",
    r"\bset_sticky_poll_state\b",
    r"\breset_sticky_poll\b",
    r"\bcast_sticky_poll_vote\b",
    r"\bupdate_sticky_delivery\b",
    r"\bshould_refresh_sticky\b",
    r"\bsticky_poll_embed\b",
    r"\bsticky_embed\b",
    r"\bcast_and_render_poll_vote\b",
    r"\bpublic_sticky_preview\b",
    r"\bMANAGED_WEBHOOK_NAME\b",
    r"dank:sticky:vote",
    r"Back to Stickies",
    r"\bSticky Messages\b",
]


def _production_sources() -> list[Path]:
    return sorted(PRODUCTION.rglob("*.py"))


def test_no_references_to_removed_sticky_system_in_production_code() -> None:
    patterns = [re.compile(item) for item in FORBIDDEN]
    offenders: list[str] = []
    for path in _production_sources():
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            if pattern.search(text):
                offenders.append(f"{path.relative_to(ROOT)}: {pattern.pattern}")
    assert offenders == [], "removed-system references remain:\n" + "\n".join(offenders)


def test_sticky_preview_module_is_deleted() -> None:
    assert not (PRODUCTION / "commands_ext" / "public_sticky_preview.py").exists()


def test_quiet_notice_entry_points_survive() -> None:
    hub = (PRODUCTION / "commands_ext" / "public_community_tools.py").read_text(encoding="utf-8")
    assert 'label="Quiet Server Notice"' in hub
    assert "open_quiet_notice_center" in hub
    quiet_ui = (PRODUCTION / "commands_ext" / "public_quiet_notice.py").read_text(encoding="utf-8")
    assert 'label="Back to Community Tools"' in quiet_ui
    assert "open_community_tools" in quiet_ui
    runtime = (PRODUCTION / "community_tools_runtime.py").read_text(encoding="utf-8")
    assert "def ensure_community_tools_runtime" in runtime
    assert runtime.count('add_listener(runtime.on_message, "on_message")') == 1


def test_post_builder_owns_the_only_sticky_gate() -> None:
    sticky = (PRODUCTION / "embed_studio_sticky.py").read_text(encoding="utf-8")
    assert "community_tools" not in sticky
    # The one-sticky-per-channel rule is Post Builder's own index lookup.
    assert "channel_sticky_key" in sticky
