from __future__ import annotations

from typing import Any

import discord

_PATCHED = False
_IMPORT_CUSTOM_IDS = {"dank_protection:import_pack", "dank_protection:manual_import_pack"}


async def _open_modal(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.commands_ext.public_setup_group import _require_setup_permission
        from stoney_verify.startup_guards.protection_pack_manual_import_guard import StarterPackImportModal
    except Exception as exc:
        return await interaction.response.send_message(f"Import unavailable: {type(exc).__name__}: {exc}", ephemeral=True)
    if not await _require_setup_permission(interaction):
        return
    await interaction.response.send_modal(StarterPackImportModal())


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.commands_ext.public_protection_center import ProtectionCenterView

        if getattr(ProtectionCenterView, "_import_button_init_patched", False):
            _PATCHED = True
            return True

        original_init = ProtectionCenterView.__init__

        def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
            original_init(self, *args, **kwargs)
            try:
                for item in list(getattr(self, "children", []) or []):
                    if getattr(item, "custom_id", "") in _IMPORT_CUSTOM_IDS:
                        return
                button = discord.ui.Button(
                    label="Import Pack",
                    emoji="🌐",
                    style=discord.ButtonStyle.secondary,
                    custom_id="dank_protection:manual_import_pack",
                    row=2,
                )
                button.callback = _open_modal
                self.add_item(button)
            except Exception as exc:
                try:
                    print(f"⚠️ protection_import_button_patch failed to add button: {type(exc).__name__}: {exc}")
                except Exception:
                    pass

        ProtectionCenterView.__init__ = patched_init
        ProtectionCenterView._import_button_init_patched = True
        _PATCHED = True
        print("✅ protection_import_button_patch active; Import Pack fallback button available when needed")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ protection_import_button_patch failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
