from __future__ import annotations

import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DANK_COMMAND_PROFILE", "public")
os.environ.setdefault("DANK_DEPLOYMENT_MODE", "production")
os.environ.setdefault("DANK_STARTUP_LOG_STYLE", "quiet")
os.environ.setdefault("DANK_COMMAND_LOG_STYLE", "quiet")
os.environ.setdefault("BOT_API_SHARED_SECRET", "command-size-audit-only-secret-1234567890")

from stoney_verify import commands as _commands  # noqa: F401,E402
from stoney_verify.globals import bot  # noqa: E402

LIMIT = 8000
COUNTED_KEYS = {"name", "description", "value"}


def command_character_count(value: Any) -> int:
    if isinstance(value, dict):
        total = 0
        for key, child in value.items():
            if key in COUNTED_KEYS and isinstance(child, str):
                total += len(child)
            elif key.endswith("_localizations") and isinstance(child, dict):
                total += sum(len(str(text)) for text in child.values())
            else:
                total += command_character_count(child)
        return total
    if isinstance(value, list):
        return sum(command_character_count(child) for child in value)
    return 0


def option_path_size(option: dict[str, Any], prefix: str) -> list[tuple[str, int]]:
    name = str(option.get("name") or "?")
    path = f"{prefix} {name}".strip()
    rows = [(path, command_character_count(option))]
    for child in option.get("options") or []:
        if isinstance(child, dict):
            rows.extend(option_path_size(child, path))
    return rows


def print_source(label: str, value: Any) -> None:
    try:
        print(f"\n===== SOURCE {label} =====")
        print(inspect.getsource(value))
    except Exception as exc:
        print(f"SOURCE_ERROR {label}: {type(exc).__name__}: {exc}")


def main() -> int:
    commands = bot.tree.get_commands()
    print(f"top_level_commands={len(commands)}")
    failed = False
    for command in commands:
        payload = command.to_dict(bot.tree)
        count = command_character_count(payload)
        json_bytes = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        command_name = str(payload.get("name") or "?")
        print(
            f"COMMAND /{command_name} chars={count}/{LIMIT} "
            f"remaining={LIMIT - count} json_bytes={json_bytes}"
        )
        if count > LIMIT:
            failed = True
        rows: list[tuple[str, int]] = []
        for option in payload.get("options") or []:
            if isinstance(option, dict):
                rows.extend(option_path_size(option, f"/{command_name}"))
        for path, size in sorted(rows, key=lambda item: item[1], reverse=True)[:20]:
            print(f"  {size:4d}  {path}")

    from stoney_verify.commands_ext import public_setup_recommend as setup
    from stoney_verify.commands_ext import public_profile_cards_core as profile
    from stoney_verify.commands_ext import public_welcome_card_studio as welcome_studio

    for label, value in (
        ("AdvancedSettingsHubView", setup.AdvancedSettingsHubView),
        ("AdvancedMemberExperienceView", setup.AdvancedMemberExperienceView),
        ("AdvancedAppearanceView", setup.AdvancedAppearanceView),
        ("ProfileSettingsView", profile.ProfileSettingsView),
        ("profile_settings", profile.profile_settings),
        ("welcome_studio_entry", welcome_studio.welcome_card_style),
    ):
        print_source(label, value)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
