from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stoney_verify.setup_doctor_canonical import normalize_setup_health, truth_rules_text

cfg = {
    "ticket_category_id": 123,
    "ticket_panel_channel_id": 456,
    "verify_channel_id": 789,
    "unverified_role_id": 111,
    "verified_role_id": 222,
    "vc_verify_enabled": False,
}

result = normalize_setup_health(
    cfg=cfg,
    blockers=[
        "Server-control role is missing: 999.",
        "Public ticket panel and verify channel are split across categories.",
        "VC verify voice channel is in the wrong category.",
        "Bot is missing required setup permissions: Manage Channels.",
    ],
    warnings=[],
    ok=[],
)

bad = []

if "Bot is missing required setup permissions: Manage Channels." not in result.blockers:
    bad.append("real required permission blocker was lost")

if any("Server-control role" in item for item in result.blockers):
    bad.append("server-control optional role remained a blocker")

if any("split across categories" in item for item in result.blockers):
    bad.append("layout issue remained a blocker")

if any("VC verify" in item for item in result.blockers):
    bad.append("disabled VC Verify issue remained a blocker")

if not any("Optional setup control" in item for item in result.warnings):
    bad.append("server-control optional warning missing")

if "Blocker" not in truth_rules_text():
    bad.append("truth rules text missing")

if bad:
    print("FAIL canonical setup doctor")
    for item in bad:
        print(" -", item)
    raise SystemExit(1)

print("PASS canonical setup doctor")
