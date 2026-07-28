from __future__ import annotations

"""Validation and safe-zone guidance for custom profile-signature artwork."""

from io import BytesIO

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

PROFILE_BACKGROUND_WIDTH = 1400
PROFILE_BACKGROUND_HEIGHT = 300
PROFILE_BACKGROUND_RATIO = PROFILE_BACKGROUND_WIDTH / PROFILE_BACKGROUND_HEIGHT
PROFILE_BACKGROUND_UPLOAD_MAX_BYTES = 8 * 1024 * 1024
PROFILE_BACKGROUND_MAX_PIXELS = 20_000_000
PROFILE_BACKGROUND_ALLOWED_FORMATS = frozenset({"png", "jpeg", "jpg", "webp"})


def profile_background_requirements() -> str:
    return (
        "**File:** PNG, JPG/JPEG, or WebP • **Upload maximum:** 8 MB\n"
        "**Canvas:** 1400 × 300 recommended • minimum 1400 × 300 • target ratio 14:3\n"
        "**Accepted ratio:** 4.29:1 through 5.04:1. Dank Shield center-crops and resizes to exactly 1400 × 300.\n"
        "**Artwork only:** do not bake usernames, roles, or platform handles into the image. "
        "Keep important artwork away from 0–270 px (avatar), 278–790 px (profile text), "
        "820–1160 px (roles/platforms), and 1160–1400 px (optional server branding)."
    )


def profile_background_guide() -> bytes:
    image = Image.new("RGB", (PROFILE_BACKGROUND_WIDTH, PROFILE_BACKGROUND_HEIGHT), (15, 17, 24))
    draw = ImageDraw.Draw(image, "RGBA")
    zones = (
        ((0, 0, 270, 300), (60, 190, 255, 70), "AVATAR"),
        ((278, 0, 790, 300), (90, 225, 130, 60), "NAME / TAGS"),
        ((820, 0, 1160, 300), (255, 170, 55, 60), "ROLES / PLATFORMS"),
        ((1160, 0, 1400, 300), (205, 90, 255, 60), "OPTIONAL SERVER BRAND"),
    )
    for box, fill, label in zones:
        draw.rectangle(box, fill=fill, outline=fill[:3] + (220,), width=4)
        draw.text((box[0] + 12, 18), label, fill=(255, 255, 255, 255))
    draw.text(
        (420, 264),
        "1400 × 300 • 14:3 • background artwork safe-zone guide",
        fill=(255, 255, 255, 255),
    )
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def normalize_profile_background_upload(payload: bytes) -> bytes:
    if not payload or len(payload) > PROFILE_BACKGROUND_UPLOAD_MAX_BYTES:
        raise ValueError("The image must be present and no larger than 8 MB.")
    try:
        with Image.open(BytesIO(payload)) as source:
            source.load()
            file_type = str(source.format or "").lower()
            width, height = source.size
            if file_type not in PROFILE_BACKGROUND_ALLOWED_FORMATS:
                raise ValueError("Use a PNG, JPG/JPEG, or WebP image.")
            if width * height > PROFILE_BACKGROUND_MAX_PIXELS:
                raise ValueError("Keep the source image below 20 megapixels.")
            if width < PROFILE_BACKGROUND_WIDTH or height < PROFILE_BACKGROUND_HEIGHT:
                raise ValueError("The image must be at least 1400 × 300 pixels.")
            ratio = width / max(1, height)
            if not 4.29 <= ratio <= 5.04:
                raise ValueError("Use a wide 14:3 image; the accepted ratio is 4.29:1 through 5.04:1.")
            rendered = ImageOps.fit(
                source.convert("RGB"),
                (PROFILE_BACKGROUND_WIDTH, PROFILE_BACKGROUND_HEIGHT),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
    except ValueError:
        raise
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(
            "Discord could not read that image. Upload a normal PNG, JPG/JPEG, or WebP file."
        ) from exc
    output = BytesIO()
    rendered.save(output, format="JPEG", quality=90, optimize=True, progressive=True)
    return output.getvalue()


__all__ = [
    "PROFILE_BACKGROUND_HEIGHT",
    "PROFILE_BACKGROUND_UPLOAD_MAX_BYTES",
    "PROFILE_BACKGROUND_WIDTH",
    "normalize_profile_background_upload",
    "profile_background_guide",
    "profile_background_requirements",
]
