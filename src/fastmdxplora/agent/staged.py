"""Running a study the agent wrote, with the estimate in the middle.

`--assisted` hands you a config and stops, because you are there to read
it. `--autonomous` does not, so something else has to be the thing that
stops it, and that thing is a budget.

A budget needs a number, and the number is not available when the agent
finishes writing. Cost scales with the *solvated* particle count, and that
depends on box shape, padding and ion concentration -- decisions setup
makes. A protein of 2,000 atoms is 60,000 solvated, and guessing from the
residue count would be inventing the water, which is most of the atoms.

So an autonomous run goes in two parts:

    setup                 -- cheap, minutes, and it settles the count
    estimate              -- from that count, on this machine
    simulation onwards    -- the expensive part, if the estimate fits

The estimate is for the platform the simulation phase will choose, read
from the config as that phase reads it. A machine not yet measured for that
platform, or measured under other settings, is measured on the spot, on the
study's own prepared system: a new container or a fresh GPU has never been
measured, and refusing there made the budget unusable exactly where it is
meant for. Only when that measurement cannot be made either does the study
stop for want of a number.

The gate sits where the information first exists and before the cost is
incurred. Anywhere earlier and it would be guessing; anywhere later and
there would be nothing left to stop.

Setup is not free, but it is the wrong order of magnitude to worry about:
minutes against the days the simulation takes, and it produces something
worth having even when the study is then refused -- a solvated system, a
particle count, and a reason.

What this does not do is approve on your behalf. An estimate that does not
fit the budget refuses, and the refusal names the number, because the
answer to "too expensive" is usually a shorter run rather than a bigger
allowance and a caller cannot make that choice without the figure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastmdxplora.refusals import Refusal, StudyError, refusal_of

__all__ = ["StagedRun", "particles_after_setup", "run_in_stages"]


@dataclass
class StagedRun:
    """What happened, stage by stage.

    Kept whole rather than reduced to a verdict, because a run that stopped
    after setup is a different thing from one that never started, and a
    caller reading a report tomorrow needs to know which.
    """

    output_dir: Path
    setup_done: bool = False
    particles: int | None = None
    #: How many runs the study makes; ``particles`` is the largest of them.
    runs: int = 1
    estimate_seconds: float | None = None
    simulated: bool = False
    refusal: Refusal | None = None
    notes: list[str] = field(default_factory=list)
    #: The second stage, the simulation onwards, has written its record: a
    #: run stopped before is carried on by running the study again, its
    #: setup first; one stopped after, by `fastmdx resume`.
    simulating: bool = False
    #: Asked to stop in setup's study: nothing was priced or run.
    stopped: bool = False
    #: What the second stage's explore returned (its runs' results).
    results: Any = field(default=None, repr=False)

    @property
    def stopped_at(self) -> str:
        if self.stopped:
            return "setup"
        if self.refusal is None:
            return "" if self.simulated else "nothing ran"
        return "estimate" if self.setup_done else "setup"

    def as_record(self) -> dict[str, Any]:
        return {
            "output_dir": str(self.output_dir),
            "setup_done": self.setup_done,
            "particles": self.particles,
            "runs": self.runs,
            "estimate_hours": (None if self.estimate_seconds is None
                               else self.estimate_seconds / 3600),
            "simulated": self.simulated,
            "stopped_at": self.stopped_at,
            "refusal": self.refusal.as_dict() if self.refusal else None,
        }

    def __str__(self) -> str:
        if self.stopped:
            return "stopped when asked, in setup"
        if self.refusal is not None:
            return f"stopped at {self.stopped_at}: {self.refusal.message}"
        if self.simulated and self.runs > 1:
            return f"ran, {self.runs} runs of up to {self.particles:,} particles"
        if self.simulated:
            return f"ran, {self.particles:,} particles"
        return "nothing ran"


def particles_after_setup(output_dir: Path | str) -> int | None:
    """The solvated particle count, from what setup already recorded.

    Read from ``setup/setup_parameters.json``, which is where setup writes
    ``n_atoms_solvated`` and where the report layer has been reading it
    from all along. Reading rather than recomputing: opening the system a
    second time could give a different answer from the one the study is
    documented with, and then two numbers would describe one run.

    Not the manifest, which was the first guess and wrong -- the manifest
    records artifacts, status and timing per phase, and the counts live
    with the phase's own parameters. Worth saying, because the manifest is
    the obvious place to look and it is not there.
    """
    import json

    from fastmdxplora.simulation.pipeline import setup_records_of

    # The named system's, for a study given `setup_from`: nothing was
    # prepared here, and that is the system the rest would simulate. A
    # prepared system's own directory is read as it is: an umbrella study's
    # windows share `shared_setup/setup`.
    prepared = (Path(output_dir)
                if (Path(output_dir) / "setup_parameters.json").is_file()
                else setup_records_of(output_dir))
    if prepared is None:
        return None
    record = prepared / "setup_parameters.json"
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    count = data.get("n_atoms_solvated")
    return int(count) if count else None


def run_in_stages(
    config: dict[str, Any],
    output_dir: Path | str,
    *,
    budget_hours: float | None = None,
    platform_name: str = "",
    precision: str = "",
    explore: Any = None,
    measure: Any = None,
    staged: StagedRun | None = None,
    force: bool = False,
    keep_previous: bool = False,
    again: str = "",
    again_argv: tuple[str, ...] = (),
) -> StagedRun:
    """Run setup, price the rest, and continue only if it fits.

    Parameters
    ----------
    budget_hours
        The ceiling. ``None`` runs without one, which is right for a
        caller who is watching and wrong for one who is not -- so
        `--autonomous` supplies it and refuses without it.
    platform_name, precision
        The platform and precision to price, when not the config's own
        (`simulation.platform`, default auto, and `simulation.precision`).
    explore
        Substituted in tests. Not so a caller can substitute the science:
        whatever this is, the config still goes through the validator.
    measure
        Substituted in tests: times the prepared system when the machine
        has no usable measurement (:func:`measure_prepared_system`).
    staged
        Filled in as the stages go, where given: a caller whose run was
        stopped part way reads how far it got.
    force, keep_previous
        As `fastmdx explore --force-overwrite` and `--rerun` give them, for
        both stages: a study whose setup is there already was refused,
        whatever was passed.
    again, again_argv
        What runs the study again, its setup first, as a sentence's clause
        and as the arguments after `fastmdx` (none where there is no command
        line to repeat), said in place of `fastmdx resume` by its stop
        lines and its records of a stop until the second stage begins: a
        resume there ran setup alone and read the study finished.
    """
    # The study's process record held from setup to the end: setup's run ends,
    # and the prepared system is then timed and priced in this process before
    # the rest runs; the record went with setup's run, so a GUI found nothing
    # going (`orchestrator._hold_run_process`).
    from fastmdxplora.orchestrator import _hold_run_process, _release_run_process

    import os

    from fastmdxplora.simulation.runner import RUN_AGAIN_BEFORE_ITS_PRICE

    _hold_run_process(Path(output_dir))
    before = os.environ.get(RUN_AGAIN_BEFORE_ITS_PRICE)
    try:
        return _run_in_stages(config, output_dir, budget_hours=budget_hours,
                              platform_name=platform_name, precision=precision,
                              explore=explore, measure=measure, staged=staged,
                              force=force, keep_previous=keep_previous,
                              again=again, again_argv=again_argv)
    finally:
        if before is None:
            os.environ.pop(RUN_AGAIN_BEFORE_ITS_PRICE, None)
        else:
            os.environ[RUN_AGAIN_BEFORE_ITS_PRICE] = before
        _release_run_process(Path(output_dir))


def _run_in_stages(
    config: dict[str, Any],
    output_dir: Path | str,
    *,
    budget_hours: float | None = None,
    platform_name: str = "",
    precision: str = "",
    explore: Any = None,
    measure: Any = None,
    staged: StagedRun | None = None,
    force: bool = False,
    keep_previous: bool = False,
    again: str = "",
    again_argv: tuple[str, ...] = (),
) -> StagedRun:
    import json
    import os

    from fastmdxplora.cost import estimate_runs
    from fastmdxplora.simulation import runner as _signals

    out = Path(output_dir)
    staged = staged if staged is not None else StagedRun(output_dir=out)
    runner = explore or _explore
    # Passed only where given, so an explore that takes no such argument is
    # called as it always was.
    given = {name: True for name, asked in (("force", force),
                                            ("keep_previous", keep_previous)) if asked}

    # The phases the study asks for, in whichever spelling it gave them. Each
    # stage replaces the study's phase keys with its own one list: setting
    # `include_phase` beside a study's `exclude_phase` (or the reverse) made
    # a conflict the person never wrote, refused after setup had run.
    asked = _phases_asked(config)
    setup_only = _with_phases(config, ["setup"])
    # Until the second stage writes its record, what runs the study again
    # is said in place of `fastmdx resume`, by its stop lines (in this
    # process and its pool's workers) and by its records.
    # Where the command was run from, beside it: its paths are as typed.
    os.environ[_signals.RUN_AGAIN_BEFORE_ITS_PRICE] = json.dumps(
        {"said": again or AGAIN, "argv": list(again_argv) if again else [],
         "cwd": os.getcwd() if again and again_argv else ""})
    _signals._THE_LAST_STUDY_HEARD_A_STOP = False
    try:
        ran = runner(config=setup_only, output_dir=str(out), **given)
    except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
        staged.refusal = refusal_of(exc)
        return staged
    cut = _stopped_when_asked(ran)
    staged.setup_done = not cut
    if cut or _signals._THE_LAST_STUDY_HEARD_A_STOP:
        # Asked to stop in setup's study (Ctrl-C, SIGTERM, the GUI's Stop):
        # nothing is priced or run. It was refused for want of a particle
        # count, or priced and simulated as if no stop was asked.
        staged.stopped = True
        return staged

    after_setup = [phase for phase in asked if phase != "setup"]
    if not after_setup:
        staged.notes.append(
            "The study asks for setup alone, so there is nothing after it to price.")
        return staged

    runs, simulate_from = _runs_of(config, out)
    counted = [(name, particles_after_setup(where), steps)
               for name, where, steps in runs]
    unknown = [name for name, particles, _ in counted if particles is None]
    if unknown:
        which = ("" if len(runs) == 1 else
                 f" ({', '.join(unknown[:5])}{' and more' if len(unknown) > 5 else ''})")
        staged.refusal = Refusal(
            code="setup.structure.undetermined",
            message=(
                "Setup finished without recording how many particles the "
                f"solvated system holds{which}, so there is nothing to price "
                "the rest of the study from. Running on regardless would "
                "spend an unknown amount, which is the one thing an "
                "unattended run must not do."),
        )
        return staged
    priced = [(int(particles), steps) for _, particles, steps in counted]
    staged.particles = max(particles for particles, _ in priced)
    staged.runs = len(runs)
    prepared_first = runs[0][1]

    platform_name, precision = _what_it_will_run_on(config, platform_name, precision)
    try:
        estimate = estimate_runs(
            priced, platform_name=platform_name, precision=precision)
    except StudyError as exc:
        refused = refusal_of(exc)
        if refused.code not in ("environment.calibration.absent",
                                "environment.calibration.stale"):
            staged.refusal = refused
            return staged
        # An unmeasured machine, or one measured under other settings: time
        # the prepared system here, on that platform, and price from that.
        from fastmdxplora.cost import measure_prepared_system
        from fastmdxplora.simulation.pipeline import setup_records_of

        timer = measure or measure_prepared_system
        try:
            prepared = (prepared_first
                        if (prepared_first / "setup_parameters.json").is_file()
                        else setup_records_of(prepared_first) or prepared_first / "setup")
            calibration = timer(prepared, platform_name=platform_name, precision=precision)
        except Exception as why:  # noqa: BLE001 - said, with the first refusal
            # Nothing measured means no ceiling, and no ceiling is the thing
            # the budget exists to provide.
            staged.refusal = Refusal(
                code=refused.code,
                message=(f"{refused.message} Timing the prepared system here did not "
                         f"work either ({why}), so there is still no number."),
                details=dict(refused.details))
            return staged
        staged.notes.append(
            f"Measured this machine on the prepared system: {calibration.steps:,} steps "
            f"of {calibration.particles:,} particles in {calibration.seconds:.1f} s on "
            f"{calibration.machine.get('platform')}.")
        estimate = estimate_runs(priced,
                                 platform_name=calibration.machine.get("platform", ""),
                                 precision=precision, calibration=calibration)
    staged.estimate_seconds = estimate.seconds
    staged.notes.append(str(estimate))

    if budget_hours is not None and estimate.hours > budget_hours:
        staged.refusal = Refusal(
            code="environment.budget.exhausted",
            message=(
                f"This study is estimated at {estimate.hours:.1f} GPU-hours "
                f"and the budget is {budget_hours:.1f}. Setup ran and its "
                "output is kept; the simulation did not start. A shorter "
                "run is usually the answer rather than a larger allowance, "
                "which is why the number is here."),
            details={"estimate_hours": estimate.hours,
                     "budget_hours": budget_hours,
                     "particles": staged.particles,
                     "runs": staged.runs},
        )
        return staged

    rest = _with_phases(config, after_setup)
    # `setup_from` is a simulation setting, not a top-level one. Put at the
    # top level the loader refused it and named the right place, which is
    # the guardrail working on the code that was written to use it.
    simulation = dict(rest.get("simulation") or {})
    # A system the study named stays named: the first stage prepared nothing
    # when it had one, so its own `setup/` holds nothing to simulate. A study
    # of several systems names none: each run simulates the one it prepared.
    injected = None
    if simulate_from is not None and not (
            simulation.get("setup_from") or simulation.get("prepared_from")):
        simulation["setup_from"] = injected = str(simulate_from)
    rest["simulation"] = simulation
    # From here a stop is carried on by `fastmdx resume`, once the second
    # stage's record is written.
    os.environ.pop(_signals.RUN_AGAIN_BEFORE_ITS_PRICE, None)
    try:
        staged.results = runner(config=rest, output_dir=str(out), **given)
    except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
        staged.refusal = refusal_of(exc)
        return staged
    finally:
        # Begun once its record is written: before, the study still read
        # as setup alone, which a resume reads finished. Said before the
        # records are written again, so a stop as they are is said so.
        staged.simulating = _the_second_stage_began(out, after_setup)
        # The record is of the study, not of the second stage: it was
        # written as `exclude_phase: [setup]` with the stage's `setup_from`,
        # so replaying it skipped the setup the study asked for.
        _record_the_study_s_phases(out, config, after_setup, injected)
    staged.simulated = True
    return staged


def _stopped_when_asked(ran: Any) -> bool:
    """Whether a run of the first stage ended stopped when asked."""
    from fastmdxplora.batch.explorer import _was_stopped

    try:
        return any(_was_stopped(result) for result in ran or [])
    except TypeError:  # an explore that returns no list of results
        return False


#: Said where no command line is there to repeat (`fastmdx agent`, Python),
#: naming no command: `fastmdx agent` has no `--force-overwrite`.
AGAIN = "it stopped before its simulation began, and is run again from its setup"


def _its_records(out: Path) -> list[Path]:
    """The resolved configs of the study and of its runs: not a shared
    preparation's (`shared_setup/`), nor any other folder's below it."""
    found = [out / "resolved_config.yml"]
    runs = out / "runs"
    if runs.is_dir():
        found += sorted(run / "resolved_config.yml" for run in runs.iterdir() if run.is_dir())
    return [path for path in found if path.is_file()]


def _phases_recorded(path: Path) -> list[str] | None:
    """The phases a resolved config records it runs (`include_phase`), or
    None where it cannot be read, or records an `exclude_phase`."""
    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict) or data.get("exclude_phase"):
        return None
    return list(data.get("include_phase") or [])


def _the_second_stage_began(out: Path, sent: list[str]) -> bool:
    """Whether the second stage wrote a record: one of the study's or its
    runs' resolved configs records exactly its phases."""
    return any(_phases_recorded(path) == list(sent) for path in _its_records(out))


def stopped_before_its_price(study: Path | str) -> dict[str, Any] | None:
    """What runs the study again (``said``, ``argv``), where it is a study
    with a budget stopped in its setup stage: every record of it and its
    runs says setup alone, and a run's record of its stop says what runs it
    again (`runner.run_again_before_its_price`). A resume of it runs setup
    alone and reads it finished. None for any other study."""
    import json

    root = Path(study)
    records = _its_records(root)
    if not records or any(_phases_recorded(path) != ["setup"] for path in records):
        return None
    manifests = [root / "manifest.json"]
    if (root / "runs").is_dir():
        manifests += sorted(run / "manifest.json" for run in (root / "runs").iterdir())
    for manifest in manifests:
        try:
            record = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for phase in (record.get("phases") or []) if isinstance(record, dict) else []:
            details = ((phase.get("refusal") or {}).get("details") or {}
                       if isinstance(phase, dict) else {})
            if isinstance(details, dict) and isinstance(details.get("details"), dict):
                # A stop outside production (`RunStopped.as_error`) records
                # its particulars one level down.
                details = {**details, **details["details"]}
            if isinstance(details, dict) and details.get("run_again"):
                return {"said": str(details["run_again"]),
                        "argv": [str(word) for word in details.get("run_again_argv") or []],
                        "cwd": str(details.get("run_again_cwd") or "")}
    return None


def _phases_asked(config: dict[str, Any]) -> list[str]:
    """The phases the study runs, in their order: its `include_phase` (or
    the earlier `include`), else every phase but its `exclude_phase` (or
    `exclude`), else every phase."""
    from fastmdxplora.orchestrator import PHASES

    include = config.get("include_phase", config.get("include"))
    exclude = config.get("exclude_phase", config.get("exclude"))
    if include:
        named = {str(phase) for phase in include}
        return [phase for phase in PHASES if phase in named]
    left_out = {str(phase) for phase in exclude or []}
    return [phase for phase in PHASES if phase not in left_out]


def _with_phases(config: dict[str, Any], phases: list[str]) -> dict[str, Any]:
    """The config with its phase selection replaced by ``phases`` alone."""
    staged = {key: value for key, value in config.items()
              if key not in ("include_phase", "exclude_phase", "include", "exclude")}
    staged["include_phase"] = list(phases)
    return staged


def _record_the_study_s_phases(out: Path, config: dict[str, Any],
                               sent: list[str], injected: Any) -> bool:
    """Write the study's own phase selection into each resolved config the
    second stage wrote, and take out the `setup_from` this module gave it.
    Once it wrote one, each record of the study and its runs the first
    stage wrote (setup alone) too: a run the second stage did not reach,
    stopped in an earlier run, read as a study of setup alone, and its
    resume ran nothing for it. A shared preparation's record
    (`shared_setup/`) is its own, and is left as it is.

    Only a file that records exactly one stage's phases is changed: any
    other was written by something else, and is left as it is. Each is put
    in place whole (`orchestrator._put_in_place`), so a stop as it is
    written leaves it as it was. Whether the second stage wrote any.
    """
    import yaml

    from fastmdxplora.orchestrator import _put_in_place

    include = config.get("include_phase", config.get("include"))
    exclude = config.get("exclude_phase", config.get("exclude"))
    records = []
    for path in sorted(out.rglob("resolved_config.yml")):
        try:
            text = path.read_text(encoding="utf-8")
            data = yaml.safe_load(text)
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(data, dict) and not data.get("exclude_phase"):
            records.append((path, text, data, list(data.get("include_phase") or [])))
    begun = _the_second_stage_began(out, sent)
    own = set(_its_records(out))
    for path, text, data, phases in records:
        if phases != list(sent) and not (begun and phases == ["setup"] and path in own):
            continue
        rewritten: dict[str, Any] = {}
        for key, value in data.items():
            if key == "include_phase":
                if include:
                    rewritten["include_phase"] = list(include)
                elif exclude:
                    rewritten["exclude_phase"] = list(exclude)
                continue
            rewritten[key] = value
        simulation = rewritten.get("simulation")
        if injected is not None and isinstance(simulation, dict):
            for key in ("setup_from", "prepared_from"):
                if simulation.get(key) == str(injected):
                    simulation[key] = None
        header = "".join(line for line in text.splitlines(keepends=True)
                         if line.startswith("#"))
        try:
            _put_in_place(path, header + yaml.safe_dump(rewritten, sort_keys=False,
                                                        default_flow_style=False))
        except OSError:
            continue
    return begun


def _runs_of(config: dict[str, Any], out: Path
             ) -> tuple[list[tuple[str, Path, int]], Path | None]:
    """Each run the study makes, as the batch layer expands it: its name,
    where its prepared system sits once setup has run, and the steps it will
    integrate; and the system the simulation stage is to be pointed at.

    A study of several runs was priced as one, on ``setup/`` beside the
    study, which a study of several runs does not have: setup had prepared
    each run's system under ``runs/`` (an umbrella study's once, under
    ``shared_setup/``), found no count, and refused every umbrella study,
    replica sweep and campaign whatever its budget.
    """
    import copy

    from fastmdxplora.cost import total_steps

    single = ([("the study", out, total_steps(config.get("simulation")))], out / "setup")
    try:
        from fastmdxplora.batch.sweep import expand_runs, normalize_sweep, normalize_systems
        from fastmdxplora.config import phase_options, validate_config
        from fastmdxplora.simulation.umbrella import plan_from_expanded

        data = copy.deepcopy(config)
        validate_config(data, require_systems=True)
        specs = expand_runs(
            systems=normalize_systems(data["systems"]),
            sweep=normalize_sweep(data["sweep"]) if data.get("sweep") is not None else None,
            base_options=phase_options(data))
        umbrella = plan_from_expanded(data) is not None
    except Exception:  # noqa: BLE001 - setup has already said what is wrong with it
        return single
    if len(specs) <= 1:
        return single

    def steps_of(spec: Any) -> int:
        return total_steps(spec.options.get("simulation"))

    if not umbrella:
        return [(spec.run_id, out / "runs" / spec.run_id, steps_of(spec)) for spec in specs], None

    study = data.get("simulation") or {}
    named = study.get("setup_from") or study.get("prepared_from")
    if named:
        from fastmdxplora.simulation.pipeline import where_a_prepared_system_sits

        shared = where_a_prepared_system_sits(Path(named))
        simulate_from = None
    else:
        shared = out / "shared_setup" / "setup"
        simulate_from = shared
    runs = [(spec.run_id, shared, steps_of(spec)) for spec in specs]
    first = (specs[0].options.get("simulation") or {}).get("umbrella") or {}
    if study.get("steered") and not first.get("seed_from"):
        # The pull that seeds the windows is a run of its own, in the same
        # system, as long as the study's production.
        pull = {k: v for k, v in study.items() if k not in ("umbrella", "steered")}
        runs.append(("the pull that seeds the windows", shared, total_steps(pull)))
    return runs, simulate_from


def _what_it_will_run_on(config: dict[str, Any], platform_name: str,
                         precision: str) -> tuple[str, str]:
    """The platform and precision the simulation phase would use, as it
    reads them from the config, with `auto` resolved as it resolves it.

    Pricing "unknown" against a measurement taken on CUDA refused as a
    different machine, so a budget from the command line could never pass,
    however the machine had been measured.
    """
    simulation = config.get("simulation") or {}
    precision = precision or str(simulation.get("precision") or "mixed")
    requested = platform_name or str(simulation.get("platform") or "auto")
    if requested != "auto":
        return requested, precision
    try:
        from fastmdxplora.simulation.runner import _import_openmm, select_platform

        return select_platform(_import_openmm(), requested="auto",
                               precision=precision)[2], precision
    except Exception:  # noqa: BLE001 - no OpenMM here: nothing to resolve it with
        return "", precision


def _explore(*, config: dict[str, Any], output_dir: str, **given: Any) -> Any:
    from fastmdxplora import FastMDXplora

    return FastMDXplora(config_data=config, output_dir=output_dir).explore(**given)
