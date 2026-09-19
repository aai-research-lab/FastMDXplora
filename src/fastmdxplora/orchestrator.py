"""The FastMDXplora project-level orchestrator.

This module implements the central orchestrator class. Following the
phase-based orchestration pattern (Aina & Kwan, JCC 2026),
the orchestrator:

  1. Holds shared project state (system input, output directory, options)
  2. Knows its registered phases (setup, simulate, analyze, report)
  3. Applies intelligent defaults and validates per-phase options
  4. Executes phases in coordinated sequence
  5. Consolidates outputs into a single project directory

Unlike a generic workflow engine (Snakemake, Nextflow, Galaxy), the workflow
is built-in and the user expresses intent through include/exclude and option
overrides, not by describing a DAG (directed acyclic graph: the
task-and-dependency model those engines use).
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmdxplora.utils.logging import get_logger
from fastmdxplora.refusals import refusal_of
from fastmdxplora.refusals import OutputExistsError
from fastmdxplora.refusals import StudyError

logger = get_logger("project")


# ---------------------------------------------------------------------------
# The canonical phase list. This is the only place phase order is defined.
# ---------------------------------------------------------------------------
PHASES: tuple[str, ...] = ("setup", "simulation", "analysis", "report")


@dataclass
class PhaseResult:
    """Lightweight record of a single phase invocation."""

    name: str
    status: str  # "ok" | "skipped" | "error"
    output_dir: Path | None = None
    started_at: str = ""
    finished_at: str = ""
    message: str = ""
    artifacts: list[str] = field(default_factory=list)
    refusal: dict[str, Any] = field(default_factory=dict)
    """What this phase refused, as a record rather than as a sentence.

    Present on an errored phase, empty otherwise. ``message`` holds the
    prose and always did; this holds the same fact in the form a program
    can branch on -- the stable code, its kind, whether it is worth
    retrying, and the particulars the sentence interpolated.

    Written into the manifest, so a reader opening the run afterwards
    sees what an in-process caller saw. A refusal is a result, and a
    study that stopped should record why it stopped in the same place it
    would have recorded what it found.
    """
    produced_by: dict[str, Any] = field(default_factory=dict)
    """Version, host and package environment that produced *this* phase.

    The manifest's top-level `version`, `environment` and `source` describe
    whichever session wrote the file last. Once a manifest can carry phases
    from more than one session -- a run simulated on the cluster and then
    analysed on a workstation, or re-analysed after an upgrade -- those
    top-level fields stop describing the phases beneath them. This says who
    produced each one.

    Empty for a phase recorded before this field existed. That is the
    honest value: we do not know what produced it, and guessing the current
    session would be worse than saying nothing.
    """

    def to_dict(self) -> dict[str, Any]:
        record = {
            "name": self.name,
            "status": self.status,
            "output_dir": str(self.output_dir) if self.output_dir else None,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "message": self.message,
            "artifacts": self.artifacts,
        }
        if self.refusal:
            record["refusal"] = self.refusal
        if self.produced_by:
            record["produced_by"] = self.produced_by
        return record


@dataclass
class RunResult:
    """Result of one run within an exploration.

    ``explore()`` always returns a list of these — a single study is a
    list of one, a sweep is a list of many. Each carries the run's
    identity and the per-phase results inside ``phases``.
    """

    run_id: str
    system: str
    status: str  # "ok" | "error" | "skipped"
    output_dir: Path | None = None
    sweep_values: dict[str, Any] = field(default_factory=dict)
    phases: list[PhaseResult] = field(default_factory=list)
    message: str = ""
    error_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "system": self.system,
            "status": self.status,
            "output_dir": str(self.output_dir) if self.output_dir else None,
            "sweep_values": self.sweep_values,
            "phases": [p.to_dict() for p in self.phases],
            "message": self.message,
            "error_type": self.error_type,
        }

    # Convenience: treat a RunResult a bit like its phase list, so common
    # patterns (iterating phases, checking a phase status) stay ergonomic.
    def phase(self, name: str) -> PhaseResult | None:
        """Return the PhaseResult for ``name``, or None if it didn't run."""
        for p in self.phases:
            if p.name == name:
                return p
        return None


class FastMDXplora:
    """Project-level orchestrator for end-to-end MD studies.

    Parameters
    ----------
    system : str | os.PathLike
        Input for a single study. Accepted forms (auto-detected):

        - Path to a PDB / CIF file (e.g. ``"protein.pdb"``)
        - 4-character PDB ID (e.g. ``"1L2Y"``), fetched from RCSB
        - One-letter amino-acid sequence, if structure prediction is
          available (future)

        Mutually exclusive with ``config``.
    config : str | os.PathLike | None
        Path to a YAML config file. Drives one system or many (with an
        optional parameter sweep and parallel execution); the interface
        is the same either way. Mutually exclusive with ``system``.
    output_dir : str | os.PathLike | None
        Where to write project outputs. Defaults to
        ``./fastmdxplora_<system>_study_<timestamp>``.
    options : dict[str, dict] | None
        Per-phase keyword arguments, e.g.
        ``{"simulation": {"duration_ns": 100}}``.
    verbose : bool
        If True, log progress to stdout in addition to the project log file.
    include, exclude : list[str] | None
        Default phase selection (``explore()`` arguments still override).

    Examples
    --------
    >>> fmdx = FastMDXplora(system="protein.pdb")
    >>> fmdx.explore()                          # doctest: +SKIP

    >>> fmdx = FastMDXplora(system="1L2Y")     # PDB ID, fetched from RCSB
    >>> fmdx.explore(                            # doctest: +SKIP
    ...     include=["setup", "simulation"],
    ...     options={"simulation": {"duration_ns": 50}},
    ... )

    >>> # A config file: one system or many, same interface:
    >>> fmdx = FastMDXplora(config="study.yml")
    >>> fmdx.explore()                          # doctest: +SKIP
    """

    def __init__(
        self,
        system: str | os.PathLike | None = None,
        *,
        config: str | os.PathLike | None = None,
        config_data: dict[str, Any] | None = None,
        output_dir: str | os.PathLike | None = None,
        options: dict[str, dict[str, Any]] | None = None,
        study_options: dict[str, Any] | None = None,
        verbose: bool = False,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
    ) -> None:
        # FastMDXplora is the single user-facing entry point. Ways to
        # construct it:
        #   - config=...      : a YAML config path (one system or many, with
        #                       optional sweep / parallel execution).
        #   - config_data=... : the same, as an already-parsed dict (used by
        #                       the CLI, which assembles a config from flags).
        #   - system=...      : a single concrete study, run directly. This is
        #                       also the path the internal batch worker uses
        #                       for each run, so it must not recurse.
        # config/config_data execution is deferred to explore().
        n_config = sum(x is not None for x in (config, config_data))
        if n_config and system is not None:
            raise StudyError(
                "Pass either `system=` (a single study) or a config "
                "(`config=` / `config_data=`), not both."
            , code="config.option.conflicting")

        self._config_path: str | None = (
            str(config) if config is not None else None
        )
        self._config_data: dict[str, Any] | None = config_data
        self._deferred_output_dir = output_dir
        self._deferred_verbose = verbose

        if n_config:
            # Config-driven: defer everything to explore(). Nothing creates
            # an output directory or banner here because the batch machinery
            # owns the layout (flat for one run, runs/<id>/ for many).
            self.system = None  # resolved per-run by the batch layer
            self.options = options or {}
            self.study_options: dict[str, Any] = dict(study_options or {})
            self.verbose = bool(verbose)
            self._config_include = include
            self._config_exclude = exclude
            self.results = []
            # Declared, not omitted. This branch used to return without
            # setting it at all, so the same class had two shapes depending
            # on which argument it was given -- and any code asking "where
            # is the output" had to know which, or discover it through an
            # AttributeError.
            #
            # That is not a hypothetical. An attempt to fix a logging leak
            # landed on the other branch and did nothing, because every
            # caller affected was on this one, and the difference was
            # invisible until traced. None says the same thing the absence
            # did -- the location is not settled yet -- in a way that can be
            # read rather than caught. explore() fills it in from the batch
            # layer, as it already did.
            self.output_dir = None
            return

        # ---- Direct single-study path -----------------------------------
        if system is None:
            raise StudyError(
                "FastMDXplora requires either a `system` input (a PDB/CIF "
                "file path, a 4-character PDB ID, or a one-letter sequence) "
                "or a `config` file."
            , code="config.option.missing_companion")

        self.system: str = str(system)

        # Phase selection (the batch layer passes the config's include/exclude)
        self._config_include: list[str] | None = include
        self._config_exclude: list[str] | None = exclude

        from fastmdxplora.naming import default_output_name, system_of

        self.output_dir: Path | None = (
            Path(output_dir) if output_dir
            else Path(default_output_name(system_of(self.config)))
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Which process this study is, so a GUI opened on the folder later
        # can adopt the run: show it as running and stop it if asked. The
        # file goes when the process exits; a crash leaves it, and an
        # adopter checks the process is alive and is this run before
        # believing it.
        _record_run_process(self.output_dir)

        self.options: dict[str, dict[str, Any]] = options or {}
        # Study-level settings (`agent`, `agent_model`), which describe the
        # config as a whole rather than a phase. The batch layer passes them
        # down from the config; a direct `system=` study has none.
        self.study_options: dict[str, Any] = dict(study_options or {})
        self.verbose: bool = bool(verbose)

        # Per-phase output subdirectories (created lazily by each phase)
        self._phase_dirs: dict[str, Path] = {
            phase: self.output_dir / phase for phase in PHASES
        }

        # Record of phase executions in this session
        self.results: list[PhaseResult] = []

        self._configure_logging()
        self._presenter = self._configure_presenter()

        # Display the opening banner once the session is ready.
        from fastmdxplora import __version__

        # The settings this run will use, not the ones a command line
        # mentioned. The banner reconstructed them from sys.argv, so a run
        # driven by a config file showed the defaults for everything -- the
        # step counts, the timestep, the temperature -- while using the
        # config's values. A banner that reports different settings from the
        # ones in force is worse than no banner.
        simulation = dict(self.options.get("simulation") or {})
        self._presenter.banner(
            System=self.system,
            Output=str(self.output_dir),
            Version=__version__,
            **{key: str(value) for key, value in simulation.items()
               if isinstance(value, (int, float, str, bool))},
        )

        # The banner already shows system/output to the user; this log
        # is for the file/audit trail and verbose console only.
        logger.debug(
            "FastMDXplora initialized: system=%s output=%s", self.system, self.output_dir
        )

    # ------------------------------------------------------------------
    # Orchestration entry point
    # ------------------------------------------------------------------
    def explore(
        self,
        *,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        options: dict[str, dict[str, Any]] | None = None,
        report: bool = True,
        dry_run: bool = False,
        force: bool = False,
    ) -> list[RunResult]:
        """Run the full pipeline, end to end.

        Parameters
        ----------
        include : list of str, optional
            Phases to run (subset of {"setup", "simulation", "analysis", "report"}).
            If omitted, all phases run.
        exclude : list of str, optional
            Phases to skip. Mutually exclusive with ``include``.
        options : dict, optional
            Per-phase option overrides applied on top of the orchestrator's
            ``options`` attribute.
        report : bool, default True
            Convenience flag. If False, skip the report phase even when
            ``include``/``exclude`` would otherwise enable it.
        dry_run : bool, default False
            If True, print the plan — every run, its system, swept values,
            output directory, and the phases that would execute — and
            return without running anything.

        Returns
        -------
        list[RunResult]
            One :class:`RunResult` per run, always. A single study is a
            list of one; a sweep is a list of many. Each ``RunResult``
            carries its per-phase :class:`PhaseResult` list in ``.phases``.

        Notes
        -----
        When ``include`` / ``exclude`` are omitted here but were set in a
        config file passed to the constructor, the config-file values are
        used. Explicit arguments to this method always win.
        """
        # This call's results, and only this call's. `self.results` was set
        # in __init__ and appended to here, so a second explore() on the same
        # object inherited the first one's phase records -- and since the
        # returned status is "error if any result is an error", a retry after
        # a failed setup came back as an error with everything succeeding.
        # The manifest survived it (the writer de-duplicates by phase name),
        # so the wrong answer reached only the Python API's return value,
        # which is the API's only signal. Retrying on the same object is the
        # obvious idiom for an interface the README advertises as
        # `FastMDXplora(config="study.yml").explore()`.
        self.results = []

        # Config-driven runs (one system or many) go through the batch
        # machinery internally. The user always sees the same FastMDXplora
        # interface; the batch layer is an implementation detail.
        if self._config_path is not None or self._config_data is not None:
            return self._explore_config(
                include=include, exclude=exclude, report=report, dry_run=dry_run,
                force=force,
            )

        # Config-file phase selection is the fallback when this call omits it.
        if include is None and exclude is None:
            include = self._config_include
            exclude = self._config_exclude

        plan = self._build_plan(include=include, exclude=exclude, want_report=report)

        # Dry run: report the plan and return without executing.
        if dry_run:
            self._print_dry_run_single(plan)
            return [RunResult(
                run_id="s1", system=self.system, status="planned",
                output_dir=self.output_dir, phases=[],
            )]

        if not dry_run:
            self._refuse_to_overwrite(plan, force=force)

        merged_options = self._merge_options(options)
        dashboard_writer = self._dashboard_writer(merged_options, plan)
        if dashboard_writer is not None:
            self._initialize_dashboard_timeline(dashboard_writer, plan)

        # Remember the resolved phase selection + merged options so the
        # resolved_config.yml dump reflects what *actually* ran (including
        # any per-call overrides), not just the construction-time config.
        self._resolved_include = include
        self._resolved_exclude = exclude
        self._resolved_options = {
            p: opts for p, opts in merged_options.items() if opts
        }

        # Plan goes to file/audit; the presenter shows headers visually.
        logger.debug("Plan: %s", " -> ".join(plan))

        # Before the expensive part, not after. The setup phase says these
        # things too, and by then the person who would have changed
        # something has walked away -- on a cluster, gone home. Said here
        # they are still choices.
        self._say_what_is_worth_knowing(merged_options)
        for phase in plan:
            self._mark_dashboard_phase_start(
                dashboard_writer, phase, merged_options.get(phase, {})
            )
            self._presenter.phase_start(phase)
            result = self._run_phase(phase, merged_options.get(phase, {}))
            self.results.append(result)
            self._presenter.phase_end(phase, status=result.status)
            self._mark_dashboard_phase_end(dashboard_writer, phase, result)
            if result.status == "error":
                logger.error("Phase '%s' failed: %s", phase, result.message)
                break

        if dashboard_writer is not None:
            failed = next((item for item in self.results if item.status == "error"), None)
            dashboard_writer.write_status(
                status="failed" if failed else "completed",
                latest_error=failed.message if failed else None,
            )
            dashboard_writer.event(
                "FastMDXplora exploration failed" if failed else "FastMDXplora exploration completed",
                level="error" if failed else "info",
            )

        self._write_manifest()
        self._write_resolved_config()
        # Only now can the bundle hold them. It is built during the report
        # phase, and these two are written once every phase has finished, so
        # the archive went out with the outputs and without the record of what
        # produced them or the file that reproduces it -- a recipient got 13 MB
        # of results and no way to trace them. The bundle is refreshed rather
        # than rebuilt: everything else in it is already correct.
        self._add_run_record_to_bundle()
        self._presenter.done()

        # Wrap the phase results into a single RunResult (a study of one).
        status = "error" if any(r.status == "error" for r in self.results) else "ok"
        return [RunResult(
            run_id="s1",
            system=self.system,
            status=status,
            output_dir=self.output_dir,
            phases=list(self.results),
        )]

    def _print_dry_run_single(self, plan: list[str]) -> None:
        """Print the plan for a single study without running it."""
        print("\nFastMDXplora dry run (no execution)")
        print("=" * 40)
        print(f"  system:  {self.system}")
        print(f"  output:  {self.output_dir}")
        print(f"  phases:  {' → '.join(plan) if plan else '(none)'}")

    def _explore_config(
        self,
        *,
        include: list[str] | None,
        exclude: list[str] | None,
        report: bool,
        dry_run: bool = False,
        force: bool = False,
    ) -> list[RunResult]:
        """Run a config-driven study through the internal batch machinery.

        Handles one system or many identically. Exposed to the user only as
        ``FastMDXplora(config=...).explore()``; the batch layer underneath
        is private.
        """
        from fastmdxplora.batch import BatchExplorer

        batch = BatchExplorer(
            config=self._config_path,
            config_data=self._config_data,
            output_dir=self._deferred_output_dir,
            verbose=self._deferred_verbose,
            force=force,
        )
        # explore()-level phase overrides win over the config file.
        if include is not None:
            batch._raw["include"] = include
            batch._raw["exclude"] = None
        elif exclude is not None:
            batch._raw["exclude"] = exclude
            batch._raw["include"] = None
        if not report:
            existing = batch._raw.get("exclude") or []
            if "report" not in existing and not batch._raw.get("include"):
                batch._raw["exclude"] = [*existing, "report"]

        if dry_run:
            run_results = batch.dry_run()
            self.output_dir = batch.output_dir
            self.results = run_results
            return run_results

        run_results = batch.run()
        # Surface the resolved output location for callers that read it.
        self.output_dir = batch.output_dir
        self.results = run_results
        return run_results

    # Convenience: per-phase entry points (also called by the CLI)
    def setup(self, **kwargs: Any) -> PhaseResult:
        """Run only the setup phase."""
        return self._run_phase("setup", kwargs)

    def simulate(self, **kwargs: Any) -> PhaseResult:
        """Run only the simulation phase."""
        return self._run_phase("simulation", kwargs)

    def analyze(self, **kwargs: Any) -> PhaseResult:
        """Run only the analysis phase."""
        return self._run_phase("analysis", kwargs)

    def report(self, **kwargs: Any) -> PhaseResult:
        """Run only the report phase."""
        return self._run_phase("report", kwargs)

    def compare(self, *, output_dir: str | os.PathLike | None = None) -> Path | None:
        """(Re)build the cross-run comparison report for a multi-run study.

        A multi-run ``explore()`` builds this automatically; call this to
        regenerate it — for example after re-running some of the runs, or
        to produce it for a batch that finished earlier.

        Parameters
        ----------
        output_dir : str | os.PathLike, optional
            The batch output directory to read (the one containing
            ``batch_manifest.json``). Defaults to this object's
            ``output_dir`` — i.e. the study it just ran.

        Returns
        -------
        Path or None
            The ``comparison/`` directory, or None if there was nothing to
            compare (fewer than two successful runs, or no analysis
            outputs were found).
        """
        from fastmdxplora.batch.compare import build_comparison_report

        target = Path(output_dir) if output_dir is not None else getattr(
            self, "output_dir", None
        )
        if target is None:
            raise StudyError(
                "compare() needs an output directory — pass output_dir=, or "
                "call it after explore() so the run's output is known."
            , code="config.option.missing_companion")
        return build_comparison_report(target)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _dashboard_writer(
        self,
        merged_options: dict[str, dict[str, Any]] | None = None,
        plan: list[str] | None = None,
    ):
        """Return the project-level telemetry writer when a dashboard is active."""

        simulation_options = (
            merged_options or self.options
        ).get("simulation", {})

        dashboard_active = (
            os.getenv("FASTMDX_DASHBOARD_ACTIVE") == "1"
        )
        dashboard_output = os.getenv("FASTMDX_DASHBOARD_OUTPUT")

        dashboard_matches_run = False

        if dashboard_active and dashboard_output:
            try:
                dashboard_matches_run = (
                    Path(dashboard_output).expanduser().resolve()
                    == self.output_dir.expanduser().resolve()
                )
            except (OSError, RuntimeError):
                dashboard_matches_run = False

        # Resolved the way the simulation runner resolves it, through the
        # schema, rather than by reading the config dict directly. Merged
        # options carry only what a config states, so a run leaving
        # `live_telemetry` at its default got telemetry from the runner and no
        # project-level writer here -- and this writer is the only thing that
        # marks the setup phase, marks analysis and report, and records that
        # the run finished. Without it a completed run described itself as
        # running with stale telemetry, and Setup never lit on the timeline.
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        field = PHASE_SCHEMAS.get("simulation", {}).get("live_telemetry")
        default = bool(getattr(field, "default", False))
        # Telemetry lives under `simulation/`, so a run that does not
        # simulate should not create that folder or claim to have recorded
        # anything. An analysis-only run watched from the browser still gets a
        # writer, because then somebody is reading it.
        simulates = plan is None or "simulation" in plan
        active = bool(
            (simulation_options.get("live_telemetry", default) and simulates)
            or dashboard_matches_run
        )

        if not active:
            return None

        try:
            from fastmdxplora.gui.telemetry import TelemetryWriter

            return TelemetryWriter(
                self.output_dir / "simulation",
                enabled=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Could not initialize dashboard phase telemetry: %s",
                exc,
            )
            return None

    @staticmethod
    def _initialize_dashboard_timeline(
        writer: Any,
        plan: list[str],
    ) -> None:
        states = {
            "setup": "waiting" if "setup" in plan else "skipped",
            "minimization": (
                "waiting" if "simulation" in plan else "skipped"
            ),
            "nvt": "waiting" if "simulation" in plan else "skipped",
            "npt": "waiting" if "simulation" in plan else "skipped",
            "production": (
                "waiting" if "simulation" in plan else "skipped"
            ),
            "analysis": "waiting" if "analysis" in plan else "skipped",
            "report": "waiting" if "report" in plan else "skipped",
        }

        writer.write_status(
            stage=plan[0] if plan else "completed",
            status="running" if plan else "completed",
            stage_states=states,
        )
        writer.event("Workflow timeline initialized")

    @staticmethod
    def _mark_dashboard_phase_start(
        writer: Any,
        phase: str,
        options: dict[str, Any],
    ) -> None:
        if writer is None:
            return

        if phase == "simulation":
            first_stage = (
                "minimization"
                if options.get("minimize", True)
                else "nvt"
            )
            writer.mark_stage(
                first_stage,
                "current",
                status="running",
            )
        else:
            writer.mark_stage(
                phase,
                "current",
                status="running",
            )

        writer.event(f"{phase.title()} phase started")

    @staticmethod
    def _mark_dashboard_phase_end(
        writer: Any,
        phase: str,
        result: PhaseResult,
    ) -> None:
        if writer is None:
            return

        if result.status == "ok":
            state = "completed"
        elif result.status == "skipped":
            state = "skipped"
        else:
            state = "failed"

        if phase == "simulation":
            from fastmdxplora.gui.telemetry import read_status

            status = read_status(writer.root.parent)
            stages = (
                status.get("stage_states")
                if isinstance(status, dict)
                else {}
            )
            stages = stages if isinstance(stages, dict) else {}

            for name in (
                "minimization",
                "nvt",
                "npt",
                "production",
            ):
                current_state = str(
                    stages.get(name, "waiting")
                ).lower()

                if current_state in {"waiting", "current"}:
                    writer.mark_stage(
                        name,
                        state,
                        status=(
                            "failed"
                            if state == "failed"
                            else "running"
                        ),
                    )
        else:
            writer.mark_stage(
                phase,
                state,
                status=(
                    "failed"
                    if state == "failed"
                    else "running"
                ),
                latest_error=(
                    result.message
                    if state == "failed"
                    else None
                ),
            )

        writer.event(
            f"{phase.title()} phase {state}",
            level="error" if state == "failed" else "info",
        )
        
    def _build_plan(
        self,
        *,
        include: list[str] | None,
        exclude: list[str] | None,
        want_report: bool,
    ) -> list[str]:
        if include is not None and exclude is not None:
            raise StudyError("Specify either `include` or `exclude`, not both.", code="config.option.conflicting")

        if include is not None:
            unknown = set(include) - set(PHASES)
            if unknown:
                raise StudyError(f"Unknown phase(s): {sorted(unknown)}. Valid: {PHASES}", code="config.phase.unknown")
            plan = [p for p in PHASES if p in include]
        elif exclude is not None:
            unknown = set(exclude) - set(PHASES)
            if unknown:
                raise StudyError(f"Unknown phase(s): {sorted(unknown)}. Valid: {PHASES}", code="config.phase.unknown")
            plan = [p for p in PHASES if p not in exclude]
        else:
            plan = list(PHASES)

        if not want_report and "report" in plan:
            plan.remove("report")

        return plan

    def _refuse_to_overwrite(self, plan: list[str], *, force: bool) -> None:
        """Stop a second run from writing over the first one's output.

        Nothing prevented it. A run started into a directory that already held
        one overwrote the science files as it produced them, appended to the
        metrics CSV and the event log, and merged its status forward from the
        previous run's -- so the page showed one run's platform and step count
        against another's charts, with the two traces drawn as one.

        Only the phases this run will produce are checked, so the phase-by-
        phase workflow still works: `analyze` into a directory holding a
        finished `simulation` is exactly the intended use.
        """
        if force:
            return
        # One construction path defers the output directory; nothing has been
        # written yet in that case, so there is nothing to refuse over.
        if getattr(self, "output_dir", None) is None:
            return
        occupied = [
            phase
            for phase in plan
            if (self.output_dir / phase).is_dir()
            and any((self.output_dir / phase).iterdir())
        ]
        if not occupied:
            return
        raise OutputExistsError(
            f"{self.output_dir} already holds output from "
            f"{', '.join(occupied)}. Choose another --output directory, "
            f"delete this one, or pass --force-overwrite to overwrite it."
        , code="environment.path.exists")

    def _merge_options(
        self, override: dict[str, dict[str, Any]] | None
    ) -> dict[str, dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {p: dict(self.options.get(p, {})) for p in PHASES}
        if override:
            for phase, opts in override.items():
                if phase not in PHASES:
                    raise StudyError(
                        f"Unknown phase '{phase}' in options. Valid: {PHASES}"
                    , code="config.phase.unknown")
                merged[phase].update(opts)
        return merged

    def _say_what_is_worth_knowing(self, options: dict[str, Any]) -> None:
        """Warn about what this run will do, while it can still be changed.

        Not a refusal, and nothing here stops a run: every case describes
        something that runs perfectly well and produces a result worth
        doubting. A validator answers "will this run", which is a different
        question and already answered elsewhere.

        The structure is read as text -- a couple of milliseconds, no OpenMM
        -- so this costs nothing beside a run of any length.
        """
        try:
            from fastmdxplora.advisories import advise
            from fastmdxplora.structure_info import count_structure
        except ImportError:  # pragma: no cover - a trimmed install
            return

        settings: dict[str, Any] = {}
        for phase in ("setup", "simulation"):
            block = options.get(phase)
            if isinstance(block, dict):
                settings.update(block)

        structure: dict[str, Any] = {}
        source = getattr(self, "system", None)
        if source:
            candidate = Path(str(source))
            if candidate.is_file():
                try:
                    structure = dict(count_structure(candidate))
                except Exception:  # noqa: BLE001 - a structure that will not read
                    structure = {}

        try:
            found = advise(structure, settings)
        except Exception:  # noqa: BLE001 - advice must never stop a run
            return

        for said in found:
            logger.warning("%s %s %s", said.summary, said.detail, said.remedy)

    @staticmethod
    def _phase_provenance() -> dict[str, Any]:
        """What produced a phase: version, host, and package environment."""
        import socket

        from fastmdxplora import __version__
        from fastmdxplora.provenance import environment_record

        try:
            host = socket.gethostname()
        except OSError:  # pragma: no cover - hostname is never load-bearing
            host = ""
        return {
            "version": __version__,
            "host": host,
            "environment": environment_record(),
        }

    @contextmanager
    def _marking(self, phase: str):
        """Stamp this phase's figures if nothing checked its method.

        One place rather than four, because the mark is a property of the
        phase and not of any figure inside it. The alternative -- a `mark`
        argument carried from here through `analyze.run`, the analysis
        orchestrator, each `Analysis`, and on to every `save_figure` call
        -- is five layers and ten call sites, and the parameter it would
        arrive at has been there all along with nothing passing it.

        Wrapped so a phase that cannot draw at all still runs. Matplotlib
        is an analysis dependency, and the setup phase must not start
        failing on an import it never needed.
        """
        mark = ""
        try:
            from fastmdxplora.marking import mark_for

            mark = mark_for(
                {**self.options, **getattr(self, "study_options", {})}, phase
            )
        except Exception:  # noqa: BLE001 -- a run is worth more than a stamp
            logger.debug("Could not work out the mark for phase %s.", phase)
        if not mark:
            yield
            return
        try:
            from fastmdxplora.analysis.plotting import marked_as
        except Exception:  # noqa: BLE001 -- no matplotlib, no figures
            yield
            return
        with marked_as(mark):
            yield

    def _run_phase(self, phase: str, kwargs: dict[str, Any]) -> PhaseResult:
        phase_dir = self._phase_dirs[phase]
        phase_dir.mkdir(parents=True, exist_ok=True)

        started = datetime.now(timezone.utc).isoformat()
        logger.debug("--> Phase '%s' starting (output=%s)", phase, phase_dir)

        try:
            run_fn = self._resolve_phase_runner(phase)
            with self._marking(phase):
                artifacts = run_fn(
                    orchestrator=self,
                    output_dir=phase_dir,
                    **kwargs,
                )
            finished = datetime.now(timezone.utc).isoformat()
            return PhaseResult(
                name=phase,
                status="ok",
                output_dir=phase_dir,
                started_at=started,
                finished_at=finished,
                message=f"Phase '{phase}' completed.",
                artifacts=list(artifacts or []),
                produced_by=self._phase_provenance(),
            )
        except Exception as exc:  # noqa: BLE001 -- we log and record
            finished = datetime.now(timezone.utc).isoformat()
            # The reason is reported once, by whoever is driving: the explore
            # loop below, or the per-phase command. Logging it here as well
            # produced two lines, the first of which said nothing useful.
            # The traceback stays available under --verbose.
            logger.debug("Phase '%s' raised an exception", phase, exc_info=True)
            return PhaseResult(
                name=phase,
                status="error",
                output_dir=phase_dir,
                started_at=started,
                finished_at=finished,
                message=str(exc),
                # Total by construction: an exception from a raise site that
                # has not been coded yet, or from a dependency, comes back
                # as `unclassified` carrying its own message. So the field
                # is always there and its resolution improves as the
                # migration proceeds, rather than appearing and disappearing.
                refusal=refusal_of(exc).as_dict(),
                produced_by=self._phase_provenance(),
            )

    @staticmethod
    def _resolve_phase_runner(phase: str):
        """Look up the run() entry point for a given phase.

        Each phase package exposes a ``run(orchestrator, output_dir, **kwargs)``
        callable; the orchestrator imports it lazily so that an optional
        backend (e.g. OpenMM) is only required when its phase is invoked.
        """
        if phase == "setup":
            from fastmdxplora.setup.pipeline import run

            return run
        if phase == "simulation":
            from fastmdxplora.simulation.pipeline import run

            return run
        if phase == "analysis":
            from fastmdxplora.analysis.analyze import run

            return run
        if phase == "report":
            from fastmdxplora.report import run

            return run
        raise StudyError(f"Unknown phase: {phase}", code="config.phase.unknown")

    def _add_run_record_to_bundle(self) -> None:
        """Put the manifest and the resolved config into the bundle.

        Quietly: a bundle that could not be updated is not a reason to fail a
        study that has otherwise finished, and the files themselves are on
        disk beside it either way.
        """
        import zipfile

        bundle = self.output_dir / "report" / "project_bundle.zip"
        if not bundle.is_file():
            return

        wanted = [name for name in ("manifest.json", "resolved_config.yml")
                  if (self.output_dir / name).is_file()]
        if not wanted:
            return

        try:
            with zipfile.ZipFile(bundle, "a", zipfile.ZIP_DEFLATED) as archive:
                held = set(archive.namelist())
                for name in wanted:
                    if name not in held:
                        archive.write(self.output_dir / name, name)
        except (OSError, zipfile.BadZipFile) as exc:  # pragma: no cover
            logger.warning("Could not add the run record to the bundle: %s", exc)

    def _write_manifest(self) -> None:
        """Write a single JSON manifest summarizing this session."""
        from fastmdxplora import __citation__, __doi__, __version__

        from fastmdxplora.provenance import (
            environment_record,
            source_provenance,
        )

        manifest_path = self.output_dir / "manifest.json"
        previous: dict[str, Any] = {}
        if manifest_path.is_file():
            try:
                with manifest_path.open(encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    previous = loaded
            except (OSError, json.JSONDecodeError):
                # A malformed or unreadable previous manifest must not stop
                # the phase from recording its own result.
                previous = {}

        phase_records: dict[str, dict[str, Any]] = {}
        phase_order: list[str] = []
        previous_phases = previous.get("phases", [])
        if not isinstance(previous_phases, list):
            previous_phases = []
        for record in previous_phases:
            if not isinstance(record, dict) or not record.get("name"):
                continue
            name = str(record["name"])
            phase_records[name] = record
            if name not in phase_order:
                phase_order.append(name)
        current_phases = [result.to_dict() for result in self.results]
        for record in current_phases:
            name = str(record["name"])
            if name not in phase_records:
                phase_order.append(name)
            phase_records[name] = record

        previous_options = previous.get("options", {})
        options = dict(previous_options) if isinstance(previous_options, dict) else {}
        options.update(self.options)

        # The top-level `version`, `environment` and `source` below describe
        # the session writing this file. Once phases from earlier sessions
        # are preserved -- which is the whole point of the merge above --
        # those fields no longer describe every phase beneath them. Each
        # phase now carries its own `produced_by`, and where more than one
        # version appears the manifest says so here, so that a reader who
        # checks only the top of the file is not told a single version
        # produced the lot.
        versions_seen: list[str] = []
        for name in phase_order:
            produced = phase_records[name].get("produced_by")
            version = (produced or {}).get("version") if isinstance(produced, dict) else None
            if version and version not in versions_seen:
                versions_seen.append(str(version))
        if __version__ not in versions_seen:
            versions_seen.append(__version__)

        manifest = {
            "tool": "FastMDXplora",
            "version": __version__,
            # Where the package was imported from a checkout, which commit.
            # The version string is written at install time, so an editable
            # install carries whatever it was when pip was last run: a real
            # study came back stamped 2.3.0 for a run using a feature 2.3.0
            # did not have. Absent for an installed copy, where the version
            # is the whole answer because the distribution was built from a
            # tag.
            "source": source_provenance(),
            # The stack that produced the numbers, not merely the one that
            # could have. Without it a result that changes between machines
            # can be demonstrated and not explained.
            "environment": environment_record(),
            "doi": __doi__,
            "citation": __citation__,
            "system": self.system,
            "output_dir": str(self.output_dir),
            "phases": [phase_records[name] for name in phase_order],
            "options": options,
        }
        # How each phase was written. Per phase rather than one summary,
        # because "partly unvalidated" tells a reader to distrust the whole
        # study, and the point of the per-phase setting is that they need
        # only distrust some of it. Omitted entirely for a study a person
        # wrote, which is most of them and every one before this existed.
        try:
            from fastmdxplora.config.agent_modes import resolve_agent_modes

            # `options` is per-phase; the study-level value sits beside it.
            # Passing only the phase blocks left `study` permanently None,
            # so a config saying `agent: assisted` at the top and nothing
            # per phase recorded no `agent` block at all -- and one that
            # also said `setup: {agent: assisted}` recorded that phase as
            # *departing* from a study value the resolver could not see.
            modes = resolve_agent_modes({**options, **self.study_options})
            if modes.study is not None or modes.departures:
                manifest["agent"] = modes.as_record()
        except Exception:  # noqa: BLE001 - a manifest is worth writing anyway
            logger.debug("Could not record how this study was written.")
        if len(versions_seen) > 1:
            manifest["versions_seen"] = versions_seen
            manifest["version_note"] = (
                "This manifest holds phases produced by more than one "
                "version. `version` above is the session that wrote the "
                "file; each phase records its own under `produced_by`."
            )
        with manifest_path.open("w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
        logger.debug("Wrote manifest: %s", manifest_path)

    def _write_resolved_config(self) -> None:
        """Write the fully-merged configuration for reproducibility.

        Produces ``resolved_config.yml`` capturing the system, output,
        phase selection, and per-phase options actually used. The file is
        a valid FastMDXplora config — feeding it back to ``--config``
        reproduces the run.
        """
        from fastmdxplora.config import write_resolved_config

        resolved = {
            "system": self.system,
            "output": str(self.output_dir),
            "verbose": self.verbose,
            "include": getattr(self, "_resolved_include", None) or self._config_include,
            "exclude": getattr(self, "_resolved_exclude", None) or self._config_exclude,
            "options": getattr(self, "_resolved_options", None) or self.options,
            # How the study was written travels with it. The config that
            # comes back out has to say the same thing the one that went in
            # did, or re-running an agent-written study produces a record
            # claiming a person wrote it.
            **dict(getattr(self, "study_options", {}) or {}),
        }
        try:
            path = write_resolved_config(resolved, self.output_dir)
            logger.debug("Wrote resolved config: %s", path)
        except Exception as exc:  # noqa: BLE001 -- never fail a run over this
            logger.debug("Could not write resolved config: %s", exc)

    def _configure_logging(self) -> None:
        """Wire up console and file logging for this project session.

        The root ``fastmdx`` logger is set to DEBUG so all records flow to
        the handlers; each handler then applies its own level filter. The
        file handler always captures at DEBUG (full audit trail). The
        console handler defaults to INFO, raised to DEBUG when
        ``verbose=True`` or ``FASTMDX_LOGLEVEL=DEBUG`` is set.
        """
        from fastmdxplora.utils.logging import attach_file_logger, set_level, setup_console

        console_level = logging.DEBUG if self.verbose else logging.INFO
        setup_console(level=console_level)
        attach_file_logger(self.output_dir / "fastmdxplora.log", level=logging.DEBUG)
        # Root logger must be at the lowest handler level so records flow.
        set_level(logging.DEBUG)
        # ...but the console handler still applies its own filter.
        # set_level above promoted ALL handlers to DEBUG; re-apply the
        # console-level filter so quiet mode stays quiet.
        from fastmdxplora.utils.logging import _console_handler

        if _console_handler is not None:
            _console_handler.setLevel(console_level)

    def _configure_presenter(self):
        """Create the session presenter for structural output.

        The presenter is silent when ``FASTMDX_LOG_STYLE=plain`` (handled
        internally by :class:`SessionPresenter`) or when stdout is not a
        TTY (handled by color auto-detection). Users wanting different
        behaviour can replace ``self._presenter`` after construction.

        This returns the process-wide presenter rather than a new instance.
        The startup banner is shown once per presenter, so constructing a
        second one made it print twice: once from the CLI and again when the
        orchestrator started a study.
        """
        from fastmdxplora.utils.presenter import get_presenter

        return get_presenter()


RUN_PROCESS_FILE = ".fastmdxplora_run.json"


def _record_run_process(output_dir: Path) -> None:
    import atexit
    import json
    import os
    import sys

    path = Path(output_dir) / RUN_PROCESS_FILE
    try:
        path.write_text(json.dumps({
            "pid": os.getpid(),
            "argv": list(sys.argv),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }), encoding="utf-8")
    except OSError:
        return

    def _remove(p: Path = path, pid: int = os.getpid()) -> None:
        # Only this process's record: a child that inherited this hook must not
        # remove the parent's.
        try:
            if os.getpid() == pid and p.is_file():
                p.unlink()
        except OSError:
            pass

    atexit.register(_remove)
