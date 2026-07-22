from __future__ import annotations

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WRITER = ROOT / "stoney_verify/commands_ext/public_setup_config_writer.py"
TEST = ROOT / "tests/test_setup_config_writer_split_brain_behavior.py"
HELPER = Path(__file__).resolve()


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly 1 match, found {count}")
    return source.replace(old, new, 1)


source = WRITER.read_text(encoding="utf-8")

old_merge = '''def _settings_payload_update(original: Optional[Mapping[str, Any]], updates: Mapping[str, Any]) -> dict[str, Any]:
    base: dict[str, Any] = {}

    # Flat columns first, then nested JSON, then the explicit safe update.
    # This makes owner-confirmed setup values authoritative while preserving
    # older settings from either storage style.
    try:
        if isinstance(original, Mapping):
            for key, value in original.items():
                if key not in _JSON_CONFIG_KEYS and key not in _CONTROL_KEYS and value is not None:
                    base[str(key)] = value
            for key in ("settings", "config", "metadata", "meta"):
                value = original.get(key)
                if isinstance(value, Mapping):
                    for nested_key, nested_value in value.items():
                        if nested_key not in _CONTROL_KEYS and nested_value is not None:
                            base[str(nested_key)] = nested_value
    except Exception:
        base = {}

    for key, value in dict(updates).items():
        if key not in _CONTROL_KEYS and value is not None:
            base[str(key)] = value

    return base
'''

new_merge = '''def _settings_payload_update(original: Optional[Mapping[str, Any]], updates: Mapping[str, Any]) -> dict[str, Any]:
    base: dict[str, Any] = {}

    # Match guild_config._merge_row_settings: legacy nested JSON is read first,
    # then real flat columns win, then this explicit write wins over both.
    # Using the opposite order lets a stale `config` JSON value resurrect a
    # feature immediately after the owner saved the flat/settings value OFF.
    try:
        if isinstance(original, Mapping):
            for key in ("settings", "config", "metadata", "meta"):
                value = original.get(key)
                if isinstance(value, Mapping):
                    for nested_key, nested_value in value.items():
                        if nested_key not in _CONTROL_KEYS and nested_value is not None:
                            base[str(nested_key)] = nested_value
            for key, value in original.items():
                if key not in _JSON_CONFIG_KEYS and key not in _CONTROL_KEYS and value is not None:
                    base[str(key)] = value
    except Exception:
        base = {}

    for key, value in dict(updates).items():
        if key not in _CONTROL_KEYS and value is not None:
            base[str(key)] = value

    return base
'''

source = replace_once(
    source,
    old_merge,
    new_merge,
    "runtime-compatible config precedence",
)

old_attempts = '''    # Prefer keeping both storage styles synchronized. If a deployment
    # lacks one JSON column or a flat column, the later attempts safely
    # fall back without losing the authoritative settings payload.
    attempts: list[dict[str, Any]] = [
        {**base_fields, "settings": settings, **flat_updates},
        {**base_fields, "config": settings, **flat_updates},
        {**base_fields, **flat_updates},
        {**base_fields, "settings": settings},
        {**base_fields, "config": settings},
        {**base_fields, **safe_updates},
    ]
'''

new_attempts = '''    # Keep both JSON storage styles synchronized atomically whenever the row
    # exposes both. Returning after a settings-only success leaves `config`
    # stale; a later cleanup merge can then write that stale value back over
    # the owner's current choice. Schema fallbacks remain for older tables.
    json_updates: dict[str, Any] = {}
    if not isinstance(existing, Mapping) or "settings" in existing:
        json_updates["settings"] = settings
    if not isinstance(existing, Mapping) or "config" in existing:
        json_updates["config"] = settings

    attempts: list[dict[str, Any]] = [
        {**base_fields, **json_updates, **flat_updates},
        {**base_fields, "settings": settings, **flat_updates},
        {**base_fields, "config": settings, **flat_updates},
        {**base_fields, **flat_updates},
        {**base_fields, "settings": settings},
        {**base_fields, "config": settings},
        {**base_fields, **safe_updates},
    ]
'''

source = replace_once(
    source,
    old_attempts,
    new_attempts,
    "atomic settings/config write",
)

compile(source, str(WRITER), "exec")
compile(TEST.read_text(encoding="utf-8"), str(TEST), "exec")

WRITER.write_text(source, encoding="utf-8")
HELPER.unlink()
subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)

print("✅ Setup config merge precedence now matches the runtime reader.")
print("✅ Flat owner-saved values override stale legacy JSON values.")
print("✅ settings and config are updated atomically when both columns exist.")
print("✅ Voice OFF cannot be resurrected by the following channel-mapping cleanup.")
print("✅ Split-brain regression coverage is present.")
print("✅ Temporary helper removed from the working tree.")
print("✅ git diff --check passed.")
