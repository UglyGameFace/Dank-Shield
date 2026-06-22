from __future__ import annotations

from pathlib import Path

fresh = Path("stoney_verify/commands_ext/public_setup_fresh_choice.py").read_text(encoding="utf-8", errors="ignore")
recommend = Path("stoney_verify/commands_ext/public_setup_recommend.py").read_text(encoding="utf-8", errors="ignore")

failures: list[str] = []

# Custom setup must save and display a real custom mix, not a stale preset.
for marker in (
    "Custom mix:",
    "__custom_current__",
    "_custom_service_config_patch",
    '"verification_requires_id": False',
    '"setup_choice_label": _custom_mix_label',
    "Tickets: **",
    "Voice Verify: **",
    "Basic Verify: **",
):
    if marker not in fresh:
        failures.append(f"custom setup missing marker: {marker}")

# Custom must not imply ID/Voice just because setup_choice is custom.
bad_recommend_markers = (
    'style in {"voice_check", "id_voice_check", "custom"}',
    'style in {"id_check", "id_voice_check", "custom"}',
)

for marker in bad_recommend_markers:
    if marker in recommend:
        failures.append(f"old custom-implies-advanced logic still present: {marker}")

# Launch must have real actions, not only a checklist.
for marker in (
    "Post Ticket Panel",
    "Post Basic Verify Panel",
    "post_ticket_panel_callback",
    "verify_panel(interaction)",
    "No ID/Voice flow appears unless those switches are ON",
    "Tickets are OFF in Custom Setup",
    "Basic Verify is OFF in Custom Setup",
):
    if marker not in recommend:
        failures.append(f"launch missing marker: {marker}")

if failures:
    print("FAIL custom state and launch actions")
    for item in failures:
        print(" -", item)
    raise SystemExit(1)

print("PASS custom state and launch actions")
