from __future__ import annotations

"""Keep ID Verify and Voice Verify setup service toggles independent.

Bug fixed: disabling ID Verify used to force-disable Voice Verify. These are
separate setup services, so a server may use voice verification without ID/photo
verification.
"""

from typing import Any

_PATCHED = False
_ORIGINAL_CALLBACK: Any = None


def _log(message: str) -> None:
    try:
        print(f"🧭 setup_verification_toggle_independence_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ setup_verification_toggle_independence_guard {message}")
    except Exception:
        pass


def apply() -> bool:
    global _PATCHED, _ORIGINAL_CALLBACK
    if _PATCHED:
        return True
    try:
        from stoney_verify.startup_guards import setup_service_modes as modes

        cls = getattr(modes, "ServiceToggleButton", None)
        original = getattr(cls, "callback", None) if cls is not None else None
        if not callable(original) or getattr(original, "_verification_toggles_independent", False):
            return False

        async def callback(self: Any, interaction: Any) -> None:
            guild = getattr(interaction, "guild", None)
            if guild is None:
                return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
            state = await modes.load_service_state(guild.id)
            payload = state.as_payload()
            payload[self.key] = not bool(payload.get(self.key, False))
            if self.key == "spam_guard_enabled" and payload[self.key]:
                payload["moderation_enabled"] = True
            # ID Verify and Voice Verify intentionally stay independent.
            # Turning off ID/photo verification must not disable voice verification.
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception:
                pass
            await modes._save_service_state(guild.id, payload, interaction.user)
            next_state = await modes.load_service_state(guild.id)
            embed = await modes.build_service_picker_embed(
                guild,
                next_state,
                saved_message=f"Updated selected service: **{self.short_label}**.",
            )
            await interaction.edit_original_response(embed=embed, view=modes.ServiceModeView(next_state))

        setattr(callback, "_verification_toggles_independent", True)
        setattr(callback, "_original_callback", original)
        _ORIGINAL_CALLBACK = original
        cls.callback = callback
        _PATCHED = True
        _log("active; ID Verify and Voice Verify service toggles are independent")
        return True
    except Exception as exc:
        _warn(f"failed: {exc!r}")
        return False


apply()

__all__ = ["apply"]
