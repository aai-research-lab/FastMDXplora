"""How long each stage runs when a config does not say: a time, not a step count.

The defaults were step counts: 250,000 steps of NVT, 500,000 of NPT and
1,000,000 of production. At the default 2 fs timestep those are 500 ps, 1 ns
and 2 ns, which is what the docs, the methods text and the Agent all said;
at the 4 fs hydrogen mass repartitioning allows they were 1 ns, 2 ns and
4 ns, and a study said to equilibrate for 1.5 ns equilibrated for 3. They
are times now, and the steps follow from the timestep.

Every reader of a stage's length takes it from here: the runner, the cost
estimate, the ensemble a config implies and the continuation of a stopped
study each kept a copy, and the continuation's was already a time while the
runner's was a step count.

It also reads them back: each stage's length from a study's records, with
where it came from (:func:`recorded_stage_lengths`), and a variable-step
run's production by its own clock (:func:`production_clock`).
"""

from __future__ import annotations

DEFAULT_TIMESTEP_FS = 2.0
DEFAULT_NVT_NS = 0.5
DEFAULT_NPT_NS = 1.0
DEFAULT_PRODUCTION_NS = 2.0


def steps_in(ns: float, timestep_fs: float) -> int:
    """The steps that cover `ns` nanoseconds at `timestep_fs`, counted as the
    runner has always counted a stated duration."""
    steps_per_ns = int(round(1_000_000.0 / float(timestep_fs)))
    return int(round(float(ns) * steps_per_ns))


#: Where a stage's length came from, as :func:`stage_lengths` says it: given
#: in nanoseconds by the study, worked out from the run's step count and its
#: timestep, or in nothing the record holds.
SET = "set"
FROM_STEPS = "from steps and timestep"
NOT_RECORDED = "not recorded"
#: A record a read held to the study could not open: it may say, unread.
NOT_READ = "not read"

#: Each stage, its length as a setting, and its step count.
STAGES = (("nvt", "nvt_duration_ns", "nvt_steps"),
          ("npt", "npt_duration_ns", "npt_steps"),
          ("production", "duration_ns", "production_steps"))


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if value == value and value not in (float("inf"), float("-inf")) else None


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return int(value)


def _variable_step(parameters: dict) -> bool:
    """Whether the run's integrator chose its own step
    (`runner.VARIABLE_STEP_INTEGRATORS`; the runner's own names where a
    caller has put another module in its place)."""
    try:
        from fastmdxplora.simulation.runner import VARIABLE_STEP_INTEGRATORS
    except ImportError:
        VARIABLE_STEP_INTEGRATORS = ("variable_langevin", "variable_verlet")
    return parameters.get("integrator") in VARIABLE_STEP_INTEGRATORS


def stage_lengths(record: dict) -> dict:
    """Each stage's length as a run's simulation record gives it, and from what.

    ``record`` is ``simulation/simulation_parameters.json`` as read. Each of
    ``nvt``, ``npt`` and ``production`` is ``{"ns", "steps", "source"}``:
    ``source`` is "set" where the study gave the length in ns, "from steps
    and timestep" where it is the run's step count (its resolved plan, else
    the steps the study gave) times the timestep it ran at, and "not
    recorded" where the record holds neither. A length not recorded is None,
    never 0 and never the default it would have been: a record without the
    plan may come from a release whose defaults were other ones. A stage
    set to 0 is 0, set.

    Where the length given in ns and the steps the run ran disagree (only a
    direct call of the runner can give both), the steps are what ran: the
    length is theirs, "from steps and timestep", and ``said`` says so. A
    variable-step integrator chose its own step, so no stage has a length
    in time ("not recorded", its steps given, ``said`` saying why).

    Beside them: ``timestep_fs``, ``production_ran_ns`` (what production
    ran, ``duration_ns_actual``, None for a variable step) and
    ``frames_saved`` (the production frames written), each None where not
    recorded.
    """
    record = record if isinstance(record, dict) else {}
    parameters = record.get("parameters")
    parameters = parameters if isinstance(parameters, dict) else {}
    resolved = record.get("resolved")
    resolved = resolved if isinstance(resolved, dict) else {}
    timestep = _number(parameters.get("timestep_fs"))
    if timestep is not None and timestep <= 0:
        timestep = None
    own_step = _variable_step(parameters)
    said: dict = {"timestep_fs": timestep}
    for stage, ns_key, steps_key in STAGES:
        given = _number(parameters.get(ns_key))
        ran = _count(resolved.get(steps_key))
        steps = ran if ran is not None else _count(parameters.get(steps_key))
        if own_step:
            if steps is None and given is not None and given >= 0 and timestep is not None:
                # The runner's plan: the steps that length would be at the
                # timestep (`runner.plan_stages`), each of whatever size.
                steps = steps_in(given, timestep)
            said[stage] = {"ns": None, "steps": steps, "source": NOT_RECORDED,
                           "said": f"{parameters.get('integrator')} chose its own "
                                   "step, so the stage's length in time is not "
                                   "recorded"}
        elif (given is not None and given >= 0 and ran is not None
              and timestep is not None and ran != steps_in(given, timestep)):
            length = round(ran * timestep / 1_000_000.0, 9)
            said[stage] = {"ns": length, "steps": ran, "source": FROM_STEPS,
                           "said": f"{ns_key} was given as {given:g} ns and the run "
                                   f"ran {ran:,} steps, {length:g} ns at "
                                   f"{timestep:g} fs: the steps are what ran"}
        elif given is not None and given >= 0:
            if steps is None and timestep is not None:
                steps = steps_in(given, timestep)
            said[stage] = {"ns": given, "steps": steps, "source": SET}
        elif steps is not None and timestep is not None:
            said[stage] = {"ns": round(steps * timestep / 1_000_000.0, 9),
                           "steps": steps, "source": FROM_STEPS}
        else:
            said[stage] = {"ns": None, "steps": steps, "source": NOT_RECORDED}
    ran_ns = _number(record.get("duration_ns_actual"))
    said["production_ran_ns"] = (ran_ns if ran_ns is not None and ran_ns >= 0
                                 and not own_step else None)
    said["frames_saved"] = _count(record.get("n_production_frames"))
    return said


#: Where ``production_ran_ns`` and ``frames_saved`` came from, as
#: :func:`recorded_stage_lengths` says it.
RECORDED = "recorded"
SUMMED = "summed over pieces"
#: A variable-step run's production, by its integrator's own clock.
FROM_CLOCK = "from the run's clock"


def _state_time(folder: object) -> tuple[float, int | None] | None:
    """The final state's time (ps) and step count, None where it is not
    there."""
    import re

    from fastmdxplora import record_access

    try:
        head = record_access.read_text(folder / "state_final.xml", encoding="utf-8")[:4096]
    except (OSError, ValueError):
        return None
    tag = re.search(r"<State\b[^>]*>", head)
    time = re.search(r'\btime="([0-9.eE+-]+)"', tag.group(0)) if tag else None
    if not time:
        return None
    count = re.search(r'\bstepCount="(\d+)"', tag.group(0))
    return float(time.group(1)), int(count.group(1)) if count else None


def _energy_time_at(folder: object, step: int) -> float | None:
    """The time (ps) the energy log wrote at production step ``step``, None
    where it wrote no row there."""
    import csv
    import io

    from fastmdxplora import record_access

    try:
        rows = list(csv.reader(io.StringIO(record_access.read_text(
            folder / "energy.csv", encoding="utf-8"))))
        header = [cell.strip().strip('#"') for cell in rows[0]]
        at, column = header.index("Step"), header.index("Time (ps)")
    except (OSError, ValueError, IndexError):
        return None
    for row in rows[1:]:
        try:
            if int(float(row[at])) == step:
                return float(row[column])
        except (ValueError, IndexError):
            continue
    return None


def _refused(path: object) -> bool:
    """Whether a read held to the study refuses ``path``: a record linked
    out of it, which `record_access.is_file` and its readers take for an
    absence."""
    from fastmdxplora import record_access

    try:
        record_access.stat(path)
    except PermissionError:
        return True
    except OSError:
        return False
    return False


def _piece_clock(folder: object) -> tuple[float | None, str]:
    """One piece's production by its own clock (ps), and from what.

    A piece that finished: its final state's time. A piece that stopped:
    its time at its last checkpoint, which is where the piece after it
    continued from (the sidecar's ``step``, read in its energy log or its
    state). Its energy log runs on to wherever it was stopped, and those
    steps were run again by the next piece, so the log's last line is never
    taken. None, with why, where the time at that step is not there.
    Raises `OutsideWorkspace` where a read held to the study refuses the
    piece or one of these records: unread, it is not said to be absent.
    """
    from fastmdxplora.refusals import OutsideWorkspace
    from fastmdxplora.simulation.runner import read_checkpoint_sidecar

    for path in (folder, folder / "checkpoint.chk.json", folder / "state_final.xml",
                 folder / "energy.csv"):
        if _refused(path):
            raise OutsideWorkspace(f"{path} is linked out of the study, so it is not read.",
                                   path=str(path))
    side = read_checkpoint_sidecar(folder / "checkpoint.chk") or {}
    state = _state_time(folder)
    finished = side.get("finished")
    if not isinstance(finished, bool):
        # A sidecar from before it said so: only a clean finish writes the
        # final state (`joining._finished`).
        finished = state is not None
    step = (_count(side.get("step"))
            if str(side.get("stage") or "").lower() == "production" else None)
    if finished and state is not None:
        return state[0], "its final state"
    if step is None:
        return None, ("it stopped before its first production checkpoint" if not finished
                      else "it has no final state and no checkpoint record")
    where = "its end" if finished else "its last checkpoint"
    if state is not None and state[1] == step:
        return state[0], f"its state at step {step:,}, {where}"
    time = _energy_time_at(folder, step)
    if time is not None:
        return time, f"its energy log at step {step:,}, {where}"
    return None, f"its energy log has no row at step {step:,}, {where}"


def _segments(root: object) -> list:
    """The pieces a study was carried on in (``segment-NNN``), in the order
    they ran; one set aside under another name is not one."""
    import re

    from fastmdxplora import record_access

    try:
        return sorted((f for f in record_access.glob(root, "segment-*")
                       if re.fullmatch(r"segment-\d+", f.name)),
                      key=lambda f: int(f.name[8:]))
    except OSError:
        return []


def production_clock(study: object) -> dict:
    """A study's production by its runs' own clocks.

    ``ps`` (None where any piece's is not recorded or not read),
    ``pieces`` (the study's own simulation, then each ``segment-NNN`` it
    was carried on in), ``source`` ("read", "not recorded" or "not read"),
    ``said`` (why, where ``ps`` is None) and ``stopped`` (whether the last
    piece stopped before its end, so ``ps`` runs to its last checkpoint).
    """
    from pathlib import Path

    from fastmdxplora.refusals import OutsideWorkspace

    root = Path(str(study))
    pieces = _segments(root)
    total, how = 0.0, ""
    for folder in [root, *pieces]:
        name = "the study's own simulation" if folder == root else folder.name
        try:
            clock, how = _piece_clock(folder / "simulation")
        except OutsideWorkspace:
            return {"ps": None, "pieces": 1 + len(pieces), "source": NOT_READ,
                    "said": f"the clock of {name} is not read: linked out of the study",
                    "stopped": False}
        if clock is None:
            return {"ps": None, "pieces": 1 + len(pieces), "source": NOT_RECORDED,
                    "said": f"the clock of {name} is not recorded: {how}",
                    "stopped": False}
        total += clock
    return {"ps": total, "pieces": 1 + len(pieces), "source": "read", "said": None,
            "stopped": how.endswith("its last checkpoint")}


def production_clock_ps(study: object) -> tuple[float, int] | None:
    """:func:`production_clock` as ``(ps, pieces)``, None where any piece's
    is not recorded. For a variable-step run, whose steps times its timestep
    is not its length; the methods and :func:`recorded_stage_lengths` both
    read it."""
    clock = production_clock(study)
    return None if clock["ps"] is None else (clock["ps"], clock["pieces"])


#: The study's simulation record, and the records read where it was never
#: written, best first: a study whose first piece was stopped before its end
#: has none of its own.
OWN_RECORD = "simulation/simulation_parameters.json"
CONFIG_RECORD = "resolved_config.yml"
STATUS_RECORD = "simulation/live_status.json"
LOG_RECORD = "simulation/simulation.log"
SIDECAR_RECORD = "simulation/checkpoint.chk.json"
MINIMIZED_RECORD = "simulation/state_minimized.xml"

#: What `read_from` names a record for.
READ_FROM = ("integrator", "timestep_fs", "nvt", "npt", "production", "pressure",
             "minimize")


def own_read_from(record: dict) -> dict:
    """`read_from` for a study's own simulation record: the record named for
    each value it holds, None for one it does not."""
    parameters = record.get("parameters") if isinstance(record, dict) else None
    parameters = parameters if isinstance(parameters, dict) else {}
    resolved = record.get("resolved") if isinstance(record, dict) else None
    resolved = resolved if isinstance(resolved, dict) else {}
    holds = {"integrator": parameters.get("integrator") is not None,
             "timestep_fs": _number(parameters.get("timestep_fs")) is not None,
             "pressure": (_number(record.get("pressure_bar_used")) is not None
                          or _number(parameters.get("pressure_bar")) is not None),
             "minimize": isinstance(parameters.get("minimize"), bool)}
    for stage, ns_key, steps_key in STAGES:
        holds[stage] = (_number(parameters.get(ns_key)) is not None
                        or _count(resolved.get(steps_key)) is not None
                        or _count(parameters.get(steps_key)) is not None)
    return {key: OWN_RECORD if holds[key] else None for key in READ_FROM}


def _inside(root: object, named: object) -> bool:
    """Whether a path a config names lies in the study (by name: nothing is
    opened to tell)."""
    import os
    from pathlib import Path

    path = Path(str(named)).expanduser()
    path = Path(os.path.normpath(path if path.is_absolute() else Path(str(root)) / path))
    base = Path(os.path.normpath(os.path.abspath(str(root))))
    return path == base or base in path.parents


def _planned_steps(root: object) -> tuple[dict, str | None]:
    """The steps the first piece planned for each stage, as its own live
    status or its log says them, and which record."""
    import json
    import re

    from fastmdxplora import record_access

    try:
        status = json.loads(record_access.read_text(root / STATUS_RECORD, encoding="utf-8"))
    except (OSError, ValueError):
        status = None
    if isinstance(status, dict):
        planned = {f"{stage}_steps": _count(status.get(f"{stage}_steps_planned"))
                   for stage, _, _ in STAGES}
        if any(value is not None for value in planned.values()):
            return planned, STATUS_RECORD
    try:
        log = record_access.read_text(root / LOG_RECORD, encoding="utf-8")
    except (OSError, ValueError):
        return {}, None
    planned = {}
    for key, title in (("nvt_steps", "NVT equilibration"), ("npt_steps", "NPT equilibration"),
                       ("production_steps", "Production")):
        found = re.search(rf"^{title}: ([0-9][0-9,]*) steps$", log, re.M)
        planned[key] = int(found.group(1).replace(",", "")) if found else None
    return (planned, LOG_RECORD) if any(v is not None for v in planned.values()) else ({}, None)


def unwritten_record(root: object) -> tuple[dict, dict]:
    """A record for a study carried on in pieces whose own simulation
    record was never written (its first piece was stopped before its end),
    and where each part came from: ``read_from``, a record's path in the
    study for each of :data:`READ_FROM`, or None.

    Read, best first:

    - ``resolved_config.yml``, only where it is the study's own: a
      ``simulation.resume_from`` naming a checkpoint in the study is a
      piece's config, which `resume._extend_study` puts in its place while
      the joined trajectory is analysed (no equilibration, the production
      that piece ran);
    - for each stage the config leaves unset, the steps the first piece
      planned, in its ``simulation/live_status.json``, else as its
      ``simulation.log`` says them;
    - the integrator, the timestep and the pressure the barostat held, where
      those leave them unset, from the first piece's record that holds them
      (``segment-NNN/simulation/...``), the timestep then from the study's
      checkpoint record; and whether it was minimized, where the config is
      not read, from its minimized state.

    The stage lengths are never read from a piece's record, which holds the
    plan of that piece, not the study's. Empty for a study not carried on in
    pieces, whose simulation may never have run: its config is what was
    asked, not what ran.
    """
    import json

    import yaml

    from fastmdxplora import record_access

    read_from: dict = dict.fromkeys(READ_FROM)
    pieces = _segments(root)
    if not pieces:
        return {}, read_from
    parameters: dict = {}
    beside: dict = {}
    try:
        config = yaml.safe_load(record_access.read_text(root / CONFIG_RECORD,
                                                        encoding="utf-8")) or {}
        simulation = config.get("simulation") if isinstance(config, dict) else None
        if isinstance(simulation, dict) and not (
                simulation.get("resume_from") and _inside(root, simulation["resume_from"])):
            parameters = dict(simulation)
    except (OSError, ValueError, yaml.YAMLError):
        pass
    config_read = bool(parameters)
    for key, names in (("integrator", ("integrator",)), ("timestep_fs", ("timestep_fs",)),
                       ("pressure", ("pressure_bar",)), ("minimize", ("minimize",))):
        if any(parameters.get(name) is not None for name in names):
            read_from[key] = CONFIG_RECORD
    resolved: dict = {}
    for stage, ns_key, steps_key in STAGES:
        if parameters.get(ns_key) is not None or parameters.get(steps_key) is not None:
            read_from[stage] = CONFIG_RECORD
    unset = [stage for stage, _, _ in STAGES if read_from[stage] is None]
    if unset:
        planned, where = _planned_steps(root)
        for stage in unset:
            if planned.get(f"{stage}_steps") is not None:
                resolved[f"{stage}_steps"] = planned[f"{stage}_steps"]
                read_from[stage] = where
    for folder in pieces:
        try:
            piece = json.loads(record_access.read_text(
                folder / OWN_RECORD, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        given = piece.get("parameters") if isinstance(piece, dict) else None
        if not isinstance(given, dict):
            continue
        for key in ("integrator", "timestep_fs"):
            if read_from[key] is None and given.get(key) is not None:
                parameters[key] = given[key]
                read_from[key] = f"{folder.name}/{OWN_RECORD}"
        # The pressure the barostat held in it, where the config left it
        # unset: the piece ran from the study's own settings.
        if read_from["pressure"] is None and _number(
                piece.get("pressure_bar_used")) is not None:
            beside = {key: piece[key] for key in ("pressure_bar_used", "barostat")
                      if piece.get(key) is not None}
            read_from["pressure"] = f"{folder.name}/{OWN_RECORD}"
        break
    if read_from["timestep_fs"] is None:
        from fastmdxplora.simulation.runner import read_checkpoint_sidecar

        side = read_checkpoint_sidecar(root / "simulation" / "checkpoint.chk") or {}
        if _number(side.get("timestep_fs")) is not None:
            parameters["timestep_fs"] = side["timestep_fs"]
            read_from["timestep_fs"] = SIDECAR_RECORD
    if not config_read:
        # The runner writes the minimized state only where it minimized, and
        # a piece carried on from a checkpoint is past it.
        parameters["minimize"] = record_access.is_file(root / MINIMIZED_RECORD)
        read_from["minimize"] = MINIMIZED_RECORD
    if not any(read_from[key] for key in READ_FROM if key != "minimize"):
        return {}, dict.fromkeys(READ_FROM)
    return ({"parameters": parameters, **beside, **({"resolved": resolved} if resolved else {})},
            read_from)


def last_piece_stopped(root: object) -> bool:
    """Whether a study carried on in pieces has its last piece stopped
    before its end: no final state, and no sidecar saying it finished."""
    from fastmdxplora import record_access
    from fastmdxplora.simulation.runner import read_checkpoint_sidecar

    pieces = _segments(root)
    if not pieces:
        return False
    folder = pieces[-1] / "simulation"
    finished = (read_checkpoint_sidecar(folder / "checkpoint.chk") or {}).get("finished")
    if isinstance(finished, bool):
        return not finished
    return not record_access.is_file(folder / "state_final.xml")


def _held() -> bool:
    import os
    from pathlib import Path

    from fastmdxplora import record_access

    return not record_access.can_read(Path(os.path.abspath(os.sep)))


def recorded_stage_lengths(study: object) -> dict:
    """A study's stage lengths from its own records, each with its source.

    What :func:`stage_lengths` says of the study's simulation record, and:

    - ``record``: "read", "not recorded" where there is no simulation
      record, or "not read" where a read held to the study could not open
      it, every stage then said the same;
    - ``read_from``: the record each of :data:`READ_FROM` was read from,
      None where no record holds it. A study carried on in pieces whose
      first piece was stopped before writing its record is read from its
      own ``resolved_config.yml``, the first piece's planned steps, its
      first piece's record and its checkpoint's (:func:`unwritten_record`),
      each stage's ``said`` naming the record;
    - ``pieces``, ``production_ran_source`` and ``frames_saved_source``: a
      study extended in pieces (`segment-*`) ran its production across
      them, so ``production_ran_ns`` is their sum
      (`resume.extended_production`, "summed over pieces") and
      ``frames_saved`` the joined trajectory's frames, None where they were
      never joined; ``production`` stays the first piece's plan, and says
      so, except a variable-step run's, which is the run's clock summed
      over the pieces (:func:`production_clock`: a stopped piece at its
      last checkpoint, where the next continued, said so of a last piece),
      with ``steps`` None and the plan beside it as ``first_piece_steps``
      (the whole study's, read from its config, as ``planned_steps``), or
      "not recorded", with why, where a piece's clock at that step is not
      there, "not read" where a piece is linked out of the study; a fixed
      step's summed production whose last piece stopped says so in
      ``production_ran_said``;
    - ``frames_kept``: for every mean an analysis recorded (its own, and
      those held one level down, as `analysis.base` collects them), the
      frames it analysed, those discarded as equilibration, and those left
      (``{"frames", "unit", "discarded", "kept"}``, None where not recorded), keyed
      by the analysis and, below its own, the mean's place in its findings;
      an analysis record that could not be read is there with "source":
      "not read".

    Called on its own, the reads are held to the study, so a record linked
    out of it is "not read"; inside a held read, that one stands.
    """
    from pathlib import Path

    from fastmdxplora import record_access

    root = Path(str(study))
    if _held():
        return _recorded(root)
    with record_access.confined_records(root, lambda _path: True):
        return _recorded(root)


def _recorded(root: object) -> dict:
    import json

    from fastmdxplora import record_access
    from fastmdxplora.refusals import OutsideWorkspace

    path = root / "simulation" / "simulation_parameters.json"
    state, record = "read", {}
    try:
        record = json.loads(record_access.read_text(path, encoding="utf-8"))
    except OutsideWorkspace:
        state = NOT_READ
    except (OSError, ValueError):
        state = NOT_RECORDED
    if not isinstance(record, dict):
        state, record = NOT_RECORDED, {}
    read_from = own_read_from(record) if state == "read" else dict.fromkeys(READ_FROM)
    if state == NOT_RECORDED:
        # Its first piece was stopped before writing it: the study's config,
        # then a later piece's record and the checkpoint's, say what it ran.
        try:
            record, read_from = unwritten_record(root)
        except OutsideWorkspace:
            record = {}
    said = stage_lengths(record)
    said["record"] = state
    said["read_from"] = read_from
    unwritten = state == NOT_RECORDED and bool(record)
    if state == NOT_READ:
        for stage, _, _ in STAGES:
            said[stage]["source"] = NOT_READ
    elif unwritten:
        for stage, _, _ in STAGES:
            if read_from[stage]:
                said[stage]["said"] = (
                    f"from {read_from[stage]}: the study's simulation record was not "
                    "written, its first piece stopped before writing it"
                    + ("; the study's whole production, as its config gives it"
                       if stage == "production" and read_from[stage] == CONFIG_RECORD
                       else "")
                    + (f"; {said[stage]['said']}" if said[stage].get("said") else ""))

    said["pieces"] = 1
    said["production_ran_source"] = (RECORDED if said["production_ran_ns"] is not None
                                     else NOT_READ if state == NOT_READ else NOT_RECORDED)
    said["frames_saved_source"] = (RECORDED if said["frames_saved"] is not None
                                   else NOT_READ if state == NOT_READ else NOT_RECORDED)
    refused = False
    try:
        from fastmdxplora.simulation.resume import extended_production

        extended = extended_production(root)
    except OutsideWorkspace:
        extended, refused = None, True
        said["production_ran_ns"], said["production_ran_source"] = None, NOT_READ
        said["frames_saved"], said["frames_saved_source"] = None, NOT_READ
    # The plan the production's steps are: the first piece's, from the
    # study's own record or the first piece's status; the whole study's,
    # from its config; none, where nothing gave them.
    production_from = read_from.get("production")
    plan_of, plan_is = (
        ("first_piece_steps", "the first piece's plan") if state == "read" else
        ("planned_steps", "the whole study's plan, as its config gives it")
        if production_from == CONFIG_RECORD else
        ("first_piece_steps", f"the first piece's plan, as its {production_from} gives it")
        if production_from else (None, None))
    stopped = last_piece_stopped(root)
    if extended:
        total, pieces = extended
        said["pieces"] = pieces
        said["production_ran_ns"] = (None if _variable_step(record.get("parameters") or {})
                                     else round(float(total), 9))
        said["production_ran_source"] = (SUMMED if said["production_ran_ns"] is not None
                                         else NOT_RECORDED)
        if not unwritten:
            said["production"] = {**said["production"],
                                  "said": "the first piece's plan; the study ran in "
                                          f"{pieces} pieces"
                                          + (f"; {said['production']['said']}"
                                             if said["production"].get("said") else "")}
        else:
            said["production"] = {**said["production"],
                                  "said": f"the study ran in {pieces} pieces"
                                          + (f"; {said['production']['said']}"
                                             if said["production"].get("said") else "")}
        if stopped:
            said["production_ran_said"] = ("the last piece stopped before its end: its "
                                           "production to its last checkpoint")
        frames = None
        try:
            joined = json.loads(record_access.read_text(
                root / "joined" / "joined.json", encoding="utf-8"))
            frames = _count(joined.get("frames")) if isinstance(joined, dict) else None
            source = SUMMED if frames is not None else NOT_RECORDED
        except OutsideWorkspace:
            source = NOT_READ
        except (OSError, ValueError):
            source = "not recorded: the pieces were not joined"
        said["frames_saved"], said["frames_saved_source"] = frames, source

    if _variable_step(record.get("parameters") or {}):
        # Its own clock, as the methods give it: steps times the timestep is
        # not a variable step's length.
        clock = production_clock(root)
        summed = clock["pieces"] > 1
        # Summed over pieces, the time is the whole run's and the steps
        # only a plan: not given side by side as one.
        # Stopped, the steps are the plan, not what ran, too.
        split = summed or clock["stopped"]
        steps = ({"steps": None, **({plan_of: said["production"].get("steps")}
                                    if plan_of else {})} if split else {})
        plan = f"; {plan_of} is {plan_is}" if split and plan_of else ""
        if clock["ps"] is not None:
            ns = round(clock["ps"] / 1000.0, 9)
            said["production"] = {**said["production"], "ns": ns, "source": FROM_CLOCK,
                                  **steps,
                                  "said": "The integrator chose its own step, so its "
                                          "length is the run's own clock"
                                  + (f", summed over {clock['pieces']} pieces" if summed
                                     else "")
                                  + (f"; {'the last piece' if summed else 'it'} stopped "
                                     "before its end, so it is its production to its "
                                     "last checkpoint" if clock["stopped"] else "")
                                  + plan + "."}
            said["production_ran_ns"], said["production_ran_source"] = ns, FROM_CLOCK
        else:
            source = NOT_READ if clock["source"] == NOT_READ or refused else NOT_RECORDED
            said["production"] = {**said["production"], "ns": None, "source": source,
                                  **steps,
                                  "said": "The integrator chose its own step, and "
                                          + (clock["said"] if clock["source"] == source
                                             else "a piece is linked out of the study, so "
                                                  "it is not read")
                                          + plan + "."}
            said["production_ran_ns"], said["production_ran_source"] = None, source
        said["pieces"] = clock["pieces"]

    said["frames_kept"] = _frames_kept(root)
    return said


def _frames_kept(root: object) -> dict:
    """Each recorded mean's frames, discarded and kept, by where it is.

    Each is ``{"frames", "unit", "discarded", "kept"}``: ``unit`` is
    "frames", or "samples" for a quantity held in a group
    (thermodynamics'), which counts the group's samples. Each analysis folder is listed and its
    record tried, so one a read held to the study refuses is named "not
    read" rather than left out.
    """
    import json
    import os

    from fastmdxplora import record_access

    def counted(mean: dict, samples: object = None) -> dict:
        frames = _count(mean.get("n_frames"))
        unit = "frames"
        if frames is None and _count(samples) is not None:
            frames, unit = _count(samples), "samples"
        discarded = _count(mean.get("discard"))
        both = frames is not None and discarded is not None and discarded <= frames
        return {"frames": frames, "unit": unit, "discarded": discarded if both else None,
                "kept": frames - discarded if both else None}

    kept: dict = {}
    try:
        record_access.stat(root / "analysis")
    except PermissionError:
        # The analysis folder itself is linked out of the study: nothing in
        # it is listed, let alone read.
        return {"analysis": {"frames": None, "unit": "frames", "discarded": None,
                             "kept": None, "source": NOT_READ}}
    except OSError:
        return kept
    try:
        names = sorted(entry.name for entry in os.scandir(root / "analysis")
                       if not entry.name.startswith(".") and entry.is_dir())
    except OSError:
        names = []
    for name in names:
        found = root / "analysis" / name / "options.json"
        try:
            document = json.loads(record_access.read_text(found, encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            # Refused by a read held to the study (`OutsideWorkspace`), or
            # not a record that reads.
            kept[name] = {"frames": None, "unit": "frames", "discarded": None,
                          "kept": None, "source": NOT_READ}
            continue
        findings = document.get("findings") if isinstance(document, dict) else None
        if not isinstance(findings, dict):
            continue
        for key, value in findings.items():
            if key == "solute_image" or not isinstance(value, dict):
                continue
            if "n_frames" in value and "mean" in value:
                kept[name if key == "mean" else f"{name}.{key}"] = counted(value)
                continue
            place = name if key == name else f"{name}.{key}"
            for inner_key, inner in value.items():
                if isinstance(inner, dict) and "mean" in inner and (
                        "units" in inner or "discard" in inner):
                    kept[f"{place}.{inner_key}"] = counted(inner, value.get("samples"))
    return kept
