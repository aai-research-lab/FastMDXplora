"""Run what the page describes, from the file the page would give you.

The dashboard used to start a run by assembling a command out of hand-picked
flags -- four setup settings, a few simulation ones -- which meant the GUI
could only ever start the kind of run somebody had thought to wire up. An
analysis of a trajectory that already exists was not among them: the phase list
was fixed at setup and simulation before anything else was considered.

So a run starts from a config file instead, the same file the download button
hands over. ``fastmdx explore --config`` is what runs here and what runs on the
cluster, from the same bytes. There is no local path and remote path to keep in
agreement, because there is one path.

That also settles what a run can contain. A config names its phases, so
analysing an existing trajectory is a config with one phase in it, and needs no
special case anywhere.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastmdxplora.gui.config_builder import config_yaml, render_config

__all__ = ["prepare_run", "CONFIG_FILENAME"]

#: Written beside the results, so a run can always be repeated from what is in
#: the output directory rather than from what somebody remembers typing.
CONFIG_FILENAME = "exploration.yml"


def prepare_run(state: dict[str, Any] | None, output_dir: str | Path, *,
                config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write the config for a run and give back the command that runs it.

    Nothing is launched here. Writing the file and starting the process are
    separate so that the file can be checked -- and so a test can confirm that
    what would run locally is the same thing the download hands over.
    """
    # A config, or the form state to build one from. Never both, and the
    # config wins: it is the thing itself, not a description of it.
    if config is not None:
        built = render_config(dict(config), full=bool((state or {}).get("full")))
    else:
        built = config_yaml(state or {})
    if not built["ok"]:
        return {
            "ok": False,
            "error": built["error"],
            "config_path": None,
            "command": None,
        }

    root = Path(output_dir).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / CONFIG_FILENAME
    config_path.write_text(built["yaml"], encoding="utf-8")

    command = [
        sys.executable,
        "-m",
        "fastmdxplora.cli.main",
        "explore",
        "--config",
        str(config_path),
        "--output",
        str(root),
    ]
    return {
        "ok": True,
        "error": None,
        "config_path": str(config_path),
        "config_yaml": built["yaml"],
        "command": command,
        "settings_changed": built["settings_changed"],
    }
