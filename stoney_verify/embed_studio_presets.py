"""Small, editable professional starter layouts for the Discord Embed Studio.

Each preset stays text-only by default. No mention pings or nonconsensual
publishing, and templates can always be customized per guild.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .embed_studio_core import new_project, validate_project

PRESETS: tuple[tuple[str, str], ...] = (
    ("announcement", "Announcement"),
    ("rules", "Server Rules"),
    ("event", "Event / Meetup"),
    ("info", "Server Information"),
    ("faq", "FAQ / Help"),
    ("promo", "Feature Showcase"),
)


def project_for_preset(key: str) -> dict[str, Any]:
    if key not in {item[0] for item in PRESETS}:
        raise ValueError("Unknown starter layout.")
    p = new_project()
    e = p["embeds"][0]
    e["color"] = "5865F2"
    if key == "announcement":
        e.update(title="📣 Community Announcement", description="What's new? Add the details here.", footer="Stay tuned for updates.")
    elif key == "rules":
        e.update(
            title="📜 Server Guidelines",
            description="Here's what everyone should know before getting started.",
            fields=[
                {"name": "Be respectful", "value": "Explain your community's expectations.", "inline": False},
                {"name": "Keep the server organized", "value": "Add posting and channel guidelines.", "inline": False},
                {"name": "Need help?", "value": "Point members toward support or moderators.", "inline": False},
            ],
        )
    elif key == "event":
        e.update(
            title="📅 Community Event",
            description="What are we doing? Add the details.",
            fields=[
                {"name": "When", "value": "Date and time, including timezone", "inline": True},
                {"name": "Where", "value": "Voice channel, server, or location", "inline": True},
                {"name": "How to join", "value": "Explain how members participate.", "inline": False},
            ],
        )
    elif key == "info":
        e.update(
            title="ℹ️ About Our Community",
            description="A quick introduction to what our server offers.",
            fields=[
                {"name": "Get started", "value": "Where new members should begin", "inline": False},
                {"name": "Explore", "value": "Popular channels and features", "inline": False},
                {"name": "Ask questions", "value": "Where to get assistance", "inline": False},
            ],
        )
    elif key == "faq":
        e.update(
            title="❓ Frequently Asked Questions",
            description="Find quick answers to the questions we get the most.",
            fields=[
                {"name": "How do I get started?", "value": "Write your answer here.", "inline": False},
                {"name": "Where do I get help?", "value": "Write your answer here.", "inline": False},
            ],
        )
    elif key == "promo":
        e.update(
            title="✨ Discover What's Available",
            description="Show your community's highlights in one easy-to-read place.",
            fields=[
                {"name": "Featured", "value": "Describe an activity or feature.", "inline": True},
                {"name": "What's next", "value": "Describe upcoming plans.", "inline": True},
            ],
            footer="Explore the server for more.",
        )
    return validate_project(deepcopy(p))
