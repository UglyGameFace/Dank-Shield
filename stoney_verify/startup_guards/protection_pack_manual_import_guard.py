from __future__ import annotations

"""Manual starter-pack import fallback for Protection Center.

This avoids network fetches and lets staff paste vetted line-delimited terms from
trusted sources. The live automod normalizer still handles spacing, symbols,
zero-width characters, and common lookalikes when matching messages.
"""

import re
import unicodedata
from typing import Any, Mapping

import discord

_PATCHED = False
ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
SPACE_RE = re.compile(r"\s+")
MAX_IMPORT_TERMS = 700
MAX_FILTER_CHARS = 22000


def _clean(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = ZERO_WIDTH_RE.sub("", text)
    text = text.replace(",", " ")
    text = SPACE_RE.sub(" ", text).strip().casefold()
    return text


def _csv_items(value: Any) -> list[str]:
    out: list[str] = []
    for chunk in str(value or "").replace("\n", ",").split(","):
        item = _clean(chunk)
        if item and item not in out:
            out.append(item)
    return out


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    for bucket in ("settings", "config", "metadata", "meta"):
        try:
            nested = getattr(cfg, bucket, None)
            if isinstance(nested, Mapping) and nested.get(key) is not None:
                return nested.get(key)
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, Mapping) and nested.get(key) is not None:
                    return nested.get(key)
        except Exception:
            pass
    return default


async def _save_terms(guild_id: int, updates: dict[str, Any]) -> None:
    from stoney_verify.guild_config import invalidate_guild_config, upsert_guild_config

    await upsert_guild_config(int(guild_id), dict(updates))
    invalidate_guild_config(int(guild_id))


class StarterPackImportModal(discord.ui.Modal, title="Import Starter Filter Pack"):
    terms = discord.ui.TextInput(
        label="Paste vetted line-delimited filter terms",
        style=discord.TextStyle.paragraph,
        max_length=3500,
        required=True,
        placeholder="Paste one term or phrase per line. Review source quality first.",
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            from stoney_verify.commands_ext.public_setup_group import _require_setup_permission
            from stoney_verify.guild_config import get_guild_config
        except Exception as exc:
            return await interaction.response.send_message(f"❌ Import unavailable: `{type(exc).__name__}: {exc}`", ephemeral=True)
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        cfg = await get_guild_config(int(guild.id), refresh=True)
        existing = _csv_items(_cfg_value(cfg, "automod_bad_words", ""))
        seen = set(existing)
        imported: list[str] = []
        skipped = 0
        for raw in str(self.terms.value or "").splitlines():
            item = _clean(raw)
            if not item or len(item) < 2 or len(item) > 80:
                skipped += 1
                continue
            if item in seen:
                skipped += 1
                continue
            trial = existing + imported + [item]
            if len(",".join(trial)) > MAX_FILTER_CHARS:
                break
            imported.append(item)
            seen.add(item)
            if len(imported) >= MAX_IMPORT_TERMS:
                break
        if not imported:
            return await interaction.response.send_message("⚪ No new valid terms were imported. They may already exist or be invalid.", ephemeral=True)
        merged = existing + imported
        await _save_terms(
            int(guild.id),
            {
                "automod_enabled": True,
                "automod_bad_words": ",".join(merged),
                "automod_filter_pack_imported_count": len(imported),
                "automod_filter_pack_skipped_count": skipped,
                "automod_filter_pack_updated_by_id": str(int(interaction.user.id)),
                "automod_updated_by_id": str(int(interaction.user.id)),
            },
        )
        await interaction.response.send_message(
            f"✅ Imported **{len(imported)}** starter-pack filters. Skipped `{skipped}` duplicates/invalid entries. Use `/dank protection` → **Test** to verify bypass behavior.",
            ephemeral=True,
        )


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.commands_ext.public_protection_center import ProtectionCenterView
        from stoney_verify.commands_ext.public_setup_group import _require_setup_permission

        async def import_pack_button(self: Any, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            _ = button
            if not await _require_setup_permission(interaction):
                return
            await interaction.response.send_modal(StarterPackImportModal())

        button = discord.ui.button(
            label="Import Pack",
            emoji="🌐",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_protection:manual_import_pack",
            row=2,
        )(import_pack_button)

        if not hasattr(ProtectionCenterView, "import_pack_button"):
            setattr(ProtectionCenterView, "import_pack_button", button)
        _PATCHED = True
        print("✅ protection_pack_manual_import_guard active; Protection Center starter-pack import available")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ protection_pack_manual_import_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
