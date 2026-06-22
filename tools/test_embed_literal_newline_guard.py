from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import discord
from stoney_verify.startup_guards.embed_literal_newline_guard import _clean_text

bad = []

cases = {
    "A\\nB": "A\nB",
    "A\\r\\nB": "A\nB",
    "A/nB": "A\nB",
}

for raw, expected in cases.items():
    actual = _clean_text(raw)
    if actual != expected:
        bad.append(f"_clean_text {raw!r} -> {actual!r}, expected {expected!r}")

embed = discord.Embed(title="A\\nB", description="C/nD")
embed.add_field(name="E\\nF", value="G\\nH", inline=False)

data = embed.to_dict()

if "\\n" in data.get("title", ""):
    bad.append("Embed title still contains literal backslash-n")
if "/n" in data.get("description", ""):
    bad.append("Embed description still contains slash-n")

for field in data.get("fields", []):
    if "\\n" in field.get("name", "") or "\\n" in field.get("value", ""):
        bad.append("Embed field still contains literal backslash-n")

if bad:
    print("FAIL embed literal newline guard")
    for item in bad:
        print(" -", item)
    raise SystemExit(1)

print("PASS embed literal newline guard")
