from __future__ import annotations

"""General-purpose server Live Captions UI.

This surface deliberately reuses the single CommunityVoiceCaptionManager and
hardened per-speaker DAVE receive path. It does not create a second voice
receiver, consent store, or transcription engine.
"""

from typing import Any, Optional

import discord

from ..guild_config import get_guild_config, invalidate_guild_config, upsert_guild_config
from ..ui.picker import DankChannelSelect
from ..community_voice_caption_runtime import (
    ensure_community_voice_caption_manager,
    live_captions_enabled,
    server_caption_scope_id,
)
from ..community_voice_receive import VoiceReceiveUnavailable, voice_receive_capability
from ..interaction_guard import safe_defer_interaction
from ..panel_lifecycle import PRIVATE_MENU_TTL_SECONDS, private_menu_lifecycle_text
from .public_owner_authority import (
    interaction_has_administrator_authority,
    interaction_has_manage_guild_authority,
    interaction_is_actual_guild_owner,
)


CAPTION_OUTPUT_CHANNEL_KEY = "live_captions_output_channel_id"
CAPTION_VOICE_SCOPE_KEY = "live_captions_voice_scope_mode"
CAPTION_ALLOWED_VOICE_CHANNELS_KEY = "live_captions_allowed_voice_channel_ids"
CAPTION_ALLOWED_VOICE_CATEGORIES_KEY = "live_captions_allowed_voice_category_ids"
CAPTION_EXCLUDED_VOICE_CHANNELS_KEY = "live_captions_excluded_voice_channel_ids"
CAPTION_OUTPUT_MODE_KEY = "live_captions_output_mode"
CAPTION_LANGUAGE_CODES_KEY = "live_captions_language_codes"

_SUPPORTED_CAPTION_LANGUAGE_CODES = frozenset({
    "af-ZA", "am-ET", "ar-EG", "hy-AM", "as-IN", "az-AZ", "be-BY",
    "bn-BD", "bn-IN", "bs-BA", "bg-BG", "rup-BG", "my-MM",
    "yue-Hant-HK", "ca-ES", "ceb", "km-KH", "hr-HR", "cs-CZ",
    "da-DK", "nl-NL", "en-GB", "en-IN", "en-US", "et-EE", "fa-IR",
    "fil-PH", "fi-FI", "fr-FR", "gl-ES", "ka-GE", "de-DE", "el-GR",
    "gu-IN", "ha-NG", "he-IL", "hi-IN", "hu-HU", "is-IS", "id-ID",
    "it-IT", "ja-JP", "jv-ID", "kea-CV", "kn-IN", "kk-KZ", "ko-KR",
    "ky-KG", "lv-LV", "ln-CD", "lt-LT", "mk-MK", "ms-MY", "ml-IN",
    "mt-MT", "cmn-Hans-CN", "mr-IN", "mn-MN", "ne-NP", "nb-NO",
    "or-IN", "pl-PL", "pt-BR", "pt-PT", "pa-IN", "pa-Guru-IN",
    "ro-RO", "ru-RU", "sr-RS", "sd-Arab-IN", "sk-SK", "sl-SI",
    "es-419", "es-US", "sw-KE", "sv-SE", "tg-TJ", "te-IN", "th-TH",
    "tr-TR", "uk-UA", "uz-UZ", "vi-VN",
})
_CAPTION_LANGUAGE_BY_CODE = {
    value.casefold(): value for value in _SUPPORTED_CAPTION_LANGUAGE_CODES
}
_CAPTION_LANGUAGE_ALIASES = {
    "english": "en-US",
    "english us": "en-US",
    "american english": "en-US",
    "english uk": "en-GB",
    "british english": "en-GB",
    "english india": "en-IN",
    "spanish": "es-419",
    "spanish latin america": "es-419",
    "spanish us": "es-US",
    "french": "fr-FR",
    "german": "de-DE",
    "italian": "it-IT",
    "portuguese": "pt-BR",
    "portuguese brazil": "pt-BR",
    "portuguese portugal": "pt-PT",
    "dutch": "nl-NL",
    "polish": "pl-PL",
    "russian": "ru-RU",
    "ukrainian": "uk-UA",
    "turkish": "tr-TR",
    "arabic": "ar-EG",
    "hebrew": "he-IL",
    "farsi": "fa-IR",
    "hindi": "hi-IN",
    "bengali": "bn-IN",
    "punjabi": "pa-IN",
    "gujarati": "gu-IN",
    "marathi": "mr-IN",
    "telugu": "te-IN",
    "kannada": "kn-IN",
    "malayalam": "ml-IN",
    "nepali": "ne-NP",
    "thai": "th-TH",
    "vietnamese": "vi-VN",
    "indonesian": "id-ID",
    "malay": "ms-MY",
    "filipino": "fil-PH",
    "japanese": "ja-JP",
    "korean": "ko-KR",
    "mandarin": "cmn-Hans-CN",
    "mandarin chinese": "cmn-Hans-CN",
    "chinese": "cmn-Hans-CN",
    "cantonese": "yue-Hant-HK",
    "swedish": "sv-SE",
    "norwegian": "nb-NO",
    "danish": "da-DK",
    "finnish": "fi-FI",
    "czech": "cs-CZ",
    "slovak": "sk-SK",
    "slovenian": "sl-SI",
    "croatian": "hr-HR",
    "serbian": "sr-RS",
    "romanian": "ro-RO",
    "bulgarian": "bg-BG",
    "greek": "el-GR",
    "hungarian": "hu-HU",
    "icelandic": "is-IS",
    "estonian": "et-EE",
    "latvian": "lv-LV",
    "lithuanian": "lt-LT",
    "georgian": "ka-GE",
    "armenian": "hy-AM",
    "azerbaijani": "az-AZ",
    "kazakh": "kk-KZ",
    "uzbek": "uz-UZ",
    "belarusian": "be-BY",
    "swahili": "sw-KE",
    "hausa": "ha-NG",
    "afrikaans": "af-ZA",
    "amharic": "am-ET",
    "catalan": "ca-ES",
    "galician": "gl-ES",
    "cebuano": "ceb",
    "javanese": "jv-ID",
    "khmer": "km-KH",
    "mongolian": "mn-MN",
}


# Discord String Selects support at most 25 options, so the complete Gemini
# language set is split into human-readable groups. CI asserts exact coverage.
_CAPTION_LANGUAGE_GROUPS: dict[str, dict[str, Any]] = {
    "english_western_europe": {
        "label": "English & Western Europe",
        "emoji": "🌍",
        "description": "English, French, German, Spanish, Portuguese, Nordic and nearby languages.",
        "options": (
            ("English (United States)", "en-US"),
            ("English (Great Britain)", "en-GB"),
            ("Catalan", "ca-ES"),
            ("Danish", "da-DK"),
            ("Dutch", "nl-NL"),
            ("Finnish", "fi-FI"),
            ("French", "fr-FR"),
            ("Galician", "gl-ES"),
            ("German", "de-DE"),
            ("Icelandic", "is-IS"),
            ("Italian", "it-IT"),
            ("Maltese", "mt-MT"),
            ("Norwegian", "nb-NO"),
            ("Portuguese (Brazil)", "pt-BR"),
            ("Portuguese (Portugal)", "pt-PT"),
            ("Spanish (Latin America)", "es-419"),
            ("Spanish (United States)", "es-US"),
            ("Swedish", "sv-SE"),
        ),
    },
    "eastern_europe": {
        "label": "Central & Eastern Europe",
        "emoji": "🗺️",
        "description": "Slavic, Baltic, Balkan and neighboring European languages.",
        "options": (
            ("Belarusian", "be-BY"),
            ("Bosnian", "bs-BA"),
            ("Bulgarian", "bg-BG"),
            ("Bulgarian (Aromanian)", "rup-BG"),
            ("Croatian", "hr-HR"),
            ("Czech", "cs-CZ"),
            ("Estonian", "et-EE"),
            ("Greek", "el-GR"),
            ("Hungarian", "hu-HU"),
            ("Latvian", "lv-LV"),
            ("Lithuanian", "lt-LT"),
            ("Macedonian", "mk-MK"),
            ("Polish", "pl-PL"),
            ("Romanian", "ro-RO"),
            ("Russian", "ru-RU"),
            ("Serbian", "sr-RS"),
            ("Slovak", "sk-SK"),
            ("Slovenian", "sl-SI"),
            ("Ukrainian", "uk-UA"),
        ),
    },
    "caucasus_central_asia": {
        "label": "Caucasus & Central Asia",
        "emoji": "🏔️",
        "description": "Armenian, Georgian, Turkic and Central Asian languages.",
        "options": (
            ("Armenian", "hy-AM"),
            ("Azerbaijani", "az-AZ"),
            ("Georgian", "ka-GE"),
            ("Kazakh", "kk-KZ"),
            ("Kyrgyz", "ky-KG"),
            ("Mongolian", "mn-MN"),
            ("Tajik", "tg-TJ"),
            ("Turkish", "tr-TR"),
            ("Uzbek", "uz-UZ"),
        ),
    },
    "middle_east_africa": {
        "label": "Middle East & Africa",
        "emoji": "🌍",
        "description": "Arabic, Hebrew, Persian and supported African languages.",
        "options": (
            ("Afrikaans", "af-ZA"),
            ("Amharic", "am-ET"),
            ("Arabic (Egypt)", "ar-EG"),
            ("Farsi", "fa-IR"),
            ("Hausa", "ha-NG"),
            ("Hebrew", "he-IL"),
            ("Kabuverdianu", "kea-CV"),
            ("Lingala", "ln-CD"),
            ("Swahili (Kenya)", "sw-KE"),
        ),
    },
    "south_asia": {
        "label": "South Asia",
        "emoji": "🌏",
        "description": "Indian subcontinent languages and English (India).",
        "options": (
            ("Assamese", "as-IN"),
            ("Bengali (Bangladesh)", "bn-BD"),
            ("Bengali (India)", "bn-IN"),
            ("English (India)", "en-IN"),
            ("Gujarati", "gu-IN"),
            ("Hindi", "hi-IN"),
            ("Kannada", "kn-IN"),
            ("Malayalam", "ml-IN"),
            ("Marathi", "mr-IN"),
            ("Nepali", "ne-NP"),
            ("Oriya", "or-IN"),
            ("Punjabi", "pa-IN"),
            ("Punjabi (Gurmukhi script)", "pa-Guru-IN"),
            ("Sindhi (Arabic script)", "sd-Arab-IN"),
            ("Telugu", "te-IN"),
        ),
    },
    "east_southeast_asia": {
        "label": "East & Southeast Asia",
        "emoji": "🌏",
        "description": "Chinese, Japanese, Korean and Southeast Asian languages.",
        "options": (
            ("Burmese", "my-MM"),
            ("Cantonese (Traditional)", "yue-Hant-HK"),
            ("Cebuano", "ceb"),
            ("Central Khmer", "km-KH"),
            ("Filipino", "fil-PH"),
            ("Indonesian", "id-ID"),
            ("Japanese", "ja-JP"),
            ("Javanese", "jv-ID"),
            ("Korean", "ko-KR"),
            ("Malay", "ms-MY"),
            ("Mandarin Chinese (Simplified)", "cmn-Hans-CN"),
            ("Thai", "th-TH"),
            ("Vietnamese", "vi-VN"),
        ),
    },
}


def _caption_language_group_for_code(code: str) -> str:
    target = str(code or "").strip()
    for key, group in _CAPTION_LANGUAGE_GROUPS.items():
        for _label, option_code in group["options"]:
            if option_code == target:
                return key
    return ""


def _caption_language_picker_embed(
    *,
    current_hint: str,
    group_key: str = "",
) -> discord.Embed:
    if group_key:
        group = _CAPTION_LANGUAGE_GROUPS[group_key]
        description = (
            f"Choose your spoken language from **{group['label']}**. "
            "This accuracy hint applies only to your voice. Other speakers keep their own language settings."
        )
        title = f"🌐 My Caption Language · {group['label']}"
    else:
        description = (
            "Choose **Auto** for Gemini's full supported multilingual/code-switching mode, "
            "or choose a language group and then your spoken language for stronger recognition. "
            "This setting affects only your voice."
        )
        title = "🌐 My Caption Language"

    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Current",
        value=f"**{_personal_language_label(current_hint)}**",
        inline=False,
    )
    return embed


def _normalize_personal_language_hint(value: Any) -> str:
    raw = " ".join(str(value or "").replace("_", "-").strip().split())
    folded = raw.casefold()
    if not folded or folded in {"auto", "automatic", "all", "all languages"}:
        return ""
    alias = _CAPTION_LANGUAGE_ALIASES.get(folded)
    if alias:
        return alias
    if folded in _CAPTION_LANGUAGE_BY_CODE:
        return _CAPTION_LANGUAGE_BY_CODE[folded]
    raise ValueError(
        "Use Auto, a supported language name such as English, or a supported BCP-47 code such as en-US."
    )


def _personal_language_label(code: str) -> str:
    value = str(code or "").strip()
    if not value:
        return "Auto · all supported languages"
    names = {
        "en-US": "English (US)",
        "en-GB": "English (UK)",
        "en-IN": "English (India)",
        "es-419": "Spanish (Latin America)",
        "es-US": "Spanish (US)",
        "fr-FR": "French",
        "de-DE": "German",
        "hi-IN": "Hindi",
        "ja-JP": "Japanese",
        "ko-KR": "Korean",
        "cmn-Hans-CN": "Mandarin Chinese",
        "yue-Hant-HK": "Cantonese",
        "pt-BR": "Portuguese (Brazil)",
    }
    return f"{names.get(value, value)} · accuracy hint"


def _id_set(value: Any) -> set[int]:
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
    else:
        text = str(value or "").strip()
        if not text:
            return set()
        items = [part.strip() for part in text.replace(";", ",").split(",")]
    out: set[int] = set()
    for item in items:
        try:
            parsed = int(str(item).strip())
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            out.add(parsed)
    return out


def _scope_mode(cfg: Any) -> str:
    value = str(getattr(cfg, "get", lambda *_: "all")(CAPTION_VOICE_SCOPE_KEY, "all") or "all").strip().lower()
    return "selected" if value == "selected" else "all"


def _caption_output_mode(cfg: Any) -> str:
    value = str(getattr(cfg, "get", lambda *_: "original")(CAPTION_OUTPUT_MODE_KEY, "original") or "original").strip().lower()
    if value == "english":
        return "english"
    if value in {"bilingual", "both", "original_english"}:
        return "bilingual"
    return "original"


def _caption_output_mode_label(mode: str) -> str:
    return {
        "english": "English translation",
        "bilingual": "Original + English",
    }.get(str(mode or "").strip().lower(), "Original language")


def _configured_output_channel(guild: discord.Guild, cfg: Any) -> Optional[discord.TextChannel]:
    try:
        channel_id = int(str(cfg.get(CAPTION_OUTPUT_CHANNEL_KEY) or "0"))
    except (TypeError, ValueError, AttributeError):
        return None
    channel = guild.get_channel(channel_id) if channel_id > 0 else None
    return channel if isinstance(channel, discord.TextChannel) else None


def _caption_output_missing_perms(
    channel: discord.TextChannel,
    bot_member: Optional[discord.Member],
) -> list[str]:
    if bot_member is None:
        return ["Bot member unavailable"]
    perms = channel.permissions_for(bot_member)
    return [
        label
        for label, allowed in (
            ("View Channel", perms.view_channel),
            ("Send Messages", perms.send_messages),
            ("Read Message History", perms.read_message_history),
        )
        if not allowed
    ]


def _voice_allowed_by_config(
    voice: discord.VoiceChannel,
    cfg: Any,
) -> tuple[bool, str]:
    voice_id = int(voice.id)
    excluded = _id_set(cfg.get(CAPTION_EXCLUDED_VOICE_CHANNELS_KEY))
    if voice_id in excluded:
        return False, "That voice channel is explicitly excluded from Live Captions."

    if _scope_mode(cfg) == "all":
        return True, ""

    allowed_channels = _id_set(cfg.get(CAPTION_ALLOWED_VOICE_CHANNELS_KEY))
    allowed_categories = _id_set(cfg.get(CAPTION_ALLOWED_VOICE_CATEGORIES_KEY))
    category_id = int(getattr(voice, "category_id", 0) or 0)
    if voice_id in allowed_channels or (category_id > 0 and category_id in allowed_categories):
        return True, ""
    return False, "That voice channel is not included in this server's selected Live Captions scope."


def _target_lines(guild: discord.Guild, ids: set[int], *, category: bool = False) -> str:
    if not ids:
        return "None"
    lines: list[str] = []
    for target_id in sorted(ids):
        target = guild.get_channel(target_id)
        if category and isinstance(target, discord.CategoryChannel):
            lines.append(f"• **{target.name}**")
        elif not category and isinstance(target, discord.VoiceChannel):
            lines.append(f"• {target.mention}")
        else:
            lines.append(f"• Missing/deleted ID `{target_id}`")
        if len(lines) >= 8:
            remaining = len(ids) - len(lines)
            if remaining > 0:
                lines.append(f"• …and {remaining} more")
            break
    return "\n".join(lines)


async def _save_caption_config(
    interaction: discord.Interaction,
    updates: dict[str, Any],
) -> Any:
    guild = interaction.guild
    if guild is None:
        raise RuntimeError("Live Captions setup must be used inside a server.")
    if not _staff_authorized(interaction):
        raise PermissionError("Manage Server or Administrator is required.")

    patch = {
        "__config_write_mode": "explicit_override",
        "__config_write_source": "live_captions_setup",
        **dict(updates),
    }
    await upsert_guild_config(int(guild.id), patch)
    invalidate_guild_config(int(guild.id))
    fresh = await get_guild_config(int(guild.id), refresh=True)

    for key, expected in updates.items():
        actual = fresh.get(key)
        if isinstance(expected, list):
            if _id_set(actual) != _id_set(expected):
                raise RuntimeError(f"Live Captions setting {key} did not persist.")
        elif str(actual or "") != str(expected or ""):
            raise RuntimeError(f"Live Captions setting {key} did not persist.")
    return fresh


def _staff_authorized(interaction: discord.Interaction) -> bool:
    return bool(
        interaction_is_actual_guild_owner(interaction)
        or interaction_has_administrator_authority(interaction)
        or interaction_has_manage_guild_authority(interaction)
    )


async def _bot_owner_authorized(interaction: discord.Interaction) -> bool:
    checker = getattr(interaction.client, "is_owner", None)
    if not callable(checker):
        return False
    try:
        return bool(await checker(interaction.user))
    except Exception:
        return False


def _current_voice_channel(interaction: discord.Interaction) -> Optional[discord.VoiceChannel]:
    channel = getattr(getattr(interaction.user, "voice", None), "channel", None)
    return channel if isinstance(channel, discord.VoiceChannel) else None


def _soak_pipeline_diagnosis(status: dict[str, Any]) -> str:
    health = status.get("health") if isinstance(status.get("health"), dict) else {}
    connection = status.get("receive_connection") if isinstance(status.get("receive_connection"), dict) else {}
    raw_udp = int(health.get("raw_udp_packets") or 0)
    speaking_signals = int(health.get("gateway_speaking_signals") or 0)
    frames_seen = int(health.get("frames_seen") or 0)
    frames_routed = int(health.get("frames_routed") or 0)
    dave_present = bool(connection.get("dave_session_present"))
    dave_ready = bool(connection.get("dave_session_ready"))
    mapped_ssrcs = int(connection.get("mapped_ssrcs") or 0)
    reader_listening = bool(connection.get("reader_listening"))
    not_consented = int(health.get("frames_not_consented") or 0)
    unknown = int(health.get("frames_unknown_source") or 0)
    mismatch = int(health.get("frames_source_mismatch") or 0)
    malformed = int(health.get("frames_malformed_pcm") or 0)
    opus_drops = int(health.get("opus_decode_drops") or 0)
    reader_failures = int(health.get("reader_failures") or 0)
    reader_error = str(connection.get("reader_error") or "").strip()
    transcribed = int(status.get("segments_transcribed") or 0)
    published = int(status.get("segments_published") or 0)
    empty = int(status.get("segments_empty") or 0)
    failures = int(status.get("segment_failures") or 0)
    provider_skipped = int(status.get("provider_skipped") or 0)
    provider_blocked_reason = str(status.get("provider_blocked_reason") or "").strip()
    receive_recoveries = int(status.get("receive_recoveries") or 0)
    receive_recovery_failures = int(status.get("receive_recovery_failures") or 0)
    recovery_reason = str(status.get("last_receive_recovery_reason") or "").strip()
    last_failure = str(status.get("last_failure") or "").strip()

    if provider_blocked_reason:
        return "🔴 **Transcription provider is blocked.** " + provider_blocked_reason
    if failures > 0:
        return (
            "🔴 **Transcription/processing failure.** "
            + (last_failure or "Check the Dank Shield host logs for the latest caption error.")
        )
    if frames_seen <= 0:
        if receive_recovery_failures > 0:
            return (
                "🔴 **The Discord voice media transport stalled and automatic recovery failed.** "
                + (recovery_reason or "Stop/start captions once to create a completely fresh voice session.")
            )
        if receive_recoveries > 0:
            return (
                f"🟠 **Dank Shield automatically rebuilt the stalled Discord voice receive transport ({receive_recoveries}×).** "
                "Speak again for a few seconds; this panel is now measuring the fresh receive connection."
            )
        if speaking_signals > 0 and reader_listening and dave_ready and mapped_ssrcs > 0:
            return (
                "🟠 **Discord signaled that an opted-in user started speaking, but no PCM arrived.** "
                "Dank Shield is automatically checking/rebuilding the stalled UDP receive transport instead of leaving this session stuck."
            )
        if not reader_listening:
            if reader_error:
                return (
                    "🔴 **The voice receive reader stopped after an error.** "
                    f"{reader_error}"
                )
            return (
                "🔴 **The voice receive reader is not listening.** "
                f"Recorded reader failures: **{reader_failures}** • corrupt Opus drops: **{opus_drops}**."
            )
        if raw_udp <= 0:
            return (
                "🔴 **No UDP voice packets reached Dank Shield.** The bot joined voice, but the receive socket saw no traffic while you spoke."
            )
        if not dave_present:
            return (
                "🔴 **UDP is arriving, but Discord did not establish a DAVE session.** The receive stack cannot safely decode encrypted voice."
            )
        if not dave_ready:
            return (
                "🔴 **UDP is arriving, but the DAVE session is not ready.** The MLS/DAVE handshake or epoch setup has not completed."
            )
        if mapped_ssrcs <= 1:
            return (
                "🔴 **UDP and DAVE are active, but no remote speaker SSRC is mapped yet.** The receive gateway is not resolving the speaking user."
            )
        return (
            "🔴 **UDP and DAVE are active, but no PCM reached the hardened speaker sink.** "
            "The receive dependency is dropping/decode-failing before Dank Shield's per-speaker boundary."
        )
    if frames_routed <= 0:
        if unknown > 0 or mismatch > 0:
            return (
                "🔴 **Voice frames arrived but speaker identity could not be trusted.** "
                f"Unknown: **{unknown}** • mismatch: **{mismatch}**. Audio is being dropped instead of mixed."
            )
        if not_consented > 0:
            opted = status.get("opted_in_user_ids")
            if isinstance(opted, (list, tuple)) and opted:
                return (
                    "🟠 **PCM is arriving. The not-consented count is cumulative and includes frames from before opt-in.** "
                    "At least one speaker is opted in now; speak again and refresh. If transcription starts, this counter is historical rather than a current consent failure."
                )
            return (
                "🟠 **Voice frames are arriving but no speaker is currently opted in.** "
                "Press **Caption My Voice**, confirm the panel says your voice is opted in, then speak again."
            )
        if malformed > 0:
            return "🔴 **Voice frames arrived with invalid PCM and were dropped.**"
        return "🟠 **Voice frames are arriving but none have reached the caption queue yet.**"
    if transcribed <= 0:
        return (
            "🟡 **DAVE receive and speaker routing are working.** Speak for 2–5 seconds, then pause for about "
            "1 second so the current speech segment can close and be sent for transcription."
        )
    if published <= 0 and empty > 0:
        return "🟠 **Gemini answered, but the transcription was empty.** Try a longer, clearly spoken sentence."
    if published > 0:
        return "🟢 **DAVE receive → consent → transcription → Discord publishing is working.**"
    return "🟡 **Audio reached transcription, but no caption has published yet.** Press **Refresh** again after a short pause."


async def _defer_update(interaction: discord.Interaction) -> None:
    if await safe_defer_interaction(
        interaction,
        ephemeral=False,
        action_name="server_live_captions_component",
    ):
        return
    raise RuntimeError("Live Captions interaction acknowledgement failed")


async def _followup(interaction: discord.Interaction, content: str) -> None:
    await interaction.followup.send(
        content,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def _edit_original(
    interaction: discord.Interaction,
    *,
    content: Optional[str] = None,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    await interaction.edit_original_response(
        content=content,
        embed=embed,
        view=view,
        allowed_mentions=discord.AllowedMentions.none(),
    )


class _OwnedView(discord.ui.View):
    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=PRIVATE_MENU_TTL_SECONDS)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_id:
            return True
        await interaction.response.send_message(
            "Open your own `/captions` panel to control your Live Captions consent.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return False


async def build_server_live_captions_embed(
    interaction: discord.Interaction,
) -> discord.Embed:
    guild = interaction.guild
    if guild is None:
        return discord.Embed(
            title="📝 Live Captions",
            description="Live Captions are available inside Discord servers.",
            color=discord.Color.blurple(),
        )

    cfg = await get_guild_config(int(guild.id), refresh=True)
    manager = ensure_community_voice_caption_manager(interaction.client)
    capability = voice_receive_capability()
    enabled = live_captions_enabled()
    general_sid = server_caption_scope_id(int(guild.id))
    general = manager.status(general_sid)
    guild_status = manager.status_for_guild(int(guild.id))
    user_id = int(interaction.user.id)
    user_prefs = await manager.preferences_for_user(
        int(guild.id),
        user_id,
    )
    personal_hint = str(user_prefs.get("language_code") or "")
    remembered_auto = bool(user_prefs.get("auto_opt_in"))
    current_voice = _current_voice_channel(interaction)
    soak_active = bool(general.get("active") and general.get("soak_test"))
    bot_owner = await _bot_owner_authorized(interaction) if not enabled else False

    if soak_active:
        headline = (
            "🟣 **DAVE soak test running.** This is the bot-owner-only validation path; "
            "the global Live Captions feature remains locked for other servers."
        )
    elif not capability.available:
        headline = f"🔴 **Host not ready.** {capability.reason}."
    elif not enabled:
        headline = (
            "🟡 **Installed, validation locked.** The live DAVE receive path is deployed, "
            "but normal transcription remains disabled until the real Discord soak test is completed. "
            + (
                "As the Dank Shield bot owner, you can press **Start / Stop Captions** to run the controlled soak test in this server."
                if bot_owner
                else "No voice audio is captured while this lock is active."
            )
        )
    elif bool(general.get("active")):
        headline = "🟢 **General Live Captions are running in this server.**"
    elif bool(guild_status.get("active")):
        headline = (
            "🟠 **Another Live Captions session already owns this server's voice receiver.** "
            "Only one caption receiver may run per server at a time."
        )
    else:
        headline = (
            "🟢 **Ready for general server use.** A server owner/admin/Manage Server member "
            "can join a voice channel and start captions from this panel."
        )

    embed = discord.Embed(
        title="📝 Dank Shield Live Captions",
        description=(
            f"{headline}\n\n"
            "This works with ordinary Discord voice channels. It does **not** require a Community Hub gaming session."
        ),
        color=discord.Color.blurple(),
    )

    opus_source = str(capability.opus_library or "")
    opus_label = (
        "bundled libopus"
        if "opuslib_next" in opus_source.replace("\\", "/")
        else opus_source
        if opus_source and opus_source != "already-loaded"
        else "loaded"
        if capability.opus_available
        else "unavailable"
    )
    embed.add_field(
        name="Host voice stack",
        value=(
            f"discord.py: **{capability.discord_py_version}** • "
            f"DAVE: **{'ready' if capability.dave_available and capability.inbound_dave_decrypt_available else 'not ready'}**\n"
            f"Opus PCM decoder: **{'ready' if capability.opus_available else 'not ready'}** • source: **{opus_label}**"
        ),
        inline=False,
    )

    if bool(general.get("active")):
        voice_id = int(general.get("voice_channel_id") or 0)
        destination_id = int(general.get("destination_channel_id") or 0)
        opted = {
            int(value)
            for value in (general.get("opted_in_user_ids") or [])
            if str(value).isdigit()
        }
        user_opted = user_id in opted
        detected_map = general.get("speaker_detected_languages") if isinstance(general.get("speaker_detected_languages"), dict) else {}
        level_map = general.get("speaker_audio_rms_dbfs") if isinstance(general.get("speaker_audio_rms_dbfs"), dict) else {}
        detected_language = str(detected_map.get(str(user_id)) or "")
        input_level = level_map.get(str(user_id))
        diagnostic_line = ""
        if detected_language or input_level is not None:
            level_text = (
                f"{float(input_level):.1f} dBFS"
                if input_level is not None
                else "not measured"
            )
            diagnostic_line = (
                f"\nYour last audio: **{level_text}**"
                + (f" • Gemini detected: **{detected_language}**" if detected_language else "")
            )
        embed.add_field(
            name="Current session",
            value=(
                f"Voice: <#{voice_id}>\n"
                f"Captions: <#{destination_id}>\n"
                f"Mode: **{'DAVE soak test' if general.get('soak_test') else 'normal'}**\n"
                f"Languages: **{'Auto-detect 85+ + code-switching' if not general.get('language_codes') else ', '.join(general.get('language_codes') or [])}**\n"
                f"Text output: **{_caption_output_mode_label(str(general.get('output_mode') or 'original'))}**\n"
                f"Your language: **{_personal_language_label(personal_hint)}**\n"
                f"Auto-caption preference: **{'ON · remembered for this server' if remembered_auto else 'OFF'}**\n"
                f"Opted-in speakers: **{len(opted)}**\n"
                f"Your voice right now: **{'active' if user_opted else 'inactive'}**"
                f"{diagnostic_line}"
            ),
            inline=False,
        )
        if bool(general.get("soak_test")):
            health = general.get("health") if isinstance(general.get("health"), dict) else {}
            connection = general.get("receive_connection") if isinstance(general.get("receive_connection"), dict) else {}
            provider_skipped = int(general.get("provider_skipped") or 0)
            provider_live_connections = int(general.get("provider_live_connections") or 0)
            provider_live_reconnects = int(general.get("provider_live_reconnects") or 0)
            provider_audio_chunks = int(general.get("provider_audio_chunks_sent") or 0)
            provider_stream_ends = int(general.get("provider_audio_stream_ends") or 0)
            provider_interim_events = int(general.get("provider_interim_events") or 0)
            provider_final_events = int(general.get("provider_final_events") or 0)
            receive_recoveries = int(general.get("receive_recoveries") or 0)
            receive_recovery_failures = int(general.get("receive_recovery_failures") or 0)
            language_hint_mismatches = int(general.get("language_hint_mismatches") or 0)
            translation_requests = int(general.get("translation_requests") or 0)
            translation_failures = int(general.get("translation_failures") or 0)
            translation_skipped = int(general.get("translation_skipped") or 0)
            embed.add_field(
                name="DAVE soak telemetry",
                value=(
                    f"Raw UDP: **{int(health.get('raw_udp_packets') or 0)}** • sink PCM: **{int(health.get('frames_seen') or 0)}** • routed: **{int(health.get('frames_routed') or 0)}** • queue: **{int(general.get('queue_depth') or 0)}**\n"
                    f"Gateway speaking signals: **{int(health.get('gateway_speaking_signals') or 0)}** • receive recoveries: **{receive_recoveries}** • recovery failures: **{receive_recovery_failures}**\n"
                    f"DAVE ready: **{'yes' if connection.get('dave_session_ready') else 'no'}** • status: **{str(connection.get('dave_session_status') or 'none')[:24]}** • protocol: **{int(connection.get('dave_protocol_version') or 0)}** • epoch: **{int(connection.get('dave_epoch') or 0)}**\n"
                    f"Reader: **{'listening' if connection.get('reader_listening') else 'stopped'}** • mapped SSRCs: **{int(connection.get('mapped_ssrcs') or 0)}** • reader failures: **{int(health.get('reader_failures') or 0)}**\n"
                    f"Corrupt Opus dropped: **{int(health.get('opus_decode_drops') or 0)}** • not consented: **{int(health.get('frames_not_consented') or 0)}** • unknown source: **{int(health.get('frames_unknown_source') or 0)}** • identity mismatch: **{int(health.get('frames_source_mismatch') or 0)}**\n"
                    f"Malformed PCM: **{int(health.get('frames_malformed_pcm') or 0)}** • transcribed: **{int(general.get('segments_transcribed') or 0)}** • published: **{int(general.get('segments_published') or 0)}** • empty: **{int(general.get('segments_empty') or 0)}**\n"
                    f"Unclear: **{int(general.get('segments_unclear') or 0)}** • failures: **{int(general.get('segment_failures') or 0)}** • provider-skipped: **{provider_skipped}**\n"
                    f"Gemini Live connections: **{provider_live_connections}** • reconnects: **{provider_live_reconnects}** • audio chunks: **{provider_audio_chunks}** • stream ends: **{provider_stream_ends}**\n"
                    f"Gemini transcript events: interim **{provider_interim_events}** • final **{provider_final_events}** • language-hint mismatches: **{language_hint_mismatches}**\n"
                    f"Translations: **{translation_requests}** • translation skipped: **{translation_skipped}** • translation failures: **{translation_failures}**"
                ),
                inline=False,
            )
            reader_error = str(connection.get("reader_error") or "").strip()
            if reader_error:
                embed.add_field(
                    name="Receive worker error",
                    value=f"`{reader_error[:900]}`",
                    inline=False,
                )
            embed.add_field(
                name="Pipeline diagnosis",
                value=_soak_pipeline_diagnosis(general),
                inline=False,
            )
    elif bool(guild_status.get("active")):
        voice_id = int(guild_status.get("voice_channel_id") or 0)
        scope_kind = str(guild_status.get("scope_kind") or "")
        owner_name = "Community Hub Live Captions" if scope_kind == "community_hub" else "another Live Captions session"
        embed.add_field(
            name="Voice receiver in use",
            value=f"{owner_name} currently owns <#{voice_id}>. End that caption session before starting general captions.",
            inline=False,
        )
    else:
        embed.add_field(
            name="Voice target",
            value=(
                f"You are currently in <#{int(current_voice.id)}>."
                if current_voice is not None
                else "Join the voice channel you want captioned before pressing **Start / Stop Captions**."
            ),
            inline=False,
        )

    output = _configured_output_channel(guild, cfg)
    scope = _scope_mode(cfg)
    allowed_channels = _id_set(cfg.get(CAPTION_ALLOWED_VOICE_CHANNELS_KEY))
    allowed_categories = _id_set(cfg.get(CAPTION_ALLOWED_VOICE_CATEGORIES_KEY))
    excluded_channels = _id_set(cfg.get(CAPTION_EXCLUDED_VOICE_CHANNELS_KEY))
    output_mode = _caption_output_mode(cfg)
    if scope == "all":
        scope_text = f"All voice channels{f' except **{len(excluded_channels)}** excluded' if excluded_channels else ''}."
    else:
        scope_text = (
            f"Selected only: **{len(allowed_channels)}** voice channel(s) + "
            f"**{len(allowed_categories)}** voice categor{'y' if len(allowed_categories) == 1 else 'ies'}"
            f"{f' • **{len(excluded_channels)}** excluded' if excluded_channels else ''}."
        )
    embed.add_field(
        name="Server setup",
        value=(
            f"Output: {output.mention if output is not None else '**Not configured**'}\n"
            f"Voice access: {scope_text}\n"
            "Languages: **Auto-detect all Gemini Transcribe supported languages (85+) + code-switching**\n"
            f"Text output: **{_caption_output_mode_label(output_mode)}**\n"
            "Owners/admins can configure routing and language output from **Setup**. "
            "Community Hub captions remain session-scoped for routing but use the same server language/output setting."
        ),
        inline=False,
    )

    embed.add_field(
        name="Your remembered preference",
        value=(
            f"Auto-caption: **{'ON' if remembered_auto else 'OFF'}** for this server\n"
            f"Language: **{_personal_language_label(personal_hint)}**\n"
            "Turn auto-caption on once and Dank Shield will automatically activate your voice whenever Live Captions is running in a voice channel you join. Turn it off at any time to revoke that remembered consent."
        ),
        inline=False,
    )
    embed.add_field(
        name="How it works",
        value=(
            "1. Staff starts Live Captions in a voice channel.\n"
            "2. Each participant explicitly enables **Auto-Caption My Voice** once for that server.\n"
            "3. Dank Shield remembers that choice and automatically activates that member whenever they join the active captioned VC.\n"
            "4. Leaving the VC or stopping captions immediately clears active audio; the remembered preference stays until the member turns it off."
        ),
        inline=False,
    )
    embed.add_field(
        name="Speaker isolation & privacy",
        value=(
            "Discord speakers stay isolated before transcription. Overlapping users are not mixed together. "
            "Each participant can keep **My Language** on Auto or save a personal language hint for better recognition. "
            "Auto-caption consent and the language hint are remembered per server only after that member explicitly chooses them. "
            "Turning auto-caption off immediately blocks new audio and purges that speaker's buffered/queued/in-flight caption audio. "
            "Opted-in audio is sent to Google Gemini's transcription API for speech-to-text. This deployment uses Gemini's Free Tier, where Google states submitted content may be used to improve its products; Dank Shield itself does not save the audio. "
            "If a microphone already captures a TV, game audio, or another person in the same room, that sound is already part "
            "of that Discord user's source stream."
        ),
        inline=False,
    )
    embed.add_field(name="Control lifetime", value=private_menu_lifecycle_text(), inline=False)
    return embed



class CaptionLanguageGroupSelect(discord.ui.Select):
    def __init__(self, owner_id: int, current_hint: str) -> None:
        self.owner_id = int(owner_id)
        self.current_hint = str(current_hint or "")
        current_group = _caption_language_group_for_code(self.current_hint)
        options = [
            discord.SelectOption(
                label="Auto · All Supported Languages",
                value="__auto__",
                description="Automatic detection + code-switching across Gemini's supported languages.",
                emoji="✨",
                default=not bool(self.current_hint),
            )
        ]
        for key, group in _CAPTION_LANGUAGE_GROUPS.items():
            options.append(
                discord.SelectOption(
                    label=str(group["label"]),
                    value=key,
                    description=str(group["description"])[:100],
                    emoji=str(group["emoji"]),
                    default=key == current_group,
                )
            )
        super().__init__(
            placeholder="Choose Auto or a language group",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="dank:captions:server:language_group:v1",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await interaction.response.send_message(
                "Open your own /captions panel to set your spoken language.",
                ephemeral=True,
            )

        selected = str(self.values[0])
        manager = ensure_community_voice_caption_manager(interaction.client)
        if selected == "__auto__":
            await _defer_update(interaction)
            if interaction.guild is None:
                return await _followup(interaction, "Live Captions language preferences can only be changed inside a server.")
            await manager.set_user_language_hint(
                int(interaction.guild.id),
                int(interaction.user.id),
                "",
            )
            await _edit_original(
                interaction,
                embed=await build_server_live_captions_embed(interaction),
                view=ServerLiveCaptionsView(self.owner_id),
            )
            return await _followup(
                interaction,
                "✅ Your caption language is now **Auto · all supported languages**. Gemini can detect supported languages and code-switching for your voice.",
            )

        if selected not in _CAPTION_LANGUAGE_GROUPS:
            return await interaction.response.send_message(
                "❌ That language group is no longer available. Reopen /captions and try again.",
                ephemeral=True,
            )

        await _defer_update(interaction)
        await _edit_original(
            interaction,
            embed=_caption_language_picker_embed(
                current_hint=self.current_hint,
                group_key=selected,
            ),
            view=CaptionLanguageChoiceView(
                self.owner_id,
                selected,
                self.current_hint,
            ),
        )


class CaptionLanguageGroupView(_OwnedView):
    def __init__(self, owner_id: int, current_hint: str) -> None:
        super().__init__(owner_id)
        self.add_item(CaptionLanguageGroupSelect(owner_id, current_hint))

    @discord.ui.button(
        label="Back",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:captions:server:language_groups_back:v1",
        row=1,
    )
    async def back(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _defer_update(interaction)
        await _edit_original(
            interaction,
            embed=await build_server_live_captions_embed(interaction),
            view=ServerLiveCaptionsView(self.owner_id),
        )


class CaptionLanguageChoiceSelect(discord.ui.Select):
    def __init__(
        self,
        owner_id: int,
        group_key: str,
        current_hint: str,
    ) -> None:
        self.owner_id = int(owner_id)
        self.group_key = str(group_key)
        group = _CAPTION_LANGUAGE_GROUPS[self.group_key]
        options = [
            discord.SelectOption(
                label=label,
                value=code,
                description=f"Use {code} as your personal Gemini recognition hint.",
                default=str(current_hint or "") == code,
            )
            for label, code in group["options"]
        ]
        super().__init__(
            placeholder=f"Choose from {group['label']}",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"dank:captions:server:language_choice:{self.group_key}:v1"[:100],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await interaction.response.send_message(
                "Open your own /captions panel to set your spoken language.",
                ephemeral=True,
            )

        code = str(self.values[0])
        if code not in _SUPPORTED_CAPTION_LANGUAGE_CODES:
            return await interaction.response.send_message(
                "❌ That language is no longer in the supported transcription list.",
                ephemeral=True,
            )

        await _defer_update(interaction)
        if interaction.guild is None:
            return await _followup(interaction, "Live Captions language preferences can only be changed inside a server.")
        manager = ensure_community_voice_caption_manager(interaction.client)
        await manager.set_user_language_hint(
            int(interaction.guild.id),
            int(interaction.user.id),
            code,
        )
        await _edit_original(
            interaction,
            embed=await build_server_live_captions_embed(interaction),
            view=ServerLiveCaptionsView(self.owner_id),
        )
        await _followup(
            interaction,
            (
                f"✅ Your Live Captions language is now **{_personal_language_label(code)}**. "
                "Only your Gemini speaker session reconnects with this accuracy hint."
            ),
        )


class CaptionLanguageChoiceView(_OwnedView):
    def __init__(
        self,
        owner_id: int,
        group_key: str,
        current_hint: str,
    ) -> None:
        super().__init__(owner_id)
        self.current_hint = str(current_hint or "")
        self.add_item(
            CaptionLanguageChoiceSelect(
                owner_id,
                group_key,
                current_hint,
            )
        )

    @discord.ui.button(
        label="Language Groups",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:captions:server:language_choices_back:v1",
        row=1,
    )
    async def back(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _defer_update(interaction)
        await _edit_original(
            interaction,
            embed=_caption_language_picker_embed(current_hint=self.current_hint),
            view=CaptionLanguageGroupView(self.owner_id, self.current_hint),
        )


class ServerLiveCaptionsView(_OwnedView):
    @discord.ui.button(
        label="Start / Stop Captions",
        emoji="🎙️",
        style=discord.ButtonStyle.primary,
        custom_id="dank:captions:server:control:v1",
        row=0,
    )
    async def control(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _defer_update(interaction)
        guild = interaction.guild
        if guild is None:
            return await _followup(interaction, "Live Captions can only run inside a server.")
        if not _staff_authorized(interaction):
            return await _followup(
                interaction,
                "Only the server owner, an administrator, or someone with Manage Server can start or stop general Live Captions.",
            )

        manager = ensure_community_voice_caption_manager(interaction.client)
        sid = server_caption_scope_id(int(guild.id))
        status = manager.status(sid)

        if bool(status.get("active")):
            await manager.stop(sid, announce=True)
            await _edit_original(
                interaction,
                embed=await build_server_live_captions_embed(interaction),
                view=ServerLiveCaptionsView(self.owner_id),
            )
            return await _followup(
                interaction,
                "✅ General Live Captions stopped. Active audio admission and buffered caption audio were cleared. Members' remembered auto-caption preferences remain until they turn them off.",
            )

        soak_test = False
        if not live_captions_enabled():
            if not await _bot_owner_authorized(interaction):
                return await _followup(
                    interaction,
                    "❌ Live Captions are still globally validation-locked. Only the Dank Shield bot owner can run the controlled DAVE soak test before public enablement.",
                )
            soak_test = True

        other = manager.status_for_guild(int(guild.id))
        if bool(other.get("active")):
            return await _followup(
                interaction,
                "❌ Another Live Captions session already owns this server's voice receiver. End it before starting general captions.",
            )

        voice = _current_voice_channel(interaction)
        if voice is None:
            return await _followup(
                interaction,
                "Join the ordinary voice channel you want captioned, then press **Start / Stop Captions** again.",
            )

        cfg = await get_guild_config(int(guild.id), refresh=True)
        destination = _configured_output_channel(guild, cfg)
        if destination is None:
            return await _followup(
                interaction,
                "❌ Live Captions need an output channel first. Open **Setup** and choose an existing text channel or create **#live-captions**.",
            )
        allowed, reason = _voice_allowed_by_config(voice, cfg)
        if not allowed:
            return await _followup(interaction, f"❌ {reason}")

        bot_member = guild.me
        missing_output = _caption_output_missing_perms(destination, bot_member)
        if missing_output:
            return await _followup(
                interaction,
                f"❌ Dank Shield cannot use {destination.mention}: missing {', '.join(missing_output)}.",
            )

        try:
            await manager.start_server(
                guild_id=int(guild.id),
                voice_channel_id=int(voice.id),
                destination_channel_id=int(destination.id),
                soak_test=soak_test,
            )
        except VoiceReceiveUnavailable as exc:
            return await _followup(interaction, f"❌ {exc}")

        await _edit_original(
            interaction,
            embed=await build_server_live_captions_embed(interaction),
            view=ServerLiveCaptionsView(self.owner_id),
        )
        if soak_test:
            await _followup(
                interaction,
                "🧪 DAVE soak test started for this server only. The global feature is still locked. Remembered auto-caption members already in this VC are restored automatically. If yours is off, enable **Auto-Caption My Voice** once, speak normally, then use **Refresh**.",
            )
        else:
            await _followup(
                interaction,
                "✅ General Live Captions started. Members who previously enabled **Auto-Caption My Voice** for this server are restored automatically when they are in the captioned VC. Everyone else remains excluded until they explicitly enable it.",
            )

    @discord.ui.button(
        label="Auto-Caption My Voice",
        emoji="📝",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:captions:server:consent:v1",
        row=0,
    )
    async def consent(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _defer_update(interaction)
        guild = interaction.guild
        if guild is None:
            return await _followup(interaction, "Live Captions can only run inside a server.")

        manager = ensure_community_voice_caption_manager(interaction.client)
        sid = server_caption_scope_id(int(guild.id))
        status = manager.status(sid)
        if not bool(status.get("active")):
            other = manager.status_for_guild(int(guild.id))
            if bool(other.get("active")) and str(other.get("scope_kind") or "") == "community_hub":
                return await _followup(
                    interaction,
                    "Community Hub Live Captions are running instead. Use that Community Hub session's **Caption My Voice** control.",
                )
            return await _followup(
                interaction,
                "General Live Captions are not running in this server right now.",
            )

        voice = _current_voice_channel(interaction)
        target_voice_id = int(status.get("voice_channel_id") or 0)
        if voice is None or int(voice.id) != target_voice_id:
            return await _followup(
                interaction,
                f"Join <#{target_voice_id}> before opting your voice into this caption session.",
            )

        try:
            enabled = await manager.toggle_consent(sid, int(interaction.user.id))
        except VoiceReceiveUnavailable as exc:
            return await _followup(interaction, f"❌ {exc}")

        await _edit_original(
            interaction,
            embed=await build_server_live_captions_embed(interaction),
            view=ServerLiveCaptionsView(self.owner_id),
        )
        if enabled:
            return await _followup(
                interaction,
                "✅ **Auto-Caption My Voice is ON for this server and remembered.** While Live Captions is running, Dank Shield will automatically activate your voice when you are in the captioned VC. Your speaker stream stays isolated. Your saved **My Language** hint is reused automatically. Opted-in audio is sent to Google Gemini's Free Tier transcription service; Dank Shield does not save the audio. Press **Auto-Caption My Voice** again at any time to revoke this remembered consent.",
            )
        await _followup(
            interaction,
            "✅ **Auto-Caption My Voice is OFF for this server.** Your remembered consent was revoked, new audio is blocked, and buffered/queued/in-flight caption audio was purged.",
        )

    @discord.ui.button(
        label="My Language",
        emoji="🌐",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:captions:server:language:v1",
        row=0,
    )
    async def my_language(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        manager = ensure_community_voice_caption_manager(interaction.client)
        if interaction.guild is None:
            return await interaction.response.send_message(
                "Live Captions language preferences can only be changed inside a server.",
                ephemeral=True,
            )
        prefs = await manager.preferences_for_user(
            int(interaction.guild.id),
            int(interaction.user.id),
        )
        current = str(prefs.get("language_code") or "")
        await _defer_update(interaction)
        await _edit_original(
            interaction,
            embed=_caption_language_picker_embed(current_hint=current),
            view=CaptionLanguageGroupView(int(interaction.user.id), current),
        )

    @discord.ui.button(
        label="Refresh",
        emoji="🔄",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:captions:server:refresh:v1",
        row=0,
    )
    async def refresh(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _defer_update(interaction)
        await _edit_original(
            interaction,
            embed=await build_server_live_captions_embed(interaction),
            view=ServerLiveCaptionsView(self.owner_id),
        )

    @discord.ui.button(
        label="Setup",
        emoji="⚙️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:captions:server:setup:v1",
        row=1,
    )
    async def setup(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await interaction.response.send_message(
                "Only the server owner, an administrator, or someone with Manage Server can change Live Captions setup.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        await _defer_update(interaction)
        await _edit_original(
            interaction,
            embed=await build_server_live_captions_setup_embed(interaction),
            view=ServerLiveCaptionsSetupView(self.owner_id),
        )

    @discord.ui.button(
        label="Dank Shield Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:captions:server:home:v1",
        row=1,
    )
    async def home(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _defer_update(interaction)
        from .public_command_surface_v2 import CompactDankHomeView, _home_embed

        await _edit_original(
            interaction,
            embed=_home_embed(),
            view=CompactDankHomeView(self.owner_id),
        )

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        custom_id="dank:captions:server:close:v1",
        row=1,
    )
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _defer_update(interaction)
        try:
            await interaction.delete_original_response()
        except (discord.NotFound, discord.HTTPException):
            await _edit_original(
                interaction,
                content="Live Captions panel closed.",
                embed=None,
                view=None,
            )


async def build_server_live_captions_setup_embed(
    interaction: discord.Interaction,
) -> discord.Embed:
    guild = interaction.guild
    if guild is None:
        return discord.Embed(
            title="⚙️ Live Captions Setup",
            description="Live Captions setup is available inside Discord servers.",
            color=discord.Color.blurple(),
        )

    cfg = await get_guild_config(int(guild.id), refresh=True)
    output = _configured_output_channel(guild, cfg)
    mode = _scope_mode(cfg)
    allowed_channels = _id_set(cfg.get(CAPTION_ALLOWED_VOICE_CHANNELS_KEY))
    allowed_categories = _id_set(cfg.get(CAPTION_ALLOWED_VOICE_CATEGORIES_KEY))
    excluded_channels = _id_set(cfg.get(CAPTION_EXCLUDED_VOICE_CHANNELS_KEY))
    output_mode = _caption_output_mode(cfg)

    embed = discord.Embed(
        title="⚙️ Live Captions Setup",
        description=(
            "Configure ordinary server voice captions here. This does not change Community Hub caption routing: "
            "Hub sessions keep using their own session discussion/thread."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Caption output",
        value=(
            output.mention
            if output is not None
            else "Not configured. Choose an existing text channel or create the read-only **#live-captions** channel."
        ),
        inline=False,
    )
    embed.add_field(
        name="Language & translation",
        value=(
            "**Input:** Auto-detect 85+ supported languages and mid-conversation code-switching\n"
            f"**Output:** {_caption_output_mode_label(output_mode)}\n"
            "English conversion uses finalized transcript text only. Audio is never sent through a second translation pass."
        ),
        inline=False,
    )
    embed.add_field(
        name="Voice scope",
        value=(
            "**All voice channels**"
            if mode == "all"
            else "**Selected voice channels/categories only**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Selected voice channels",
        value=_target_lines(guild, allowed_channels),
        inline=False,
    )
    embed.add_field(
        name="Selected voice categories",
        value=_target_lines(guild, allowed_categories, category=True),
        inline=False,
    )
    embed.add_field(
        name="Excluded voice channels",
        value=_target_lines(guild, excluded_channels),
        inline=False,
    )
    embed.add_field(
        name="How selection works",
        value=(
            "**All** lets any ordinary server VC start captions except explicit exclusions. "
            "**Selected** allows a VC when either that exact VC or its category is selected. "
            "Exclusions always win. This makes existing gaming categories such as squad rooms work without Dank Shield creating replacement VCs."
        ),
        inline=False,
    )
    return embed


class CaptionOutputPickerView(_OwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)

        async def _picked(
            interaction: discord.Interaction,
            channel: discord.abc.GuildChannel,
        ) -> None:
            if not _staff_authorized(interaction):
                return await _followup(interaction, "❌ Manage Server or Administrator is required.")
            if not isinstance(channel, discord.TextChannel):
                return await interaction.response.send_message(
                    "Pick a normal server text channel for caption output.",
                    ephemeral=True,
                )
            missing = _caption_output_missing_perms(
                channel,
                interaction.guild.me if interaction.guild else None,
            )
            if missing:
                return await interaction.response.send_message(
                    f"Dank Shield cannot use {channel.mention} for captions yet: missing {', '.join(missing)}.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            await _defer_update(interaction)
            try:
                await _save_caption_config(
                    interaction,
                    {CAPTION_OUTPUT_CHANNEL_KEY: str(int(channel.id))},
                )
            except Exception as exc:
                return await _followup(interaction, f"❌ Could not save the caption output channel: {type(exc).__name__}: {str(exc)[:180]}")
            await _edit_original(
                interaction,
                embed=await build_server_live_captions_setup_embed(interaction),
                view=ServerLiveCaptionsSetupView(self.owner_id),
            )
            await _followup(interaction, f"✅ Live Captions output set to {channel.mention}.")

        self.add_item(
            DankChannelSelect(
                author_id=owner_id,
                on_pick=_picked,
                placeholder="Choose the Live Captions output text channel…",
                channel_types=[discord.ChannelType.text],
                row=0,
            )
        )


class CaptionVoiceRulePickerView(_OwnedView):
    def __init__(self, owner_id: int, *, rule: str) -> None:
        super().__init__(owner_id)
        self.rule = rule

        if rule == "allowed_category":
            placeholder = "Add an allowed voice category…"
            channel_types = [discord.ChannelType.category]
        elif rule == "excluded_channel":
            placeholder = "Exclude a voice channel…"
            channel_types = [discord.ChannelType.voice]
        else:
            placeholder = "Add an allowed voice channel…"
            channel_types = [discord.ChannelType.voice]

        async def _picked(
            interaction: discord.Interaction,
            channel: discord.abc.GuildChannel,
        ) -> None:
            if not _staff_authorized(interaction):
                return await _followup(interaction, "❌ Manage Server or Administrator is required.")
            expected_category = self.rule == "allowed_category"
            if expected_category and not isinstance(channel, discord.CategoryChannel):
                return await interaction.response.send_message("Pick a voice category.", ephemeral=True)
            if not expected_category and not isinstance(channel, discord.VoiceChannel):
                return await interaction.response.send_message("Pick a voice channel.", ephemeral=True)

            await _defer_update(interaction)
            guild = interaction.guild
            if guild is None:
                return await _followup(interaction, "❌ This must be used inside a server.")
            cfg = await get_guild_config(int(guild.id), refresh=True)

            if self.rule == "allowed_category":
                key = CAPTION_ALLOWED_VOICE_CATEGORIES_KEY
            elif self.rule == "excluded_channel":
                key = CAPTION_EXCLUDED_VOICE_CHANNELS_KEY
            else:
                key = CAPTION_ALLOWED_VOICE_CHANNELS_KEY

            values = _id_set(cfg.get(key))
            values.add(int(channel.id))
            updates: dict[str, Any] = {key: [str(value) for value in sorted(values)]}
            if self.rule in {"allowed_channel", "allowed_category"}:
                updates[CAPTION_VOICE_SCOPE_KEY] = "selected"

            try:
                await _save_caption_config(interaction, updates)
            except Exception as exc:
                return await _followup(interaction, f"❌ Could not save the voice rule: {type(exc).__name__}: {str(exc)[:180]}")

            await _edit_original(
                interaction,
                embed=await build_server_live_captions_setup_embed(interaction),
                view=ServerLiveCaptionsSetupView(self.owner_id),
            )
            await _followup(interaction, "✅ Live Captions voice rule saved.")

        self.add_item(
            DankChannelSelect(
                author_id=owner_id,
                on_pick=_picked,
                placeholder=placeholder,
                channel_types=channel_types,
                row=0,
            )
        )


class CaptionLanguageOutputSelect(discord.ui.Select):
    def __init__(self, owner_id: int, current_mode: str) -> None:
        self.owner_id = int(owner_id)
        mode = _caption_output_mode_label(current_mode)
        options = [
            discord.SelectOption(
                label="Original language",
                value="original",
                description="Show Gemini's finalized transcript in the language spoken.",
                emoji="🗣️",
                default=current_mode == "original",
            ),
            discord.SelectOption(
                label="English",
                value="english",
                description="Translate non-English finalized text to English only.",
                emoji="🇺🇸",
                default=current_mode == "english",
            ),
            discord.SelectOption(
                label="Original + English",
                value="bilingual",
                description="Show the original transcript plus an English translation.",
                emoji="🌐",
                default=current_mode == "bilingual",
            ),
        ]
        super().__init__(
            placeholder=f"Caption text output: {mode}",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="dank:captions:setup:language_output:v1",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await interaction.response.send_message(
                "Open your own Live Captions setup panel.",
                ephemeral=True,
            )
        if not _staff_authorized(interaction):
            return await interaction.response.send_message(
                "❌ Manage Server or Administrator is required.",
                ephemeral=True,
            )
        await _defer_update(interaction)
        selected = str(self.values[0] if self.values else "original")
        try:
            await _save_caption_config(
                interaction,
                {
                    CAPTION_OUTPUT_MODE_KEY: selected,
                    # Empty language hints intentionally mean Gemini auto-detects
                    # across its full supported language set and code-switching.
                    CAPTION_LANGUAGE_CODES_KEY: [],
                },
            )
        except Exception as exc:
            return await _followup(
                interaction,
                f"❌ Could not save caption language output: {type(exc).__name__}: {str(exc)[:180]}",
            )
        await _edit_original(
            interaction,
            embed=await build_server_live_captions_setup_embed(interaction),
            view=ServerLiveCaptionsSetupView(self.owner_id),
        )
        await _followup(
            interaction,
            f"✅ Live Captions now use **Auto-detect 85+ languages** with **{_caption_output_mode_label(selected)}** output.",
        )


class CaptionLanguageOutputView(_OwnedView):
    def __init__(self, owner_id: int, current_mode: str) -> None:
        super().__init__(owner_id)
        self.add_item(CaptionLanguageOutputSelect(owner_id, current_mode))

    @discord.ui.button(
        label="Back to Setup",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:captions:setup:language_back:v1",
        row=1,
    )
    async def back(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _defer_update(interaction)
        await _edit_original(
            interaction,
            embed=await build_server_live_captions_setup_embed(interaction),
            view=ServerLiveCaptionsSetupView(self.owner_id),
        )


class ServerLiveCaptionsSetupView(_OwnedView):
    @discord.ui.button(
        label="Select Output Channel",
        emoji="📍",
        style=discord.ButtonStyle.primary,
        custom_id="dank:captions:setup:output:v1",
        row=0,
    )
    async def output(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await interaction.response.send_message("❌ Manage Server or Administrator is required.", ephemeral=True)
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="📍 Choose Caption Output",
                description="Choose the existing text channel where ordinary server Live Captions should post.",
                color=discord.Color.blurple(),
            ),
            view=CaptionOutputPickerView(self.owner_id),
        )

    @discord.ui.button(
        label="Create #live-captions",
        emoji="➕",
        style=discord.ButtonStyle.success,
        custom_id="dank:captions:setup:create_output:v1",
        row=0,
    )
    async def create_output(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await interaction.response.send_message("❌ Manage Server or Administrator is required.", ephemeral=True)
        await _defer_update(interaction)
        guild = interaction.guild
        if guild is None:
            return await _followup(interaction, "❌ This must be used inside a server.")

        channel = discord.utils.get(guild.text_channels, name="live-captions")
        created = False
        if channel is None:
            bot_member = guild.me
            if bot_member is None or not bot_member.guild_permissions.manage_channels:
                return await _followup(
                    interaction,
                    "❌ Dank Shield needs **Manage Channels** to create #live-captions. You can still use **Select Output Channel** with an existing channel.",
                )
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=False,
                    read_message_history=True,
                ),
                bot_member: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    embed_links=True,
                ),
            }
            try:
                channel = await guild.create_text_channel(
                    name="live-captions",
                    overwrites=overwrites,
                    topic="Read-only Dank Shield Live Captions output for ordinary server voice channels.",
                    reason="Dank Shield Live Captions setup",
                )
                created = True
            except (discord.Forbidden, discord.HTTPException) as exc:
                return await _followup(interaction, f"❌ Could not create #live-captions: {type(exc).__name__}: {str(exc)[:180]}")

        missing = _caption_output_missing_perms(channel, guild.me)
        if missing:
            return await _followup(
                interaction,
                f"❌ Dank Shield cannot use {channel.mention} for captions yet: missing {', '.join(missing)}.",
            )

        try:
            await _save_caption_config(
                interaction,
                {CAPTION_OUTPUT_CHANNEL_KEY: str(int(channel.id))},
            )
        except Exception as exc:
            return await _followup(interaction, f"❌ Caption channel exists but could not be saved: {type(exc).__name__}: {str(exc)[:180]}")

        await _edit_original(
            interaction,
            embed=await build_server_live_captions_setup_embed(interaction),
            view=ServerLiveCaptionsSetupView(self.owner_id),
        )
        verb = "Created and saved" if created else "Reused and saved"
        await _followup(interaction, f"✅ {verb} {channel.mention}.")

    @discord.ui.button(
        label="Toggle All / Selected",
        emoji="🎚️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:captions:setup:scope:v1",
        row=1,
    )
    async def scope(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await interaction.response.send_message("❌ Manage Server or Administrator is required.", ephemeral=True)
        await _defer_update(interaction)
        guild = interaction.guild
        if guild is None:
            return await _followup(interaction, "❌ This must be used inside a server.")
        cfg = await get_guild_config(int(guild.id), refresh=True)
        new_mode = "selected" if _scope_mode(cfg) == "all" else "all"
        try:
            await _save_caption_config(interaction, {CAPTION_VOICE_SCOPE_KEY: new_mode})
        except Exception as exc:
            return await _followup(interaction, f"❌ Could not save voice scope: {type(exc).__name__}: {str(exc)[:180]}")
        await _edit_original(
            interaction,
            embed=await build_server_live_captions_setup_embed(interaction),
            view=ServerLiveCaptionsSetupView(self.owner_id),
        )
        await _followup(interaction, f"✅ Voice scope is now **{new_mode}**.")

    @discord.ui.button(
        label="Add Allowed VC",
        emoji="🔊",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:captions:setup:add_vc:v1",
        row=1,
    )
    async def add_vc(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await interaction.response.send_message("❌ Manage Server or Administrator is required.", ephemeral=True)
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🔊 Add Allowed Voice Channel",
                description="Pick one existing voice channel. Repeat this action to add more.",
                color=discord.Color.blurple(),
            ),
            view=CaptionVoiceRulePickerView(self.owner_id, rule="allowed_channel"),
        )

    @discord.ui.button(
        label="Add Voice Category",
        emoji="🗂️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:captions:setup:add_category:v1",
        row=1,
    )
    async def add_category(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await interaction.response.send_message("❌ Manage Server or Administrator is required.", ephemeral=True)
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🗂️ Add Allowed Voice Category",
                description="Pick a category such as your gaming/squad-room category. Every voice channel inside it becomes eligible.",
                color=discord.Color.blurple(),
            ),
            view=CaptionVoiceRulePickerView(self.owner_id, rule="allowed_category"),
        )

    @discord.ui.button(
        label="Exclude VC",
        emoji="🚫",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:captions:setup:exclude_vc:v1",
        row=2,
    )
    async def exclude_vc(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await interaction.response.send_message("❌ Manage Server or Administrator is required.", ephemeral=True)
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🚫 Exclude Voice Channel",
                description="Pick a voice channel that must never start ordinary server Live Captions. Exclusions override All and Selected modes.",
                color=discord.Color.blurple(),
            ),
            view=CaptionVoiceRulePickerView(self.owner_id, rule="excluded_channel"),
        )

    @discord.ui.button(
        label="Language & Translation",
        emoji="🌐",
        style=discord.ButtonStyle.primary,
        custom_id="dank:captions:setup:language:v1",
        row=2,
    )
    async def language_output(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await interaction.response.send_message(
                "❌ Manage Server or Administrator is required.",
                ephemeral=True,
            )
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "❌ This must be used inside a server.",
                ephemeral=True,
            )
        await _defer_update(interaction)
        cfg = await get_guild_config(int(guild.id), refresh=True)
        mode = _caption_output_mode(cfg)
        await _edit_original(
            interaction,
            embed=discord.Embed(
                title="🌐 Live Captions Language & Translation",
                description=(
                    "Gemini Live automatically detects **85+ supported languages** and can handle code-switching. "
                    "Choose how finalized captions are displayed. English translation uses the finalized transcript text only; "
                    "the original audio is not sent through another translation pass."
                ),
                color=discord.Color.blurple(),
            ),
            view=CaptionLanguageOutputView(self.owner_id, mode),
        )

    @discord.ui.button(
        label="Clear Voice Rules",
        emoji="🧹",
        style=discord.ButtonStyle.danger,
        custom_id="dank:captions:setup:clear_rules:v1",
        row=2,
    )
    async def clear_rules(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await interaction.response.send_message("❌ Manage Server or Administrator is required.", ephemeral=True)
        await _defer_update(interaction)
        try:
            await _save_caption_config(
                interaction,
                {
                    CAPTION_VOICE_SCOPE_KEY: "all",
                    CAPTION_ALLOWED_VOICE_CHANNELS_KEY: [],
                    CAPTION_ALLOWED_VOICE_CATEGORIES_KEY: [],
                    CAPTION_EXCLUDED_VOICE_CHANNELS_KEY: [],
                },
            )
        except Exception as exc:
            return await _followup(interaction, f"❌ Could not clear voice rules: {type(exc).__name__}: {str(exc)[:180]}")
        await _edit_original(
            interaction,
            embed=await build_server_live_captions_setup_embed(interaction),
            view=ServerLiveCaptionsSetupView(self.owner_id),
        )
        await _followup(interaction, "✅ Voice rules cleared. Ordinary server Live Captions now allow all voice channels.")

    @discord.ui.button(
        label="Live Captions",
        emoji="📝",
        style=discord.ButtonStyle.primary,
        custom_id="dank:captions:setup:back:v1",
        row=3,
    )
    async def back(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _defer_update(interaction)
        await _edit_original(
            interaction,
            embed=await build_server_live_captions_embed(interaction),
            view=ServerLiveCaptionsView(self.owner_id),
        )

    @discord.ui.button(
        label="Setup Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:captions:setup:setup_home:v1",
        row=3,
    )
    async def setup_home(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not _staff_authorized(interaction):
            return await interaction.response.send_message("❌ Manage Server or Administrator is required.", ephemeral=True)
        await _defer_update(interaction)
        guild = interaction.guild
        if guild is None:
            return await _followup(interaction, "❌ This must be used inside a server.")
        from . import public_setup_start

        embed, view = await public_setup_start._build_main_setup_payload(guild)
        await _edit_original(interaction, embed=embed, view=view)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        custom_id="dank:captions:setup:close:v1",
        row=4,
    )
    async def close_setup(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _defer_update(interaction)
        try:
            await interaction.delete_original_response()
        except (discord.NotFound, discord.HTTPException):
            await _edit_original(interaction, content="Live Captions setup closed.", embed=None, view=None)


async def open_server_live_captions_setup(
    interaction: discord.Interaction,
) -> None:
    if interaction.guild is None:
        if interaction.response.is_done():
            return await interaction.followup.send("Live Captions setup is available inside Discord servers.", ephemeral=True)
        return await interaction.response.send_message("Live Captions setup is available inside Discord servers.", ephemeral=True)
    if not _staff_authorized(interaction):
        if interaction.response.is_done():
            return await _followup(interaction, "❌ Manage Server or Administrator is required.")
        return await interaction.response.send_message("❌ Manage Server or Administrator is required.", ephemeral=True)

    if not interaction.response.is_done():
        await safe_defer_interaction(
            interaction,
            ephemeral=True,
            action_name="server_live_captions_setup_open",
        )
    await _edit_original(
        interaction,
        embed=await build_server_live_captions_setup_embed(interaction),
        view=ServerLiveCaptionsSetupView(int(interaction.user.id)),
    )


async def open_server_live_captions(
    interaction: discord.Interaction,
    *,
    replace_message: bool = True,
) -> None:
    if interaction.guild is None:
        if interaction.response.is_done():
            return await interaction.followup.send(
                "Live Captions are available inside Discord servers.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        return await interaction.response.send_message(
            "Live Captions are available inside Discord servers.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    if not interaction.response.is_done():
        await safe_defer_interaction(
            interaction,
            ephemeral=not replace_message,
            action_name="server_live_captions_open",
        )

    embed = await build_server_live_captions_embed(interaction)
    view = ServerLiveCaptionsView(int(interaction.user.id))
    await _edit_original(interaction, content=None, embed=embed, view=view)


async def open_server_live_captions_command(
    interaction: discord.Interaction,
) -> None:
    await open_server_live_captions(interaction, replace_message=False)


__all__ = [
    "CaptionLanguageOutputSelect",
    "CaptionLanguageOutputView",
    "CaptionOutputPickerView",
    "CaptionVoiceRulePickerView",
    "ServerLiveCaptionsSetupView",
    "ServerLiveCaptionsView",
    "build_server_live_captions_embed",
    "build_server_live_captions_setup_embed",
    "open_server_live_captions",
    "open_server_live_captions_command",
    "open_server_live_captions_setup",
]
