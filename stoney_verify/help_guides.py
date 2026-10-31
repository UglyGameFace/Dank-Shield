from __future__ import annotations

"""Practical, read-only instructions for the canonical Dank Shield feature owners.

Help explains what a person should do; the real feature screens continue to
own permissions, state, validation, previews and saves. Keep all registry keys
covered rather than introducing a second, stale slash-command directory.
"""

from dataclasses import dataclass

from .navigation_registry import FEATURES


@dataclass(frozen=True)
class HelpGuide:
    purpose: str
    steps: tuple[str, ...]
    outcome: str
    caution: str = ""


HELP_GUIDES: dict[str, HelpGuide] = {
    "setup_center": HelpGuide(
        "Configure the server without memorizing separate commands.",
        ("Open Setup & Settings and choose the area you want to change.",
         "Select the roles or channels the setup screen asks for. Use This Channel when available, or search for another.",
         "Read any permission or readiness warnings, save the selected settings, and reopen the panel to check them."),
        "Only the settings you confirm are changed; other server features stay in their own menus.",
        "Requires server management permissions."),
    "verification": HelpGuide(
        "Let members complete the server's verification steps and receive the right access.",
        ("Open Verification and review the server's current Unverified and access-role mappings.",
         "Choose the verification method and where members should see the verification instructions or panel.",
         "Check visibility and permissions from an unverified member's perspective, then test the verification flow."),
        "Members who complete the configured checks can receive their intended access.",
        "Verification roles and private-channel permissions must agree; a bot cannot bypass Discord's channel permissions."),
    "action_reminder_center": HelpGuide(
        "See member setup tasks and the reminder settings in one place.",
        ("Open the Action & Reminder Center.",
         "Choose Review Member Setup or Find Verification to see what needs attention.",
         "Open Reminder Settings or My Reminder Preferences for the relevant delivery controls."),
        "This is a directory and status screen. Opening it does not send any messages.",
        "Automated sends require the appropriate owner settings and healthy delivery storage."),
    "action_notification_systems": HelpGuide(
        "Find the correct owner for each kind of server notification.",
        ("Open Other Notification Settings.",
         "Choose whether you are dealing with verification, tickets, member activity, or another notification type.",
         "Use that feature's own settings to choose channels, opt-ins, and delivery behavior."),
        "Notification changes remain with the relevant feature rather than being silently applied to everything.",
        "A server-wide notification switch does not replace per-feature consent or permissions."),
    "action_builder": HelpGuide(
        "Preview who a Member Setup reminder rule would affect before enabling delivery.",
        ("Open Reminder Rules (Preview).",
         "Choose the setup requirement or audience to inspect.",
         "Review the generated preview and any missing prerequisites before using separate delivery settings."),
        "A draft or preview lets staff inspect targeting without automatically sending reminders.",
        "A preview is not proof that a message was delivered."),
    "action_safety_schedule": HelpGuide(
        "Control when optional Member Setup reminders may send.",
        ("Open Reminder Times & Limits.",
         "Review the current timing, rate limits, and send readiness.",
         "Change permitted scheduling controls and explicitly enable automatic delivery only if you intend to."),
        "Enabled schedules use their own safeguards, rather than inheriting a manual-send button's behavior.",
        "Delivery may pause if the private ledger or prerequisites are unhealthy."),
    "action_my_preferences": HelpGuide(
        "Choose whether you personally receive optional Member Setup reminders.",
        ("Open My Reminder Preferences.",
         "Review your current reminder choice and any available opt-in or opt-out controls.",
         "Save the preference and reopen the panel to verify the saved state."),
        "The choice applies to your eligible optional reminders, not every mandatory server message.",
        "Server-level notification rules may be separate."),
    "verification_reminders": HelpGuide(
        "Remind eligible unverified members without sending duplicate or untracked notices.",
        ("Open Verification Reminders, then Configure to review the audience, destination and schedule.",
         "Choose Use This Channel, Choose Other Channel, or Use DMs, then press Preview to inspect the message.",
         "Use Check Scheduled Due for automatic eligibility, or Send Reminder Now to preview and confirm a manual send. Check Database if sending is blocked."),
        "Manual sending requires its own preview and confirmation. Automatic reminders follow separate scheduling limits.",
        "Never treat an unhealthy PostgreSQL delivery ledger as permission to send; it deliberately blocks delivery."),
    "welcome": HelpGuide(
        "Choose what members see when they join or leave.",
        ("Open Welcome, Join & Exit and choose the join or exit message section.",
         "Pick a destination channel and adjust the message or card layout.",
         "Preview the result, then save or post only through the supplied confirmation controls."),
        "Saved welcome or exit settings determine future messages; previews are not live join events.",
        "Ensure the bot can see and post in the selected channels."),
    "member_setup_manager": HelpGuide(
        "Set the prerequisites members must complete to receive access.",
        ("Open Member Setup Manager and review the existing access role and eligibility rules.",
         "Choose the prerequisites and any Strict Gate protections needed for the server.",
         "Review the impact on currently unverified members before saving or enabling the gate."),
        "The access gate uses the server's configured roles and prerequisites.",
        "Changing access rules can affect channel visibility. Review permissions before publishing."),
    "protection": HelpGuide(
        "Configure safeguards against spam, harmful links, invitations and destructive actions.",
        ("Open Protection and choose Spam Guard, Invite Shield, AntiNuke or another protection area.",
         "Review which actions are monitored and what happens when a rule triggers.",
         "Configure exceptions and staff alerts, then test the rule on a safe example before relying on it."),
        "Only enabled safeguards with valid permissions can enforce or alert.",
        "Review exceptions for approved advertising bots; knowing another member is not proof of misconduct."),
    "members_moderation": HelpGuide(
        "Review members, activity, account history and moderation tools.",
        ("Open Members & Moderation, then Member Command Center.",
         "Choose Live Members for a current account or Intelligence for stored history and review evidence.",
         "Inspect evidence and choose a specific moderation action only when justified; confirm any destructive action."),
        "Member history and connection information are staff review leads, not automatic proof of guilt.",
        "Ban and cleanup controls require staff authority and must never be tested on real members casually."),
    "roles_profiles": HelpGuide(
        "Create and maintain Discord roles and member profile options.",
        ("Open Roles & Profiles.",
         "Choose whether you need a server role, role assignment, or member-facing profile control.",
         "Use the role picker, review the role hierarchy and permissions, then confirm changes."),
        "Role changes take effect according to Discord's role hierarchy and the bot's permissions.",
        "Avoid granting administrator or sensitive permissions to self-assignable roles."),
    "profile_builder": HelpGuide(
        "Choose which badges, profile tags and optional roles members can pick.",
        ("Open Profile Builder and review the choices currently offered to members.",
         "Select approved profile options or cosmetics and the roles allowed for those choices.",
         "Preview the member-facing panel and publish only when the choices are correct."),
        "Members can choose from the approved options made available to them.",
        "Keep staff and protected roles out of self-service cosmetic options."),
    "community_pings_manager": HelpGuide(
        "Organize opt-in roles for events, sessions and server announcements.",
        ("Open Community & Pings Manager.",
         "Choose a role group or notification topic and set allowed opt-in choices.",
         "Review any prerequisites or conflicting role choices, then save and test the member picker."),
        "Members manage optional community pings through their own preference screens.",
        "Do not use this to bypass Discord's role hierarchy or ping people who opted out."),
    "community_tools": HelpGuide(
        "Create a poll, manage a sticky post, or use the server's utility tools.",
        ("Open Community Tools and select Polls, Stickies or the utility you want.",
         "For a posted item, choose the channel and review the content or permissions.",
         "Preview or confirm the action, then check the result in that channel."),
        "Utilities that only answer questions do not change the server; posting tools need an explicit action.",
        "Staff-only tools keep their existing permission checks."),
    "share_router": HelpGuide(
        "Choose which member posts are shared and where media goes.",
        ("Open Share Router and choose Where Videos Go to set Private DM, Server Channel or both.",
         "Choose Share From Existing Channel or add forwarding rules; select which posts qualify.",
         "Save This Choice, test a supported link, and check the configured destination."),
        "Regular source posts remain in their channels unless an independent moderation rule removes them.",
        "DM delivery is a destination choice, not a reason to message every member automatically."),
    "share_downloads": HelpGuide(
        "Manage optional private video-request channels and hosted viewing links.",
        ("Open Share Router Downloads.",
         "Choose the request channel or download settings, then review source tests and expiry controls.",
         "Test a permitted video link and confirm the resulting delivery method before enabling it for members."),
        "Downloads and viewing links are controlled separately from ordinary Share Router forwarding.",
        "A provider may fail or block a particular video even when the bot is healthy."),
    "toke_settings": HelpGuide(
        "Configure community sesh cards, timing and attendance.",
        ("Open Toke Sesh Settings.",
         "Review meetup timing, session limits, and voice-presence options.",
         "Save the chosen settings and test with a regular /toke session."),
        "The session's live attendance is separate from permanent role or notification settings.",
        "Only ping members through their configured opt-in community roles."),
    "somewhere_420": HelpGuide(
        "See where it is 4:20 and join or start a session.",
        ("Open It's 4:20 Somewhere and use Refresh World Clock if needed.",
         "Choose Start /toke or Quick Text-Only Sesh if you want to celebrate.",
         "Use My 4:20 Preferences to manage your participation and notification choices."),
        "World-clock viewing alone does not start or send a session.",
        "Use the staff-only server settings for server-wide posting behavior."),
    "community_hub": HelpGuide(
        "Find gaming groups and arrange sessions with other players.",
        ("Open Community Hub and select Find Players, My Groups, or Start Gaming Session.",
         "For discovery, choose your game and filters; for hosting, review the group's visibility settings.",
         "Confirm a group or session, then use its posted controls to manage participants."),
        "Public discovery depends on host and server opt-in; private groups are not automatically made public.",
        "Use Match Safety and report tools when dealing with unfamiliar players."),
    "tickets": HelpGuide(
        "Set up support tickets and review open requests.",
        ("Open Ticket Operations and choose Public Panel to configure where members create tickets.",
         "Use Categories, Intake & Routing or Staff Alerts to organize requests and notifications.",
         "Post or update the public panel, then use Active or Unassigned to manage staff work."),
        "Members use the posted ticket panel; staff use the private ticket controls.",
        "Choose staff roles and privacy carefully before posting a ticket panel."),
    "embed_studio": HelpGuide(
        "Build an announcement or styled Discord post in a few steps.",
        ("Open Post Builder and choose a layout.",
         "Use Write Message, Look & Images and optionally Add Buttons to design it.",
         "Use Preview & Post, inspect the draft and select its destination before publishing."),
        "Saved Designs and Edit Existing Post can be used without starting over.",
        "Previewing does not publish a message; confirm its channel and mention settings before posting."),
    "server_design": HelpGuide(
        "Edit channel naming and the server's visual appearance.",
        ("Open Server Design and choose the channel or naming area you want to update.",
         "Use the shared Search-Safe picker to choose the exact role or channel.",
         "Preview the name or style and confirm the change."),
        "Only the selected server objects are changed.",
        "A decorated channel name should remain searchable and readable to members."),
    "card_assets": HelpGuide(
        "Upload artwork or a licensed font for welcome and exit cards.",
        ("Open Card Assets to review the supported choices.",
         "Use /dank upload and choose Join Card Background, Exit Card Background or Custom Card Font, then attach a compatible file.",
         "Return to Welcome & Exit Studio and preview the updated card."),
        "Uploaded assets change card appearance only when the relevant card uses them.",
        "Discord buttons cannot open an attachment picker, so uploads require the /dank upload command."),
    "live_captions": HelpGuide(
        "Show speech as text in supported Discord voice sessions.",
        ("Open Live Captions and review whether captions are available on your server.",
         "Follow the displayed consent and channel selection steps to start a session.",
         "Check the live text output and use the session controls to stop when finished."),
        "Caption quality depends on voice audio and configured services.",
        "Follow the feature's consent requirements and do not assume captions are private DMs."),
    "voice_safety": HelpGuide(
        "Review optional consent-based voice moderation and its alerts.",
        ("Open AI Voice Safety and inspect its server status and monitored-chat settings.",
         "Set authorized channels, consent choices and where staff alerts should appear.",
         "Use the built-in diagnostic or controlled test path before depending on alerts."),
        "Voice monitoring applies only when configured and permitted.",
        "Do not assume it is recording or detecting voice until the live receiver is verified."),
    "logs_activity": HelpGuide(
        "Choose where moderation, member join/leave and other activity records appear.",
        ("Open Logs & Activity.",
         "Pick the event type and the appropriate private staff channel.",
         "Save the destination and confirm the bot can see and post in that channel."),
        "Logs record supported events occurring after the relevant settings are active.",
        "Avoid routing sensitive moderation information to public channels."),
    "server_stats": HelpGuide(
        "Display useful member and ticket counts in the server.",
        ("Open Server Stats and choose the counter you want to set up.",
         "Choose its category or existing channel and verify the bot's permissions.",
         "Refresh or repair a counter if it is stale or its channel was deleted."),
        "Configured counters reflect the bot's latest supported server data.",
        "Do not assume a missing member-intent or inaccessible channel can be inferred perfectly."),
    "status": HelpGuide(
        "See whether the bot is responding and what runtime is loaded.",
        ("Open Status.",
         "Review the service state, runtime information and any warnings.",
         "Compare the reported state with the affected feature before assuming the whole bot is offline."),
        "A healthy gateway connection does not guarantee that every database or provider works.",
        "For a failing service, use Diagnostics or that feature's own health check."),
    "diagnostics": HelpGuide(
        "Investigate bot health without changing live settings.",
        ("Open Diagnostics.",
         "Run the available read-only checks for the service that is failing.",
         "Review the reported failure category and use the corresponding feature's repair controls only when needed."),
        "Diagnostics report what could be checked and may mark some results incomplete.",
        "Never paste database passwords, tokens, or private URLs into Discord."),
    "my_profile": HelpGuide(
        "Set up how your member profile appears to other people.",
        ("Open My Profile.",
         "Edit your signature, appearance, privacy or supported platforms.",
         "Preview and save your changes before returning to the server."),
        "Your saved choices affect the member profile you expose through the bot.",
        "Server-managed roles and other members' preferences are separate."),
    "my_member_setup": HelpGuide(
        "Find out which join requirements you still need to complete.",
        ("Open My Member Setup.",
         "Read the checklist for outstanding verification or required member choices.",
         "Open each incomplete requirement, complete it, then refresh the checklist."),
        "The list updates based on the server's configured eligibility rules.",
        "Only server staff can change what counts as a required prerequisite."),
    "profile_tags": HelpGuide(
        "Choose from the server's available cosmetic or profile role options.",
        ("Open Profile Tags & Cosmetics.",
         "Select a category and the optional tags or roles you want.",
         "Confirm your choices and return to My Profile to see the result."),
        "Only server-approved options are offered.",
        "Protected and staff roles cannot be assigned through cosmetic selection."),
    "my_community_pings": HelpGuide(
        "Opt into or out of optional game, event and session notifications.",
        ("Open My Community & Pings.",
         "Choose the groups or interests you want to hear about.",
         "Save the selection and reopen it if you want to turn off a notification group."),
        "Only supported opt-in notification roles are changed.",
        "Mandatory server messages and role prerequisites may remain separate."),
    "help": HelpGuide(
        "Learn how to use features without memorizing slash commands.",
        ("Choose a topic and tap a feature's Guide button.",
         "Read its steps, expected result and any important warning, then choose Open Feature.",
         "Use Find Help for specific questions or Browse Guides to find every supported tool."),
        "Help only explains features; actions happen inside their normal authorized screens."),
}


def validate_help_guides() -> tuple[str, ...]:
    known = {feature.key for feature in FEATURES}
    errors = []
    for key in sorted(known - HELP_GUIDES.keys()):
        errors.append(f"missing help guide: {key}")
    for key in sorted(HELP_GUIDES.keys() - known):
        errors.append(f"retired help destination: {key}")
    for key, guide in HELP_GUIDES.items():
        if not guide.purpose.strip() or not guide.outcome.strip():
            errors.append(f"{key}: explanation/outcome missing")
        if not (2 <= len(guide.steps) <= 4) or any(not step.strip() for step in guide.steps):
            errors.append(f"{key}: expected 2-4 actionable steps")
    return tuple(errors)


HELP_GUIDE_ERRORS = validate_help_guides()
if HELP_GUIDE_ERRORS:
    raise RuntimeError("Invalid Dank Shield help: " + "; ".join(HELP_GUIDE_ERRORS))


__all__ = ["HelpGuide", "HELP_GUIDES", "HELP_GUIDE_ERRORS", "validate_help_guides"]
