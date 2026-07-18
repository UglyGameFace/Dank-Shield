from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
RECOMMEND = ROOT / "stoney_verify/commands_ext/public_setup_recommend.py"
FRESH = ROOT / "stoney_verify/commands_ext/public_setup_fresh_choice.py"
TEST = ROOT / "tests/test_setup_single_path_front_door_v2_static.py"


def replace_block(text: str, start: str, end: str, replacement: str = "") -> str:
    left = text.index(start)
    right = text.index(end, left)
    return text[:left] + replacement.rstrip() + ("\n\n" if replacement.strip() else "") + text[right:]


recommend = RECOMMEND.read_text(encoding="utf-8")

# Remove dead review buttons that are no longer part of Setup Check.
if "class SetupReviewAdvancedButton" in recommend:
    recommend = replace_block(
        recommend,
        "class SetupReviewAdvancedButton",
        "class SetupReviewHomeButton",
    )

# Replace the stale legacy setup-choice instructions with the same one-path message.
recommend, count = re.subn(
    r'''        embed\.add_field\(\n            name="Next step",\n            value=\(\n                "• Press \*\*Use My Existing Server\*\* if your roles/channels already exist\\n"\n                "• Press \*\*Create Missing Items\*\* if you want Dank Shield to create missing basics\\n"\n                "• Press \*\*Health Check\*\* when you think setup is ready\."\n            \),\n            inline=False,\n        \)''',
    '''        embed.add_field(
            name="Next",
            value=(
                "Press **Continue Setup** on Setup Home. "
                "Dank Shield will show only the next thing you need to set up."
            ),
            inline=False,
        )''',
    recommend,
    count=1,
)
if count != 1:
    raise SystemExit(f"stale legacy next-step replacement count={count}")

replacements = {
    'embed.set_footer(text=f"Guild {guild.id} • /dank setup • simple home")':
        'embed.set_footer(text=f"Guild {guild.id} • /dank setup")',
    'text="Press one choice below. Nothing else is deleted."':
        'text="Choose one option from the menu. Nothing is deleted."',
    'title="🧭 Use Existing Roles / Channels"':
        'title="🧭 Choose Existing Roles & Channels"',
    'description="Map the roles, channels, and folders your server already has. Names do not matter; Dank Shield saves Discord IDs."':
        'description="Choose the roles, channels, and folders your server already uses. Dank Shield remembers the Discord items you pick."',
    'name="Recommended order"': 'name="Choose These in Order"',
    'value="1. Ticket Basics\\n2. Access Roles\\n3. Verification Channels\\n4. Logs + Status\\n5. Behavior Settings"':
        'value="1. Ticket setup\\n2. Member roles\\n3. Verification channels\\n4. Log channels\\n5. Timers and rules"',
    '"❌ Make Missing Things failed: "': '"❌ Creating missing setup items failed: "',
    'saved_message="Service switches opened. Turn each feature ON/OFF here."':
        'saved_message="Choose which features are ON or OFF."',
    'title="Service Switches Did Not Open"': 'title="Feature Settings Did Not Open"',
    '"🛡️ Protection opened from "\n            "**Advanced Options**."':
        '"🛡️ Spam & Raid Protection opened from "\n            "**Other Settings**."',
    'title="⏱️ Timers & Behavior"': 'title="⏱️ Timers & Rules"',
    '"Change verification timers, ticket naming, "\n            "verification style, and other server behavior. "':
        '"Change verification timers, ticket names, "\n            "and other setup rules. "',
    '"Advanced Options • Back to Advanced returns to the "\n            "grouped menu"':
        '"Other Settings • use Back to Other Settings to return"',
    'description="Change enabled features, timers, and saved role/channel mappings."':
        'description="Turn features on or off, change timers and rules, or choose different roles and channels."',
    'description="Manage logging, protection tools, and permission repair."':
        'description="Choose what gets logged, change spam and raid protection, or fix channel access."',
    'description="Open the server design, preview, and rollback tools."':
        'description="Change how the server looks, preview changes, or undo the last design change."',
    '"🎨 **Server Design** — fonts, frames, emojis, preview, and rollback."':
        '"🎨 **Server Design** — fonts, frames, emojis, previews, and undo tools."',
    'title="🧯 Reset / Recovery"': 'title="🧯 Fix Setup or Start Over"',
    'description="Use this only when you deliberately want to recover or start setup over."':
        'description="Use this only if setup is broken or you want to start again."',
    '"🧯 **Recovery / Start Over** — safely reset or recover setup."':
        '"🧯 **Fix or Start Over** — repair setup or restart it safely."',
    'label="Recovery / Start Over"': 'label="Fix or Start Over"',
    'value="Visual design, preview, and rollback tools."':
        'value="Change how the server looks, preview changes, or undo a design change."',
    'title="🧪 Test / Launch"': 'title="🧪 Test & Launch"',
    '"This is where you post the panels and run the real test. "\n            "Use an alt account before real members."':
        '"Post the panels, then test them before real members use them. "\n            "Use a second Discord account for the test when possible."',
    'name="Selected Services"': 'name="Features That Are On"',
    'name="Launch Actions"': 'name="What To Test"',
    'name="Expected Result"': 'name="What Should Happen"',
    '"5. Join with an alt, click the public panel(s), and confirm roles/logs."':
        '"5. Join with a second test account, use the public panels, and make sure roles and logs work."',
    'label="Run Setup Check"': 'label="Check Setup Again"',
}
for old, new in replacements.items():
    recommend = recommend.replace(old, new)

RECOMMEND.write_text(recommend, encoding="utf-8")

fresh = FRESH.read_text(encoding="utf-8")

fresh_replacements = {
    '"tickets": ("Tickets only", {"tickets_enabled": True, "verification_enabled": False, "voice_verification_enabled": False, "spam_guard_enabled": False, "moderation_enabled": False}, "Ticket panel and ticket lifecycle only.", "🎫")':
        '"tickets": ("Tickets only", {"tickets_enabled": True, "verification_enabled": False, "voice_verification_enabled": False, "spam_guard_enabled": False, "moderation_enabled": False}, "Support ticket panel and ticket tools.", "🎫")',
    '"basic_verify": ("Basic Verify only", {"tickets_enabled": False, "verification_enabled": True, "voice_verification_enabled": False, "spam_guard_enabled": False, "moderation_enabled": False}, "One-button verify gate. No ID, no VC, no ticket required.", "✅")':
        '"basic_verify": ("Simple Verify only", {"tickets_enabled": False, "verification_enabled": True, "voice_verification_enabled": False, "spam_guard_enabled": False, "moderation_enabled": False}, "One Verify button. No tickets, ID upload, or voice check.", "✅")',
    '"voice_verify": ("Basic + Voice Verify", {"tickets_enabled": True, "verification_enabled": True, "voice_verification_enabled": True, "spam_guard_enabled": False, "moderation_enabled": True}, "Basic Verify plus staff voice-check support.", "🎙️")':
        '"voice_verify": ("Simple + Voice Verify", {"tickets_enabled": True, "verification_enabled": True, "voice_verification_enabled": True, "spam_guard_enabled": False, "moderation_enabled": True}, "Simple Verify plus a staff voice check.", "🎙️")',
    '"spamguard": ("SpamGuard only", {"tickets_enabled": False, "verification_enabled": False, "voice_verification_enabled": False, "spam_guard_enabled": True, "moderation_enabled": True}, "Spam protection and logs without ticket or verify blockers.", "🛡️")':
        '"spamguard": ("SpamGuard only", {"tickets_enabled": False, "verification_enabled": False, "voice_verification_enabled": False, "spam_guard_enabled": True, "moderation_enabled": True}, "Spam and raid protection with logs.", "🛡️")',
    '"all": ("Everything", {"tickets_enabled": True, "verification_enabled": True, "voice_verification_enabled": True, "spam_guard_enabled": True, "moderation_enabled": True}, "Tickets, Basic Verify, Voice Verify, SpamGuard, and logs.", "🚀")':
        '"all": ("Everything", {"tickets_enabled": True, "verification_enabled": True, "voice_verification_enabled": True, "spam_guard_enabled": True, "moderation_enabled": True}, "Tickets, Simple Verify, Voice Verify, SpamGuard, and logs.", "🚀")',
    '"A verification ticket with a Verify in VC option."':
        '"A verification ticket with a button to request a staff voice check."',
    '"A verification ticket with an Upload ID button."':
        '"A private button to upload an ID for staff review."',
    '"Upload ID, Verify in VC, reveal link, regenerate link if enabled, and website button if configured."':
        '"Private ID upload and a button to request a staff voice check."',
    'f"Basic Verify: **{_state_word(bool(state.verification))}**\\n"':
        'f"Simple Verify: **{_state_word(bool(state.verification))}**\\n"',
    'labels.append("Basic Verify")': 'labels.append("Simple Verify")',
    'return "Custom mix: " + (", ".join(labels) if labels else "No services selected")':
        'return "Your features: " + (", ".join(labels) if labels else "No features selected")',
    'saved_message="Still using your current **Custom mix**."':
        'saved_message="Still using your current feature choices."',
    'CustomServiceToggleButton("verification_enabled", "Basic Verify", state.verification, "✅", 2)':
        'CustomServiceToggleButton("verification_enabled", "Simple Verify", state.verification, "✅", 2)',
    '"Saved **Custom setup**. Existing server items are detected automatically. Turn on/off only what this server should actually use."':
        '"Saved **Choose My Own Features**. Dank Shield checks what is already set up and pre-selects matching features. Turn off anything you do not want."',
    'enabled.append("Basic Verify")': 'enabled.append("Simple Verify")',
    'enabled.append("Voice Verification")': 'enabled.append("Voice Verify")',
    'enabled.append("SpamGuard setup / Logs")': 'enabled.append("SpamGuard / Logs")',
    'return "Choose at least one service first." if not enabled else "Health Check will focus on: " + ", ".join(enabled) + "."':
        'return "Choose at least one feature first." if not enabled else "Setup will check: " + ", ".join(enabled) + "."',
}
for old, new in fresh_replacements.items():
    fresh = fresh.replace(old, new)

FRESH.write_text(fresh, encoding="utf-8")

# Extend permanent UX contracts so stale wording cannot creep back in.
test = TEST.read_text(encoding="utf-8")
if "test_secondary_setup_screens_use_the_same_plain_language" not in test:
    test += '''\n\ndef test_secondary_setup_screens_use_the_same_plain_language():\n    legacy_choice = block(RECOMMEND, "class SetupChoiceView(", "class SetupReviewFixNextButton(")\n    for stale in ("Use My Existing Server", "Create Missing Items", "Health Check"):\n        assert stale not in legacy_choice\n    for stale in ("Service switches opened", "Service Switches Did Not Open", "Advanced Options •", "Timers & Behavior", "Use Existing Roles / Channels"):\n        assert stale not in RECOMMEND\n\n\ndef test_custom_feature_picker_avoids_setup_jargon():\n    for stale in ("Ticket panel and ticket lifecycle only.", "No ID, no VC", "verify blockers", "Custom mix:", "Basic Verify only", "Basic + Voice Verify"):\n        assert stale not in FRESH\n    for expected in ("Support ticket panel and ticket tools.", "Simple Verify only", "Simple + Voice Verify", "Your features:", "Simple Verify"):\n        assert expected in FRESH\n\n\ndef test_plain_language_fallback_actions_are_consistent():\n    for expected in ("Choose Existing Roles & Channels", "Choose which features are ON or OFF", "Feature Settings Did Not Open", "Check Setup Again", "Fix Setup or Start Over"):\n        assert expected in RECOMMEND\n'''
TEST.write_text(test, encoding="utf-8")

for path in (RECOMMEND, FRESH, TEST):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")

print("PASS: applied full setup plain-language second pass")
