from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "stoney_verify" / "tickets_new" / "managed_category_service.py"
GROUP = ROOT / "stoney_verify" / "commands_ext" / "public_ticket_category_group.py"


def test_managed_category_service_calls_reconciliation_rpc() -> None:
    text = SERVICE.read_text(encoding="utf-8")
    tree = ast.parse(text)
    assert "reconcile_dank_ticket_categories" in text
    assert "p_guild_id" in text
    assert any(isinstance(node, ast.AsyncFunctionDef) and node.name == "sync_managed_categories" for node in ast.walk(tree))


def test_public_category_group_exposes_sync_and_managed_guards() -> None:
    text = GROUP.read_text(encoding="utf-8")
    assert 'name="sync"' in text
    assert "managed_by_dank" in text
    assert "global Dank Shield catalog" in text
