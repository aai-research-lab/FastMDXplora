"""A config said as a plan: what will be built, run and measured.

The Agent's reply showed the refusals it worked through and a row of
buttons; what it had written was behind "Show the config", as YAML. A person
deciding whether to run it had to read the file to learn that it would
simulate for 2 ns because no length was given, or that the box would be a
dodecahedron. This says it in a few lines, with the values the run will
take: those the config gives, and the defaults it would otherwise take,
marked as defaults, so nothing is left to be found out after the compute has
been spent.

The lengths are resolved by the runner's own function and the umbrella
windows by the umbrella module's, so the plan cannot say something the run
would not do.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastmdxplora.config.schema import PHASE_SCHEMAS

#: The settings each line of a plan says, by its label.
_SAYS: dict[str, tuple[str, ...]] = {
    "System": ("systems",),
    "Replicas": ("sweep",),
    "Varied": ("sweep",),
    "Force field": ("setup.forcefield", "setup.force_field", "setup.water_model"),
    "Solvent": ("setup.box_shape", "setup.solvent_padding_nm", "setup.ion_concentration_M",
                "setup.ion_positive", "setup.ion_negative"),
    "Residue states": ("setup.residue_states",),
    "Membrane": ("setup.membrane",),
    "Ligand": ("setup.ligand", "setup.ligand_name"),
    "Conditions": ("simulation.temperature_K", "simulation.pressure_bar",
                   "simulation.pressure_atm"),
    "Equilibration": ("simulation.nvt_steps", "simulation.npt_steps",
                      "simulation.nvt_duration_ns", "simulation.npt_duration_ns"),
    "Production": ("simulation.duration_ns", "simulation.production_steps",
                   "simulation.timestep_fs"),
    "Sampling": ("simulation.umbrella", "simulation.metadynamics", "simulation.steered",
                 "simulation.plumed"),
    "Stops when": ("simulation.stop_when",),
    "Analyses": ("analysis.include", "analysis.exclude"),
    "Ceiling": ("budget_hours",),
}

#: Where a value came from, by the ``source`` its decision records, in the
#: order one line's settings are read: what the person asked says most.
_SOURCES = (("person", "asked"), ("fastmdx-defaults.yml", "yours"), ("agent", "agent"))


def plan_of(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Lines of ``{"label", "value", "default", "settings"}``, in the order a
    study runs; ``settings`` the dotted names a line says, so where each
    value came from can be read from the config's ``decisions``."""
    lines: list[dict[str, Any]] = []
    setup = _block(config, "setup")
    simulation = _block(config, "simulation")
    analysis = _block(config, "analysis")
    phases = _phases(config)

    def line(label: str, value: str, default: bool = False) -> None:
        lines.append({"label": label, "value": value, "default": default,
                      "settings": list(_SAYS.get(label, ()))})

    systems = [s for s in (config.get("systems") or []) if isinstance(s, dict)]
    names = [str(s.get("id") or s.get("system") or "?") for s in systems]
    if len(systems) == 1 and systems[0].get("id") and systems[0].get("system") \
            and str(systems[0]["id"]) != str(systems[0]["system"]):
        # Its name and what it is: "trpcage_1L2Y, from 1L2Y".
        names = [f"{systems[0]['id']}, from {Path(str(systems[0]['system'])).name}"]
    if names:
        line("System" if len(names) == 1 else f"{len(names)} systems",
             ", ".join(names[:6]) + (f" and {len(names) - 6} more" if len(names) > 6 else ""))
        if len(names) > 1:
            lines[-1]["settings"] = ["systems"]
    sweep = config.get("sweep") if isinstance(config.get("sweep"), dict) else {}
    for axis, values in sweep.items():
        values = values if isinstance(values, list) else [values]
        if str(axis).endswith("random_seed"):
            line("Replicas", f"{len(values)}, differing only by random seed")
        elif str(axis) == "setup.model":
            line("Replicas", f"{len(values)}, from models "
                             + ", ".join(str(v) for v in values) + " of the ensemble")
        else:
            line("Varied", f"{axis}: " + ", ".join(str(v) for v in values))

    if "setup" in phases:
        forcefield, chosen = _value(setup, "setup", "forcefield")
        if setup.get("force_field"):
            forcefield, chosen = setup["force_field"], True
        water, water_given = _value(setup, "setup", "water_model")
        text = ("chosen for the structure" if str(forcefield) == "auto"
                else str(forcefield))
        if water:
            text += f", {water} water"
        line("Force field", text, default=not (chosen or water_given))
        shape, a = _value(setup, "setup", "box_shape")
        padding, b = _value(setup, "setup", "solvent_padding_nm")
        salt, c = _value(setup, "setup", "ion_concentration_M")
        positive, _ = _value(setup, "setup", "ion_positive")
        negative, _ = _value(setup, "setup", "ion_negative")
        # A membrane's box is rectangular, built from whole bilayer patches;
        # `box_shape` is not used for one, so it is not said.
        box = "rectangular box of whole bilayer patches" if setup.get("membrane") \
            else f"{shape} box"
        line("Solvent", f"{box}, {_number(padding)} nm padding, "
                        f"{_number(salt)} M {positive}/{negative}", default=not (a or b or c))
        states = setup.get("residue_states")
        if isinstance(states, dict) and states:
            line("Residue states", ", ".join(f"{key} {value}" for key, value in states.items()))
        if setup.get("membrane"):
            membrane = setup["membrane"]
            lipid = membrane.get("lipid") if isinstance(membrane, dict) else membrane
            line("Membrane", f"{lipid} bilayer" if lipid else "a bilayer")
        # The ligand is named by its file, its residue name in the structure,
        # or both; a ligand named only by residue was not said at all.
        if setup.get("ligand") and setup.get("ligand_name"):
            line("Ligand", f"{setup['ligand']} ({setup['ligand_name']})")
        elif setup.get("ligand"):
            line("Ligand", str(setup["ligand"]))
        elif setup.get("ligand_name"):
            line("Ligand", f"{setup['ligand_name']}, as the structure holds it")

    if "simulation" in phases:
        temperature, t = _value(simulation, "simulation", "temperature_K")
        pressure, p = _value(simulation, "simulation", "pressure_bar")
        if simulation.get("pressure_atm") is not None:
            pressure, p = f"{simulation['pressure_atm']} atm", True
        else:
            pressure = f"{_number(pressure)} bar"
        line("Conditions", f"{_number(temperature)} K, {pressure}", default=not (t or p))
        timestep, dt_given = _value(simulation, "simulation", "timestep_fs")
        stages = _stages(simulation, float(timestep))
        if stages is not None:
            nvt, npt, production = stages
            given = any(simulation.get(k) is not None for k in (
                "nvt_steps", "npt_steps", "nvt_duration_ns", "npt_duration_ns"))
            line("Equilibration", f"NVT {_time(nvt)}, then NPT {_time(npt)}", default=not given)
            given = any(simulation.get(k) is not None for k in ("duration_ns", "production_steps"))
            each = " per window" if isinstance(simulation.get("umbrella"), dict) else ""
            then = ""
            if isinstance(simulation.get("stop_when"), dict) and simulation["stop_when"]:
                each, then = " first", "; then more, as the numbers ask"
            line("Production",
                 f"{_time(production)}{each}, {_number(timestep)} fs steps{then}" if production
                 else "none: the study equilibrates and stops", default=not given)
        sampling = _sampling(simulation)
        if sampling:
            line("Sampling", sampling)
        stop_when = simulation.get("stop_when")
        if isinstance(stop_when, dict) and stop_when:
            from fastmdxplora.simulation.stopping import rule_said

            line("Stops when", rule_said(stop_when))

    if "analysis" in phases:
        chosen = analysis.get("include")
        if isinstance(chosen, str):
            chosen = [part.strip() for part in chosen.split(",") if part.strip()]
        line("Analyses", ", ".join(chosen) if chosen else "the default set",
             default=not chosen)

    if "simulation" in phases:
        # What the run will be held to, from the list the report ticks, so
        # what is promised here is what is checked there.
        from fastmdxplora.report.convergence import CHECKS

        line("Checked after", "; ".join(short for _, _, short in CHECKS))

    if "report" in phases:
        line("Report", "a written report and a dashboard", default=True)

    budget = config.get("budget_hours")
    if budget is not None:
        line("Ceiling", f"{_number(budget)} hours of compute, checked after setup, "
                        "when the cost is first known")
    return lines


def _block(config: dict[str, Any], name: str) -> dict[str, Any]:
    block = config.get(name)
    return block if isinstance(block, dict) else {}


def _phases(config: dict[str, Any]) -> list[str]:
    include = config.get("include_phase")
    exclude = config.get("exclude_phase") or []
    every = ["setup", "simulation", "analysis", "report"]
    if isinstance(include, list) and include:
        return [p for p in every if p in include]
    return [p for p in every if p not in exclude]


def _value(block: dict[str, Any], phase: str, name: str) -> tuple[Any, bool]:
    """The value the run will take, and whether the config gave it."""
    if block.get(name) is not None:
        return block[name], True
    field = PHASE_SCHEMAS[phase].get(name)
    if field is None:
        return None, False
    # A phase starts some settings unset and resolves them itself (the
    # pressure, from bar or atm); the declared default is what that gives.
    value = field.phase_value
    return (field.default if value is None else value), False


def _stages(simulation: dict[str, Any], timestep_fs: float) -> tuple[float, float, float] | None:
    """NVT, NPT and production in nanoseconds, as the runner resolves them."""
    from fastmdxplora.simulation.runner import plan_stages

    if not timestep_fs > 0:
        return None
    # Every value is a number or None by here (`_float`, `_int`).
    steps = plan_stages(
        duration_ns=_float(simulation.get("duration_ns")),
        timestep_fs=timestep_fs,
        nvt_steps=_int(simulation.get("nvt_steps")),
        npt_steps=_int(simulation.get("npt_steps")),
        production_steps=_int(simulation.get("production_steps")),
        nvt_duration_ns=_float(simulation.get("nvt_duration_ns")),
        npt_duration_ns=_float(simulation.get("npt_duration_ns")),
    )
    per_ns = 1_000_000.0 / timestep_fs
    return (steps["nvt_steps"] / per_ns, steps["npt_steps"] / per_ns,
            steps["production_steps"] / per_ns)


def _sampling(simulation: dict[str, Any]) -> str:
    umbrella = simulation.get("umbrella")
    if isinstance(umbrella, dict):
        from fastmdxplora.simulation.umbrella import plan_windows

        try:
            plan = plan_windows(umbrella)
        except Exception:  # noqa: BLE001 - the validator has said why already
            return f"umbrella sampling along {umbrella.get('collective_variable', '?')}"
        centres = [w.centre for w in plan.windows]
        return (f"umbrella sampling, {len(centres)} windows along "
                f"{plan.collective_variable} from {min(centres):g} to {max(centres):g}")
    for key, name in (("metadynamics", "metadynamics"), ("steered", "steered pulling")):
        block = simulation.get(key)
        if isinstance(block, dict):
            variable = block.get("collective_variable")
            return f"{name} along {variable}" if variable else name
    if simulation.get("plumed"):
        return "a PLUMED bias of your own"
    return ""


def _time(ns: float) -> str:
    """A length in the unit it reads best in: 500 ps, 1 ns, 2.5 microseconds."""
    if ns == 0:
        return "none"
    if ns < 1:
        return f"{ns * 1000:g} ps"
    if ns >= 1000:
        return f"{ns / 1000:g} µs"
    return f"{ns:g} ns"


def _number(value: Any) -> str:
    """300.0 as 300, 0.15 as 0.15."""
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def _float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def sourced(lines: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    """The plan's lines, each with where its value came from, read from the
    config's ``decisions``: ``asked`` (the person's request said it),
    ``yours`` (their fastmdx-defaults.yml), ``agent`` (the Agent chose it,
    with ``why``), or ``default`` (FastMDXplora's own, nothing said it).
    A value set with no decision recorded is left unsaid rather than
    guessed."""
    decisions = config.get("decisions") if isinstance(config.get("decisions"), dict) else {}
    said: list[dict[str, Any]] = []
    for line in lines:
        line = dict(line)
        recorded = [decisions[name] for name in line.get("settings") or ()
                    if isinstance(decisions.get(name), dict)]
        for source, called in _SOURCES:
            chosen = [d for d in recorded if d.get("source") == source]
            if chosen:
                line["source"] = called
                why = str(chosen[0].get("why") or "").strip()
                if why and called != "asked":
                    line["why"] = why
                break
        else:
            if line.get("default"):
                line["source"] = "default"
        said.append(line)
    return said


#: Keys a change of version does not show: who wrote it and why.
_NOT_A_CHANGE = ("agent", "agent_model", "decisions", "paper")
_MISSING = object()
_LABELLED = {"systems": "System", "analysis.include": "Analyses",
             "analysis.exclude": "Analyses left out"}


def changes_between(before: Any, after: dict[str, Any]) -> list[dict[str, str]]:
    """What changed from one version of a study to the next, each setting
    as the page names it: ``{"setting", "label", "before", "after"}``,
    in the order the new version holds them."""
    if not isinstance(before, dict):
        return []
    old, new = _flat(before), _flat(after)
    changes = []
    for key in [*new, *(k for k in old if k not in new)]:
        if key.split(".")[0] in _NOT_A_CHANGE:
            continue
        was, now = old.get(key, _MISSING), new.get(key, _MISSING)
        if _plain(was) == _plain(now):
            # The same, once what is unset inside is left out (an empty
            # override on a system: third and fourth reviews, 10-07).
            continue
        from fastmdxplora.gui.schema_payload import label_and_unit

        name = key.rsplit(".", 1)[-1]
        label, unit = label_and_unit(name)
        if key in _LABELLED:
            label, unit = _LABELLED[key], ""
        elif key.startswith("sweep."):
            label = f"Varied {label.lower()}"
        if _unset(_plain(was)) and _unset(_plain(now)):
            # Unset either way (missing, None, an empty block): not a change.
            continue
        before, after = _said_both(was, now, unit)
        if before == after:
            continue
        changes.append({"setting": key, "label": label, "before": before, "after": after})
    return changes


def _flat(config: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in config.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict) and value and name.count(".") < 2:
            flat.update(_flat(value, name + "."))
        else:
            flat[name] = value
    return flat


def _unset(value: Any) -> bool:
    return value is _MISSING or value is None or value == {}


def _plain(value: Any) -> Any:
    """The value with what is unset inside it left out, at any depth."""
    if isinstance(value, dict):
        kept = {k: _plain(v) for k, v in value.items()}
        return {k: v for k, v in kept.items() if not _unset(v)}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _own(system: dict[str, Any]) -> Any:
    """A system's own settings: what it says beside its name and file."""
    return _plain({k: v for k, v in system.items() if k not in ("id", "system")})


def _is_system(value: Any) -> bool:
    return isinstance(value, dict) and ("system" in value or "id" in value)


def _system_name(value: dict[str, Any]) -> str:
    return str(value.get("id") or value.get("system"))


def _paired(was: list[Any], now: list[Any]) -> list[tuple[int, int]]:
    """Each system of one version with its own in the next, as places in
    the two lists: by its name (or its file, unnamed), the first with the
    first of that name, and the rest in the order they come. A system with
    no other is added or removed."""
    def keyed(values: list[Any]) -> dict[tuple[str, int], int]:
        seen: dict[str, int] = {}
        out = {}
        for at, v in enumerate(values):
            if _is_system(v):
                name = _system_name(v)
                seen[name] = seen.get(name, 0) + 1
                out[(name, seen[name])] = at
        return out

    olds, news = keyed(was), keyed(now)
    pairs = [(olds[k], news[k]) for k in olds if k in news]
    rest = zip([at for k, at in olds.items() if k not in news],
               [at for k, at in news.items() if k not in olds])
    return pairs + list(rest)


def _said_both(was: Any, now: Any, unit: str) -> tuple[str, str]:
    """A setting before and after, in brief, and in full where the brief
    reads the same yet the values differ: a system's own settings, or its
    file in another folder (second review, 10-07). Judged system by system,
    so one is said in full beside another's rename (fourth review, 10-07);
    and every system of a name whose count changed, so which one went is
    seen (eighth review, 10-08). A list of other settings (restraints, the
    variables of a bias) is said as any value is."""
    if (isinstance(was, list) and isinstance(now, list)
            and any(_is_system(v) for v in [*was, *now])):
        full_was: set[int] = set()
        full_now: set[int] = set()
        for i, j in _paired(was, now):
            old, new = was[i], now[j]
            if (_own(old) != _own(new)
                    or (_system_said(old) == _system_said(new) and _plain(old) != _plain(new))):
                full_was.add(i)
                full_now.add(j)

        def counted(values: list[Any]) -> dict[str, int]:
            names: dict[str, int] = {}
            for v in values:
                if _is_system(v):
                    names[_system_name(v)] = names.get(_system_name(v), 0) + 1
            return names

        olds, news = counted(was), counted(now)
        changed = {n for n in {*olds, *news} if olds.get(n, 0) != news.get(n, 0)
                   and olds.get(n) and news.get(n)}
        full_was.update(i for i, v in enumerate(was) if _is_system(v)
                        and _system_name(v) in changed)
        full_now.update(j for j, v in enumerate(now) if _is_system(v)
                        and _system_name(v) in changed)

        def said(values: list[Any], full: set[int]) -> str:
            return ", ".join(_system_said(v, whole=at in full) if _is_system(v)
                             else _said(v, "") for at, v in enumerate(values)) or "none"

        return said(was, full_was), said(now, full_now)
    before, after = _said(was, unit), _said(now, unit)
    if before == after:
        before, after = _said(was, unit, whole=True), _said(now, unit, whole=True)
    return before, after


def _said(value: Any, unit: str, *, whole: bool = False) -> str:
    if _unset(value):
        return "not set"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return f"{_number(value)} {unit}".strip()
    if isinstance(value, list):
        return ", ".join(_system_said(v, whole=whole) if _is_system(v)
                         else _said(v, "", whole=whole) if isinstance(v, dict) else str(v)
                         for v in value) or "none"
    if isinstance(value, dict):
        import json

        return json.dumps(value, separators=(", ", ": "), default=str, sort_keys=whole)
    return f"{value} {unit}".strip() if unit else str(value)


def _system_said(system: dict[str, Any], *, whole: bool = False) -> str:
    """A system by its name and what it is: "protein (1UAO)", so a structure
    changed under the same name is seen. In ``whole``, the file's full path
    and the system's own settings too."""
    named, what = system.get("id"), system.get("system")
    if named and what and str(named) != str(what):
        said = f"{named} ({what if whole else Path(str(what)).name})"
    else:
        said = str(named or what or system)
    if whole:
        import json

        own = _own(system)
        if own:
            said += " " + json.dumps(own, separators=(", ", ": "), default=str,
                                     sort_keys=True)
    return said
