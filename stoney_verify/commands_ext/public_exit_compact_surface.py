from __future__ import annotations

"""Attach canonical Exit Card entries to the final compact /dank welcome group."""

from typing import Any

from discord import app_commands

from .public_setup_group import dank_group


def _command(name: str, description: str, callback: Any) -> app_commands.Command:
    resolved = getattr(callback, "callback", callback)
    if not callable(resolved):
        raise TypeError(f"{name} does not provide a callable command callback")
    return app_commands.Command(name=name, description=description, callback=resolved)


def register_compact_exit_card_commands(bot: Any, tree: Any) -> int:
    _ = bot
    from .public_command_hub import DANK_PAYLOAD_SAFETY_LIMIT, dank_payload_size
    from .public_exit_card_studio import exit_card_upload
    from stoney_verify.exit_card_studio_ui import (
        open_exit_card_studio,
        send_exit_studio_preview,
    )

    group = dank_group.get_command("welcome")
    if not isinstance(group, app_commands.Group):
        raise RuntimeError("Compact /dank welcome group is unavailable for Exit Card Studio")

    existing = {str(getattr(item, "name", "")) for item in group.commands}
    entries = (
        (
            "exit-card-studio",
            "Open the complete live Exit Card Studio.",
            open_exit_card_studio,
        ),
        (
            "exit-card-preview",
            "Preview the exact current production exit-card design.",
            send_exit_studio_preview,
        ),
        (
            "exit-card-upload",
            "Upload custom exit-card background artwork.",
            exit_card_upload,
        ),
    )
    added: list[str] = []
    for name, description, callback in entries:
        if name in existing:
            continue
        group.add_command(_command(name, description, callback))
        added.append(name)

    size = dank_payload_size(tree)
    if size > DANK_PAYLOAD_SAFETY_LIMIT:
        for name in added:
            try:
                group.remove_command(name)
            except Exception:
                pass
        raise RuntimeError(
            f"Exit Card Studio entries grew /dank payload to {size}; "
            f"safety limit is {DANK_PAYLOAD_SAFETY_LIMIT}"
        )
    print(
        f"✅ public_exit_compact_surface attached Exit Card Studio entries "
        f"payload={size}/{DANK_PAYLOAD_SAFETY_LIMIT}"
    )
    return size


__all__ = ["register_compact_exit_card_commands"]
