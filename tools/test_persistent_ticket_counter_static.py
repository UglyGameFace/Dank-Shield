from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_public_panel_uses_persistent_allocator() -> None:
    text = read("stoney_verify/commands_ext/public_ticket_panel_clean.py")
    assert "reserve_persistent_ticket_number" in text
    match = re.search(r"async def _next_number\([\s\S]*?\n\n\ndef _channel_is_closed_like", text)
    assert match, "_next_number block not found"
    block = match.group(0)
    assert "return await reserve_persistent_ticket_number" in block
    assert "guild.text_channels" not in block
    assert "_db_max_ticket_number" not in block


def test_ticket_service_uses_persistent_allocator() -> None:
    text = read("stoney_verify/tickets_new/service.py")
    assert "reserve_persistent_ticket_number" in text
    match = re.search(r"async def _reserve_next_ticket_number\([\s\S]*?\n\n\nasync def _next_ticket_number", text)
    assert match, "_reserve_next_ticket_number block not found"
    block = match.group(0)
    assert "return await reserve_persistent_ticket_number" in block
    assert "_channel_scan_max_ticket_number" not in block
    assert ".update(" not in block


def test_legacy_sequence_guard_no_longer_overrides_native_numbering() -> None:
    text = read("stoney_verify/startup_guards/__init__.py")
    assert "external_ticket_history_sequence_guard" not in text


def test_schema_bootstrap_exposes_atomic_rpc() -> None:
    text = read("stoney_verify/startup_guards/auto_schema_bootstrap.py")
    assert "create or replace function public.reserve_ticket_number" in text
    assert "update public.ticket_counters" in text
    assert "return v_next" in text


def test_counter_allocator_is_db_authoritative() -> None:
    text = read("stoney_verify/tickets_new/counter_allocator.py")
    assert "COUNTER_TABLE = \"ticket_counters\"" in text
    assert "TICKETS_TABLE = \"tickets\"" in text
    assert "reserve_next_ticket_number" in text
    assert "refusing to allocate from Discord channels" in text


if __name__ == "__main__":
    tests = [
        test_public_panel_uses_persistent_allocator,
        test_ticket_service_uses_persistent_allocator,
        test_legacy_sequence_guard_no_longer_overrides_native_numbering,
        test_schema_bootstrap_exposes_atomic_rpc,
        test_counter_allocator_is_db_authoritative,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")

