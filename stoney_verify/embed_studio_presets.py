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
    ("minecraft", "Minecraft • Server Relaunch"),
    ("modded", "Modded • Adventure Awaits"),
    ("game_night", "Multiplayer • Game Night"),
    ("tournament", "Tournament • Join the Challenge"),
    ("server_update", "Game Server • Patch Notes"),
    ("recruitment", "Players Wanted • Join the Crew"),
)

# Short mobile picker descriptions make layouts distinguishable before loading.
PRESET_DESCRIPTIONS: dict[str, str] = {
    "announcement": "A clean, headline-first community update",
    "rules": "Readable rules with separated sections",
    "event": "When, where, and how to join",
    "info": "Introduce your server and what to explore",
    "faq": "Clear questions and quick answers",
    "promo": "Showcase the best things in your community",
    "minecraft": "Bold comeback with address and editions",
    "modded": "Showcase mods, features and world progression",
    "game_night": "Party up with a scheduled multiplayer event",
    "tournament": "Compete with clear rules and prizes",
    "server_update": "What's new, fixed, and coming next",
    "recruitment": "Attract players with a clear join guide",
}


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
    elif key == "minecraft":
        e.update(
            color="57C56F",
            author_name="MINECRAFT • THE WORLD IS OPEN",
            title="⛏️ WE'RE BACK • BUILD YOUR NEXT ADVENTURE",
            description=(
                "**A fresh chapter is here.** Gather your crew, claim your spot, "
                "and explore a world worth returning to.\n\n"
                "🌲 **Survive. Explore. Create.**\n"
                "Make the announcement yours: replace the sample address, "
                "supported editions, and features before posting."
            ),
            fields=[
                {"name": "🌍 SERVER ADDRESS", "value": "`play.example.net:25565`  *(replace with your real address)*", "inline": False},
                {"name": "🎮 SUPPORTED EDITIONS", "value": "List only editions you actually support.", "inline": True},
                {"name": "🧩 WHAT'S INSIDE", "value": "List verified mods, plugins, worlds and events.", "inline": True},
                {"name": "🚪 START PLAYING", "value": "Open Multiplayer → Add Server → enter your address.", "inline": False},
            ],
            footer="Add your banner under Look & Images • Edit every detail before posting",
        )
    elif key == "modded":
        e.update(
            color="9C6AFF",
            author_name="MODDED SURVIVAL • NEW HORIZONS",
            title="🧬 YOUR NEXT MODDED ADVENTURE STARTS HERE",
            description=(
                "**Not your average survival world.**\n"
                "A place for ambitious builders, explorers and curious players.\n\n"
                "⚙️ **Custom mechanics** • 🧭 **Big discoveries** • 🤝 **A real community**\n"
                "Replace these highlights with what your server actually offers."
            ),
            fields=[
                {"name": "🧩 FEATURED MODS", "value": "List confirmed mods and their required versions.", "inline": False},
                {"name": "🗺️ WORLD & PROGRESSION", "value": "Explain the gameplay loop and any reset policy.", "inline": False},
                {"name": "📥 HOW TO CONNECT", "value": "Server address, version and required modpack/install steps.", "inline": False},
            ],
            footer="Add a gameplay screenshot or banner • All details are editable",
        )
    elif key == "game_night":
        e.update(
            color="00B8D9",
            author_name="COMMUNITY GAME NIGHT",
            title="🎮 SQUAD UP • THE LOBBY IS CALLING",
            description=(
                "**No solo queues tonight.** Grab your friends and join the fun.\n\n"
                "🎧 Chill voice chat • 🎯 Team up • 🎉 Make some memories"
            ),
            fields=[
                {"name": "📅 WHEN", "value": "Date, time and timezone", "inline": True},
                {"name": "🕹️ GAME / MODE", "value": "Game, platform and lobby details", "inline": True},
                {"name": "🔊 MEET HERE", "value": "Voice channel or event link (edit this field)", "inline": False},
                {"name": "✅ HOW TO RSVP", "value": "Explain how members sign up or join in.", "inline": False},
            ],
            footer="Edit details • Use Add Buttons for an HTTPS event link",
        )
    elif key == "tournament":
        e.update(
            color="FFC44D",
            author_name="COMPETITIVE EVENT • COMMUNITY CHALLENGE",
            title="🏆 THE CHALLENGE IS ON • ARE YOU IN?",
            description=(
                "**Bring your best game.** Join the competition, earn bragging rights "
                "and have a good time.\n\n"
                "⚔️ **Play fair. Play smart. Play together.**"
            ),
            fields=[
                {"name": "🎮 GAME & FORMAT", "value": "Game, bracket or match format", "inline": False},
                {"name": "⏰ START TIME", "value": "Date, time and timezone", "inline": True},
                {"name": "🎁 PRIZES", "value": "List confirmed rewards or say 'for fun'.", "inline": True},
                {"name": "📝 RULES & ENTRY", "value": "Eligibility, registration, and where to check in", "inline": False},
            ],
            footer="Only announce real prizes and confirmed event times",
        )
    elif key == "server_update":
        e.update(
            color="F2A65A",
            author_name="SERVER UPDATE • RELEASE NOTES",
            title="🛠️ WORLD UPDATE • HERE'S WHAT CHANGED",
            description=(
                "**New adventures, smoother gameplay.**\n"
                "Keep your players in the loop with a quick, readable rundown."
            ),
            fields=[
                {"name": "✨ NEW", "value": "List features confirmed in this release.", "inline": False},
                {"name": "🔧 FIXED", "value": "List verified fixes and stability improvements.", "inline": False},
                {"name": "📦 COMPATIBILITY", "value": "Supported game versions, clients and mod requirements.", "inline": False},
                {"name": "⏭️ NEXT", "value": "Only list plans that have actually been announced.", "inline": False},
            ],
            footer="Edit the patch details and add a screenshot before posting",
        )
    elif key == "recruitment":
        e.update(
            color="FF669E",
            author_name="PLAYERS WANTED • COMMUNITY RECRUITMENT",
            title="🚀 YOUR NEXT GAMING CREW IS HERE",
            description=(
                "**Looking for somewhere to play, build and belong?**\n"
                "Meet new people, find a squad and jump into your next adventure.\n\n"
                "🎮 **Play together** • 💬 **Meet the crew** • 🌟 **Make it yours**"
            ),
            fields=[
                {"name": "🌟 WHY JOIN?", "value": "Share three real things your server does well.", "inline": False},
                {"name": "🕹️ GAMES & PLATFORMS", "value": "List supported games, platforms and editions.", "inline": False},
                {"name": "🔗 HOW TO JOIN", "value": "Invite link, server address or onboarding instructions.", "inline": False},
            ],
            footer="Avoid fake player counts or unsupported gameplay claims",
        )
    return validate_project(deepcopy(p))
