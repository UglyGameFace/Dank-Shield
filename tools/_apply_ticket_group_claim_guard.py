from __future__ import annotations

from pathlib import Path


root = Path(__file__).resolve().parents[1]
path = root / "stoney_verify/commands_ext/public_ticket_group.py"
text = path.read_text(encoding="utf-8")

old_import = '''from . import ticket_admin as legacy
from . import ticket_channel_admin as channel_legacy
'''
new_import = '''from . import ticket_admin as legacy
from . import ticket_channel_admin as channel_legacy
from ..tickets_new.service import authorize_ticket_action
'''
if text.count(old_import) != 1:
    raise SystemExit("public_ticket_group imports marker not found exactly once")
text = text.replace(old_import, new_import, 1)

old_group = '''ticket_group = app_commands.Group(
    name="ticket",
    description="Ticket actions and staff tools.",
)
'''
new_group = '''class ClaimFirstTicketGroup(app_commands.Group):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Claim-gate every canonical /ticket action before its callback runs."""
        if not _staff_check(interaction):
            # Preserve the command's existing Staff only response for non-staff.
            return True

        command = getattr(interaction, "command", None)
        command_name = str(getattr(command, "name", "") or "").strip().lower()
        if command_name == "claim":
            return True

        namespace = getattr(interaction, "namespace", None)
        selected_channel = getattr(namespace, "channel", None) if namespace is not None else None
        channel = selected_channel if isinstance(selected_channel, discord.TextChannel) else interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return True

        try:
            row = await legacy._refresh_ticket_row(channel)
        except Exception:
            row = None
        if not isinstance(row, dict):
            # Let the command's normal context validation explain non-ticket channels.
            return True

        action = {
            "info": "view_info",
            "owner": "view_info",
            "access": "view_info",
            "add": "access",
            "remove": "access",
            "rename": "rename",
            "lock": "lock",
            "unlock": "unlock",
        }.get(command_name, command_name or "interaction")

        try:
            decision = await authorize_ticket_action(
                channel_id=channel.id,
                actor=interaction.user,
                action=action,
                row=row,
            )
        except Exception as exc:
            message = (
                "❌ Ticket authorization is temporarily unavailable. "
                "Nothing was changed."
            )
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(message, ephemeral=True)
                else:
                    await interaction.response.send_message(message, ephemeral=True)
            except Exception:
                pass
            print(
                f"❌ ticket_claim_policy command authorization failed "
                f"guild={getattr(interaction, 'guild_id', None)} "
                f"channel={channel.id} command={command_name} "
                f"error={type(exc).__name__}"
            )
            return False

        if decision.allowed:
            return True

        try:
            message = f"❌ {decision.message}"
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except Exception:
            pass
        return False


ticket_group = ClaimFirstTicketGroup(
    name="ticket",
    description="Ticket actions and staff tools.",
)
'''
if text.count(old_group) != 1:
    raise SystemExit("public_ticket_group declaration marker not found exactly once")
text = text.replace(old_group, new_group, 1)

path.write_text(text, encoding="utf-8")
print("Applied group-wide claim-first guard to canonical /ticket commands.")
