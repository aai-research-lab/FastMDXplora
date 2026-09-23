"""Where this user's settings live, outside any study.

Three things are kept per user rather than per study: which model writes
configs, what this machine measured for the cost of a step, and the machines
a study can be sent to. Each is a fact about the person or the hardware, and
none of them belongs in a config that is supposed to run anywhere.

The rule for the directory was written out in two modules before a third
needed it. It lives here now, so a user who moves it with
``FASTMDXPLORA_CONFIG_DIR`` moves all of them at once.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["user_config_dir"]


def user_config_dir() -> Path:
    """The directory holding this user's FastMDXplora settings.

    ``FASTMDXPLORA_CONFIG_DIR`` wins where it is set, so a cluster job, a
    test or a shared machine can point elsewhere without touching the home
    directory. Otherwise the platform's convention: ``%APPDATA%`` on
    Windows, ``$XDG_CONFIG_HOME`` or ``~/.config`` everywhere else.
    """
    root = os.environ.get("FASTMDXPLORA_CONFIG_DIR")
    if root:
        return Path(root)
    if os.name == "nt":  # pragma: no cover - platform-specific
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "fastmdxplora"
