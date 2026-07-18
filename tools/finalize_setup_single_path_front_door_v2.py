from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECOMMEND = ROOT / "stoney_verify/commands_ext/public_setup_recommend.py"
FRESH = ROOT / "stoney_verify/commands_ext/public_setup_fresh_choice.py"
TEST = ROOT / "tests/test_setup_single_path_front_door_v2_static.py"


def replace_block(text: str, start: str, end: str, replacement: str) -> str:
    left = text.index(start)
    right = text.index(end, left)
    return text[:left] + replacement.rstrip() + "\n\n" + text[right:]


recommend = RECOMMEND.read_text(encoding="utf-8")

# Make every user-facing setup label literal enough to understand at a glance.
replacements = {
    'label="Fix This Step"': 'label="Set Up This Step"',
    'label="Advanced Settings"': 'label="Other Settings"',
    'label="Setup Check / Diagnostics"': 'label="Check Setup for Problems"',
    'label="Reset / Recovery"': 'label="Fix Setup or Start Over"',
    'label="Features On / Off"': 'label="Turn Features On / Off"',
    'label="Timers & Behavior"': 'label="Timers & Rules"',
    'label="Detailed Role / Channel Mapping"': 'label="Choose Roles & Channels"',
    'label="Modlog Tracking"': 'label="Choose What Gets Logged"',
    'label="Protection"': 'label="Spam & Raid Protection"',
    'label="Permission Repair"': 'label="Fix Channel Permissions"',
    'label="Back to Advanced Settings"': 'label="Back to Other Settings"',
    'title="⚙️ Advanced Settings"': 'title="⚙️ Other Settings"',
    'name="⚙️ Advanced Settings"': 'name="⚙️ Other Settings"',
    'title="🛡️ Logs, Protection & Repairs"': 'title="🛡️ Logs & Safety"',
    'name="🛡️ Logs, Protection & Repairs"': 'name="🛡️ Logs & Safety"',
    'label="Logs, Protection & Repairs"': 'label="Logs & Safety"',
    '"🧩 **Features On / Off** — choose which services run."': '"🧩 **Turn Features On / Off** — choose which features this server uses."',
    '"⏱️ **Timers & Behavior** — timers, naming, and flow settings."': '"⏱️ **Timers & Rules** — change timers, names, and how setup actions work."',
    '"🧭 **Detailed Role / Channel Mapping** — deliberately remap saved items."': '"🧭 **Choose Roles & Channels** — change which Discord roles and channels Dank Shield uses."',
    '"🧾 **Modlog Tracking** — choose which server events are recorded."': '"🧾 **Choose What Gets Logged** — choose which server actions are saved in the log."',
    '"🛡️ **Protection** — open the Protection Center."': '"🛡️ **Spam & Raid Protection** — change spam and raid safety settings."',
    '"🛠️ **Permission Repair** — preview and repair saved setup channel permissions."': '"🛠️ **Fix Channel Permissions** — check and fix access to Dank Shield channels."',
    '"Advanced Settings • use Back to Advanced Settings to return"': '"Other Settings • use Back to Other Settings to return"',
    '"Advanced Settings"': '"Other Settings"',
}
for old, new in replacements.items():
    recommend = recommend.replace(old, new)

# More Options wording should describe actions, not internal concepts.
recommend = recommend.replace(
    'embed.add_field(name="⚙️ Other Settings", value="Edit optional feature, ticket, logging, protection, mapping, or design settings.", inline=False)',
    'embed.add_field(name="⚙️ Other Settings", value="Change features, roles, channels, tickets, logs, safety settings, or server design.", inline=False)',
)
recommend = recommend.replace(
    'embed.add_field(name="🩺 Setup Check / Diagnostics", value="Run the full setup truth check manually.", inline=False)',
    'embed.add_field(name="🩺 Check Setup for Problems", value="Check what is missing or set up incorrectly.", inline=False)',
)
recommend = recommend.replace(
    'embed.add_field(name="🧯 Reset / Recovery", value="Repair or deliberately start setup over.", inline=False)',
    'embed.add_field(name="🧯 Fix Setup or Start Over", value="Use repair tools or deliberately restart setup.", inline=False)',
)
recommend = recommend.replace(
    'description="These are optional settings. They are not part of the normal guided setup path."',
    'description="These are extra settings. Most people do not need them during normal setup."',
)
recommend = recommend.replace(
    'embed.add_field(name="🧩 Features, Roles & Channels", value="Feature switches, timers, and detailed saved-item mapping.", inline=False)',
    'embed.add_field(name="🧩 Features, Roles & Channels", value="Turn features on or off, change timers, and choose the roles and channels Dank Shield uses.", inline=False)',
)
recommend = recommend.replace(
    'embed.add_field(name="🛡️ Logs & Safety", value="Modlog tracking, Protection Center, and permission repair.", inline=False)',
    'embed.add_field(name="🛡️ Logs & Safety", value="Choose what gets logged, change spam and raid protection, and fix channel permissions.", inline=False)',
)
recommend = recommend.replace(
    'name="Next Step",',
    'name="Do This Next",',
)

RECOMMEND.write_text(recommend, encoding="utf-8")

fresh = FRESH.read_text(encoding="utf-8")

# Replace the whole custom-setup embed so a failed earlier regex cannot leave bad indentation.
fresh = replace_block(
    fresh,
    "def _custom_services_embed(",
    "class CustomServicePresetSelect(discord.ui.Select):",
    '''def _custom_services_embed(guild: discord.Guild, state: Any, *, saved_message: str = "") -> discord.Embed:
    payload = {key: bool(state.as_payload().get(key, False)) for key in _CUSTOM_SERVICE_FLAG_KEYS}
    preset_key = _custom_preset_key_for_payload(payload)
    preset_label = CUSTOM_PRESETS.get(preset_key, ("Your choices", {}, "", "🧩"))[0] if preset_key else "Your choices"

    embed = discord.Embed(
        title="🧩 Choose Your Features",
        description=(
            "Choose what you want Dank Shield to do in this server. "
            "A green button means the feature is ON. A gray button means it is OFF."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    if saved_message:
        embed.add_field(name="Saved", value=saved_message[:1024], inline=False)

    embed.add_field(name="Your Setup", value=f"**{preset_label}**\n{_custom_mix_label(payload)}", inline=False)
    embed.add_field(name="Features", value=_service_summary_text(state), inline=False)
    embed.add_field(
        name="Next",
        value=(
            "Turn the features on or off, then press **Continue Setup**. "
            "Dank Shield will walk you through the rest one step at a time."
        ),
        inline=False,
    )
    embed.set_footer(text=f"Guild {guild.id} • choose your features")
    return embed
''',
)

# Friendlier setup-type names and descriptions; underlying keys stay unchanged.
choice_replacements = {
    'PlainSetupChoice("basic_server", "Basic Server", "🏠", "Simple server setup with support tickets, starter logs, and normal public-server defaults.", "A clean support button when they need staff help.", True, False, False, "basic")':
        'PlainSetupChoice("basic_server", "Tickets + Server Basics", "🏠", "Sets up support tickets and basic logs. A good choice for most servers that do not need member verification.", "A support button when they need help from staff.", True, False, False, "basic")',
    'PlainSetupChoice("basic_verify", "Basic Verify", "✅", "Simple Verify button flow: no ID upload, no website token, no voice check, no forced ticket.", "A Verify button that grants the configured access role and removes the waiting role.", False, False, False, "basic_verify")':
        'PlainSetupChoice("basic_verify", "Simple Verify", "✅", "Members press one Verify button to get the member role. No ID upload or voice check.", "One Verify button that gives them server access.", False, False, False, "basic_verify")',
    'PlainSetupChoice("help_desk", "Help Desk", "🎫", "Support-ticket focused setup for help requests, reports, appeals, and staff triage.", "A clean ticket panel with fast support choices.", True, False, False, "help_desk")':
        'PlainSetupChoice("help_desk", "Help Desk / Tickets", "🎫", "Sets up support tickets for help requests, reports, appeals, and staff support.", "A ticket panel where they choose what they need help with.", True, False, False, "help_desk")',
    'PlainSetupChoice("custom_setup", "Custom", "⚙️", "Choose every service yourself: tickets, Basic Verify, voice verify, SpamGuard, and logs.", "Whatever services you turn on in the next screen.", False, False, False, "custom")':
        'PlainSetupChoice("custom_setup", "Choose My Own Features", "⚙️", "Choose exactly which features you want: tickets, Simple Verify, Voice Verify, SpamGuard, and logs.", "Only the features you choose on the next screen.", False, False, False, "custom")',
}
for old, new in choice_replacements.items():
    fresh = fresh.replace(old, new)

fresh = fresh.replace('title="🧩 Custom Setup — Service Switches"', 'title="🧩 Choose Your Features"')
fresh = fresh.replace('placeholder="Choose what this server needs"', 'placeholder="What do you want Dank Shield to do?"')
fresh = fresh.replace('description="Current manual ON/OFF switches."', 'description="Your current feature choices."')

FRESH.write_text(fresh, encoding="utf-8")

# Update the permanent test to enforce the clearer labels.
test = TEST.read_text(encoding="utf-8")
test = test.replace('assert "Fix This Step" in guided', 'assert "Set Up This Step" in guided')
test = test.replace(
    'for text in ("Change Setup Type", "Advanced Settings", "Setup Check / Diagnostics", "Reset / Recovery", "Help", "Back Home"):',
    'for text in ("Change Setup Type", "Other Settings", "Check Setup for Problems", "Fix Setup or Start Over", "Help", "Back Home"):',
)
test = test.replace('assert "Logs, Protection & Repairs" in hub', 'assert "Logs & Safety" in hub')
test = test.replace('assert "Choose what this server needs" in choice', 'assert "What do you want Dank Shield to do?" in choice')
test += '''\n\ndef test_no_vague_setup_group_names_remain_in_user_navigation():\n    for text in ("Member Experience", "Core Setup", "Monitoring & Repair", "Setup Check / Diagnostics"):\n        assert text not in RECOMMEND\n\n\ndef test_plain_language_action_labels_exist():\n    for text in ("Turn Features On / Off", "Timers & Rules", "Choose Roles & Channels", "Choose What Gets Logged", "Spam & Raid Protection", "Fix Channel Permissions"):\n        assert text in RECOMMEND\n\n\ndef test_setup_type_names_are_plain_language():\n    for text in ("Tickets + Server Basics", "Simple Verify", "Help Desk / Tickets", "Choose My Own Features"):\n        assert text in FRESH\n'''
TEST.write_text(test, encoding="utf-8")

for path in (RECOMMEND, FRESH, TEST):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")

print("PASS: finalized plain-language one-path setup UX")
