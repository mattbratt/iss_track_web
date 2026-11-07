"""The integration's engine and frontend assets, from the pinned submodule.

The ISS Track integration is a git submodule at vendor/iss_track. Its engine
package imports nothing from Home Assistant, so it can be put on the path
and imported as is. The website never carries a copy of the pass math.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INTEGRATION = ROOT / "vendor" / "iss_track" / "custom_components" / "iss_track"
WWW = INTEGRATION / "www"
WEB = ROOT / "web"

if not (INTEGRATION / "engine").is_dir():
    raise RuntimeError(
        "The engine is missing. Run: git submodule update --init"
    )

if str(INTEGRATION) not in sys.path:
    sys.path.insert(0, str(INTEGRATION))

import engine  # noqa: E402
import const as policy  # noqa: E402  (the integration's timings and limits)

__all__ = ["INTEGRATION", "ROOT", "WEB", "WWW", "engine", "policy"]
