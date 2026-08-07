from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from stoney_verify.tickets_new import managed_category_service as categories


def test_member_label_dedupe_does_not_hide_custom_row_from_setup_inventory() -> None:
    support = next(
        row for row in categories.catalog_category_rows()
        if row["managed_category_key"] == "support"
    )
    support["is_enabled"] = True
    custom = {
        "id": "custom-vip-support",
        "slug": "vip_support_portal",
        "name": "Support",
        "button_label": "Support",
        "description": "Owner-created VIP support flow.",
        "intake_type": "custom",
        "sort_order": 15,
        "is_enabled": True,
        "managed_by_dank": False,
    }

    inventory = categories.dedupe_category_rows(
        [support, custom],
        enabled_only=False,
        fallback=False,
    )
    inventory_keys = {categories.canonical_category_key(row) for row in inventory}
    assert inventory_keys == {"support", "custom:vip-support-portal"}

    visible = categories.dedupe_category_rows(
        [support, custom],
        enabled_only=True,
        fallback=False,
    )
    labels = [str(row.get("button_label") or row.get("name") or "").strip().lower() for row in visible]
    assert labels == ["support"]
    assert categories.canonical_category_key(visible[0]) == "support"


def test_runtime_reconcile_runs_stale_key_preflight_before_main_rpc(monkeypatch: Any) -> None:
    class FakeRpc:
        def __init__(self, owner: "FakeSupabase", name: str) -> None:
            self.owner = owner
            self.name = name

        def execute(self) -> SimpleNamespace:
            self.owner.executed.append(self.name)
            data = (
                [{"guild_id": "123", "updated_count": 1}]
                if self.name == "reconcile_dank_ticket_categories"
                else []
            )
            return SimpleNamespace(data=data)

    class FakeSupabase:
        def __init__(self) -> None:
            self.requested: list[tuple[str, dict[str, str]]] = []
            self.executed: list[str] = []

        def rpc(self, name: str, payload: dict[str, str]) -> FakeRpc:
            self.requested.append((name, payload))
            return FakeRpc(self, name)

    fake = FakeSupabase()
    monkeypatch.setattr(categories, "_supabase", lambda: fake)

    result = categories._sync_managed_categories_sync(123)

    assert [name for name, _payload in fake.requested] == [
        "prepare_dank_ticket_category_repair",
        "reconcile_dank_ticket_categories",
    ]
    assert fake.executed == [
        "prepare_dank_ticket_category_repair",
        "reconcile_dank_ticket_categories",
    ]
    assert result == [{"guild_id": "123", "updated_count": 1}]
