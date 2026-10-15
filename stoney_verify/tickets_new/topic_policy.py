"""Member-visible ticket topics. Private metadata must stay in the ticket DB.

The Discord channel topic is readable by any member who can view the channel.
Keep this text free of owner IDs, ticket counters, categories and ghost flags.
Legacy key=value topics remain supported by compatibility readers.
"""

PUBLIC_TICKET_TOPIC = (
    "Dank Shield support ticket | Describe your request here. "
    "Staff will help you in this channel."
)


def is_public_ticket_topic(value: object) -> bool:
    """Identify a bot-written, public-only ticket topic."""
    return str(value or "").strip().casefold() == PUBLIC_TICKET_TOPIC.casefold()
