"""Moved to :mod:`fastmdxplora.structure_info`.

It was never GUI code: it reads a structure as text and returns counts, and
the orchestrator needs it before any interface exists. Kept here as a
re-export because the old path may be imported from outside this repository,
where a rename is somebody else's breakage rather than FastMDXplora's.
"""

from __future__ import annotations

from fastmdxplora.structure_info import *  # noqa: F401,F403
from fastmdxplora.structure_info import __dict__ as _moved

globals().update({k: v for k, v in _moved.items() if not k.startswith("__")})
