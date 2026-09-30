"""What made each figure, and the command that makes it again.

A figure copied into a paper leaves its study behind, and with it everything
that says what it is: which release drew it and from which numbers, how many
frames it rests on, the selection and the options, and how to draw it again.
All of that is recorded (the analysis manifest, each analysis's
`options.json`, the phase's `produced_by` in the Manifest), and the Analysis
page showed none of it. This gathers it per analysis for a chip on each
figure.

The command reruns the one analysis over the same trajectory, with the same
frames, selection and options, into a folder of its own beside the study, so
nothing of the study is overwritten. It is rendered by the command line's own
translator (:func:`fastmdxplora.config.languages.cli_command`), which a round
trip through the real parser holds to the config it came from; where a
setting has no flag, the config is given instead.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

__all__ = ["figure_provenance"]

#: The widths a figure can be drawn at (`analysis.figure_width`).
WIDTHS = ("page", "single_column", "double_column")

#: The packages whose version decides what an analysis reports, in the
#: order the chip names them.
_NAMED_PACKAGES = ("mdtraj", "numpy", "scipy", "matplotlib")


def figure_provenance(root: str | Path) -> dict[str, dict[str, Any]]:
    """Per analysis that finished, what made its figures and how to make
    them again. Empty where the study has not been analysed."""
    from fastmdxplora import __version__

    base = Path(root)
    manifest = _read(base / "analysis" / "analysis_manifest.json")
    if not isinstance(manifest, dict):
        return {}
    phase = _analysis_phase(_read(base / "manifest.json"))
    produced = phase.get("produced_by") if isinstance(phase.get("produced_by"), dict) else {}
    environment = (produced.get("environment")
                   if isinstance(produced.get("environment"), dict) else {})
    resolved = manifest.get("resolved") if isinstance(manifest.get("resolved"), dict) else {}
    results = manifest.get("results") if isinstance(manifest.get("results"), dict) else {}
    made_with = str(produced.get("version") or "")

    found: dict[str, dict[str, Any]] = {}
    for name, result in results.items():
        if not isinstance(result, dict) or result.get("status") != "ok":
            continue
        record = _read(base / "analysis" / str(name) / "options.json")
        record = record if isinstance(record, dict) else {}
        options = record.get("options") if isinstance(record.get("options"), dict) else {}
        selection = record.get("selection")
        config = _the_config_that_makes_it_again(base, str(name), resolved, options, selection)
        command, config_text = _said(config)
        width = str(manifest.get("figure_width") or resolved.get("figure_width") or "page")
        # The same, drawn at each other width a journal sets figures at.
        at_widths = {}
        for other in WIDTHS:
            if other == width:
                continue
            sized = json.loads(json.dumps(config))
            sized["analysis"]["figure_width"] = other
            sized["output"] = f"{config['output']}_{other}"
            said, text = _said(sized)
            at_widths[other] = said or text
        found[str(name)] = {
            "analysis": str(name),
            "version": made_with or None,
            "this_version": str(__version__),
            "host": produced.get("host") or None,
            "packages": {package: environment.get(package) for package in _NAMED_PACKAGES
                         if environment.get(package)},
            "made": result.get("finished_at") or phase.get("finished_at") or None,
            "trajectory": _shown(base, resolved.get("trajectory")),
            "frames": manifest.get("n_frames"),
            "stride": resolved.get("stride"),
            "first": resolved.get("first"),
            "last": resolved.get("last"),
            "selection": selection,
            "options": options,
            "command": command,
            "config": config_text,
            "width": width,
            "at_widths": at_widths,
        }
    return found


def _the_config_that_makes_it_again(base: Path, name: str, resolved: dict[str, Any],
                                    options: dict[str, Any], selection: Any) -> dict[str, Any]:
    analysis: dict[str, Any] = {
        "trajectory": resolved.get("trajectory"),
        "topology": resolved.get("topology"),
        "include": [name],
    }
    for key in ("stride", "first", "last", "figure_colours", "figure_width"):
        if resolved.get(key) is not None and resolved.get(key) not in ("colour", "page"):
            analysis[key] = resolved[key]
    if selection and selection != resolved.get("selection"):
        analysis.setdefault("options", {}).setdefault(name, {})["selection"] = selection
    elif resolved.get("selection"):
        analysis["selection"] = resolved["selection"]
    if options:
        analysis.setdefault("options", {}).setdefault(name, {}).update(options)
    # The structure the trajectory refers to is the system, as the builder
    # writes a study of a trajectory it was given.
    system = resolved.get("topology")
    return {"systems": [{"system": system}] if system else [],
            "include_phase": ["analysis"],
            "output": str(base.resolve().parent / f"{base.name}_{name}_again"),
            "analysis": {key: value for key, value in analysis.items() if value is not None}}


def _said(config: dict[str, Any]) -> tuple[str | None, str | None]:
    """The command, or the config where a setting has no flag."""
    from fastmdxplora.config.languages import UntranslatableSetting, cli_command

    try:
        return cli_command(config), None
    except (UntranslatableSetting, ValueError, KeyError):
        import yaml

        return None, yaml.safe_dump(config, sort_keys=False, default_flow_style=False)


def _analysis_phase(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        return {}
    for phase in manifest.get("phases") or []:
        if isinstance(phase, dict) and (phase.get("name") or phase.get("phase")) == "analysis":
            return phase
    return {}


def _shown(base: Path, path: Any) -> Any:
    """A path inside the study, relative to it; anything else as recorded."""
    if isinstance(path, list):
        return [_shown(base, item) for item in path]
    if not isinstance(path, str) or not path:
        return path
    try:
        relative = os.path.relpath(Path(path).resolve(), base.resolve())
    except ValueError:
        return path
    return path if relative.startswith("..") else relative


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
