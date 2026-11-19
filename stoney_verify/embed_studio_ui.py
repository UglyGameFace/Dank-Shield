"""One menu-first Embed Studio shared by Community Tools and Server Setup.

No new slash-command root, background worker, or global draft cache. A draft
belongs to its staff interaction; named templates and bot post references use
the existing per-guild atomic config store.
"""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
from dataclasses import dataclass, field
from typing import Any, Callable
from datetime import datetime, timezone
import asyncio
import secrets
import time

import discord

from .ui.current_channel import current_text_channel
from .ui.picker import DankPickerView, DankRoleSelect, chunk_choices, make_choice

from .embed_studio_core import (
    STUDIO_KEY, MAX_EMBEDS, MAX_FIELDS, MAX_TEMPLATES,
    StudioValidationError, new_project, validate_project, render_embeds,
    link_view, default_embed, parse_library, library_payload, https_url, draft_snapshot,
    MAX_PORTABLE_BYTES, export_portable_project, import_portable_project,
    MAX_THEMES, THEME_PRESETS, theme_name, theme_from_project, apply_theme,
    MAX_MEDIA_ITEMS, MAX_TEMPLATES_BYTES, SMART_TEXT_TOKENS, validate_media_item, apply_media_url, resolve_smart_text,
)

_MENTIONS = discord.AllowedMentions.none()


@dataclass
class StudioSession:
    guild_id: int
    owner_id: int
    project: dict[str, Any] = field(default_factory=new_project)
    channel_id: int = 0
    active: int = 0
    post_key: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)
    saved_version: int = 0
    dirty: bool = False
    save_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def apply(self, mutation: Callable[[dict[str, Any]], None]) -> None:
        candidate = deepcopy(self.project)
        mutation(candidate)
        checked = validate_project(candidate)
        self.history.append(self.project)
        self.history = self.history[-10:]
        self.project = checked
        self.dirty = True
        self.active = min(self.active, len(self.project["embeds"]) - 1)

    def undo(self) -> bool:
        if not self.history:
            return False
        self.project = self.history.pop()
        self.dirty = True
        self.active = min(self.active, len(self.project["embeds"]) - 1)
        return True


def _staff(interaction: discord.Interaction) -> bool:
    member = interaction.user
    guild = interaction.guild
    return bool(
        guild is not None and isinstance(member, discord.Member)
        and (
            int(member.id) == int(guild.owner_id)
            or member.guild_permissions.administrator
            or member.guild_permissions.manage_guild
        )
    )


async def _say(interaction: discord.Interaction, content: str, **kwargs: Any) -> None:
    kwargs.setdefault("ephemeral", True)
    kwargs.setdefault("allowed_mentions", _MENTIONS)
    if interaction.response.is_done():
        await interaction.followup.send(content, **kwargs)
    else:
        await interaction.response.send_message(content, **kwargs)


class _StudioView(discord.ui.View):
    def __init__(self, session: StudioSession, *, timeout: int = 900) -> None:
        super().__init__(timeout=timeout)
        self.session = session

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (
            interaction.guild is None or int(interaction.guild.id) != self.session.guild_id
            or int(interaction.user.id) != self.session.owner_id
        ):
            await _say(interaction, "Open your own Embed Studio in the server to use these controls.")
            return False
        if not _staff(interaction):
            await _say(interaction, "Embed Studio needs Manage Server or Administrator access.")
            return False
        return True


async def _ack_editor_navigation(interaction: discord.Interaction) -> None:
    """Claim the component before rendering or any potentially slow autosave.

    The editor uses a transient, private View; never replay a stale click or
    acknowledge it twice. Discord panel updates use a deferred message update
    while modal/command origins need a private thinking response.
    """
    if interaction.response.is_done():
        return
    if interaction.message is not None:
        await interaction.response.defer()
    else:
        await interaction.response.defer(ephemeral=True, thinking=True)


def _summary(session: StudioSession) -> discord.Embed:
    """A short, actionable checklist rather than a map of developer features."""
    project = session.project
    current = project["embeds"][session.active]
    has_text = bool(
        project["content"].strip()
        or any(
            (entry.get("title") or "").strip()
            or (entry.get("description") or "").strip()
            for entry in project["embeds"]
        )
    )
    is_edit = bool(session.post_key)
    destination = f"<#{session.channel_id}>" if session.channel_id else "Not selected yet"
    embed = discord.Embed(
        title="📣 Edit a Server Post" if is_edit else "📣 Create a Server Post",
        description=(
            "**Make a post in four easy steps**\n"
            "**1. Pick a Layout** (optional) or write your own.\n"
            "**2. Write Message** and add pictures or colors if you like.\n"
            "**3. Choose a Channel** with the button below.\n"
            "**4. Preview & Post** when you're happy with it.\n\n"
            "Saved designs, buttons, forms, and staff tools are in their own menus. "
            "**Nothing goes public until you confirm.**"
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Message",
        value=(
            ("✅ Text added" if has_text else "◻️ Add your message")
            + (f" • {len(project['embeds'])} card(s)" if len(project["embeds"]) > 1 else "")
            + (f" • {len(current['fields'])} extra section(s)" if current["fields"] else "")
        ),
        inline=True,
    )
    embed.add_field(name="Where to post", value=destination, inline=True)
    embed.add_field(
        name="What happens next",
        value=(
            "Review changes to your existing post."
            if is_edit else
            ("Ready to preview." if session.channel_id else "Choose a channel first.")
        ),
        inline=False,
    )
    embed.set_footer(text="Private controls expire after 15 minutes • Reopen Post Builder to resume your saved draft.")
    return embed

def _preview_embed(session: StudioSession) -> discord.Embed:
    return render_embeds(session.project)[session.active]


async def _home(interaction: discord.Interaction, session: StudioSession, *, notice: str = "") -> None:
    await _ack_editor_navigation(interaction)
    autosave_warning = await _autosave(interaction, session)
    message = (
        ("✅ " + notice + "\n" if notice else "")
        + ("⚠️ " + autosave_warning + "\n" if autosave_warning else "")
        + "Private editor • choose a section; Publish requires confirmation."
    )
    current = _preview_embed(session)
    summary = _summary(session)
    # Discord's 6000-character cap applies across every displayed embed.
    # A full-length single embed must not be made invalid by editor chrome.
    preview_embeds = [current] if len(current) + len(summary) > 6000 else [summary, current]
    kwargs = {
        "content": message,
        "embeds": preview_embeds,
        "view": StudioHomeView(session),
        "allowed_mentions": _MENTIONS,
    }
    if interaction.response.is_done():
        await interaction.edit_original_response(**kwargs)
    elif interaction.message is not None:
        await interaction.response.edit_message(**kwargs)
    else:
        await interaction.response.send_message(**kwargs, ephemeral=True)


def _error(exc: Exception) -> str:
    if isinstance(exc, StudioValidationError):
        return str(exc)
    return "The draft could not be updated. Nothing was published."


async def _select_studio_destination(
    interaction: discord.Interaction, session: StudioSession,
    channel: discord.TextChannel,
) -> None:
    if interaction.guild is None or channel.guild.id != session.guild_id:
        return await _say(interaction, "Choose a text channel in this server.")
    session.channel_id = int(channel.id)
    session.dirty = True
    if session.post_key:
        session.post_key = ""  # Changing destination must never edit the old post.
    await _home(interaction, session, notice=f"Channel set to {channel.mention}. No post was sent.")


class _StudioPickerView(DankPickerView):
    """House picker bound to an Embed Studio session.

    Mirrors _StudioView's owner + staff gate, and pages long choice lists
    with chunk_choices so every option stays reachable past Discord's
    25-option select limit (a server can have well over 100 text channels).
    """

    def __init__(
        self, session: StudioSession, *, choices: Any, on_pick: Any,
        custom_id: str, placeholder: str, title: str = "Embed Studio",
        on_home: Any = None, home_label: str = "Back", page: int = 0,
    ) -> None:
        all_choices = list(choices or [])
        pages = chunk_choices(all_choices) or [[]]
        page = max(0, min(int(page), len(pages) - 1))
        super().__init__(
            author_id=int(session.owner_id),
            choices=pages[page],
            on_pick=on_pick,
            custom_id=f"{custom_id}:{page}" if len(pages) > 1 else custom_id,
            placeholder=(
                f"{placeholder} • Page {page + 1}/{len(pages)}"
                if len(pages) > 1 else placeholder
            ),
            title=title,
            on_home=on_home,
            home_label=home_label,
        )
        self.session = session
        self._all_choices = all_choices
        self._picker_on_pick = on_pick
        self._base_custom_id = custom_id
        self._base_placeholder = placeholder
        self._picker_title = title
        self._picker_on_home = on_home
        self._picker_home_label = home_label
        self.page = page
        self.pages = len(pages)
        if len(pages) > 1:
            self.add_item(_StudioPickerPageButton(delta=-1, disabled=page <= 0))
            self.add_item(_StudioPickerPageButton(delta=1, disabled=page >= len(pages) - 1))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (
            interaction.guild is None or int(interaction.guild.id) != self.session.guild_id
            or int(interaction.user.id) != self.session.owner_id
        ):
            await _say(interaction, "Open your own Embed Studio in the server to use these controls.")
            return False
        if not _staff(interaction):
            await _say(interaction, "Embed Studio needs Manage Server or Administrator access.")
            return False
        return True

    def _for_page(self, page: int) -> "_StudioPickerView":
        return _StudioPickerView(
            self.session, choices=self._all_choices, on_pick=self._picker_on_pick,
            custom_id=self._base_custom_id, placeholder=self._base_placeholder,
            title=self._picker_title, on_home=self._picker_on_home,
            home_label=self._picker_home_label, page=page,
        )


class _StudioPickerPageButton(discord.ui.Button):
    def __init__(self, *, delta: int, disabled: bool) -> None:
        super().__init__(
            label="Previous" if delta < 0 else "Next",
            emoji="◀️" if delta < 0 else "▶️",
            style=discord.ButtonStyle.secondary,
            custom_id=(
                "dank:embed_studio:picker:prev" if delta < 0
                else "dank:embed_studio:picker:next"
            ),
            row=2, disabled=bool(disabled),
        )
        self.delta = int(delta)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, _StudioPickerView) or not await view.interaction_check(interaction):
            return
        page = max(0, min(view.page + self.delta, view.pages - 1))
        await interaction.response.edit_message(view=view._for_page(page))


class _StudioPickerOpenButton(discord.ui.Button):
    """Swaps the current studio menu for a house picker on the same message."""

    def __init__(
        self, session: StudioSession, *, label: str, emoji: str | None,
        custom_id: str, row: int, heading: str, description: str,
        build: Callable[[discord.Interaction], _StudioPickerView],
        style: discord.ButtonStyle = discord.ButtonStyle.primary,
    ) -> None:
        super().__init__(label=label, emoji=emoji, style=style, custom_id=custom_id, row=row)
        self.session = session
        self._heading = heading
        self._description = description
        self._build = build

    async def callback(self, interaction: discord.Interaction) -> None:
        await _menu(
            interaction, heading=self._heading, description=self._description,
            view=self._build(interaction),
        )


def _destination_choices(guild: discord.Guild) -> list[Any]:
    choices = []
    for channel in guild.text_channels:
        category = channel.category
        choices.append(make_choice(
            f"#{channel.name}", str(channel.id),
            description=category.name if category is not None else "No category",
        ))
    return choices


async def _pick_studio_destination(
    interaction: discord.Interaction, session: StudioSession, value: str,
) -> None:
    guild = interaction.guild
    try:
        channel = guild.get_channel(int(value)) if guild else None
    except (TypeError, ValueError):
        channel = None
    if not isinstance(channel, discord.TextChannel) or channel.guild.id != session.guild_id:
        return await _say(interaction, "Choose a normal text channel in this server.")
    await _select_studio_destination(interaction, session, channel)


def _destination_picker_view(
    interaction: discord.Interaction, session: StudioSession,
) -> _StudioPickerView:
    guild = interaction.guild
    return _StudioPickerView(
        session,
        choices=_destination_choices(guild) if guild is not None else [],
        on_pick=lambda pick_interaction, value: _pick_studio_destination(
            pick_interaction, session, value),
        custom_id="dank:embed_studio:destination:v1",
        placeholder="Choose a channel before you post…",
        title="Choose a Channel",
        on_home=lambda home_interaction: _home(home_interaction, session),
        home_label="Back to My Post",
    )


class StudioHomeView(_StudioView):
    """Keep the common posting path visible; group everything else by purpose."""

    def __init__(self, session: StudioSession) -> None:
        super().__init__(session)
        self.add_item(_StudioPickerOpenButton(
            session, label="Choose a Channel", emoji="🧭",
            custom_id="dank:embed_studio:destination:open:v1", row=4,
            heading="📍 Choose a Channel",
            description=(
                "Pick where this post will go. Every text channel in the "
                "server is listed in server order, with its category shown "
                "under the name. Choosing a channel never posts anything."
            ),
            build=lambda interaction: _destination_picker_view(interaction, session),
        ))

    @discord.ui.button(label="Pick a Layout", emoji="✨", style=discord.ButtonStyle.primary, row=0)
    async def starter_layouts(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _starters(interaction, self.session)

    @discord.ui.button(label="Write Message", emoji="✏️", style=discord.ButtonStyle.primary, row=0)
    async def write(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(StudioTextModal(self.session))

    @discord.ui.button(label="Look & Images", emoji="🎨", style=discord.ButtonStyle.secondary, row=0)
    async def design(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _design_tools(interaction, self.session)

    @discord.ui.button(label="Add Buttons", emoji="🔗", style=discord.ButtonStyle.secondary, row=1)
    async def actions(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _button_tools(interaction, self.session)

    @discord.ui.button(label="Saved Designs", emoji="💾", style=discord.ButtonStyle.secondary, row=1)
    async def saved_designs(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _saved_tools(interaction, self.session)

    @discord.ui.button(label="More Tools", emoji="🧰", style=discord.ButtonStyle.secondary, row=1)
    async def advanced(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _advanced_tools(interaction, self.session)

    @discord.ui.button(label="Edit Existing Post", emoji="🛠️", style=discord.ButtonStyle.secondary, row=2)
    async def edit_posted(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _published(interaction, self.session)

    @discord.ui.button(label="Undo", emoji="↩️", style=discord.ButtonStyle.secondary, row=2)
    async def undo(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not self.session.undo():
            return await _say(interaction, "No changes to undo.")
        await _home(interaction, self.session, notice="Last edit undone.")

    @discord.ui.button(label="Use This Channel", emoji="📍", style=discord.ButtonStyle.primary, row=3)
    async def use_this_channel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        channel = current_text_channel(interaction, require_bot_embed=True)
        if channel is None:
            return await _say(
                interaction,
                "Open Post Builder in the server text channel you want to use, "
                "or choose another with the channel picker below. "
                "Dank Shield needs View Channel, Send Messages and Embed Links."
            )
        await _select_studio_destination(interaction, self.session, channel)

    @discord.ui.button(label="Preview & Post", emoji="👁️", style=discord.ButtonStyle.success, row=2)
    async def preview(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _review(interaction, self.session)


class StudioDesignTools(_StudioView):
    @discord.ui.button(label="Colors & Style", emoji="🎨", style=discord.ButtonStyle.primary, row=0)
    async def appearance(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(StudioAppearanceModal(self.session))

    @discord.ui.button(label="Add Pictures", emoji="🖼️", style=discord.ButtonStyle.secondary, row=0)
    async def images(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(StudioImagesModal(self.session))

    @discord.ui.button(label="Saved Pictures", emoji="📷", style=discord.ButtonStyle.secondary, row=0)
    async def media(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _media_library(interaction, self.session)

    @discord.ui.button(label="Reuse Server Style", emoji="✨", style=discord.ButtonStyle.secondary, row=1)
    async def theme(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _themes(interaction, self.session)

    @discord.ui.button(label="Game Server Details", emoji="🎮", style=discord.ButtonStyle.primary, row=1)
    async def game_details(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(StudioGameServerModal(self.session))

    @discord.ui.button(label="Event Details", emoji="📅", style=discord.ButtonStyle.primary, row=1)
    async def event_details(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(StudioEventDetailsModal(self.session))

    @discord.ui.button(label="Back to My Post", emoji="◀️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


async def _design_tools(interaction: discord.Interaction, session: StudioSession) -> None:
    await _menu(
        interaction, heading="🎨 Make Your Post Look Good",
        description=(
            "Choose **Colors & Style** to change how the post looks. "
            "**Add Pictures** for new image links, **Saved Pictures** to reuse images, "
            "or **Reuse Server Style** for a look your staff already saved. "
            "**Game Server Details** quickly edits address, editions, mods and join steps. "
            "**Event Details** updates time, location, program and RSVP/entry instructions. "
            "None of these options posts anything."
        ),
        view=StudioDesignTools(session),
    )


class StudioButtonTools(_StudioView):
    @discord.ui.button(label="Website Buttons", emoji="🔗", style=discord.ButtonStyle.primary, row=0)
    async def links(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _links(interaction, self.session)

    @discord.ui.button(label="Let Members Pick Roles", emoji="🎭", style=discord.ButtonStyle.secondary, row=0)
    async def roles(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _role_controls(interaction, self.session)

    @discord.ui.button(label="Collect Answers", emoji="📝", style=discord.ButtonStyle.secondary, row=1)
    async def forms(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _form_builder(interaction, self.session)

    @discord.ui.button(label="Back to My Post", emoji="◀️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


async def _button_tools(interaction: discord.Interaction, session: StudioSession) -> None:
    await _menu(
        interaction, heading="🔗 What Should Members Be Able to Do?",
        description=(
            "**Website Buttons:** Open a safe web link.\n"
            "**Let Members Pick Roles:** Add opt-in roles, like game or notification roles.\n"
            "**Collect Answers:** Ask questions and receive private staff responses.\n\n"
            "These are optional. A simple announcement doesn't need any buttons."
        ),
        view=StudioButtonTools(session),
    )


class StudioSavedTools(_StudioView):
    @discord.ui.button(label="Saved Post Layouts", emoji="💾", style=discord.ButtonStyle.primary, row=0)
    async def templates(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _templates(interaction, self.session)

    @discord.ui.button(label="Restore My Draft", emoji="📂", style=discord.ButtonStyle.secondary, row=0)
    async def reload(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _reload_saved(interaction, self.session)

    @discord.ui.button(label="Share With Staff", emoji="👥", style=discord.ButtonStyle.secondary, row=1)
    async def team_review(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _team_review_home(interaction, self.session)

    @discord.ui.button(label="Import / Export", emoji="📦", style=discord.ButtonStyle.secondary, row=1)
    async def json(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _portable_menu(interaction, self.session)

    @discord.ui.button(label="Back to My Post", emoji="◀️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


async def _saved_tools(interaction: discord.Interaction, session: StudioSession) -> None:
    await _menu(
        interaction, heading="💾 Saved Designs & Staff Sharing",
        description=(
            "**Saved Post Layouts** are designs your team can reuse. "
            "**Restore My Draft** reloads your last saved edit. "
            "**Share With Staff** sends a design for another moderator to review. "
            "**Import / Export** is for moving a design file between servers or backups. "
            "Nothing is published from this menu."
        ),
        view=StudioSavedTools(session),
    )


class StudioAdvancedTools(_StudioView):
    @discord.ui.button(label="Extra Text Sections", emoji="📋", style=discord.ButtonStyle.secondary, row=0)
    async def fields(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _fields(interaction, self.session)

    @discord.ui.button(label="Multiple Post Cards", emoji="🧩", style=discord.ButtonStyle.secondary, row=0)
    async def embeds(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _embeds(interaction, self.session)

    @discord.ui.button(label="Automatic Server Details", emoji="🔤", style=discord.ButtonStyle.secondary, row=1)
    async def smart_text(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _smart_text_help(interaction, self.session)

    @discord.ui.button(label="Upcoming Scheduled Posts", emoji="📅", style=discord.ButtonStyle.secondary, row=1)
    async def schedules(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _scheduled_posts(interaction, self.session)

    @discord.ui.button(label="Recurring Campaigns", emoji="🔁", style=discord.ButtonStyle.secondary, row=1)
    async def campaigns(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        from .embed_studio_campaign_ui import recurring_campaigns
        await recurring_campaigns(interaction, self.session)

    @discord.ui.button(label="Back to My Post", emoji="◀️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


async def _advanced_tools(interaction: discord.Interaction, session: StudioSession) -> None:
    await _menu(
        interaction, heading="🧰 More Options",
        description=(
            "**Extra Text Sections:** Add labeled information inside your post. "
            "**Multiple Post Cards:** Put several designs into one Discord message. "
            "**Automatic Server Details:** Insert the server name, channel name, "
            "member count, or date. "
            "**Upcoming Scheduled Posts:** Review or cancel posts you've scheduled.\n\n"
            "You can ignore all of this when making a normal announcement."
        ),
        view=StudioAdvancedTools(session),
    )

class StudioJsonUploadModal(discord.ui.Modal, title="Import Embed Studio JSON"):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(timeout=300)
        self.session = session
        self.opened_project = deepcopy(session.project)
        self.opened_channel_id = session.channel_id
        self.opened_post_key = session.post_key
        self.upload = discord.ui.FileUpload(min_values=1, max_values=1, required=True)
        self.add_item(discord.ui.Label(
            text="Select a Studio .json file",
            description="Up to 64 KiB. Import changes your private draft only.",
            component=self.upload,
        ))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if (
            interaction.guild is None or int(interaction.guild.id) != self.session.guild_id
            or int(interaction.user.id) != self.session.owner_id
            or not _staff(interaction)
        ):
            return await _say(interaction, "Import requires your own server's staff editor.")
        if len(self.upload.values) != 1:
            return await _say(interaction, "Choose exactly one JSON file.")
        attachment = self.upload.values[0]
        if (
            not str(attachment.filename).lower().endswith(".json")
            or not 0 < int(attachment.size) <= MAX_PORTABLE_BYTES
        ):
            return await _say(interaction, "Select a .json Studio export up to 64 KiB.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            data = await asyncio.wait_for(attachment.read(use_cached=True), timeout=10)
            model = import_portable_project(data, guild_id=self.session.guild_id)
            # A second editor panel could have changed the in-memory draft
            # while this modal was open or the file was downloading.
            if (
                self.session.project != self.opened_project
                or self.session.channel_id != self.opened_channel_id
                or self.session.post_key != self.opened_post_key
            ):
                return await _say(interaction, "The draft changed while this import was open. Reopen Import JSON; no changes made.")

            # Import is only an editor mutation. The destination is retained,
            # but an existing published-message edit target must be cleared.
            def replace_draft(project: dict[str, Any]) -> None:
                project.clear()
                project.update(model)

            self.session.apply(replace_draft)
        except StudioValidationError as exc:
            return await _say(interaction, f"Import rejected: {exc}")
        except (discord.HTTPException, asyncio.TimeoutError, OSError, ValueError):
            return await _say(interaction, "Could not safely read that JSON file. Your draft was kept.")
        except Exception:
            return await _say(interaction, "Import failed. Your current draft remains unchanged.")
        self.session.active = 0
        self.session.post_key = ""
        await _home(interaction, self.session, notice="JSON imported as a private draft. Review before publishing.")


class StudioJsonMenu(_StudioView):
    @discord.ui.button(label="Export JSON File", emoji="📤", style=discord.ButtonStyle.primary, row=0)
    async def export(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        try:
            data = export_portable_project(self.session.project, guild_id=self.session.guild_id)
        except StudioValidationError as exc:
            return await _say(interaction, str(exc))
        # Separate ephemeral attachment: the editor and existing draft stay intact.
        await _say(
            interaction,
            "Your portable Embed Studio JSON is ready. Import it later from JSON Files.",
            file=discord.File(BytesIO(data), filename="dank-embed-studio.json"),
        )

    @discord.ui.button(label="Import JSON File", emoji="📥", style=discord.ButtonStyle.secondary, row=0)
    async def import_file(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(StudioJsonUploadModal(self.session))

    @discord.ui.button(label="Back to Studio", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


async def _portable_menu(interaction: discord.Interaction, session: StudioSession) -> None:
    await _menu(
        interaction,
        heading="📦 Portable Embed Studio JSON",
        description=(
            "Export your full embed design to a private JSON attachment, or import a "
            "Studio JSON file through Discord's upload dialog. Imports replace only "
            "your current private draft, never a published post. Destination and "
            "published-message edit targets are never copied. Self-role controls "
            "can only move within their original server."
        ),
        view=StudioJsonMenu(session),
    )


def _starter_picker_view(session: StudioSession) -> _StudioPickerView:
    from .embed_studio_presets import PRESETS, PRESET_DESCRIPTIONS

    async def on_pick(interaction: discord.Interaction, selected: str) -> None:
        if (session.project != new_project() or session.post_key):
            return await _menu(
                interaction,
                heading="Replace Your Current Draft?",
                description=(
                    "Picking a different layout starts a new draft. Your current work "
                    "can be restored with **Undo**, but the new layout replaces what "
                    "you're editing. Existing posted messages stay unchanged."
                ),
                view=ConfirmStarterLayout(session, selected),
            )
        await _use_starter_layout(interaction, session, selected)

    return _StudioPickerView(
        session,
        choices=[
            make_choice(name, key, description=PRESET_DESCRIPTIONS[key])
            for key, name in PRESETS
        ],
        on_pick=on_pick,
        custom_id="dank:embed_studio:starter:v1",
        placeholder="Choose a post style • gaming, news, events…",
        title="Start with a Layout",
        on_home=lambda interaction: _home(interaction, session),
        home_label="Back to My Post",
    )


async def _use_starter_layout(
    interaction: discord.Interaction, session: StudioSession, key: str,
) -> None:
    from .embed_studio_presets import project_for_preset
    # New layouts never inherit an old published-post edit target.
    chosen = project_for_preset(key)
    session.history.append(deepcopy(session.project))
    session.history = session.history[-10:]
    session.project = chosen
    session.dirty = True
    session.active = 0
    session.post_key = ""
    await _home(interaction, session, notice="Layout ready. Edit the words and choose where to post.")


class ConfirmStarterLayout(_StudioView):
    def __init__(self, session: StudioSession, key: str) -> None:
        super().__init__(session)
        self.key = key
        self.used = False
        self.snapshot = deepcopy(session.project)
        self.post_key = session.post_key

    @discord.ui.button(label="Use New Layout", emoji="✅", style=discord.ButtonStyle.primary)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if self.used:
            return await _say(interaction, "This layout choice was already used.")
        self.used = True
        if self.session.project != self.snapshot or self.session.post_key != self.post_key:
            return await _say(
                interaction, "Your draft changed since this confirmation. Reopen Pick a Layout."
            )
        await _use_starter_layout(interaction, self.session, self.key)

    @discord.ui.button(label="Keep My Draft", emoji="◀️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session, notice="Your current draft was kept.")


async def _starters(interaction: discord.Interaction, session: StudioSession) -> None:
    await _menu(
        interaction,
        heading="✨ Start with a Layout",
        description=(
            "**Game Servers:** Minecraft Relaunch, Modded Adventure, Game Night, "
            "Tournament, Patch Notes and Players Wanted.\n"
            "**Discord Events:** Movie Night, Live Watch Party, Trivia, Karaoke, "
            "Hangout, Giveaway, AMA and Creative Contest.\n"
            "**Community:** Announcements, Rules, Events, Info, FAQs and Showcase.\n\n"
            "Choose a design from the list, then customize details, colors, artwork "
            "and buttons. No layout posts or schedules anything automatically."
        ),
        view=_starter_picker_view(session),
    )

class _ProjectModal(discord.ui.Modal):
    def __init__(self, session: StudioSession, title: str) -> None:
        super().__init__(title=title, timeout=900)
        self.session = session

    async def commit(self, interaction: discord.Interaction, mutation: Callable[[dict[str, Any]], None]) -> None:
        if not _staff(interaction) or int(interaction.user.id) != self.session.owner_id:
            return await _say(interaction, "This editor belongs to another staff member.")
        try:
            self.session.apply(mutation)
        except (StudioValidationError, ValueError) as exc:
            return await _say(interaction, f"❌ {_error(exc)}")
        await _home(interaction, self.session, notice="Draft updated.")


class StudioTextModal(_ProjectModal):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(session, "Write Embed Text")
        e = session.project["embeds"][session.active]
        self.title_input = discord.ui.TextInput(label="Headline", required=False, max_length=256, default=e["title"])
        self.body_input = discord.ui.TextInput(label="Description", required=False, style=discord.TextStyle.paragraph, max_length=4000, default=e["description"][:4000])
        self.content_input = discord.ui.TextInput(label="Text above embeds (optional)", required=False, style=discord.TextStyle.paragraph, max_length=1900, default=session.project["content"][:1900])
        for control in (self.title_input, self.body_input, self.content_input):
            self.add_item(control)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        idx = self.session.active
        await self.commit(interaction, lambda p: p["embeds"][idx].update(
            title=str(self.title_input.value or ""),
            description=str(self.body_input.value or ""),
        ) or p.update(content=str(self.content_input.value or "")))


class StudioAppearanceModal(_ProjectModal):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(session, "Appearance & Author")
        e = session.project["embeds"][session.active]
        controls = [
            ("color", "Color: six-digit HEX", 7, False),
            ("author_name", "Author / small headline", 256, False),
            ("author_url", "Author HTTPS link (optional)", 2048, False),
            ("footer", "Footer text", 2048, False),
            ("title_url", "Clicking headline opens HTTPS URL", 2048, False),
        ]
        self.inputs = {}
        for key, name, length, required in controls:
            field = discord.ui.TextInput(
                label=name, required=required, max_length=length,
                default=e[key], style=discord.TextStyle.paragraph if key == "footer" else discord.TextStyle.short,
            )
            self.inputs[key] = field
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        values = {key: str(item.value or "") for key, item in self.inputs.items()}
        await self.commit(interaction, lambda p: p["embeds"][self.session.active].update(values))


_GAME_DETAIL_FIELDS = (
    ("address", "🌍 SERVER ADDRESS", ("🌍 SERVER ADDRESS",)),
    ("platforms", "🎮 SUPPORTED EDITIONS", ("🎮 SUPPORTED EDITIONS", "🕹️ GAMES & PLATFORMS")),
    ("features", "🧩 WHAT'S INSIDE", ("🧩 WHAT'S INSIDE", "🧩 FEATURED MODS", "🌟 WHY JOIN?")),
    ("join", "🚪 START PLAYING", ("🚪 START PLAYING", "📥 HOW TO CONNECT", "🔗 HOW TO JOIN")),
)


def _update_game_server_fields(project: dict[str, Any], index: int, values: dict[str, str]) -> None:
    """Update only supplied details; preserve other fields, art, buttons and copy."""
    fields = project["embeds"][index]["fields"]
    for key, title, aliases in _GAME_DETAIL_FIELDS:
        value = values.get(key, "").strip()
        if not value:
            continue
        existing = next((field for field in fields if field["name"] in aliases), None)
        if existing is not None:
            existing["value"] = value
        else:
            fields.append({"name": title, "value": value, "inline": False})


class StudioGameServerModal(_ProjectModal):
    """One mobile-friendly form for the facts people actually need to join."""

    def __init__(self, session: StudioSession) -> None:
        super().__init__(session, "Game Server Details")
        current = session.project["embeds"][session.active]["fields"]
        def previous(keys: tuple[str, ...], limit: int) -> str:
            value = next((f["value"] for f in current if f["name"] in keys), "")
            # A long value edited in Extra Text Sections must not crash this
            # smaller quick form or get silently truncated on submission.
            return value if len(value) <= limit else ""

        self.address = discord.ui.TextInput(
            label="Server address / IP:port", required=False, max_length=180,
            placeholder="play.example.net:25565",
            default=previous(_GAME_DETAIL_FIELDS[0][2], 180),
        )
        self.platforms = discord.ui.TextInput(
            label="Supported editions or platforms", required=False, max_length=180,
            placeholder="Java / Bedrock / PC / console • only what works",
            default=previous(_GAME_DETAIL_FIELDS[1][2], 180),
        )
        self.features = discord.ui.TextInput(
            label="Verified mods and best features", required=False,
            max_length=700, style=discord.TextStyle.paragraph,
            placeholder="Installed mods, custom worlds, events, gameplay",
            default=previous(_GAME_DETAIL_FIELDS[2][2], 700),
        )
        self.join = discord.ui.TextInput(
            label="How to join", required=False, max_length=500,
            style=discord.TextStyle.paragraph,
            placeholder="Game version, modpack, steps to connect",
            default=previous(_GAME_DETAIL_FIELDS[3][2], 500),
        )
        for item in (self.address, self.platforms, self.features, self.join):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        values = {
            "address": str(self.address.value or ""),
            "platforms": str(self.platforms.value or ""),
            "features": str(self.features.value or ""),
            "join": str(self.join.value or ""),
        }
        if not any(value.strip() for value in values.values()):
            return await _say(interaction, "Add at least one server detail. Your draft is unchanged.")
        index = self.session.active
        await self.commit(
            interaction, lambda project: _update_game_server_fields(project, index, values),
        )


_EVENT_DETAILS_FIELD_ALIASES = (
    ("when", "📅 WHEN", ("📅 WHEN", "⏰ START TIME", "When")),
    ("where", "📍 WHERE", ("📍 WHERE", "Where", "🔊 MEET HERE")),
    ("topic", "✨ EVENT DETAILS", (
        "✨ EVENT DETAILS", "🎞️ MOVIE PICK", "📡 WHAT WE'RE WATCHING",
        "🎯 TOPICS & FORMAT", "🎵 SONG QUEUE", "🛋️ THE VIBE",
        "🎁 CONFIRMED PRIZE & RULES", "🎙️ HOST & TOPICS",
        "🖌️ THEME, RULES & JUDGING", "🎮 GAME & FORMAT", "🕹️ GAME / MODE",
    )),
    ("join", "✅ HOW TO JOIN", (
        "✅ HOW TO JOIN", "How to join", "✅ HOW TO RSVP",
        "📝 RULES & ENTRY",
    )),
)


def _update_event_details_fields(
    project: dict[str, Any], index: int, values: dict[str, str],
) -> None:
    """Edit time, location, program and participation without erasing the rest."""
    limits = {"when": 180, "where": 180, "topic": 700, "join": 500}
    # Enforce the same limits as the visible modal at the model boundary.
    # Otherwise direct callers could accept values Discord itself rejects.
    for key, limit in limits.items():
        if len(str(values.get(key, "")).strip()) > limit:
            raise StudioValidationError(
                f"Event {key} must be {limit} characters or fewer."
            )
    fields = project["embeds"][index]["fields"]
    for key, label, aliases in _EVENT_DETAILS_FIELD_ALIASES:
        value = values.get(key, "").strip()
        if not value:
            continue
        existing = next((f for f in fields if f["name"] in aliases), None)
        if existing is None:
            fields.append({"name": label, "value": value, "inline": key in {"when", "where"}})
        else:
            existing["value"] = value


class StudioEventDetailsModal(_ProjectModal):
    """Fast mobile editor for movie nights and typical Discord server events."""

    def __init__(self, session: StudioSession) -> None:
        super().__init__(session, "Discord Event Details")
        current = session.project["embeds"][session.active]["fields"]

        def previous(aliases: tuple[str, ...], limit: int) -> str:
            existing = next((field["value"] for field in current if field["name"] in aliases), "")
            return existing if len(existing) <= limit else ""

        self.when = discord.ui.TextInput(
            label="Event date, time and timezone", required=False, max_length=180,
            placeholder="Friday at 8 PM Eastern",
            default=previous(_EVENT_DETAILS_FIELD_ALIASES[0][2], 180),
        )
        self.where = discord.ui.TextInput(
            label="Discord channel or event location", required=False, max_length=180,
            placeholder="Movie Night voice channel / Stage channel",
            default=previous(_EVENT_DETAILS_FIELD_ALIASES[1][2], 180),
        )
        self.topic = discord.ui.TextInput(
            label="Movie, activity, theme or prize", required=False, max_length=700,
            style=discord.TextStyle.paragraph,
            placeholder="What's happening? Include only confirmed details.",
            default=previous(_EVENT_DETAILS_FIELD_ALIASES[2][2], 700),
        )
        self.join = discord.ui.TextInput(
            label="How to join, RSVP or enter", required=False, max_length=500,
            style=discord.TextStyle.paragraph,
            placeholder="Instructions, access requirements and any rules",
            default=previous(_EVENT_DETAILS_FIELD_ALIASES[3][2], 500),
        )
        for control in (self.when, self.where, self.topic, self.join):
            self.add_item(control)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        values = {
            "when": str(self.when.value or ""),
            "where": str(self.where.value or ""),
            "topic": str(self.topic.value or ""),
            "join": str(self.join.value or ""),
        }
        if not any(value.strip() for value in values.values()):
            return await _say(interaction, "Enter at least one event detail. Your draft was kept.")
        index = self.session.active
        await self.commit(
            interaction, lambda p: _update_event_details_fields(p, index, values),
        )


class MediaSaveModal(discord.ui.Modal, title="Save Image or GIF Link"):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(timeout=900)
        self.session = session
        embed = session.project["embeds"][session.active]
        self.name_input = discord.ui.TextInput(
            label="Name", required=True, max_length=60, placeholder="Server logo, event banner...",
        )
        self.url_input = discord.ui.TextInput(
            label="Direct HTTPS image or GIF link", required=True, max_length=2048,
            default=next((embed[key] for key in ("image", "thumbnail", "author_icon", "footer_icon")
                          if embed[key]), ""),
        )
        self.add_item(self.name_input)
        self.add_item(self.url_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if (not _staff(interaction) or interaction.guild is None
                or int(interaction.guild.id) != self.session.guild_id
                or int(interaction.user.id) != self.session.owner_id):
            return await _say(interaction, "Open your own server's Studio to save media.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            item = validate_media_item({"name": str(self.name_input.value or ""),
                                        "url": str(self.url_input.value or "")})
            source, lib = await _load_library(self.session.guild_id)
            if len(lib["media"]) >= MAX_MEDIA_ITEMS:
                return await _say(interaction, f"{MAX_MEDIA_ITEMS} media links are saved. Remove one first.")
            if any(str(row.get("name") or "").casefold() == item["name"].casefold()
                   for row in lib["media"].values()):
                return await _say(interaction, "That media name is already taken.")
            lib["media"][secrets.token_hex(6)] = item
            ok = await _save_library(self.session.guild_id, source, lib)
        except Exception as exc:
            return await _say(interaction, "Link not saved: " + _error(exc))
        if not ok:
            return await _say(interaction, "Another moderator updated the media library. Reopen it.")
        await _say(interaction, "✅ Saved the reusable media link for this server. Nothing posted.")


MEDIA_PAGE_SIZE = 20


class StudioFormBasicsModal(discord.ui.Modal, title="Set Up Feedback Form"):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(timeout=600)
        self.session = session
        form = session.project.get("form") or {}
        self.title_input = discord.ui.TextInput(
            label="Form title",required=True,max_length=45,default=form.get("title",""),
        )
        self.button_input = discord.ui.TextInput(
            label="Button label",required=True,max_length=80,default=form.get("button_label","Send Feedback"),
        )
        self.add_item(self.title_input)
        self.add_item(self.button_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from . import embed_studio_availability as availability
        denial = await availability.feature_denial(
            availability.FEATURE_FORMS, self.session.guild_id,
            client=interaction.client, action="set up a form")
        if denial is not None:
            return await _say(interaction, denial)
        try:
            def mutate(project):
                existing = project.get("form") or {}
                project["form"] = {
                    "title": str(self.title_input.value or ""),
                    "button_label": str(self.button_input.value or ""),
                    "questions": deepcopy(existing.get("questions") or []),
                }
            self.session.apply(mutate)
        except StudioValidationError as exc:
            return await _say(interaction,"Could not save form: "+str(exc))
        await _form_builder(interaction,self.session)


class StudioFormQuestionModal(discord.ui.Modal, title="Add Form Question"):
    def __init__(self,session: StudioSession) -> None:
        super().__init__(timeout=600)
        self.session=session
        self.label_input=discord.ui.TextInput(
            label="Question",required=True,max_length=45,placeholder="What would you like to tell us?",
        )
        self.placeholder_input=discord.ui.TextInput(
            label="Example or hint (optional)",required=False,max_length=100,
        )
        self.kind_input=discord.ui.TextInput(
            label="Answer length: short or paragraph",
            default="paragraph",required=True,max_length=9,
        )
        self.required_input=discord.ui.TextInput(
            label="Is an answer required? yes/no",default="yes",required=True,max_length=3,
        )
        for item in (self.label_input,self.placeholder_input,self.kind_input,self.required_input):
            self.add_item(item)

    async def on_submit(self,interaction: discord.Interaction) -> None:
        required=str(self.required_input.value or "").strip().lower()
        if required not in ("yes","no"):
            return await _say(interaction,"Choose yes or no for required.")
        def change(project):
            form=project.get("form")
            if not form:
                raise StudioValidationError("Set up the form title first.")
            form["questions"].append({
                "label":str(self.label_input.value or ""),
                "placeholder":str(self.placeholder_input.value or ""),
                "style":str(self.kind_input.value or "").strip().lower(),
                "required":required=="yes",
            })
        try:
            self.session.apply(change)
        except StudioValidationError as exc:
            return await _say(interaction,"Question was not added: "+str(exc))
        await _form_builder(interaction,self.session)


def _form_question_picker_view(session: StudioSession) -> _StudioPickerView:
    form=session.project.get("form") or {}
    choices=[
        make_choice(row["label"][:100],str(i))
        for i,row in enumerate(form.get("questions") or [])
    ] or [make_choice("No questions yet","none")]

    async def on_pick(interaction: discord.Interaction,index: str) -> None:
        if index=="none":
            return await _say(interaction,"Add a question first.")
        def change(project):
            project["form"]["questions"].pop(int(index))
        try:
            session.apply(change)
        except (StudioValidationError,IndexError,TypeError):
            return await _say(interaction,"Question changed. Reopen Form Builder.")
        await _form_builder(interaction,session)

    return _StudioPickerView(
        session,choices=choices,on_pick=on_pick,
        custom_id="dank:embed_studio:form_question:v1",
        placeholder="Choose a question to remove",
        title="Remove a Question",
        on_home=lambda interaction: _form_builder(interaction,session),
        home_label="Back",
    )


class StudioFormDeleteConfirm(_StudioView):
    @discord.ui.button(label="Remove Form From Draft",emoji="🗑️",style=discord.ButtonStyle.danger)
    async def delete(self,interaction: discord.Interaction,_button: discord.ui.Button) -> None:
        self.session.apply(lambda project: project.update(form=None))
        await _home(interaction,self.session,notice="Form removed from this draft. Published posts stay unchanged.")

    @discord.ui.button(label="Keep Form",emoji="◀️",style=discord.ButtonStyle.secondary)
    async def keep(self,interaction: discord.Interaction,_button: discord.ui.Button) -> None:
        await _form_builder(interaction,self.session)


def _form_inbox_picker_view(session: StudioSession,rows: list[dict[str,Any]]) -> _StudioPickerView:
    choices=[
        make_choice(
            f"Member {str(row['user_id'])[:18]} • {str(row['created_at'])[:20]}",
            str(i),
        ) for i,row in enumerate(rows)
    ] or [make_choice("No responses yet","none")]

    async def on_pick(interaction: discord.Interaction,value: str) -> None:
        if value=="none":
            return await _say(interaction,"No form responses yet.")
        try:
            row=rows[int(value)]
            parts=[
                f"**{discord.utils.escape_markdown(str(item.get('question') or '')[:50])}**\n"
                + discord.utils.escape_mentions(str(item.get("answer") or "")[:900])
                for item in row.get("answers",[])[:5] if isinstance(item,dict)
            ]
            note=(f"Private response from member {str(row['user_id'])[:20]}\n"
                  + "\n\n".join(parts))
        except (ValueError,KeyError,IndexError,TypeError):
            return await _say(interaction,"That response is unavailable.")
        await _say(interaction,note[:1900])

    return _StudioPickerView(
        session,choices=choices,on_pick=on_pick,
        custom_id="dank:embed_studio:form_inbox:v1",
        placeholder="Read a private response",
        title="Private Form Responses",
        on_home=lambda interaction: _form_builder(interaction,session),
        home_label="Back to Forms",
    )


async def _form_inbox(interaction: discord.Interaction,session: StudioSession) -> None:
    from .embed_studio_forms import recent_responses
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    try:
        rows=await recent_responses(session.guild_id)
    except StudioValidationError as exc:
        return await _say(interaction,str(exc))
    except Exception:
        return await _say(interaction,"Private form submissions are unavailable right now.")
    await _menu(interaction,heading="🔒 Private Form Responses",
                description="Recent answers are visible only to authorized moderators. "
                            "Never share sensitive responses publicly.",
                view=_form_inbox_picker_view(session,rows))


class StudioFormBuilderMenu(_StudioView):
    @discord.ui.button(label="Set Up Form",emoji="📝",style=discord.ButtonStyle.primary,row=0)
    async def basics(self,interaction: discord.Interaction,_button: discord.ui.Button) -> None:
        await interaction.response.send_modal(StudioFormBasicsModal(self.session))

    @discord.ui.button(label="Add Question",emoji="➕",style=discord.ButtonStyle.secondary,row=0)
    async def question(self,interaction: discord.Interaction,_button: discord.ui.Button) -> None:
        if not self.session.project.get("form"):
            return await _say(interaction,"Set up the form title first.")
        if len(self.session.project["form"]["questions"])>=5:
            return await _say(interaction,"Five questions is the maximum.")
        await interaction.response.send_modal(StudioFormQuestionModal(self.session))

    @discord.ui.button(label="Remove Question",emoji="➖",style=discord.ButtonStyle.secondary,row=0)
    async def remove_question(self,interaction: discord.Interaction,_button: discord.ui.Button) -> None:
        await _menu(interaction,heading="Remove a Question",
                    description="Choose a question to remove from this draft.",
                    view=_form_question_picker_view(self.session))

    @discord.ui.button(label="Response Inbox",emoji="📥",style=discord.ButtonStyle.secondary,row=1)
    async def responses(self,interaction: discord.Interaction,_button: discord.ui.Button) -> None:
        await _form_inbox(interaction,self.session)

    @discord.ui.button(label="Remove Form",emoji="🗑️",style=discord.ButtonStyle.danger,row=1)
    async def remove(self,interaction: discord.Interaction,_button: discord.ui.Button) -> None:
        await _menu(interaction,heading="Remove this form?",
                    description="Only your current draft changes; existing posts keep their forms.",
                    view=StudioFormDeleteConfirm(self.session))

    @discord.ui.button(label="Back to Studio",emoji="🏠",style=discord.ButtonStyle.secondary,row=2)
    async def back(self,interaction: discord.Interaction,_button: discord.ui.Button) -> None:
        await _home(interaction,self.session)


async def _form_builder(interaction: discord.Interaction,session: StudioSession) -> None:
    from . import embed_studio_availability as availability
    denial = await availability.feature_denial(
        availability.FEATURE_FORMS, session.guild_id,
        client=interaction.client, action="open the form builder")
    if denial is not None:
        return await _say(interaction, denial)
    form=session.project.get("form")
    description=("Set up the title, then add up to five questions. Each response "
                 "goes privately to the moderator inbox, not into a public channel. "
                 "Forms require a separate explicit publish confirmation.\n\n"
                 + (f"**{discord.utils.escape_markdown(form['title'])}** "
                    f"({len(form['questions'])}/5 questions)"
                    if form else "No form configured for this draft."))
    await _menu(interaction,heading="📝 Studio Form Builder",
                description=description,view=StudioFormBuilderMenu(session))


class StudioArtworkUploadModal(discord.ui.Modal, title="Upload Artwork"):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(timeout=300)
        self.session = session
        self.file_upload = discord.ui.FileUpload(min_values=1, max_values=1, required=True)
        self.add_item(discord.ui.Label(
            text="Upload PNG, JPEG, WebP or GIF",
            description="6 MiB maximum. Images in published posts are publicly viewable.",
            component=self.file_upload,
        ))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from .embed_studio_assets import (
            StudioAssetError, MAX_UPLOAD_BYTES, uploads_enabled,
            upload_artwork, discard_failed_upload,
        )
        if (interaction.guild is None or int(interaction.guild.id) != self.session.guild_id
                or int(interaction.user.id) != self.session.owner_id
                or not _staff(interaction)):
            return await _say(interaction, "Upload requires your own server's staff editor.")
        if not uploads_enabled():
            return await _say(interaction, "Managed uploads are not enabled on this deployment.")
        if len(self.file_upload.values) != 1:
            return await _say(interaction, "Choose exactly one image.")
        attachment = self.file_upload.values[0]
        if not 0 < int(getattr(attachment, "size", 0) or 0) <= MAX_UPLOAD_BYTES:
            return await _say(interaction, "Choose an image or GIF up to 6 MiB.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        uploaded = None
        saved = False
        try:
            data = await asyncio.wait_for(attachment.read(use_cached=True), timeout=15)
            if len(data) > MAX_UPLOAD_BYTES:
                raise StudioAssetError("File exceeded the upload limit.")
            uploaded = await upload_artwork(self.session.guild_id, data, str(attachment.filename))
            for _ in range(3):
                source, lib = await _load_library(self.session.guild_id)
                if len(lib["media"]) >= MAX_MEDIA_ITEMS:
                    raise StudioAssetError("Media Library is full. Remove a saved link first.")
                if any(str(row.get("name") or "").casefold() == uploaded.name.casefold()
                       for row in lib["media"].values()):
                    raise StudioAssetError("An image already has that name. Rename the file and retry.")
                lib["media"][secrets.token_hex(6)] = {
                    "name": uploaded.name, "url": uploaded.url,
                }
                if await _save_library(self.session.guild_id, source, lib):
                    saved = True
                    break
            if not saved:
                raise StudioAssetError("Another moderator updated saved media. Retry this upload.")
        except StudioValidationError as exc:
            await _say(interaction, f"Artwork not saved: {exc}")
        except (discord.HTTPException, asyncio.TimeoutError, OSError):
            await _say(interaction, "Could not safely upload that image. Existing designs were kept.")
        except Exception:
            await _say(interaction, "Upload storage is unavailable. Existing designs were kept.")
        else:
            await _say(interaction, "✅ Artwork saved to your reusable Media Library. Nothing was posted.")
        finally:
            if uploaded is not None and not saved:
                await discard_failed_upload(uploaded)


def _media_picker_view(
    session: StudioSession, lib: dict[str, Any], *, page: int = 0,
) -> _StudioPickerView:
    start = page * MEDIA_PAGE_SIZE
    entries = list(lib["media"].items())[start:start + MEDIA_PAGE_SIZE]
    choices = [
        make_choice(str(item["name"])[:100], key)
        for key, item in entries
    ]
    if not choices:
        choices = [make_choice("No saved media yet", "none")]

    async def on_pick(interaction: discord.Interaction, key: str) -> None:
        if key == "none":
            return await _say(interaction, "Save an image or GIF link first.")
        await _menu(
            interaction, heading="Place this media in your embed",
            description="Choose a position below, or remove the saved link. Nothing posts automatically.",
            view=MediaPlacementMenu(session, key),
        )

    return _StudioPickerView(
        session, choices=choices, on_pick=on_pick,
        custom_id="dank:embed_studio:media:v1",
        placeholder="Choose a saved image or GIF",
        title="Saved Pictures",
        on_home=lambda interaction: _media_library(interaction, session, page=page),
        home_label="Back",
    )


class MediaPlacementMenu(_StudioView):
    def __init__(self, session: StudioSession, key: str) -> None:
        super().__init__(session)
        self.key = key

    async def place(self, interaction: discord.Interaction, field: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            _source, lib = await _load_library(self.session.guild_id)
            row = lib["media"].get(self.key)
            if row is None:
                return await _say(interaction, "That saved link was removed. Reopen the library.")
            url = validate_media_item(row)["url"]
            index = self.session.active
            self.session.apply(lambda draft: draft.update(
                apply_media_url(draft, embed_index=index, target=field, url=url)
            ))
        except Exception as exc:
            return await _say(interaction, "Could not apply media: " + _error(exc))
        await _home(interaction, self.session, notice="Image added to the current draft.")

    @discord.ui.button(label="Large Image", emoji="🖼️", style=discord.ButtonStyle.primary, row=0)
    async def large(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self.place(interaction, "image")

    @discord.ui.button(label="Thumbnail", emoji="🔍", style=discord.ButtonStyle.secondary, row=0)
    async def small(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self.place(interaction, "thumbnail")

    @discord.ui.button(label="Author Icon", emoji="👤", style=discord.ButtonStyle.secondary, row=0)
    async def author(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self.place(interaction, "author_icon")

    @discord.ui.button(label="Footer Icon", emoji="🧩", style=discord.ButtonStyle.secondary, row=0)
    async def footer(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self.place(interaction, "footer_icon")

    @discord.ui.button(label="Remove Saved Link", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def delete(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _menu(interaction, heading="Delete this saved link?",
                    description="Drafts and existing posts will not change. Confirm removal below.",
                    view=ConfirmMediaRemoval(self.session, self.key))

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _media_library(interaction, self.session)


class MediaLibraryMenu(_StudioView):
    def __init__(self, session: StudioSession, lib: dict[str, Any], *, page: int = 0) -> None:
        super().__init__(session)
        count = len(lib["media"])
        self.pages = max(1, (count + MEDIA_PAGE_SIZE - 1) // MEDIA_PAGE_SIZE)
        self.page = max(0, min(int(page), self.pages - 1))
        self.add_item(_StudioPickerOpenButton(
            session, label="Choose a Saved Picture", emoji="🖼️",
            custom_id="dank:embed_studio:media:open:v1", row=0,
            heading="🖼️ Saved Pictures",
            description="Choose a saved image or GIF to place in your post.",
            build=lambda interaction: _media_picker_view(session, lib, page=self.page),
        ))
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page >= self.pages - 1

    @discord.ui.button(label="Save Media Link", emoji="💾", style=discord.ButtonStyle.primary, row=1)
    async def save(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(MediaSaveModal(self.session))

    @discord.ui.button(label="Previous", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _media_library(interaction, self.session, page=self.page - 1)

    @discord.ui.button(label="Next", emoji="▶️", style=discord.ButtonStyle.secondary, row=1)
    async def next(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _media_library(interaction, self.session, page=self.page + 1)

    @discord.ui.button(label="Upload Image/GIF", emoji="📤", style=discord.ButtonStyle.primary, row=1)
    async def upload_image(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        from .embed_studio_assets import uploads_enabled
        if not uploads_enabled():
            return await _say(interaction, "Managed artwork uploads are not enabled yet.")
        from .embed_studio_artwork_budget import get_budget, ArtworkBudgetError
        try:
            get_budget()
        except ArtworkBudgetError:
            return await _say(
                interaction,
                "Image uploads are not set up yet. You can still save and reuse image links."
            )
        await interaction.response.send_modal(StudioArtworkUploadModal(self.session))

    @discord.ui.button(label="Back to Studio", emoji="🏠", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


class ConfirmMediaRemoval(_StudioView):
    def __init__(self, session: StudioSession, key: str) -> None:
        super().__init__(session)
        self.key = key
        self.used = False

    @discord.ui.button(label="Confirm Delete Link", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def delete(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if self.used:
            return await _say(interaction, "This deletion has already been submitted.")
        self.used = True
        await interaction.response.defer(ephemeral=True)
        try:
            source, lib = await _load_library(self.session.guild_id)
            if self.key not in lib["media"]:
                return await _say(interaction, "That link is no longer in the library.")
            lib["media"].pop(self.key)
            ok = await _save_library(self.session.guild_id, source, lib)
        except Exception as exc:
            return await _say(interaction, _error(exc))
        if not ok:
            return await _say(interaction, "The saved media changed. Reopen the library.")
        await _home(interaction, self.session, notice="Saved link removed. Existing messages remain.")

    @discord.ui.button(label="Cancel", emoji="◀️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _media_library(interaction, self.session)


async def _media_library(interaction: discord.Interaction, session: StudioSession, *, page: int = 0) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    try:
        _source, lib = await _load_library(session.guild_id)
    except Exception as exc:
        return await _say(interaction, _error(exc))
    view = MediaLibraryMenu(session, lib, page=page)
    from .embed_studio_assets import uploads_enabled
    usage_note = ""
    if uploads_enabled():
        from .embed_studio_artwork_budget import (
            ArtworkBudgetError, get_budget, read_artwork_usage, MIB,
        )
        try:
            budget = get_budget()
            usage = await read_artwork_usage(session.guild_id)
            usage_note = (
                f"\nImage storage: {usage['guild_used'] / MIB:.1f} of "
                f"{budget.guild_bytes / MIB:.0f} MB for this server. "
                f"Shared space: {usage['global_used'] / MIB:.1f} of "
                f"{budget.global_bytes / MIB:.0f} MB."
            )
            if usage["stale_reservations"]:
                usage_note += (
                    f" {usage['stale_reservations']} unfinished upload(s) need an owner check."
                )
        except (ArtworkBudgetError, asyncio.TimeoutError):
            usage_note = "\nStorage usage is temporarily unavailable. Saved links still work."
    await _menu(
        interaction, heading="🖼️ Saved Pictures",
        description=(
            f"{len(lib['media'])}/{MAX_MEDIA_ITEMS} saved links • Page {view.page + 1}/{view.pages}. "
            "Browse your saved image and GIF links, or add an image to reuse in a post. "
            "Removing a saved link won't delete pictures from posts already published."
            + usage_note
        ),
        view=view,
    )

class SmartTextHelp(_StudioView):
    @discord.ui.button(label="Back to Studio", emoji="◀️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


async def _smart_text_help(interaction: discord.Interaction, session: StudioSession) -> None:
    tokens = "\n".join(f"• `{token}`: {meaning}" for token, meaning in SMART_TEXT_TOKENS)
    await _menu(
        interaction, heading="🔤 Smart Text",
        description=(
            "Type these tokens in your text, headline, footer or fields. They are replaced "
            "in Preview and frozen in the published post. They do not automatically refresh.\n\n" + tokens
        ),
        view=SmartTextHelp(session),
    )


class StudioImagesModal(_ProjectModal):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(session, "Images & Icons")
        e = session.project["embeds"][session.active]
        specs = (
            ("image", "Large image HTTPS URL"),
            ("thumbnail", "Small thumbnail HTTPS URL"),
            ("author_icon", "Author icon HTTPS URL"),
            ("footer_icon", "Footer icon HTTPS URL"),
        )
        self.inputs = {}
        for key, label in specs:
            item = discord.ui.TextInput(label=label, required=False, max_length=2048, default=e[key])
            self.inputs[key] = item
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        values = {key: str(item.value or "") for key, item in self.inputs.items()}
        await self.commit(interaction, lambda p: p["embeds"][self.session.active].update(values))


async def _menu(interaction: discord.Interaction, *, heading: str, description: str, view: discord.ui.View) -> None:
    # Menu changes must not wait for guild-config reads or autosave before
    # Discord receives the acknowledgement, especially on mobile.
    await _ack_editor_navigation(interaction)
    session = getattr(view, "session", None)
    if isinstance(session, StudioSession):
        warning = await _autosave(interaction, session)
        if warning:
            description = "⚠️ " + warning + "\n\n" + description
    embed = discord.Embed(title=heading, description=description[:3900], color=discord.Color.blurple())
    kwargs = {"content": None, "embed": embed, "view": view, "allowed_mentions": _MENTIONS}
    if interaction.response.is_done():
        await interaction.edit_original_response(**kwargs)
    elif interaction.message is not None:
        await interaction.response.edit_message(**kwargs)
    else:
        await interaction.response.send_message(**kwargs, ephemeral=True)


class FieldModal(_ProjectModal):
    def __init__(self, session: StudioSession, index: int | None = None) -> None:
        super().__init__(session, "Add/Edit Embed Field")
        self.index = index
        fields = session.project["embeds"][session.active]["fields"]
        f = fields[index] if index is not None and index < len(fields) else {}
        self.name_input = discord.ui.TextInput(label="Field heading", max_length=256, default=f.get("name"))
        self.value_input = discord.ui.TextInput(label="Field text", style=discord.TextStyle.paragraph, max_length=1024, default=f.get("value"))
        self.inline_input = discord.ui.TextInput(label="Small column? yes/no", required=False, max_length=3, default="yes" if f.get("inline") else "no")
        for item in (self.name_input, self.value_input, self.inline_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        inline = str(self.inline_input.value or "no").strip().lower()
        if inline not in {"yes", "no", "y", "n"}:
            return await _say(interaction, "Use yes or no for the small-column setting.")
        f = {"name": str(self.name_input.value or ""), "value": str(self.value_input.value or ""), "inline": inline in {"yes", "y"}}
        idx = self.session.active
        def change(p: dict[str, Any]) -> None:
            fields = p["embeds"][idx]["fields"]
            if self.index is None:
                if len(fields) >= MAX_FIELDS:
                    raise StudioValidationError("This embed already has 25 fields.")
                fields.append(f)
            elif self.index < len(fields):
                fields[self.index] = f
            else:
                raise StudioValidationError("That field no longer exists.")
        await self.commit(interaction, change)


def _field_picker_view(session: StudioSession) -> _StudioPickerView:
    fields = session.project["embeds"][session.active]["fields"]
    choices = [
        make_choice(f"{i + 1}. {f['name'][:85]}", str(i))
        for i, f in enumerate(fields[:25])
    ]
    if not choices:
        choices = [make_choice("No fields added yet", "none")]

    async def on_pick(interaction: discord.Interaction, value: str) -> None:
        if value == "none":
            return await _say(interaction, "Press Add Field first.")
        await _menu(interaction, heading="Field controls", description="Edit, move up/down, or remove this field.", view=FieldActions(session, int(value)))

    return _StudioPickerView(
        session, choices=choices, on_pick=on_pick,
        custom_id="dank:embed_studio:field:v1",
        placeholder="Select a field to edit, move or remove",
        title="Embed Fields",
        on_home=lambda interaction: _fields(interaction, session),
        home_label="Back",
    )


class FieldListView(_StudioView):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(session)
        self.add_item(_StudioPickerOpenButton(
            session, label="Choose a Field", emoji="📋",
            custom_id="dank:embed_studio:field:open:v1", row=0,
            heading="📋 Embed Fields",
            description="Choose one field to edit, move or remove it.",
            build=lambda interaction: _field_picker_view(session),
        ))

    @discord.ui.button(label="Add Field", emoji="➕", style=discord.ButtonStyle.primary, row=1)
    async def add(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(FieldModal(self.session))

    @discord.ui.button(label="Back to Studio", emoji="◀️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


class FieldActions(_StudioView):
    def __init__(self, session: StudioSession, index: int) -> None:
        super().__init__(session)
        self.index = index

    @discord.ui.button(label="Edit", emoji="✏️", style=discord.ButtonStyle.primary)
    async def edit(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(FieldModal(self.session, self.index))

    @discord.ui.button(label="Move Up", emoji="⬆️", style=discord.ButtonStyle.secondary)
    async def up(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self.move(interaction, -1)

    @discord.ui.button(label="Move Down", emoji="⬇️", style=discord.ButtonStyle.secondary)
    async def down(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self.move(interaction, 1)

    async def move(self, interaction: discord.Interaction, offset: int) -> None:
        idx, next_idx = self.index, self.index + offset
        def mutation(p: dict[str, Any]) -> None:
            fields = p["embeds"][self.session.active]["fields"]
            if not (0 <= idx < len(fields) and 0 <= next_idx < len(fields)):
                raise StudioValidationError("Field is already at the edge.")
            fields[idx], fields[next_idx] = fields[next_idx], fields[idx]
        try:
            self.session.apply(mutation)
        except StudioValidationError as exc:
            return await _say(interaction, str(exc))
        self.index = next_idx
        await _fields(interaction, self.session)

    @discord.ui.button(label="Remove", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def remove(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        idx = self.index
        self.session.apply(lambda p: p["embeds"][self.session.active]["fields"].pop(idx))
        await _fields(interaction, self.session)

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _fields(interaction, self.session)


async def _fields(interaction: discord.Interaction, session: StudioSession) -> None:
    items = session.project["embeds"][session.active]["fields"]
    description = (
        f"**{len(items)}/25 fields**. Fields can appear side by side or stacked.\n"
        "Use Add Field or choose one below to edit, reorder or remove it."
    )
    await _menu(interaction, heading="📋 Embed Fields", description=description, view=FieldListView(session))


class LinkModal(_ProjectModal):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(session, "Add URL Button")
        self.label_input = discord.ui.TextInput(label="Button text", max_length=80, placeholder="Read the rules")
        self.url_input = discord.ui.TextInput(label="HTTPS destination", max_length=2048, placeholder="https://example.com")
        for item in (self.label_input, self.url_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        link = {"label": str(self.label_input.value or ""), "url": str(self.url_input.value or "")}
        def change(p: dict[str, Any]) -> None:
            if len(p["links"]) >= 5:
                raise StudioValidationError("Remove an existing button before adding another.")
            p["links"].append(link)
        await self.commit(interaction, change)


def _link_picker_view(session: StudioSession) -> _StudioPickerView:
    choices = [
        make_choice(f"{i + 1}. {link['label'][:80]}", str(i))
        for i, link in enumerate(session.project["links"])
    ]
    if not choices:
        choices = [make_choice("No link buttons yet", "none")]

    async def on_pick(interaction: discord.Interaction, value: str) -> None:
        if value == "none":
            return await _say(interaction, "Press Add URL Button first.")
        idx = int(value)
        session.apply(lambda p: p["links"].pop(idx))
        await _links(interaction, session)

    return _StudioPickerView(
        session, choices=choices, on_pick=on_pick,
        custom_id="dank:embed_studio:link:v1",
        placeholder="Choose a URL button to remove",
        title="Link Buttons",
        on_home=lambda interaction: _links(interaction, session),
        home_label="Back",
    )


class LinkMenu(_StudioView):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(session)
        self.add_item(_StudioPickerOpenButton(
            session, label="Choose a Link to Remove", emoji="🔗",
            custom_id="dank:embed_studio:link:open:v1", row=0,
            heading="🔗 Link Buttons",
            description="Choose an existing URL button to remove it.",
            build=lambda interaction: _link_picker_view(session),
        ))

    @discord.ui.button(label="Add URL Button", emoji="➕", style=discord.ButtonStyle.primary, row=1)
    async def add(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(LinkModal(self.session))

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


async def _links(interaction: discord.Interaction, session: StudioSession) -> None:
    await _menu(
        interaction,
        heading="🔗 Link Buttons",
        description="Add up to five safe clickable HTTPS buttons. They keep working after a restart. Select any existing button to remove it.",
        view=LinkMenu(session),
    )




async def _add_studio_role(interaction: discord.Interaction, session: StudioSession, role: discord.Role) -> None:
    guild = interaction.guild
    if guild is None or guild.id != session.guild_id or not _staff(interaction) or interaction.user.id != session.owner_id:
        return await _say(interaction, "This role editor belongs to another server or staff member.")
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    try:
        from .guild_config import get_guild_config
        from .commands_ext.public_self_roles_group import _profile_cosmetic_role_blocker
        config = await get_guild_config(int(guild.id), refresh=True)
        if str(config.get("source") or "").startswith("unavailable:"):
            return await _say(interaction, "Cannot verify role safety right now. Nothing added.")
        reason = _profile_cosmetic_role_blocker(guild, role, config)
        if reason:
            return await _say(interaction, f"That role is not eligible for optional self-selection: {reason}")
        if any(a["role_id"] == str(role.id) for a in session.project["role_actions"]):
            return await _say(interaction, "That role is already offered in this embed.")
        def add(p: dict[str, Any]) -> None:
            p["role_actions"].append({"label": role.name[:80], "role_id": str(role.id)})
            if len(p["role_actions"]) > 5 or len(p["role_actions"]) + len(p["links"]) > 5:
                p["role_mode"] = "dropdown"
        session.apply(add)
    except StudioValidationError as exc:
        return await _say(interaction, str(exc))
    except Exception as exc:
        return await _say(interaction, f"Role safety could not be verified ({type(exc).__name__}). Nothing added.")
    await _role_controls(interaction, session)


def _studio_role_select(session: StudioSession) -> DankRoleSelect:
    """House-framework role wrapper for the quick-pick slot in Role Controls."""

    async def on_pick(interaction: discord.Interaction, role: discord.Role) -> None:
        guild = interaction.guild
        resolved = guild.get_role(int(role.id)) if guild else None
        if not isinstance(resolved, discord.Role):
            return await _say(interaction, "Choose a role belonging to this server.")
        await _add_studio_role(interaction, session, resolved)

    select = DankRoleSelect(
        author_id=int(session.owner_id),
        on_pick=on_pick,
        placeholder="Quick pick an optional server role…",
        row=0,
    )
    select.custom_id = "dank:embed_studio:role:v1"
    return select


async def _browse_studio_roles(interaction: discord.Interaction, session: StudioSession) -> None:
    guild = interaction.guild
    if guild is None:
        return await _say(interaction, "Open the Studio inside your server.")
    await interaction.response.defer(ephemeral=True)
    try:
        from .guild_config import get_guild_config
        from .commands_ext.public_self_roles_group import _profile_cosmetic_role_blocker
        from .ui.resource_browser import DankGuildResourceBrowserView
        config = await get_guild_config(int(guild.id), refresh=True)
        if str(config.get("source") or "").startswith("unavailable:"):
            return await _say(interaction, "Role safety cannot be checked at the moment.")
    except Exception:
        return await _say(interaction, "Role browsing could not load. Nothing changed.")

    def allowed(role: Any) -> bool:
        return (
            isinstance(role, discord.Role)
            and str(role.id) not in {a["role_id"] for a in session.project["role_actions"]}
            and not _profile_cosmetic_role_blocker(guild, role, config)
        )

    async def picked(pick_interaction: discord.Interaction, resource: Any) -> None:
        if not isinstance(resource, discord.Role):
            return await _say(pick_interaction, "Choose a role.")
        await _add_studio_role(pick_interaction, session, resource)

    async def back(back_interaction: discord.Interaction) -> None:
        await _role_controls(back_interaction, session)

    view = DankGuildResourceBrowserView(
        guild=guild, author_id=session.owner_id, resource_kinds=("role",),
        on_pick=picked, custom_id="dank:embed_studio:search_roles:v1",
        title="Search-Safe Studio Role Picker",
        placeholder="Search or browse safe optional roles…",
        predicate=allowed, on_home=back,
        home_label="Back to Role Controls",
        empty_message="No more safe roles are available to add.",
    )
    await _menu(interaction, heading="🔎 Search-Safe Role Browser",
                description="Find a safe role by normal words, styled name, previous name, mention or ID.", view=view)

def _existing_role_picker_view(session: StudioSession) -> _StudioPickerView:
    actions = session.project["role_actions"]
    choices = [
        make_choice(f"{i+1}. {a['label'][:80]}", str(i))
        for i, a in enumerate(actions)
    ] or [make_choice("No roles yet", "none")]

    async def on_pick(interaction: discord.Interaction, value: str) -> None:
        if value == "none":
            return await _say(interaction, "Add a role first.")
        await _menu(
            interaction,
            heading="🎭 Role Action Settings",
            description="Change the displayed label, move the button, or remove it. Role IDs cannot be edited manually.",
            view=StudioRoleActionView(session, int(value)),
        )

    return _StudioPickerView(
        session, choices=choices, on_pick=on_pick,
        custom_id="dank:embed_studio:existing_role:v1",
        placeholder="Manage an existing role button…",
        title="Role Buttons & Dropdown",
        on_home=lambda interaction: _role_controls(interaction, session),
        home_label="Back",
    )


class StudioRoleLabelModal(discord.ui.Modal, title="Rename Role Action"):
    def __init__(self, session: StudioSession, index: int) -> None:
        super().__init__(timeout=900)
        self.session = session
        self.index = index
        action = session.project["role_actions"][index]
        self.label_input = discord.ui.TextInput(label="Button or menu label", default=action["label"], max_length=80)
        self.add_item(self.label_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not _staff(interaction) or interaction.user.id != self.session.owner_id:
            return await _say(interaction, "This editor belongs to another staff member.")
        try:
            idx = self.index
            text = str(self.label_input.value or "")
            self.session.apply(lambda p: p["role_actions"][idx].update(label=text))
        except (StudioValidationError, IndexError) as exc:
            return await _say(interaction, str(exc))
        await _home(interaction, self.session, notice="Role label updated.")


class StudioRoleActionView(_StudioView):
    def __init__(self, session: StudioSession, index: int) -> None:
        super().__init__(session)
        self.index = index

    @discord.ui.button(label="Rename", emoji="✏️", style=discord.ButtonStyle.primary)
    async def rename(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(StudioRoleLabelModal(self.session, self.index))

    @discord.ui.button(label="Move Up", emoji="⬆️", style=discord.ButtonStyle.secondary)
    async def up(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self.move(interaction, -1)

    @discord.ui.button(label="Move Down", emoji="⬇️", style=discord.ButtonStyle.secondary)
    async def down(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self.move(interaction, 1)

    async def move(self, interaction: discord.Interaction, delta: int) -> None:
        idx, target = self.index, self.index + delta
        def change(p: dict[str, Any]) -> None:
            items = p["role_actions"]
            if not 0 <= idx < len(items) or not 0 <= target < len(items):
                raise StudioValidationError("Role is already at the edge.")
            items[idx], items[target] = items[target], items[idx]
        try:
            self.session.apply(change)
        except StudioValidationError as exc:
            return await _say(interaction, str(exc))
        self.index = target
        await _role_controls(interaction, self.session)

    @discord.ui.button(label="Remove", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def remove(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if self.index >= len(self.session.project["role_actions"]):
            return await _say(interaction, "That role is already gone.")
        self.session.apply(lambda p: p["role_actions"].pop(self.index))
        await _role_controls(interaction, self.session)

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _role_controls(interaction, self.session)


class StudioRolesMenu(_StudioView):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(session)
        self.add_item(_studio_role_select(session))
        self.add_item(_StudioPickerOpenButton(
            session, label="Manage Existing Role", emoji="🎭",
            custom_id="dank:embed_studio:existing_role:open:v1", row=1,
            heading="🎭 Role Buttons & Dropdown",
            description="Choose an existing role button to change its label, order, or remove it.",
            build=lambda interaction: _existing_role_picker_view(session),
        ))

    @discord.ui.button(label="Buttons / Dropdown", emoji="🔁", style=discord.ButtonStyle.secondary, row=2)
    async def toggle_mode(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        target = "dropdown" if self.session.project["role_mode"] == "buttons" else "buttons"
        try:
            self.session.apply(lambda p: p.update(role_mode=target))
        except StudioValidationError as exc:
            return await _say(interaction, str(exc))
        await _role_controls(interaction, self.session)

    @discord.ui.button(label="Find Role", emoji="🔎", style=discord.ButtonStyle.primary, row=2)
    async def search(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _browse_studio_roles(interaction, self.session)

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


async def _role_controls(interaction: discord.Interaction, session: StudioSession) -> None:
    mode = session.project["role_mode"]
    items = session.project["role_actions"]
    names = "\n".join(f"• {i+1}. {item['label']}" for i, item in enumerate(items))
    await _menu(
        interaction, heading="🎭 Role Buttons & Dropdown",
        description=(
            f"**{len(items)}/10** optional self-assignable roles • layout: **{mode}**\n"
            "Choose a role below; Dank Shield checks permissions and safe-role rules. "
            "Members can add or remove their own offered roles, never staff or access roles.\n"
            "Buttons: up to five combined with link buttons. "
            "Dropdown: up to ten options alongside link buttons.\n\n"
            + (names or "No roles added.")
        ),
        view=StudioRolesMenu(session),
    )

class EmbedMenu(_StudioView):
    @discord.ui.button(label="Toggle Timestamp", emoji="🕒", style=discord.ButtonStyle.secondary, row=0)
    async def timestamp(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        idx = self.session.active
        self.session.apply(lambda p: p["embeds"][idx].update(timestamp=not p["embeds"][idx]["timestamp"]))
        await _home(interaction, self.session, notice="Timestamp setting changed.")

    @discord.ui.button(label="Previous", emoji="◀️", style=discord.ButtonStyle.secondary, row=0)
    async def previous(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        self.session.active = (self.session.active - 1) % len(self.session.project["embeds"])
        await _home(interaction, self.session)

    @discord.ui.button(label="Next", emoji="▶️", style=discord.ButtonStyle.secondary, row=0)
    async def next(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        self.session.active = (self.session.active + 1) % len(self.session.project["embeds"])
        await _home(interaction, self.session)

    @discord.ui.button(label="Add Embed", emoji="➕", style=discord.ButtonStyle.primary, row=1)
    async def add(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        try:
            self.session.apply(lambda p: p["embeds"].append(default_embed()))
        except StudioValidationError as exc:
            return await _say(interaction, str(exc))
        self.session.active = len(self.session.project["embeds"]) - 1
        await _home(interaction, self.session)

    @discord.ui.button(label="Duplicate", emoji="📋", style=discord.ButtonStyle.secondary, row=1)
    async def duplicate(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        try:
            self.session.apply(lambda p: p["embeds"].insert(self.session.active + 1, deepcopy(p["embeds"][self.session.active])))
        except StudioValidationError as exc:
            return await _say(interaction, str(exc))
        self.session.active += 1
        await _home(interaction, self.session)

    @discord.ui.button(label="Remove Current", emoji="🗑️", style=discord.ButtonStyle.danger, row=2)
    async def remove(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if len(self.session.project["embeds"]) < 2:
            return await _say(interaction, "A message needs at least one embed.")
        self.session.apply(lambda p: p["embeds"].pop(self.session.active))
        await _home(interaction, self.session)

    @discord.ui.button(label="Back to Studio", emoji="🏠", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


async def _embeds(interaction: discord.Interaction, session: StudioSession) -> None:
    await _menu(
        interaction,
        heading="🧩 Multi-Embed Layout",
        description=f"Editing embed {session.active + 1} of {len(session.project['embeds'])}. Add, duplicate, remove or switch. Discord permits up to {MAX_EMBEDS} embeds with 6000 characters combined.",
        view=EmbedMenu(session),
    )


async def _load_library(guild_id: int) -> tuple[Any, dict[str, Any]]:
    from .guild_config import get_guild_config
    from .embed_studio_storage_v2 import storage_v2_enabled, read_library
    source = await get_guild_config(guild_id, refresh=True)
    if str(source.get("source") or "").startswith("unavailable:"):
        raise StudioValidationError("Studio storage is offline. Draft and preview still work.")
    if storage_v2_enabled():
        # Deliberate, fail-closed cutover. Legacy guild settings remain untouched.
        return await read_library(guild_id, source)
    return source, parse_library(source)


async def _save_library(guild_id: int, source: Any, lib: dict[str, Any]) -> bool:
    from .embed_studio_storage_v2 import StudioV2Source, storage_v2_enabled, save_library
    if storage_v2_enabled():
        if not isinstance(source, StudioV2Source) or int(source.guild_id) != int(guild_id):
            raise StudioValidationError("Studio storage backend changed. Reopen the editor; no write attempted.")
        return await save_library(source, lib)
    if isinstance(source, StudioV2Source):
        raise StudioValidationError("Studio storage backend changed. Reopen the editor; no write attempted.")
    from .guild_config import compare_and_swap_guild_config_key
    value = library_payload(lib)
    ok, _saved = await compare_and_swap_guild_config_key(
        guild_id, STUDIO_KEY, expected=source.get(STUDIO_KEY),
        value=value, source="dank_embed_studio",
    )
    return bool(ok)



async def _autosave(interaction: discord.Interaction, session: StudioSession) -> str:
    """Persist validated per-staff drafts through the existing guild CAS."""
    if not session.dirty:
        return ""
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    async with session.save_lock:
        if not session.dirty:
            return ""
        snapshot = deepcopy(session.project)
        channel_id, active, post_key = session.channel_id, session.active, session.post_key
        prior_version = session.saved_version
        try:
            # Retry only whole-library CAS conflicts caused by unrelated
            # templates, post-history or other staff drafts. Never overwrite
            # this editor owner's newer draft revision.
            for _attempt in range(3):
                source, lib = await _load_library(session.guild_id)
                next_version = draft_snapshot(
                    lib, owner_id=session.owner_id, expected_version=prior_version,
                    project=snapshot, channel_id=channel_id, active=active,
                    post_key=post_key, now=int(time.time()),
                )
                if await _save_library(session.guild_id, source, lib):
                    session.saved_version = next_version
                    if (session.project == snapshot and session.channel_id == channel_id
                        and session.active == active and session.post_key == post_key):
                        session.dirty = False
                    return ""
            return "Autosave collided with another server edit. Changes remain in this open editor."
        except StudioValidationError as exc:
            return str(exc)
        except Exception:
            return "Autosave unavailable. Changes remain only in this open editor."


def _restore_snapshot(session: StudioSession, lib: dict[str, Any]) -> bool:
    saved = lib.get("drafts", {}).get(str(session.owner_id))
    if not isinstance(saved, dict):
        return False
    project = validate_project(saved.get("project") or {})
    version = int(saved.get("version") or 0)
    if version <= 0:
        raise StudioValidationError("Saved draft revision is invalid.")
    channel_id = max(0, int(saved.get("channel_id") or 0))
    active = max(0, min(int(saved.get("active") or 0), len(project["embeds"]) - 1))
    key = str(saved.get("post_key") or "")
    session.project = project
    session.saved_version = version
    session.channel_id = channel_id
    session.active = active
    # A deleted or evicted published post must not become an edit target.
    session.post_key = key if key in lib["published"] else ""
    session.history.clear()
    session.dirty = False
    return True


async def _reload_saved(interaction: discord.Interaction, session: StudioSession) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    try:
        _, lib = await _load_library(session.guild_id)
        found = _restore_snapshot(session, lib)
    except (StudioValidationError, ValueError, TypeError):
        return await _say(interaction, "Saved draft could not be restored. Your current editor was not published.")
    except Exception:
        return await _say(interaction, "Saved draft storage is unavailable. No changes were published.")
    await _home(interaction, session, notice="Saved draft restored." if found else "No saved draft found for your account.")


class ThemeNameModal(discord.ui.Modal, title="Save Server Brand Theme"):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(timeout=900)
        self.session = session
        # Snapshot what was on the screen when the modal opened.
        self.style = theme_from_project(session.project, embed_index=session.active)
        self.name_input = discord.ui.TextInput(
            label="Theme name", placeholder="Example: Welcome and announcements",
            required=True, max_length=60,
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if (
            not _staff(interaction) or interaction.guild is None
            or int(interaction.guild.id) != self.session.guild_id
            or int(interaction.user.id) != self.session.owner_id
        ):
            return await _say(interaction, "Open your own server's Studio to save themes.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            name = theme_name(str(self.name_input.value or ""))
            source, lib = await _load_library(self.session.guild_id)
            if len(lib["themes"]) >= MAX_THEMES:
                return await _say(interaction, "Eight server brand themes are saved. Remove an unused one first.")
            if any(str(row.get("name") or "").casefold() == name.casefold() for row in lib["themes"].values()):
                return await _say(interaction, "That theme name is already in use. Choose a different name.")
            lib["themes"][secrets.token_hex(6)] = {"name": name, "style": self.style}
            ok = await _save_library(self.session.guild_id, source, lib)
        except Exception as exc:
            return await _say(interaction, "Theme not saved: " + _error(exc))
        if not ok:
            return await _say(interaction, "Another moderator updated server themes. Open Brand Themes again.")
        await _say(interaction, f"✅ Server theme **{discord.utils.escape_markdown(name)}** saved. No messages were posted.")


def _brand_theme_picker_view(
    session: StudioSession, lib: dict[str, Any],
) -> _StudioPickerView:
    choices = [
        make_choice(name, "preset:" + key, description="Color preset")
        for key, name, _style in THEME_PRESETS
    ] + [
        make_choice(str(row["name"])[:100], "saved:" + key,
                    description="Saved server brand style")
        for key, row in lib["themes"].items()
    ]

    async def on_pick(interaction: discord.Interaction, value: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            if value.startswith("preset:"):
                chosen = next(
                    (style for key, _name, style in THEME_PRESETS if key == value[7:]),
                    None,
                )
            elif value.startswith("saved:"):
                _source, fresh_lib = await _load_library(session.guild_id)
                record = fresh_lib["themes"].get(value[6:])
                chosen = record.get("style") if isinstance(record, dict) else None
            else:
                chosen = None
            if chosen is None:
                return await _say(interaction, "That theme is no longer available. Refresh Brand Themes.")
            session.apply(lambda project: project.update(apply_theme(project, chosen)))
        except Exception as exc:
            return await _say(interaction, "Theme not applied: " + _error(exc))
        await _home(interaction, session, notice="Brand style applied to all embeds. Preview before posting.")

    return _StudioPickerView(
        session, choices=choices, on_pick=on_pick,
        custom_id="dank:embed_studio:brand_theme:v1",
        placeholder="Apply a brand theme to all embeds…",
        title="Server Brand Themes",
        on_home=lambda interaction: _themes(interaction, session),
        home_label="Back to Studio",
    )


class BrandThemeMenu(_StudioView):
    def __init__(self, session: StudioSession, lib: dict[str, Any]) -> None:
        super().__init__(session)
        self.lib = lib
        self.add_item(_StudioPickerOpenButton(
            session, label="Apply a Theme", emoji="🎨",
            custom_id="dank:embed_studio:brand_theme:open:v1", row=0,
            heading="🎨 Server Brand Themes",
            description="Choose a color preset or a saved server style to apply to all embeds in this draft.",
            build=lambda interaction: _brand_theme_picker_view(session, lib),
        ))

    @discord.ui.button(label="Save Current Style", emoji="💾", style=discord.ButtonStyle.primary, row=1)
    async def save(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ThemeNameModal(self.session))

    @discord.ui.button(label="Match All Embeds", emoji="🖌️", style=discord.ButtonStyle.secondary, row=1)
    async def match(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        style = theme_from_project(self.session.project, embed_index=self.session.active)
        self.session.apply(lambda project: project.update(apply_theme(project, style)))
        await _home(interaction, self.session, notice="All embeds now match the current embed's style.")

    @discord.ui.button(label="Remove Saved Theme", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def remove(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _remove_theme_menu(interaction, self.session)

    @discord.ui.button(label="Back to Studio", emoji="◀️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


async def _themes(interaction: discord.Interaction, session: StudioSession) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    try:
        _source, lib = await _load_library(session.guild_id)
    except Exception as exc:
        return await _say(interaction, _error(exc))
    await _menu(
        interaction, heading="🎨 Server Brand Themes",
        description=(
            f"{len(lib['themes'])}/{MAX_THEMES} shared themes. Choose a color preset or saved "
            "style to update the current draft's embeds, or save this embed's color, "
            "author and footer for moderators to reuse. Nothing posts automatically. "
            "Undo is available in the editor."
        ),
        view=BrandThemeMenu(session, lib),
    )


def _remove_theme_picker_view(
    session: StudioSession, lib: dict[str, Any],
) -> _StudioPickerView:
    choices = [
        make_choice(str(row["name"])[:100], key)
        for key, row in lib["themes"].items()
    ] or [make_choice("No saved themes", "none")]

    async def on_pick(interaction: discord.Interaction, key: str) -> None:
        if key == "none":
            return await _say(interaction, "There are no saved themes.")
        await _menu(interaction, heading="Remove shared theme?",
                    description="Only the saved theme is removed; existing drafts and messages stay unchanged.",
                    view=ConfirmThemeRemovalView(session, key))

    return _StudioPickerView(
        session, choices=choices, on_pick=on_pick,
        custom_id="dank:embed_studio:remove_theme:v1",
        placeholder="Choose saved theme to remove",
        title="Remove a Saved Brand Theme",
        on_home=lambda interaction: _themes(interaction, session),
        home_label="Back",
    )


class ConfirmThemeRemovalView(_StudioView):
    def __init__(self, session: StudioSession, key: str) -> None:
        super().__init__(session)
        self.key = key
        self.used = False

    @discord.ui.button(label="Remove This Theme", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def remove(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if self.used:
            return await _say(interaction, "This removal was already submitted.")
        self.used = True
        await interaction.response.defer(ephemeral=True)
        try:
            source, lib = await _load_library(self.session.guild_id)
            if self.key not in lib["themes"]:
                return await _say(interaction, "Theme no longer exists.")
            lib["themes"].pop(self.key)
            ok = await _save_library(self.session.guild_id, source, lib)
        except Exception as exc:
            return await _say(interaction, _error(exc))
        if not ok:
            return await _say(interaction, "Another moderator updated server themes. Refresh this screen.")
        await _home(interaction, self.session, notice="Saved theme removed. Published posts unchanged.")

    @discord.ui.button(label="Cancel", emoji="◀️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _themes(interaction, self.session)


async def _remove_theme_menu(interaction: discord.Interaction, session: StudioSession) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    try:
        _source, lib = await _load_library(session.guild_id)
    except Exception as exc:
        return await _say(interaction, _error(exc))
    await _menu(interaction, heading="Remove a Saved Brand Theme",
                description="Only saved styles are listed. Removal requires a second confirmation.",
                view=_remove_theme_picker_view(session, lib))


class TemplateNameModal(discord.ui.Modal, title="Save Embed Template"):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(timeout=900)
        self.session = session
        self.name_input = discord.ui.TextInput(label="Template name", placeholder="Rules • Weekly announcement", max_length=60)
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not _staff(interaction) or interaction.user.id != self.session.owner_id:
            return await _say(interaction, "You cannot save another editor's template.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        name = str(self.name_input.value or "").strip()
        if not name:
            return await _say(interaction, "A template needs a name.")
        try:
            source, lib = await _load_library(self.session.guild_id)
            if len(lib["templates"]) >= MAX_TEMPLATES:
                return await _say(interaction, "12 templates are saved. Remove an unused one first.")
            key = secrets.token_hex(6)
            lib["templates"][key] = {"name": name, "project": validate_project(self.session.project)}
            applied = await _save_library(self.session.guild_id, source, lib)
        except Exception as exc:
            return await _say(interaction, f"Template not saved: {_error(exc)}")
        if not applied:
            return await _say(interaction, "Templates changed in another session. Open Templates again.")
        await _say(interaction, f"✅ Template **{discord.utils.escape_markdown(name)}** saved for this server.")


def _template_picker_view(
    session: StudioSession, lib: dict[str, Any],
) -> _StudioPickerView:
    choices = [
        make_choice(str(item.get("name") or "Untitled")[:90], key)
        for key, item in list(lib["templates"].items())[:25]
    ]
    if not choices:
        choices = [make_choice("No saved templates", "none")]

    async def on_pick(interaction: discord.Interaction, key: str) -> None:
        if key == "none":
            return await _say(interaction, "Save your first template from this screen.")
        await interaction.response.defer(ephemeral=True)
        try:
            _source, fresh_lib = await _load_library(session.guild_id)
            item = fresh_lib["templates"].get(key)
            if not item:
                return await _say(interaction, "That template was removed. Refresh this menu.")
            draft = validate_project(item["project"])
        except Exception as exc:
            return await _say(interaction, f"Could not load template: {_error(exc)}")
        session.history.append(session.project)
        session.history = session.history[-10:]
        session.project = draft
        session.dirty = True
        session.active = 0
        session.post_key = ""
        await _home(interaction, session, notice="Template loaded. Nothing posted.")

    return _StudioPickerView(
        session, choices=choices, on_pick=on_pick,
        custom_id="dank:embed_studio:template:v1",
        placeholder="Choose a template to load",
        title="Reusable Server Templates",
        on_home=lambda interaction: _templates(interaction, session),
        home_label="Back",
    )


class TemplateMenu(_StudioView):
    def __init__(self, session: StudioSession, lib: dict[str, Any]) -> None:
        super().__init__(session)
        self.lib = lib
        self.add_item(_StudioPickerOpenButton(
            session, label="Load a Template", emoji="💾",
            custom_id="dank:embed_studio:template:open:v1", row=0,
            heading="💾 Reusable Server Templates",
            description="Choose a saved template to load into this draft. Nothing is posted.",
            build=lambda interaction: _template_picker_view(session, lib),
        ))

    @discord.ui.button(label="Save Current Draft", emoji="💾", style=discord.ButtonStyle.success, row=1)
    async def save(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(TemplateNameModal(self.session))

    @discord.ui.button(label="Manage Saved Templates", emoji="🗑️", style=discord.ButtonStyle.secondary, row=1)
    async def manage(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _template_deletion(interaction, self.session)

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


async def _templates(interaction: discord.Interaction, session: StudioSession) -> None:
    await interaction.response.defer(ephemeral=True)
    try:
        _, lib = await _load_library(session.guild_id)
    except Exception as exc:
        return await _say(interaction, _error(exc))
    await _menu(
        interaction, heading="💾 Reusable Server Templates",
        description=f"{len(lib['templates'])}/{MAX_TEMPLATES} templates. Save this draft, load a previous one or remove an unused template. Your work isn't published by saving.",
        view=TemplateMenu(session, lib),
    )


def _delete_template_picker_view(
    session: StudioSession, lib: dict[str, Any],
) -> _StudioPickerView:
    choices = [
        make_choice(str(item.get("name") or "Untitled")[:90], key)
        for key, item in lib["templates"].items()
    ] or [make_choice("No saved templates", "none")]

    async def on_pick(interaction: discord.Interaction, key: str) -> None:
        if key == "none":
            return await _say(interaction, "There is no template to remove.")
        await _menu(interaction, heading="Delete template?", description="This deletes a saved template, not any published message. Confirm to continue.", view=ConfirmDeleteTemplateView(session, key))

    return _StudioPickerView(
        session, choices=choices, on_pick=on_pick,
        custom_id="dank:embed_studio:delete_template:v1",
        placeholder="Choose template to REMOVE",
        title="Manage Templates",
        on_home=lambda interaction: _templates(interaction, session),
        home_label="Back",
    )


class ConfirmDeleteTemplateView(_StudioView):
    def __init__(self, session: StudioSession, key: str) -> None:
        super().__init__(session)
        self.key = key
        self.used = False

    @discord.ui.button(label="Delete Saved Template", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def delete(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if self.used:
            return await _say(interaction, "This request was already submitted.")
        self.used = True
        await interaction.response.defer(ephemeral=True)
        try:
            source, lib = await _load_library(self.session.guild_id)
            if self.key not in lib["templates"]:
                return await _say(interaction, "Template no longer exists.")
            lib["templates"].pop(self.key)
            ok = await _save_library(self.session.guild_id, source, lib)
        except Exception as exc:
            return await _say(interaction, _error(exc))
        if not ok:
            return await _say(interaction, "A different admin updated the library. Please refresh.")
        await _home(interaction, self.session, notice="Saved template removed.")

    @discord.ui.button(label="Cancel", emoji="◀️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _templates(interaction, self.session)


async def _template_deletion(interaction: discord.Interaction, session: StudioSession) -> None:
    await interaction.response.defer(ephemeral=True)
    try:
        _, lib = await _load_library(session.guild_id)
    except Exception as exc:
        return await _say(interaction, _error(exc))
    await _menu(interaction, heading="Manage Templates", description="Select a template to remove; a second confirmation is required.", view=_delete_template_picker_view(session, lib))


def _target_channel(interaction: discord.Interaction, session: StudioSession) -> discord.TextChannel | None:
    guild = interaction.guild
    if guild is None or int(guild.id) != session.guild_id:
        return None
    channel = guild.get_channel(session.channel_id)
    return channel if isinstance(channel, discord.TextChannel) else None


def _post_blockers(interaction: discord.Interaction, channel: discord.TextChannel) -> list[str]:
    member = interaction.user
    bot = channel.guild.me
    if not _staff(interaction) or not isinstance(member, discord.Member):
        return ["Manage Server / Administrator"]
    if not isinstance(bot, discord.Member):
        return ["Bot membership unavailable"]
    perms = channel.permissions_for(bot)
    return [
        label for label, permitted in (
            ("View Channel", perms.view_channel),
            ("Send Messages", perms.send_messages),
            ("Embed Links", perms.embed_links),
            ("Read Message History", perms.read_message_history),
        ) if not permitted
    ]


async def _review(interaction: discord.Interaction, session: StudioSession) -> None:
    channel = _target_channel(interaction, session)
    if channel is None:
        return await _say(interaction, "Choose a destination channel in the editor first.")
    blockers = _post_blockers(interaction, channel)
    if blockers:
        return await _say(interaction, "Bot cannot publish in that channel: " + ", ".join(blockers))
    try:
        resolved = resolve_smart_text(
            session.project, guild_name=str(channel.guild.name),
            channel_name=str(channel.name), member_count=getattr(channel.guild, "member_count", None),
        )
        embeds = render_embeds(resolved)
    except StudioValidationError as exc:
        return await _say(interaction, str(exc))
    status = (
        f"👁️ PRIVATE PREVIEW for {channel.mention}. "
        + ("Review the changes to your existing post." if session.post_key
           else "Check how your new post will look.")
        + " Nothing has changed publicly yet. Choose the green button only when ready."
    )
    if resolved["content"]:
        status += "\nPlain message: " + resolved["content"][:1300]
    kwargs = {"content": status[:1950], "embeds": embeds, "view": StudioReviewView(session, resolved_project=resolved), "allowed_mentions": _MENTIONS}
    if interaction.response.is_done():
        await interaction.edit_original_response(**kwargs)
    elif interaction.message is not None:
        await interaction.response.edit_message(**kwargs)
    else:
        await interaction.response.send_message(**kwargs, ephemeral=True)


class StudioReviewSubmissionModal(discord.ui.Modal, title="Send Design for Staff Review"):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(timeout=300)
        self.session = session
        self.snapshot = deepcopy(session.project)
        self.channel_id = session.channel_id
        self.post_key = session.post_key
        self.name_input = discord.ui.TextInput(
            label="Design name", required=True, max_length=80,
            placeholder="Rules update, event announcement...",
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from .embed_studio_reviews import submit_review, reviews_enabled
        if (not reviews_enabled() or not _staff(interaction)
                or interaction.guild is None
                or int(interaction.guild.id) != self.session.guild_id
                or int(interaction.user.id) != self.session.owner_id):
            return await _say(interaction, "Team Review is not enabled or staff access changed.")
        if (not self.channel_id or self.post_key
                or self.session.project != self.snapshot
                or self.session.channel_id != self.channel_id
                or self.session.post_key != self.post_key):
            return await _say(interaction, "Select a destination and reopen Team Review. This draft changed.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            channel = _target_channel(interaction, self.session)
            if channel is None or _post_blockers(interaction, channel):
                return await _say(interaction, "The selected posting channel is no longer usable.")
            await submit_review(self.session.guild_id, self.session.owner_id,
                                self.channel_id, str(self.name_input.value), self.snapshot)
        except StudioValidationError as exc:
            return await _say(interaction, "Could not submit design: " + str(exc))
        except Exception:
            return await _say(interaction, "Team Review storage unavailable. No request was sent.")
        await _say(interaction, "✅ Design sent to the server's private staff review queue. "
                   "It was not published and your draft remains unchanged.")


def _review_queue_picker_view(
    session: StudioSession, rows: list[dict[str, Any]], *, state: str,
) -> _StudioPickerView:
    choices = [
        make_choice(str(row["title"])[:100], str(row["id"]),
                    description=f"By {row['submitted_by']} • Channel {row['channel_id']}"[:100])
        for row in rows
    ] or [make_choice("No requests found", "none")]

    async def on_pick(interaction: discord.Interaction, value: str) -> None:
        if value == "none":
            return await _say(interaction, "No designs in this queue.")
        if state == "pending":
            await _menu(interaction, heading="Review this design",
                        description=("Approving loads a private copy into your own editor. "
                                     "It never publishes. You cannot approve your own submission."),
                        view=StudioReviewDecisionView(session, value))
        else:
            await _load_approved_review(interaction, session, value)

    return _StudioPickerView(
        session, choices=choices, on_pick=on_pick,
        custom_id=f"dank:embed_studio:review_queue:{state}:v1",
        placeholder="Choose a staff design...",
        title="Team Design Review",
        on_home=lambda interaction: _team_review_home(interaction, session),
        home_label="Back to Team Review",
    )


class StudioReviewDecisionView(_StudioView):
    def __init__(self, session: StudioSession, review_id: str) -> None:
        super().__init__(session)
        self.review_id = review_id
        self.used = False

    async def _decide(self, interaction: discord.Interaction, *, action: str) -> None:
        from .embed_studio_reviews import decide_review, load_review_project
        if self.used:
            return await _say(interaction, "This decision was already submitted.")
        self.used = True
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await decide_review(self.session.guild_id, self.review_id,
                                         self.session.owner_id, action)
            if not result:
                return await _say(interaction,
                    "This request changed, or you submitted it yourself. Refresh the review queue.")
            if action == "approved":
                project = load_review_project(result, guild_id=self.session.guild_id)
                # Private editor mutation retains Undo history. No public message
                # changes or staff-to-staff draft overwrite.
                self.session.apply(lambda draft: (draft.clear(), draft.update(project)))
                self.session.channel_id = int(result["channel_id"])
                self.session.post_key = ""
        except StudioValidationError as exc:
            return await _say(interaction, "Review was not loaded: " + str(exc))
        except Exception:
            return await _say(interaction, "Review service unavailable. Reopen Team Review before retrying.")
        if action == "approved":
            return await _home(interaction, self.session,
                notice="Approved draft opened in your private editor. Review and preview before publishing.")
        await _say(interaction, "Request rejected. No public messages or private drafts changed.")

    @discord.ui.button(label="Approve & Edit Copy", emoji="✅", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._decide(interaction, action="approved")

    @discord.ui.button(label="Reject Request", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._decide(interaction, action="rejected")

    @discord.ui.button(label="Keep Pending", emoji="◀️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _team_review_queue(interaction, self.session, state="pending")


async def _load_approved_review(interaction: discord.Interaction, session: StudioSession,
                                review_id: str) -> None:
    from .embed_studio_reviews import get_review, load_review_project
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    try:
        row = await get_review(session.guild_id, review_id)
        if row is None:
            return await _say(interaction, "That review no longer exists.")
        project = load_review_project(row, guild_id=session.guild_id)
        session.apply(lambda draft: (draft.clear(), draft.update(project)))
        session.channel_id = int(row["channel_id"])
        session.post_key = ""
    except StudioValidationError as exc:
        return await _say(interaction, "Cannot load approved design: " + str(exc))
    except Exception:
        return await _say(interaction, "Approved designs are unavailable. The existing draft was kept.")
    await _home(interaction, session, notice="Approved design loaded as a private draft. Preview before publishing.")


async def _team_review_queue(interaction: discord.Interaction, session: StudioSession,
                             *, state: str) -> None:
    from .embed_studio_reviews import list_reviews
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    try:
        rows = await list_reviews(session.guild_id, state=state)
    except StudioValidationError as exc:
        return await _say(interaction, str(exc))
    except Exception:
        return await _say(interaction, "Review queue is unavailable. No changes made.")
    await _menu(interaction,
                heading="👥 Pending Team Reviews" if state == "pending" else "✅ Approved Designs",
                description=(f"{len(rows)} recent server designs. "
                             "Only staff may view them. Approved designs load privately; "
                             "publishing always needs a separate preview."),
                view=_review_queue_picker_view(session, rows, state=state))


class StudioTeamReviewHomeView(_StudioView):
    @discord.ui.button(label="Submit Current Draft", emoji="📤", style=discord.ButtonStyle.primary, row=0)
    async def submit(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        from .embed_studio_reviews import reviews_enabled
        if not reviews_enabled():
            return await _say(interaction, "Team Review has not been enabled yet.")
        await interaction.response.send_modal(StudioReviewSubmissionModal(self.session))

    @discord.ui.button(label="Pending Reviews", emoji="📋", style=discord.ButtonStyle.secondary, row=0)
    async def pending(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _team_review_queue(interaction, self.session, state="pending")

    @discord.ui.button(label="Approved Designs", emoji="✅", style=discord.ButtonStyle.secondary, row=0)
    async def approved(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _team_review_queue(interaction, self.session, state="approved")

    @discord.ui.button(label="Back to Studio", emoji="🏠", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


async def _team_review_home(interaction: discord.Interaction, session: StudioSession) -> None:
    from .embed_studio_reviews import reviews_enabled
    if not reviews_enabled():
        return await _say(interaction, "Team Review is off until the owner enables its storage.")
    await _menu(interaction, heading="👥 Team Design Review",
                description="Send a draft to your staff team without posting it. "
                            "Another moderator can approve and edit a private copy, "
                            "or reject it. No automatic publication.",
                view=StudioTeamReviewHomeView(session))


class StudioScheduleModal(discord.ui.Modal, title="Schedule an Announcement"):
    def __init__(self, session: StudioSession, *, preview: dict[str, Any],
                 channel_id: int, post_key: str, review: Any) -> None:
        super().__init__(timeout=300)
        self.session = session
        self.preview = deepcopy(preview)
        self.channel_id = channel_id
        self.post_key = post_key
        self.review = review
        self.used = False
        self.when_input = discord.ui.TextInput(
            label="Date, time and timezone offset",
            placeholder="2026-11-01T18:00-05:00",
            required=True, max_length=35,
        )
        self.add_item(self.when_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from .embed_studio_schedules import (
            StudioScheduleError, parse_schedule_time, safe_scheduled_project,
            enqueue,
        )
        from . import embed_studio_availability as availability
        denial = await availability.feature_denial(
            availability.FEATURE_SCHEDULING, self.session.guild_id,
            client=interaction.client, action="schedule a post")
        if denial is not None:
            return await _say(interaction, denial)
        if (not _staff(interaction)
                or interaction.guild is None
                or int(interaction.guild.id) != self.session.guild_id
                or int(interaction.user.id) != self.session.owner_id):
            return await _say(interaction, "Scheduled publishing is not enabled, or staff access changed.")
        if self.used or self.review.sent:
            return await _say(interaction, "This preview was already used. Open a fresh preview.")
        self.used = True
        if (self.session.project != self.preview
                or self.session.channel_id != self.channel_id
                or self.session.post_key != self.post_key
                or self.post_key):
            return await _say(interaction, "This preview changed or edits an existing post. Reopen Preview.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            channel = _target_channel(interaction, self.session)
            if channel is None or _post_blockers(interaction, channel):
                return await _say(interaction, "Destination or publishing permissions changed.")
            when = parse_schedule_time(str(self.when_input.value or ""))
            project = safe_scheduled_project(self.preview)
            from .commands_ext.public_embed_group import _require_safe_embed_action
            if not await _require_safe_embed_action(interaction, action="send public embed"):
                return
            if (self.session.project != self.preview
                    or self.session.channel_id != self.channel_id
                    or self.session.post_key != self.post_key):
                return await _say(interaction, "Preview changed while scheduling; nothing was queued.")
            job = await enqueue(self.session.guild_id, self.channel_id,
                                self.session.owner_id, project, when)
        except StudioValidationError as exc:
            return await _say(interaction, f"Could not schedule: {exc}")
        except Exception:
            return await _say(interaction, "Scheduling storage unavailable. No automatic post was queued.")
        self.review.sent = True
        stamp = int(when.timestamp())
        await _say(interaction,
            f"✅ Announcement scheduled for <t:{stamp}:F> in <#{self.channel_id}>. "
            "You can cancel it under Scheduled Posts. No message was posted yet.")


def _schedule_picker_view(
    session: StudioSession, rows: list[dict[str, Any]],
) -> _StudioPickerView:
    choices = [
        make_choice(
            f"Channel {str(row['channel_id'])[:18]} • {str(row['execute_at'])[:22]}",
            str(row["id"]),
        ) for row in rows
    ] or [make_choice("No upcoming announcements", "none")]

    async def on_pick(interaction: discord.Interaction, value: str) -> None:
        if value == "none":
            return await _say(interaction, "There are no upcoming announcements.")
        await _menu(interaction, heading="Cancel this scheduled announcement?",
                    description="Cancellation affects only a pending job. It never deletes past posts.",
                    view=CancelScheduleView(session, value))

    return _StudioPickerView(
        session, choices=choices, on_pick=on_pick,
        custom_id="dank:embed_studio:schedule:v1",
        placeholder="Choose a scheduled announcement",
        title="Scheduled Announcements",
        on_home=lambda interaction: _home(interaction, session),
        home_label="Back to Studio",
    )


class CancelScheduleView(_StudioView):
    def __init__(self, session: StudioSession, job_id: str) -> None:
        super().__init__(session)
        self.job_id = job_id
        self.used = False

    @discord.ui.button(label="Cancel Pending Post", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def cancel_post(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if self.used:
            return await _say(interaction, "This action has already been submitted.")
        self.used = True
        await interaction.response.defer(ephemeral=True, thinking=True)
        from .embed_studio_schedules import cancel
        try:
            stopped = await cancel(self.session.guild_id, self.job_id)
        except Exception:
            return await _say(interaction, "Could not cancel. Check Scheduled Posts before assuming it was stopped.")
        await _say(interaction, "✅ Scheduled post cancelled." if stopped
                   else "Post is no longer pending. It may have started or been cancelled already.")

    @discord.ui.button(label="Keep Post", emoji="◀️", style=discord.ButtonStyle.secondary)
    async def keep(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _scheduled_posts(interaction, self.session)


async def _scheduled_posts(interaction: discord.Interaction, session: StudioSession) -> None:
    from .embed_studio_schedules import list_pending
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    try:
        rows = await list_pending(session.guild_id)
    except StudioValidationError as exc:
        return await _say(interaction, str(exc))
    except Exception:
        return await _say(interaction, "Upcoming posts are unavailable. No changes were made.")
    await _menu(interaction, heading="📅 Scheduled Announcements",
                description="Upcoming one-time posts. Open Preview to schedule another. "
                            "A missed or failed message is never sent twice automatically.",
                view=_schedule_picker_view(session, rows))


class StudioReviewView(_StudioView):
    def __init__(self, session: StudioSession, *, resolved_project: dict[str, Any] | None = None) -> None:
        super().__init__(session, timeout=300)
        self.sent = False
        # Bind confirmation to exactly what the staff member previewed.
        # Other open editor messages may still mutate the session while this
        # confirmation is visible; a stale preview must never be publishable.
        self.preview_project = deepcopy(session.project)
        self.preview_resolved_project = validate_project(resolved_project if resolved_project is not None else session.project)
        self.preview_channel_id = session.channel_id
        self.preview_post_key = session.post_key
        from . import embed_studio_availability as availability
        self.repeat_post.disabled = (
            not availability.sync_button_enabled(availability.FEATURE_RECURRING)
            or bool(session.post_key)
            or bool(session.project.get("role_actions"))
            or bool(session.project.get("form"))
        )
        self.schedule_later.disabled = (
            not availability.sync_button_enabled(availability.FEATURE_SCHEDULING)
            or bool(session.post_key)
            or bool(session.project.get("role_actions"))
            or bool(session.project.get("form"))
        )
        self.publish.label = "Save Changes" if session.post_key else "Post Now"
        # Link buttons really open their HTTPS target in preview. Role actions
        # are disabled here: a private preview must never grant a public role.
        for link in session.project["links"]:
            self.add_item(discord.ui.Button(label=link["label"], url=link["url"], style=discord.ButtonStyle.link, row=1))
        form = session.project.get("form")
        if form:
            self.add_item(discord.ui.Button(
                label=form["button_label"],disabled=True,
                style=discord.ButtonStyle.primary,row=1,
            ))
        role_items = session.project.get("role_actions") or []
        if role_items and session.project.get("role_mode") == "dropdown":
            self.add_item(discord.ui.Select(
                placeholder="Role dropdown (active after publishing)",
                options=[discord.SelectOption(label=item["label"], value=str(i)) for i, item in enumerate(role_items)],
                min_values=1, max_values=1, disabled=True, row=2,
            ))
        else:
            for item in role_items:
                self.add_item(discord.ui.Button(label=item["label"], disabled=True, style=discord.ButtonStyle.secondary, row=1))

    @discord.ui.button(label="Schedule for Later", emoji="📅", style=discord.ButtonStyle.primary, row=0)
    async def schedule_later(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        from . import embed_studio_availability as availability
        denial = await availability.feature_denial(
            availability.FEATURE_SCHEDULING, self.session.guild_id,
            client=interaction.client, action="schedule a post")
        if denial is not None:
            return await _say(interaction, denial)
        if self.sent or self.session.project != self.preview_project or (
            self.session.channel_id != self.preview_channel_id
            or self.session.post_key != self.preview_post_key
        ):
            return await _say(interaction, "This preview changed or was already used. Reopen Preview.")
        if self.session.post_key:
            return await _say(interaction, "Scheduled edits of existing posts are not supported.")
        if self.session.project.get("role_actions"):
            return await _say(interaction, "Self-role panels require immediate moderator publishing.")
        if self.session.project.get("form"):
            return await _say(interaction, "Interactive forms require immediate moderator publishing.")
        await interaction.response.send_modal(StudioScheduleModal(
            self.session, preview=self.preview_project,
            channel_id=self.preview_channel_id, post_key=self.preview_post_key,
            review=self,
        ))

    @discord.ui.button(label="Repeat Daily/Weekly", emoji="🔁", style=discord.ButtonStyle.secondary, row=0)
    async def repeat_post(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        from . import embed_studio_availability as availability
        denial = await availability.feature_denial(
            availability.FEATURE_RECURRING, self.session.guild_id,
            client=interaction.client, action="repeat a post")
        if denial is not None:
            return await _say(interaction, denial)
        if (self.sent or self.session.project != self.preview_project
                or self.session.channel_id != self.preview_channel_id
                or self.session.post_key != self.preview_post_key
                or self.session.post_key):
            return await _say(interaction, "Preview changed or is already used. Reopen Preview.")
        if self.session.project.get("role_actions") or self.session.project.get("form"):
            return await _say(interaction, "Forms and role controls cannot repeat unattended.")
        from .embed_studio_campaign_ui import StudioCampaignModal
        await interaction.response.send_modal(StudioCampaignModal(
            self.session, preview=self.preview_project,
            channel_id=self.preview_channel_id, post_key=self.preview_post_key,
            review=self,
        ))

    @discord.ui.button(label="Post Now", emoji="✅", style=discord.ButtonStyle.success, row=0)
    async def publish(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if self.sent:
            return await _say(interaction, "This confirmation has already been used.")
        self.sent = True
        await interaction.response.defer(ephemeral=True, thinking=True)
        if (
            self.session.project != self.preview_project
            or self.session.channel_id != self.preview_channel_id
            or self.session.post_key != self.preview_post_key
        ):
            return await _say(interaction, "The draft changed after your preview. Open Preview again before publishing.")
        editing_existing = bool(self.session.post_key)
        channel = _target_channel(interaction, self.session)
        if channel is None:
            return await _say(interaction, "Destination was changed or removed; nothing posted.")
        blockers = _post_blockers(interaction, channel)
        if blockers:
            return await _say(interaction, "Publishing blocked: " + ", ".join(blockers))
        try:
            from .commands_ext.public_embed_group import _require_safe_embed_action
            if not await _require_safe_embed_action(interaction, action="send public embed"):
                return
            # Re-evaluate the same immutable preview snapshot after the
            # awaited permission/guild preflight, immediately before send.
            if (
                self.session.project != self.preview_project
                or self.session.channel_id != self.preview_channel_id
                or self.session.post_key != self.preview_post_key
            ):
                return await _say(interaction, "This preview is stale. Reopen it; nothing was sent.")
            draft = validate_project(self.preview_resolved_project)
            embeds = render_embeds(draft)
            # Role controls need durable server records. Storage is checked
            # BEFORE sending, and the published message is made inert if the
            # subsequent atomic tracking write fails.
            from .embed_studio_roles import member_role_view
            import secrets
            role_items = draft.get("role_actions") or []
            role_token = secrets.token_hex(16) if role_items else ""
            form = draft.get("form")
            from .embed_studio_forms import form_view,StudioFormError
            if form:
                from . import embed_studio_availability as availability
                denial = await availability.feature_denial(
                    availability.FEATURE_FORMS, self.session.guild_id,
                    client=interaction.client, action="publish a form")
                if denial is not None:
                    return await _say(interaction, denial)
            if form and not form.get("questions"):
                return await _say(interaction, "Add at least one question before publishing.")
            form_token = secrets.token_hex(16) if form else ""
            if role_items or form:
                _source,current_library=await _load_library(self.session.guild_id)
                if not self.session.post_key and len(current_library["published"]) >= 20 and all(
                    isinstance(item,dict) and (item.get("role_actions") or item.get("form"))
                    for item in current_library["published"].values()
                ):
                    return await _say(interaction,
                        "This server already has 20 active role or form posts. "
                        "Edit an older panel to remove its controls first.")
            if role_items:
                _source, current_library = await _load_library(self.session.guild_id)
                if not self.session.post_key and len(current_library["published"]) >= 20 and all(
                    isinstance(item, dict) and item.get("role_actions")
                    for item in current_library["published"].values()
                ):
                    return await _say(
                        interaction,
                        "This server already has 20 active role panels tracked. "
                        "Open Edit Posted and remove role actions from an older panel first."
                    )
                from .guild_config import get_guild_config
                from .commands_ext.public_self_roles_group import _profile_cosmetic_role_blocker
                guild_cfg = await get_guild_config(self.session.guild_id, refresh=True)
                for item in role_items:
                    role = channel.guild.get_role(int(item["role_id"]))
                    if not isinstance(role, discord.Role):
                        return await _say(interaction, "An offered role was deleted. Remove it from the draft first.")
                    reason = _profile_cosmetic_role_blocker(channel.guild, role, guild_cfg)
                    if reason:
                        return await _say(interaction, f"An offered role is no longer safe: {reason}")
            action_view = member_role_view(draft, role_token) if role_items else form_view(draft, form_token)
            message: discord.Message
            if self.session.post_key:
                source, lib = await _load_library(self.session.guild_id)
                item = lib["published"].get(self.session.post_key)
                if not item or int(item.get("channel_id", 0)) != channel.id:
                    return await _say(interaction, "Tracked post changed or was removed; no edit made.")
                try:
                    message = await channel.fetch_message(int(item["message_id"]))
                except discord.NotFound:
                    # The tracked Discord message was deleted, so there is
                    # nothing left to edit. Drop the stale tracking record —
                    # left in place it strands every later edit on this same
                    # raw error and keeps haunting Edit Posted — detach the
                    # session from it, and leave the draft untouched:
                    # publishing again posts the draft as a brand-new message.
                    dead_key = self.session.post_key
                    lib["published"].pop(dead_key, None)
                    try:
                        await _save_library(self.session.guild_id, source, lib)
                    except Exception:
                        pass
                    if self.session.post_key == dead_key:
                        self.session.post_key = ""
                    self.session.dirty = True
                    autosave_warning = await _autosave(interaction, self.session)
                    return await _say(
                        interaction,
                        "That post was deleted, so there is nothing to edit. "
                        "I removed its stale tracking entry, and your changes "
                        "are saved in your draft — publish again and it will "
                        "post as a brand-new message."
                        + (f"\n⚠️ {autosave_warning}" if autosave_warning else ""),
                    )
                if message.author.id != interaction.client.user.id:
                    return await _say(interaction, "This message is not owned by Dank Shield. Edit rejected.")
                await message.edit(content=draft["content"] or None, embeds=embeds, view=action_view, allowed_mentions=_MENTIONS)
            else:
                message = await channel.send(content=draft["content"] or None, embeds=embeds, view=action_view, allowed_mentions=_MENTIONS)
        except (StudioValidationError, discord.HTTPException, discord.Forbidden) as exc:
            return await _say(interaction, f"❌ Publish failed: {type(exc).__name__}. No further changes attempted.")
        except Exception as exc:
            return await _say(interaction, f"❌ Could not complete publish: {type(exc).__name__}. Verify the destination.")
        self.session.post_key = f"{channel.id}:{message.id}"
        self.session.dirty = True
        tracked = False
        try:
            source, lib = await _load_library(self.session.guild_id)
            lib["published"].pop(self.session.post_key, None)
            lib["published"][self.session.post_key] = {
                "channel_id": str(channel.id), "message_id": str(message.id),
                "by": str(interaction.user.id),
                "role_token": role_token, "role_mode": draft["role_mode"],
                "role_actions": draft["role_actions"],
                "form": deepcopy(form), "form_token": form_token,
            }
            # Store original Smart Text only for safe, normalized records.
            # Enrollment happens separately after explicit moderator approval.
            from .embed_studio_storage_v2 import storage_v2_enabled
            from .embed_studio_live_text import live_project, StudioLiveError
            if storage_v2_enabled():
                try:
                    lib["published"][self.session.post_key]["live_source"] = live_project(self.preview_project)
                except StudioLiveError:
                    pass
            from .embed_studio_core import trim_published_history
            try:
                lib["published"] = trim_published_history(lib["published"], max_posts=20)
            except StudioValidationError:
                tracked = False
            else:
                # If the bounded history retained all live role panels, a
                # link-only message may be published but not tracked. Do not
                # report a successful history save for an evicted reference.
                tracked = (
                    self.session.post_key in lib["published"]
                    and await _save_library(self.session.guild_id, source, lib)
                )
        except Exception:
            pass
        if (role_items or form) and not tracked:
            # Missing registry means live controls cannot be authorized. Avoid
            # showing unusable buttons, and do not pretend they are persistent.
            try:
                await message.edit(view=link_view(draft), allowed_mentions=_MENTIONS)
            except (discord.Forbidden, discord.HTTPException):
                pass  # Runtime still rejects all untracked or stale buttons.
        autosave_warning = await _autosave(interaction, self.session)
        await _say(
            interaction,
            f"✅ {'Updated' if editing_existing else 'Published'}: {message.jump_url}"
            + ("" if tracked else "\n⚠️ Message sent, but saved-post tracking is unavailable. Keep the message link.")
            + ("\n⚠️ " + autosave_warning if autosave_warning else ""),
        )

    @discord.ui.button(label="Back & Edit", emoji="✏️", style=discord.ButtonStyle.secondary, row=0)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


def _from_message(message: discord.Message) -> dict[str, Any]:
    project = {"version": 1, "content": str(message.content or ""), "embeds": [], "links": []}
    for embed in list(message.embeds)[:10]:
        d = embed.to_dict()
        author = d.get("author") or {}
        footer = d.get("footer") or {}
        project["embeds"].append({
            "title": str(d.get("title") or ""),
            "description": str(d.get("description") or ""),
            "title_url": str(d.get("url") or ""),
            "color": f"{int(d.get('color') or 0x5865F2):06X}",
            "author_name": str(author.get("name") or ""),
            "author_url": str(author.get("url") or ""),
            "author_icon": str(author.get("icon_url") or ""),
            "footer": str(footer.get("text") or ""),
            "footer_icon": str(footer.get("icon_url") or ""),
            "image": str((d.get("image") or {}).get("url") or ""),
            "thumbnail": str((d.get("thumbnail") or {}).get("url") or ""),
            "timestamp": bool(d.get("timestamp")),
            "fields": [
                {"name": f.get("name") or "", "value": f.get("value") or "", "inline": bool(f.get("inline"))}
                for f in d.get("fields", [])
            ],
        })
    for row in message.components:
        for button in getattr(row, "children", []):
            if getattr(button, "url", None) and getattr(button, "label", None):
                project["links"].append({"label": str(button.label), "url": str(button.url)})
    return validate_project(project)


def _tracked_post_picker_view(
    session: StudioSession, lib: dict[str, Any],
) -> _StudioPickerView:
    choices = [
        make_choice(f"#{item['channel_id']} • message {item['message_id']}", key)
        for key, item in list(lib["published"].items())[-20:]
    ] or [make_choice("No tracked messages yet", "none")]

    async def on_pick(interaction: discord.Interaction, key: str) -> None:
        if key == "none":
            return await _say(interaction, "Publish a message first; it will appear here.")
        await interaction.response.defer(ephemeral=True)
        try:
            _, fresh_lib = await _load_library(session.guild_id)
            item = fresh_lib["published"].get(key)
            if not item:
                return await _say(interaction, "This tracked message no longer exists.")
            channel = interaction.guild.get_channel(int(item["channel_id"]))
            if not isinstance(channel, discord.TextChannel):
                return await _say(interaction, "That channel is not available.")
            if _post_blockers(interaction, channel):
                return await _say(interaction, "Dank Shield cannot safely edit in that channel.")
            message = await channel.fetch_message(int(item["message_id"]))
            if message.author.id != interaction.client.user.id:
                return await _say(interaction, "This is not a Dank Shield message.")
            draft = _from_message(message)
            if isinstance(item.get("live_source"), dict):
                from .embed_studio_live_text import live_project
                try:
                    draft = live_project(item["live_source"])
                except StudioValidationError:
                    pass
        except Exception as exc:
            return await _say(interaction, f"Could not load this message: {_error(exc)}")
        session.history.append(session.project)
        session.history = session.history[-10:]
        draft["role_mode"] = str(item.get("role_mode") or "buttons")
        draft["role_actions"] = deepcopy(item.get("role_actions") or [])
        draft["form"] = deepcopy(item.get("form"))
        try:
            draft = validate_project(draft)
        except StudioValidationError:
            return await _say(interaction, "This tracked post has an unsupported role configuration. Ask staff to repair it.")
        session.project = draft
        session.dirty = True
        session.channel_id = int(channel.id)
        session.post_key = key
        session.active = 0
        await _home(interaction, session, notice="Published message loaded. Edits are not live until confirmed.")

    return _StudioPickerView(
        session, choices=choices, on_pick=on_pick,
        custom_id="dank:embed_studio:tracked_post:v1",
        placeholder="Choose a previously published embed",
        title="Edit a Published Embed",
        on_home=lambda interaction: _home(interaction, session),
        home_label="Back",
    )


class PublishedMenu(_StudioView):
    def __init__(self, session: StudioSession, lib: dict[str, Any]) -> None:
        super().__init__(session)
        self.lib = lib
        self.add_item(_StudioPickerOpenButton(
            session, label="Choose a Published Post", emoji="🛠️",
            custom_id="dank:embed_studio:tracked_post:open:v1", row=0,
            heading="🛠️ Edit a Published Embed",
            description="Choose one of the last 20 tracked Dank Shield posts to load it into your private editor.",
            build=lambda interaction: _tracked_post_picker_view(session, lib),
        ))

    @discord.ui.button(label="Live Text Updates", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def live_text(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        from .embed_studio_live_ui import open_live_text_menu
        await open_live_text_menu(interaction, self.session)

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _home(interaction, self.session)


async def _published(interaction: discord.Interaction, session: StudioSession) -> None:
    await interaction.response.defer(ephemeral=True)
    try:
        _, lib = await _load_library(session.guild_id)
    except Exception as exc:
        return await _say(interaction, _error(exc))
    await _menu(
        interaction, heading="🛠️ Edit a Published Embed",
        description="Select one of the last 20 tracked Dank Shield posts. We'll fetch the actual message, then let you edit a private copy. Publishing applies the changes to that same bot-owned message.",
        view=PublishedMenu(session, lib),
    )


async def open_embed_studio(interaction: discord.Interaction) -> None:
    """Shared entry point; no extra app-command budget and no public changes."""
    if not _staff(interaction):
        return await _say(interaction, "❌ Embed Studio requires Manage Server or Administrator.")
    if interaction.response.is_done():
        # Calls from an already-acknowledged Feature Center create their own
        # private editor; they never alter the public panel.
        pass
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    session = StudioSession(guild_id=int(interaction.guild.id), owner_id=int(interaction.user.id))
    notice = ""
    try:
        _, lib = await _load_library(session.guild_id)
        if _restore_snapshot(session, lib):
            notice = "Recovered your last autosaved draft. Nothing was published."
    except (StudioValidationError, ValueError, TypeError):
        notice = "Saved draft could not be read; starting a new local draft."
    except Exception:
        notice = "Storage unavailable; editing works, but autosave will need to recover."
    await _home(interaction, session, notice=notice)
