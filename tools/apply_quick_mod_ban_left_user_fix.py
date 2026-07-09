from __future__ import annotations

"""Allow Quick Mod ban to work after a member leaves or is kicked.

Discord can create a guild ban from a user ID. The old QuickModView resolved the
target only as a Guild Member, so once the user left, Ban failed with
"Target member is no longer in this server." Kick/timeout still require a current
member, but Ban should fall back to banning a discord.Object snowflake.

Run from repo root:
    python tools/apply_quick_mod_ban_left_user_fix.py
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODLOG = ROOT / "stoney_verify/modlog.py"

OLD_RESOLVE = '''    async def _resolve_target(
        self,
        interaction: discord.Interaction,
    ) -> Tuple[Optional[discord.Member], Optional[str]]:
        try:
            if interaction.guild is None:
                return None, "Guild context missing."

            target = interaction.guild.get_member(self.target_user_id)
            if target is None:
                try:
                    target = await interaction.guild.fetch_member(self.target_user_id)
                except Exception:
                    target = None

            if target is None:
                return None, "Target member is no longer in this server."

            return target, None
        except Exception:
            return None, "Failed to resolve target member."

'''

NEW_RESOLVE = '''    async def _resolve_target(
        self,
        interaction: discord.Interaction,
    ) -> Tuple[Optional[discord.Member], Optional[str]]:
        try:
            if interaction.guild is None:
                return None, "Guild context missing."

            target = interaction.guild.get_member(self.target_user_id)
            if target is None:
                try:
                    target = await interaction.guild.fetch_member(self.target_user_id)
                except Exception:
                    target = None

            if target is None:
                return None, "Target member is no longer in this server. Use Ban to ban by user ID."

            return target, None
        except Exception:
            return None, "Failed to resolve target member."

    def _ban_object(self) -> discord.Object:
        return discord.Object(id=int(self.target_user_id))

    def _target_mention(self) -> str:
        return f"<@{int(self.target_user_id)}>"

    def _bot_can_ban_by_id(self, guild: discord.Guild) -> Tuple[bool, str]:
        try:
            me = _bot_member_for_guild(guild)
            if not isinstance(me, discord.Member):
                return (False, "Bot member could not be resolved.")
            if not _moderator_has_permission(me, "ban_members") and not _moderator_has_permission(me, "administrator"):
                return (False, "Bot needs **Ban Members** to ban a user who already left.")
            if int(self.target_user_id) == int(guild.owner_id or 0):
                return (False, "Bot cannot ban the server owner.")
            return (True, "")
        except Exception:
            return (False, "Failed to verify bot ban permission.")

'''

OLD_BAN = '''    @discord.ui.button(label="Ban", style=discord.ButtonStyle.danger, emoji="🔨")
    async def ban_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        moderator, error = await self._ensure_mod(interaction, perm_name="ban_members")
        if error or moderator is None:
            await self._deny(interaction, error or "Permission denied.")
            return

        target, error = await self._resolve_target(interaction)
        if error or target is None:
            await self._deny(interaction, error or "Target not found.")
            return

        ok, reason_text = _can_act_on_member(moderator, target)
        if not ok:
            await self._deny(interaction, reason_text)
            return

        ok, reason_text = _bot_can_act_on_member(interaction.guild, target)  # type: ignore[arg-type]
        if not ok:
            await self._deny(interaction, reason_text)
            return

        reason = _quick_mod_default_reason("ban", moderator)

        try:
            await target.ban(reason=reason, delete_message_days=0)
            await self._ok(interaction, f"🔨 Banned {target.mention}")
        except Exception as e:
            await self._deny(interaction, f"Ban failed: {e}")

'''

NEW_BAN = '''    @discord.ui.button(label="Ban", style=discord.ButtonStyle.danger, emoji="🔨")
    async def ban_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        moderator, error = await self._ensure_mod(interaction, perm_name="ban_members")
        if error or moderator is None:
            await self._deny(interaction, error or "Permission denied.")
            return
        if interaction.guild is None:
            await self._deny(interaction, "Guild context missing.")
            return
        if int(self.target_user_id) == int(getattr(moderator, "id", 0) or 0):
            await self._deny(interaction, "You cannot ban yourself.")
            return

        target, _resolve_error = await self._resolve_target(interaction)
        reason = _quick_mod_default_reason("ban", moderator)

        if isinstance(target, discord.Member):
            ok, reason_text = _can_act_on_member(moderator, target)
            if not ok:
                await self._deny(interaction, reason_text)
                return

            ok, reason_text = _bot_can_act_on_member(interaction.guild, target)  # type: ignore[arg-type]
            if not ok:
                await self._deny(interaction, reason_text)
                return

            try:
                await target.ban(reason=reason, delete_message_days=0)
                await self._ok(interaction, f"🔨 Banned {target.mention}")
            except Exception as e:
                await self._deny(interaction, f"Ban failed: {e}")
            return

        ok, reason_text = self._bot_can_ban_by_id(interaction.guild)
        if not ok:
            await self._deny(interaction, reason_text)
            return

        try:
            await interaction.guild.ban(self._ban_object(), reason=reason, delete_message_days=0)
            await self._ok(interaction, f"🔨 Banned {self._target_mention()} by user ID. They had already left or been kicked.")
        except discord.NotFound:
            await self._deny(interaction, "Ban failed: Discord could not find that user ID.")
        except discord.Forbidden:
            await self._deny(interaction, "Ban failed: I need **Ban Members**, and my role/permissions must allow this action.")
        except Exception as e:
            await self._deny(interaction, f"Ban failed: {e}")

'''


def replace_required(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        print(f"✅ patched {label}")
        return text.replace(old, new)
    if new in text:
        print(f"✅ already patched {label}")
        return text
    raise SystemExit(f"Could not find target block for {label}")


def main() -> None:
    text = MODLOG.read_text(encoding="utf-8")
    text = replace_required(text, OLD_RESOLVE, NEW_RESOLVE, "QuickMod target resolver")
    text = replace_required(text, OLD_BAN, NEW_BAN, "QuickMod ban fallback")

    required = (
        "Use Ban to ban by user ID",
        "discord.Object(id=int(self.target_user_id))",
        "await interaction.guild.ban(self._ban_object()",
        "They had already left or been kicked",
        "Bot needs **Ban Members** to ban a user who already left",
    )
    missing = [token for token in required if token not in text]
    if missing:
        raise SystemExit("Quick Mod ban-left-user fix missing tokens: " + ", ".join(missing))

    MODLOG.write_text(text, encoding="utf-8")
    print("✅ Quick Mod Ban now works for users who already left/kicked")


if __name__ == "__main__":
    main()
