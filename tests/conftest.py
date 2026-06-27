from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
root_text = str(ROOT)
if root_text not in sys.path:
    sys.path.insert(0, root_text)


_ORIGINAL_READ_TEXT = Path.read_text
_LEGACY_DESIGN_GUARD = "stoney_verify/startup_guards/server_design_studio_command_guard.py"
_REAL_DESIGN_SOURCE = ROOT / "stoney_verify/commands_ext/public_design_studio.py"


def _normalized_path_text(path: Path) -> str:
    return str(path).replace("\\", "/")


def _read_text_with_design_source_redirect(self: Path, *args, **kwargs) -> str:
    """Keep old static tests aimed at the real Dank Design implementation.

    The runtime guard is now a thin compatibility shim, while the product code
    lives in commands_ext/public_design_studio.py. Many older static tests still
    inspect the old guard path at import time. Redirect only that exact source
    read so tests continue checking the real implementation instead of the shim.
    """

    if _normalized_path_text(self).endswith(_LEGACY_DESIGN_GUARD):
        return _ORIGINAL_READ_TEXT(_REAL_DESIGN_SOURCE, *args, **kwargs)
    return _ORIGINAL_READ_TEXT(self, *args, **kwargs)


if getattr(Path.read_text, "__name__", "") != "_read_text_with_design_source_redirect":
    Path.read_text = _read_text_with_design_source_redirect
