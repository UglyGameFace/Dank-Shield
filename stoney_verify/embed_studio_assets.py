"""Opt-in permanent image/GIF uploads for public Discord embeds.

Never stores binary bytes in Postgres, guild settings or the bot's RAM cache.
Images are deliberately public once uploaded: a lasting Discord embed cannot
depend on an expiring private signed URL.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import os
import secrets
from typing import Any, Mapping

from PIL import Image, UnidentifiedImageError

from .embed_studio_core import StudioValidationError, https_url
from .embed_studio_storage_v2 import verify_project

BUCKET = "dank-studio-artwork"
ASSET_TABLE = "dank_embed_studio_assets"
FLAG = "DANK_EMBED_STUDIO_UPLOADS_ENABLED"
MAX_UPLOAD_BYTES = 6 * 1024 * 1024
MAX_DIMENSION = 8192
MAX_PIXELS = 32_000_000
MAX_GIF_FRAMES = 300

MIME = {
    "PNG": ("png", "image/png"),
    "JPEG": ("jpg", "image/jpeg"),
    "WEBP": ("webp", "image/webp"),
    "GIF": ("gif", "image/gif"),
}


class StudioAssetError(StudioValidationError):
    pass


@dataclass(frozen=True)
class UploadedArtwork:
    guild_id: int
    name: str
    url: str
    object_path: str
    image_sha256: str
    content_type: str
    size_bytes: int


def uploads_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get(FLAG) or "").strip().lower() in {"1", "true", "yes", "on"}


def inspect_image(data: bytes, filename: str) -> tuple[str, str, str]:
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_UPLOAD_BYTES:
        raise StudioAssetError("Choose a real image or GIF no larger than 6 MiB.")
    # Never trust MIME or filename from a Discord attachment.
    try:
        with Image.open(BytesIO(data)) as img:
            kind = str(img.format or "").upper()
            if kind not in MIME:
                raise StudioAssetError("Only PNG, JPEG, WebP and GIF are supported.")
            width, height = img.size
            if (width < 1 or height < 1 or width > MAX_DIMENSION
                    or height > MAX_DIMENSION or width * height > MAX_PIXELS):
                raise StudioAssetError("Image dimensions exceed the supported safe range.")
            if int(getattr(img, "n_frames", 1) or 1) > MAX_GIF_FRAMES:
                raise StudioAssetError("Animated image contains too many frames.")
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise StudioAssetError("This file is not a valid supported image.") from exc
    ext, mime = MIME[kind]
    name = str(filename or "Artwork").rsplit(".", 1)[0].strip()
    name = "".join(ch for ch in name if ch.isprintable())[:60].strip() or "Uploaded artwork"
    return name, ext, mime


def _client() -> Any:
    verify_project()
    from .globals import get_supabase
    db = get_supabase()
    if db is None:
        raise StudioAssetError("Artwork storage is unavailable.")
    return db


def _upload_sync(guild_id: int, data: bytes, filename: str) -> UploadedArtwork:
    if int(guild_id) <= 0:
        raise StudioAssetError("Upload from a server's private Embed Studio.")
    if not uploads_enabled():
        raise StudioAssetError("Managed image uploads are not enabled on this deployment yet.")
    name, ext, mime = inspect_image(data, filename)
    # Always use a fresh unpredictable object path. A failed CAS can undo its
    # own new upload without deleting another moderator's shared content.
    object_path = f"{int(guild_id)}/{secrets.token_hex(16)}.{ext}"
    from .embed_studio_artwork_budget import (
        get_budget, reserve_artwork, finalize_artwork, release_artwork,
    )
    budget = get_budget()  # missing explicit allocation means uploads fail closed
    db = _client()
    # Reserve before contacting Storage so simultaneous uploads cannot exceed
    # the same remaining allowance. A timed-out upload is treated as uncertain.
    reserve_artwork(db, guild_id=int(guild_id), object_path=object_path,
                    size_bytes=len(data), budget=budget)
    digest = sha256(data).hexdigest()
    try:
        bucket = db.storage.from_(BUCKET)
        result = bucket.upload(
            path=object_path, file=BytesIO(data),
            file_options={"content-type": mime, "cache-control": "86400", "upsert": "false"},
        )
        if result is None:
            raise StudioAssetError("Storage did not confirm upload.")
        url = https_url(bucket.get_public_url(object_path), "Public artwork URL")
        if not url:
            raise StudioAssetError("Storage did not provide a permanent artwork URL.")
        finalize_artwork(
            db, guild_id=int(guild_id), object_path=object_path, size_bytes=len(data),
            name=name, content_type=mime, digest=digest,
        )
        return UploadedArtwork(int(guild_id), name, url, object_path,
                               digest, mime, len(data))
    except Exception:
        # Only release the reservation after a *confirmed* removal of this
        # newly generated path. A failed or ambiguous Storage call keeps the
        # quota occupied until staff review; never silently overbook bytes.
        try:
            removed = bucket.remove([object_path])
            if removed is not None:
                release_artwork(db, int(guild_id), object_path)
        except Exception:
            pass
        raise


async def upload_artwork(guild_id: int, data: bytes, filename: str) -> UploadedArtwork:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_upload_sync, guild_id, data, filename), timeout=35
        )
    except (StudioAssetError, asyncio.CancelledError):
        raise
    except Exception as exc:
        raise StudioAssetError(
            f"Artwork upload could not complete ({type(exc).__name__})."
        ) from exc


def _discard_sync(art: UploadedArtwork) -> None:
    db = _client()
    removed = db.storage.from_(BUCKET).remove([art.object_path])
    if removed is None:
        raise StudioAssetError(
            "Artwork removal not confirmed; keep its metadata and quota allocated."
        )
    db.table(ASSET_TABLE).delete().eq("guild_id", str(art.guild_id)).eq(
        "object_path", art.object_path
    ).execute()


async def discard_failed_upload(art: UploadedArtwork) -> None:
    """Best effort for failed metadata saves, never for published-media deletion."""
    try:
        await asyncio.wait_for(asyncio.to_thread(_discard_sync, art), timeout=15)
    except Exception:
        # A maintenance audit should detect uploaded artwork not linked to
        # a Studio record; no background deletion can break published embeds.
        pass
