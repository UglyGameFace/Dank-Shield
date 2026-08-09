from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stoney_verify.globals import bot
import stoney_verify.commands  # noqa: F401
from stoney_verify.commands_ext.public_command_hub import (
    DANK_PAYLOAD_SAFETY_LIMIT,
    dank_payload_size,
)
from stoney_verify.commands_ext.public_setup_group import dank_group

size = dank_payload_size(bot.tree)
children = sorted(str(getattr(command, "name", "")) for command in dank_group.commands)
roots = sorted(
    str(getattr(command, "name", ""))
    for command in bot.tree.get_commands(guild=None)
    if str(getattr(command, "name", "")) != "View Dank Profile"
)
print(
    json.dumps(
        {
            "dank_payload_size": size,
            "limit": DANK_PAYLOAD_SAFETY_LIMIT,
            "children": children,
            "roots": roots,
        }
    )
)
if size > DANK_PAYLOAD_SAFETY_LIMIT:
    raise SystemExit(f"/dank payload is too large: {size}/{DANK_PAYLOAD_SAFETY_LIMIT}")
if children != ["home", "purge", "upload"]:
    raise SystemExit(f"unexpected final /dank children: {children}")
if roots != ["dank", "mod", "ticket", "tickets", "verify"]:
    raise SystemExit(f"unexpected compact-v2 global roots: {roots}")
