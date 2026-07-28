from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from stoney_verify.profile_card_runtime import _compact_server_role_labels
from stoney_verify.profile_card_service import DEFAULT_PROFILE_PREFERENCES, normalize_preferences
from stoney_verify.profile_custom_background import normalize_profile_background_upload
from stoney_verify.profile_signature_style import (
    MEMBER_CUSTOM_BACKGROUND_KEY,
    effective_profile_style,
    encode_profile_asset,
    theme_style_updates,
)


class FakeRole:
    def __init__(self, role_id: int, name: str, position: int) -> None:
        self.id = role_id
        self.name = name
        self.position = position
        self.managed = False

    def is_default(self) -> bool:
        return False

    def __lt__(self, other):
        return self.position < other.position


def test_live_signatures_are_opt_in_by_default() -> None:
    assert DEFAULT_PROFILE_PREFERENCES["live_cards_enabled"] is False
    assert normalize_preferences({})["live_cards_enabled"] is False
    assert normalize_preferences({"live_cards_enabled": True})["live_cards_enabled"] is True


def test_server_branding_has_an_independent_default() -> None:
    assert DEFAULT_PROFILE_PREFERENCES["show_server_branding"] is True


def test_owner_truth_precedes_real_complete_roles() -> None:
    guild = SimpleNamespace(owner_id=42)
    member = SimpleNamespace(
        id=42,
        guild=guild,
        roles=[
            FakeRole(1, "Actually Extremely Long But Complete Community Role", 20),
            FakeRole(2, "Second Real Role", 10),
        ],
    )
    assert _compact_server_role_labels(member, {}) == [
        "Server Owner",
        "Actually Extremely Long But Complete Community Role",
        "Second Real Role",
    ]


def test_theme_change_preserves_independent_overrides() -> None:
    assert theme_style_updates("purple", member=True) == {"signature_theme": "purple"}
    assert theme_style_updates("dark", member=False) == {"profile_signature_theme": "dark"}


def test_member_custom_background_wins_over_server_background() -> None:
    personal = encode_profile_asset(b"personal")
    server = encode_profile_asset(b"server")
    preferences = {
        "signature_background_mode": "custom",
        MEMBER_CUSTOM_BACKGROUND_KEY: personal,
    }
    config = {"profile_signature_custom_background_b64": server}
    assert effective_profile_style(preferences, config)["custom_background"] == b"personal"


def test_uploaded_background_is_normalized_to_exact_card_size() -> None:
    source = Image.new("RGB", (2800, 600), (10, 20, 30))
    payload = BytesIO()
    source.save(payload, format="PNG")
    normalized = normalize_profile_background_upload(payload.getvalue())
    with Image.open(BytesIO(normalized)) as opened:
        assert opened.size == (1400, 300)


def test_uploaded_background_rejects_wrong_ratio() -> None:
    source = Image.new("RGB", (1400, 1400), (10, 20, 30))
    payload = BytesIO()
    source.save(payload, format="PNG")
    try:
        normalize_profile_background_upload(payload.getvalue())
    except ValueError as exc:
        assert "14:3" in str(exc)
    else:
        raise AssertionError("wrong-ratio upload was accepted")
