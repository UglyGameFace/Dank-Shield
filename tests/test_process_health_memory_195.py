from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROCESS_HEALTH = ROOT / "stoney_verify" / "startup_guards" / "process_health.py"


def _source() -> str:
    return PROCESS_HEALTH.read_text(encoding="utf-8")


def test_process_health_source_parses_linux_current_rss_separately_from_peak() -> None:
    source = _source()
    tree = ast.parse(source)
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}

    assert "_current_rss_mb" in functions
    assert "_peak_rss_mb" in functions
    assert "_memory_snapshot" in functions
    assert 'Path("/proc/self/status")' in source
    assert 'Path("/proc/self/statm")' in source
    assert 'line.startswith("VmRSS:")' in source


def test_ru_maxrss_is_labeled_peak_not_current() -> None:
    source = _source()
    current_start = source.index("def _current_rss_mb")
    peak_start = source.index("def _peak_rss_mb")
    snapshot_start = source.index("def _memory_snapshot")

    current_body = source[current_start:peak_start]
    peak_body = source[peak_start:snapshot_start]

    # The current-RSS function may document why ru_maxrss is unsuitable, but it
    # must never call the peak-RSS API to obtain its current value.
    assert "resource.getrusage" not in current_body
    assert "resource.getrusage" in peak_body
    assert "ru_maxrss" in peak_body
    assert 'rss_current≈' in source
    assert 'rss_peak≈' in source
    assert 'return f"rss≈' not in source


def test_memory_snapshot_keeps_current_and_peak_visible_in_same_heartbeat() -> None:
    source = _source()
    start = source.index("def _memory_snapshot")
    end = source.index("def _operation_queue_snapshot", start)
    function_source = source[start:end]

    assert "_current_rss_mb()" in function_source
    assert "_peak_rss_mb()" in function_source
    assert 'rss_current≈{current_text}' in function_source
    assert 'rss_peak≈{peak_text}' in function_source
