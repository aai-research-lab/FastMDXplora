"""What goes into the trajectory, and what must go with it.

Water is nine tenths of a solvated system and nine tenths of the file. A
run that leaves it out is a tenth the size and can no longer answer any
question about the solvent, which is a trade worth making by default and
worth being able to undo.

The hazard is not the size. It is that a trajectory holding a subset, read
against the topology of the whole system, does not fail: it misaligns, and
every measurement afterwards is of the wrong atoms.
"""

from __future__ import annotations

import mdtraj as md
import pytest

from pathlib import Path

from fastmdxplora.simulation.runner import (
    resolve_save_selection,
    write_trajectory_topology,
)


def _solvated(n_residues=8, n_water=40):
    import numpy as np

    top = md.Topology()
    chain = top.add_chain()
    positions = []
    for i in range(n_residues):
        res = top.add_residue("ALA", chain, resSeq=i + 1)
        for k, (name, element) in enumerate((
                ("N", md.element.nitrogen), ("CA", md.element.carbon),
                ("C", md.element.carbon))):
            top.add_atom(name, element, res)
            positions.append([i * 0.38, 0.1 * k, 0.0])
    water = top.add_chain()
    for i in range(n_water):
        res = top.add_residue("HOH", water, resSeq=i + 1)
        top.add_atom("O", md.element.oxygen, res)
        positions.append([1.0 + 0.05 * i, 2.0, 0.0])
    return top, np.array(positions)


class TestWhichAtomsAreSaved:
    def test_the_default_leaves_the_solvent_out(self):
        top, _ = _solvated()
        kept, described = resolve_save_selection(top, "not water")

        assert kept is not None
        assert len(kept) == 24, "the protein's atoms and none of the water"
        assert "24 of 64" in described

    def test_all_keeps_everything_and_uses_openmm_s_own_reporter(self):
        top, _ = _solvated()
        for asked in ("all", None, "*"):
            kept, described = resolve_save_selection(top, asked)
            assert kept is None, "None means no subset, so nothing is sliced"
            assert described == "every atom"

    def test_a_selection_matching_everything_is_not_a_subset(self):
        """Slicing to the whole system would be work for no reason."""
        top, _ = _solvated(n_water=0)
        kept, _described = resolve_save_selection(top, "not water")
        assert kept is None

    def test_a_selection_that_excludes_nothing_says_so(self):
        """The quiet failure: it parses, it is wrong, and it still matches.

        `not resname WAT` on a system whose water is HOH keeps every atom.
        Reported as plain "every atom" it is indistinguishable from having
        asked for `all`, so a study that meant to drop the solvent gets a
        full-size trajectory with nothing to suggest why.
        """
        top, _ = _solvated()
        kept, described = resolve_save_selection(top, "not resname WAT")

        assert kept is None
        assert "excluded none" in described
        assert "resname WAT" in described

        # Asking for everything outright is not the same event and does
        # not carry the warning.
        _kept, plain = resolve_save_selection(top, "all")
        assert plain == "every atom"


class TestTheCasesThatMustNotRefuse:
    def test_a_box_of_pure_water(self):
        """A legitimate study: it is how a water model's density is checked.

        `not water` selects nothing there, and a default that refused would
        refuse the one study it cannot help with.
        """
        top = md.Topology()
        chain = top.add_chain()
        for i in range(20):
            res = top.add_residue("HOH", chain, resSeq=i + 1)
            top.add_atom("O", md.element.oxygen, res)

        kept, described = resolve_save_selection(top, "not water")
        assert kept is None
        assert "matched none" in described

    def test_a_topology_it_cannot_read(self):
        """Not a reason to fail a run that is otherwise fine.

        The trajectory is still writable; it just holds everything, and the
        sentence says why so nobody wonders at the file size.
        """
        kept, described = resolve_save_selection(object(), "not water")
        assert kept is None
        assert "could not be read" in described

    def test_a_leg_without_the_openmm_app_layer(self):
        """The failure that is invisible where OpenMM is installed.

        MDTraj raises ImportError, not TypeError, when `openmm.app` is
        absent, and this function runs on every simulation path -- so the
        exception missing from the fallback failed eight tests on the one
        CI leg without the `[md]` extra, none of them about saving a
        subset. Forced here, because a machine that has OpenMM cannot
        reach it by running the code.
        """
        from unittest import mock

        import mdtraj as md

        absent = ImportError(
            "The code at topology.py:430 requires the openmm.app module")
        with mock.patch.object(
                md.Topology, "from_openmm", side_effect=absent):
            kept, described = resolve_save_selection(object(), "not water")

        assert kept is None
        assert "could not be read" in described

    def test_a_selection_that_will_not_parse_does_refuse(self):
        """The one case here that should stop the run.

        A malformed selection is a mistake in the study file, fixable in
        one edit, and it would affect every frame. A valid selection that
        happens to match nothing is a fact about the system instead, which
        is why the two are handled differently.
        """
        top, _ = _solvated()
        with pytest.raises(ValueError, match="not a selection this can read"):
            resolve_save_selection(top, "protien")


class TestTheTopologyMatchesTheTrajectory:
    """The failure this exists to prevent is silent.

    A 24-atom trajectory read against a 64-atom topology does not raise;
    it lines the wrong atoms up with the wrong names.
    """

    def test_a_subset_gets_its_own_topology(self, tmp_path):
        top, positions = _solvated()
        kept, _ = resolve_save_selection(top, "not water")
        written = write_trajectory_topology(
            top, positions, tmp_path / "trajectory_topology.pdb", kept)

        assert written is not None and written.is_file()
        reloaded = md.load_pdb(str(written))
        assert reloaded.n_atoms == len(kept)
        assert {r.name for r in reloaded.topology.residues} == {"ALA"}

    def test_saving_everything_writes_no_second_topology(self, tmp_path):
        """Setup's own topology already describes it.

        A second copy would be a second thing to keep in step.
        """
        top, positions = _solvated()
        written = write_trajectory_topology(
            top, positions, tmp_path / "trajectory_topology.pdb", None)
        assert written is None
        assert not (tmp_path / "trajectory_topology.pdb").exists()


class TestTheStudyIsToldWhatItLost:
    def test_the_water_analysis_says_why_it_is_absent(self):
        """Rather than leaving a study to wonder where its directory went.

        Since the default strips solvent, the commonest cause is not that
        the run had no water but that it was not saved.
        """
        from fastmdxplora.analysis.water_sites import WaterSites

        assert WaterSites.requires_water is True
        assert "save_selection" in WaterSites.absent_because
        assert "`all`" in WaterSites.absent_because

    def test_the_schema_offers_it_with_the_trade_stated(self):
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        field = PHASE_SCHEMAS["simulation"].get("save_selection")
        assert field.default == "not water"
        assert "water-site analysis" in field.help


class TestEverythingThatReadsTheTrajectoryAgrees:
    """Writing the matching topology is half the job.

    A subset trajectory read against the prepared system's topology is an
    atom-count mismatch, which stops the analysis phase outright -- and
    where the counts happen to agree it is worse, because it lines the
    wrong atoms up with the wrong names. Every reader has to prefer the
    file that describes what was written.
    """

    @staticmethod
    def _run(tmp_path, *, subset: bool):
        sim = tmp_path / "simulation"
        sim.mkdir(parents=True, exist_ok=True)
        (sim / "topology.pdb").write_text("ATOM\n", encoding="utf-8")
        (sim / "production.dcd").write_bytes(b"")
        if subset:
            (sim / "trajectory_topology.pdb").write_text(
                "ATOM\n", encoding="utf-8")
        return tmp_path

    def test_the_analysis_phase_prefers_the_matching_topology(self, tmp_path):
        # A study that saved only its alpha carbons: analysed, it reads the
        # trajectory against the topology written beside it and runs.
        # Against the prepared system's topology it would not load at all.
        import importlib
        import json
        from types import SimpleNamespace

        analyze = importlib.import_module("fastmdxplora.analysis.analyze")
        root = _a_study_that_saved_a_subset(tmp_path)
        analyze.run(orchestrator=SimpleNamespace(output_dir=root, system="x", _presenter=None),
                    output_dir=root / "analysis", include=["rmsd"])
        manifest = json.loads((root / "analysis" / "analysis_manifest.json").read_text(
            encoding="utf-8"))
        assert manifest["results"]["rmsd"]["status"] == "ok"

    def test_the_playback_prefers_it_too(self, tmp_path):
        # The browser's copy holds the eight atoms that were written.
        import mdtraj as md

        from fastmdxplora.gui.trajectory_playback import playback_info

        root = _a_study_that_saved_a_subset(tmp_path)
        info = playback_info(root)
        assert info["playback_available"], info.get("reason")
        shown = md.load(str(root / "simulation" / info["companion_pdb"]))
        assert shown.n_atoms == 8 and {a.name for a in shown.topology.atoms} == {"CA"}

    def test_every_known_reader_prefers_it(self, tmp_path):
        """Named rather than found by scanning.

        A first version of this checked the source for any file mentioning
        both `production.dcd` and `topology.pdb`, which flagged a docstring
        example, the module that writes the file, and the setup path that
        reads its own input. A check with three false positives out of four
        is one that gets deleted rather than fixed, so the readers are
        listed and a new one joins this list. The two above run the analysis
        phase and the playback; the command line's and the cross-tool
        benchmark's lookups are asked here, with the subset topology and
        without it.
        """
        from fastmdxplora.cli.main import _infer_system_from_output
        from fastmdxplora.validation.cross_tool import trajectory_and_topology

        root = _a_study_that_saved_a_subset(tmp_path)
        assert Path(_infer_system_from_output(str(root))).name == "trajectory_topology.pdb"
        assert trajectory_and_topology(root, {})[1].name == "trajectory_topology.pdb"
        (root / "simulation" / "trajectory_topology.pdb").unlink()
        assert Path(_infer_system_from_output(str(root))).name == "topology.pdb"
        # The benchmark's own fallback is the prepared system's topology.
        assert trajectory_and_topology(root, {})[1] == root / "setup" / "topology.pdb"


def _a_study_that_saved_a_subset(root):
    """A finished study that kept only its alpha carbons: the prepared
    system's topology with the water, the trajectory of eight atoms, and
    the topology written beside it that describes them. Read against the
    wrong one, the counts do not agree and nothing loads."""
    import numpy as np

    top, xyz = _solvated()
    full = md.Trajectory(xyz[None].astype(np.float32), top)
    simulation = root / "simulation"
    simulation.mkdir(parents=True, exist_ok=True)
    full.save_pdb(str(simulation / "topology.pdb"))
    (root / "setup").mkdir(exist_ok=True)
    full.save_pdb(str(root / "setup" / "topology.pdb"))
    subset = full.atom_slice(full.topology.select("name CA"))
    subset.save_pdb(str(simulation / "trajectory_topology.pdb"))
    moved = subset.xyz + np.random.default_rng(0).normal(0, 0.05, (8, *subset.xyz.shape[1:]))
    md.Trajectory(moved.astype(np.float32), subset.topology).save_dcd(
        str(simulation / "production.dcd"))
    return root
