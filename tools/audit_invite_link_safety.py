from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

ROOT = REPO_ROOT / "stoney_verify"

failures: list[str] = []

def text(path: str) -> str:
    return Path(path).read_text(errors="ignore")

# Central extraction must not false-positive normal URLs containing discord.gg in a path/query.
from stoney_verify.invite_policy_engine import extract_invite_codes_from_text

false_positive_samples = [
    "https://google.com",
    "https://discord.com",
    "https://example.com/redirect/discord.gg/testcode",
    "https://example.com/?next=discord.gg/testcode",
    "https://github.com/UglyGameFace/Dank-Shield",
]

for sample in false_positive_samples:
    found = extract_invite_codes_from_text(sample)
    if found:
        failures.append(f"central extractor false-positive {sample!r} -> {found}")

# Protection invite cleanup must not scan component labels/custom IDs.
pic = text("stoney_verify/startup_guards/protection_invite_toggle_cleanup_guard.py")
if '("url", "label", "custom_id")' in pic:
    failures.append("protection_invite_toggle_cleanup_guard scans component label/custom_id")
if "await message.delete(reason=" in pic or "await message.delete()" in pic:
    failures.append("protection_invite_toggle_cleanup_guard still has direct message.delete")
if "policy.scan_channel_invites" not in pic:
    failures.append("protection_invite_toggle_cleanup_guard scan does not call central policy.scan_channel_invites")

# Spam cleanup may delete normal spam bursts, but invite-containing messages must go through central policy.
psc = text("stoney_verify/commands_ext/public_spam_cleanup_hardening.py")
if "policy.extract_invite_codes_from_message(message)" not in psc:
    failures.append("public_spam_cleanup_hardening does not detect invite messages before delete")
if "policy.delete_message_if_allowed(message, decision)" not in psc:
    failures.append("public_spam_cleanup_hardening does not delegate invite deletes to central policy")

# Live invite paths should use central decision/delete helpers.
live = text("stoney_verify/startup_guards/invite_live_enforcer_guard.py")
if "decide_invite_message" not in live or "delete_message_if_allowed" not in live:
    failures.append("invite_live_enforcer_guard is not using central decision/delete helpers")

runtime = text("stoney_verify/startup_guards/discord_invite_blocker_runtime_guard.py")
if "policy.decide_invite_message" not in runtime or "policy.delete_message_if_allowed" not in runtime:
    failures.append("discord_invite_blocker_runtime_guard is not using central decision/delete helpers")

print("=== Invite Link Safety Audit ===")
if failures:
    for item in failures:
        print("FAIL:", item)
    raise SystemExit(1)

print("PASS: invite extraction and invite-delete paths are centralized/safe.")
