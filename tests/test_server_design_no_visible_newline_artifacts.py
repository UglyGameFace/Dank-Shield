from __future__ import annotations

import ast
import re
from pathlib import Path


TARGETS = [
    Path("stoney_verify/startup_guards/server_design_studio_command_guard.py"),
    Path("stoney_verify/startup_guards/server_design_majority_layout_guard.py"),
    Path("stoney_verify/services/server_design_majority_layout.py"),
    Path("stoney_verify/services/server_design_studio.py"),
]


# Real bad copy examples:
#   "Hello\\nWorld" shown to Discord
#   "Hello/nWorld" shown to Discord
#
# False positives we allow:
#   "\n" used by Python for real joins
#   "\\n" used by sanitizer code
#   "/name" in normal copy like "emoji/name/font"
_EXACT_SANITIZER_MARKERS = {"\\n", "\\\\n", "/n", "\n"}
_VISIBLE_BACKSLASH_N = re.compile(r"\\\\n|\\n")
_VISIBLE_SLASH_N = re.compile(r"(?<![A-Za-z0-9_])/n(?![A-Za-z0-9_])")


def _looks_like_user_copy(text: str) -> bool:
    if text in _EXACT_SANITIZER_MARKERS:
        return False

    # Tiny marker strings are usually parser/sanitizer internals, not Discord text.
    if len(text.strip()) <= 4:
        return False

    return True


def test_dank_design_user_text_has_no_visible_newline_artifacts():
    bad: list[str] = []

    for path in TARGETS:
        if not path.exists():
            continue

        tree = ast.parse(path.read_text(), filename=str(path))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue

            text = node.value
            if not _looks_like_user_copy(text):
                continue

            has_bad_backslash_n = bool(_VISIBLE_BACKSLASH_N.search(text))
            has_bad_slash_n = bool(_VISIBLE_SLASH_N.search(text))

            if has_bad_backslash_n or has_bad_slash_n:
                preview = text.replace("\n", "\\n")[:160]
                bad.append(f"{path}:{getattr(node, 'lineno', '?')}: {preview!r}")

    assert not bad, "Visible newline artifacts found:\n" + "\n".join(bad[:50])
