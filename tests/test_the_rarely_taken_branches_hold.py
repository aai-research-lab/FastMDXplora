"""The branches of 1040 to 1059 that the rest of the suite does not reach.

Each is a way a record, a file or a molecule can be other than the usual:
unreadable, of the wrong shape, missing, or chemically unlike a
phospholipid. They are the branches most likely to break unseen, because
nothing an ordinary study does takes them, and codecov measured them as the
added lines no test ran. Each is checked for what it answers, not only that
it runs.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")


# ---------------------------------------------------------------------------
# The batch explorer's records
# ---------------------------------------------------------------------------


class TestASharedSystemsRecords:

    def test_a_structure_that_cannot_be_read_has_no_digest(self, tmp_path, monkeypatch) -> None:
        from fastmdxplora.batch.explorer import _file_digest

        structure = tmp_path / "x.pdb"
        structure.write_text("END\n")

        def unreadable(self):
            raise PermissionError("no")

        monkeypatch.setattr(Path, "read_bytes", unreadable)
        assert _file_digest(structure) is None

    def test_a_setup_record_without_its_settings_says_nothing(self, tmp_path) -> None:
        from fastmdxplora.batch.explorer import _what_it_was_prepared_for

        (tmp_path / "setup").mkdir()
        (tmp_path / "setup" / "setup_parameters.json").write_text(
            json.dumps({"parameters": "not a mapping"}))
        assert _what_it_was_prepared_for(tmp_path) is None

    @pytest.mark.parametrize("first, second, same", [
        ("amber14", "amber14", True), ({"a": 1}, {"a": 1}, True),
        ("amber14", "charmm36", False), (7, 7.0, True), (True, 1, False)])
    def test_settings_are_compared_as_values(self, first, second, same) -> None:
        from fastmdxplora.batch.explorer import _same_setting

        assert _same_setting(first, second) is same


def _explorer(**attributes):
    from fastmdxplora.batch.explorer import BatchExplorer

    explorer = BatchExplorer.__new__(BatchExplorer)
    explorer.__dict__.update({"_is_umbrella": False, "run_specs": [], **attributes})
    return explorer


class TestWhatADryRunSaysAboutSetup:

    def test_a_plan_without_setup_says_nothing_about_it(self) -> None:
        spec = SimpleNamespace(options={})
        assert _explorer()._what_setup_would_do(spec, ["simulation"]) == (False, "")

    def test_umbrella_windows_naming_nothing_stop_before_any(self, tmp_path) -> None:
        spec = SimpleNamespace(options={"simulation": {"setup_from": str(tmp_path / "gone")}})
        skips, said = _explorer(_is_umbrella=True)._what_setup_would_do(
            spec, ["setup", "simulation"])
        assert skips and "stops before any window" in said

    def test_runs_that_differ_are_listed_each_with_its_own(self, tmp_path, capsys) -> None:
        from fastmdxplora.batch.explorer import BatchExplorer

        prepared = tmp_path / "earlier" / "setup"
        prepared.mkdir(parents=True)
        for name in ("system.xml", "state.xml", "topology.pdb"):
            (prepared / name).write_text("x")
        structure = tmp_path / "x.pdb"
        structure.write_text("END\n")
        batch = BatchExplorer(config_data={
            "systems": [{"id": "x", "system": str(structure)}],
            "sweep": {"simulation.setup_from": [str(tmp_path / "earlier"),
                                                str(tmp_path / "nowhere")]},
        }, output_dir=str(tmp_path / "out"))
        batch.dry_run()
        said = capsys.readouterr().out
        assert "phases: simulation → analysis → report" in said
        assert "phases: setup → simulation → analysis → report" in said
        assert "so the simulation will refuse" in said


# ---------------------------------------------------------------------------
# The simulation pipeline's references
# ---------------------------------------------------------------------------


class TestWhereARecordIs:

    @pytest.mark.parametrize("seeded", ["not json", json.dumps(["a list"]),
                                        json.dumps({"no": "digest"})])
    def test_a_seed_record_that_says_nothing_leaves_the_folder_itself(self, tmp_path, seeded) -> None:
        from fastmdxplora.simulation.pipeline import _the_records_of
        from fastmdxplora.simulation.seeding import SEEDED_FROM

        (tmp_path / SEEDED_FROM).write_text(seeded)
        assert _the_records_of(tmp_path) == tmp_path

    def test_no_seed_record_leaves_the_folder_itself(self, tmp_path) -> None:
        from fastmdxplora.simulation.pipeline import _the_records_of

        assert _the_records_of(tmp_path) == tmp_path

    def test_an_input_with_no_fetch_record_is_not_found(self, tmp_path) -> None:
        from fastmdxplora.simulation.pipeline import FETCHED_RECORD, _sent_from

        run = tmp_path / "study" / "runs" / "a"
        run.mkdir(parents=True)
        (tmp_path / "study" / FETCHED_RECORD).write_text("not json")
        assert _sent_from(run, "inputs/prep") == []

    def test_a_folder_that_cannot_be_resolved_finds_nothing(self, tmp_path, monkeypatch) -> None:
        from fastmdxplora.simulation.pipeline import _sent_from

        def unresolvable(self, strict=False):
            raise OSError("gone")

        monkeypatch.setattr(Path, "resolve", unresolvable)
        assert _sent_from(tmp_path, "inputs/prep") == []


class TestAJoinFindsTheStudyItReused:

    def test_a_record_that_cannot_be_followed_is_no_study(self, tmp_path, monkeypatch) -> None:
        from fastmdxplora.analysis import joining
        from fastmdxplora.simulation import pipeline

        monkeypatch.setattr(pipeline, "_prepared_system_recorded", lambda d: {"x": 1})

        def broken(directory, reference):
            raise RuntimeError("unreadable")

        monkeypatch.setattr(pipeline, "_the_system_recorded", broken)
        assert joining._study_it_reused(tmp_path) is None
        monkeypatch.setattr(pipeline, "_the_system_recorded", lambda d, r: None)
        assert joining._study_it_reused(tmp_path) is None

    def test_a_system_with_no_config_near_it_is_no_study(self, tmp_path, monkeypatch) -> None:
        from fastmdxplora.analysis import joining
        from fastmdxplora.simulation import pipeline

        setup = tmp_path / "a" / "b" / "setup"
        setup.mkdir(parents=True)
        monkeypatch.setattr(pipeline, "_prepared_system_recorded", lambda d: {"x": 1})
        monkeypatch.setattr(pipeline, "_the_system_recorded", lambda d, r: setup)
        assert joining._study_it_reused(tmp_path) is None
        (tmp_path / "a" / "resolved_config.yml").write_text("{}")
        assert joining._study_it_reused(tmp_path) == tmp_path / "a"


# ---------------------------------------------------------------------------
# The file log, the schema's words, the GUI's adoption, a fetch
# ---------------------------------------------------------------------------


class TestTheFileLogIsLetGoOnlyWhereItWrites:

    def test_another_path_is_left_attached(self, tmp_path) -> None:
        from fastmdxplora.utils.logging import (
            attach_file_logger,
            detach_file_logger,
            file_logger_path,
        )

        attach_file_logger(tmp_path / "study.log")
        try:
            assert detach_file_logger(tmp_path / "another.log") is False
            assert file_logger_path() == (tmp_path / "study.log").resolve() or \
                file_logger_path() == tmp_path / "study.log"
        finally:
            assert detach_file_logger(tmp_path / "study.log") is True

    def test_a_handler_that_fails_to_close_is_still_let_go(self, tmp_path, monkeypatch) -> None:
        from fastmdxplora.utils.logging import attach_file_logger, detach_file_logger

        attach_file_logger(tmp_path / "study.log")
        handler = next(h for h in logging.getLogger("fastmdx").handlers
                       if isinstance(h, logging.FileHandler))

        def refuses():
            raise OSError("disk gone")

        monkeypatch.setattr(handler, "close", refuses)
        assert detach_file_logger() is True
        assert detach_file_logger() is False


def test_a_bound_with_only_a_top_is_said_as_one() -> None:
    from fastmdxplora.config.describe import _bounds
    from fastmdxplora.config.schema import Field

    assert _bounds(Field("x", float, 1.0, "help", maximum=5.0)) == "at most 5"


def test_a_run_recorded_with_no_arguments_matches_any_command_line() -> None:
    from fastmdxplora.gui.exploration import _carries_the_recorded_arguments

    assert _carries_the_recorded_arguments(["python", "-m", "fastmdx"], ["fastmdx"]) is True


def test_a_prepared_system_named_oddly_is_passed_over(tmp_path) -> None:
    from fastmdxplora.remote.jobs import Job
    from fastmdxplora.remote.send import _tie_back_to_its_inputs

    (tmp_path / "simulation").mkdir()
    (tmp_path / "simulation" / "simulation_parameters.json").write_text(
        json.dumps({"prepared_system": "inputs/prep"}))
    job = Job(name="j", machine="m", remote_dir="/remote/j", scheduler="process",
              handle="1", submitted_at="now", code={}, local_output=str(tmp_path))
    assert _tie_back_to_its_inputs(job, tmp_path) == []


# ---------------------------------------------------------------------------
# Membranes: placement, the fit, the record, the advice
# ---------------------------------------------------------------------------


class TestPlacementAtItsEdges:

    def test_a_file_that_will_not_load_alone_is_passed_over(self) -> None:
        pytest.importorskip("openmm")
        from fastmdxplora.setup.membrane import lipid_parameter_file

        assert lipid_parameter_file(["no-such-file.xml", "charmm36.xml"], "POPC") == "charmm36.xml"

    def test_an_opm_frame_whose_membrane_the_fit_puts_elsewhere_is_kept_and_said(
            self, caplog) -> None:
        from fastmdxplora.setup.membrane import place_for_membrane
        from tests.test_membrane_fit import _barrel

        caplog.set_level(logging.INFO, logger="fastmdx")
        top, points = _barrel()
        placed = place_for_membrane(top, points + [0, 0, 1.6], frame="opm", orient=True)
        assert placed.record["placed_by"] == "OPM frame"
        assert "OPM's frame is kept" in caplog.text
        assert "`membrane_orient` is not applied" in caplog.text

    def test_no_protein_with_a_stated_orientation_is_placed_as_it_is(self) -> None:
        from fastmdxplora.setup.membrane import place_for_membrane
        from tests.test_membrane_fit import _protein

        top, points = _protein([("LEU", "CA", "carbon", (0.1 * i, 0, 0)) for i in range(5)])
        placed = place_for_membrane(top, points, orientation_checked=True)
        assert placed.record["placed_by"].endswith("no protein to fit a centre to")

    def test_no_protein_to_orient_is_refused(self) -> None:
        from fastmdxplora.refusals import StudyError
        from fastmdxplora.setup.membrane import place_for_membrane
        from tests.test_membrane_fit import _protein

        top, points = _protein([("LEU", "CA", "carbon", (0.1 * i, 0, 0)) for i in range(5)])
        with pytest.raises(StudyError) as refused:
            place_for_membrane(top, points, orient=True)
        assert refused.value.code == "setup.membrane.no_belt"

    def test_a_structure_with_no_protein_or_no_surface_has_no_fit(self, monkeypatch) -> None:
        from fastmdxplora.setup import membrane_fit
        from tests.test_membrane_fit import _barrel

        top = md.Topology()
        residue = top.add_residue("HOH", top.add_chain())
        top.add_atom("O", md.element.oxygen, residue)
        assert membrane_fit.fit_membrane(top, np.zeros((1, 3))) is None
        barrel, points = _barrel()
        monkeypatch.setattr(membrane_fit, "_surface", lambda t, c: np.zeros(len(c)))
        assert membrane_fit.fit_membrane(barrel, points) is None
        assert membrane_fit.fit_along(barrel, points, (0, 0, 1)) is None


def test_the_methods_say_nothing_of_a_thickness_not_recorded(tmp_path) -> None:
    from fastmdxplora.report.methods import _bilayer_sentence

    said = _bilayer_sentence({"lipid": "POPC", "placed_by": "stated orientation and centre"},
                             1.0, "Na+", "Cl-", 0.15)
    assert "thickness" not in said
    assert "in the orientation and position supplied" in said


def test_a_lipid_whose_transition_is_not_known_is_not_advised_on() -> None:
    from fastmdxplora.advisories import _a_bilayer_near_or_below_its_transition

    assert _a_bilayer_near_or_below_its_transition({}, {"membrane": "POPG"}) is None


def test_an_opm_file_that_cannot_be_read_or_has_an_odd_header(tmp_path) -> None:
    from fastmdxplora.setup.pipeline import _take_out_opm_markers

    assert _take_out_opm_markers(tmp_path) is None  # a folder, not a file
    path = tmp_path / "input.pdb"
    path.write_text(
        "REMARK      1/2 of bilayer thickness:   thick\n"
        "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C\n"
        "HETATM    2  N   DUM     2       0.000   0.000 -15.000\n", encoding="utf-8")
    assert _take_out_opm_markers(path) == {"markers": 1, "half_thickness_nm": None}


# ---------------------------------------------------------------------------
# The bilayer analyses at their edges
# ---------------------------------------------------------------------------


def _patch(lipid: str = "DMPC"):
    app = pytest.importorskip("openmm.app")
    return md.load(str(Path(app.__file__).parent / "data" / f"{lipid}.pdb"))


class TestTheBilayerAtItsEdges:

    def test_an_atom_without_an_element_is_read_by_its_name(self) -> None:
        from fastmdxplora.analysis.bilayer import _symbol

        assert _symbol(SimpleNamespace(element=None, name="C12")) == "C"

    def test_an_empty_section_and_a_full_or_empty_grid(self) -> None:
        from fastmdxplora.analysis.bilayer import _closed, cross_section

        cell = np.array([[5.0, 0.0], [0.0, 5.0]])
        assert cross_section(np.zeros((0, 2)), np.zeros(0), cell) == 0.0
        full, empty = np.ones((4, 4), bool), np.zeros((4, 4), bool)
        assert _closed(full, cell.T, 4, 4, 0.2) is full
        assert _closed(empty, cell.T, 4, 4, 0.2) is empty

    def test_a_figure_without_its_trajectory_counts_frames(self, tmp_path) -> None:
        import matplotlib.pyplot as plt

        from fastmdxplora.analysis.area_per_lipid import AreaPerLipid

        figure, axes = plt.subplots()
        AreaPerLipid(output_dir=tmp_path).plot(np.array([0.6, 0.61, 0.62]), axes)
        assert list(axes.lines[0].get_xdata()) == [0, 1, 2]
        plt.close(figure)

    def test_a_head_that_changes_leaflet_is_said(self, tmp_path) -> None:
        from fastmdxplora.analysis.bilayer_thickness import BilayerThickness

        traj = _patch()
        two = md.join([traj, traj])
        phosphorus = two.topology.select("name P")
        z = two.xyz[0, phosphorus, 2]
        upper = phosphorus[z > np.median(z)][0]
        two.xyz[1, upper, 2] -= 2 * (two.xyz[1, upper, 2] - np.median(z))
        analysis = BilayerThickness(output_dir=tmp_path)
        analysis.compute(two)
        assert "ranges from 63 to 64" in analysis.findings["leaflet_changes"]

    def test_a_bilayer_without_phosphates_has_no_phosphate_thickness(self, tmp_path) -> None:
        from fastmdxplora.analysis.bilayer_thickness import BilayerThickness
        from fastmdxplora.refusals import StudyError

        top = md.Topology()
        chain = top.add_chain()
        points = []
        for index in range(40):
            residue = top.add_residue("CHL1", chain)
            top.add_atom("O3", md.element.oxygen, residue)
            top.add_atom("C1", md.element.carbon, residue)
            side = 1.0 if index % 2 else -1.0
            points += [[index * 0.1, 0.0, 3.0 + side * 1.5], [index * 0.1, 0.0, 3.0 + side * 0.5]]
        traj = md.Trajectory(np.asarray(points, np.float32)[None], top,
                             unitcell_lengths=[[5.0, 5.0, 6.0]],
                             unitcell_angles=[[90.0, 90.0, 90.0]])
        with pytest.raises(StudyError) as refused:
            BilayerThickness(output_dir=tmp_path).compute(traj)
        assert refused.value.code == "analysis.system.inapplicable"

    def test_the_thickness_beside_a_protein_says_so(self, tmp_path) -> None:
        from fastmdxplora.analysis.bilayer_thickness import BilayerThickness

        traj = _patch()
        top = traj.topology.copy()
        residue = top.add_residue("ALA", top.add_chain())
        for index in range(3):
            top.add_atom(f"C{index}", md.element.carbon, residue)
        middle = float(np.median(traj.xyz[0, :, 2]))
        extra = np.array([[[3.0, 3.0, middle - 0.5], [3.0, 3.0, middle], [3.0, 3.0, middle + 0.5]]],
                         np.float32)
        with_protein = md.Trajectory(np.concatenate([traj.xyz, extra], axis=1), top,
                                     unitcell_lengths=traj.unitcell_lengths,
                                     unitcell_angles=traj.unitcell_angles)
        analysis = BilayerThickness(output_dir=tmp_path)
        analysis.compute(with_protein)
        assert "near_a_protein" in analysis.findings


def _graph(symbols: list[str], bonds: list[tuple[int, int]]):
    neighbours = [set() for _ in symbols]
    for first, second in bonds:
        neighbours[first].add(second)
        neighbours[second].add(first)
    return symbols, neighbours


def _chain_of(length: int, start: int, symbols: list[str], bonds: list) -> None:
    """A saturated carbon chain of `length` carbons bonded to atom `start`."""
    previous = start
    for position in range(length):
        carbon = len(symbols)
        symbols.append("C")
        bonds.append((previous, carbon))
        for _ in range(3 if position == length - 1 else 2):
            symbols.append("H")
            bonds.append((carbon, len(symbols) - 1))
        previous = carbon


class TestChainsAreFoundOnlyWhereTheyAre:

    def test_an_amide_linked_chain_is_n_acyl(self) -> None:
        from fastmdxplora.analysis.lipid_order import _chains

        symbols = ["C", "O", "N", "H"]  # carbonyl C, its O, the amide N and H
        bonds = [(0, 1), (0, 2), (2, 3)]
        _chain_of(5, 0, symbols, bonds)
        chains, _ = _chains(("SM",), *_graph(symbols, bonds))
        assert [c.label for c in chains] == ["N-acyl (6:0)"]

    @pytest.mark.parametrize("case", ["carboxylate", "ester_to_phosphorus", "methyl_ester",
                                      "quaternary", "short", "ketone_with_nitrogens"])
    def test_what_is_not_an_acyl_chain(self, case) -> None:
        from fastmdxplora.analysis.lipid_order import _chains

        if case == "carboxylate":        # C(=O)O-: no ester oxygen
            symbols, bonds = ["C", "O", "O"], [(0, 1), (0, 2)]
            _chain_of(5, 0, symbols, bonds)
        elif case == "ester_to_phosphorus":  # the ester oxygen's other side is no carbon
            symbols, bonds = ["C", "O", "O", "P"], [(0, 1), (0, 2), (2, 3)]
            _chain_of(5, 0, symbols, bonds)
        elif case == "methyl_ester":     # the carbon on the ester carries three H
            symbols, bonds = ["C", "O", "O", "C", "H", "H", "H"], [
                (0, 1), (0, 2), (2, 3), (3, 4), (3, 5), (3, 6)]
            _chain_of(5, 0, symbols, bonds)
        elif case == "quaternary":       # a bare carbon with two carbons on it
            symbols, bonds = ["C"], []
            _chain_of(3, 0, symbols, bonds)
            _chain_of(3, 0, symbols, bonds)
        elif case == "short":            # an ester with two carbons after it
            symbols, bonds = ["C", "O", "O", "C", "H"], [(0, 1), (0, 2), (2, 3), (3, 4)]
            _chain_of(2, 0, symbols, bonds)
        else:                            # one oxygen and two nitrogens
            symbols, bonds = ["C", "O", "N", "N"], [(0, 1), (0, 2), (0, 3)]
        chains, _ = _chains(("X",), *_graph(symbols, bonds))
        assert chains == []


class TestTheOrderOfLipidsWithoutChains:

    @staticmethod
    def _with(traj, names: list[str], *, hydrogens: bool):
        top = traj.topology.copy()
        chain = top.add_chain()
        heads = top.select("name P")
        middle = float(np.median(traj.xyz[0, heads, 2])) if len(heads) else 4.0
        points = []
        for index, name in enumerate(names):
            residue = top.add_residue(name, chain)
            top.add_atom("P", md.element.phosphorus, residue)
            top.add_atom("C1", md.element.carbon, residue)
            if hydrogens:
                top.add_atom("H1", md.element.hydrogen, residue)
            side = 2.0 if index % 2 else -2.0
            points += [[0.5 + index * 0.3, 0.5, middle + side],
                       [0.5 + index * 0.3, 0.5, middle + side * 0.9]]
            if hydrogens:
                points.append([0.5 + index * 0.3, 0.6, middle + side * 0.9])
        xyz = np.concatenate([traj.xyz, np.asarray(points, np.float32)[None]], axis=1)
        return md.Trajectory(xyz, top, unitcell_lengths=traj.unitcell_lengths,
                             unitcell_angles=traj.unitcell_angles)

    def test_lipids_left_out_are_named(self, tmp_path) -> None:
        from fastmdxplora.analysis.lipid_order import LipidOrder

        traj = self._with(self._with(_patch(), ["POPG", "POPG"], hydrogens=True),
                          ["POPE", "POPE"], hydrogens=False)
        analysis = LipidOrder(output_dir=tmp_path)
        table = analysis.compute(traj)
        assert set(table["lipid"]) == {"DMPC"}
        assert "POPG" in analysis.findings["no_chain_found"]
        assert "POPE" in analysis.findings["no_hydrogens"]

    def test_lipids_with_hydrogens_and_no_chains_are_refused(self, tmp_path) -> None:
        from fastmdxplora.analysis.lipid_order import LipidOrder
        from fastmdxplora.refusals import StudyError

        empty = md.Trajectory(np.zeros((1, 0, 3), np.float32), md.Topology(),
                              unitcell_lengths=[[6.0, 6.0, 8.0]],
                              unitcell_angles=[[90.0, 90.0, 90.0]])
        traj = self._with(empty, ["POPG"] * 24, hydrogens=True)
        with pytest.raises(StudyError) as refused:
            LipidOrder(output_dir=tmp_path).compute(traj)
        assert refused.value.code == "analysis.system.inapplicable"
