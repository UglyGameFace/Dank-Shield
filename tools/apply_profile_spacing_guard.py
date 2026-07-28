from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str, *, label: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one exact match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> None:
    renderer = "stoney_verify/profile_signature_live_renderer.py"

    replace_once(
        renderer,
        '''def _safe_text(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").replace("\\r", " ").replace("\\n", " ").split())
    return text[: max(0, int(limit))]


''',
        '''def _safe_text(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").replace("\\r", " ").replace("\\n", " ").split())
    return text[: max(0, int(limit))]


def _fit_text_width(
    draw: ImageDraw.ImageDraw,
    value: Any,
    font: Any,
    *,
    max_width: int,
    limit: int = 160,
) -> str:
    """Return one safe line that cannot cross its reserved pixel boundary."""
    clean = _safe_text(value, limit)
    width_limit = max(0, int(max_width))
    if not clean or width_limit <= 0:
        return ""

    def width(text: str) -> int:
        box = draw.textbbox((0, 0), text, font=font)
        return max(0, box[2] - box[0])

    if width(clean) <= width_limit:
        return clean

    ellipsis = "…"
    if width(ellipsis) > width_limit:
        return ""
    low = 0
    high = len(clean)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = clean[:middle].rstrip() + ellipsis
        if width(candidate) <= width_limit:
            low = middle
        else:
            high = middle - 1
    return clean[:low].rstrip() + ellipsis


''',
        label="renderer pixel-fit helper",
    )

    replace_once(
        renderer,
        '''    server_roles = [str(value) for value in server_role_labels if str(value).strip()]
    primary_role = server_roles[0] if server_roles else ""
    if primary_role:
        badge = _safe_text(primary_role, 28).upper()
        badge_width = _chip_width(draw, badge, small_font)
        badge_x = 857
        draw.rounded_rectangle(
            (badge_x, 43, badge_x + badge_width, 83),
            radius=16,
            fill=primary + (42,),
            outline=primary + (180,),
            width=2,
        )
        draw.text((badge_x + 16, 52), badge, font=small_font, fill=primary + (255,))
''',
        '''    server_roles = [str(value) for value in server_role_labels if str(value).strip()]
    primary_role = server_roles[0] if server_roles else ""
    if primary_role:
        badge_x = 857
        badge_max_width = 335
        badge = _fit_text_width(
            draw,
            primary_role.upper(),
            small_font,
            max_width=badge_max_width - 32,
            limit=80,
        )
        if badge:
            badge_width = min(badge_max_width, _chip_width(draw, badge, small_font))
            draw.rounded_rectangle(
                (badge_x, 43, badge_x + badge_width, 83),
                radius=16,
                fill=primary + (42,),
                outline=primary + (180,),
                width=2,
            )
            draw.text((badge_x + 16, 52), badge, font=small_font, fill=primary + (255,))
''',
        label="bounded server-role badge",
    )

    replace_once(
        renderer,
        '''    if shared_names:
        platform_line = _safe_text("  •  ".join(shared_names[:2]), 46)
        draw.text((857, 177), platform_line, font=platform_font, fill=secondary + (255,))
''',
        '''    if shared_names:
        platform_line = _fit_text_width(
            draw,
            "  •  ".join(shared_names[:2]),
            platform_font,
            max_width=335,
            limit=160,
        )
        if platform_line:
            draw.text((857, 177), platform_line, font=platform_font, fill=secondary + (255,))
''',
        label="bounded platform username line",
    )

    replace_once(
        renderer,
        '''    row = 0
    for label, accent in labels:
        width = _chip_width(draw, _safe_text(label, 34), chip_font)
        if chip_x + width > max_chip_x and chip_x > content_x:
            row += 1
            if row >= 2:
                break
            chip_x = content_x
            chip_y += 48
        width = _draw_compact_label(draw, x=chip_x, y=chip_y, label=label, font=chip_font, accent=accent)
        chip_x += width + 10
''',
        '''    row = 0
    for label, accent in labels:
        clean_label = _safe_text(label, 120)
        width = _chip_width(draw, clean_label, chip_font)
        if chip_x + width > max_chip_x and chip_x > content_x:
            row += 1
            if row >= 2:
                break
            chip_x = content_x
            chip_y += 48
        available_text_width = max(0, max_chip_x - chip_x - 32)
        fitted_label = _fit_text_width(
            draw,
            clean_label,
            chip_font,
            max_width=available_text_width,
            limit=120,
        )
        if not fitted_label:
            continue
        width = _draw_compact_label(
            draw,
            x=chip_x,
            y=chip_y,
            label=fitted_label,
            font=chip_font,
            accent=accent,
        )
        chip_x += width + 10
''',
        label="bounded role and profile-tag chips",
    )

    replace_once(
        renderer,
        '''    server_label = _safe_text(server_name, 22)
    if server_label:
        label_box = draw.textbbox((0, 0), server_label, font=chip_font)
        label_width = label_box[2] - label_box[0]
        draw.text(
            (server_box_x + (server_size - label_width) / 2, 232),
            server_label,
            font=chip_font,
            fill=tuple(theme.text) + (225,),
        )
''',
        '''    server_label = _fit_text_width(
        draw,
        server_name,
        chip_font,
        max_width=server_size,
        limit=80,
    )
    if server_label:
        label_box = draw.textbbox((0, 0), server_label, font=chip_font)
        label_width = label_box[2] - label_box[0]
        draw.text(
            (server_box_x + (server_size - label_width) / 2, 232),
            server_label,
            font=chip_font,
            fill=tuple(theme.text) + (225,),
        )
''',
        label="bounded server-name label",
    )

    test_path = ROOT / "tests/test_profile_role_display_separation.py"
    test_text = test_path.read_text(encoding="utf-8")
    test_text = test_text.replace(
        "from pathlib import Path\n",
        "from pathlib import Path\n\nfrom PIL import Image, ImageDraw\n\nimport stoney_verify.profile_signature_live_renderer as renderer_module\n",
        1,
    )
    test_block = '''

def test_banner_text_fitter_respects_reserved_pixel_widths() -> None:
    image = Image.new("RGBA", (1400, 340), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = renderer_module._font(20, style_key="clean", regular=True)
    for max_width in (126, 335, 884):
        fitted = renderer_module._fit_text_width(
            draw,
            "WIDE SERVER ROLE AND PLATFORM USERNAME " * 20,
            font,
            max_width=max_width,
        )
        assert fitted.endswith("…")
        box = draw.textbbox((0, 0), fitted, font=font)
        assert box[2] - box[0] <= max_width


def test_banner_applies_pixel_fitting_to_every_dynamic_right_side_label() -> None:
    assert "badge_max_width = 335" in RENDERER
    assert 'max_width=335' in RENDERER
    assert 'max_width=server_size' in RENDERER
    assert "available_text_width = max(0, max_chip_x - chip_x - 32)" in RENDERER
'''
    if "test_banner_text_fitter_respects_reserved_pixel_widths" in test_text:
        raise RuntimeError("spacing regression tests already exist")
    test_path.write_text(test_text.rstrip() + test_block.rstrip() + "\n", encoding="utf-8")

    Path(__file__).unlink()
    print("Applied and validated pixel-safe profile banner spacing.")


if __name__ == "__main__":
    main()
