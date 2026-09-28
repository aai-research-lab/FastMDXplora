"""A metadynamics bias on a distance is kept on a grid.

Without a grid PLUMED sums every hill deposited so far at every step, and a
long run slows without end: on one torsion run, the slowing over its first
8,000 hills puts 100 ns at about 19 hours, against about 7 on a grid. Only torsions were gridded, because a grid has to be one the
variable cannot leave, and a distance was taken to have no ceiling. It has
one: PLUMED measures it by the minimum image, which the periodic cell bounds.
Every `ligand_distance` study, the ones the case studies rest on, ran on the
hill sum.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from fastmdxplora.simulation.metadynamics import (
    CELL_GROWTH_ALLOWED,
    GRID_POINTS_PER_SIGMA,
    MetadynamicsPlan,
    _grid_keywords,
    build_plumed_script,
    longest_minimum_image_nm,
)

CUBE = np.eye(3) * 6.0
#: A rhombic dodecahedron as OpenMM writes one (the default box here).
DODECAHEDRON = 6.0 * np.array([[1.0, 0.0, 0.0],
                               [0.0, 1.0, 0.0],
                               [0.5, 0.5, math.sqrt(2.0) / 2.0]])


def _plan(variable: str, sigma: float = 0.05, **atoms) -> MetadynamicsPlan:
    return MetadynamicsPlan(collective_variable=variable, sigma=sigma,
                            atoms=atoms or {"selection_a": [0, 1], "selection_b": [2, 3]})


def _grid(keywords: str) -> dict[str, list[str]]:
    return {key: value.split(",") for key, value in
            re.findall(r"GRID_(MIN|MAX|BIN)=(\S+)", keywords)}


def _minimum_image(vector, box) -> float:
    """The shortest image of a separation, by search over neighbouring cells."""
    shifts = np.array([[i, j, k] for i in (-2, -1, 0, 1, 2)
                       for j in (-2, -1, 0, 1, 2) for k in (-2, -1, 0, 1, 2)])
    return float(np.min(np.linalg.norm(vector + shifts @ box, axis=1)))


class TestTheCellBoundsADistance:

    def test_a_cube_is_bounded_by_half_its_diagonal(self) -> None:
        assert longest_minimum_image_nm(CUBE) == pytest.approx(3.0 * math.sqrt(3.0))

    @pytest.mark.parametrize("box", [CUBE, DODECAHEDRON, np.diag([5.0, 6.0, 9.0])],
                             ids=["cube", "dodecahedron", "rectangular"])
    def test_no_separation_is_longer(self, box) -> None:
        """Checked by brute force over separations anywhere in the cell."""
        rng = np.random.default_rng(7)
        bound = longest_minimum_image_nm(box)
        found = max(_minimum_image(u @ box, box) for u in rng.random((4000, 3)))
        assert found <= bound + 1e-9
        # And not a loose bound: the farthest found is close to it. Half the
        # body diagonal, the simple bound, is half as long again for the
        # dodecahedron.
        assert found >= 0.9 * bound

    def test_a_dodecahedron_is_bounded_by_its_vertices(self) -> None:
        """Its image distance over the square root of two."""
        assert longest_minimum_image_nm(DODECAHEDRON) == pytest.approx(6.0 / math.sqrt(2.0))


class TestWhatIsGridded:

    @pytest.mark.parametrize("variable", ["distance", "ligand_distance"])
    def test_a_distance_once_the_cell_is_known(self, variable) -> None:
        grid = _grid(_grid_keywords([_plan(variable)], DODECAHEDRON))
        top = longest_minimum_image_nm(DODECAHEDRON) * CELL_GROWTH_ALLOWED
        assert grid["MIN"] == ["0"]
        assert float(grid["MAX"][0]) == pytest.approx(top, abs=1e-4)
        assert int(grid["BIN"][0]) >= top / (0.05 / GRID_POINTS_PER_SIGMA)

    def test_not_before(self) -> None:
        """The script written at setup has no cell yet; the one attached at
        production does."""
        assert _grid_keywords([_plan("distance")]) == ""

    def test_a_membrane_depth_in_a_rectangular_cell(self) -> None:
        grid = _grid(_grid_keywords([_plan("membrane_depth")], np.diag([6.0, 6.0, 9.0])))
        half = 4.5 * CELL_GROWTH_ALLOWED
        assert [float(v) for v in (grid["MIN"][0], grid["MAX"][0])] == pytest.approx([-half, half])

    def test_not_a_membrane_depth_in_a_sheared_cell(self) -> None:
        assert _grid_keywords([_plan("membrane_depth")], DODECAHEDRON) == ""

    @pytest.mark.parametrize("variable, low, high", [("angle", "0", "pi"), ("q", "0", "1")])
    def test_variables_bounded_by_their_definition(self, variable, low, high) -> None:
        grid = _grid(_grid_keywords([_plan(variable, sigma=0.05)]))
        assert (grid["MIN"], grid["MAX"]) == ([low], [high])

    def test_a_coordination_number_by_its_pairs(self) -> None:
        plan = _plan("coordination", sigma=0.5, selection_a=[0, 1, 2], selection_b=[3, 4])
        assert _grid(_grid_keywords([plan]))["MAX"] == ["6"]

    def test_not_a_grid_too_large_to_hold(self) -> None:
        many = _plan("coordination", sigma=0.1, selection_a=list(range(400)),
                     selection_b=list(range(400, 800)))
        assert _grid_keywords([many]) == ""

    @pytest.mark.parametrize("variable", ["ligand_rmsd", "radius_of_gyration"])
    def test_not_a_variable_nothing_bounds(self, variable) -> None:
        assert _grid_keywords([_plan(variable)], CUBE) == ""

    def test_a_pair_is_gridded_only_if_both_are(self) -> None:
        both = _grid(_grid_keywords([_plan("distance"), _plan("torsion", sigma=0.35)], CUBE))
        assert both["MIN"] == ["0", "-pi"] and len(both["BIN"]) == 2
        assert _grid_keywords([_plan("distance"), _plan("ligand_rmsd")], CUBE) == ""


def test_the_script_carries_the_grid(tmp_path) -> None:
    script = build_plumed_script(_plan("distance"), box_nm=CUBE)
    metad = next(line for line in script.splitlines() if line.startswith("metad:"))
    assert "GRID_MIN=0 GRID_MAX=" in metad


def test_the_runner_grids_by_the_cell_production_starts_in(tmp_path) -> None:
    """Rewritten when the bias goes on, after equilibration has settled the
    cell, with the cell OpenMM reports."""
    pytest.importorskip("openmm")
    from functools import partial

    from openmm import unit

    from fastmdxplora.simulation.runner import _grid_by_the_cell

    plan = _plan("distance")
    path = tmp_path / "metadynamics.plumed"
    path.write_text(build_plumed_script(plan), encoding="utf-8")
    vectors = unit.Quantity(np.eye(3) * 60.0, unit.angstrom)
    state = SimpleNamespace(getPeriodicBoxVectors=lambda asNumpy=False: vectors)
    simulation = SimpleNamespace(context=SimpleNamespace(getState=lambda: state))

    _grid_by_the_cell(simulation, partial(build_plumed_script, plan), path, unit=unit)

    written = Path(path).read_text(encoding="utf-8")
    top = 3.0 * math.sqrt(3.0) * CELL_GROWTH_ALLOWED
    assert f"GRID_MAX={top:.4f}" in written
