"""The parts of the cross-tool comparison that run without the reference.

Its ProLIF-driven commands cannot execute here: the reference tools are
deliberately not dependencies, and there is no finished run in a test
environment. What can be exercised is everything the ten rounds of
hardening went into -- finding artifacts whose recorded paths belong to
another machine, reading a table whose format nobody wrote down, and
turning per-pair rows into a residue-level bracket. Each of those was a
cluster-side failure, and each is a pure function.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fastmdxplora.validation.cross_tool import (
    HARMONIZED_PARAMETERS,
    _numeric_column,
    _occupancy_from_csv,
    find_artifact,
    load_manifest,
    our_kind_family,
    topology_has_resname,
    residue_label,
    trajectory_and_topology,
    unmeasured_interaction_families,
)


class TestJoiningTwoToolsTables:
    """Neither tool writes a residue the way the other does."""

    @pytest.mark.parametrize("raw,expected", [
        ("ASP189", "ASP189"), ("ASP 189 A", "ASP189"), ("ASP189.A", "ASP189"),
        ("ASP189-OD1", "ASP189"), ("asp189", "ASP189"), ("TRP215.A", "TRP215"),
    ])
    def test_every_decoration_reduces_to_one_label(self, raw, expected):
        assert residue_label(raw) == expected

    def test_something_unrecognisable_is_passed_through(self):
        """Rather than dropped, which would silently shorten a table."""
        assert residue_label("LIG") == "LIG"

    @pytest.mark.parametrize("kind,family", [
        ("hbond_donor", "hbond"), ("HydrogenBond", "hbond"),
        ("salt_bridge", "salt_bridge"), ("Ionic", "salt_bridge"),
        ("pi_stacking", "pi_stacking"), ("Hydrophobic", "hydrophobic"),
        ("XBond_halogen", "halogen"), ("MetalDonor", "metal"),
    ])
    def test_kinds_map_onto_families(self, kind, family):
        assert our_kind_family(kind) == family

    def test_an_unfamiliar_kind_is_excluded_rather_than_guessed(self):
        """A water bridge is not any of the families being compared.

        Mapping it onto the nearest one would put a row in the table that
        the other tool never produced.
        """
        assert our_kind_family("water_bridge") is None
        assert our_kind_family("something novel") is None

    def test_harmonized_hydrophobic_cutoff_matches_native_rule(self):
        assert HARMONIZED_PARAMETERS["Hydrophobic"]["distance"] == pytest.approx(4.0)

    def test_native_not_measured_families_are_read_from_options(self, tmp_path):
        adir = tmp_path / "analysis" / "pl_interactions"
        adir.mkdir(parents=True)
        (adir / "options.json").write_text(json.dumps({
            "findings": {
                "not_measured": {
                    "salt_bridge": "charge ambiguous",
                    "pi_cation": "charge ambiguous",
                }
            }
        }), encoding="utf-8")
        assert unmeasured_interaction_families(tmp_path) == {
            "salt_bridge", "pi_cation"
        }

    def test_missing_or_malformed_options_are_not_measurements(self, tmp_path):
        assert unmeasured_interaction_families(tmp_path) == set()
        adir = tmp_path / "analysis" / "pl_interactions"
        adir.mkdir(parents=True)
        (adir / "options.json").write_text("{not-json", encoding="utf-8")
        assert unmeasured_interaction_families(tmp_path) == set()

    def test_topology_residue_presence_can_prove_ligand_removal(self, tmp_path):
        top = tmp_path / "trajectory_topology.pdb"
        top.write_text(
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000\nEND\n",
            encoding="utf-8",
        )
        assert not topology_has_resname(top, "BNZ")

        top.write_text(
            "HETATM    1  C1  BNZ A   1       0.000   0.000   0.000\nEND\n",
            encoding="utf-8",
        )
        assert topology_has_resname(top, "bnz")


class TestPeriodicLigandReimaging:
    @staticmethod
    def _stub_mdanalysis(monkeypatch):
        import sys
        import types

        import numpy as np

        mda = types.ModuleType("MDAnalysis")
        lib = types.ModuleType("MDAnalysis.lib")
        math = types.ModuleType("MDAnalysis.lib.mdamath")
        math.triclinic_vectors = lambda dimensions: np.diag(dimensions[:3])
        monkeypatch.setitem(sys.modules, "MDAnalysis", mda)
        monkeypatch.setitem(sys.modules, "MDAnalysis.lib", lib)
        monkeypatch.setitem(sys.modules, "MDAnalysis.lib.mdamath", math)

    def test_zero_box_is_a_no_op(self, monkeypatch):
        self._stub_mdanalysis(monkeypatch)
        import numpy as np

        from fastmdxplora.validation.cross_tool import _ReimageLigand

        ligand = type("Atoms", (), {"positions": np.array([[9.0, 0.0, 0.0]])})()
        protein = type("Atoms", (), {"positions": np.array([[1.0, 0.0, 0.0]])})()
        ts = type("Timestep", (), {"dimensions": np.zeros(6)})()
        before = ligand.positions.copy()
        assert _ReimageLigand(ligand, protein)(ts) is ts
        assert np.array_equal(ligand.positions, before)

    def test_ligand_moves_to_the_triclinic_minimum_image(self, monkeypatch):
        self._stub_mdanalysis(monkeypatch)
        import numpy as np

        from fastmdxplora.validation.cross_tool import _ReimageLigand

        ligand = type("Atoms", (), {"positions": np.array([[9.0, 0.0, 0.0]])})()
        protein = type("Atoms", (), {"positions": np.array([[1.0, 0.0, 0.0]])})()
        ts = type("Timestep", (), {
            "dimensions": np.array([10.0, 10.0, 10.0, 90.0, 90.0, 90.0])
        })()
        _ReimageLigand(ligand, protein)(ts)
        assert ligand.positions[0, 0] == pytest.approx(-1.0)


class TestFindingArtifactsAfterTheFerry:
    """A manifest records the cluster's absolute paths.

    They exist nowhere on the laptop the run was copied to, which is how
    the first real comparison failed. An absolute path is re-rooted under
    the local directory by everything after the run's own name in it.
    """

    def _run(self, tmp_path: Path) -> Path:
        run = tmp_path / "benchmark-3ptb-bound"
        (run / "simulation").mkdir(parents=True)
        (run / "simulation" / "production.dcd").write_bytes(b"x")
        (run / "setup").mkdir()
        (run / "setup" / "topology.pdb").write_text("ATOM\n", encoding="utf-8")
        return run

    def test_a_cluster_path_is_re_rooted(self, tmp_path):
        run = self._run(tmp_path)
        manifest = {"artifacts": {"traj": (
            "/scratch/aaina/projects/x/benchmark-3ptb-bound/simulation/"
            "production.dcd")}}
        found = find_artifact(run, manifest, "production", ".dcd")

        assert found is not None
        assert found == run / "simulation" / "production.dcd"

    def test_a_relative_path_is_tried_as_given(self, tmp_path):
        run = self._run(tmp_path)
        manifest = {"traj": "simulation/production.dcd"}
        assert find_artifact(run, manifest, ".dcd") == (
            run / "simulation" / "production.dcd")

    def test_a_recorded_path_that_does_not_exist_is_not_returned(self, tmp_path):
        """So the caller falls through to the conventional layout.

        Returning a path that is merely recorded would fail later, further
        from the cause.
        """
        run = self._run(tmp_path)
        manifest = {"traj": "/elsewhere/gone/production.dcd"}
        assert find_artifact(run, manifest, "gone") is None

    def test_all_needles_must_match(self, tmp_path):
        run = self._run(tmp_path)
        manifest = {"a": "simulation/production.dcd"}
        assert find_artifact(run, manifest, "production", ".pdb") is None

    def test_the_conventional_layout_is_the_fallback(self, tmp_path, capsys):
        run = self._run(tmp_path)
        (run / "manifest.json").write_text(json.dumps({}), encoding="utf-8")
        traj, top = trajectory_and_topology(run, load_manifest(run))

        assert traj.name == "production.dcd"
        assert top.name == "topology.pdb"
        assert "fallback" in capsys.readouterr().out

    def test_a_directory_without_a_manifest_says_so(self, tmp_path):
        with pytest.raises(SystemExit, match="run directory"):
            load_manifest(tmp_path)


class TestReadingATableNobodyWroteDown:
    """The interaction table is `.dat`, comment-led, and pre-aggregated.

    Each of those was discovered by a failure: the extension is not `.csv`,
    the header may be behind a `#`, the numbers may be in scientific
    notation, and the rows are per atom pair rather than per frame.
    Reading it as per-frame records printed 100.0 for every pair that
    existed at all, which was the whole of the first deltas table.
    """

    def _write(self, tmp_path: Path, text: str) -> Path:
        path = tmp_path / "pl_interactions.dat"
        path.write_text(text, encoding="utf-8")
        return path

    def test_an_aggregated_table_becomes_a_residue_bracket(self, tmp_path):
        path = self._write(tmp_path, (
            "kind,ligand_atom,protein_atom,frames_present,frames_total,"
            "occupancy,episodes,standard_error,well_sampled,residue\n"
            "hydrophobic,3224,2724,804,2000,0.402,281,0.029,True,VAL213\n"
            "hydrophobic,3225,2724,601,2000,0.3005,279,0.027,True,VAL213\n"
            "hydrophobic,3223,2728,8,2000,0.004,5,0.028,True,VAL213\n"))
        occupancy = _occupancy_from_csv(path)

        value = occupancy[("VAL213", "hydrophobic")]
        low = value[0] if isinstance(value, tuple) else value
        assert low == pytest.approx(40.2, abs=0.1), (
            "the floor is the largest single pair, not the sum")

    def test_pairs_on_one_protein_atom_do_not_add(self, tmp_path):
        """Ring carbons touching one atom fire in the same frames.

        Summing them convicted a passing negative control at 24.8% where
        the union was about 3%.
        """
        path = self._write(tmp_path, (
            "kind,ligand_atom,protein_atom,frames_present,frames_total,"
            "occupancy,episodes,standard_error,well_sampled,residue\n"
            + "".join(
                f"hydrophobic,{3220 + i},2724,60,2000,0.03,50,0.02,True,LEU118\n"
                for i in range(6))))
        value = _occupancy_from_csv(path)[("LEU118", "hydrophobic")]
        high = value[1] if isinstance(value, tuple) else value

        assert high == pytest.approx(3.0, abs=0.2), (
            "six pairs on one atom are one contact, not six")


class TestPickingTheColumnThatHoldsTheNumbers:
    def test_a_headerless_column_is_read(self, tmp_path):
        path = tmp_path / "rmsd.dat"
        path.write_text("1.0e-01\n2.0e-01\n3.0e-01\n", encoding="utf-8")
        values = _numeric_column(path, "rmsd")
        assert len(values) == 3
        assert values[0] == pytest.approx(0.1)

    def test_a_commented_header_is_not_taken_for_data(self, tmp_path):
        """`#` leads the header, and its tokens are not floats.

        Detecting the header by trying to parse the first line is what
        makes scientific notation dangerous: 'e' looks like a name.
        """
        path = tmp_path / "rg.dat"
        path.write_text("# frame rg\n0 1.67\n1 1.68\n", encoding="utf-8")
        values = _numeric_column(path, "rg")
        assert len(values) == 2


class TestReadingPerFrameRecords:
    """The other shape the interaction record can arrive in.

    A JSON list of per-frame contacts is the shape a future per-frame
    export gives, and it is the one where a residue's occupancy is exact
    rather than bracketed, because the frames are there to be unioned.
    """

    def test_frames_are_unioned_per_residue(self):
        from fastmdxplora.validation.cross_tool import _occupancy_from_records

        records = (
            [{"frame": f, "kind": "hydrophobic", "residue": "LEU118"}
             for f in range(0, 40)]
            + [{"frame": f, "kind": "hydrophobic", "residue": "LEU118"}
               for f in range(20, 60)]
            + [{"frame": f, "kind": "hbond", "residue": "ASP189"}
               for f in range(0, 100)]
        )
        occupancy = _occupancy_from_records(records)

        # 100 distinct frames appear; LEU118 is present in 60 of them.
        assert occupancy[("LEU118", "hydrophobic")] == pytest.approx(60.0)
        assert occupancy[("ASP189", "hbond")] == pytest.approx(100.0)

    def test_records_under_a_key_are_found(self):
        from fastmdxplora.validation.cross_tool import _occupancy_from_records

        payload = {"contacts": [
            {"frame": 0, "kind": "salt_bridge", "residue": "ASP189"},
            {"frame": 1, "kind": "salt_bridge", "residue": "ASP189"},
        ]}
        assert _occupancy_from_records(payload)[
            ("ASP189", "salt_bridge")] == pytest.approx(100.0)

    def test_a_shape_it_does_not_recognise_yields_nothing(self):
        """So the caller tries the next candidate file.

        Returning a partial reading of an unfamiliar structure would put
        numbers in the table that no file actually contains.
        """
        from fastmdxplora.validation.cross_tool import _occupancy_from_records

        assert _occupancy_from_records({"summary": {"mean": 1.0}}) == {}
        assert _occupancy_from_records([{"kind": "hbond"}]) == {}
        assert _occupancy_from_records([]) == {}


class TestFindingTheLigand:
    """Which run had a ligand in it, asked of a finished run.

    The setup record answers it and the configuration cannot, because
    `setup.ligand_name` has a default. Once `resolved_config.yml` names
    every setting the run used -- which is the point of that file -- the
    default is in every config, apo runs included, and a comparison that
    reads it goes looking for a residue that was never prepared.
    """

    @staticmethod
    def _setup_record(run_dir, ligand):
        setup = run_dir / "setup"
        setup.mkdir(parents=True, exist_ok=True)
        record = {"resolved_forcefield": {"name": "amber14-all"}}
        if ligand is not None:
            record["resolved_forcefield"]["ligand"] = {"name": ligand}
        (setup / "setup_parameters.json").write_text(
            json.dumps(record), encoding="utf-8")

    @staticmethod
    def _config(run_dir, **setup_block):
        import yaml

        (run_dir / "resolved_config.yml").write_text(
            yaml.safe_dump({"setup": setup_block}), encoding="utf-8")

    def test_it_reads_the_setup_record(self, tmp_path):
        from fastmdxplora.validation.cross_tool import ligand_resname

        self._setup_record(tmp_path, "BEN")
        assert ligand_resname(tmp_path) == "BEN"

    def test_the_setup_record_beats_the_configuration(self, tmp_path):
        """One says what was asked for, the other what was prepared."""
        from fastmdxplora.validation.cross_tool import ligand_resname

        self._setup_record(tmp_path, "BEN")
        self._config(tmp_path, ligand_name="LIG")
        assert ligand_resname(tmp_path) == "BEN"

    def test_a_run_with_no_ligand_has_none(self, tmp_path):
        """Even though its configuration names the default one.

        This is the case that a full resolved config creates and a sparse
        one did not: `ligand_name: LIG` sits in an apo run's config
        because it sits in every run's config.
        """
        from fastmdxplora.validation.cross_tool import ligand_resname

        self._setup_record(tmp_path, None)
        self._config(tmp_path, ligand_name="LIG")
        with pytest.raises(SystemExit, match="--ligand"):
            ligand_resname(tmp_path)

    def test_a_default_alone_is_not_a_ligand(self, tmp_path):
        """With no setup record either -- a config that says nothing.

        A value equal to the declared default is what the file says about
        every run, so it distinguishes none of them.
        """
        from fastmdxplora.validation.cross_tool import ligand_resname

        self._config(tmp_path, ligand_name="LIG")
        with pytest.raises(SystemExit, match="--ligand"):
            ligand_resname(tmp_path)

    def test_an_older_run_still_reads_from_its_configuration(self, tmp_path):
        """Runs finished before setup wrote a record, and runs whose setup
        phase was skipped, have only the config to go on."""
        from fastmdxplora.validation.cross_tool import ligand_resname

        self._config(tmp_path, ligand_name="BEN")
        assert ligand_resname(tmp_path) == "BEN"

    def test_an_unreadable_setup_record_falls_back(self, tmp_path):
        from fastmdxplora.validation.cross_tool import ligand_resname

        (tmp_path / "setup").mkdir()
        (tmp_path / "setup" / "setup_parameters.json").write_text(
            "{not json", encoding="utf-8")
        self._config(tmp_path, ligand_name="BEN")
        assert ligand_resname(tmp_path) == "BEN"

    def test_without_one_it_says_what_to_pass(self, tmp_path):
        from fastmdxplora.validation.cross_tool import ligand_resname

        with pytest.raises(SystemExit, match="--ligand"):
            ligand_resname(tmp_path)


class TestTheReferenceToolsResolve:
    """The one thing the source-text checks cannot see.

    Every other test here reads the module and asserts a string is
    present. A helper that calls itself instead of importing satisfies all
    of them: the words are there, the imports are there, the pragma is
    there. It fails only when something calls it, which nothing in the
    suite did, because the commands that call it need ProLIF and a
    finished run.

    Shipped that way, and found by running the comparison on real data:
    992 frames of recursion instead of a trajectory.
    """

    def test_it_returns_the_two_tools(self):
        pytest.importorskip("MDAnalysis", reason="the [validation] extra")
        pytest.importorskip("prolif", reason="the [validation] extra")

        from fastmdxplora.validation.cross_tool import _reference_tools

        mda, plf = _reference_tools()
        assert mda.__name__ == "MDAnalysis"
        assert plf.__name__ == "prolif"

    def test_it_does_not_call_itself(self):
        """Called, which is the only way to see it. With the reference
        installed it returns the tools; without it, it says how to get them.
        A version that calls itself raises RecursionError either way, so
        this runs where the test above is skipped -- which is everywhere the
        [validation] extra is not installed, CI included."""
        from fastmdxplora.refusals import BackendUnavailable
        from fastmdxplora.validation.cross_tool import _reference_tools

        try:
            tools = _reference_tools()
        except BackendUnavailable as refused:
            assert refused.code == "environment.backend.missing"
            assert "pip install" in str(refused)
        else:
            assert [tool.__name__ for tool in tools] == ["MDAnalysis", "prolif"]

class TestTheExactResidueTableIsPreferred:
    """A bracket against another tool's point compares different things.

    The pair table can only bound a residue's occupancy from both sides;
    the union is the quantity. Once a run writes the union, the comparison
    must read it -- and alphabetically the pair table sorts first, so the
    preference has to be stated rather than left to the glob.
    """

    def _run(self, tmp_path, *, with_exact: bool):
        adir = tmp_path / "analysis" / "pl_interactions"
        adir.mkdir(parents=True)
        (adir / "pl_interactions.dat").write_text(
            "kind,ligand_atom,protein_atom,frames_present,frames_total,"
            "occupancy,episodes,standard_error,well_sampled,residue\n"
            + "".join(
                f"hydrophobic,{3220 + i},2724,400,2000,0.2,50,0.02,True,"
                "VAL213\n" for i in range(3)),
            encoding="utf-8")
        if with_exact:
            (adir / "pl_interactions_by_residue.dat").write_text(
                "residue,kind,frames_present,frames_total,occupancy,"
                "episodes,atom_pairs\n"
                "VAL213,hydrophobic,900,2000,0.45,120,3\n",
                encoding="utf-8")
        (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
        return tmp_path

    def test_the_union_is_read_where_it_exists(self, tmp_path):
        from fastmdxplora.validation.cross_tool import our_occupancy

        occupancy = our_occupancy(
            self._run(tmp_path, with_exact=True), {})
        value = occupancy[("VAL213", "hydrophobic")]

        assert not isinstance(value, tuple), "a union is a number"
        assert value == pytest.approx(45.0)

    def test_the_bracket_is_the_fallback(self, tmp_path):
        """A run made before the union was exported still compares."""
        from fastmdxplora.validation.cross_tool import our_occupancy

        occupancy = our_occupancy(
            self._run(tmp_path, with_exact=False), {})
        value = occupancy[("VAL213", "hydrophobic")]

        assert isinstance(value, tuple), (
            "without the union table the pair rows only bound the answer")
        assert value[0] == pytest.approx(20.0)
