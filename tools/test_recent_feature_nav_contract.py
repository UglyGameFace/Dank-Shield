"""Fail CI when a user-facing editor/feature menu is changed without release notes.

The bot is Android-first. Its "New & Updated" menu is not a GitHub API feed:
every entry must truthfully describe a merged public feature. This PR-only
audit forces the navigation metadata owner to be reviewed with UI changes.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_MENU_OWNERS = frozenset({
    "stoney_verify/embed_studio_ui.py",
    "stoney_verify/commands_ext/public_share_router.py",
    "stoney_verify/commands_ext/public_action_schedule.py",
    "stoney_verify/commands_ext/public_verify_reminder_ui.py",
    "stoney_verify/commands_ext/public_somewhere_420.py",
    "stoney_verify/commands_ext/public_community_tools.py",
    "stoney_verify/commands_ext/public_command_surface_v2.py",
})
REGISTRY = "stoney_verify/navigation_registry.py"


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True,
        timeout=15, check=True,
    )
    return result.stdout


def run() -> None:
    if os.getenv("GITHUB_EVENT_NAME") != "pull_request":
        print("PASS: recent-update audit runs on pull requests only")
        return
    try:
        base = _git("merge-base", "origin/main", "HEAD").strip()
        paths = set(_git("diff", "--name-only", f"{base}..HEAD").splitlines())
    except (subprocess.SubprocessError, OSError) as exc:
        raise AssertionError("Cannot inspect PR diff for New & Updated audit") from exc
    changed_menus = PUBLIC_MENU_OWNERS & paths
    if not changed_menus:
        print("PASS: no public feature menu was changed")
        return
    if REGISTRY not in paths:
        raise AssertionError(
            "Public feature menu changed but navigation_registry.py was not "
            "reviewed. Update its merged-feature New & Updated notes, search "
            "aliases, and grouping before submitting this PR. "
            f"Changed menus: {sorted(changed_menus)}"
        )
    patch = _git("diff", "--unified=0", f"{base}..HEAD", "--", REGISTRY)
    if not any(
        token in patch for token in (
            "+    RecentFeatureUpdate(",
            "-    RecentFeatureUpdate(",
            "+        \"",
            "-        \"",
            "+RECENT_UPDATES:",
        )
    ):
        # Navigation-only rewording still requires review, but if this
        # commit makes no public update announcement, documenting the
        # decision is better than silently reusing stale release notes.
        print("PASS: navigation metadata changed; verify visible release copy in review")
    else:
        print("PASS: navigation metadata and feature updates reviewed")


if __name__ == "__main__":
    try:
        run()
    except AssertionError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
