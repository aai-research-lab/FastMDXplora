"""Batch orchestration — the single execution path for all runs.

Every FastMDXplora run goes through :class:`BatchExplorer`. There is no
separate "single run" path: a one-system config is simply a batch of
one. This keeps one code path, one config shape (``systems:`` is always
a list), and one mental model.

Output layout adapts to the run count:

  - **One run** → flat, familiar layout written directly to the output
    directory: ``output/setup/``, ``output/simulation/``, etc., with the
    usual ``manifest.json`` and ``resolved_config.yml``. No ``runs/``
    wrapper, no ``batch_manifest.json``.
  - **Many runs** → each run in ``output/runs/<id>/`` (a complete study),
    plus a top-level ``batch_manifest.json`` indexing them all.

Execution modes (``execution:`` block):

  - **sequential** (default) — one run at a time, in process.
  - **parallel** — a process pool of ``workers`` runs at once. Setting
    ``devices: [0, 1, ...]`` pins each worker to a distinct device
    round-robin, one run per GPU, which is the safe default where there is
    more than one card.

    ``workers`` without ``devices`` puts them all on the same GPU. This
    said that was always slower than sequential, and that is not a
    measurement -- it depends entirely on whether one run saturates the
    card, and a modestly sized solvated system on a large modern GPU does
    not. A thirty-window umbrella study of trypsin and benzamidine, three
    at a time on one RTX 4090, ran ten groups of three at about 390 ns/day
    aggregate; the claim above would have had it run one at a time. How
    many fit is a property of the system and the card, so measure it rather
    than take a number from here.

Process-based (not thread-based) parallelism is mandatory: OpenMM
contexts and the GIL don't share across threads. Each run is therefore
dispatched to a subprocess via a module-level worker function.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from concurrent.futures import (
    FIRST_COMPLETED,
    ProcessPoolExecutor,
    wait as wait_for_any,
)
from multiprocessing import get_context
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastmdxplora.batch.sweep import (
    RunSpec,
    expand_runs,
    normalize_sweep,
    normalize_systems,
)
from fastmdxplora.config import load_config_file, validate_config
from fastmdxplora.utils.logging import get_logger

if TYPE_CHECKING:
    from fastmdxplora.orchestrator import RunResult

logger = get_logger("batch")


#: How long the terminal may say nothing before it says something. A window of
#: a real study runs for hours, and every worker's output goes to its own log
#: so three of them do not interleave -- which left the terminal silent for the
#: whole run, with no way to tell a study that is working from one that has
#: hung.
HEARTBEAT_SECONDS = 60.0


def _elapsed(seconds: float) -> str:
    """A duration a person can read at a glance."""
    seconds = int(seconds)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m{seconds:02d}s"


#: How wide the bar is drawn. Narrow enough to leave room for the counts on
#: an eighty-column terminal.
_BAR_WIDTH = 28


def _progress_line(*, running: int, done: int, queued: int, total: int,
                   seconds: float) -> str:
    """What is happening, for a terminal that would otherwise show nothing.

    A bar rather than a sentence. A study of nine windows on a laptop prints
    one of these a minute for an hour, and sixty lines saying almost the same
    thing bury the ones that matter -- the windows finishing, and anything
    that went wrong. The stage runners already draw a bar for the steps
    inside a run; this is the same thing for the runs inside a study.
    """
    filled = int(round(_BAR_WIDTH * done / total)) if total else 0
    bar = "━" * filled + "─" * (_BAR_WIDTH - filled)
    percent = (100.0 * done / total) if total else 0.0
    return (f"    {bar} {percent:5.1f}%  {done}/{total} done, "
            f"{running} running, {queued} queued  {_elapsed(seconds)}")


def _is_a_terminal() -> bool:
    """Whether stdout is somewhere a redrawn line means anything."""
    import sys

    stream = sys.stdout
    if not hasattr(stream, "isatty"):
        return False
    try:
        return bool(stream.isatty())
    except Exception:  # noqa: BLE001 - a stream that cannot say is not one.
        return False


def _how_far_along(run_dirs) -> str:
    """Step progress for the runs in flight, from what they already write.

    A member of a campaign has no terminal. Its stage bars go to
    `on_step_progress`, which in a worker goes nowhere, and the runner
    deliberately keeps them out of the run log -- a file of the same line at
    one per cent intervals is noise. The consequence was that a campaign
    printed "0/3 done" for hours with no way to tell a healthy run from a
    stalled one, and the only recourse was watching a DCD grow.

    Nothing new is recorded here. `live_status.json` already carries
    `current_step` against `total_planned_steps`, written by the telemetry
    the runner starts by default. This reads it back, so the study's own
    heartbeat can say how far its members have got.

    Returns empty where nothing can be read: telemetry switched off, a run
    that has not written its first status, or a file caught mid-write. A
    heartbeat that fails is worse than a heartbeat that says less.
    """
    parts = []
    for run_id, run_dir in run_dirs:
        status_path = Path(run_dir) / "simulation" / "live_status.json"
        try:
            status = json.loads(status_path.read_text())
        except Exception:  # noqa: BLE001 -- absent, partial, or unreadable
            continue
        done = status.get("current_step")
        total = status.get("total_planned_steps")
        if not isinstance(done, int) or not isinstance(total, int) or total <= 0:
            continue
        # The short form of the run id: the axis value is what distinguishes
        # members, and the shared prefix is the same on every one of them.
        label = str(run_id).split("__")[-1]
        parts.append(f"{label} {100.0 * done / total:.0f}%")
    return ("  [" + ", ".join(parts) + "]") if parts else ""


def _clear_progress_line() -> None:
    """Wipe the bar before an event is printed over it.

    A finished window prints its own line, and without this it would land on
    top of a bar that ends with no newline.
    """
    if _is_a_terminal():
        print("\r" + " " * 78 + "\r", end="", flush=True)


def _give_this_worker_its_share(threads: int) -> None:
    """Cap the threads one parallel run may take, in the worker that runs it.

    OpenMM's CPU platform takes every core it can see, and a pool of workers
    each doing that does not divide the machine between them -- it has them
    fight over it. Six runs on eight cores oversubscribe by six times, and
    the study finishes slower than three would have.

    Set in the worker process rather than passed down through the runner,
    because it is a property of being one of several on a machine and not of
    the simulation, and because the libraries below read it once at import.
    """
    import os

    for variable in ("OPENMM_CPU_THREADS", "OMP_NUM_THREADS",
                     "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                     "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ[variable] = str(threads)


def _threads_for_each(n_workers: int) -> int:
    """The cores each worker may use, so together they fill the machine once.

    At least one: a worker with no threads runs nothing, and a study with
    more workers than cores is a scheduling choice rather than an error.
    """
    import os

    cores = os.cpu_count() or 1
    return max(1, cores // max(1, n_workers))


def _a_prepared_system_is_there(setup_dir: Path) -> bool:
    """Whether the three files a simulation starts from are on disk.

    Asked through the simulation phase's own check, so what counts as
    prepared is decided in one place. Its answer is a tuple of paths, and an
    empty answer is a tuple of Nones -- which is truthy, so it is the first
    element that has to be read.
    """
    from fastmdxplora.simulation.pipeline import _setup_outputs_present

    return _setup_outputs_present(setup_dir)[0] is not None


def _first_error_phase_message(phases: list[Any]) -> str:
    for phase in phases:
        if getattr(phase, "status", None) == "error":
            return getattr(phase, "message", "") or f"Phase '{phase.name}' failed."
    return ""


#: Umbrella's own settings, which the collective-variable layer does not take.
_UMBRELLA_ONLY = frozenset({
    "centres", "centers", "centre", "from", "to", "n_windows",
    "force_constant", "minimum_overlap", "minimum_samples",
    "index",
})


def _resolve_umbrella_selections(spec: dict[str, Any], topology: Any) -> None:
    """Resolve an umbrella block's coordinate, and check its windows.

    Two checks, because they catch different things. `plan_windows` reads the
    positions and the force constant. The selections go through the shared
    collective-variable layer, which is where a selection matching no atoms
    is caught -- and that layer belongs to metadynamics, so it asks for a
    `sigma` an umbrella study has no use for. Steered MD had the same problem
    and answered it by supplying one and ignoring it; the same answer serves
    here rather than a second copy of the resolver.
    """
    from fastmdxplora.simulation.metadynamics import plan_from_config
    from fastmdxplora.simulation.umbrella import plan_windows

    # The windows, only where the study's own spec is still intact. By the
    # time this runs the block has been expanded: each window carries the
    # single `centre` it sits at, and `from`, `to` and `n_windows` are gone.
    # Asking the study's planner to validate a window's spec reported `from`
    # as missing from a config that had it -- a refusal for the absence of a
    # key that expansion had consumed on purpose.
    if any(key in spec for key in ("centres", "centers", "from", "n_windows")):
        plan_windows(spec)

    coordinate = {k: v for k, v in spec.items() if k not in _UMBRELLA_ONLY}
    coordinate.setdefault("sigma", 0.05)
    coordinate.setdefault("unbounded", True)
    plan_from_config(coordinate, topology)


def _check_selections_against(prepared: Path, spec: dict[str, Any]) -> None:
    """Resolve a biasing block's selections before any window is launched.

    A selection is checked when a window starts, which is after the shared
    system has been built and a run submitted. `resid 3 and name CA` on a
    tripeptide -- MDTraj counts residues from zero, so there is no resid 3 --
    cost a full preparation and a failed run to discover, and in a parallel
    study it failed three windows at once. The topology is on disk by then
    and the selections are in the config, so nothing about it needed a
    simulation to find out.

    Silent where it cannot tell: a selection this cannot resolve is not
    thereby wrong, and refusing on that basis would be worse than the wait.
    """
    # Each method's own planner, not one for both. `plan_from_config` is
    # metadynamics', and it requires `sigma` -- the width of a hill. An
    # umbrella study has no hills and no sigma, so checking one through that
    # planner refused a valid config for lacking a setting the method does
    # not have. The selection-resolving code is shared between the methods;
    # the validation around it is not.
    planners = []
    if isinstance(spec.get("umbrella"), dict):
        umbrella = dict(spec["umbrella"])
        planners.append(
            lambda: _resolve_umbrella_selections(umbrella, _mdtop()))
    if isinstance(spec.get("metadynamics"), dict):
        from fastmdxplora.simulation.metadynamics import plan_from_config

        metadynamics = dict(spec["metadynamics"])
        planners.append(lambda: plan_from_config(metadynamics, _mdtop()))
    if isinstance(spec.get("steered"), dict):
        from fastmdxplora.simulation.steered import plan_steered

        steered = dict(spec["steered"])
        planners.append(lambda: plan_steered(steered, _mdtop()))
    if not planners:
        return
    topology = prepared / "topology.pdb"
    if not topology.is_file():
        return

    def _mdtop():
        import mdtraj as md

        return md.load(str(topology)).topology

    try:
        for planner in planners:
            planner()
    except ValueError as exc:
        raise ValueError(
            f"{exc}\n\nFound before any window ran, by resolving the "
            "selection against the prepared system. Nothing has been "
            "simulated."
        ) from exc
    except Exception:  # noqa: BLE001 - anything else is not a verdict
        # Could not resolve it here, which is not the same as it being wrong.
        return


def _why_it_failed(result: Any) -> str:
    """The reason a run stopped, ready to print.

    The message was written into the manifest and never to the screen, so a
    study that stopped said only which run had failed. Three separate causes
    -- a selection matching no atoms, a box collapsing under NPT, a directory
    already holding output -- each looked identical from the terminal, and
    each needed the manifest opened to tell apart. A run's own log has the
    detail; the reason it stopped belongs where the reader is looking.
    """
    message = str(getattr(result, "message", "") or "").strip()
    if not message:
        return "No reason was recorded. The run's own log may say more."
    return message


def _skipped_run_result(spec: RunSpec, run_out: Path, message: str) -> "RunResult":
    from fastmdxplora.orchestrator import RunResult

    return RunResult(
        run_id=spec.run_id,
        system=spec.system,
        status="skipped",
        output_dir=run_out,
        sweep_values=spec.sweep_values,
        phases=[],
        message=message,
    )


# ---------------------------------------------------------------------------
# Module-level worker (must be top-level so ProcessPoolExecutor can pickle it)
# ---------------------------------------------------------------------------
@contextmanager
def _nothing_to_silence():
    """A context manager that lets the output through."""
    yield


def _execute_run(
    spec_dict: dict[str, Any],
    run_out: str,
    include: list[str] | None,
    exclude: list[str] | None,
    verbose: bool,
    device_override: str | None,
    quiet: bool = True,
    force: bool = False,
) -> "RunResult":
    """Run one study and return a RunResult. Safe to call in a subprocess.

    Takes plain dicts/strings (picklable) rather than RunSpec objects so it
    works cleanly across the process boundary. Returns a RunResult, which
    is a dataclass and pickles back cleanly.
    """
    # Spawned workers (Windows uses 'spawn') do not pass through the CLI
    # entry point, so their stdout/stderr are not reconfigured to UTF-8 and
    # default to the platform codec (cp1252 on Windows). The presenter prints
    # non-ASCII status glyphs (✓, ▸, box-drawing), which then raise
    # UnicodeEncodeError and fail the run. Reconfigure here, as main() does.
    import sys as _sys

    for _stream in (_sys.stdout, _sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    # Several workers share one terminal, so none of them should print a
    # banner to it. Three copies of the wordmark arriving a character at a
    # time is not three runs starting -- it is one unreadable screen. Each
    # run writes its own log file, and the batch prints the progress that
    # belongs to the whole study.
    # Restored when the run ends, because this function is called in-process
    # by the tests as well as in a subprocess by a real run, and a worker
    # that leaves a global changed poisons everything after it.
    import os as _os
    import sys as _sys

    class _QuietBanner:
        """Keep a worker's output out of the shared terminal.

        Two sources. Ours obeys an environment variable. PLUMED's comes from
        C++ and writes to the file descriptor directly, so redirecting
        Python's ``sys.stdout`` does not reach it -- the same lesson as a
        Markdown renderer that printed its installation guide from a dynamic
        loader. The descriptor itself has to move.

        It moves to the run's own log, so nothing is lost: a worker's output
        belongs beside its results rather than mixed with two others'.
        """

        def __init__(self, log_path):
            self.log_path = log_path

        def __enter__(self):
            self.previous = _os.environ.get("FASTMDX_LOG_STYLE")
            _os.environ["FASTMDX_LOG_STYLE"] = "plain"

            self.saved = None
            try:
                _os.makedirs(_os.path.dirname(self.log_path), exist_ok=True)
                self.sink = open(self.log_path, "a", encoding="utf-8")
                self.saved = (_os.dup(1), _os.dup(2))
                _os.dup2(self.sink.fileno(), 1)
                _os.dup2(self.sink.fileno(), 2)
            except OSError:
                # A worker that cannot redirect should still run.
                self.saved = None
            return self

        def __exit__(self, *_exc):
            if self.saved is not None:
                # Flush while the descriptors are still moved. PLUMED's
                # writes sit in the C runtime's buffer until something
                # empties it, and whatever is still there when the
                # descriptors go back lands in the shared terminal -- the
                # leak this guard exists to prevent, arriving late. The
                # trajectory loader learned the same thing from MDTraj's
                # plugin messages on macOS.
                from fastmdxplora.utils.native_output import _LIBC

                for stream in (_sys.stdout, _sys.stderr):
                    try:
                        stream.flush()
                    except (ValueError, OSError):
                        pass
                if _LIBC is not None:
                    try:
                        _LIBC.fflush(None)  # fflush(NULL): every open stream
                    except OSError:
                        pass

                out, err = self.saved
                _os.dup2(out, 1)
                _os.dup2(err, 2)
                _os.close(out)
                _os.close(err)
                self.sink.close()
            if self.previous is None:
                _os.environ.pop("FASTMDX_LOG_STYLE", None)
            else:
                _os.environ["FASTMDX_LOG_STYLE"] = self.previous
            return False

    # Imported here so the subprocess sets up its own logging/imports.
    from fastmdxplora import FastMDXplora
    from fastmdxplora.orchestrator import RunResult

    options = dict(spec_dict["options"])
    # Round-robin GPU pinning: stamp this worker's device onto the run.
    if device_override is not None:
        sim = dict(options.get("simulation", {}))
        sim["device_index"] = device_override
        options["simulation"] = sim

    try:
        # Redirected only where output would collide. The guard exists so
        # three parallel workers do not interleave three PLUMED banners into
        # one unreadable screen; with a single run there is nothing to
        # interleave with, and redirecting it left a config-driven study of
        # one system printing a line and then nothing for half an hour.
        with _QuietBanner(_os.path.join(run_out, "run.log")) if quiet \
                else _nothing_to_silence():
            fmdx = FastMDXplora(
                system=spec_dict["system"],
                output_dir=run_out,
                options=options,
                verbose=verbose,
            )
            # A single-system explore() returns a one-element list of
            # RunResult; take its phases and re-stamp the run's identity.
            inner = fmdx.explore(
                include=include, exclude=exclude, report=True, force=force
            )
        phases = inner[0].phases if inner else []
        status = "error" if any(p.status == "error" for p in phases) else "ok"
        message = _first_error_phase_message(phases) if status == "error" else ""
        return RunResult(
            run_id=spec_dict["run_id"],
            system=spec_dict["system"],
            status=status,
            output_dir=Path(run_out),
            sweep_values=spec_dict["sweep_values"],
            phases=phases,
            message=message,
            error_type="PhaseError" if status == "error" else None,
        )
    except Exception as exc:  # noqa: BLE001 -- isolate per-run failures
        return RunResult(
            run_id=spec_dict["run_id"],
            system=spec_dict["system"],
            status="error",
            output_dir=Path(run_out),
            sweep_values=spec_dict["sweep_values"],
            phases=[],
            message=f"{type(exc).__name__}: {exc}",
            error_type=type(exc).__name__,
        )


def _not_submitted(after: str, *, umbrella: bool) -> str:
    """Why a run was not started.

    Umbrella windows are one measurement, so a failure ends the study rather
    than costing it a data point -- and saying so is the difference between a
    reader thinking something crashed and knowing the rest was not attempted
    on purpose.
    """
    if umbrella:
        return (
            f"Not submitted: window '{after}' failed, and a free energy needs "
            "every window -- the rest would be hours spent on runs that get "
            "thrown away."
        )
    return (
        f"Not submitted because continue_on_error=False stopped after failed "
        f"run '{after}'."
    )


# The dashboard matches this to explain the commonest failure a run has.
# A constant rather than a literal on both sides: a reworded message would
# otherwise stop being recognised with nothing failing anywhere.
ALREADY_HOLD_RESULTS = "These output directories already hold results:"


def _say_if_the_replicas_will_not_share_water(run_specs) -> None:
    """Name `prepared_from` where runs differ only in how they are driven.

    Solvation does not place water the same way twice. This module already
    shares one prepared system across umbrella windows for that reason --
    "water arranged differently between windows is noise in the free energy
    rather than physics" -- but that path is gated on `_is_umbrella`, and a
    seed sweep is not umbrella.

    A seed sweep is the same case in kind: one system, one setup block, only
    the integrator's seed differing. Left alone, each member solvates
    independently. Measured on a three-member sweep: 30,654 atoms against
    30,803. Anything called a replica sweep then measures dynamics variance
    *plus* solvation variance, and nothing in the output says so, because
    every recorded setting is identical -- the difference is in water that no
    setting names.

    This says it rather than fixing it, deliberately. Sharing setup by
    default would silently change what an existing config produces, and a
    study half-run under the old behaviour would stop being comparable with
    its own earlier members. The person is told at plan time, once, with the
    setting that resolves it.
    """
    if len(run_specs) < 2:
        return

    def preparation(spec):
        return json.dumps(
            {"system": spec.system, "setup": spec.options.get("setup") or {}},
            sort_keys=True, default=str)

    if len({preparation(spec) for spec in run_specs}) != 1:
        return  # Different systems, or prepared differently on purpose.
    # Both spellings. Checking only one told a study that had already done
    # the right thing to go and do it -- the worst kind of warning, because
    # a reader who believes it concludes their config is wrong.
    if any((spec.options.get("simulation") or {}).get("setup_from")
           or (spec.options.get("simulation") or {}).get("prepared_from")
           for spec in run_specs):
        return

    logger.warning(
        "These %d runs share one system and one setup block, so they differ "
        "only in how they are driven -- but each will solvate independently, "
        "and solvation does not place water the same way twice. Differences "
        "between them will include water placement as well as dynamics. Set "
        "`simulation.setup_from` to a finished study to give them the same "
        "prepared system.",
        len(run_specs))


class BatchExplorer:
    """Run one or more FastMDXplora studies (systems × sweep).

    Parameters
    ----------
    config : str | os.PathLike
        Path to a YAML config with a ``systems:`` list (and optionally
        ``sweep:`` / ``execution:``).
    output_dir : str | os.PathLike | None
        Root output directory. One run → written here directly; many runs
        → each in ``runs/<id>/``. Defaults to a timestamped directory.
    verbose : bool
        Forwarded to each run.
    continue_on_error : bool | None
        Override the config's ``execution.continue_on_error``. If None,
        the config value (default True) is used.

    Examples
    --------
    >>> BatchExplorer(config="study.yml").run()       # doctest: +SKIP
    """

    def __init__(
        self,
        config: str | os.PathLike | None = None,
        *,
        config_data: dict[str, Any] | None = None,
        output_dir: str | os.PathLike | None = None,
        verbose: bool = False,
        continue_on_error: bool | None = None,
        force: bool = False,
    ) -> None:
        if config is None and config_data is None:
            raise ValueError("BatchExplorer requires `config` (path) or `config_data` (dict).")

        if config_data is not None:
            self.config_path = str(config) if config is not None else "<in-memory>"
            raw = dict(config_data)
        else:
            self.config_path = str(config)
            raw = load_config_file(self.config_path)
        validate_config(raw, require_systems=True)
        self._raw = raw
        self.force = bool(force)
        self.verbose = verbose

        # Execution settings
        execution = raw.get("execution") or {}
        self.workers = execution.get("workers")
        self.devices = execution.get("devices")
        # Asking for workers or devices is asking to run in parallel. The two
        # were read independently, so `workers: 3` on its own scheduled the
        # runs one at a time and said nothing about it -- and a study of nine
        # windows took three times as long as it was told to. Nobody sets a
        # worker count wanting one at a time, and nobody lists GPUs to leave
        # all but one idle. `mode` written out still wins, so a config that
        # asks for both a worker count and sequential gets sequential.
        self.mode = execution.get("mode") or (
            "parallel" if (self.workers or self.devices) else "sequential")
        # Umbrella windows are one measurement, not several systems. A
        # campaign should carry on when one system fails -- the others are
        # still results. A free energy cannot be computed at all if a window
        # is missing, so continuing spends hours producing runs that will be
        # thrown away.
        from fastmdxplora.simulation.umbrella import plan_from_expanded

        self._is_umbrella = plan_from_expanded(raw) is not None

        self.continue_on_error = (
            continue_on_error if continue_on_error is not None
            else execution.get(
                "continue_on_error", not self._is_umbrella)
        )
        # The cross-run comparison report is a reporting artifact, so it's
        # controlled by the `report` block (default on).
        report_block = raw.get("report") or {}
        self.comparison = report_block.get("comparison", True)

        # Output root
        resolved_output = output_dir or raw.get("output")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.output_dir = (
            Path(resolved_output) if resolved_output
            else Path(f"fastmdxplora_output_{timestamp}")
        )

        # Expand the run matrix
        self.run_specs = self._build_run_specs(raw)
        self.is_single = len(self.run_specs) == 1
        self.results: list[dict[str, Any]] = []
        _say_if_the_replicas_will_not_share_water(self.run_specs)

    # ------------------------------------------------------------------
    def _build_run_specs(self, raw: dict[str, Any]) -> list[RunSpec]:
        from fastmdxplora.config import phase_options

        base_options = phase_options(raw)

        systems = normalize_systems(raw["systems"])
        sweep = (
            normalize_sweep(raw["sweep"]) if raw.get("sweep") is not None else None
        )
        return expand_runs(systems=systems, sweep=sweep, base_options=base_options)

    # ------------------------------------------------------------------
    def _refuse_to_overwrite_runs(
        self, include: list[str] | None, exclude: list[str] | None
    ) -> None:
        if self.force:
            return
        from fastmdxplora.orchestrator import PHASES

        planned = [
            phase
            for phase in PHASES
            if (not include or phase in include)
            and (not exclude or phase not in exclude)
        ]
        clashes: list[str] = []
        for spec in self.run_specs:
            run_out = self._run_output_dir(spec)
            occupied = [
                phase
                for phase in planned
                if (run_out / phase).is_dir() and any((run_out / phase).iterdir())
            ]
            if occupied:
                clashes.append(f"{run_out} ({', '.join(occupied)})")
        if clashes:
            listed = "\n  ".join(clashes)
            raise FileExistsError(
                f"{ALREADY_HOLD_RESULTS}\n  "
                f"{listed}\n"
                "Choose another output directory, delete these, or pass "
                "--force-overwrite to overwrite them."
            )

    def _run_output_dir(self, spec: RunSpec) -> Path:
        """Flat output for a single run; runs/<id>/ for many."""
        if self.is_single:
            return self.output_dir
        return self.output_dir / "runs" / spec.run_id

    def _resolve_workers(self) -> int:
        """Decide the parallel worker count."""
        if self.workers:
            return max(1, int(self.workers))
        if self.devices:
            return max(1, len(self.devices))
        # CPU default: all cores, capped at the number of runs.
        return max(1, min(os.cpu_count() or 1, len(self.run_specs)))

    def _device_for_worker(self, worker_slot: int) -> str | None:
        """Round-robin GPU device for a worker slot, or None if no devices."""
        if not self.devices:
            return None
        dev = self.devices[worker_slot % len(self.devices)]
        return str(dev)

    # ------------------------------------------------------------------
    def run(self) -> list[dict[str, Any]]:
        """Execute every run. Returns per-run result records."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        n = len(self.run_specs)
        include = self._raw.get("include")
        exclude = self._raw.get("exclude")

        # Checked here, before anything starts. Each run also refuses to
        # overwrite its own directory, but that refusal is raised inside a
        # worker and recorded as a failed run -- the study exits non-zero and
        # says nothing about why, or what to do instead. A study of eight
        # systems should also not run seven of them before saying the eighth
        # cannot start.
        self._refuse_to_overwrite_runs(include, exclude)

        shared = self._maybe_prepare_once(include, exclude)
        if shared is not None:
            # The windows read the system that was just prepared, so none of
            # them prepares one of its own. Which of the two lists says so
            # depends on which the study used: naming a phase in both is
            # refused, and adding to the wrong one failed every window before
            # it had done anything.
            if include:
                include = [phase for phase in include if phase != "setup"]
            else:
                exclude = list(exclude or []) + ["setup"]

        if self.is_single:
            logger.info("Exploring 1 molecular system in %s", self.output_dir)
        else:
            (self.output_dir / "runs").mkdir(exist_ok=True)
            logger.info(
                "Exploring %d molecular systems in %s (mode=%s)", n, self.output_dir, self.mode
            )
            print(f"\nFastMDXplora: exploring {n} systems ({self.mode})\n{'=' * 40}")

        if self.mode == "parallel" and not self.is_single:
            self.results = self._run_parallel(include, exclude)
        else:
            self.results = self._run_sequential(include, exclude)

        # Only write a batch manifest when there's actually a batch.
        if not self.is_single:
            self._write_batch_manifest()
            self._maybe_aggregate_members()
            # The free energy first, because the comparison report reads it.
            # `_umbrella_result` looks for `pmf.json` beside the manifest and
            # treats its absence as "these runs are not an umbrella study",
            # so building the comparison first meant an umbrella campaign was
            # written up as a set of independent runs whose radius of
            # gyration happened to differ -- the preamble that says otherwise
            # exists, and never ran. A batch that is not umbrella exits
            # `_maybe_build_pmf` at its first check and is unaffected.
            self._maybe_build_pmf()
            self._maybe_build_comparison()
            self._print_summary()
        return list(self.results)

    # ------------------------------------------------------------------
    def _maybe_aggregate_members(self) -> None:
        """One table across the members, written beside the manifest.

        Separate from the comparison report, which overlays series. This
        collects what each member concluded -- its settled means and the
        error it claimed for them -- and, for a seed sweep, sets those
        errors against the spread the replicas actually show. That
        comparison is the only check a reported uncertainty gets.
        """
        import json as _json

        from fastmdxplora.batch.aggregate import aggregate_members

        try:
            summary = aggregate_members(self.output_dir)
        except Exception as exc:  # noqa: BLE001 - a summary must not
            # take the campaign down with it: every member's own results
            # are already on disk and are the thing worth keeping.
            logger.warning("Could not aggregate members: %s", exc)
            return

        destination = Path(self.output_dir) / "members.json"
        destination.write_text(
            _json.dumps(summary, indent=2), encoding="utf-8")

        if summary.get("refused"):
            return
        for name, entry in summary.get("analyses", {}).items():
            note = entry.get("calibration")
            if note and "too tight" in note:
                logger.warning("%s: %s", name, note)

    # ------------------------------------------------------------------
    def _maybe_prepare_once(self, include, exclude) -> Path | None:
        """Prepare the system once for a set of umbrella windows.

        Seven windows of one real study came out with 37,212, 37,254, 37,436
        and 37,445 atoms: four different systems for one measurement. The
        windows are the same molecule held at different points along a
        coordinate, so they should be the same molecule. Solvation does not
        place water the same way twice, and water arranged differently
        between windows is noise in the free energy rather than physics.

        A window is not a system of its own, so it does not get a system of
        its own. Preparing seven times was also seven times the work.

        Only for a study whose runs are one measurement. An ordinary campaign
        is untouched: different systems *are* different systems, and one
        preparation shared between them would be wrong rather than efficient.
        """
        if not self._is_umbrella:
            return None

        wanted = set(include) if include else {"setup", "simulation",
                                               "analysis", "report"}
        if "setup" not in wanted or "setup" in set(exclude or []):
            # Nothing is being prepared here -- the windows are simulating
            # from something that already exists. They may still need
            # seeding, and this is the path a study takes when it reuses a
            # prepared system, which is exactly when seeding matters most.
            # Returning early without seeding meant a study could ask to be
            # seeded, say nothing, and start every window in the same place.
            # Both spellings, and this is the one that matters most: a
            # config saying `setup_from` took this path, matched nothing,
            # and started every window in the same place -- the bug this
            # branch exists to prevent, reintroduced by renaming the key
            # that reaches it.
            reused = (self._raw or {}).get("simulation") or {}
            supplied = reused.get("setup_from") or reused.get("prepared_from")
            if supplied:
                self._give_each_window_its_start(Path(supplied))
            return None
        if wanted == {"setup"}:
            # Preparing is the whole study. There is nothing to share it
            # with, and doing it once would leave every window with no work.
            return None

        # An umbrella study is one system by the time it gets here -- expansion
        # refuses several -- but a sweep can still ask for the windows to be
        # prepared differently from one another. Sharing then would quietly
        # ignore what the sweep asked for, which is worse than preparing
        # seven times.
        preparations = {
            json.dumps(spec.options.get("setup") or {}, sort_keys=True, default=str)
            for spec in self.run_specs
        }
        if len(preparations) != 1:
            logger.warning(
                "The windows are swept over %d different setup settings, so "
                "each prepares its own system.", len(preparations))
            return None

        shared = self.output_dir / "shared_setup"
        prepared = shared / "setup"
        if self.force and shared.exists() and not _a_prepared_system_is_there(prepared):
            # A half-written shared setup from an interrupted study: with
            # --force the intent is plainly to start again, and leaving the
            # remains there means the preparation refuses its own directory.
            import shutil

            shutil.rmtree(shared)
        if not _a_prepared_system_is_there(prepared):
            print("\nPreparing one system for every window\n" + "=" * 40)
            first = self.run_specs[0]
            result = _execute_run(
                first.to_dict(), str(shared), ["setup"], None,
                self.verbose, None, quiet=False, force=self.force,
            )
            if result.status == "error" or not _a_prepared_system_is_there(prepared):
                # Every window would fail the same way, one after another,
                # for hours. Say it once here instead.
                raise RuntimeError(
                    "The system could not be prepared, so there is nothing "
                    f"for the windows to simulate: {result.message or prepared}"
                )

        # Outside the block that prepares, because the selections need
        # checking against whatever system the windows will use -- and a
        # study re-run with --force finds one already there and prepares
        # nothing. Inside, this checked only freshly prepared systems, which
        # is the one case where the author has just seen the structure built.
        _check_selections_against(
            prepared, self.run_specs[0].options.get("simulation") or {})

        self._give_each_window_its_start(prepared)
        return prepared

    # ------------------------------------------------------------------
    def _give_each_window_its_start(self, prepared: Path) -> None:
        """Point every window at the system it begins from.

        One place, because there are two ways to arrive here -- a system
        prepared just now, or one named by `prepared_from` and reused -- and
        a seeded study must behave the same either way. It did not: seeding
        lived on one of those paths, so asking to reuse a prepared system
        silently turned seeding off.
        """
        seeds = self._maybe_seed_the_windows(prepared)
        for spec in self.run_specs:
            simulation = dict(spec.options.get("simulation") or {})
            index = (simulation.get("umbrella") or {}).get("index")
            mine = seeds.get(int(index)) if index is not None else None
            simulation["prepared_from"] = str(mine or prepared)
            # A window does not pull. The block travels with the study so
            # `_maybe_seed_the_windows` can find it; leaving it on the window
            # would have every one of them drag the ligand out again while
            # restrained at a fixed point.
            simulation.pop("steered", None)
            spec.options["simulation"] = simulation

    # ------------------------------------------------------------------
    def _maybe_seed_the_windows(self, prepared: Path) -> dict[int, str]:
        """Start each window where it belongs, from a steered pull.

        Empty where the study did not ask for it, and every window then
        shares the one prepared state as before.

        Two ways to ask, because there are two situations. A `steered` block
        beside the `umbrella` one means *pull, then seed*: one config, one
        command, which is the case this exists for. `umbrella.seed_from`
        naming a finished pull means *reuse that one*, which matters because
        retuning the spacing or the force constant should not cost another
        pull -- the windows change, the pathway does not.

        The pull runs from the same prepared system the windows will use.
        Seeds are positions in a particular `system.xml`, and positions from
        a second preparation belong to a different arrangement of water.
        """
        from fastmdxplora.simulation.metadynamics import (
            with_general_selection_names,
        )
        from fastmdxplora.simulation.seeding import seed_windows
        from fastmdxplora.simulation.umbrella import plan_from_expanded

        raw = self._raw or {}
        simulation = raw.get("simulation") or {}
        pull_spec = simulation.get("steered")
        plan = plan_from_expanded(raw)
        if plan is None:
            return {}

        first_window = (self.run_specs[0].options.get("simulation") or {}
                        ).get("umbrella") or {}
        reuse = first_window.get("seed_from")
        if not pull_spec and not reuse:
            return {}

        pull_output = (Path(reuse) if reuse
                       else self.output_dir / "seed_pull")
        if pull_spec and not _a_pull_is_there(pull_output):
            print("\nPulling once, to start every window where it belongs\n"
                  + "=" * 52)
            # `to_dict()` nests the phase blocks under "options", which is
            # where `_execute_run` reads them. Assigning `options["simulation"]`
            # put a key at the top level that nothing reads and left the
            # window's own block in place -- so the pull ran as umbrella
            # window 0, restrained at 0.4 nm, dragging nothing anywhere.
            options = dict(self.run_specs[0].to_dict())
            options["run_id"] = "seed-pull"
            carried = {k: v for k, v in simulation.items()
                       if k not in ("umbrella", "steered")}
            carried["prepared_from"] = str(prepared)
            carried["steered"] = pull_spec
            # Every atom, overriding whatever the study saves elsewhere. A
            # trajectory defaults to "not water" because water is ten times
            # the file for questions nobody is asking -- but a seed is a
            # complete set of positions, and a frame without solvent cannot
            # produce one. Taking the water from the prepared system instead
            # would put bound-state solvent where the ligand now is.
            carried["save_selection"] = "all"
            options["options"] = dict(options.get("options") or {})
            options["options"]["simulation"] = carried
            result = _execute_run(
                options, str(pull_output), ["simulation"], None,
                self.verbose, None, quiet=False, force=self.force,
            )
            if result.status == "error":
                raise RuntimeError(
                    "The pull that seeds the windows did not finish, so "
                    "there are no starting structures to take: "
                    f"{result.message or pull_output}"
                )

        centres = [w.centre for w in plan.windows]
        # The seeder has to read the same config the PLUMED builder reads.
        # `with_general_selection_names` is what turns the general
        # `select_atoms` into whichever role name a variable uses, and it
        # runs inside `plan_from_config` -- so the pull was translated and
        # this was not. A study written with `select_atoms`, which is the
        # spelling the documentation leads with, therefore pulled for two
        # and a half hours and then found nothing here to measure against.
        #
        # Two readers of one block again, which is what patch 0038 was
        # about. The line below it already carried the lesson for the
        # ligand and not for the selection.
        window = with_general_selection_names(
            dict(first_window),
            str(first_window.get("collective_variable", "")).lower())
        seeds = seed_windows(
            pull_output, prepared, centres, self.output_dir / "seeds",
            # Either spelling: the umbrella block accepts both, so the
            # thing reading it has to as well.
            ligand_resname=str(window.get("ligand_resname")
                               or window.get("ligand_name") or ""),
            site_selection=str(window.get("site_selection") or ""),
            temperature_K=float(simulation.get("temperature_K", 300.0)),
            random_seed=int(simulation.get("random_seed") or 0),
        )
        record = self.output_dir / "seeds" / "seeds.json"
        record.write_text(
            json.dumps({"pull": str(pull_output),
                        "seeds": [s.as_record() for s in seeds]}, indent=2),
            encoding="utf-8")
        return {s.index: s.directory for s in seeds}

    # ------------------------------------------------------------------
    def _maybe_build_pmf(self) -> None:
        """Recombine umbrella windows once they have all run.

        The windows are ordinary runs, so nothing before this point knows
        they belong together. Here is where they have all finished and the
        set is visible -- and where the overlap between them can be checked,
        which is the thing that decides whether a free energy exists at all.
        """
        import json

        from fastmdxplora.simulation.umbrella import (
            as_a_config_block,
            collect_samples,
            compute_pmf,
            design_from_a_pilot,
            plan_from_expanded,
        )

        # Rebuilt from the runs rather than carried in the config: a config is
        # what a user wrote, not a place to hide state, and validation
        # rightly refused an extra key when this was smuggled through one.
        plan = plan_from_expanded(self._raw or {})
        if plan is None:
            return

        # Where the runs actually went, from the same method that put them
        # there. Reconstructing the path from the output directory got both
        # the "runs" level and the slugged identifier wrong.
        directories = {}
        for spec in self.run_specs:
            block = (spec.options.get("simulation") or {}).get("umbrella") or {}
            if block.get("index") is not None:
                directories[int(block["index"])] = self._run_output_dir(spec)

        try:
            # The plan's fraction, not the function's default: a study
            # that asked to discard a third and quietly got a fifth
            # would report the third in `pmf.json` and have used the
            # other number to build the histograms.
            samples = collect_samples(
                directories,
                equilibration_fraction=plan.equilibration_fraction)
        except FileNotFoundError as exc:
            # Some window did not produce sampling. Recorded rather than
            # raised: the runs that did work are still on disk and worth
            # keeping.
            payload = {"pmf": None, "refused": str(exc),
                       "plan": plan.as_record()}
        else:
            temperature = float(
                ((self._raw or {}).get("simulation") or {})
                .get("temperature_K", 300.0))
            payload = compute_pmf(samples, plan, temperature_K=temperature)
            payload["plan"] = plan.as_record()

            # What these windows say the study should have been. Every
            # window measures the gradient where it came to rest, and the
            # gradient fixes both settings a study has to choose, so a run
            # that has just finished can size the next one -- a short set of
            # windows run deliberately as a pilot, or this one.
            #
            # Computed whether the study passed or refused, and especially
            # when it refused: a refusal that says which windows slid is
            # worth less than one that comes with the design that holds
            # them. It sits in a `try` because it is advice, and a study
            # that produced a free energy must not lose it to a failure in
            # the paragraph recommending the next study.
            try:
                payload["next_study"] = design_from_a_pilot(
                    samples, plan, temperature_K=temperature,
                    # The curve where this study produced one: its slope is
                    # the same gradient the windows measure, with every
                    # window's sampling behind it instead of one window's
                    # median. A study that refused has none, and then the
                    # windows' own displacements are what there is.
                    curve=((payload["pmf"]["coordinate"],
                            payload["pmf"]["free_energy_kjmol"])
                           if payload.get("pmf") else None))
            except (ValueError, ZeroDivisionError) as exc:
                payload["next_study"] = {"not_designed": str(exc)}

            # A binding free energy only where there is a free energy to
            # take it from. The overlap and sampling gates decide that, and
            # this sits behind them rather than beside them: a number
            # integrated over a curve stitched across a gap would be the
            # most quotable thing the study produced and the least
            # supported. Attempted only for a ligand-distance coordinate,
            # since the standard-state correction is about the volume a
            # ligand gives up and means nothing along a torsion.
            if payload.get("pmf") and plan.collective_variable in (
                    "ligand_distance", "distance"):
                from fastmdxplora.simulation.binding import (
                    binding_free_energy)

                payload["binding"] = binding_free_energy(
                    payload["pmf"]["coordinate"],
                    payload["pmf"]["free_energy_kjmol"],
                    temperature_K=temperature,
                )

        destination = Path(self.output_dir) / "pmf.json"
        destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        # Cleared before anything is drawn, so a refusal cannot leave the
        # previous run's figure in place. `--force` clears the run
        # directories and not this one, so a study that refused where the
        # last one succeeded kept a plot from a different set of windows --
        # and the comparison report beside it linked to that plot as though
        # it were this study's result.
        self._clear_previous_drawing()
        drawn = None if payload.get("refused") else self._draw_pmf(destination)

        # Printed rather than sent to a presenter this class does not have.
        # The `getattr` chain here looked for `_presenter` and `presenter`,
        # and a BatchExplorer has neither, so the free energy -- the result
        # the whole study exists for -- was computed, written and drawn in
        # silence while the comparison of the windows' restrained
        # trajectories announced itself on the line below.
        if payload.get("refused"):
            print("Free energy:    not computed -- "
                  + payload["refused"].split(".")[0])
        else:
            drawn_note = f", drawn in {drawn.parent.name}/" if drawn else ""
            print(f"Free energy:    {destination}{drawn_note}")

        # Printed when the study refused, or when it passed with windows off
        # their centres -- the two cases where the person is about to choose
        # settings for another run. A study where everything held prints
        # nothing and leaves the design in `pmf.json`.
        design = payload.get("next_study") or {}
        if design.get("centres") and (payload.get("refused")
                                      or payload.get("drifted")):
            print(f"Next study:     {design['n_windows']} windows from these "
                  f"windows' own gradients, "
                  f"{design['covers'][0]:g} to {design['covers'][1]:g}, "
                  f"worst overlap {design['worst_predicted_overlap']:.2f} "
                  "predicted")
            print(as_a_config_block(design), end="")

    def _clear_previous_drawing(self) -> None:
        """Remove a figure from an earlier study of this directory.

        A stale plot is worse than no plot: it is the previous answer,
        presented as this one.
        """
        import shutil

        drawn = Path(self.output_dir) / "free_energy"
        if drawn.is_dir():
            shutil.rmtree(drawn, ignore_errors=True)

    def _draw_pmf(self, pmf_json: Path) -> Path | None:
        """Draw the study's free energy, beside the study.

        The PMF belongs to the study rather than to any window, and putting
        the drawing in the per-run analysis phase left it undrawn: each
        window analysed itself before the last one finished, so no window
        found a result to draw, and by the time one existed no analysis
        phase remained. The windows had each produced ten figures of their
        own restrained trajectory, and the one result the study existed for
        had none.

        Best effort, like the comparison report beside it: a study whose
        windows all succeeded must not fail here.
        """
        try:
            from fastmdxplora.analysis.pmf import PMF

            analysis = PMF(output_dir=Path(self.output_dir) / "free_energy")
            result = analysis.run(None)
            return getattr(result, "figure_path", None)
        except Exception as exc:  # noqa: BLE001 -- never break the study
            logger.warning(
                "The free energy was computed and written to %s but could "
                "not be drawn (%s). The numbers are unaffected.",
                pmf_json.name, exc)
            return None

    def _maybe_build_comparison(self) -> None:
        """Build the cross-run comparison report (best-effort).

        Controlled by ``execution.comparison`` (default True). A failure
        here must never fail the batch — the runs themselves succeeded.
        """
        if not self.comparison:
            return
        n_ok = sum(1 for r in self.results if r.status == "ok")
        if n_ok < 2:
            return
        try:
            from fastmdxplora.batch.compare import build_comparison_report

            path = build_comparison_report(self.output_dir)
            if path is not None:
                print(f"Comparison:     {path / 'comparison_report.md'}")
        except Exception as exc:  # noqa: BLE001 -- never break the batch
            logger.warning("Comparison report failed (runs are unaffected): %s", exc)

    # ------------------------------------------------------------------
    def dry_run(self) -> list["RunResult"]:
        """Report the plan without executing anything.

        Prints each run, its system, swept values, target output directory,
        and the phases that would execute, then returns a list of
        ``RunResult`` with status ``"planned"`` (no phases populated).
        """
        from fastmdxplora.orchestrator import RunResult, PHASES

        include = self._raw.get("include")
        exclude = self._raw.get("exclude")
        # Compute the phase plan the same way the orchestrator would.
        if include:
            plan = [p for p in PHASES if p in include]
        elif exclude:
            plan = [p for p in PHASES if p not in exclude]
        else:
            plan = list(PHASES)

        n = len(self.run_specs)
        layout = "flat" if self.is_single else "runs/<id>/"
        print("\nFastMDXplora dry run (no execution)")
        print("=" * 50)
        print(f"  runs:    {n}")
        print(f"  output:  {self.output_dir}  ({layout} layout)")
        print(f"  phases:  {' → '.join(plan) if plan else '(none)'}")
        if self.mode == "parallel":
            print(f"  mode:    parallel ({self._resolve_workers()} workers"
                  + (f", devices={self.devices}" if self.devices else "") + ")")
        else:
            print("  mode:    sequential")
        print("-" * 50)

        planned: list[RunResult] = []
        for i, spec in enumerate(self.run_specs, start=1):
            run_out = self._run_output_dir(spec)
            label = spec.run_id if not self.is_single else spec.system
            sweep = ""
            if spec.sweep_values:
                sweep = "  [" + ", ".join(
                    f"{k}={v}" for k, v in spec.sweep_values.items()) + "]"
            print(f"  [{i}/{n}] {label}{sweep}")
            print(f"          → {run_out}")
            planned.append(RunResult(
                run_id=spec.run_id, system=spec.system, status="planned",
                output_dir=run_out, sweep_values=spec.sweep_values, phases=[],
            ))
        print("=" * 50)
        return planned

    # ------------------------------------------------------------------
    def _run_sequential(self, include, exclude) -> list["RunResult"]:
        results: list[RunResult] = []
        n = len(self.run_specs)
        for i, spec in enumerate(self.run_specs, start=1):
            run_out = self._run_output_dir(spec)
            if not self.is_single:
                print(f"\n[{i}/{n}] {spec.run_id}")
                if spec.sweep_values:
                    pretty = ", ".join(f"{k}={v}" for k, v in spec.sweep_values.items())
                    print(f"        {pretty}")
            # Sequential: a single device (first listed) may still be pinned.
            device = self._device_for_worker(0) if self.devices else None
            result = _execute_run(
                spec.to_dict(), str(run_out), include, exclude,
                self.verbose, device, quiet=not self.is_single,
                force=self.force,
            )
            results.append(result)
            if result.status == "error" and not self.continue_on_error:
                logger.error(
                    "Stopping after failed run '%s': %s",
                    spec.run_id, _why_it_failed(result))
                for skipped in self.run_specs[i:]:
                    results.append(_skipped_run_result(
                        skipped,
                        self._run_output_dir(skipped),
                        _not_submitted(
                            spec.run_id,
                            umbrella=getattr(self, "_is_umbrella", False),
                        ),
                    ))
                break
        return results

    # ------------------------------------------------------------------
    def _run_parallel(self, include, exclude) -> list["RunResult"]:
        from fastmdxplora.orchestrator import RunResult

        n_workers = self._resolve_workers()
        n = len(self.run_specs)
        print(f"Parallel execution: {n_workers} worker(s)"
              + (f", devices={self.devices}" if self.devices else ""))

        # A worker's output goes to its own log so three of them do not
        # interleave, which means the terminal will show nothing at all unless
        # this loop says something.
        print("Each run writes its own log; the study reports here.")

        results: list[RunResult] = []
        futures = {}
        next_index = 0
        done = 0
        stopped_after: str | None = None
        began = time.monotonic()

        def submit_next(pool: ProcessPoolExecutor) -> bool:
            nonlocal next_index
            if next_index >= n:
                return False
            spec = self.run_specs[next_index]
            run_out = self._run_output_dir(spec)
            device = self._device_for_worker(next_index)
            fut = pool.submit(
                _execute_run,
                spec.to_dict(), str(run_out), include, exclude,
                self.verbose, device, force=self.force,
            )
            futures[fut] = (next_index, spec)
            next_index += 1
            # Said on the way in, so a run that is still going has been named
            # once and its log can be followed while it goes.
            print(f"       started {spec.run_id} -> {run_out / 'run.log'}",
                  flush=True)
            return True

        def await_one():
            """The next run to finish, saying what is happening until one does.

            A window runs for hours. Without this the terminal shows the last
            completion and then nothing, and a study that is working looks
            exactly like one that has hung.
            """
            while True:
                finished, pending = wait_for_any(
                    futures, timeout=HEARTBEAT_SECONDS,
                    return_when=FIRST_COMPLETED)
                if finished:
                    return next(iter(finished))
                # Redrawn in place where there is a terminal to redraw on.
                # Piped to a file, a carriage return makes one endless line,
                # so there the heartbeat is written out as before -- a log is
                # read after the fact and wants the history.
                in_flight = [
                    (spec.run_id, self._run_output_dir(spec))
                    for fut, (_index, spec) in futures.items()
                    if fut in pending
                ]
                line = _progress_line(
                    running=len(pending), done=done, total=n,
                    queued=n - next_index, seconds=time.monotonic() - began
                ) + _how_far_along(in_flight)
                if _is_a_terminal():
                    print("\r" + line, end="", flush=True)
                else:
                    print(line, flush=True)

        threads_each = _threads_for_each(n_workers)
        print(f"Each worker takes up to {threads_each} thread(s) of "
              f"{os.cpu_count() or 1}; without that they compete for all of "
              "them.", flush=True)
        pool = ProcessPoolExecutor(
            max_workers=n_workers,
            # Spawn, on every platform. Linux's default is still fork, and a
            # forked worker inherits the parent as it stood -- including the
            # thread pools numpy, mdtraj and OpenMM had started, whose
            # threads do not survive the fork but whose locks do. Three CI
            # runs in a row walled silently at the first parallel test, six
            # hours each until a job timeout existed, beginning the day
            # OpenMM landed in CI -- and never on a one-core machine, where
            # those pools are never spun up, which is why it would not
            # reproduce locally. macOS and Windows have spawned all along;
            # this makes Linux match them, and it is where Python itself
            # goes in 3.14. Spawn is also what makes the initializer below
            # honest: the thread-cap variables are read once at import, and
            # only a fresh interpreter imports after they are set.
            mp_context=get_context("spawn"),
            initializer=_give_this_worker_its_share,
            initargs=(threads_each,))
        try:
            for _ in range(min(n_workers, n)):
                submit_next(pool)

            while futures:
                fut = await_one()
                _idx, spec = futures.pop(fut)
                done += 1
                try:
                    result = fut.result()
                except Exception as exc:  # noqa: BLE001
                    result = RunResult(
                        run_id=spec.run_id, system=spec.system, status="error",
                        output_dir=self._run_output_dir(spec),
                        sweep_values=spec.sweep_values, phases=[],
                        message=f"{type(exc).__name__}: {exc}",
                        error_type=type(exc).__name__,
                    )
                mark = "✓" if result.status == "ok" else "✗"
                _clear_progress_line()
                print(f"[{done}/{n}] {mark} {spec.run_id}")
                if result.status == "error":
                    print(f"      {_why_it_failed(result)}")
                results.append(result)

                if result.status == "error" and not self.continue_on_error:
                    stopped_after = spec.run_id
                    logger.error(
                        "Stopping parallel batch after failed run '%s': %s",
                        spec.run_id, _why_it_failed(result))
                    break

                if stopped_after is None:
                    submit_next(pool)

            if stopped_after is not None:
                # Do not submit new work. Running tasks cannot always be
                # cancelled, so collect anything already in flight and mark
                # never-submitted runs explicitly as skipped.
                for fut, (_idx, spec) in list(futures.items()):
                    if fut.cancel():
                        results.append(_skipped_run_result(
                            spec,
                            self._run_output_dir(spec),
                            (
                                "Cancelled because continue_on_error=False "
                                f"stopped after failed run '{stopped_after}'."
                            ),
                        ))
                        futures.pop(fut, None)

                while futures:
                    fut = await_one()
                    _idx, spec = futures.pop(fut)
                    done += 1
                    try:
                        result = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        result = RunResult(
                            run_id=spec.run_id, system=spec.system, status="error",
                            output_dir=self._run_output_dir(spec),
                            sweep_values=spec.sweep_values, phases=[],
                            message=f"{type(exc).__name__}: {exc}",
                            error_type=type(exc).__name__,
                        )
                    mark = "✓" if result.status == "ok" else "✗"
                    _clear_progress_line()
                    print(f"[{done}/{n}] {mark} {spec.run_id}")
                    results.append(result)

                for spec in self.run_specs[next_index:]:
                    results.append(_skipped_run_result(
                        spec,
                        self._run_output_dir(spec),
                        _not_submitted(
                            stopped_after,
                            umbrella=getattr(self, "_is_umbrella", False),
                        ),
                    ))
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

        # Preserve deterministic (submission) order in the manifest.
        order = {spec.run_id: i for i, spec in enumerate(self.run_specs)}
        results.sort(key=lambda r: order.get(r.run_id, 0))
        return results

    # ------------------------------------------------------------------
    def _write_batch_manifest(self) -> None:
        from fastmdxplora import __citation__, __doi__, __version__

        manifest = {
            "tool": "FastMDXplora",
            "kind": "batch",
            "version": __version__,
            "doi": __doi__,
            "citation": __citation__,
            "config": self.config_path,
            "output_dir": str(self.output_dir),
            "n_runs": len(self.run_specs),
            "execution": {
                "mode": self.mode,
                "workers": self._resolve_workers() if self.mode == "parallel" else 1,
                "devices": self.devices,
            },
            "systems": [
                {"id": s["id"], "system": s["system"]}
                for s in normalize_systems(self._raw["systems"])
            ],
            "sweep": self._raw.get("sweep") or {},
            "runs": [r.to_dict() for r in self.results],
        }
        path = self.output_dir / "batch_manifest.json"
        with path.open("w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, default=str)
        logger.debug("Wrote batch manifest: %s", path)

    # ------------------------------------------------------------------
    def _print_summary(self) -> None:
        ok = sum(1 for r in self.results if r.status == "ok")
        err = sum(1 for r in self.results if r.status == "error")
        skipped = sum(1 for r in self.results if r.status == "skipped")
        print(f"\n{'=' * 40}")
        print(
            f"Batch complete: {ok} ok, {err} error(s), "
            f"{skipped} skipped, {len(self.results)} total"
        )
        print(f"Batch output:   {self.output_dir}")
        print(f"Manifest:       {self.output_dir / 'batch_manifest.json'}")


def _a_pull_is_there(directory: Path) -> bool:
    """Whether a finished pull already sits where one would be written.

    A study re-run with the same output does not pull again: the pathway is
    the expensive part and it does not change when the windows do.
    """
    root = directory / "simulation"
    root = root if root.is_dir() else directory
    return any(root.glob("*.dcd")) or any(root.glob("*.xtc"))
