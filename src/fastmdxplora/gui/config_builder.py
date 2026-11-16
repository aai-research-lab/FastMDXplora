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

__all__ = ["build_config", "config_yaml", "render_config"]


def _defaults_for(phase: str) -> dict[str, Any]:
    return {f.name: f.default for f in PHASE_SCHEMAS[phase].fields}


def _yours_for(state: dict[str, Any], defaults: Any) -> dict[str, dict[str, Any]]:
    """Your defaults by phase, as a run of this state would be given them:
    none for a continuation, no setup for a study prepared from another, and
    none for a setting the study sweeps (`with_defaults`' own rules)."""
    values = getattr(defaults, "values", None)
    if not values:
        return {}
    simulation = state.get("simulation") if isinstance(state.get("simulation"), dict) else {}
    if simulation.get("resume_from"):
        return {}
    rows = state.get("sweep")
    if isinstance(rows, dict):
        swept = {str(axis) for axis in rows}
    else:
        swept = {str(row.get("axis")) for row in rows or () if isinstance(row, dict)}
    return {phase: {key: value for key, value in given.items()
                    if f"{phase}.{key}" not in swept}
            for phase, given in values.items()
            if phase in PHASE_SCHEMAS and not (phase == "setup" and simulation.get("setup_from"))}


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


def build_config(state: dict[str, Any], *, full: bool = False,
                 defaults: Any = None, _yours_written: bool = True) -> dict[str, Any]:
    """A config dict from the page's state.

    ``state`` holds a block per phase, plus the top-level keys the run needs
    (``system``, ``output``, ``include``). Unknown phases and unknown fields
    are dropped rather than passed through: the page should not be able to
    write a file the command line will refuse to read.

    With ``full``, every setting is named at whatever value it would take,
    default or chosen. Without it, only what differs from the default.

    ``defaults`` is your fastmdx-defaults.yml where one applies. The file
    names what the run takes from it, as `fastmdx explore` fills it (a
    system's own value kept, a default some systems take given to those),
    each recorded in ``decisions``: a file taken where there is no defaults
    file runs the study shown here. FastMDXplora's own value chosen over
    yours is kept (it was left out, and the run took yours).
    """
    # The state arrives from the browser, not through the loader, so a
    # study written with the earlier `include` is settled before anything
    # reads it: the top-level keys are copied out before the phase list is
    # consulted, and canonicalising later left the earlier spelling
    # uncopied and every phase reading as running.
    from fastmdxplora.config.loader import canonical_phase_keys

    state = canonical_phase_keys(dict(state))
    config: dict[str, Any] = {}
    # Your defaults as the run is given them: `with_defaults` on what the
    # form chose, so a system's own value keeps that system's and a default
    # only some systems take goes to those. Read for a full config, which
    # names what the run will use; a short one names what was chosen.
    yours: dict[str, dict[str, Any]] = {}
    given_yours: dict[str, Any] = {}
    if full and _yours_for(state, defaults):
        from fastmdxplora.config.defaults_file import with_defaults

        chosen_only = build_config(state, full=False, defaults=defaults,
                                   _yours_written=False)
        given_yours, names = with_defaults(chosen_only, defaults)
        for name in names:
            phase, _, key = name.partition(".")
            block = given_yours.get(phase)
            if isinstance(block, dict) and key in block and not (
                    isinstance(chosen_only.get(phase), dict) and key in chosen_only[phase]):
                yours.setdefault(phase, {})[key] = block[key]
    # What each setting is left at when the form gives nothing: yours where
    # your defaults give it. A value chosen is left out of a short file only
    # where it is FastMDXplora's own and yours say nothing else, so a file
    # taken where no defaults file is runs the study shown here.
    left_at = _yours_for(state, defaults)

    # Who wrote the study, carried through rather than dropped. The form
    # loads `agent` and `agent_model` into `study` and then, building the
    # config back, wrote none of it -- so opening an Agent-drafted config
    # in the builder and saving it produced a file claiming a person wrote
    # it, headed "Written by the FastMDXplora GUI". That is the one thing
    # those fields exist to record.
    study = state.get("study")
    if isinstance(study, dict):
        fields = {f.name: f for f in TOP_LEVEL.fields}
        for key in STUDY_LEVEL_KEYS:
            if study.get(key) is None:
                continue
            field = fields.get(key)
            value = _coerce(study[key], field) if field is not None else study[key]
            if value is not None:
                config[key] = value

    for key in ("output", "include_phase", "exclude_phase", "verbose"):
        value = state.get(key)
        if value not in (None, "", [], {}):
            config[key] = value

    # A run names its inputs under `systems`, one entry each, so that a
    # config describing one protein and one describing forty have the same
    # shape. The page offers a single system; the file it writes is the same
    # file a batch would use, with one entry in the list.
    system = state.get("system")
    if state.get("systems"):
        # The form's list of systems, each with its name and any settings
        # for it alone. Before, the form held one structure, and a study of
        # several opened here came back as its first, its names and its
        # per-system settings gone without a word.
        config["systems"] = [entry for entry in (
            _system_entry(raw) for raw in state["systems"] if isinstance(raw, dict))
            if entry]
    elif system:
        entry: dict[str, Any] = {"system": system}
        if state.get("system_id"):
            entry["id"] = state["system_id"]
        config["systems"] = [entry]

    # A full config records what the run will use, so it names every phase the
    # run includes -- not only the ones the form happened to touch. A phase
    # left entirely alone still runs, and still uses values worth recording.
    running = set(config.get("include_phase") or PHASE_SCHEMAS.keys())

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

    # A sweep, as the page's rows give it: a setting and the values typed for
    # it, read by the rule the command line's --sweep uses.
    rows = state.get("sweep")
    if rows:
        from fastmdxplora.batch.sweep import normalize_sweep, values_from_text

        pairs = rows.items() if isinstance(rows, dict) else (
            (row.get("axis"), row.get("values")) for row in rows if isinstance(row, dict))
        axes: dict[str, Any] = {}
        for axis, values in pairs:
            if not axis or values in (None, "", []):
                continue
            axes[str(axis)] = list(values) if isinstance(values, list) else values_from_text(values)
        if axes:
            config["sweep"] = normalize_sweep(axes)

    for phase, group in PHASE_SCHEMAS.items():
        block = state.get(phase)
        if not isinstance(block, dict):
            if not (full and phase in running):
                continue
            block = {}
        own = _defaults_for(phase)
        # The default a setting has here: yours where the run takes yours,
        # else FastMDXplora's own.
        defaults_here = {**own, **yours.get(phase, {})}
        fields = {f.name: f for f in group.fields}
        kept: dict[str, Any] = {}
        chosen: set[str] = set()
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
            if not full and value == own.get(name) and value == left_at.get(
                    phase, {}).get(name, value):
                continue
            kept[name] = value
            chosen.add(name)
        if full:
            # What the run would use under the page's choices. A setting
            # nobody touched still appears, at the value it will take; one
            # whose other spelling is chosen, here or by a system, is left to
            # it (a default `pressure_bar` beside a chosen `pressure_atm` is
            # the pressure the run would take).
            from fastmdxplora.config.loader import same_setting

            swept = set(config.get("sweep") or ())
            spelled = {f"{phase}.{name}" for name in chosen} | {
                f"{phase}.{key}" for entry in config.get("systems") or ()
                if isinstance(entry, dict) and isinstance(entry.get(phase), dict)
                for key in entry[phase]} | swept
            for name, value in defaults_here.items():
                dotted = f"{phase}.{name}"
                # A swept setting, or one whose other spelling is swept, is
                # the sweep's: a written `pressure_bar` beside a swept
                # `pressure_atm` was refused as both given.
                if value is None or name in chosen or dotted in swept or (
                        (same_setting(dotted) - {dotted}) & spelled):
                    continue
                kept[name] = value
        if kept:
            config[phase] = kept

    if given_yours:
        # The systems as your defaults leave them (a default some systems
        # take is in their blocks), and the reasons as the run records them,
        # so a full config that names yours says where they came from.
        if isinstance(given_yours.get("systems"), list):
            config["systems"] = given_yours["systems"]
        if isinstance(given_yours.get("decisions"), dict):
            config["decisions"] = given_yours["decisions"]

    if not full and _yours_written and _yours_for(state, defaults):
        # Your defaults written in, as the run will be given them: Download
        # config and Copy the command left them to a file that is not taken
        # with the study, and the study ran at 300 K elsewhere.
        from fastmdxplora.config.defaults_file import with_defaults

        config, _ = with_defaults(config, defaults)

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


def _system_entry(raw: dict[str, Any]) -> dict[str, Any] | None:
    """One row of the form's systems as the config writes it: the
    structure, its name where given, and its own settings, each brought in
    from text as the study's settings are."""
    structure = str(raw.get("system") or "").strip()
    if not structure:
        return None
    entry: dict[str, Any] = {"system": structure}
    name = str(raw.get("id") or "").strip()
    if name:
        entry["id"] = name
    for phase, group in PHASE_SCHEMAS.items():
        block = raw.get(phase)
        if not isinstance(block, dict):
            continue
        fields = {f.name: f for f in group.fields}
        kept = {}
        for key, value in block.items():
            field = fields.get(key)
            coerced = _coerce(value, field) if field is not None else value
            if coerced is not None:
                kept[key] = coerced
        if kept:
            entry[phase] = kept
    # Anything else a row carries (a key a later release adds) is passed on
    # for the validator to judge, not dropped here.
    for key, value in raw.items():
        if key not in entry and key not in ("system", "id", *PHASE_SCHEMAS):
            entry[key] = value
    return entry


def config_yaml(state: dict[str, Any], *, full: bool = False,
                defaults: Any = None) -> dict[str, Any]:
    """The config as text, from the builder's form state. A sweep row that
    cannot be read is said under the form rather than failing the request.
    ``defaults``: your fastmdx-defaults.yml that applies where the study
    will run, for :func:`build_config` and the plan."""
    from fastmdxplora.batch.sweep import SweepError

    from fastmdxplora.config.loader import ConfigError

    try:
        config = build_config(state, full=full, defaults=defaults)
        short = build_config(state, full=False, defaults=defaults) if full else None
    except (SweepError, ConfigError) as exc:
        # A row that cannot be read, or the phase lists given in both
        # spellings and differing: said under the form, not a failed request.
        return {"ok": False, "error": str(exc), "yaml": "", **refused(exc)}
    answer = render_config(config, full=full, short=short, defaults=defaults)
    if answer.get("ok"):
        from fastmdxplora.gui.applicable import not_applicable

        # Which analyses this study leaves nothing for, and why, from its
        # config alone; the preview adds what the structure holds.
        answer["not_applicable"] = not_applicable(config)
    return answer


def refused(exc: BaseException) -> dict[str, Any]:
    """A refusal as the form reads it: its record, and what would fix it,
    naming the setting so the form can say it on that setting's field."""
    from fastmdxplora.refusals import refusal_of
    from fastmdxplora.remedies import remedy_for

    found = refusal_of(exc)
    try:
        remedy = remedy_for(found, where="the config").as_record()
    except Exception:  # noqa: BLE001 - the refusal stands without its fix
        remedy = None
    return {"refusal": found.as_dict(), "remedy": remedy}


def export_config(config: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    """Export an existing Config without losing choices in inactive phases.

    The Builder's form describes only the phases currently running. An
    Agent version is a whole Config: its other phase settings and decision
    records must survive a download too. Full exports use the Builder's
    default filling, with every original choice laid back over it.
    """
    from copy import deepcopy

    chosen = deepcopy(config)
    try:
        validate_config(deepcopy(chosen))
    except ConfigError as exc:
        return {"ok": False, "error": str(exc), **refused(exc)}
    filled = {}
    if full:
        running = phases_it_runs(chosen)
        defaults = build_config({**chosen, "include_phase": running,
                                 "exclude_phase": []}, full=True)
        filled = {phase: defaults[phase] for phase in running if phase in defaults}

    def overlay(target: dict, given: dict) -> None:
        for key, value in given.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                overlay(target[key], value)
            else:
                target[key] = deepcopy(value)

    overlay(filled, chosen)
    return render_config(filled, full=full, short=chosen, keep_choices=True)


def _names_yours(config: dict[str, Any]) -> bool:
    """Whether the file holds a value your defaults gave, said by the reason
    recorded for it."""
    from fastmdxplora.config.defaults_file import DEFAULTS_FILE

    said = config.get("decisions")
    return isinstance(said, dict) and any(
        isinstance(one, dict) and one.get("source") == DEFAULTS_FILE
        for one in said.values())


def _without_yours_reasons(config: dict[str, Any]) -> dict[str, Any]:
    """The study for its command: the values your defaults gave stay, their
    reasons are left to the file, so the command stays one a person reads
    (it carried a `--decisions` argument of JSON for each)."""
    from fastmdxplora.config.defaults_file import DEFAULTS_FILE

    said = config.get("decisions")
    if not isinstance(said, dict):
        return config
    kept = {name: one for name, one in said.items()
            if not (isinstance(one, dict) and one.get("source") == DEFAULTS_FILE)}
    trimmed = {key: value for key, value in config.items() if key != "decisions"}
    if kept:
        trimmed["decisions"] = kept
    return trimmed


def render_config(config: dict[str, Any], *, full: bool = False,
                  short: dict[str, Any] | None = None,
                  keep_choices: bool = False,
                  defaults: Any = None) -> dict[str, Any]:
    """The config as text, validated, from a config -- the one source.

    Everything that renders a config to YAML goes through here: the form,
    via config_yaml after build_config; the Agent, directly, because it
    already has a config. It used to have to fake form state to reach the
    launch, and the fake and the real disagreed about where phase settings
    lived, so every Agent-launched run ran with defaults. One source of
    truth means the Agent's config is the config that launches, not a
    translation of it.

    Returns ``ok`` false and the validator's own message where the settings
    would be refused, so the failure arrives here rather than on the cluster
    an hour later.

    With ``defaults`` (your fastmdx-defaults.yml), the plan says the values
    the run will take with them filled in, marked as yours, and a study
    they would make refused is refused here, naming the file.
    """
    import copy

    import yaml

    def refusal(exc: ConfigError) -> dict[str, Any]:
        return {
            "ok": False,
            "error": str(exc),
            **refused(exc),
            "yaml": None,
            "settings_changed": sum(
                len(v) for k, v in config.items() if isinstance(v, dict)
            ),
        }

    try:
        validate_config(config)
    except ConfigError as exc:
        return refusal(exc)

    # The config knows who wrote it: `agent` is set when the Agent did. A
    # file that says "the GUI" on a study the Agent drafted misattributes
    # the one thing that field exists to record.
    author = ("the FastMDXplora Agent" if config.get("agent")
              else "the FastMDXplora GUI")
    header = (
        f"# Written by {author}.\n"
        "#\n"
        + (
            "# Every setting is named here at the value the run will use,\n"
            "# defaults included, so this file says what the run did without\n"
            "# depending on which version of the software was current.\n"
            if full
            else
            "# The settings chosen in this Config are kept, including\n"
            "# those for phases not currently selected. Ask for the full\n"
            "# config to record every value the run will use.\n"
            if keep_choices
            else
            ("# Only settings that differ from FastMDXplora's defaults appear\n"
             "# here, with what your fastmdx-defaults.yml gives written in, so\n"
             "# the file runs the same study where that file is not. Ask for\n"
             "# the full config to record every value the run will use.\n")
            if _names_yours(config)
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
    # The command in the header is derived from the short form -- a
    # command's defaults come free by omission. The form's path supplies
    # it; the Agent's config already is the short form.
    decided = short if (full and short is not None) else config
    # What the run will be given: the decided settings with your defaults
    # filling what they leave unset, as `fastmdx explore` fills them.
    with_yours = decided
    if defaults is not None:
        from fastmdxplora.config.defaults_file import refused_with, with_defaults

        with_yours, _ = with_defaults(copy.deepcopy(decided), defaults)
        caused = refused_with(decided, with_yours, defaults)
        if caused is not None:
            return refusal(caused)
    try:
        command = cli_command(_without_yours_reasons(decided))
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
    from fastmdxplora.gui.plan import plan_of, sourced

    try:
        plan = plan_of(with_yours)
        if with_yours is not decided:
            # Each value your defaults give marked as yours.
            plan = sourced(plan, with_yours)
    except Exception:  # noqa: BLE001 - the plan is said beside the file, never instead of it
        plan = []
    return {
        "ok": True,
        "error": None,
        # The study said in lines, as the Agent's plan says it, defaults
        # marked: what the form's review shows.
        "plan": plan,
        "command": command,
        "script": python_script(decided),
        "yaml": header + body,
        "settings_changed": sum(
            len(v) for k, v in config.items() if isinstance(v, dict)
        ),
    }


def check_config_file(path: str, *, to_run: bool = False,
                      defaults: Any = None, base: Any = None) -> dict[str, Any]:
    """Read a config someone already has and say whether it will run.

    The point of writing a config is that it goes elsewhere -- edited by hand,
    committed beside a paper, carried to a cluster. It should be possible to
    bring one back and be told whether it still works before queueing an hour
    of compute behind it.

    Reports the phases it names, so somebody can see at a glance whether the
    file does what they remember it doing.

    ``to_run``: judged as `fastmdx explore --config` judges it, systems
    required and the stopping rule checked against them, and with your
    defaults (``defaults``) filled in. Without it, as a file to open in the
    form, which may have no systems yet. ``base``: the folder the run starts
    in, which a relative structure path is read from.
    """
    import yaml

    from pathlib import Path

    if not str(path or "").strip():
        # Not the folder `Path("")` is, which was said as "That is a
        # folder, not a config: .".
        return {"ok": False, "error": "No config was named."}
    target = Path(path).expanduser()
    if not target.exists():
        return {"ok": False, "error": f"No such file: {target}"}
    if target.is_dir():
        return {"ok": False, "error": f"That is a folder, not a config: {target}"}

    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": f"Could not read it: {exc}"}
    except UnicodeDecodeError:
        return {"ok": False, "error": "That is not a text file: a config is YAML, "
                                      "written in UTF-8."}

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        # A syntax error, which is the most common thing to come back with
        # after somebody has edited a file by hand.
        return {"ok": False, "error": f"That is not valid YAML: {exc}"}
    except RecursionError:
        return {"ok": False, "error": "That YAML is nested too deeply to be a config."}

    if not isinstance(data, dict):
        return {"ok": False, "error": "A config is a mapping of settings."}

    return {**check_config(data, to_run=to_run, defaults=defaults, base=base),
            "path": str(target)}


def _lists_as_lists(data: dict[str, Any]) -> dict[str, Any]:
    """The analysis lists as lists. A bare name, or names separated by
    commas, is what the config file accepts for them; the form checked the
    raw text and refused it, so a study the command line opens failed to
    open here."""
    analysis = data.get("analysis")
    if isinstance(analysis, dict):
        analysis = dict(analysis)
        for key in ("include", "exclude"):
            if isinstance(analysis.get(key), str):
                analysis[key] = [part.strip() for part in analysis[key].split(",") if part.strip()]
        data["analysis"] = analysis
    return data


def phases_it_runs(data: dict[str, Any], *, in_the_form: bool = False) -> list[str]:
    """The phases a config runs, as `fastmdx explore` plans them: its
    `include_phase`, else all but its `exclude_phase`, else all. The form,
    which asks a config naming a trajectory to analyse one, leaves setup and
    simulation out of it (`in_the_form`); a study's own resolved config
    names its trajectory too, and runs all four."""
    ordered = [str(p) for p in PHASE_SCHEMAS]
    include = data.get("include_phase")
    exclude = data.get("exclude_phase")
    if isinstance(include, list) and include:
        running = [p for p in ordered if p in {str(x) for x in include}]
    elif isinstance(exclude, list) and exclude:
        running = [p for p in ordered if p not in {str(x) for x in exclude}]
    else:
        running = ordered
    analysis = data.get("analysis") if isinstance(data.get("analysis"), dict) else {}
    if in_the_form and analysis.get("trajectory"):
        # A config that names a trajectory was written to analyse one;
        # setup and simulation have nothing to do there even if unstated.
        running = [p for p in running if p not in ("setup", "simulation")]
    return running


def check_config(data: dict[str, Any], *, to_run: bool = False,
                 defaults: Any = None, base: Any = None) -> dict[str, Any]:
    """The same verdict, for a config that is not on disk.

    Split out of :func:`check_config_file` so a config that never had a
    path -- one the agent has just written, held in the browser -- can be
    checked and opened in the form by the same code that checks one from a
    file. The alternative was a second implementation in JavaScript of the
    mapping below, which is where the two would drift.

    ``to_run``, ``defaults`` and ``base`` as :func:`check_config_file`
    takes them: a config with no systems said "Runs." here and was refused
    by the run. Judged to run, what can be seen wrong before anything is
    built is refused too, as `fastmdx explore` refuses it: a system setup
    cannot read, a selection that does not parse.
    """
    import copy

    # Validated in place: the validator settles the older spellings into
    # the current ones as it goes, and the summary below reads the settled
    # form. The analysis lists are given their shape first.
    data.update(_lists_as_lists(dict(data)))
    as_given = copy.deepcopy(data)
    try:
        validate_config(data, require_systems=to_run)
    except ConfigError as exc:
        said = refused(exc)
        if (said["refusal"].get("code") == "config.option.missing_companion"
                and not data.get("systems")):
            # The validator's words are written for the Agent, which takes a
            # structure from a request; a person is told what to add.
            return {"ok": False, **said,
                    "error": ("This Config names no system, so there is no "
                              "structure to run. Add one under `systems:`, "
                              "a PDB id or a structure file:\n"
                              "  systems:\n    - {system: 1L2Y}")}
        return {"ok": False, "error": str(exc), **said}
    judged = None
    if to_run and defaults is not None:
        from fastmdxplora.config.defaults_file import refused_with, with_defaults

        filled, _ = with_defaults(copy.deepcopy(as_given), defaults)
        caused = refused_with(as_given, filled, defaults, require_systems=True)
        if caused is not None:
            return {"ok": False, "error": str(caused), **refused(caused)}
        # Judged as the run is given it, your defaults filled, as
        # `fastmdx explore` and check_study judge it.
        judged = filled
    if to_run:
        from pathlib import Path

        from fastmdxplora.config.before_run import refused_before_a_run

        caught = refused_before_a_run(
            data, judged if judged is not None else data,
            defaults if judged is not None else None,
            base=Path(base) if base is not None else None)
        if caught is not None:
            return {"ok": False, "error": str(caught), **refused(caught)}

    # The phases it runs, as the form ticks them: "simulation" alone was
    # said of a config that runs all four, its blocks read for its phases.
    phases = phases_it_runs(data)
    systems = data.get("systems") or []
    # What the form says under Worth knowing, said for a file too: a 10 fs
    # timestep checked here as "Runs." and ran, to fail in NVT.
    from fastmdxplora.advisories import advise

    settings = {**(data.get("setup") if isinstance(data.get("setup"), dict) else {}),
                **(data.get("simulation") if isinstance(data.get("simulation"), dict) else {})}
    try:
        # Each with what to do about it: a summary alone said what was
        # wrong and not what would run.
        knowing = [" ".join(str(part).strip() for part in (a.summary, a.remedy) if part)
                   for a in advise({}, settings)]
    except Exception:  # noqa: BLE001 - advice, never a verdict
        knowing = []
    return {
        "ok": True,
        "error": None,
        "worth_knowing": knowing,
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
    # Read directly rather than through the validator, so a study written
    # with the earlier `include` is settled before the phases are read --
    # otherwise the form opens with every phase ticked.
    from fastmdxplora.config.loader import canonical_phase_keys

    data = _lists_as_lists(canonical_phase_keys(dict(data)))
    if checked is None:
        # Checked on a copy, as a file's is: the validator expands an
        # umbrella block into a run per window in place, and the form was
        # then filled from the first window, so an umbrella study opened
        # here (an Agent's, through its Download config or Open in the
        # builder) came back as one plain run with the umbrella gone.
        import copy

        checked = check_config(copy.deepcopy(data))
        if not checked["ok"]:
            return checked

    systems = data.get("systems") or []
    first = systems[0] if isinstance(systems, list) and systems else {}
    analysis = data.get("analysis") if isinstance(data.get("analysis"), dict) else {}

    state: dict[str, Any] = {
        # Every system, whole: its structure, its name and its own settings.
        "systems": [dict(entry) for entry in systems if isinstance(entry, dict)]
        if isinstance(systems, list) else [],
        "system": str(first.get("system") or "") if isinstance(first, dict) else "",
        "system_id": str(first.get("id") or "") if isinstance(first, dict) else "",
        "output": str(data.get("output") or ""),
        "include_phase": checked["phases"],
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
        # As stated, not as strings. `agent` and `agent_model` are strings
        # anyway; `budget_hours` is a number, and str() on it produced a
        # config the validator refused -- "should be number, got str" --
        # on the way from the Agent's Run here to the launch.
        # Every run option the form offers, not only how the study was
        # written: `explain` and `verbose` are run options too, and were
        # left at their defaults by a loaded study that set them.
        "study": {
            key: data[key]
            for key in (*STUDY_LEVEL_KEYS, "explain", "verbose")
            if data.get(key) is not None
        },
        # How the runs are scheduled, for the form's execution controls,
        # which a loaded study never filled: a study with `mode: parallel`
        # opened here and started again ran sequentially.
        "execution": dict(data["execution"]) if isinstance(data.get("execution"), dict) else {},
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
    running = phases_it_runs(data, in_the_form=True)

    for phase in running:
        block = data.get(phase)
        state["phases"][phase] = {
            key: value for key, value in block.items()
            if key not in {"options", "trajectory", "topology"}
        } if isinstance(block, dict) else {}
    # The browser ticks its boxes from `include`, and reads `phases` only
    # for the values inside each. The first fix populated `phases` alone,
    # and the form went on ticking from `checked["phases"]` -- which is
    # the blocks present, the very thing this replaces. Both fields say
    # the same thing now, from the same derivation.
    state["include_phase"] = list(running)

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

    # The sweep, one row per setting, its values written as they are typed.
    if data.get("sweep"):
        from fastmdxplora.batch.sweep import normalize_sweep, text_from_values

        state["sweep"] = [{"axis": axis, "values": text_from_values(values)}
                          for axis, values in normalize_sweep(data["sweep"]).items()]
    return {"ok": True, "error": None, "state": state, "summary": checked}
