from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

from stoney_verify.commands_ext import public_ticket_panel_clean as clean


def test_custom_ticket_label_is_not_rewritten_to_generic_support() -> None:
    row = {
        "slug": "vip_support_portal",
        "name": "VIP Support Desk",
        "button_label": "VIP Support Desk",
        "description": "Help for VIP members.",
        "is_enabled": True,
        "managed_by_dank": False,
    }

    assert clean._row_name(row) == "VIP Support Desk"
    assert clean._row_desc(row) == "Help for VIP members."


def test_member_menu_dedupe_collapses_true_aliases_without_collapsing_custom_rows() -> None:
    rows = clean._rows(
        [
            {
                "slug": "support",
                "name": "Support",
                "button_label": "Support",
                "is_enabled": True,
                "managed_by_dank": True,
                "managed_category_key": "support",
                "managed_catalog_version": 3,
                "sort_order": 999,
            },
            {
                "slug": "general-support",
                "name": "Support",
                "button_label": "Support",
                "is_enabled": True,
                "managed_by_dank": False,
                "sort_order": 998,
            },
            {
                "slug": "vip_support_portal",
                "name": "VIP Support Desk",
                "button_label": "VIP Support Desk",
                "description": "Help for VIP members.",
                "is_enabled": True,
                "managed_by_dank": False,
                "sort_order": 20,
            },
        ]
    )

    labels = [clean._row_name(row) for row in rows]
    assert labels.count("Support") == 1
    assert labels.count("VIP Support Desk") == 1
    assert len(labels) == 2


def test_ticket_menu_loads_each_guild_from_canonical_managed_state(monkeypatch) -> None:
    calls: list[int] = []

    async def fake_state(guild_id: int):
        calls.append(int(guild_id))
        label = "Alpha Requests" if int(guild_id) == 111 else "Beta Requests"
        return SimpleNamespace(
            active_rows=[
                {
                    "slug": f"guild_{guild_id}_request",
                    "name": label,
                    "button_label": label,
                    "description": f"Configured only for guild {guild_id}.",
                    "is_enabled": True,
                    "managed_by_dank": False,
                    "sort_order": 10,
                }
            ],
            required=False,
            reason="",
        )

    monkeypatch.setattr(clean.managed_categories, "ensure_category_setup_state", fake_state)

    async def scenario() -> None:
        alpha, alpha_warning = await clean._load_rows(SimpleNamespace(id=111))
        beta, beta_warning = await clean._load_rows(SimpleNamespace(id=222))

        assert [clean._row_name(row) for row in alpha] == ["Alpha Requests"]
        assert [clean._row_name(row) for row in beta] == ["Beta Requests"]
        assert alpha_warning == ""
        assert beta_warning == ""

    asyncio.run(scenario())
    assert calls == [111, 222]


def test_clean_loader_does_not_bypass_canonical_category_service() -> None:
    source = inspect.getsource(clean._load_rows)
    assert "ensure_category_setup_state" in source
    assert 'table("ticket_categories")' not in source
