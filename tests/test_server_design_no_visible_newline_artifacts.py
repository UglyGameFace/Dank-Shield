from __future__ import annotations

import ast
from pathlib import Path


TARGETS = [
    Path("stoney_verify/startup_guards/server_design_studio_command_guard.py"),
    Path("stoney_verify/startup_guards/server_design_majority_layout_guard.py"),
    Path("stoney_verify/services/server_design_majority_layout.py"),
    Path("stoney_verify/services/server_design_studio.py"),
]


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

            # Actual newline characters are fine. Visible slash/backslash artifacts are not.
            if "\\n" in text or "/n" in text or "\\\\n" in text:
                preview = text.replace("\n", "\\n")[:120]
                bad.append(f"{path}:{getattr(node, 'lineno', '?')}: {preview!r}")

    assert not bad, "Visible newline artifacts found:\n" + "\n".join(bad[:50])
