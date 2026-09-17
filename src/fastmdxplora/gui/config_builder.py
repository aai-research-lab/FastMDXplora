"""Turn what the page holds into a config file, and say so if it will not work.

The point of writing a config rather than starting a run directly is that the
same file works somewhere else. A laptop is a poor place to run fifty
nanoseconds; a cluster is a poor place to decide what to run. So the browser
builds the file, and the file goes wherever the compute is::

    fastmdx explore --config exploration.yml

Nothing is generated here that the command line would reject. The config is put
through the same validator the CLI uses before it is handed back, so a
configuration that will fail on the cluster fails in the browser instead, while
the person is still looking at the form that produced it.

A file can be written either way. Left to itself it names only what was
decided: a file restating forty settings the software would have chosen anyway
hides the three that were chosen, and those three are what a reader of the
methods section needs.

Asked for in full, it names everything, defaults included. That is the file to
keep beside a result, because it says what the run did without the reader
having to know which version of the software was current at the time -- a
default that moves later cannot silently change what the file meant.

Both are read back the same way. Neither is more correct; they answer
different questions.
"""

from __future__ import annotations

from typing import Any

from fastmdxplora.config.loader import (
    STUDY_LEVEL_KEYS,
    ConfigError,
    validate_config,
)
from fastmdxplora.config.schema import PHASE_SCHEMAS, TOP_LEVEL

__all__ = ["build_config", "config_yaml"]


def _defaults_for(phase: str) -> dict[str, Any]:
    return {f.name: f.default for f in PHASE_SCHEMAS[phase].fields}


def _coerce(value: Any, field: Any) -> Any:
    """Bring a value in from a form, where everything arrives as text.

    A field's type may be several types at once -- a temperature is written
    either 300 or 300.0 -- so the accepted types are taken as a set rather
    than compared one at a time.
    """
    if value is None or value == "":
        return None
    accepted = field.type if isinstance(field.type, tuple) else (field.type,)

    if bool in accepted:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"true", "1", "yes", "on"}
    if dict in accepted and isinstance(value, str):
        # An umbrella, steered or metadynamics block is a mapping, and the
        # form can only send text. Read as YAML, which is what the person is
        # already writing when they put one in a config file -- and what the
        # help text for these settings describes.
        import yaml

        try:
            parsed = yaml.safe_load(value)
        except yaml.YAMLError:
            # Kept as written rather than dropped: validation reports it, and
            # silently discarding a block somebody typed is worse than a
            # refusal that says why.
            return value
        return parsed if isinstance(parsed, dict) else value
    if list in accepted and isinstance(value, str):
        return [part for part in (p.strip() for p in value.split(",")) if part]
    if float in accepted or int in accepted:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return value
        # A whole number goes in as one where the field takes an int, so the
        # file reads 300 rather than 300.0.
        if int in accepted and number.is_integer():
            return int(number)
        return number
    return value



def _coerce_like(value: Any, default: Any) -> Any:
    """Bring a nested option in from a form, guided by what it defaults to.

    The per-analysis settings have no schema Field to consult, so the default
    stands in for the type: a setting that defaults to 5 wants a number, and
    one that defaults to a list wants a list. Left as text, a config would
    record n_clusters: '8' -- which runs, because the analysis casts it, but
    which reads as though somebody meant the string.
    """
    if isinstance(value, str):
        text_value = value.strip()
        if isinstance(default, bool):
            return text_value.lower() in {"true", "1", "yes", "on"}
        if isinstance(default, (list, tuple)):
            return [p.strip() for p in text_value.split(",") if p.strip()]
        if isinstance(default, int) and not isinstance(default, bool):
            try:
                return int(float(text_value))
            except ValueError:
                return value
        if isinstance(default, float):
            try:
                return float(text_value)
            except ValueError:
                return value
    return value


def _analysis_option_defaults(names: Any) -> dict[str, dict[str, Any]]:
    """Every setting each named analysis would use, at its default.

    A full config that stopped at the phase boundary recorded the scope and
    the stride and said nothing about how the clustering was done -- which is
    where the decisions that change a result actually live. "Every setting"
    has to mean the nested ones too.

    Reading them needs the analysis stack. Where it is absent the file is
    written without them rather than not at all: an incomplete record beats a
    refused download.
    """
    try:
        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis.describe import describe_all
    except Exception:  # noqa: BLE001
        return {}

    described = describe_all()
    wanted = list(names) if names else list(described)
    filled: dict[str, dict[str, Any]] = {}
    for name in wanted:
        options = described.get(name)
        if not options:
            continue
        block = {
            option.name: (
                list(option.default)
                if isinstance(option.default, tuple)
                else option.default
            )
            for option in options
            # Settings every analysis shares are recorded once, on the phase,
            # rather than repeated under each analysis.
            if option.owner != "Analysis" and option.default is not None
        }
        if block:
            filled[name] = block
    return filled


def build_config(state: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    """A config dict from the page's state.

    ``state`` holds a block per phase, plus the top-level keys the run needs
    (``system``, ``output``, ``include``). Unknown phases and unknown fields
    are dropped rather than passed through: the page should not be able to
    write a file the command line will refuse to read.

    With ``full``, every setting is named at whatever value it would take,
    default or chosen. Without it, only what differs from the default.
    """
    config: dict[str, Any] = {}

    for key in ("output", "include", "exclude", "verbose"):
        value = state.get(key)
        if value not in (None, "", [], {}):
            config[key] = value

    # A run names its inputs under `systems`, one entry each, so that a
    # config describing one protein and one describing forty have the same
    # shape. The page offers a single system; the file it writes is the same
    # file a batch would use, with one entry in the list.
    system = state.get("system")
    if system:
        entry: dict[str, Any] = {"system": system}
        if state.get("system_id"):
            entry["id"] = state["system_id"]
        config["systems"] = [entry]
    elif state.get("systems"):
        config["systems"] = state["systems"]

    # A full config records what the run will use, so it names every phase the
    # run includes -- not only the ones the form happened to touch. A phase
    # left entirely alone still runs, and still uses values worth recording.
    running = set(config.get("include") or PHASE_SCHEMAS.keys())

    # Settings that belong to the run rather than a phase. The form keeps them
    # under a sentinel key so one control builder draws everything; here they
    # are lifted to where the config file expects them.
    run_options = state.get("__run__")
    if isinstance(run_options, dict):
        top_level = {f.name: f for f in TOP_LEVEL.fields}
        for name, raw in run_options.items():
            field = top_level.get(name)
            if field is None:
                continue
            value = _coerce(raw, field)
            if value is None:
                continue
            if not full and value == field.default:
                continue
            config[name] = value

    # Scheduling, kept under its own sentinel for the same reason: one
    # control builder draws it, and it is lifted here to the block the config
    # file expects. `execution` is a top-level block and not a phase, so it
    # is not in the loop below.
    scheduling = state.get("__execution__")
    if isinstance(scheduling, dict):
        from fastmdxplora.config.schema import EXECUTION

        fields = {f.name: f for f in EXECUTION.fields}
        kept: dict[str, Any] = {}
        for name, raw in scheduling.items():
            field = fields.get(name)
            if field is None:
                continue
            value = _coerce(raw, field)
            if value is None:
                continue
            if not full and value == field.default:
                continue
            kept[name] = value
        if kept:
            config["execution"] = {**(config.get("execution") or {}), **kept}

    for phase, group in PHASE_SCHEMAS.items():
        block = state.get(phase)
        if not isinstance(block, dict):
            if not (full and phase in running):
                continue
            block = {}
        defaults = _defaults_for(phase)
        fields = {f.name: f for f in group.fields}
        kept: dict[str, Any] = {}
        if full:
            # Start from what the run would use, then let the page's choices
            # land on top. A setting nobody touched still appears, at the
            # value it will actually take.
            kept.update({k: v for k, v in defaults.items() if v is not None})
        for name, raw in block.items():
            field = fields.get(name)
            if field is None:
                continue
            value = _coerce(raw, field)
            if value is None:
                continue
            # Restating a default tells a reader nothing and buries the
            # settings that were actually chosen -- unless the whole point of
            # this file is to record every value the run used.
            if not full and value == defaults.get(name):
                continue
            kept[name] = value
        if kept:
            config[phase] = kept

    if full and "analysis" in running:
        # The nested settings, which is where the decisions that change a
        # result are made: which clustering methods, what they compare, how
        # many clusters.
        block = config.setdefault("analysis", {})
        chosen = block.get("include")
        defaults = _analysis_option_defaults(chosen)
        if defaults:
            given = block.get("options") or {}
            for name, values in defaults.items():
                merged = dict(values)
                for key, chosen_value in (given.get(name) or {}).items():
                    merged[key] = _coerce_like(chosen_value, values.get(key))
                defaults[name] = merged
            for name, values in given.items():
                defaults.setdefault(name, values)
            block["options"] = defaults

    return config


def config_yaml(state: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    """The config as text, validated, with what it will do stated plainly.

    Returns ``ok`` false and the validator's own message where the settings
    would be refused, so the failure arrives here rather than on the cluster an
    hour later.
    """
    import yaml

    config = build_config(state, full=full)

    try:
        validate_config(config)
    except ConfigError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "yaml": None,
            "settings_changed": sum(
                len(v) for k, v in config.items() if isinstance(v, dict)
            ),
        }

    header = (
        "# Written by the FastMDXplora GUI.\n"
        "#\n"
        + (
            "# Every setting is named here at the value the run will use,\n"
            "# defaults included, so this file says what the run did without\n"
            "# depending on which version of the software was current.\n"
            if full
            else
            "# Only settings that differ from the defaults appear here, so\n"
            "# what this file says is what was decided. Ask for the full\n"
            "# config to record every value the run will use.\n"
        )
        + "#\n"
        "#     fastmdx explore --config this-file.yml\n"
        "#\n"
    )

    # The same study in its other two languages, so nobody translates a form
    # into flags by hand -- which is where a prefix goes wrong and the run
    # that follows is not the study the form described. A study the command
    # line cannot say (rare; a many-system batch) keeps this file as its
    # only command form and says so.
    from fastmdxplora.config.languages import (
        UntranslatableSetting,
        cli_command,
        python_script,
    )

    # Translated from the *decided* settings, not the full restatement: a
    # full config names every default on purpose so the file is complete,
    # but a command's defaults come free by omission -- and the nested
    # per-analysis options a full file carries have no flags at all. The two
    # forms describe the same run; the short one is the one a person types.
    decided = config if not full else build_config(state, full=False)
    try:
        command = cli_command(decided)
        header += (
            "# Or, without this file:\n"
            "#\n"
            f"#     {command}\n"
            "#\n"
        )
    except UntranslatableSetting as why:
        command = None
        header += f"# ({why})\n#\n"

    body = yaml.safe_dump(config, sort_keys=False, default_flow_style=False)
    return {
        "ok": True,
        "error": None,
        "command": command,
        "script": python_script(decided),
        "yaml": header + body,
        "settings_changed": sum(
            len(v) for k, v in config.items() if isinstance(v, dict)
        ),
    }


def check_config_file(path: str) -> dict[str, Any]:
    """Read a config someone already has and say whether it will run.

    The point of writing a config is that it goes elsewhere -- edited by hand,
    committed beside a paper, carried to a cluster. It should be possible to
    bring one back and be told whether it still works before queueing an hour
    of compute behind it.

    Reports the phases it names, so somebody can see at a glance whether the
    file does what they remember it doing.
    """
    import yaml

    from pathlib import Path

    target = Path(path).expanduser()
    if not target.exists():
        return {"ok": False, "error": f"No such file: {target}"}
    if target.is_dir():
        return {"ok": False, "error": f"That is a folder, not a config: {target}"}

    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": f"Could not read it: {exc}"}

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        # A syntax error, which is the most common thing to come back with
        # after somebody has edited a file by hand.
        return {"ok": False, "error": f"That is not valid YAML: {exc}"}

    if not isinstance(data, dict):
        return {"ok": False, "error": "A config is a mapping of settings."}

    return {**check_config(data), "path": str(target)}


def check_config(data: dict[str, Any]) -> dict[str, Any]:
    """The same verdict, for a config that is not on disk.

    Split out of :func:`check_config_file` so a config that never had a
    path -- one the agent has just written, held in the browser -- can be
    checked and opened in the form by the same code that checks one from a
    file. The alternative was a second implementation in JavaScript of the
    mapping below, which is where the two would drift.
    """
    try:
        validate_config(data)
    except ConfigError as exc:
        return {"ok": False, "error": str(exc)}

    phases = data.get("include")
    if not isinstance(phases, list) or not phases:
        phases = [name for name in PHASE_SCHEMAS if isinstance(data.get(name), dict)]
    systems = data.get("systems") or []
    return {
        "ok": True,
        "error": None,
        "phases": [str(p) for p in phases],
        "systems": len(systems) if isinstance(systems, list) else 0,
        "settings_named": sum(
            len(v) for k, v in data.items() if isinstance(v, dict)
        ),
    }


def load_config_into_state(path: str) -> dict[str, Any]:
    """A config someone already has, in the shape the page's form works in.

    So it can be looked at, changed, and run -- rather than only run as it
    stands. What comes back is a starting position for the form, not a
    promise: the file is never written to. Anything changed is written as a
    new file, because the one on disk may be committed beside a paper, shared
    with somebody, or the record of a run that already happened.
    """
    import yaml

    from pathlib import Path

    checked = check_config_file(path)
    if not checked["ok"]:
        return checked

    data = yaml.safe_load(Path(path).expanduser().read_text(encoding="utf-8"))
    return state_from_config(data, checked)


def state_from_config(
    data: dict[str, Any],
    checked: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The same mapping, for a config that is not on disk.

    This is what the agent panel's *Load into the form* needs: it holds a
    config as a mapping, never as a path, and the button that was meant to
    do this called a global that does not exist, so it silently did
    nothing.
    """
    if checked is None:
        checked = check_config(data)
        if not checked["ok"]:
            return checked

    systems = data.get("systems") or []
    first = systems[0] if isinstance(systems, list) and systems else {}
    analysis = data.get("analysis") if isinstance(data.get("analysis"), dict) else {}

    state: dict[str, Any] = {
        "system": str(first.get("system") or "") if isinstance(first, dict) else "",
        "system_id": str(first.get("id") or "") if isinstance(first, dict) else "",
        "output": str(data.get("output") or ""),
        "include": checked["phases"],
        # A config naming a trajectory was written to analyse one; a config
        # naming only a structure was written to build one. The page needs to
        # know which so it can ask the right questions.
        "start": "trajectory" if analysis.get("trajectory") else "structure",
        "trajectory": str(analysis.get("trajectory") or ""),
        "topology": str(analysis.get("topology") or ""),
        "phases": {},
        "analyses": [],
        "analysis_options": {},
        # How the study was written. Dropped before, because only the phase
        # blocks were copied -- so opening an agent-written config in the
        # form and saving it produced a config claiming a person wrote it,
        # which is the one thing this field exists to record.
        "study": {
            key: str(data[key])
            for key in STUDY_LEVEL_KEYS
            if data.get(key) is not None
        },
    }

    # Which phases run is `include`/`exclude`, defaulting to all of them.
    # It used to be read off which phase blocks were present, so a config
    # with `simulation: {duration_ns: 2}` and no `setup:` block loaded with
    # only Simulate ticked -- and an absent `setup:` block means setup with
    # its defaults, not no setup. The two questions, "does this phase run"
    # and "does this phase have custom settings", shared one answer, which
    # is the same shape as npt_steps deciding both how long to equilibrate
    # and whether there was a barostat. A config the validator accepts and
    # `fastmdx explore` runs then arrived in the form unrunnable.
    ordered = [str(p) for p in PHASE_SCHEMAS]
    include = data.get("include")
    exclude = data.get("exclude")
    if isinstance(include, list) and include:
        running = [p for p in ordered if p in {str(x) for x in include}]
    elif isinstance(exclude, list) and exclude:
        running = [p for p in ordered if p not in {str(x) for x in exclude}]
    else:
        running = ordered
    if state["start"] == "trajectory":
        # A config that names a trajectory was written to analyse one;
        # setup and simulation have nothing to do there even if unstated.
        running = [p for p in running if p not in ("setup", "simulation")]

    for phase in running:
        block = data.get(phase)
        state["phases"][phase] = {
            key: value for key, value in block.items()
            if key not in {"options", "trajectory", "topology"}
        } if isinstance(block, dict) else {}

    included = analysis.get("include")
    if isinstance(included, str):
        state["analyses"] = [p.strip() for p in included.split(",") if p.strip()]
    elif isinstance(included, list):
        state["analyses"] = [str(p) for p in included]

    options = analysis.get("options")
    if isinstance(options, dict):
        state["analysis_options"] = {
            name: dict(values) for name, values in options.items()
            if isinstance(values, dict)
        }

    return {"ok": True, "error": None, "state": state, "summary": checked}
