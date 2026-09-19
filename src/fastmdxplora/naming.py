"""One rule for the name of a study's output folder.

    fastmdxplora_<system>_study_<UTC timestamp>

A folder called ``fastmdxplora_output_20260919_022007`` said nothing about
what it held; ``fastmdxplora_1UAO_study_20260919022007`` says the system
and that it is a study, and sorts by time. The rule lived in six places
in Python and one in JavaScript, two of them with no timestamp at all, so
a second run could land in the first run's folder. It lives here; every
caller asks.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

__all__ = ["default_output_name", "system_slug"]

_PREFIX = "fastmdxplora"


def system_slug(system: Any) -> str:
    """The system as a folder-safe word: a PDB id as typed, a file by its
    stem, a Unicode or spaced name reduced to what a shell accepts."""
    if system is None:
        return ""
    text = str(system).strip()
    if not text:
        return ""
    # Split on either separator by hand: a Windows path written on a Mac
    # -- C:\runs\lys.cif -- is one component to POSIX Path.
    last = re.split(r"[\\/]", text)[-1]
    stem = last.rsplit(".", 1)[0] if "." in last else last
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-_")
    return stem[:40]


def default_output_name(system: Any = None, *, when: datetime | None = None) -> str:
    """``fastmdxplora_<system>_study_<YYYYMMDDHHMMSS>``, UTC.

    With no system known, ``fastmdxplora_study_<timestamp>``: still a study,
    still timestamped, never a fixed name two runs could collide on.
    """
    stamp = (when or datetime.now(timezone.utc)).strftime("%Y%m%d%H%M%S")
    slug = system_slug(system)
    middle = f"_{slug}" if slug else ""
    return f"{_PREFIX}{middle}_study_{stamp}"


def system_of(config: Any) -> Any:
    """The first system a config names, for naming its folder."""
    if not isinstance(config, dict):
        return None
    systems = config.get("systems")
    if isinstance(systems, list) and systems:
        first = systems[0]
        if isinstance(first, dict):
            return first.get("id") or first.get("system")
        return first
    return config.get("system")
