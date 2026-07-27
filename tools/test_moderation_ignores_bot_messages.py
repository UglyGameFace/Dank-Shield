#!/usr/bin/env python3
"""Regression audit: bot-owned presentation output cannot feed moderation.

Live profile signatures are Discord messages authored by Dank Shield. Spam and
raid message handlers must reject bot authors before counters, evidence, alerts,
or enforcement are touched.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "stoney_verify"


def _function_source(path: Path, node: ast.AsyncFunctionDef, lines: list[str]) -> str:
    end = int(getattr(node, "end_lineno", node.lineno))
    return "\n".join(lines[node.lineno - 1 : end])


def _has_bot_author_guard(source: str) -> bool:
    compact = "".join(source.lower().split())
    accepted = (
        ".author.bot",
        "message.author.bot",
        "getattr(message.author,\"bot\"",
        "getattr(getattr(message,\"author\"",
        "author_is_bot",
        "is_bot_author",
        "_is_human_message",
        "_should_ignore_message",
        "_is_supported_message",
    )
    return any(token in compact for token in accepted)


def main() -> int:
    checked: list[str] = []
    missing: list[str] = []

    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        lowered_path = str(path.relative_to(ROOT)).lower()
        text = path.read_text(encoding="utf-8")
        lowered_text = text.lower()
        if "spam" not in lowered_path and "raid" not in lowered_path:
            if "spamguard" not in lowered_text and "raid" not in lowered_text:
                continue
        if "on_message" not in text:
            continue
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        lines = text.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if node.name not in {"on_message", "handle_message", "process_message"}:
                continue
            args = {argument.arg for argument in node.args.args}
            if "message" not in args:
                continue
            label = f"{path.relative_to(ROOT)}:{node.lineno}:{node.name}"
            checked.append(label)
            source = _function_source(path, node, lines)
            if not _has_bot_author_guard(source):
                missing.append(label)

    if not checked:
        raise AssertionError("No SpamGuard/raid message handlers were discovered")
    if missing:
        raise AssertionError(
            "Bot-author guard missing from moderation message handlers: "
            + ", ".join(missing)
        )

    print(
        "PASS bot-owned messages are rejected by moderation handlers "
        f"handlers={len(checked)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
