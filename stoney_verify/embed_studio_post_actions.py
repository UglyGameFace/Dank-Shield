"""Delete Post and Repost for Post Builder published posts.

One shared implementation behind every Published-menu surface (Edit
Posted post actions, the Sticky Posts choice screen, the Polls control
screen):

- **Delete Post** removes the tracked Discord message and the published
  record together, behind a two-step confirm that names the post. The
  author's saved draft is never touched, so the post can be published
  again later. A message that is already gone still removes the record;
  a message Discord refuses to delete (missing Manage Messages) keeps
  the record, and the copy says so — Felix is never told a post is
  deleted when it is not.
- **Repost** is one tap for a post whose message was deleted: it
  re-checks the message first (a stale missing-flag refuses and clears
  itself), then sends a fresh copy from the record's saved source,
  repoints the same record at the new message, and restores stickiness
  if the post was sticky when it was deleted. Polls restart at zero
  votes; ended polls and Live Text posts are refused in plain words.

Deletion *detection* lives in the Studio sticky runtime
(``on_message_delete`` plus startup reconcile): it flags the record
``message_missing`` and preserves ``was_sticky`` — see
:func:`stoney_verify.embed_studio_post_labels.flag_record_missing`.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional

import discord

from .embed_studio_core import StudioValidationError, render_embeds, validate_project
from .embed_studio_post_labels import (
    channel_label,
    channel_names_from_guild,
    clear_missing_flag,
    post_identity,
)

_MENTIONS = discord.AllowedMentions.none()


async def _load_library(guild_id: int) -> tuple[Any, dict[str, Any]]:
    from .embed_studio_ui import _load_library as load_library

    return await load_library(guild_id)


async def _save_library(guild_id: int, source: Any, lib: dict[str, Any]) -> bool:
    from .embed_studio_ui import _save_library as save_library

    return await save_library(guild_id, source, lib)


def _ids(item: Any) -> tuple[int, int]:
    if not isinstance(item, dict):
        return 0, 0
    try:
        return int(item.get("channel_id") or 0), int(item.get("message_id") or 0)
    except (TypeError, ValueError):
        return 0, 0


def _is_live_text(item: Any) -> bool:
    return bool(
        isinstance(item, dict)
        and (str(item.get("live_nonce") or "") or isinstance(item.get("live_source"), dict))
    )


async def _remove_record(guild_id: int, post_key: str) -> bool:
    """Remove one published record through the CAS save path."""
    source, lib = await _load_library(int(guild_id))
    if post_key not in lib.get("published", {}):
        return True
    lib["published"].pop(post_key, None)
    return bool(await _save_library(int(guild_id), source, lib))


def _drop_sticky_index(client: Any, channel_id: int, post_key: str) -> None:
    if client is None:
        return
    from .embed_studio_sticky import studio_sticky_runtime

    runtime = studio_sticky_runtime(client)
    if runtime is None:
        return
    entry = runtime.entry_for(int(channel_id))
    if entry is not None and entry.post_key == str(post_key):
        runtime.remove_sticky(int(channel_id))


async def delete_published_post(
    guild_id: int, post_key: str, *, guild: Any = None, client: Any = None,
) -> tuple[bool, str]:
    """Delete one tracked post's message and record. (ok, plain words)."""
    try:
        _source, lib = await _load_library(int(guild_id))
    except Exception:
        return False, "Post Builder storage is unavailable right now. Nothing was changed."
    item = lib.get("published", {}).get(post_key)
    if not isinstance(item, dict):
        return False, "That tracked post no longer exists. Nothing was changed."
    channel_id, message_id = _ids(item)
    name = post_identity(item)
    channel_text = channel_label(item, channel_names_from_guild(guild))

    message_missing = bool(item.get("message_missing"))
    if not message_missing and guild is not None and channel_id > 0 and message_id > 0:
        channel = guild.get_channel(channel_id) if hasattr(guild, "get_channel") else None
        if isinstance(channel, discord.TextChannel):
            from .startup_guards.discord_api_safety import fetch_message_with_api_safety

            try:
                message = await fetch_message_with_api_safety(
                    channel, message_id, label="studio delete post")
            except discord.NotFound:
                message_missing = True
                message = None
            except discord.Forbidden:
                return False, (
                    f"I couldn't delete **{name}** in {channel_text} — Dank Shield "
                    "may be missing Manage Messages there. The post is still up "
                    "and still tracked in Post Builder; nothing was changed."
                )
            except discord.HTTPException:
                return False, (
                    f"I couldn't reach the message for **{name}**, so I left the "
                    "post tracked in Post Builder. Nothing was changed — try again in a moment."
                )
            if not message_missing and message is not None:
                try:
                    await message.delete()
                except discord.NotFound:
                    message_missing = True
                except discord.Forbidden:
                    return False, (
                        f"I couldn't delete **{name}** in {channel_text} — Dank Shield "
                        "may be missing Manage Messages there. The post is still up "
                        "and still tracked in Post Builder; nothing was changed."
                    )
                except discord.HTTPException:
                    return False, (
                        f"Discord wouldn't delete the message for **{name}**, so I "
                        "left the post tracked in Post Builder. Nothing was changed."
                    )
    # The message is gone (deleted above, already gone, flagged gone, or
    # its channel is unreachable): now remove the tracking record.
    try:
        removed = await _remove_record(int(guild_id), str(post_key))
    except Exception:
        removed = False
    if not removed:
        return False, (
            "The message is gone from Discord, but I couldn't remove the post "
            "from Post Builder just now. Run Delete Post again and it will "
            "finish the job — your saved draft was not touched."
        )
    _drop_sticky_index(client, channel_id, str(post_key))
    if message_missing:
        return True, (
            "The message was already deleted in Discord, so I just removed it "
            "from Post Builder. Your saved draft was not deleted — you can "
            "publish it again any time."
        )
    return True, (
        f"Post deleted. **{name}** is gone from {channel_text} and removed "
        "from Post Builder. Your saved draft was not deleted — you can "
        "publish it again any time."
    )


async def repost_published_post(
    guild_id: int, post_key: str, *, guild: Any = None, client: Any = None,
) -> tuple[bool, str]:
    """Repost one tracked post whose message was deleted. (ok, words)."""
    try:
        _source, lib = await _load_library(int(guild_id))
    except Exception:
        return False, "Post Builder storage is unavailable right now. Nothing was changed."
    item = lib.get("published", {}).get(post_key)
    if not isinstance(item, dict):
        return False, "That tracked post no longer exists. Nothing was changed."
    channel_id, message_id = _ids(item)
    name = post_identity(item)
    channel_names = channel_names_from_guild(guild)
    channel_text = channel_label(item, channel_names)
    if guild is None or channel_id <= 0:
        return False, "That tracked post has no channel saved. Nothing was changed."
    channel = guild.get_channel(channel_id) if hasattr(guild, "get_channel") else None
    if not isinstance(channel, discord.TextChannel):
        return False, f"The channel for **{name}** is not available, so I couldn't repost it."

    if _is_live_text(item):
        return False, (
            "This post has Live Text updates on, and Live Text works by editing "
            "one fixed message in place — it can't be reposted. Publish it again "
            "from your draft instead."
        )

    poll: Optional[dict[str, Any]] = None
    if isinstance(item.get("poll"), dict):
        from .embed_studio_polls import validate_poll

        try:
            poll = validate_poll(item["poll"])
        except StudioValidationError:
            return False, "That poll's saved details couldn't be read, so I couldn't repost it."
        if poll["state"] == "ended":
            return False, (
                "That poll had already ended, so there's nothing to restart. "
                "Create a new poll if you want to run it again."
            )

    # Repost defends itself against a stale flag: if the message is
    # actually still up, there is nothing to repost.
    if message_id > 0:
        from .startup_guards.discord_api_safety import fetch_message_with_api_safety

        try:
            await fetch_message_with_api_safety(
                channel, message_id, label="studio repost check")
        except discord.NotFound:
            pass  # Confirmed gone — repost below.
        except discord.Forbidden:
            return False, (
                f"I couldn't check {channel_text} for **{name}** — Dank Shield may "
                "be missing permissions there. Nothing was reposted, so the post "
                "can't end up doubled."
            )
        except discord.HTTPException:
            return False, (
                f"I couldn't check whether **{name}** is still up, so I didn't "
                "repost it — that keeps the post from ending up doubled."
            )
        else:
            # Still up: clear the stale flag (and revive stickiness the
            # flag had parked) and refuse.
            try:
                source, fresh = await _load_library(int(guild_id))
                current = fresh.get("published", {}).get(post_key)
                if isinstance(current, dict) and current.get("message_missing"):
                    was_sticky = bool(current.get("was_sticky"))
                    clear_missing_flag(current)
                    if was_sticky:
                        current["was_sticky"] = False
                        current["sticky"] = {
                            "enabled": True,
                            "last_sent_at": datetime.now(timezone.utc).isoformat(),
                        }
                    await _save_library(int(guild_id), source, fresh)
            except Exception:
                pass
            return False, "That post is still up — nothing to repost."

    # Build the fresh copy from what the record itself stores.
    send_content: Any = None
    send_embeds: list[discord.Embed] = []
    send_view: Any = None
    if poll is not None:
        from .embed_studio_polls import poll_embed, poll_view

        fresh_poll = validate_poll({**poll, "votes": {}, "state": "active"})
        send_embeds = [poll_embed(fresh_poll)]
        send_view = poll_view(fresh_poll, str(item.get("poll_token") or ""))
    else:
        project_raw = item.get("repost_source") or item.get("live_source")
        if not isinstance(project_raw, dict):
            return False, (
                "I don't have a saved copy of that post's words — it was posted "
                "before Post Builder started keeping one. Publish it again from "
                "your draft instead."
            )
        try:
            project = validate_project(deepcopy(project_raw))
        except (StudioValidationError, ValueError, TypeError):
            return False, "That post's saved copy couldn't be read, so I couldn't repost it."
        project["role_mode"] = str(item.get("role_mode") or "buttons")
        project["role_actions"] = deepcopy(item.get("role_actions") or [])
        project["form"] = deepcopy(item.get("form"))
        try:
            project = validate_project(project)
        except (StudioValidationError, ValueError, TypeError):
            return False, "That post's saved copy couldn't be read, so I couldn't repost it."
        from .embed_studio_sticky import _message_view

        send_content = project["content"] or None
        send_embeds = render_embeds(project)
        send_view = _message_view(item, project)

    try:
        new_message = await channel.send(
            content=send_content, embeds=send_embeds, view=send_view,
            allowed_mentions=_MENTIONS,
        )
    except (discord.Forbidden, discord.HTTPException):
        return False, (
            f"Dank Shield couldn't post in {channel_text}, so **{name}** is still "
            "marked as deleted. Nothing was changed."
        )

    was_sticky = bool(item.get("was_sticky")) or bool(
        isinstance(item.get("sticky"), dict) and item["sticky"].get("enabled"))
    now = datetime.now(timezone.utc)
    try:
        source, fresh = await _load_library(int(guild_id))
        current = fresh.get("published", {}).get(post_key)
        if not isinstance(current, dict) or int(current.get("message_id") or 0) != message_id:
            raise StudioValidationError("Tracked post changed while reposting.")
        current["message_id"] = str(int(new_message.id))
        clear_missing_flag(current)
        if poll is not None:
            current["poll"] = {**validate_poll(current["poll"]), "votes": {}, "state": "active"}
        if was_sticky:
            current["was_sticky"] = False
            current["sticky"] = {"enabled": True, "last_sent_at": now.isoformat()}
        saved = await _save_library(int(guild_id), source, fresh)
    except Exception:
        saved = False
    if not saved:
        # Tracking is authoritative: an untracked copy would be a
        # duplicate nobody can edit, so roll the fresh copy back.
        try:
            await new_message.delete()
        except Exception:
            pass
        return False, (
            "The repost couldn't be saved for tracking, so I removed the fresh "
            "copy again. The post is still marked as deleted — try Repost again."
        )

    if was_sticky and client is not None:
        from .embed_studio_sticky import StudioStickyEntry, studio_sticky_runtime

        runtime = studio_sticky_runtime(client)
        if runtime is not None:
            runtime.set_sticky(StudioStickyEntry(
                guild_id=int(guild_id), channel_id=channel_id,
                post_key=str(post_key), last_sent_at=now,
            ))
    if poll is not None:
        return True, (
            f"Post is back up in {channel_text}. It's a fresh poll, so votes "
            "start over from zero."
        )
    if was_sticky:
        return True, (
            f"Post is back up in {channel_text}, and it's sticky again — it "
            "will keep moving to the bottom as people chat."
        )
    return True, f"Post is back up in {channel_text}."


async def mark_published_post_missing(
    guild_id: int, post_key: str, *, expected_message_id: int = 0,
) -> bool:
    """Flag one record message_missing via the CAS save. Best-effort."""
    from .embed_studio_post_labels import flag_record_missing

    try:
        source, lib = await _load_library(int(guild_id))
        item = lib.get("published", {}).get(post_key)
        if not isinstance(item, dict):
            return False
        if expected_message_id and int(item.get("message_id") or 0) != int(expected_message_id):
            return False
        if not flag_record_missing(item):
            return True
        return bool(await _save_library(int(guild_id), source, lib))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Post Builder UI: the shared post-actions screen, the Delete Post
# confirm, and the buttons other action screens reuse. Every control
# uses the house picker framework or a stable custom_id, per Felix's
# standing picker rule.
# ---------------------------------------------------------------------------

from .embed_studio_ui import (  # noqa: E402
    StudioSession,
    _StudioView,
    _menu,
    _published,
    _say,
)


def post_action_description(
    item: dict[str, Any], *, guild: Any = None, notice: str = "",
) -> str:
    """Plain-words summary of one post for an action screen."""
    from .embed_studio_sticky import post_is_sticky

    channel_text = channel_label(item, channel_names_from_guild(guild))
    lines = [f"**{post_identity(item)}**", f"Channel: {channel_text}"]
    if bool(item.get("message_missing")):
        lines.append(
            "⚠️ The message was deleted in Discord. Repost brings it back; "
            "Delete Post removes it from Post Builder for good."
        )
    elif post_is_sticky(item) or bool(item.get("was_sticky")):
        lines.append("This post is sticky — it is re-posted at the bottom of its channel as people chat.")
    if notice:
        lines.insert(0, notice)
    return "\n".join(lines)


async def open_delete_post_confirm(
    interaction: discord.Interaction, session: StudioSession, key: str,
) -> None:
    """Two-step confirm: names the post and says exactly what happens."""
    try:
        _source, lib = await _load_library(session.guild_id)
    except Exception as exc:
        from .embed_studio_ui import _error

        return await _say(interaction, _error(exc))
    item = lib.get("published", {}).get(key)
    if not isinstance(item, dict):
        return await _say(interaction, "That tracked post no longer exists.")
    channel_text = channel_label(item, channel_names_from_guild(interaction.guild))
    description = (
        f"Delete **{post_identity(item)}** from {channel_text}?\n\n"
        "This deletes the message in Discord and removes the post from "
        "Post Builder. Your saved draft is **not** deleted — you can "
        "publish it again later.\n\n"
        "If the message was already deleted in Discord, confirming just "
        "removes the post from Post Builder."
    )
    await _menu(
        interaction, heading="🗑️ Delete Post?",
        description=description,
        view=StudioDeletePostConfirmView(session, key),
    )


async def run_repost(
    interaction: discord.Interaction, session: StudioSession, key: str,
    *, back: Any = None,
) -> None:
    """One tap: repost, then show the post's action screen with the result."""
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    ok, message = await repost_published_post(
        session.guild_id, key, guild=interaction.guild, client=interaction.client,
    )
    await open_post_actions(
        interaction, session, key, notice=("✅ " if ok else "❌ ") + message,
    )


async def open_post_actions(
    interaction: discord.Interaction, session: StudioSession, key: str,
    *, notice: str = "",
) -> None:
    """The Published menu's post-actions screen for one picked post."""
    try:
        _source, lib = await _load_library(session.guild_id)
    except Exception as exc:
        from .embed_studio_ui import _error

        return await _say(interaction, _error(exc))
    item = lib.get("published", {}).get(key)
    if not isinstance(item, dict):
        return await _say(interaction, "That tracked post no longer exists.")
    missing = bool(item.get("message_missing"))
    await _menu(
        interaction, heading="🛠️ Post Actions",
        description=post_action_description(item, guild=interaction.guild, notice=notice),
        view=StudioPostActionsView(session, key, missing=missing),
    )


class StudioPostActionsView(_StudioView):
    def __init__(self, session: StudioSession, key: str, *, missing: bool) -> None:
        super().__init__(session)
        self.key = key
        self.missing = bool(missing)
        self.edit.disabled = self.missing
        self.repost.disabled = not self.missing

    @discord.ui.button(
        label="Edit This Post", emoji="✏️", style=discord.ButtonStyle.primary,
        custom_id="dank:embed_studio:post_actions_edit:v1", row=0,
    )
    async def edit(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        from .embed_studio_ui import _load_tracked_post_into_editor

        await _load_tracked_post_into_editor(interaction, self.session, self.key)

    @discord.ui.button(
        label="Repost", emoji="📨", style=discord.ButtonStyle.success,
        custom_id="dank:embed_studio:repost:v1", row=0,
    )
    async def repost(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await run_repost(interaction, self.session, self.key)

    @discord.ui.button(
        label="Delete Post", emoji="🗑️", style=discord.ButtonStyle.danger,
        custom_id="dank:embed_studio:delete_post:v1", row=1,
    )
    async def delete_post(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await open_delete_post_confirm(interaction, self.session, self.key)

    @discord.ui.button(
        label="Back", emoji="◀️", style=discord.ButtonStyle.secondary,
        custom_id="dank:embed_studio:post_actions_back:v1", row=1,
    )
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _published(interaction, self.session)


class StudioDeletePostConfirmView(_StudioView):
    def __init__(self, session: StudioSession, key: str) -> None:
        super().__init__(session)
        self.key = key
        self.used = False

    @discord.ui.button(
        label="Delete Post", emoji="🗑️", style=discord.ButtonStyle.danger,
        custom_id="dank:embed_studio:delete_confirm:v1", row=0,
    )
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if self.used:
            return await _say(interaction, "This request was already submitted.")
        self.used = True
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok, message = await delete_published_post(
            self.session.guild_id, self.key,
            guild=interaction.guild, client=interaction.client,
        )
        if not ok:
            return await _say(interaction, "❌ " + message)
        await _menu(
            interaction, heading="🗑️ Post Deleted",
            description="✅ " + message,
            view=StudioPostDeletedView(self.session),
        )

    @discord.ui.button(
        label="Cancel", emoji="◀️", style=discord.ButtonStyle.secondary,
        custom_id="dank:embed_studio:delete_cancel:v1", row=0,
    )
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await open_post_actions(interaction, self.session, self.key)


class StudioPostDeletedView(_StudioView):
    @discord.ui.button(
        label="Back to Published", emoji="◀️", style=discord.ButtonStyle.secondary,
        custom_id="dank:embed_studio:delete_done_back:v1", row=0,
    )
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await _published(interaction, self.session)


__all__ = [
    "StudioDeletePostConfirmView",
    "StudioPostActionsView",
    "StudioPostDeletedView",
    "delete_published_post",
    "mark_published_post_missing",
    "open_delete_post_confirm",
    "open_post_actions",
    "post_action_description",
    "repost_published_post",
    "run_repost",
]
