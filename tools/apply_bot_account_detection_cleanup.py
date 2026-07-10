from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
RAID = ROOT / "stoney_verify/raidguard.py"
CLEANUP = ROOT / "stoney_verify/commands_ext/public_cleanup_group.py"
TEST = ROOT / "tools/test_bot_account_detection_cleanup_static.py"


def patch_raidguard() -> None:
    text = RAID.read_text(encoding="utf-8")

    text = text.replace(
        'return "BOT ACCOUNT • excluded from raid/alt scoring"',
        'return "Official Bot: Yes\\nAlt/Raid Risk: excluded from human raid/alt scoring\\nDM Raider Report Risk: separate report flow"',
    )

    old = '''    parts: List[str] = [
        f"{tier} ({level} / {score}/100)",
        f"Account age: {age_human}",
    ]'''
    new = '''    parts: List[str] = [
        "Official Bot: No",
        f"Alt/Raid Risk: {tier} ({level} / {score}/100)",
        f"Account age: {age_human}",
        "DM Raider Report Risk: separate user-report evidence required",
    ]'''

    if old in text:
        text = text.replace(old, new)
    elif "Alt/Raid Risk:" in text and "Official Bot: No" in text:
        pass
    else:
        raise SystemExit("Could not patch build_alt_detection_summary wording")

    if "BOT ACCOUNT • excluded from raid/alt scoring" in text:
        raise SystemExit("Old BOT ACCOUNT wording still remains")

    RAID.write_text(text, encoding="utf-8")
    print("✅ raidguard wording separated official bots, alt/raid risk, and DM reports")


def ensure_cleanup_imports(text: str) -> str:
    if "import discord" not in text:
        if "from __future__ import annotations" in text:
            text = text.replace("from __future__ import annotations\n", "from __future__ import annotations\n\nimport discord\n", 1)
        else:
            text = "import discord\n" + text

    if "app_commands" not in text.split("\n", 40)[0:]:
        # Conservative import insertion; harmless if app_commands is already imported later.
        if "from discord import app_commands" not in text:
            text = text.replace("import discord\n", "import discord\nfrom discord import app_commands\n", 1)

    if "from discord import app_commands" not in text and "app_commands." in text:
        text = text.replace("import discord\n", "import discord\nfrom discord import app_commands\n", 1)

    return text


def cleanup_group_name(text: str) -> str:
    m = re.search(r"(\w+)\s*=\s*app_commands\.Group\([^)]*name\s*=\s*['\"]cleanup['\"]", text, re.S)
    if m:
        return m.group(1)
    if "cleanup_group" in text:
        return "cleanup_group"
    raise SystemExit("Could not find cleanup app command group")


DM_REPORT_BLOCK_TEMPLATE = r'''

# ============================================================
# DM spam / DM raider report flow
# ============================================================

_DM_RAIDER_REPORT_COUNTS = globals().get("_DM_RAIDER_REPORT_COUNTS", {})


def _dm_raider_report_key(guild_id, target_user_id):
    return (int(guild_id), int(target_user_id))


def _dm_raider_increment_report(guild_id, target_user_id):
    key = _dm_raider_report_key(guild_id, target_user_id)
    _DM_RAIDER_REPORT_COUNTS[key] = int(_DM_RAIDER_REPORT_COUNTS.get(key, 0) or 0) + 1
    return int(_DM_RAIDER_REPORT_COUNTS[key])


def _dm_raider_staff_channel(guild, fallback):
    try:
        from stoney_verify.modlog import _get_modlog_channel
        ch = _get_modlog_channel(guild)
        if isinstance(ch, discord.TextChannel):
            return ch
    except Exception:
        pass
    return fallback if isinstance(fallback, discord.TextChannel) else None


def _dm_raider_can_staff_action(member):
    try:
        perms = getattr(member, "guild_permissions", None)
        return bool(
            getattr(perms, "administrator", False)
            or getattr(perms, "ban_members", False)
            or getattr(perms, "manage_messages", False)
        )
    except Exception:
        return False


class DmRaiderReportActionView(discord.ui.View):
    def __init__(self, *, target_user_id, report_count):
        super().__init__(timeout=900)
        self.target_user_id = int(target_user_id)
        self.report_count = int(report_count)

    async def interaction_check(self, interaction):
        if not _dm_raider_can_staff_action(interaction.user):
            await interaction.response.send_message(
                "❌ Staff action required. You need Ban Members, Manage Messages, or Administrator.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Ban by ID", emoji="🔨", style=discord.ButtonStyle.danger, custom_id="dank:dm_report:v1:ban_by_id")
    async def ban_by_id(self, interaction, button):
        _ = button
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ Guild context missing.", ephemeral=True)

        member = interaction.user
        perms = getattr(member, "guild_permissions", None)
        if not (getattr(perms, "administrator", False) or getattr(perms, "ban_members", False)):
            return await interaction.response.send_message("❌ You need **Ban Members** to ban by ID.", ephemeral=True)

        try:
            await guild.ban(
                discord.Object(id=int(self.target_user_id)),
                reason=f"Dank Shield DM spam report action by {interaction.user} ({interaction.user.id}); reports={self.report_count}",
                delete_message_days=0,
            )
            await interaction.response.send_message(
                f"🔨 Banned `<@{self.target_user_id}>` by user ID from DM spam report.",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message("❌ Ban failed: I need Ban Members and a role high enough to ban this user.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Ban failed: `{type(e).__name__}: {e}`", ephemeral=True)

    @discord.ui.button(label="Purge User Messages", emoji="🧹", style=discord.ButtonStyle.secondary, custom_id="dank:dm_report:v1:purge_hint")
    async def purge_hint(self, interaction, button):
        _ = button
        await interaction.response.send_message(
            "Run a fresh purge preview, then use its delete button:\n"
            f"`/dank cleanup purge user_id:{self.target_user_id} scope:Whole server dry_run:true`\n\n"
            "This keeps DM reports separate from message deletion so the bot does not silently nuke channels.",
            ephemeral=True,
        )


@{GROUP_NAME}.command(name="report-dm-spam", description="Report a member/user who DM-spammed or sent NSFW/scam DMs.")
@app_commands.describe(
    target_user_id="Raw Discord user ID of the suspected DM spammer/raider",
    evidence="Short note: what happened, screenshot link, or what users reported",
)
async def cleanup_report_dm_spam(interaction: discord.Interaction, target_user_id: str, evidence: str = ""):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This only works inside a server.", ephemeral=True)

    raw = str(target_user_id or "").replace("<@", "").replace("!", "").replace(">", "").strip()
    if not raw.isdigit():
        return await interaction.response.send_message("❌ Provide a raw numeric Discord user ID.", ephemeral=True)

    target_id = int(raw)
    if target_id <= 0:
        return await interaction.response.send_message("❌ Invalid user ID.", ephemeral=True)

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    report_count = _dm_raider_increment_report(int(guild.id), target_id)
    staff_channel = _dm_raider_staff_channel(guild, interaction.channel)

    embed = discord.Embed(
        title="🚩 DM Raider Report",
        description=(
            "A member/staff report says this user may be sending unwanted DMs, NSFW images, scams, or raid spam.\n\n"
            "**Important:** Dank Shield cannot read private DMs. This is report-based evidence, not private-message surveillance."
        ),
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Target", value=f"<@{target_id}> (`{target_id}`)", inline=False)
    embed.add_field(name="Reporter", value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=False)
    embed.add_field(name="Report count this runtime", value=f"`{report_count}`", inline=True)
    embed.add_field(name="Official Bot", value="Unknown from report alone", inline=True)
    embed.add_field(name="DM Raider Report Risk", value="Report evidence exists — staff review required", inline=False)
    embed.add_field(
        name="Evidence / note",
        value=(str(evidence or "No note provided.")[:900]),
        inline=False,
    )
    embed.add_field(
        name="Recommended next steps",
        value=(
            "1. Review screenshot/user reports.\n"
            "2. Ban by ID if confirmed.\n"
            "3. Run user-message purge if they also posted in-server.\n"
            "4. Check invite/source reputation if multiple reports came from the same invite."
        ),
        inline=False,
    )
    embed.set_footer(text="Dank Shield DM report flow • report evidence, not DM reading")

    if staff_channel is None:
        return await interaction.followup.send("❌ No staff/modlog channel was available for the DM report.", ephemeral=True)

    try:
        await staff_channel.send(embed=embed, view=DmRaiderReportActionView(target_user_id=target_id, report_count=report_count))
    except Exception as e:
        return await interaction.followup.send(f"❌ Could not post DM report: `{type(e).__name__}: {e}`", ephemeral=True)

    await interaction.followup.send(
        f"✅ DM spam report sent to {staff_channel.mention}. Staff can ban by ID or start a purge from that card.",
        ephemeral=True,
    )
'''


def patch_cleanup() -> None:
    text = CLEANUP.read_text(encoding="utf-8")
    text = ensure_cleanup_imports(text)
    group_name = cleanup_group_name(text)

    if "async def cleanup_report_dm_spam" not in text:
        text += DM_REPORT_BLOCK_TEMPLATE.replace("{GROUP_NAME}", group_name)
    else:
        print("✅ DM report command already present")

    required = (
        "DmRaiderReportActionView",
        "report-dm-spam",
        "DM Raider Report",
        "Dank Shield cannot read private DMs",
        "Ban by ID",
        "Purge User Messages",
    )
    missing = [token for token in required if token not in text]
    if missing:
        raise SystemExit("Missing DM report tokens: " + ", ".join(missing))

    CLEANUP.write_text(text, encoding="utf-8")
    print("✅ cleanup DM spam report flow added")


def write_test() -> None:
    TEST.write_text(
        '''from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAID = (ROOT / "stoney_verify/raidguard.py").read_text(encoding="utf-8")
CLEANUP = (ROOT / "stoney_verify/commands_ext/public_cleanup_group.py").read_text(encoding="utf-8")


def test_risk_wording_separates_bot_alt_and_dm_report() -> None:
    assert "Official Bot: Yes" in RAID
    assert "Official Bot: No" in RAID
    assert "Alt/Raid Risk:" in RAID
    assert "DM Raider Report Risk:" in RAID
    assert "BOT ACCOUNT • excluded from raid/alt scoring" not in RAID


def test_dm_report_command_exists_under_cleanup() -> None:
    assert "report-dm-spam" in CLEANUP
    assert "async def cleanup_report_dm_spam" in CLEANUP
    assert "DM Raider Report" in CLEANUP
    assert "Dank Shield cannot read private DMs" in CLEANUP


def test_dm_report_has_staff_actions_without_auto_purge() -> None:
    assert "class DmRaiderReportActionView" in CLEANUP
    assert "Ban by ID" in CLEANUP
    assert "Purge User Messages" in CLEANUP
    assert "guild.ban(" in CLEANUP
    assert "Run a fresh purge preview" in CLEANUP


def test_dm_report_is_not_private_dm_surveillance() -> None:
    assert "report-based evidence" in CLEANUP
    assert "not private-message surveillance" in CLEANUP or "not DM reading" in CLEANUP


if __name__ == "__main__":
    for test in (
        test_risk_wording_separates_bot_alt_and_dm_report,
        test_dm_report_command_exists_under_cleanup,
        test_dm_report_has_staff_actions_without_auto_purge,
        test_dm_report_is_not_private_dm_surveillance,
    ):
        test()
        print(f"PASS {test.__name__}")
''',
        encoding="utf-8",
    )
    print("✅ wrote bot detection cleanup static test")


def main() -> None:
    patch_raidguard()
    patch_cleanup()
    write_test()


if __name__ == "__main__":
    main()
