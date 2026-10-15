"""One menu-first Embed Studio shared by Community Tools and Server Setup.

No new slash-command root, background worker, or global draft cache. A draft
belongs to its staff interaction; named templates and bot post references use
the existing per-guild atomic config store.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable
from datetime import datetime, timezone
import secrets

import discord

from .embed_studio_core import (
    STUDIO_KEY, MAX_EMBEDS, MAX_FIELDS, MAX_TEMPLATES,
    StudioValidationError, new_project, validate_project, render_embeds,
    link_view, default_embed, parse_library, library_payload, https_url,
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

    def apply(self, mutation: Callable[[dict[str, Any]], None]) -> None:
        candidate = deepcopy(self.project)
        mutation(candidate)
        checked = validate_project(candidate)
        self.history.append(self.project)
        self.history = self.history[-10:]
        self.project = checked
        self.active = min(self.active, len(self.project["embeds"]) - 1)

    def undo(self) -> bool:
        if not self.history:
            return False
        self.project = self.history.pop()
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


def _summary(session: StudioSession) -> discord.Embed:
    project = session.project
    current = project["embeds"][session.active]
    embed = discord.Embed(
        title="🧱 Dank Shield Embed Studio",
        description=(
            "Build your message with the buttons below. The next screen shows "
            "only what you need. Nothing goes public until you review and publish."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Current draft",
        value=(
            f"Embed **{session.active + 1}/{len(project['embeds'])}**"
            f" • {len(current['fields'])}/25 fields"
            f" • {len(project['links'])}/5 link buttons"
            f" • {len(session.history)} undo steps"
        ),
        inline=False,
    )
    embed.add_field(
        name="Destination",
        value=f"<#{session.channel_id}>" if session.channel_id else "Choose a channel below",
        inline=True,
    )
    embed.add_field(
        name="Mode",
        value="Edit a previously published message" if session.post_key else "New draft",
        inline=True,
    )
    embed.set_footer(text="Text • Appearance • Media • Fields • Links • Layout • Save • Preview")
    return embed


def _preview_embed(session: StudioSession) -> discord.Embed:
    return render_embeds(session.project)[session.active]


async def _home(interaction: discord.Interaction, session: StudioSession, *, notice: str = "") -> None:
    message = (
        ("✅ " + notice + "\n" if notice else "")
        + "Private editor • choose a section; Publish requires confirmation."
    )
    kwargs = {
        "content": message,
        "embeds": [_summary(session), _preview_embed(session)],
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


class DestinationSelect(discord.ui.ChannelSelect):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(
            placeholder="1. Choose where to publish…",
            min_values=1, max_values=1, row=4,
            channel_types=[discord.ChannelType.text],
        )
        self.session = session

    async def callback(self, interaction: discord.Interaction) -> None:
        chosen = self.values[0] if self.values else None
        guild = interaction.guild
        channel = guild.get_channel(int(getattr(chosen, "id", 0) or 0)) if guild else None
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != self.session.guild_id:
            return await _say(interaction, "Choose a normal text channel in this server.")
        self.session.channel_id = int(channel.id)
        if self.session.post_key:
            self.session.post_key = ""  # Never edit a post after switching destination.
        await _home(interaction, self.session, notice="Destination selected.")


class StudioHomeView(_StudioView):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(session)
        self.add_item(DestinationSelect(session))

    @discord.ui.button(label="Write", emoji="✏️", style=discord.ButtonStyle.primary, row=0)
    async def write(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(StudioTextModal(self.session))

    @discord.ui.button(label="Appearance", emoji="🎨", style=discord.ButtonStyle.secondary, row=0)
    async def appearance(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(StudioAppearanceModal(self.session))

    @discord.ui.button(label="Images", emoji="🖼️", style=discord.ButtonStyle.secondary, row=0)
    async def images(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await interaction.response.send_modal(StudioImagesModal(self.session))

    @discord.ui.button(label="Fields", emoji="📋", style=discord.ButtonStyle.secondary, row=0)
    async def fields(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _fields(interaction, self.session)

    @discord.ui.button(label="Link Buttons", emoji="🔗", style=discord.ButtonStyle.secondary, row=1)
    async def links(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _links(interaction, self.session)

    @discord.ui.button(label="Embeds", emoji="🧩", style=discord.ButtonStyle.secondary, row=1)
    async def embeds(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _embeds(interaction, self.session)

    @discord.ui.button(label="Undo", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def undo(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not self.session.undo():
            return await _say(interaction, "No changes to undo.")
        await _home(interaction, self.session, notice="Last edit undone.")

    @discord.ui.button(label="Templates", emoji="💾", style=discord.ButtonStyle.secondary, row=2)
    async def templates(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _templates(interaction, self.session)

    @discord.ui.button(label="Edit Posted", emoji="🛠️", style=discord.ButtonStyle.secondary, row=2)
    async def edit_posted(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _published(interaction, self.session)

    @discord.ui.button(label="Preview & Publish", emoji="👁️", style=discord.ButtonStyle.success, row=3)
    async def preview(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _review(interaction, self.session)


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
        self.body_input = discord.ui.TextInput(label="Description", required=False, style=discord.TextStyle.paragraph, max_length=4000, default=e["description"])
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


class FieldSelect(discord.ui.Select):
    def __init__(self, session: StudioSession) -> None:
        fields = session.project["embeds"][session.active]["fields"]
        choices = [
            discord.SelectOption(label=f"{i + 1}. {f['name'][:85]}", value=str(i))
            for i, f in enumerate(fields[:25])
        ]
        if not choices:
            choices = [discord.SelectOption(label="No fields added yet", value="none")]
        super().__init__(placeholder="Select a field to edit, move or remove", options=choices, row=0)
        self.session = session

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "none":
            return await _say(interaction, "Press Add Field first.")
        await _menu(interaction, heading="Field controls", description="Edit, move up/down, or remove this field.", view=FieldActions(self.session, int(self.values[0])))


class FieldListView(_StudioView):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(session)
        self.add_item(FieldSelect(session))

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


class LinkSelect(discord.ui.Select):
    def __init__(self, session: StudioSession) -> None:
        options = [
            discord.SelectOption(label=f"{i + 1}. {link['label'][:80]}", value=str(i))
            for i, link in enumerate(session.project["links"])
        ]
        if not options:
            options = [discord.SelectOption(label="No link buttons yet", value="none")]
        super().__init__(placeholder="Choose a URL button to remove", options=options, row=0)
        self.session = session

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "none":
            return await _say(interaction, "Press Add URL Button first.")
        idx = int(self.values[0])
        self.session.apply(lambda p: p["links"].pop(idx))
        await _links(interaction, self.session)


class LinkMenu(_StudioView):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(session)
        self.add_item(LinkSelect(session))

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
    source = await get_guild_config(guild_id, refresh=True)
    if str(source.get("source") or "").startswith("unavailable:"):
        raise StudioValidationError("Template storage is offline. Draft and preview still work.")
    return source, parse_library(source)


async def _save_library(guild_id: int, source: Any, lib: dict[str, Any]) -> bool:
    from .guild_config import compare_and_swap_guild_config_key
    value = library_payload(lib)
    ok, _saved = await compare_and_swap_guild_config_key(
        guild_id, STUDIO_KEY, expected=source.get(STUDIO_KEY),
        value=value, source="dank_embed_studio",
    )
    return bool(ok)


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


class TemplateSelect(discord.ui.Select):
    def __init__(self, session: StudioSession, lib: dict[str, Any]) -> None:
        self.session = session
        choices = [
            discord.SelectOption(label=str(item.get("name") or "Untitled")[:90], value=key)
            for key, item in list(lib["templates"].items())[:25]
        ]
        if not choices:
            choices = [discord.SelectOption(label="No saved templates", value="none")]
        super().__init__(placeholder="Choose a template to load", options=choices, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        key = self.values[0]
        if key == "none":
            return await _say(interaction, "Save your first template from this screen.")
        await interaction.response.defer(ephemeral=True)
        try:
            _source, lib = await _load_library(self.session.guild_id)
            item = lib["templates"].get(key)
            if not item:
                return await _say(interaction, "That template was removed. Refresh this menu.")
            draft = validate_project(item["project"])
        except Exception as exc:
            return await _say(interaction, f"Could not load template: {_error(exc)}")
        self.session.history.append(self.session.project)
        self.session.history = self.session.history[-10:]
        self.session.project = draft
        self.session.active = 0
        self.session.post_key = ""
        await _home(interaction, self.session, notice="Template loaded. Nothing posted.")


class TemplateMenu(_StudioView):
    def __init__(self, session: StudioSession, lib: dict[str, Any]) -> None:
        super().__init__(session)
        self.add_item(TemplateSelect(session, lib))

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


class DeleteTemplateSelect(discord.ui.Select):
    def __init__(self, session: StudioSession, lib: dict[str, Any]) -> None:
        self.session = session
        choices = [
            discord.SelectOption(label=str(item.get("name") or "Untitled")[:90], value=key)
            for key, item in lib["templates"].items()
        ] or [discord.SelectOption(label="No saved templates", value="none")]
        super().__init__(placeholder="Choose template to REMOVE", options=choices, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        key = self.values[0]
        if key == "none":
            return await _say(interaction, "There is no template to remove.")
        await _menu(interaction, heading="Delete template?", description="This deletes a saved template, not any published message. Confirm to continue.", view=ConfirmDeleteTemplateView(self.session, key))


class DeleteTemplateMenu(_StudioView):
    def __init__(self, session: StudioSession, lib: dict[str, Any]) -> None:
        super().__init__(session)
        self.add_item(DeleteTemplateSelect(session, lib))

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _templates(interaction, self.session)


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
    await _menu(interaction, heading="Manage Templates", description="Select a template to remove; a second confirmation is required.", view=DeleteTemplateMenu(session, lib))


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
        embeds = render_embeds(session.project)
    except StudioValidationError as exc:
        return await _say(interaction, str(exc))
    status = (
        f"👁️ PRIVATE PREVIEW for {channel.mention}. "
        + ("Update the tracked message" if session.post_key else "Publish a NEW message")
        + " below. The real embeds are shown here. Nothing has been sent."
    )
    if session.project["content"]:
        status += "\nPlain message: " + session.project["content"][:1300]
    kwargs = {"content": status[:1950], "embeds": embeds, "view": StudioReviewView(session), "allowed_mentions": _MENTIONS}
    if interaction.response.is_done():
        await interaction.edit_original_response(**kwargs)
    elif interaction.message is not None:
        await interaction.response.edit_message(**kwargs)
    else:
        await interaction.response.send_message(**kwargs, ephemeral=True)


class StudioReviewView(_StudioView):
    def __init__(self, session: StudioSession) -> None:
        super().__init__(session, timeout=300)
        self.sent = False
        # Link buttons on the preview serve as a useful live test.
        for link in session.project["links"]:
            self.add_item(discord.ui.Button(label=link["label"], url=link["url"], style=discord.ButtonStyle.link, row=1))

    @discord.ui.button(label="Publish / Save Edit", emoji="✅", style=discord.ButtonStyle.success, row=0)
    async def publish(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if self.sent:
            return await _say(interaction, "This confirmation has already been used.")
        self.sent = True
        await interaction.response.defer(ephemeral=True, thinking=True)
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
            draft = validate_project(self.session.project)
            embeds = render_embeds(draft)
            action_view = link_view(draft)
            message: discord.Message
            if self.session.post_key:
                source, lib = await _load_library(self.session.guild_id)
                item = lib["published"].get(self.session.post_key)
                if not item or int(item.get("channel_id", 0)) != channel.id:
                    return await _say(interaction, "Tracked post changed or was removed; no edit made.")
                message = await channel.fetch_message(int(item["message_id"]))
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
        tracked = False
        try:
            source, lib = await _load_library(self.session.guild_id)
            lib["published"].pop(self.session.post_key, None)
            lib["published"][self.session.post_key] = {
                "channel_id": str(channel.id), "message_id": str(message.id),
                "by": str(interaction.user.id),
            }
            while len(lib["published"]) > 20:
                lib["published"].pop(next(iter(lib["published"])))
            tracked = await _save_library(self.session.guild_id, source, lib)
        except Exception:
            pass
        await _say(
            interaction,
            f"✅ {'Updated' if editing_existing else 'Published'}: {message.jump_url}"
            + ("" if tracked else "\n⚠️ Message sent, but saved-post tracking is unavailable. Keep the message link."),
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


class TrackedPostSelect(discord.ui.Select):
    def __init__(self, session: StudioSession, lib: dict[str, Any]) -> None:
        self.session = session
        options = [
            discord.SelectOption(label=f"#{item['channel_id']} • message {item['message_id']}", value=key)
            for key, item in list(lib["published"].items())[-20:]
        ] or [discord.SelectOption(label="No tracked messages yet", value="none")]
        super().__init__(placeholder="Choose a previously published embed", options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        key = self.values[0]
        if key == "none":
            return await _say(interaction, "Publish a message first; it will appear here.")
        await interaction.response.defer(ephemeral=True)
        try:
            _, lib = await _load_library(self.session.guild_id)
            item = lib["published"].get(key)
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
        except Exception as exc:
            return await _say(interaction, f"Could not load this message: {_error(exc)}")
        self.session.history.append(self.session.project)
        self.session.history = self.session.history[-10:]
        self.session.project = draft
        self.session.channel_id = int(channel.id)
        self.session.post_key = key
        self.session.active = 0
        await _home(interaction, self.session, notice="Published message loaded. Edits are not live until confirmed.")


class PublishedMenu(_StudioView):
    def __init__(self, session: StudioSession, lib: dict[str, Any]) -> None:
        super().__init__(session)
        self.add_item(TrackedPostSelect(session, lib))

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
    session = StudioSession(guild_id=int(interaction.guild.id), owner_id=int(interaction.user.id))
    await _home(interaction, session)
