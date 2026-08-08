from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
router = (ROOT / "stoney_verify/startup_guards/member_lifecycle_router_guard.py").read_text(errors="ignore")
join_runtime = (ROOT / "stoney_verify/welcome_card_runtime.py").read_text(errors="ignore")
exit_runtime = (ROOT / "stoney_verify/exit_card_runtime.py").read_text(errors="ignore")
events = (ROOT / "stoney_verify/events.py").read_text(errors="ignore")
fallback_path = ROOT / "stoney_verify/commands_ext/public_member_lifecycle_logs.py"
fallback = fallback_path.read_text(errors="ignore") if fallback_path.exists() else ""

failures: list[str] = []

required_router = [
    "refresh=True",
    "JOIN_LEAVE_KEYS",
    "STAFF_AUDIT_KEYS",
    '"join_leave_log_channel_id"',
    '"join_exit_log_channel_id"',
    "resolve_join_card_channel",
    "resolve_exit_card_channel",
    "send_live_welcome_card",
    "send_live_exit_card",
    "canonical join result guild=",
    "canonical exit result guild=",
    "staff audit remains a separate route",
]

for marker in required_router:
    if marker not in router:
        failures.append(f"router missing marker: {marker}")

required_join_runtime = [
    'embed.set_footer(text="dank_shield:welcome_card_runtime:v1")',
    "embed_links",
    "read_message_history",
    "duplicate_suppressed",
]
for marker in required_join_runtime:
    if marker not in join_runtime:
        failures.append(f"join runtime missing marker: {marker}")

required_exit_runtime = [
    'embed.set_footer(text="dank_shield:exit_card_runtime:v1")',
    "embed_links",
    "read_message_history",
    "duplicate_suppressed",
    "AllowedMentions.none()",
]
for marker in required_exit_runtime:
    if marker not in exit_runtime:
        failures.append(f"exit runtime missing marker: {marker}")

retired_public_sender_markers = [
    "join log sent guild=",
    "leave log sent guild=",
    "_send_join_leave_join",
    "_send_public_leave",
    'set_footer(text="dank_shield:join_leave_event:v3")',
    'set_footer(text="dank_shield:leave_event:v4")',
]
for marker in retired_public_sender_markers:
    if marker in router:
        failures.append(f"router still contains retired public lifecycle sender marker: {marker}")

legacy_bad_events = [
    "if JOIN_LOG_CHANNEL_ID and int(JOIN_LOG_CHANNEL_ID) != 0:",
]
for marker in legacy_bad_events:
    if marker in events:
        failures.append(f"legacy events.py still uses global join log route: {marker}")

if "member_lifecycle_router_guard" not in events:
    failures.append("events.py does not document central join/leave router ownership")

if fallback_path.exists():
    for marker in (
        '"join_leave_log_channel_id"',
        '"join_exit_log_channel_id"',
        '"leave_log_channel_id"',
    ):
        if marker not in fallback:
            failures.append(f"public lifecycle fallback missing alias: {marker}")

if failures:
    print("FAIL join/leave log centralization")
    for item in failures:
        print(" -", item)
    raise SystemExit(1)

print("PASS join/leave log centralization")
