from __future__ import annotations

from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str, *, marker: str) -> None:
    text = read(path)
    if marker in text:
        print(f"already applied: {path} marker={marker}")
        return
    if old not in text:
        raise RuntimeError(f"missing replacement anchor in {path}: {old[:100]!r}")
    write(path, text.replace(old, new, 1))
    print(f"applied: {path} marker={marker}")


def insert_before_after_anchor(path: str, *, anchor: str, before: str, block: str, marker: str) -> None:
    text = read(path)
    if marker in text:
        print(f"already applied: {path} marker={marker}")
        return
    start = text.find(anchor)
    if start < 0:
        raise RuntimeError(f"missing primary anchor in {path}: {anchor!r}")
    pos = text.find(before, start + len(anchor))
    if pos < 0:
        raise RuntimeError(f"missing insertion anchor in {path}: {before!r}")
    write(path, text[:pos] + block + text[pos:])
    print(f"applied: {path} marker={marker}")


SPAM_PATH = "stoney_verify/spam_guard.py"
INVITE_PATH = "stoney_verify/invite_policy_engine.py"
CENTER_PATH = "stoney_verify/commands_ext/public_protection_center.py"
TEST_PATH = "tests/test_public_protection_center_native_interaction_static.py"

insert_before_after_anchor(
    SPAM_PATH,
    anchor='reason="Spam Guard: repeated external invite attempts blocked by Invite Shield",',
    before="            await _log_trigger(\n",
    marker="source=invite-shield error=",
    block='''            try:\n                from .security_stats import record_spam_guard_action\n\n                await record_spam_guard_action(\n                    guild.id,\n                    deleted_messages=0,\n                    action_taken=action_taken,\n                    quarantine_case=quarantine_case,\n                )\n            except Exception as e:\n                _debug(f"security stats record failed guild={guild.id} source=invite-shield error={repr(e)}")\n\n''',
)

insert_before_after_anchor(
    SPAM_PATH,
    anchor='reason="Spam guard: probable hacked-account spam burst",',
    before="            await _log_trigger(\n",
    marker="source=spam-guard error=",
    block='''            try:\n                from .security_stats import record_spam_guard_action\n\n                await record_spam_guard_action(\n                    guild.id,\n                    deleted_messages=delete_count,\n                    action_taken=action_taken,\n                    quarantine_case=quarantine_case,\n                )\n            except Exception as e:\n                _debug(f"security stats record failed guild={guild.id} source=spam-guard error={repr(e)}")\n\n''',
)

replace_once(
    INVITE_PATH,
    '        decision.delete_error = ""\n        record_invite_decision(message, decision)\n        return True\n',
    '''        decision.delete_error = ""\n        try:\n            from stoney_verify.security_stats import record_security_event\n\n            if message.guild is not None:\n                await record_security_event(int(message.guild.id), invites_blocked=1)\n        except Exception:\n            # Statistics must never turn a successful moderation delete into a failure.\n            pass\n        record_invite_decision(message, decision)\n        return True\n''',
    marker="await record_security_event(int(message.guild.id), invites_blocked=1)",
)

replace_once(
    CENTER_PATH,
    'from ..interaction_guard import log_interaction_failure, run_guarded_interaction, safe_send_interaction\n',
    '''from ..interaction_guard import log_interaction_failure, run_guarded_interaction, safe_send_interaction\nfrom ..security_stats import (\n    SECURITY_STATS_ENABLED_KEY,\n    ensure_security_stats_display,\n    refresh_security_stats_display,\n)\n''',
    marker="from ..security_stats import (",
)

replace_once(
    CENTER_PATH,
    '            "**Block All Links** = stop every URL.\\n"\n',
    '            "**Block All Links** = stop every URL.\\n"\n            "**Live Stats** = create locked voice-channel counters using real Spam Guard actions.\\n"\n',
    marker="**Live Stats** = create locked voice-channel counters",
)

replace_once(
    CENTER_PATH,
    '    spam, spam_source = await _load_spam_settings(int(guild.id))\n    embed = _protection_embed(guild, cfg, spam, spam_source)\n',
    '''    spam, spam_source = await _load_spam_settings(int(guild.id))\n    try:\n        await refresh_security_stats_display(guild, force=True)\n    except Exception as exc:\n        log_interaction_failure(\n            interaction,\n            exc,\n            stage="security_stats_refresh_failed",\n            action_name="protection.live_stats.refresh",\n            fix_hint="The Protection Center still works; check Manage Channels/Manage Roles if the live stats display stops updating.",\n        )\n    embed = _protection_embed(guild, cfg, spam, spam_source)\n''',
    marker='stage="security_stats_refresh_failed"',
)

insert_before_after_anchor(
    CENTER_PATH,
    anchor='        elif custom_id == "dank_protection:edit_spamguard":',
    before="\n\nasync def _apply_protection_preset",
    marker='custom_id == "dank_protection:live_stats"',
    block='''        elif custom_id == "dank_protection:live_stats":\n            live_stats_on = _cfg_bool(cfg, SECURITY_STATS_ENABLED_KEY, False)\n            child.label = f"Live Stats: {'ON' if live_stats_on else 'SET UP'}"\n            child.style = discord.ButtonStyle.success if live_stats_on else discord.ButtonStyle.secondary\n''',
)

insert_before_after_anchor(
    CENTER_PATH,
    anchor='class ProtectionCenterView(discord.ui.View):',
    before='    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="dank_protection:refresh", row=3)\n',
    marker='async def live_stats_button(self, interaction: discord.Interaction',
    block='''    @discord.ui.button(label="Live Stats", emoji="📊", style=discord.ButtonStyle.secondary, custom_id="dank_protection:live_stats", row=3)\n    async def live_stats_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:\n        _ = button\n\n        async def action() -> None:\n            if not await _require_setup_permission(interaction):\n                return\n            guild = interaction.guild\n            if guild is None:\n                await _send_ephemeral(interaction, "❌ This must be used inside a server.")\n                return\n            ok, note = await ensure_security_stats_display(guild)\n            if not ok:\n                await _send_ephemeral(interaction, note)\n                return\n            await _refresh_panel(interaction, content=note)\n\n        await _guard_protection_action(\n            interaction,\n            "protection.live_stats",\n            action,\n            defer=True,\n        )\n\n''',
)

replace_once(
    TEST_PATH,
    '        "protection.allow_links",\n        "protection.refresh",\n',
    '        "protection.allow_links",\n        "protection.live_stats",\n        "protection.refresh",\n',
    marker='        "protection.live_stats",',
)

print("SG-STATS-001 runtime integration applied")
