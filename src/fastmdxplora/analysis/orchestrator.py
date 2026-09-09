"""Analysis-level orchestrator.

:class:`AnalysisOrchestrator` is the analysis-phase counterpart to the
project-level :class:`fastmdxplora.FastMDXplora` class. Its responsibility
is to coordinate the individual :class:`~fastmdxplora.analysis.base.Analysis`
modules: discover what's available, validate the user's options, execute
the chosen subset in order, capture results and errors, and write a single
phase-level manifest.

Architecturally the orchestrator follows a seven-phase pipeline
(Aina & Kwan, JCC 2026):

  1. Discovery — what analyses are available?
  2. Validation — does the user's options dict have the right shape?
  3. Planning — apply include/exclude to produce the execution list.
  4. Defaults — merge per-analysis defaults under user overrides.
  5. Filtering — match kwargs to each analysis's constructor signature.
  6. Execution — run each analysis sequentially, catching errors.
  7. Consolidation — write the manifest, return the result dict.

In FastMDXplora the orchestrator is constructed by the project-level
``FastMDXplora.analyze()`` method, but it also supports direct use as a
standalone class (see :class:`fastmdxplora.AnalysisOrchestrator`).
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import mdtraj as md

from fastmdxplora.analysis.base import Analysis, AnalysisResult
from fastmdxplora.config.schema import ANALYSIS
from fastmdxplora.analysis.loading import (
    PathLike,
    TrajectoryInput,
    load_trajectory,
)
from fastmdxplora.utils.logging import get_logger

logger = get_logger("analysis.orchestrator")


# ---------------------------------------------------------------------------
# Analysis scope
# ---------------------------------------------------------------------------
#: Valid analysis scopes. Scope resolves to a default atom selection used by
#: analyses that don't define their own, so analyses never run on the full
#: solvated system (water + ions) by accident.
_SCOPE_DEFAULT = next(
    f.default for f in ANALYSIS.fields if f.name == "scope"
)

VALID_SCOPES = next(
    f.choices for f in ANALYSIS.fields if f.name == "scope"
)


def _resolve_scope(scope: str, ligand_resname: str | None) -> str | None:
    """Resolve a scope name to a concrete MDTraj selection string.

    - ``solute``  : protein + ligand (no water/ions). The default. Falls back
                    to ``protein`` when there is no ligand.
    - ``protein`` : protein residues only.
    - ``ligand``  : the ligand residue(s) only (requires ``ligand_resname``).
    - ``all``     : no selection (operate on every atom) — escape hatch.
    """
    key = (scope or _SCOPE_DEFAULT).strip().lower()
    if key not in VALID_SCOPES:
        raise ValueError(
            f"Unknown analysis scope {scope!r}. Valid: {', '.join(VALID_SCOPES)}."
        )
    if key == "all":
        return None
    if key == "protein":
        return "protein"
    if key == "ligand":
        if not ligand_resname:
            raise ValueError(
                "scope='ligand' requires a ligand to be present, but no "
                "ligand residue name is known for this run."
            )
        return f"resname {ligand_resname}"
    # solute: protein + ligand if present, else protein.
    if ligand_resname:
        return f"protein or resname {ligand_resname}"
    return "protein"


# ---------------------------------------------------------------------------
# Analysis registry
# ---------------------------------------------------------------------------
# The registry is populated by importing fastmdxplora.analysis (which
# imports each analysis module, which registers itself). Keeping it as a
# module-level dict avoids circular-import gymnastics and makes discovery
# explicit. Subclasses of Analysis register themselves on import via
# register_analysis(name, cls).
#: Residue names for water, so an analysis needing solvent is not run on a
#: system that has none.
_WATER_RESIDUES = frozenset({"HOH", "WAT", "TIP", "TIP3", "SOL", "H2O"})

_REGISTRY: dict[str, type[Analysis]] = {}


def register_analysis(name: str, cls: type[Analysis]) -> None:
    """Register an analysis class under a short name.

    Called by each analysis module at import time. Idempotent — re-registering
    the same name with the same class is a no-op; re-registering a different
    class raises ``ValueError``.
    """
    existing = _REGISTRY.get(name)
    if existing is not None and existing is not cls:
        raise ValueError(
            f"Analysis name {name!r} is already registered to "
            f"{existing.__name__}; cannot rebind to {cls.__name__}."
        )
    _REGISTRY[name] = cls


def available_analyses() -> tuple[str, ...]:
    """Return the names of all registered analyses, in registration order."""
    return tuple(_REGISTRY.keys())


def get_analysis_class(name: str) -> type[Analysis]:
    """Look up a registered analysis class by name."""
    if name not in _REGISTRY:
        raise KeyError(
            f"Unknown analysis: {name!r}. Available: {list(_REGISTRY)}"
        )
    return _REGISTRY[name]


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
class AnalysisOrchestrator:
    """Coordinate the execution of trajectory analysis modules.

    The orchestrator loads the trajectory once at construction (matching the
    standard pattern) and holds it on ``self.traj``. Subsequent calls
    to :meth:`run` operate on that loaded trajectory.

    Parameters
    ----------
    trajectory : path, list of paths, or glob
        Trajectory file(s) to analyze. Passed verbatim to
        :func:`~fastmdxplora.analysis.loading.load_trajectory`.
    topology : path, optional
        Topology file. If omitted, auto-resolution is attempted (see
        :func:`load_trajectory`).
    output_dir : path, optional
        Where to write per-analysis subdirectories. Defaults to
        ``./fastmdx_analysis_<timestamp>``.
    selection : str, optional
        Default MDTraj selection string applied to every analysis that
        does not override it.
    stride, first, last : int, optional
        Frame-selection parameters applied at load time.

    Examples
    --------
    Run all registered analyses with defaults::

        from fastmdxplora.analysis import AnalysisOrchestrator

        ao = AnalysisOrchestrator("traj.dcd", topology="top.pdb")
        results = ao.run()

    Selectively run RMSD and Rg with custom RMSD options::

        results = ao.run(
            include=["rmsd", "rg"],
            options={"rmsd": {"ref": 0, "selection": "name CA"}},
        )

    Exclude expensive analyses on a quick first pass::

        results = ao.run(exclude=["cluster", "dimred"])
    """

    def __init__(
        self,
        trajectory: TrajectoryInput,
        topology: PathLike | None = None,
        *,
        output_dir: PathLike | None = None,
        selection: str | None = None,
        scope: str = "solute",
        ligand_resname: str | None = None,
        stride: int | None = None,
        first: int | None = None,
        last: int | None = None,
        saving_interval_ps: float | None = None,
    ) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.output_dir: Path = (
            Path(output_dir)
            if output_dir is not None
            else Path(f"fastmdx_analysis_{timestamp}")
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.default_selection: str | None = selection
        # Scope resolves to a concrete selection used as the default for
        # analyses that don't define their own (the solvent-blind ones), so
        # they never run on the full solvated system (water/ions). Analyses
        # with a meaningful own default (e.g. "name CA") keep it.
        self.scope: str = scope
        self.ligand_resname: str | None = ligand_resname
        self.scope_selection: str | None = _resolve_scope(scope, ligand_resname)

        # Cache the trajectory and the load-time parameters so the manifest
        # can record exactly what was analyzed.
        self._trajectory_input = trajectory
        self._topology_input = topology
        # The interval belongs in the recorded load parameters, not beside
        # them: it decides whether every time axis in this run is in
        # nanoseconds or in frames, and a manifest that does not say which
        # leaves a reader unable to tell the two apart afterwards.
        self._load_kwargs = {
            "stride": stride, "first": first, "last": last,
            "saving_interval_ps": saving_interval_ps,
        }

        logger.debug("AnalysisOrchestrator: loading trajectory...")
        self.traj: md.Trajectory = load_trajectory(
            trajectory, topology, stride=stride, first=first, last=last,
            saving_interval_ps=saving_interval_ps,
        )

        # Results from the most recent run() call.
        self.results: dict[str, AnalysisResult] = {}

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def run(
        self,
        *,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        options: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, AnalysisResult]:
        """Execute the planned analyses against ``self.traj``.

        Parameters
        ----------
        include : list of str, optional
            Subset of analysis names to run. Mutually exclusive with
            ``exclude``.
        exclude : list of str, optional
            Subset of analysis names to skip.
        options : dict, optional
            Per-analysis keyword arguments. Keys are analysis names
            (e.g. ``"rmsd"``); values are dicts forwarded to the analysis
            constructor. Unrecognized kwargs are silently dropped so the
            orchestrator can be safely called with a superset of options.

        Returns
        -------
        dict[str, AnalysisResult]
            Mapping from analysis name to result, in execution order.
            Also stored on ``self.results``.
        """
        plan = self._build_plan(include, exclude)
        merged_options = self._merge_options(plan, options)

        logger.debug("Plan: %s", ", ".join(plan))

        self.results = {}
        for name in plan:
            cls = get_analysis_class(name)
            raw_opts = dict(merged_options[name])
            # Supply the detected ligand residue name to ligand-aware analyses
            # (unless the user already set it). _filter_kwargs drops it for
            # analyses whose constructor doesn't accept it.
            if self.ligand_resname and "ligand_resname" not in raw_opts:
                raw_opts["ligand_resname"] = self.ligand_resname
            opts = self._filter_kwargs(cls, raw_opts)
            # Selection precedence: an explicit per-analysis selection wins;
            # then an orchestrator-wide `selection`; otherwise, if the
            # analysis has no meaningful default of its own (default_selection
            # is None — the solvent-blind analyses), fall back to the scope
            # selection so it never runs on the full solvated system. Analyses
            # that define their own default (e.g. "name CA") keep it.
            # An analysis that works out its own atoms -- a protein-ligand
            # measure derives both sides from the ligand's residue name, and
            # dihedrals from the backbone -- has nothing to apply a general
            # selection to. Passing one anyway was harmless only because they
            # ignored it, which is the reason it went unnoticed that the
            # setting did nothing.
            if "selection" not in opts and getattr(cls, "honours_selection", True):
                if self.default_selection is not None:
                    opts["selection"] = self.default_selection
                elif getattr(cls, "default_selection", None) is None:
                    opts["selection"] = self.scope_selection
            opts["output_dir"] = self.output_dir

            try:
                analysis = cls(**opts)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to instantiate analysis '%s'", name)
                self.results[name] = AnalysisResult(
                    name=name,
                    status="error",
                    message=f"instantiation failed: {exc}",
                )
                continue

            logger.debug("--> running analysis '%s'", name)
            # Its own trajectory, not the shared one. Superposition in
            # mdtraj rotates coordinates in place, so an analysis that
            # aligns leaves every later analysis reading rotated
            # coordinates -- and the result of one measure came to depend
            # on which others had run before it, which nothing in the
            # record could show. On a 20 ns trypsin-benzamidine run,
            # `pl_interactions` reported 252 hydrophobic contacts after
            # rmsf and ligand_rmsd had aligned the frames, and 10 when run
            # by itself. The 10 was right.
            self.results[name] = analysis.run(self.traj[:])

        self._reweight()
        self._write_manifest()
        return dict(self.results)

    def _reweight(self) -> None:
        """Correct the averages of a biased run, where a correction exists.

        Runs after the analyses rather than as one of them because it reads
        what they produced, and an analysis that depends on its siblings
        having already run would depend on the order they were registered in.

        A failure here must not lose the analyses: they are computed, on
        disk, and correct as biased-ensemble averages, which is what the
        methods text already calls them.
        """
        from fastmdxplora.analysis.reweighted_averages import reweight_results

        try:
            record = reweight_results(
                self.results,
                _REGISTRY,
                n_frames=int(self.traj.n_frames),
                frame_times_ps=getattr(self.traj, "time", []),
                output_dir=self.output_dir,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Reweighting failed")
            self.results["reweighted"] = AnalysisResult(
                name="reweighted",
                status="error",
                message=(
                    f"The bias could not be undone: {exc}. The averages above "
                    "stand as averages over the biased ensemble."),
            )
            return

        if record is None:
            return

        directory = self.output_dir / "reweighted"
        if not record.get("applies", True):
            # A biased run with no correction available. There is a record
            # because the averages need labelling, but no table and no figure.
            self.results["reweighted"] = AnalysisResult(
                name="reweighted",
                status="ok",
                data=record,
                output_dir=directory,
                artifacts=[directory / "reweighted_averages.json"],
                message=(
                    f"The bias from {record['biasing_method']} cannot be "
                    "undone, so the averages above are of the biased "
                    "ensemble and are labelled as such."),
            )
            return

        self.results["reweighted"] = AnalysisResult(
            name="reweighted",
            status="ok",
            data=record,
            output_dir=directory,
            data_path=directory / "reweighted_averages.dat",
            figure_path=directory / "reweighted_averages.png",
            artifacts=[directory / "reweighted_averages.json"],
            message=(
                f"{len(record['quantities'])} averages reweighted against the "
                f"deposited bias, on "
                f"{record['effective_sample_size']:.0f} effective frames of "
                f"{record['n_frames']}."),
        )

    # Convenience aliases for the standard names
    def analyze(
        self,
        *,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        options: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, AnalysisResult]:
        """Alias for :meth:`run`."""
        return self.run(include=include, exclude=exclude, options=options)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _build_plan(
        self,
        include: list[str] | None,
        exclude: list[str] | None,
    ) -> list[str]:
        all_names = list(_REGISTRY.keys())
        has_ligand = bool(self.ligand_resname)

        has_water = any(
            residue.name.upper() in _WATER_RESIDUES
            for residue in self.traj.topology.residues
        ) if getattr(self, "traj", None) is not None else True

        def _ligand_ok(name: str) -> bool:
            """Ligand-only analyses run by default only when a ligand exists."""
            cls = _REGISTRY[name]
            return has_ligand or not getattr(cls, "requires_ligand", False)

        def _umbrella_ok(name: str) -> bool:
            """An umbrella result exists only where an umbrella study ran.

            The free energy along the coordinate is what such a study is for,
            and it belongs with the analyses rather than as a JSON file
            nothing reads. But there is nothing to draw for an ordinary run,
            and an analysis that fails on every unbiased trajectory would
            turn a missing study into a failed phase.
            """
            cls = _REGISTRY[name]
            if not getattr(cls, "requires_umbrella", False):
                return True
            here = Path(self.output_dir)
            return any((parent / "pmf.json").is_file()
                       for parent in (here, here.parent, here.parent.parent))

        def _metadynamics_ok(name: str) -> bool:
            """A surface exists only where a metadynamics run produced one.

            Read from the record beside the run rather than from the config,
            so a study that asked for metadynamics and refused a surface is
            told apart from one that never ran it.
            """
            cls = _REGISTRY[name]
            if not getattr(cls, "requires_metadynamics", False):
                return True
            here = Path(self.output_dir)
            return any(
                (parent / "metadynamics_surface.json").is_file()
                or (parent / "simulation" / "metadynamics_surface.json").is_file()
                for parent in (here, here.parent))

        def _fold_ok(name: str) -> bool:
            """A fold analysis needs a chain long enough to have one.

            Q reports the fraction of native tertiary contacts retained, and
            a tripeptide has no residue pair far enough apart in sequence to
            make one. Reported as an error, that read as an analysis that
            broke rather than one that did not apply.
            """
            cls = _REGISTRY[name]
            if not getattr(cls, "requires_tertiary_structure", False):
                return True
            traj = getattr(self, "traj", None)
            if traj is None:
                return True
            separation = int(getattr(cls, "min_seq_separation", 4) or 4)

            # The solute's residues, not the box's. A solvated tripeptide has
            # 529 residues counting water, so counting them all let the gate
            # through and the analysis then sliced to the solute and found
            # three -- the exact number the gate was meant to catch. The
            # analysis's own comment says as much: it slices first because a
            # solvated system has thousands of water residues.
            try:
                protein = traj.topology.select("protein")
                residues = (traj.atom_slice(protein).n_residues
                            if len(protein) else traj.n_residues)
            except Exception:  # noqa: BLE001 - a selection this cannot make
                residues = traj.n_residues
            return residues > separation

        def _alignable(name: str) -> bool:
            """A superposition needs three atoms to be defined.

            Alanine dipeptide has one CA, which is what RMSD and RMSF align
            on by default. MDTraj printed "UNCONVERGED ROTATION MATRIX.
            RETURNING IDENTITY" once per frame from its C extension -- so a
            window's log filled with thousands of lines of it -- and returned
            distances measured against no alignment at all, which look like
            results and are not.

            Reported as inapplicable rather than attempted, in the same way a
            chain too short to have a fold is: the analysis did not break, it
            does not apply to a molecule this small.
            """
            cls = _REGISTRY[name]
            needed = int(getattr(cls, "min_atoms_to_align", 0) or 0)
            if needed <= 0:
                return True
            traj = getattr(self, "traj", None)
            if traj is None:
                return True
            selection = getattr(cls, "default_selection", None)
            if not selection:
                return True
            try:
                matched = len(traj.topology.select(selection))
            except Exception:  # noqa: BLE001 - a selection this cannot make
                return True
            return matched >= needed

        def _steered_ok(name: str) -> bool:
            """A pull's record exists only where a steered run produced one."""
            cls = _REGISTRY[name]
            if not getattr(cls, "requires_steered", False):
                return True
            here = Path(self.output_dir)
            return any(
                (parent / "steered_work.json").is_file()
                or (parent / "simulation" / "steered_work.json").is_file()
                for parent in (here, here.parent))

        def _amide_ok(name: str) -> bool:
            """An N--H order parameter needs the H.

            A structure prepared without hydrogens has no amide vector to
            measure, and neither does a united-atom model. Refusing at
            compute time turned "this system has no hydrogens" into a
            failed analysis rather than one the system does not pose a
            question for, which is the same category error the water gate
            above exists to avoid.
            """
            cls = _REGISTRY[name]
            if not getattr(cls, "requires_amide_hydrogens", False):
                return True
            traj = getattr(self, "traj", None)
            if traj is None:
                return True
            from fastmdxplora.analysis.order_parameters import amide_pairs
            try:
                return bool(amide_pairs(traj.topology))
            except Exception:
                return False

        def _box_ok(name: str) -> bool:
            """A g(r) divides by the bulk density, which needs a volume.

            A trajectory with no unit cell has none, and the curve that
            comes from assuming one is a histogram wearing the units of a
            distribution function. Left out rather than run and refused.
            """
            cls = _REGISTRY[name]
            if not getattr(cls, "requires_periodic_box", False):
                return True
            traj = getattr(self, "traj", None)
            if traj is None:
                return True
            return getattr(traj, "unitcell_lengths", None) is not None

        def _state_ok(name: str) -> bool:
            """Thermodynamics needs the state record, which only our own
            simulation phase writes. A trajectory imported from elsewhere
            brings coordinates and not the ensemble they came from, and
            that is a question it does not pose rather than one this fails
            to answer."""
            cls = _REGISTRY[name]
            if not getattr(cls, "requires_state_record", False):
                return True
            here = Path(getattr(self, "output_dir", ".") or ".")
            for parent in (here, here.parent, here.parent.parent):
                for candidate in (
                    parent / "simulation" / "energy.csv",
                    parent / "simulation" / "state_data.csv",
                    parent / "state_data.csv",
                    parent / "simulation" / "production_state.csv",
                ):
                    if candidate.is_file():
                        return True
            return False

        def _bfactor_ok(name: str) -> bool:
            """A comparison against B-factors needs a file that has them.

            A study started from a generated or minimised coordinate file
            writes zeros in that column, and a correlation against zeros is
            not a comparison. Same category as the water and amide gates:
            the system poses no question rather than failing to answer one.
            """
            cls = _REGISTRY[name]
            if not getattr(cls, "requires_crystallographic_bfactors", False):
                return True
            from fastmdxplora.analysis.bfactor_comparison import (
                has_crystallographic_bfactors)
            here = Path(getattr(self, "output_dir", ".") or ".")
            for parent in (here, here.parent, here.parent.parent):
                for candidate in (
                    parent / "setup" / "input.pdb",
                    parent / "setup" / "structure.pdb",
                    parent / "input.pdb",
                ):
                    if candidate.is_file() and has_crystallographic_bfactors(
                            candidate):
                        return True
            return False

        def _water_ok(name: str) -> bool:
            """Likewise for water. An analysis of where water sits has nothing
            to say about a system with none -- an implicit-solvent run, or a
            trajectory stripped of solvent to save space -- and refusing by
            default turned "there is no water here" into a failed phase."""
            cls = _REGISTRY[name]
            return has_water or not getattr(cls, "requires_water", False)

        if include is not None and exclude is not None:
            raise ValueError("Specify either `include` or `exclude`, not both.")

        if include is not None:
            unknown = [n for n in include if n not in _REGISTRY]
            if unknown:
                raise ValueError(
                    f"Unknown analyses in include: {unknown}. "
                    f"Available: {all_names}"
                )
            # Explicit include is honored as-is (even ligand analyses — they
            # will raise a clear error if no ligand is actually present).
            return [n for n in all_names if n in include]

        if exclude is not None:
            unknown = [n for n in exclude if n not in _REGISTRY]
            if unknown:
                raise ValueError(
                    f"Unknown analyses in exclude: {unknown}. "
                    f"Available: {all_names}"
                )
            return [
                n for n in all_names
                if n not in exclude and _ligand_ok(n) and _water_ok(n)
                and _amide_ok(n) and _bfactor_ok(n) and _state_ok(n)
                and _box_ok(n)
                and _umbrella_ok(n) and _metadynamics_ok(n)
                and _steered_ok(n) and _fold_ok(n)
                and _alignable(n)
            ]

        # Default plan: everything except ligand-only analyses when there is
        # no ligand. With a ligand, the ligand analyses run automatically.
        return [n for n in all_names
                if _ligand_ok(n) and _water_ok(n) and _amide_ok(n)
                and _bfactor_ok(n) and _state_ok(n) and _box_ok(n)
                and _umbrella_ok(n)
                and _metadynamics_ok(n) and _steered_ok(n)
                and _fold_ok(n) and _alignable(n)]

    def _merge_options(
        self,
        plan: list[str],
        override: dict[str, dict[str, Any]] | None,
    ) -> dict[str, dict[str, Any]]:
        """Per-analysis options dict, defaults under user overrides."""
        merged: dict[str, dict[str, Any]] = {name: {} for name in plan}
        if override:
            for name, opts in override.items():
                if name not in merged:
                    continue  # ignore options targeting excluded analyses
                if not isinstance(opts, dict):
                    raise ValueError(
                        f"options[{name!r}] must be a dict, got {type(opts).__name__}"
                    )
                self._reject_unknown_options(name, opts)
                merged[name].update(opts)
        return merged

    @staticmethod
    def _accepted_options(cls: type[Analysis]) -> set[str]:
        """Every option an analysis names, its own and the base class's."""
        accepted: set[str] = set()
        for klass in cls.__mro__:
            init = klass.__dict__.get("__init__")
            if init is None:
                continue
            for param in inspect.signature(init).parameters.values():
                if param.name == "self":
                    continue
                if param.kind in (
                    inspect.Parameter.KEYWORD_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                ):
                    accepted.add(param.name)
        return accepted

    @classmethod
    def _reject_unknown_options(cls, name: str, opts: dict[str, Any]) -> None:
        """Refuse a setting the analysis has no name for.

        Every analysis ends its signature with ``**kwargs``, which the base
        class stores and nothing reads. A misspelled option was therefore
        accepted, ignored, and the run reported success: asking for
        ``n_clusteres`` clustered at the default and said nothing. A setting
        the user wrote and the software did not apply is worse than a stopped
        run, because it looks like an answer to the question they asked.
        """
        analysis_cls = get_analysis_class(name)
        accepted = cls._accepted_options(analysis_cls)
        unknown = sorted(set(opts) - accepted)
        if not unknown:
            return
        raise ValueError(
            f"options[{name!r}] has no setting called "
            f"{', '.join(repr(u) for u in unknown)}. "
            f"{name} accepts: {', '.join(sorted(accepted))}."
        )

    @staticmethod
    def _filter_kwargs(
        cls: type[Analysis], kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        """Drop kwargs that the analysis constructor doesn't accept.

        Each Analysis subclass declares its own constructor signature.
        Unrecognized kwargs are dropped rather than passed through, because
        the **options sink only collects analysis-specific options — kwargs
        for one analysis (e.g. ``ref`` for RMSD) are not valid for another
        (e.g. RMSF). The base class accepts ``selection``, ``output_dir``,
        and **options, so anything documented in the subclass docstring
        survives the filter.
        """
        sig = inspect.signature(cls.__init__)
        accepted = set(sig.parameters.keys()) - {"self"}
        # If the subclass accepts **kwargs, pass everything through.
        if any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        ):
            return dict(kwargs)
        return {k: v for k, v in kwargs.items() if k in accepted}

    def _write_manifest(self) -> None:
        """Write the phase-level analysis manifest."""
        manifest = {
            "phase": "analysis",
            "trajectory_input": (
                str(self._trajectory_input)
                if not isinstance(self._trajectory_input, (list, tuple))
                else [str(p) for p in self._trajectory_input]
            ),
            "topology_input": (
                str(self._topology_input) if self._topology_input else None
            ),
            "load_kwargs": self._load_kwargs,
            "default_selection": self.default_selection,
            "n_frames": int(self.traj.n_frames),
            "n_atoms": int(self.traj.n_atoms),
            "n_residues": int(self.traj.n_residues),
            "plan": list(self.results.keys()),
            "results": {name: r.to_dict() for name, r in self.results.items()},
        }
        path = self.output_dir / "analysis_manifest.json"
        with path.open("w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
        logger.debug("Wrote analysis manifest: %s", path)
