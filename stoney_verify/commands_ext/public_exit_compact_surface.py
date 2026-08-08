from __future__ import annotations

"""Attach Exit Card compatibility entries, then install the final UI-first surface.

The Exit registrar still runs before the final compactor so existing import/order
contracts stay intact. DS-COMMAND-UX-024 then removes redundant Discord-visible
shortcuts while leaving the canonical Exit runtime, Studio implementation, and
cleanup implementation fully loaded and reachable from mega menus.
"""

from typing import Any

from discord import app_commands

from .public_setup_group import dank_group


def _command(name: str, description: str, callback: Any) -> app_commands.Command:
    resolved = getattr(callback, "callback", callback)
    if not callable(resolved):
        raise TypeError(f"{name} does not provide a callable command callback")
    return app_commands.Command(name=name, description=description, callback=resolved)


def _install_final_layers(bot: Any, tree: Any) -> dict[str, Any]:
    # register_extra_commands() may run the additive registrars again after the
    # initial import. Reassert the final command tree on every pass instead of
    # trusting a one-time installed flag; otherwise an old group/shortcut could
    # be rebuilt after the first compaction and survive until Discord sync.
    from . import public_command_surface_v2 as compact_surface
    from .public_cleanup_command_center import install_cleanup_menu_compat
    from .public_lifecycle_menu_compat import install_lifecycle_menu_compat

    compact_surface._INSTALLED = False
    result = compact_surface.install_compact_public_surface_v2(bot, tree)
    install_lifecycle_menu_compat()
    install_cleanup_menu_compat()
    return result


def register_compact_exit_card_commands(bot: Any, tree: Any) -> int:
    from .public_command_hub import DANK_PAYLOAD_SAFETY_LIMIT, dank_payload_size

    group = dank_group.get_command("welcome")
    if not isinstance(group, app_commands.Group):
        # register_extra_commands() can legitimately call this registrar again
        # after v2 has already removed /dank welcome. Reassert the compact tree
        # rather than resurrecting the retired subgroup.
        result = _install_final_layers(bot, tree)
        final_size = int(result.get("dank_payload", 0) or dank_payload_size(tree))
        print(
            "✅ public_exit_compact_surface final UI-first surface reasserted "
            f"payload={final_size}/{DANK_PAYLOAD_SAFETY_LIMIT}"
        )
        return final_size

    from .public_exit_card_studio import exit_card_upload
    from stoney_verify.exit_card_studio_ui import (
        open_exit_card_studio,
        send_exit_studio_preview,
    )

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

    precompact_size = dank_payload_size(tree)
    if precompact_size > DANK_PAYLOAD_SAFETY_LIMIT:
        for name in added:
            try:
                group.remove_command(name)
            except Exception:
                pass
        raise RuntimeError(
            f"Exit Card Studio entries grew /dank payload to {precompact_size}; "
            f"safety limit is {DANK_PAYLOAD_SAFETY_LIMIT}"
        )

    print(
        f"✅ public_exit_compact_surface loaded Exit Card compatibility entries "
        f"payload={precompact_size}/{DANK_PAYLOAD_SAFETY_LIMIT}"
    )

    # This is intentionally the final application-command-tree mutation. All
    # underlying modules remain loaded; only redundant autocomplete entry points
    # are removed/replaced by action-complete centers.
    result = _install_final_layers(bot, tree)
    final_size = int(result.get("dank_payload", precompact_size) or precompact_size)
    print(
        "✅ public_exit_compact_surface final UI-first surface ready "
        f"payload={final_size}/{DANK_PAYLOAD_SAFETY_LIMIT}"
    )
    return final_size


__all__ = ["register_compact_exit_card_commands"]
