"""Every study setting, carried through every interface and back.

The interfaces are meant to be generated from one source -- the schema -- so
that any study can be written as a config, a command, a script or a form. This
measures whether that holds: each setting is given a value other than its
default, carried out through an interface, read back, and compared.

"Read back" is literal wherever the interface can be run: the command is parsed
by the real parser into the config `explore` builds, the script is executed
against a stand-in for the package that records what it is given, and the
resolved config is written and loaded. The form is read from the payload the
GUI builds its controls from.
"""

from __future__ import annotations

import contextlib
import io
import shlex
import tempfile
from pathlib import Path
from typing import Any, Callable

#: The study-level keys with no field description, so nothing is generated
#: from them: every interface has to know them by hand.
UNDESCRIBED = {
    "sweep": {"simulation.temperature_K": [300.0, 310.0]},
    "systems": [{"system": "1UBQ"}, {"system": "2LZM"}],
}

INTERFACES = ("cli", "cli_command", "python_script", "gui", "resolved_config")

#: Settings that decide which runs a study has -- listed runs, or windows an
#: umbrella block expands into -- and so are compared as runs.
BECOME_RUNS = ("systems", "umbrella")


def the_runs(config: dict[str, Any]) -> list[tuple[Any, Any]]:
    """Each run a normalised study will make: its system and its window."""
    runs = []
    for entry in config.get("systems") or []:
        simulation = entry.get("simulation") or (entry.get("options") or {}).get("simulation") or {}
        runs.append((entry.get("system"), simulation.get("umbrella")))
    return runs


def settings() -> list[tuple[str, str]]:
    """Every study setting as (block, name); block is a phase, "execution",
    "(top-level)", or "(study)" for the undescribed study keys."""
    from fastmdxplora.config.schema import all_schemas

    found = [(block, field.name) for block, group in all_schemas().items()
             for field in group.fields]
    return found + [("(study)", key) for key in UNDESCRIBED]


def field(block: str, name: str) -> Any:
    from fastmdxplora.config.schema import all_schemas

    group = all_schemas().get(block)
    return next((f for f in group.fields if f.name == name), None) if group else None


def a_value_other_than_the_default(block: str, name: str) -> Any:
    """A value the setting accepts that differs from its default, so a setting
    that is dropped cannot pass by coming back as the default."""
    if block == "(study)":
        return UNDESCRIBED[name]
    f = field(block, name)
    default = f.default
    if f.choices:
        return next((c for c in f.choices if c != default), f.choices[0])
    kind = f.type
    if isinstance(kind, tuple):
        kind = float if float in kind else int if int in kind else kind[0]
    if kind is bool:
        return not bool(default)
    if kind in (int, float):
        low = f.minimum if f.minimum is not None else 1
        high = f.maximum if f.maximum is not None else None
        value = kind(low) + (kind(1) if kind is int else kind(0.5))
        if high is not None and value > high:
            value = kind(high)
        if value == default:
            value = kind(low)
        return value
    if f.example not in (None, "", [], {}) and f.example != default:
        return f.example
    if kind is dict:
        return {"key": "value"}
    if kind is list:
        return ["x"]
    return "x"


def a_config(block: str, name: str, value: Any) -> dict[str, Any]:
    """The smallest study that sets this one setting, in the shape a config
    file takes: its input named under `systems`, as a config must."""
    config: dict[str, Any] = {"systems": [{"system": "1UBQ"}]}
    if block == "(study)":
        config[name] = value
    elif block == "(top-level)":
        config[name] = value
    else:
        config[block] = {name: value}
    return config


def read_back(config: dict[str, Any], block: str, name: str) -> Any:
    """Where the setting sits in a config an interface produced."""
    if block in ("(study)", "(top-level)"):
        if name == "system" and "system" not in config and config.get("systems"):
            return (config["systems"][0] or {}).get("system")
        return config.get(name)
    return (config.get(block) or {}).get(name)


def same(a: Any, b: Any) -> bool:
    # A bare value and a one-item list are the same setting for a list field.
    if isinstance(a, (list, tuple)) and not isinstance(b, (list, tuple, dict)) and len(a) == 1:
        a = a[0]
    if isinstance(b, (list, tuple)) and not isinstance(a, (list, tuple, dict)) and len(b) == 1:
        b = b[0]
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    # A number only matches a number: "310" arriving for 310 is a setting
    # the interface carried as text, not the same setting.
    numbers = (int, float)
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, numbers) and isinstance(b, numbers):
        return float(a) == float(b)
    return a == b


# --------------------------------------------------------------- interfaces

def _explore_parser():
    from fastmdxplora.cli.main import _build_parser

    parser = _build_parser()
    action = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    return parser, action.choices["explore"]


def through_the_command(config: dict[str, Any]) -> dict[str, Any]:
    """config -> cli_command -> the real parser -> the config explore builds."""
    from fastmdxplora.cli.main import _build_explore_config
    from fastmdxplora.config.languages import cli_command

    parser, _ = _explore_parser()
    argv = shlex.split(cli_command(config))[1:]
    return _build_explore_config(parser.parse_args(argv))


def through_the_script(config: dict[str, Any]) -> dict[str, Any]:
    """config -> python_script -> executed against a recording stand-in."""
    import types

    from fastmdxplora.config.languages import python_script

    seen: dict[str, Any] = {}

    class Study:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def explore(self, **kwargs):
            seen.update(kwargs)
            return []

    class Batch:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def run(self, **kwargs):
            seen.update(kwargs)
            return []

    stand_in = types.ModuleType("fastmdxplora")
    stand_in.FastMDXplora = Study
    stand_in.BatchExplorer = Batch
    source = python_script(config).replace("import fastmdxplora as fastmdx", "")
    exec(compile(source, "<python_script>", "exec"), {"fastmdx": stand_in, "__name__": "x"})
    rebuilt: dict[str, Any] = dict(seen.get("options") or {})
    for key, value in seen.items():
        if key != "options":
            rebuilt["output" if key == "output_dir" else key] = value
    config_given = seen.get("config_data") or seen.get("config")
    if isinstance(config_given, dict):
        # Checked the way the API checks a whole study before it runs one, so
        # a script that would be refused is not counted as carrying anything.
        import copy

        from fastmdxplora.config.loader import validate_config

        validate_config(copy.deepcopy(config_given), require_systems=True)
        rebuilt.update(config_given)
    return rebuilt


def through_the_resolved_config(config: dict[str, Any]) -> dict[str, Any]:
    from fastmdxplora.config import load_config_file, write_resolved_config

    from fastmdxplora.config.schema import PHASE_KEYS

    # Shaped as the orchestrator hands it over: phase blocks under `options`.
    handed = {k: v for k, v in config.items() if k not in PHASE_KEYS}
    handed["options"] = {k: v for k, v in config.items() if k in PHASE_KEYS}
    where = Path(tempfile.mkdtemp())
    return load_config_file(write_resolved_config(handed, where))


def the_form() -> dict[str, set[str]]:
    """Every control the GUI builds, by the block the payload files it under."""
    from fastmdxplora.gui.schema_payload import schema_payload

    payload = schema_payload()
    blocks: dict[str, set[str]] = {}
    for phase, fields in (payload.get("phases") or {}).items():
        items = fields.get("fields", fields) if isinstance(fields, dict) else fields
        blocks[phase] = {f.get("name") for f in items if isinstance(f, dict)}
    blocks["(top-level)"] = {f.get("name") for f in payload.get("run_options") or []
                             if isinstance(f, dict)}
    blocks["execution"] = {f.get("name") for f in payload.get("execution_options") or []
                           if isinstance(f, dict)}
    # The phase lists are built in the page from its phase switches, one per
    # entry of `phases`. The output folder is not offered: the server places
    # each study in its workspace.
    if payload.get("phases"):
        blocks["(top-level)"] |= {"include_phase", "exclude_phase"}
    # A sweep is offered as rows over the settings the payload lists.
    if payload.get("sweep_axes"):
        blocks["(study)"] = {"sweep"}
    return blocks


def the_cli_flag(block: str, name: str) -> str | None:
    """The long flag `explore` takes for this setting, if any."""
    import importlib

    cli = importlib.import_module("fastmdxplora.cli.main")
    _, explore = _explore_parser()
    verb = {v: k for k, v in cli._SCHEMA_KEY.items()}.get(block, block)
    hyphen = name.replace("_", "-")
    if block in ("(top-level)", "(study)"):
        predicted, dests = {f"--{hyphen}", f"--no-{hyphen}"}, {name, f"{name}_dir", name.split("_")[0]}
    else:
        prefix = "execution" if block == "execution" else verb
        predicted = {f"--{prefix}-{hyphen}", f"--{prefix}-no-{hyphen}"}
        dests = {f"{verb}__{name}", f"execution__{name}"}
    for action in explore._actions:
        if predicted & set(action.option_strings):
            return next(iter(predicted & set(action.option_strings)))
    for action in explore._actions:
        if action.dest in dests and action.option_strings:
            return max(action.option_strings, key=len)
    return None


def measure() -> dict[tuple[str, str], dict[str, Any]]:
    """For every setting: whether each interface carries it, and its name there."""
    form = the_form()
    table: dict[tuple[str, str], dict[str, Any]] = {}
    round_trips: dict[str, Callable] = {
        "cli_command": through_the_command,
        "python_script": through_the_script,
        "resolved_config": through_the_resolved_config,
    }
    import copy

    from fastmdxplora.config.loader import normalise_config

    for block, name in settings():
        value = a_value_other_than_the_default(block, name)
        config = a_config(block, name, value)
        try:
            wanted = normalise_config(copy.deepcopy(config))
        except Exception:  # noqa: BLE001 -- the value itself is not a study
            wanted = config
        row: dict[str, Any] = {"value": value}
        flag = the_cli_flag(block, name)
        row["cli"] = flag is not None
        row["cli_name"] = flag
        for interface, trip in round_trips.items():
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    # A route gets what a user hands it; the resolved config is
                    # written from the study the orchestrator holds, normalised.
                    given = wanted if interface == "resolved_config" else config
                    produced = normalise_config(trip(copy.deepcopy(given)))
                row[interface] = (
                    same(read_back(produced, block, name), read_back(wanted, block, name))
                    and (name not in BECOME_RUNS or same(the_runs(produced), the_runs(wanted))))
            except SystemExit:
                row[interface] = False
                row[f"{interface}_why"] = "the parser refused it"
            except Exception as exc:  # noqa: BLE001 -- the reason is the finding
                row[interface] = False
                row[f"{interface}_why"] = type(exc).__name__
        row["gui"] = name in form.get(block, set())
        table[(block, name)] = row
    return table
