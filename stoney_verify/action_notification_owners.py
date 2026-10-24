from __future__ import annotations

"""Read-only map of existing notification owners for Action Center navigation.

These entries are NOT activation, delivery, schedule, consent, or completion
authority. Every feature keeps its existing policy, worker and data store.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class NotificationOwner:
    key: str
    label: str
    emoji: str
    description: str
    feature_route: str
    owner_module: str
    delivery_contract: str


# Routes refer to registered canonical Dank Shield feature destinations.
# Directory navigation never calls a worker or creates another sender.
NOTIFICATION_OWNERS: tuple[NotificationOwner, ...] = (
    NotificationOwner(
        "verification", "Verification Reminders", "📩",
        "Server-managed Simple Verify nudges automatically cover Unverified members; stops after verification.",
        "verification_reminders", "stoney_verify.verification_reminders",
        "Existing server-enabled worker; all pending Unverified members are eligible without personal opt-in.",
    ),
    NotificationOwner(
        "activity", "Member Activity Notices", "👥",
        "Scheduled member-review DMs, response buttons and notice results stay with Members.",
        "members_moderation", "stoney_verify.commands_ext.public_members_group",
        "Existing independent notice worker; use /members notices for results.",
    ),
    NotificationOwner(
        "tickets", "Ticket Reminders", "🎫",
        "SLA alerts, inactivity reminders and closing rules remain with Tickets.",
        "tickets", "stoney_verify.workers.ticket_automation_worker",
        "Existing ticket automation worker; own enablement and state.",
    ),
    NotificationOwner(
        "quiet", "Quiet Server Notices", "🌙",
        "Activity-triggered channel notices remain managed through Community Tools.",
        "community_tools", "stoney_verify.community_tools_runtime",
        "Existing community runtime; open Quiet Server Notice from Community Tools.",
    ),
    NotificationOwner(
        "world420", "4:20 Notifications", "🌍",
        "Personal and guild-local reminders remain owned by It's 4:20 Somewhere.",
        "somewhere_420", "stoney_verify.somewhere_420_runtime",
        "Existing timezone-aware bounded claim and delivery worker.",
    ),
    NotificationOwner(
        "toke", "/toke Session Reminders", "🍃",
        "Opt-in one-time session DMs belong to the session that created them.",
        "toke_settings", "stoney_verify.toke_sessions",
        "Existing session lifecycle; use the sesh itself for member reminder opt-in.",
    ),
)


def find_notification_owner(key: str) -> NotificationOwner | None:
    return next((item for item in NOTIFICATION_OWNERS if item.key == key), None)
