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
    ("movie_night", "Movie Night • Lights Out"),
    ("watch_party", "Live Watch Party • Tune In"),
    ("trivia", "Trivia Night • Brain Battle"),
    ("karaoke", "Karaoke • Open Mic"),
    ("hangout", "Community Hangout • Chill VC"),
    ("giveaway", "Giveaway • Community Rewards"),
    ("ama", "AMA • Ask Us Anything"),
    ("art_contest", "Creative Contest • Show Your Work"),
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
    "movie_night": "Movie, watch location, time and access requirements",
    "watch_party": "Live streams, esports and shared watch sessions",
    "trivia": "Questions, teams, times and how to play",
    "karaoke": "Voice channel, song queue and microphone rules",
    "hangout": "Casual voice chat and a welcoming event invite",
    "giveaway": "Verified prize, entry rules and close time",
    "ama": "Hosted questions, speakers and how to participate",
    "art_contest": "Creative theme, submissions, deadline and judging",
}


# Event copy stays generic until staff fill in real dates, channel locations,
# lawful media access and prizes. These are drafts, not scheduled Discord events.
_EVENT_PRESET_SPECS: dict[str, dict[str, str]] = {
    "movie_night": {
        "color": "D86EFF",
        "author_name": "COMMUNITY CINEMA • MOVIE NIGHT",
        "title": "🎬 LIGHTS DOWN • MOVIE NIGHT IS ON",
        "description": (
            "**Grab your popcorn, find a seat and join the watch party.**\n\n"
            "🍿 Movie picks • 💬 Reactions • 🎞️ Good company\n"
            "Set the actual movie and start time before announcing."
        ),
        "extra": "🎞️ MOVIE PICK",
        "hint": "Movie title, rating and any content notes.",
        "footer": "Use only a stream or watch method you are authorized to share",
    },
    "watch_party": {
        "color": "38BDF8",
        "author_name": "LIVE WATCH PARTY • COMMUNITY STREAM",
        "title": "📺 WATCH TOGETHER • THE SHOW STARTS HERE",
        "description": (
            "**Big moment? Better with the whole server.**\n\n"
            "🎥 Shared reactions • 🎙️ Live chat • ⚡ Hype together"
        ),
        "extra": "📡 WHAT WE'RE WATCHING",
        "hint": "Stream, match, creator or event and where viewers can access it.",
        "footer": "Check stream permissions and viewer access before sharing",
    },
    "trivia": {
        "color": "F5C242",
        "author_name": "TRIVIA NIGHT • COMMUNITY QUIZ",
        "title": "🧠 TRIVIA NIGHT • THINK YOU KNOW IT ALL?",
        "description": (
            "**Teams, tough questions, and excellent wrong answers.**\n\n"
            "❓ Trivia rounds • 🏆 Friendly competition • 🤝 New teammates"
        ),
        "extra": "🎯 TOPICS & FORMAT",
        "hint": "Topics, teams, scoring and approximate rounds.",
        "footer": "No automatic quiz or prize is created by this layout",
    },
    "karaoke": {
        "color": "FB7185",
        "author_name": "OPEN MIC • KARAOKE NIGHT",
        "title": "🎤 TAKE THE MIC • KARAOKE NIGHT",
        "description": (
            "**Your stage, your song, your moment.**\n\n"
            "🎶 Sing together • 💖 Cheer each other on • 🎙️ No pressure"
        ),
        "extra": "🎵 SONG QUEUE",
        "hint": "How to queue songs and who is hosting; follow music rights/rules.",
        "footer": "Be respectful • Use audio you are allowed to share",
    },
    "hangout": {
        "color": "64D3A1",
        "author_name": "COMMUNITY HANGOUT • OPEN LOUNGE",
        "title": "💬 COME HANG OUT • EVERYONE'S INVITED",
        "description": (
            "**No competition, no schedule pressure, just community time.**\n\n"
            "☕ Meet the crew • 🎧 Chill in voice • ✨ Stay as long as you like"
        ),
        "extra": "🛋️ THE VIBE",
        "hint": "Topics, activities and which members the hangout is for.",
        "footer": "Welcome newcomers and follow the server rules",
    },
    "giveaway": {
        "color": "FCD34D",
        "author_name": "COMMUNITY REWARDS • GIVEAWAY",
        "title": "🎁 COMMUNITY GIVEAWAY • JOIN THE FUN",
        "description": (
            "**A little something for our community.**\n\n"
            "Check the verified prize, eligibility, deadline and official entry rules."
        ),
        "extra": "🎁 CONFIRMED PRIZE & RULES",
        "hint": "Only list a real prize, eligibility, entry method and winner process.",
        "footer": "No prizes, entries or winners are automated by this draft",
    },
    "ama": {
        "color": "A78BFA",
        "author_name": "LIVE Q&A • ASK ME ANYTHING",
        "title": "❓ ASK US ANYTHING • COMMUNITY Q&A",
        "description": (
            "**Get answers from the people behind the community.**\n\n"
            "💡 Questions welcome • 🎙️ Live discussion • 🤝 Meet the hosts"
        ),
        "extra": "🎙️ HOST & TOPICS",
        "hint": "Confirmed guests or hosts, topics and question guidelines.",
        "footer": "Add the real event date and hosts before posting",
    },
    "art_contest": {
        "color": "FF83C4",
        "author_name": "CREATOR SHOWCASE • COMMUNITY CHALLENGE",
        "title": "🎨 SHOW WHAT YOU'VE GOT • CREATOR CONTEST",
        "description": (
            "**Artists, builders, editors and makers, this one's for you.**\n\n"
            "🖼️ Share your work • 👀 Discover talent • 💫 Inspire the crew"
        ),
        "extra": "🖌️ THEME, RULES & JUDGING",
        "hint": "Theme, allowed formats, submission deadline, prizes and judging.",
        "footer": "Only announce confirmed rewards, dates and entry rules",
    },
}


def _event_layout(key: str) -> dict[str, Any]:
    spec = _EVENT_PRESET_SPECS[key]
    return {
        "color": spec["color"],
        "author_name": spec["author_name"],
        "title": spec["title"],
        "description": spec["description"],
        "fields": [
            {"name": "📅 WHEN", "value": "Add date, time and timezone.", "inline": True},
            {"name": "📍 WHERE", "value": "Discord voice/text channel or event location.", "inline": True},
            {"name": spec["extra"], "value": spec["hint"], "inline": False},
            {"name": "✅ HOW TO JOIN", "value": "RSVP instructions or how members can take part.", "inline": False},
        ],
        "footer": spec["footer"],
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
    elif key in _EVENT_PRESET_SPECS:
        e.update(_event_layout(key))
    return validate_project(deepcopy(p))
