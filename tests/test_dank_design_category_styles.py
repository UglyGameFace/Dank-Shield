from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from stoney_verify.commands_ext import public_design_studio as legacy
from stoney_verify.commands_ext import public_design_studio_v2 as design
from stoney_verify.services import server_design_majority_layout as majority
from stoney_verify.services import server_design_plan_service as plan_service
from stoney_verify.services import server_design_studio as studio
from stoney_verify.services import server_design_style_zones as zones

CATEGORY_ID = 555
CHANNEL_ID = 777

_BASE_OPTIONS: dict[str, object] = {
    "theme_id": "gothic_clean",
    "strength": 4,
    "font": "sans",
    "separator_id": "bar_thin",
    "category_frame_id": "line",
}


def _options(**extra: object) -> dict[str, object]:
    return {**_BASE_OPTIONS, **extra}


# ---------------------------------------------------------------------------
# Normalization (spec: validate category_styles, drop junk silently)
# ---------------------------------------------------------------------------


def test_normalize_category_styles_keeps_valid_entries() -> None:
    raw = {
        str(CATEGORY_ID): {
            "font": "small_caps",
            "category_font": "serif_italic",
            "separator_id": "middle_dot",
            "category_frame_id": "heavy_line",
        }
    }

    out = studio.normalize_category_styles(raw)

    assert out == {
        str(CATEGORY_ID): {
            "font": "small_caps",
            "category_font": "serif_italic",
            "separator_id": "middle_dot",
            "category_frame_id": "heavy_line",
        }
    }


def test_normalize_category_styles_drops_junk_silently() -> None:
    raw = {
        str(CATEGORY_ID): {
            "font": "fraktur",
            "category_font": "bogus-font",
            "separator_id": "bar_full",
            "category_frame_id": "nope",
        },
        "not-an-id": {"font": "sans"},
        "-5": {"font": "sans"},
        "0": {"font": "sans"},
        "9": "junk",
        "10": {"font": "not-a-font"},
        "11": {},
    }

    out = studio.normalize_category_styles(raw)

    assert out == {
        str(CATEGORY_ID): {
            "font": "fraktur",
            "category_font": None,
            "separator_id": "bar_full",
            "category_frame_id": None,
        }
    }
    assert studio.normalize_category_styles(None) == {}
    assert studio.normalize_category_styles("junk") == {}


def test_normalize_plan_options_preserves_category_styles_in_strict_mode() -> None:
    options = _options(
        category_styles={
            str(CATEGORY_ID): {"font": "bold_sans", "category_font": None, "separator_id": None, "category_frame_id": None},
            "junk-key": {"font": "sans"},
        }
    )

    normalized = plan_service.normalize_plan_options(options, strict=True)

    assert normalized["category_styles"] == {
        str(CATEGORY_ID): {
            "font": "bold_sans",
            "category_font": None,
            "separator_id": None,
            "category_frame_id": None,
        }
    }
    # Strict lock handling is untouched by the new key.
    assert normalized["exact_match"] is True


# ---------------------------------------------------------------------------
# Zone templates (spec: stamping produces a valid category_styles entry)
# ---------------------------------------------------------------------------


def test_zone_templates_resolve_to_valid_category_styles() -> None:
    assert set(zones.category_style_template_ids()) == set(zones.CATEGORY_STYLE_TEMPLATES)

    for template_id in zones.category_style_template_ids():
        entry = zones.category_style_from_template(template_id)
        assert entry is not None, template_id
        assert set(entry) == {"font", "category_font", "separator_id", "category_frame_id"}
        # A stamped entry must survive studio validation unchanged.
        assert studio.normalize_category_style(entry) == entry, template_id


def test_zone_template_pairings_match_the_scheme() -> None:
    smoke = zones.category_style_from_template("smoke")
    assert smoke is not None
    assert smoke["category_font"] == "serif_italic"
    assert smoke["font"] == "italic_sans"

    garden = zones.category_style_from_template("garden")
    assert garden is not None
    assert garden["category_font"] == "serif_bold_italic"
    assert garden["font"] == "italic_sans"

    nightlife = zones.category_style_from_template("nightlife")
    assert nightlife is not None
    assert nightlife["category_font"] == "bold_script"
    assert nightlife["font"] == "bold_sans"

    staff = zones.category_style_from_template("staff")
    assert staff is not None
    assert staff["font"] == "bold_sans"
    assert staff["category_frame_id"] == "heavy_line"

    assert zones.category_style_from_template("does-not-exist") is None
    assert zones.category_style_from_template(None) is None


# ---------------------------------------------------------------------------
# Plan resolution priority: lock > category style > global draft
# ---------------------------------------------------------------------------


def _resolve(options: dict[str, object], *, channel_id: int = CHANNEL_ID, category_id: int = CATEGORY_ID, kind: str = "text") -> dict:
    return legacy._effective_format_options(  # type: ignore[attr-defined]
        options,
        channel_id=channel_id,
        category_id=category_id,
        kind=kind,
    )


def test_category_style_applies_to_channels_without_locks() -> None:
    options = _options(
        category_styles={
            str(CATEGORY_ID): {
                "font": "small_caps",
                "category_font": "serif_italic",
                "separator_id": "middle_dot",
                "category_frame_id": "heavy_line",
            }
        }
    )

    effective = _resolve(options)

    assert effective["__format_lock_scope"] == "category_style"
    assert effective["font"] == "small_caps"
    assert effective["separator_id"] == "middle_dot"
    assert effective["category_frame_id"] == "heavy_line"


def test_category_header_uses_category_font_then_font() -> None:
    options = _options(
        category_styles={
            str(CATEGORY_ID): {"font": "small_caps", "category_font": "serif_italic", "separator_id": None, "category_frame_id": None}
        }
    )

    header = _resolve(options, channel_id=CATEGORY_ID, kind="category")
    assert header["__format_lock_scope"] == "category_style"
    assert header["font"] == "serif_italic"

    fallback = _options(
        category_styles={
            str(CATEGORY_ID): {"font": "bold_sans", "category_font": None, "separator_id": None, "category_frame_id": None}
        }
    )
    header = _resolve(fallback, channel_id=CATEGORY_ID, kind="category")
    assert header["font"] == "bold_sans"


def test_locks_override_category_styles() -> None:
    style = {str(CATEGORY_ID): {"font": "small_caps", "category_font": None, "separator_id": None, "category_frame_id": None}}

    channel_locked = _options(
        category_styles=style,
        channel_format_locks={str(CHANNEL_ID): {"font": "fraktur"}},
    )
    effective = _resolve(channel_locked)
    assert effective["__format_lock_scope"] == "channel"
    assert effective["font"] == "fraktur"

    category_locked = _options(
        category_styles=style,
        category_format_locks={str(CATEGORY_ID): {"font": "monospace"}},
    )
    effective = _resolve(category_locked)
    assert effective["__format_lock_scope"] == "category"
    assert effective["font"] == "monospace"

    global_locked = _options(
        category_styles=style,
        format_lock_global={"enabled": True, "font": "serif_bold"},
    )
    effective = _resolve(global_locked)
    assert effective["__format_lock_scope"] == "global"
    assert effective["font"] == "serif_bold"


def test_category_style_for_another_category_does_not_apply() -> None:
    options = _options(
        category_styles={"999": {"font": "small_caps", "category_font": None, "separator_id": None, "category_frame_id": None}}
    )

    effective = _resolve(options)

    assert effective["__format_lock_scope"] == "auto"
    assert effective["font"] == "sans"


def test_invalid_category_style_is_ignored_by_the_resolver() -> None:
    options = _options(category_styles={str(CATEGORY_ID): {"font": "not-a-font"}})

    effective = _resolve(options)

    assert effective["__format_lock_scope"] == "auto"
    assert effective["font"] == "sans"


def test_resolved_category_style_flows_into_styled_names() -> None:
    options = plan_service.normalize_plan_options(
        _options(
            strength=5,
            category_styles={
                str(CATEGORY_ID): {"font": "small_caps", "category_font": "serif_bold_italic", "separator_id": None, "category_frame_id": None}
            },
        ),
        strict=True,
    )

    channel_options = _resolve(options)
    channel = studio.build_styled_name(
        "general-chat",
        kind="text",
        theme_id="gothic_clean",
        strength=5,
        separator_id=str(channel_options.get("separator_id") or "") or None,
        font=str(channel_options.get("font") or "") or None,
        exact_match=True,
    )
    assert channel.font == "small_caps"
    assert channel.status == "changed"

    header_options = _resolve(options, channel_id=CATEGORY_ID, kind="category")
    header = studio.build_styled_name(
        "the-garden",
        kind="category",
        theme_id="gothic_clean",
        strength=5,
        category_frame_id=str(header_options.get("category_frame_id") or "") or None,
        font=str(header_options.get("font") or "") or None,
        exact_match=True,
    )
    assert header.font == "serif_bold_italic"
    assert header.status == "changed"


def test_auto_detect_options_carry_category_styles_and_saved_locks() -> None:
    options = plan_service.normalize_plan_options(
        _options(
            category_styles={
                str(CATEGORY_ID): {"font": "small_caps", "category_font": None, "separator_id": None, "category_frame_id": None}
            },
            category_format_locks={str(CATEGORY_ID): {"font": "monospace", "separator_id": "bar_full"}},
        ),
        strict=True,
    )
    records = [
        {"id": str(CATEGORY_ID), "category_id": "", "kind": "category", "name": "general"},
        {"id": str(CHANNEL_ID), "category_id": str(CATEGORY_ID), "kind": "text", "name": "general-chat"},
    ]

    # Majority inference registers runtime-only frame/separator specs in the
    # studio module globals. Snapshot and restore them so this test cannot
    # leak catalog entries into other suites sharing the process.
    catalog_snapshot = (
        studio.CATEGORY_FRAMES,
        dict(studio.CATEGORY_FRAMES_BY_ID),
        studio.SEPARATOR_LIBRARY,
        dict(studio.SEPARATORS_BY_ID),
    )
    try:
        inferred, _profiles = majority.build_category_aware_options(studio, options, records)
    finally:
        (
            studio.CATEGORY_FRAMES,
            studio.CATEGORY_FRAMES_BY_ID,
            studio.SEPARATOR_LIBRARY,
            studio.SEPARATORS_BY_ID,
        ) = catalog_snapshot

    # The style survives inference, and the saved category lock stays in place
    # above it, so the resolver keeps the lock authoritative.
    assert inferred["category_styles"][str(CATEGORY_ID)]["font"] == "small_caps"
    effective = _resolve(inferred, channel_id=CATEGORY_ID, kind="category")
    assert effective["__format_lock_scope"] == "category"
    assert effective["font"] == "monospace"


# ---------------------------------------------------------------------------
# Studio UI: Category Styles screen (spec: pick category, stamp template or
# fonts, save through the same load/save path as the other studio pickers)
# ---------------------------------------------------------------------------


def _fake_guild() -> SimpleNamespace:
    garden = SimpleNamespace(
        id=CATEGORY_ID,
        name="The Garden",
        position=0,
        channels=[SimpleNamespace(id=CHANNEL_ID, name="general-chat", position=0)],
    )
    staff = SimpleNamespace(id=556, name="Staff", position=1, channels=[])
    return SimpleNamespace(
        id=4242,
        categories=[garden, staff],
        get_channel=lambda channel_id: None,
    )


def _fake_interaction(guild: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=92),
        response=SimpleNamespace(
            defer=AsyncMock(),
            edit_message=AsyncMock(),
            is_done=lambda: False,
        ),
        edit_original_response=AsyncMock(),
    )


def _select(view: object, placeholder_part: str) -> object:
    return next(
        item
        for item in getattr(view, "children", [])
        if placeholder_part in str(getattr(item, "placeholder", "") or "")
    )


def _button(view: object, label: str) -> object:
    return next(item for item in getattr(view, "children", []) if getattr(item, "label", "") == label)


def test_frame_view_opens_category_styles_screen(monkeypatch) -> None:
    monkeypatch.setattr(design, "_require_design_permission", AsyncMock(return_value=True))
    guild = _fake_guild()
    interaction = _fake_interaction(guild)

    async def check() -> None:
        frame_view = design.DesignServerCategoryFrameView({})
        await _button(frame_view, "Category Styles").callback(interaction)  # type: ignore[attr-defined]
        kwargs = interaction.response.edit_message.await_args.kwargs
        assert isinstance(kwargs["view"], design.DesignCategoryStylesView)
        assert kwargs["view"].category_id == CATEGORY_ID

    asyncio.run(check())


def test_template_stamp_and_font_override_update_the_draft(monkeypatch) -> None:
    monkeypatch.setattr(design, "_require_design_permission", AsyncMock(return_value=True))
    guild = _fake_guild()
    interaction = _fake_interaction(guild)

    async def check() -> None:
        view = design.DesignCategoryStylesView(guild, {})
        template_select = _select(view, "zone template")
        template_select._values = ["garden"]  # type: ignore[attr-defined]
        await template_select.callback(interaction)  # type: ignore[attr-defined]
        stamped = interaction.response.edit_message.await_args.kwargs["view"]
        assert stamped.template_id == "garden"
        assert stamped.draft["category_font"] == "serif_bold_italic"
        assert stamped.draft["font"] == "italic_sans"

        interaction.response.edit_message.reset_mock()
        font_select = _select(stamped, "channel font")
        font_select._values = ["monospace"]  # type: ignore[attr-defined]
        await font_select.callback(interaction)  # type: ignore[attr-defined]
        customised = interaction.response.edit_message.await_args.kwargs["view"]
        assert customised.draft["font"] == "monospace"
        assert customised.draft["category_font"] == "serif_bold_italic"
        assert customised.template_id == ""

    asyncio.run(check())


def test_save_category_style_uses_the_shared_options_save_path(monkeypatch) -> None:
    monkeypatch.setattr(design, "_require_design_permission", AsyncMock(return_value=True))
    saved: list[dict] = []

    async def fake_save(interaction: object, options: dict) -> dict:
        saved.append(dict(options))
        return options

    monkeypatch.setattr(design, "_load_design_options", AsyncMock(return_value={"theme_id": "gothic_clean"}))
    monkeypatch.setattr(design.legacy, "_save_options", fake_save)
    guild = _fake_guild()
    interaction = _fake_interaction(guild)

    async def check() -> None:
        view = design.DesignCategoryStylesView(
            guild,
            {},
            category_id=CATEGORY_ID,
            draft=zones.category_style_from_template("nightlife"),
            template_id="nightlife",
        )
        await _button(view, "Save Category Style").callback(interaction)  # type: ignore[attr-defined]

    asyncio.run(check())

    assert len(saved) == 1
    assert saved[0]["category_styles"] == {
        str(CATEGORY_ID): {
            "font": "bold_sans",
            "category_font": "bold_script",
            "separator_id": None,
            "category_frame_id": None,
        }
    }


def test_save_with_no_style_selected_removes_the_entry(monkeypatch) -> None:
    monkeypatch.setattr(design, "_require_design_permission", AsyncMock(return_value=True))
    saved: list[dict] = []

    async def fake_save(interaction: object, options: dict) -> dict:
        saved.append(dict(options))
        return options

    existing = {
        "theme_id": "gothic_clean",
        "category_styles": {str(CATEGORY_ID): {"font": "sans", "category_font": None, "separator_id": None, "category_frame_id": None}},
    }
    monkeypatch.setattr(design, "_load_design_options", AsyncMock(return_value=dict(existing)))
    monkeypatch.setattr(design.legacy, "_save_options", fake_save)
    guild = _fake_guild()
    interaction = _fake_interaction(guild)

    async def check() -> None:
        view = design.DesignCategoryStylesView(guild, existing, category_id=CATEGORY_ID, clear_style=True)
        await _button(view, "Save Category Style").callback(interaction)  # type: ignore[attr-defined]

    asyncio.run(check())

    assert saved[0]["category_styles"] == {}
