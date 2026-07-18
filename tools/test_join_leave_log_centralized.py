from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
router = (ROOT / "stoney_verify/startup_guards/member_lifecycle_router_guard.py").read_text(errors="ignore")
events = (ROOT / "stoney_verify/events.py").read_text(errors="ignore")
fallback_path = ROOT / "stoney_verify/commands_ext/public_member_lifecycle_logs.py"
fallback = fallback_path.read_text(errors="ignore") if fallback_path.exists() else ""

failures: list[str] = []

required_router = [
    "refresh=True",
    "JOIN_LEAVE_KEYS",
    '"join_leave_log_channel_id"',
    '"join_exit_log_channel_id"',
    "join log sent guild=",
    "leave log sent guild=",
    "embed_links",
    "read_message_history",
]

for marker in required_router:
    if marker not in router:
        failures.append(f"router missing marker: {marker}")

legacy_bad = [
    "if JOIN_LOG_CHANNEL_ID and int(JOIN_LOG_CHANNEL_ID) != 0:",
]

for marker in legacy_bad:
    if marker in events:
        failures.append(f"legacy events.py still uses global join log route: {marker}")

if "member_lifecycle_router_guard" not in events:
    failures.append("events.py does not document central join/leave router ownership")

if fallback_path.exists():
    for marker in ('"join_leave_log_channel_id"', '"join_exit_log_channel_id"', '"leave_log_channel_id"'):
        if marker not in fallback:
            failures.append(f"public lifecycle fallback missing alias: {marker}")

if failures:
    print("FAIL join/leave log centralization")
    for item in failures:
        print(" -", item)
    raise SystemExit(1)

print("PASS join/leave log centralization")
