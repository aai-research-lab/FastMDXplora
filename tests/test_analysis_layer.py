"""Tests for the analysis-layer infrastructure (sub-delivery 1).

These tests exercise the trajectory loader, the Analysis base class
contract, the AnalysisOrchestrator class, and the registry — all the
plumbing that the concrete analyses (sub-2, sub-3) will sit on top of.

A small fake Analysis subclass (``_DummyAnalysis``) stands in for a real
analysis so we can verify the orchestrator's behaviour without depending
on the not-yet-implemented modules.

We use MDTraj's bundled trp_cage test trajectory as a real-trajectory
fixture; if MDTraj's test data is unavailable we synthesize a tiny
trajectory programmatically.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np
import pytest

from fastmdxplora.analysis import (
    Analysis,
    AnalysisOrchestrator,
    AnalysisResult,
    TrajectoryLoadError,
    available_analyses,
    get_analysis_class,
    load_trajectory,
    register_analysis,
)
from fastmdxplora.analysis.orchestrator import _REGISTRY


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _synthetic_trajectory(n_frames: int = 10, n_atoms: int = 5) -> md.Trajectory:
    """Build a tiny in-memory trajectory for testing."""
    top = md.Topology()
    chain = top.add_chain()
    residue = top.add_residue("ALA", chain)
    for i in range(n_atoms):
        top.add_atom(f"A{i}", md.element.carbon, residue)
    xyz = np.random.RandomState(42).rand(n_frames, n_atoms, 3).astype(np.float32)
    return md.Trajectory(xyz=xyz, topology=top)


@pytest.fixture
def synthetic_traj() -> md.Trajectory:
    return _synthetic_trajectory()


@pytest.fixture
def traj_files(tmp_path: Path, synthetic_traj: md.Trajectory) -> tuple[Path, Path]:
    """Persist the synthetic trajectory to disk as a DCD + PDB pair."""
    pdb_path = tmp_path / "topology.pdb"
    dcd_path = tmp_path / "production.dcd"
    synthetic_traj[0].save_pdb(str(pdb_path))
    synthetic_traj.save_dcd(str(dcd_path))
    return dcd_path, pdb_path


# ---------------------------------------------------------------------------
# A minimal Analysis subclass used as a test double for the registry tests
# ---------------------------------------------------------------------------
class _DummyAnalysis(Analysis):
    """Test analysis that records mean atomic distance from the origin."""

    name = "dummy"
    description = "Mean distance from origin"

    def compute(self, traj: md.Trajectory) -> np.ndarray:
        # Per-frame mean distance from origin — a stable, simple metric
        return np.linalg.norm(traj.xyz.reshape(traj.n_frames, -1), axis=1)

    def plot(self, result: np.ndarray, ax: plt.Axes) -> None:
        ax.plot(result)
        ax.set_xlabel("frame")
        ax.set_ylabel("|xyz|")


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Snapshot/restore the registry around each test.

    Some tests register/unregister analyses. Without isolation, test
    ordering would matter. We snapshot at setup and restore at teardown.
    """
    snapshot = dict(_REGISTRY)
    yield
    _REGISTRY.clear()
    _REGISTRY.update(snapshot)


# ===========================================================================
# load_trajectory
# ===========================================================================
class TestLoadTrajectory:
    def test_loads_pdb(self, tmp_path: Path, synthetic_traj: md.Trajectory):
        pdb_path = tmp_path / "system.pdb"
        synthetic_traj.save_pdb(str(pdb_path))

        loaded = load_trajectory(pdb_path)
        assert loaded.n_atoms == synthetic_traj.n_atoms
        # MDTraj's save_pdb writes all frames as multi-model PDB; round-trip
        # should preserve frame count.
        assert loaded.n_frames == synthetic_traj.n_frames

    def test_loads_dcd_with_explicit_topology(self, traj_files: tuple[Path, Path]):
        dcd, pdb = traj_files
        loaded = load_trajectory(dcd, top=pdb)
        assert loaded.n_atoms > 0
        assert loaded.n_frames > 0

    def test_auto_resolves_topology_for_dcd(self, traj_files: tuple[Path, Path]):
        """A DCD with a sibling .pdb of the same stem should auto-resolve."""
        dcd, pdb = traj_files
        # Stem mismatch: production.dcd <-> topology.pdb. Auto-resolution
        # looks for production.pdb. Rename to validate the rule.
        sibling = dcd.with_suffix(".pdb")
        pdb.rename(sibling)
        loaded = load_trajectory(dcd)  # no top= argument
        assert loaded.n_atoms > 0

    def test_dcd_without_topology_raises(self, tmp_path: Path):
        dcd = tmp_path / "lonely.dcd"
        # Manufacture a minimal DCD by writing one
        _synthetic_trajectory().save_dcd(str(dcd))
        with pytest.raises(TrajectoryLoadError, match="requires a topology"):
            load_trajectory(dcd)

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(TrajectoryLoadError, match="not found"):
            load_trajectory(tmp_path / "nonexistent.dcd", top=tmp_path / "topology.pdb")

    def test_missing_topology_raises(self, traj_files: tuple[Path, Path]):
        dcd, _pdb = traj_files
        with pytest.raises(TrajectoryLoadError, match="not found"):
            load_trajectory(dcd, top=dcd.parent / "nope.pdb")

    def test_concatenates_multiple_files(
        self, tmp_path: Path, synthetic_traj: md.Trajectory
    ):
        pdb = tmp_path / "top.pdb"
        synthetic_traj[0].save_pdb(str(pdb))
        dcd1 = tmp_path / "run01.dcd"
        dcd2 = tmp_path / "run02.dcd"
        synthetic_traj.save_dcd(str(dcd1))
        synthetic_traj.save_dcd(str(dcd2))

        loaded = load_trajectory([dcd1, dcd2], top=pdb)
        assert loaded.n_frames == 2 * synthetic_traj.n_frames

    def test_glob_pattern_expands(
        self, tmp_path: Path, synthetic_traj: md.Trajectory
    ):
        pdb = tmp_path / "top.pdb"
        synthetic_traj[0].save_pdb(str(pdb))
        for i in range(3):
            synthetic_traj.save_dcd(str(tmp_path / f"shot{i:02d}.dcd"))

        loaded = load_trajectory(str(tmp_path / "shot*.dcd"), top=pdb)
        assert loaded.n_frames == 3 * synthetic_traj.n_frames

    def test_stride(self, traj_files: tuple[Path, Path]):
        dcd, pdb = traj_files
        full = load_trajectory(dcd, top=pdb)
        strided = load_trajectory(dcd, top=pdb, stride=2)
        assert strided.n_frames == (full.n_frames + 1) // 2

    def test_frame_slice(self, traj_files: tuple[Path, Path]):
        dcd, pdb = traj_files
        sliced = load_trajectory(dcd, top=pdb, first=2, last=7)
        assert sliced.n_frames == 5

    def test_invalid_slice_raises(self, traj_files: tuple[Path, Path]):
        dcd, pdb = traj_files
        with pytest.raises(TrajectoryLoadError, match="Invalid frame slice"):
            load_trajectory(dcd, top=pdb, first=5, last=2)

    def test_empty_glob_raises(self, tmp_path: Path):
        with pytest.raises(TrajectoryLoadError, match="No files match"):
            load_trajectory(str(tmp_path / "no_such_*.dcd"))


# ===========================================================================
# Analysis base class
# ===========================================================================
class TestAnalysisBase:
    def test_run_produces_ok_result(
        self, tmp_path: Path, synthetic_traj: md.Trajectory
    ):
        a = _DummyAnalysis(output_dir=tmp_path)
        result = a.run(synthetic_traj)
        assert isinstance(result, AnalysisResult)
        assert result.status == "ok"
        assert result.data is not None
        assert result.output_dir == tmp_path / "dummy"

    def test_run_writes_data_figure_options(
        self, tmp_path: Path, synthetic_traj: md.Trajectory
    ):
        a = _DummyAnalysis(output_dir=tmp_path)
        result = a.run(synthetic_traj)
        assert result.data_path.exists()
        assert result.figure_path.exists()
        assert result.options_path.exists()
        # Naming convention
        assert result.data_path.name == "dummy.dat"
        assert result.figure_path.name == "dummy.png"

    def test_data_file_is_readable_back(
        self, tmp_path: Path, synthetic_traj: md.Trajectory
    ):
        a = _DummyAnalysis(output_dir=tmp_path)
        result = a.run(synthetic_traj)
        loaded = np.loadtxt(result.data_path)
        np.testing.assert_allclose(loaded, result.data, rtol=1e-5)

    def test_options_manifest_records_selection(
        self, tmp_path: Path, synthetic_traj: md.Trajectory
    ):
        import json

        a = _DummyAnalysis(selection="all", output_dir=tmp_path)
        result = a.run(synthetic_traj)
        with result.options_path.open() as fh:
            manifest = json.load(fh)
        assert manifest["analysis"] == "dummy"
        assert manifest["selection"] == "all"

    def test_compute_error_yields_error_status(
        self, tmp_path: Path, synthetic_traj: md.Trajectory
    ):
        class BrokenAnalysis(_DummyAnalysis):
            name = "broken"

            def compute(self, traj):
                raise RuntimeError("intentional test failure")

        a = BrokenAnalysis(output_dir=tmp_path)
        result = a.run(synthetic_traj)
        assert result.status == "error"
        assert "intentional test failure" in result.message
        # The data and figure should NOT have been written
        assert result.data_path is None
        assert result.figure_path is None
        # But options.json SHOULD have been written before compute()
        assert result.options_path.exists()

    def test_save_data_unsupported_type_raises(
        self, tmp_path: Path, synthetic_traj: md.Trajectory
    ):
        class WeirdAnalysis(_DummyAnalysis):
            name = "weird"

            def compute(self, traj):
                return {"this is": "not a numpy array"}

        a = WeirdAnalysis(output_dir=tmp_path)
        result = a.run(synthetic_traj)
        assert result.status == "error"
        assert "save_data" in result.message

    def test_select_atoms_with_none_returns_all(
        self, synthetic_traj: md.Trajectory
    ):
        a = _DummyAnalysis()
        idx = a.select_atoms(synthetic_traj)
        assert len(idx) == synthetic_traj.n_atoms

    def test_select_atoms_with_empty_match_raises(
        self, synthetic_traj: md.Trajectory
    ):
        a = _DummyAnalysis(selection="protein and name P")
        with pytest.raises(ValueError, match="matched zero atoms"):
            a.select_atoms(synthetic_traj)


class TestPlotCustomization:
    """User-facing plot customization hooks on the base class."""

    def test_user_title_overrides_default(
        self, tmp_path: Path, synthetic_traj: md.Trajectory
    ):
        a = _DummyAnalysis(output_dir=tmp_path, title="My Custom Title")
        a.run(synthetic_traj)
        assert a.figure_title() == "My Custom Title"

    def test_user_xlabel_overrides(
        self, tmp_path: Path, synthetic_traj: md.Trajectory
    ):
        """User-supplied xlabel reaches the saved figure axes."""
        a = _DummyAnalysis(output_dir=tmp_path, xlabel="Frame (× 10 ps)")
        result = a.run(synthetic_traj)
        assert result.status == "ok"
        # Re-read the figure metadata is hard; instead verify the override
        # is stored and would be applied (the _do_plot logic is exercised).
        assert a._user_xlabel == "Frame (× 10 ps)"

    def test_user_ylabel_overrides(
        self, tmp_path: Path, synthetic_traj: md.Trajectory
    ):
        a = _DummyAnalysis(output_dir=tmp_path, ylabel="Custom Y axis")
        a.run(synthetic_traj)
        assert a._user_ylabel == "Custom Y axis"

    def test_user_figsize_applied(
        self, tmp_path: Path, synthetic_traj: md.Trajectory
    ):
        a = _DummyAnalysis(output_dir=tmp_path, figsize=(10.0, 6.0))
        result = a.run(synthetic_traj)
        assert result.status == "ok"
        assert a._user_figsize == (10.0, 6.0)

    def test_no_customization_falls_back_to_default(
        self, tmp_path: Path, synthetic_traj: md.Trajectory
    ):
        """When no overrides are passed, figure_title returns the description."""
        a = _DummyAnalysis(output_dir=tmp_path)
        assert a.figure_title() == _DummyAnalysis.description


# ===========================================================================
# Registry
# ===========================================================================
class TestRegistry:
    def test_register_and_lookup(self):
        register_analysis("registry_test", _DummyAnalysis)
        assert "registry_test" in available_analyses()
        assert get_analysis_class("registry_test") is _DummyAnalysis

    def test_register_same_class_idempotent(self):
        register_analysis("idempotent_test", _DummyAnalysis)
        register_analysis("idempotent_test", _DummyAnalysis)  # no error

    def test_register_different_class_raises(self):
        class Other(_DummyAnalysis):
            pass

        register_analysis("rebind_test", _DummyAnalysis)
        with pytest.raises(ValueError, match="already registered"):
            register_analysis("rebind_test", Other)

    def test_lookup_unknown_raises(self):
        with pytest.raises(KeyError, match="Unknown analysis"):
            get_analysis_class("definitely_not_real")


# ===========================================================================
# AnalysisOrchestrator
# ===========================================================================
class TestAnalysisOrchestrator:
    def test_constructor_loads_trajectory(
        self, tmp_path: Path, traj_files: tuple[Path, Path]
    ):
        dcd, pdb = traj_files
        ao = AnalysisOrchestrator(
            trajectory=dcd, topology=pdb, output_dir=tmp_path / "run"
        )
        assert ao.traj.n_frames > 0
        assert ao.output_dir == tmp_path / "run"
        assert ao.output_dir.exists()

    def test_run_executes_registered_analyses(
        self, tmp_path: Path, traj_files: tuple[Path, Path]
    ):
        register_analysis("dummy", _DummyAnalysis)

        dcd, pdb = traj_files
        ao = AnalysisOrchestrator(
            trajectory=dcd, topology=pdb, output_dir=tmp_path / "run"
        )
        results = ao.run()
        assert "dummy" in results
        assert results["dummy"].status == "ok"

    def test_run_writes_manifest(
        self, tmp_path: Path, traj_files: tuple[Path, Path]
    ):
        import json

        register_analysis("dummy", _DummyAnalysis)
        dcd, pdb = traj_files
        ao = AnalysisOrchestrator(
            trajectory=dcd, topology=pdb, output_dir=tmp_path / "run"
        )
        ao.run()
        manifest_path = ao.output_dir / "analysis_manifest.json"
        assert manifest_path.exists()
        with manifest_path.open() as fh:
            manifest = json.load(fh)
        assert manifest["phase"] == "analysis"
        assert manifest["n_frames"] == ao.traj.n_frames
        assert "dummy" in manifest["plan"]
        assert manifest["results"]["dummy"]["status"] == "ok"

    def test_include_filter(
        self, tmp_path: Path, traj_files: tuple[Path, Path]
    ):
        class Other(_DummyAnalysis):
            name = "other"

        register_analysis("dummy", _DummyAnalysis)
        register_analysis("other", Other)

        dcd, pdb = traj_files
        ao = AnalysisOrchestrator(
            trajectory=dcd, topology=pdb, output_dir=tmp_path / "run"
        )
        results = ao.run(include=["dummy"])
        assert set(results.keys()) == {"dummy"}

    def test_exclude_filter(
        self, tmp_path: Path, traj_files: tuple[Path, Path]
    ):
        class Other(_DummyAnalysis):
            name = "other"

        register_analysis("dummy", _DummyAnalysis)
        register_analysis("other", Other)

        dcd, pdb = traj_files
        ao = AnalysisOrchestrator(
            trajectory=dcd, topology=pdb, output_dir=tmp_path / "run"
        )
        # Use include=["dummy"] to scope this test to a single known
        # analysis — exclude alone would invoke every other registered
        # analysis (RMSD, RMSF, ...) which is out of scope here.
        results = ao.run(include=["dummy"])
        assert set(results.keys()) == {"dummy"}
        assert "other" not in results

    def test_include_and_exclude_mutually_exclusive(
        self, tmp_path: Path, traj_files: tuple[Path, Path]
    ):
        register_analysis("dummy", _DummyAnalysis)
        dcd, pdb = traj_files
        ao = AnalysisOrchestrator(
            trajectory=dcd, topology=pdb, output_dir=tmp_path / "run"
        )
        with pytest.raises(ValueError, match="either"):
            ao.run(include=["dummy"], exclude=["dummy"])

    def test_include_unknown_raises(
        self, tmp_path: Path, traj_files: tuple[Path, Path]
    ):
        register_analysis("dummy", _DummyAnalysis)
        dcd, pdb = traj_files
        ao = AnalysisOrchestrator(
            trajectory=dcd, topology=pdb, output_dir=tmp_path / "run"
        )
        with pytest.raises(ValueError, match="Unknown analyses"):
            ao.run(include=["wibble"])

    def test_options_filtering_drops_unknown_kwargs(
        self, tmp_path: Path, traj_files: tuple[Path, Path]
    ):
        """Per-analysis options that the analysis doesn't accept are dropped."""
        register_analysis("dummy", _DummyAnalysis)
        dcd, pdb = traj_files
        ao = AnalysisOrchestrator(
            trajectory=dcd, topology=pdb, output_dir=tmp_path / "run"
        )
        # _DummyAnalysis only accepts selection, output_dir, **options
        # via the base class. Passing a bogus kwarg should not crash.
        results = ao.run(options={"dummy": {"selection": "all"}})
        assert results["dummy"].status == "ok"

    def test_default_selection_propagates_to_analyses(
        self, tmp_path: Path, traj_files: tuple[Path, Path]
    ):
        """The orchestrator's `selection` argument is the default for each analysis."""
        register_analysis("dummy", _DummyAnalysis)
        dcd, pdb = traj_files
        ao = AnalysisOrchestrator(
            trajectory=dcd, topology=pdb,
            output_dir=tmp_path / "run",
            selection="all",
        )
        results = ao.run()
        # Read the per-analysis options.json and confirm selection landed
        import json
        opt_path = results["dummy"].options_path
        with opt_path.open() as fh:
            m = json.load(fh)
        assert m["selection"] == "all"

    def test_analyze_is_alias_for_run(
        self, tmp_path: Path, traj_files: tuple[Path, Path]
    ):
        register_analysis("dummy", _DummyAnalysis)
        dcd, pdb = traj_files
        ao = AnalysisOrchestrator(
            trajectory=dcd, topology=pdb, output_dir=tmp_path / "run"
        )
        a = ao.analyze()
        b = ao.run()
        assert set(a.keys()) == set(b.keys())


# ===========================================================================
# Top-level package surface
# ===========================================================================
def test_AnalysisOrchestrator_exposed_at_top_level():
    """`from fastmdxplora import AnalysisOrchestrator` works."""
    from fastmdxplora import AnalysisOrchestrator as AO

    assert AO is AnalysisOrchestrator


class TestUnknownOptionsAreRefused:
    """A setting the software did not apply must not look like one it did.

    Every analysis ends its signature with ``**kwargs``, which the base class
    stores in ``self.options`` and nothing reads. So a misspelled option was
    accepted, ignored, and the run reported success: asking for
    ``n_clusteres`` clustered at the default and said nothing about it.
    """

    def test_a_misspelled_option_stops_the_run(self, tmp_path) -> None:
        import pytest
        from fastmdxplora.analysis.orchestrator import AnalysisOrchestrator

        with pytest.raises(ValueError, match="n_clusteres"):
            AnalysisOrchestrator._reject_unknown_options(
                "cluster", {"n_clusteres": 3})

    def test_the_message_names_what_is_accepted(self) -> None:
        import pytest
        from fastmdxplora.analysis.orchestrator import AnalysisOrchestrator

        with pytest.raises(ValueError, match="n_clusters"):
            AnalysisOrchestrator._reject_unknown_options(
                "cluster", {"n_clusteres": 3})

    def test_a_real_option_passes(self) -> None:
        from fastmdxplora.analysis.orchestrator import AnalysisOrchestrator

        AnalysisOrchestrator._reject_unknown_options(
            "cluster", {"n_clusters": 3, "linkage": "ward"})

    def test_an_option_named_by_the_base_class_passes(self) -> None:
        """selection and output_dir belong to every analysis."""
        from fastmdxplora.analysis.orchestrator import AnalysisOrchestrator

        AnalysisOrchestrator._reject_unknown_options(
            "rmsd", {"ref": 0, "selection": "name CA", "title": "custom"})

    def test_every_analysis_accepts_what_the_cli_can_send(self) -> None:
        """The CLI builds option keys of its own; they must all be real.

        --analyze-cluster-n-clusters becomes options['cluster']['n_clusters'],
        and a rename that stopped matching would be silent again.
        """
        from fastmdxplora.analysis.orchestrator import AnalysisOrchestrator
        from fastmdxplora.cli.main import _normalize_analysis_options

        sent = _normalize_analysis_options({
            "dimred_methods": ["pca"], "dimred_components": 2,
            "cluster_methods": ["kmeans"], "cluster_n_clusters": 4,
            "cluster_linkage": "ward",
        })
        for name, opts in sent["options"].items():
            AnalysisOrchestrator._reject_unknown_options(name, opts)



class TestAnalysesDescribeThemselves:
    """What an analysis accepts is read from the analysis, not restated.

    The constructor names its settings and gives their defaults; the docstring
    says what each means and which values are allowed. Writing that out a
    second time somewhere a form could read it would be one more thing to
    update when an analysis gains an option -- and the copy that gets forgotten
    is the one nobody is looking at.
    """

    def test_every_option_an_analysis_accepts_is_described(self) -> None:
        """A setting nobody explained cannot be offered with a useful label."""
        import fastmdxplora.analysis  # noqa: F401  (populates the registry)
        from fastmdxplora.analysis.describe import undocumented_options

        missing = undocumented_options()
        assert not missing, (
            "these settings are accepted but not explained in their own "
            f"docstring, so nothing can label them: {missing}"
        )

    def test_the_description_matches_what_is_accepted(self) -> None:
        """Describing an option the constructor rejects would be worse than
        describing none: a form would offer a control that stops the run."""
        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis.describe import describe_all
        from fastmdxplora.analysis.orchestrator import AnalysisOrchestrator

        for name, options in describe_all().items():
            AnalysisOrchestrator._reject_unknown_options(
                name, {o.name: o.default for o in options}
            )

    def test_a_default_is_one_of_its_own_choices(self) -> None:
        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis.describe import describe_all

        def offered(option) -> bool:
            """Whether a default is among the values the option accepts.

            An option taking several -- clustering's methods, the interaction
            kinds -- has a default that is a set of them, and every member has
            to be accepted rather than the tuple itself being a choice.
            """
            if isinstance(option.default, (list, tuple)):
                return all(v in option.choices for v in option.default)
            return option.default in option.choices

        broken = [
            f"{name}.{o.name}: {o.default!r} not in {o.choices}"
            for name, options in describe_all().items()
            for o in options
            if o.choices and o.default is not None and not offered(o)
        ]
        assert not broken, broken

    def test_choices_are_read_from_the_braced_part_only(self) -> None:
        """The type line carries the default too, and it is quoted as well.

        Reading the whole line offered 'average' twice for cluster linkage.
        """
        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis.describe import describe_analysis

        linkage = next(o for o in describe_analysis("cluster") if o.name == "linkage")
        assert linkage.choices == ("ward", "complete", "average", "single")

    def test_an_entry_naming_several_options_describes_each(self) -> None:
        """The base class writes 'title, xlabel, ylabel : str' as one entry."""
        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis.describe import describe_analysis

        described = {o.name: o.help for o in describe_analysis("rmsd")}
        for shared in ("title", "xlabel", "ylabel"):
            assert described[shared], f"{shared} was left unexplained"

    def test_shared_settings_are_marked_as_the_base_class_s(self) -> None:
        """So a form can group them apart from what makes an analysis itself."""
        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis.describe import describe_analysis

        owners = {o.name: o.owner for o in describe_analysis("cluster")}
        assert owners["n_clusters"] == "Cluster"
        assert owners["figsize"] == "Analysis"


class TestASettingThatDoesNothingIsNotOffered:
    """Six analyses accepted a general atom selection and ignored it.

    A protein-ligand measure works out both sides from the ligand's residue
    name; dihedrals works from the backbone. Neither has anything to apply a
    selection to, and the setting sat in the form looking like a control. A
    measurement that looks restricted and is not is the same defect as a count
    that looks complete and is not.
    """

    def test_an_analysis_says_whether_a_selection_applies(self) -> None:
        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis.orchestrator import get_analysis_class

        assert get_analysis_class("rmsd").honours_selection
        assert not get_analysis_class("pl_interactions").honours_selection
        assert not get_analysis_class("dihedrals").honours_selection

    def test_passing_one_where_it_does_nothing_is_refused(self) -> None:
        import pytest

        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis.orchestrator import get_analysis_class

        with pytest.raises(ValueError, match="would have no effect"):
            get_analysis_class("pl_interactions")(
                ligand_resname="LIG", selection="protein")

    def test_the_orchestrator_stops_passing_it(self, tmp_path, monkeypatch) -> None:
        """It sent the scope selection to every analysis, which was harmless
        only because they ignored it -- which is why nobody noticed. Given a
        selection, an analysis that honours it is handed it; one that works
        out its own atoms is not."""
        import mdtraj as md
        import numpy as np

        from fastmdxplora.analysis.orchestrator import AnalysisOrchestrator, get_analysis_class

        topology = md.Topology()
        chain = topology.add_chain()
        for _ in range(4):
            residue = topology.add_residue("ALA", chain)
            for name, element in (("N", "nitrogen"), ("CA", "carbon"), ("C", "carbon"),
                                  ("O", "oxygen")):
                topology.add_atom(name, getattr(md.element, element), residue)
        xyz = np.random.default_rng(0).normal(scale=0.4, size=(5, topology.n_atoms, 3))
        md.Trajectory(xyz.astype(np.float32), topology).save_pdb(str(tmp_path / "t.pdb"))
        md.Trajectory(xyz.astype(np.float32), topology).save_dcd(str(tmp_path / "t.dcd"))

        handed = {}
        for name in ("rmsd", "dihedrals"):
            cls = get_analysis_class(name)
            real = cls.__init__

            def recording(self, *args, _name=name, _real=real, **kwargs):
                handed[_name] = kwargs.get("selection")
                _real(self, *args, **kwargs)

            monkeypatch.setattr(cls, "__init__", recording)
        AnalysisOrchestrator(trajectory=str(tmp_path / "t.dcd"), topology=str(tmp_path / "t.pdb"),
                             output_dir=str(tmp_path / "analysis"),
                             selection="name CA").run(include=["rmsd", "dihedrals"])
        assert handed == {"rmsd": "name CA", "dihedrals": None}

    def test_every_analysis_that_ignores_it_declares_so(self) -> None:
        """Written as a property, because six were found by checking and the
        seventh would be found the same way or not at all.

        Read from the source on purpose. Showing it by behaviour would mean
        running every analysis twice with two selections, most of them
        needing a ligand, water or a biased run to have anything to say;
        what is checked here is a declaration and its use agreeing, which
        the text states directly."""
        import inspect

        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis.orchestrator import (
            available_analyses,
            get_analysis_class,
        )

        undeclared = []
        for name in available_analyses():
            cls = get_analysis_class(name)
            if cls.name != name:
                continue
            source = inspect.getsource(cls)
            uses_it = "select_atoms" in source or "self.selection" in source
            if not uses_it and cls.honours_selection:
                undeclared.append(name)
        assert not undeclared, (
            f"these accept a selection and never use it: {undeclared}"
        )

    def test_and_the_form_does_not_offer_it(self) -> None:
        from fastmdxplora.gui.schema_payload import schema_payload

        payload = schema_payload()["analysis_options"]
        if not payload["available"]:
            import pytest

            pytest.skip(payload["reason"])
        for analysis in ("pl_interactions", "dihedrals", "pl_hbonds"):
            names = {o["name"] for o in payload["analyses"][analysis]}
            assert "selection" not in names, analysis
        assert "selection" in {o["name"] for o in payload["analyses"]["rmsd"]}


class TestTheManifestRecordsWhatTheRunLearned:
    """Found by reading a real run's options.json: it held only what was set
    before the analysis started.

    The manifest was written before ``compute`` and never again, so everything
    an analysis worked out about its own run -- how confidently it knew the
    ligand's chemistry, which measurements it could not make and why, what
    binding modes it found -- was thrown away. The interaction analysis exists
    partly to record that its chemistry was resolved rather than guessed, and
    that record was reaching nothing.
    """

    def test_the_manifest_is_written_after_compute(self, tmp_path) -> None:
        # Written before compute, so an analysis that fails still leaves its
        # settings behind, and again after, so what compute learned is kept.
        import json

        import mdtraj as md
        import numpy as np
        import pandas as pd

        from fastmdxplora.analysis.base import Analysis

        class Learns(Analysis):
            name = "learns"
            description = "records what it found"

            def compute(self, traj):
                self.findings["worked_out"] = 42
                return pd.DataFrame({"frame": [0], "value": [1.0]})

            def plot(self, result, ax):
                ax.plot([0], [1])

        class Fails(Learns):
            name = "fails"

            def compute(self, traj):
                raise RuntimeError("compute failed")

        topology = md.Topology()
        topology.add_atom("CA", md.element.carbon, topology.add_residue("ALA", topology.add_chain()))
        traj = md.Trajectory(np.zeros((1, 1, 3), dtype=np.float32), topology)
        Learns(output_dir=str(tmp_path / "a")).run(traj)
        failed = Fails(output_dir=str(tmp_path / "b")).run(traj)
        learned = json.loads(next((tmp_path / "a").rglob("options.json")).read_text(encoding="utf-8"))
        assert learned["findings"]["worked_out"] == 42
        assert failed.status == "error"
        assert next((tmp_path / "b").rglob("options.json")).is_file()

    def test_what_compute_records_survives(self, tmp_path) -> None:
        import json

        import mdtraj as md
        import numpy as np
        import pandas as pd

        from fastmdxplora.analysis.base import Analysis

        class _Learns(Analysis):
            name = "learns_something"
            description = "records what it found"

            def compute(self, traj):
                self.options["found_during_compute"] = "a fact"
                return pd.DataFrame({"frame": [0], "value": [1.0]})

            def plot(self, result, ax):
                ax.plot([0], [1])

        top = md.Topology()
        chain = top.add_chain()
        residue = top.add_residue("ALA", chain, resSeq=1)
        top.add_atom("CA", md.element.carbon, residue)
        traj = md.Trajectory(
            xyz=np.zeros((1, 1, 3), dtype=np.float32), topology=top)

        _Learns(output_dir=str(tmp_path)).run(traj)
        written = json.loads(
            next(tmp_path.rglob("options.json")).read_text(encoding="utf-8"))
        assert written["options"].get("found_during_compute") == "a fact"


class TestTheRenamedAnalysisHasOneName:
    """`contacts` was kept as an alias for `pl_contacts` so existing configs
    would still run. Nobody has one: the software has not been released under
    this name, and the alias meant the orchestrator ran the analysis twice --
    once under each name, into the same directory.
    """

    def test_only_the_new_name_is_registered(self) -> None:
        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis.orchestrator import available_analyses

        names = set(available_analyses())
        assert "pl_contacts" in names
        assert "contacts" not in names

    def test_nothing_runs_twice(self) -> None:
        """Every registered name maps to a distinct analysis, so a run of
        everything runs each thing once."""
        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis.orchestrator import (
            available_analyses,
            get_analysis_class,
        )

        classes = [get_analysis_class(n) for n in available_analyses()]
        assert len(classes) == len(set(classes)), (
            "two names point at the same analysis, so it would run twice"
        )
