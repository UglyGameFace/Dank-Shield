from __future__ import annotations

"""Compatibility import for the presentation-only compact setup module."""

import sys

from stoney_verify.setup_ui import public_setup_compact as _implementation

sys.modules[__name__] = _implementation
