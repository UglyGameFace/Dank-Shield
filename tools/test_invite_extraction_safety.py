from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stoney_verify.invite_policy_engine import extract_invite_codes_from_text

SHOULD_NOT_MATCH = [
    "https://google.com",
    "https://discord.com",
    "https://example.com/redirect/discord.gg/testcode",
    "https://example.com/?next=discord.gg/testcode",
    "https://github.com/UglyGameFace/Dank-Shield",
    "normal link with the word discord but no invite",
]

SHOULD_MATCH = [
    "discord.gg/testcode",
    "https://discord.gg/testcode",
    "https://discord.com/invite/testcode",
    "discord . gg/testcode",
    "discord.com / invite / testcode",
]

bad = []

for text in SHOULD_NOT_MATCH:
    found = extract_invite_codes_from_text(text)
    if found:
        bad.append(f"false positive: {text!r} -> {found}")

for text in SHOULD_MATCH:
    found = extract_invite_codes_from_text(text)
    if not found:
        bad.append(f"missed invite: {text!r}")

if bad:
    print("FAIL invite extraction safety")
    for item in bad:
        print(item)
    raise SystemExit(1)

print("PASS invite extraction safety")
