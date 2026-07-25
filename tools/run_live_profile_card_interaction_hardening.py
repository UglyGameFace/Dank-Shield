from pathlib import Path
import runpy


ROOT = Path(__file__).resolve().parents[1]
PATCHER = ROOT / "tools" / "apply_live_profile_card_interaction_hardening.py"
source = PATCHER.read_text(encoding="utf-8")
marker = "# Manager commands defer before database writes and cleanup work."
section = source.index(marker)
first_call = source.index("replace_once(", section)
start = source.index("replace_once(", first_call + 1)
end = source.index("\nreplace_once(", start + 1)

replacement = '''replace_once(
    "stoney_verify/commands_ext/public_profile_cards.py",
    \'\'\'    runtime = getattr(getattr(interaction, "client", None), _RUNTIME_ATTRIBUTE, None)\n    if not enabled and isinstance(runtime, LiveProfileCardRuntime):\n        await runtime.disable_channel(guild, channel)\n\n    await interaction.response.send_message(\n        embed=_live_status_embed(guild, updated),\n        ephemeral=True,\n        allowed_mentions=discord.AllowedMentions.none(),\n    )\n\'\'\',
    \'\'\'    runtime = getattr(getattr(interaction, "client", None), _RUNTIME_ATTRIBUTE, None)\n    if not enabled and isinstance(runtime, LiveProfileCardRuntime):\n        await runtime.disable_channel(guild, channel)\n\n    await _send_private(interaction, embed=_live_status_embed(guild, updated))\n\'\'\',
)'''

PATCHER.write_text(source[:start] + replacement + source[end:], encoding="utf-8")
runpy.run_path(str(PATCHER), run_name="__main__")
