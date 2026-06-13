#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import py_compile
import sys

ROOT = Path(__file__).resolve().parents[1]

FILES = [
    "stoney_verify/startup_guards/ticket_panel_doctor_command.py",
    "stoney_verify/startup_guards/ticket_panel_doctor_production_wording.py",
    "stoney_verify/startup_guards/ticket_panel_doctor_stability_guard.py",
    "stoney_verify/startup_guards/ticket_panel_repair_records_command.py",
    "stoney_verify/startup_guards/ticket_panel_command_epoch_guard.py",
    "stoney_verify/startup_guards/ticket_staff_identity_guard.py",
    "stoney_verify/startup_guards/voice_ticket_claim_notice_guard.py",
    "stoney_verify/startup_guards/ticket_open_controls_status_guard.py",
    "stoney_verify/startup_guards/ticket_action_controls_refresh_guard.py",
    "stoney_verify/startup_guards/setup_feature_health_scoreboard.py",
    "stoney_verify/commands_ext/public_ticket_panel_clean.py",
]

CHECKS = {
    "stoney_verify/startup_guards/ticket_panel_doctor_command.py": ["_doctor_command", "group.get_command"],
    "stoney_verify/startup_guards/ticket_panel_doctor_production_wording.py": [
        "ticket_panel_command_epoch_guard",
        "ticket_staff_identity_guard",
        "voice_ticket_claim_notice_guard",
        "ticket_open_controls_status_guard",
        "ticket_action_controls_refresh_guard",
        "ticket_panel_repair_records_command",
        "ticket_panel_doctor_stability_guard",
        "setup_check_existing_server_inference_guard",
    ],
    "stoney_verify/startup_guards/ticket_action_controls_refresh_guard.py": [
        "_action_claim",
        "_action_unclaim",
        "TransferTicketModal",
        "SetPriorityModal",
        "post_or_replace_open_ticket_controls",
        "_TICKET_ACTION_CONTROLS_REFRESH_GUARD_APPLIED",
    ],
    "stoney_verify/startup_guards/voice_ticket_claim_notice_guard.py": [
        "voice verification approval",
        "Ticket claimed by",
        "display_name",
        "_VOICE_TICKET_CLAIM_NOTICE_GUARD_APPLIED",
    ],
    "stoney_verify/startup_guards/ticket_open_controls_status_guard.py": [
        "Claimed By",
        "_claimed_member",
        "display_avatar",
        "staff status refreshes after ticket actions",
        "_TICKET_OPEN_CONTROLS_STATUS_GUARD_APPLIED",
    ],
    "stoney_verify/startup_guards/ticket_staff_identity_guard.py": [
        "_display_name",
        "_avatar_url",
        "claimed_by_display_name",
        "claimed_by_avatar_url",
        "claim_identity_source",
        "_TICKET_STAFF_IDENTITY_SERVICE_PATCHED",
        "_TICKET_STAFF_IDENTITY_REPO_PATCHED",
    ],
    "stoney_verify/startup_guards/ticket_panel_doctor_stability_guard.py": [
        "_stable_doctor_command",
        "_stable_health_lines",
        "_stable_setup_ticket_score",
        "_saved_panel_message_status",
        "optional and not set",
        "Ticket creation is not blocked",
        "Ticket creation essentials are usable; optional cleanup remains.",
        "_TICKET_PANEL_HEALTH_FALSE_POSITIVE_GUARD",
        "_TICKET_SETUP_SCORE_FALSE_POSITIVE_GUARD",
        "_TICKET_PANEL_DOCTOR_STABILITY_GUARD",
    ],
    "stoney_verify/startup_guards/ticket_panel_repair_records_command.py": [
        "repair-records",
        "apply_changes",
        "never deletes ticket rows",
        "Changed records were marked closed",
        "_TICKET_PANEL_REPAIR_RECORDS_COMMAND_APPLIED",
    ],
    "stoney_verify/startup_guards/ticket_panel_command_epoch_guard.py": [
        "ticket-panel-health-doctor-repair-v1",
        "COMMAND_CLEANUP_EPOCH",
        "command outdated",
    ],
    "stoney_verify/startup_guards/setup_feature_health_scoreboard.py": ["_ticket_score", "Feature Health Scoreboard"],
    "stoney_verify/commands_ext/public_ticket_panel_clean.py": ["_health_lines", "_ticket_panel_group", "ticket_panel_message_id"],
}


def main() -> int:
    for path in FILES:
        target = ROOT / path
        if not target.exists():
            print(f"missing {path}", file=sys.stderr)
            return 1
        try:
            py_compile.compile(str(target), doraise=True)
        except py_compile.PyCompileError as exc:
            print(f"compile failed {path}: {exc}", file=sys.stderr)
            return 1
    for path, snippets in CHECKS.items():
        text = (ROOT / path).read_text(encoding="utf-8")
        for snippet in snippets:
            if snippet not in text:
                print(f"{path} missing {snippet}", file=sys.stderr)
                return 1
    print("Ticket panel doctor audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
