"""What a run records about its box, and what it says about when it biases.

Both found by using the software rather than reading it. A failed run had to
be diagnosed by reading CRYST1 out of `solvated.pdb` by hand, because nothing
recorded the box. And the line announcing metadynamics is printed where the
script is written -- before minimisation -- so it read as though the bias were
live from the first step, which sent the diagnosis of that same failed run off
in the wrong direction for a while.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from fastmdxplora.setup.prepare import _box_record


class _Quantity(float):
    def value_in_unit(self, _unit: Any) -> float:
        return float(self)


class _Topology:
    def __init__(self, vectors: Any) -> None:
        self._vectors = vectors

    def getPeriodicBoxVectors(self) -> Any:
        return self._vectors


class _Modeller:
    def __init__(self, vectors: Any) -> None:
        self.topology = _Topology(vectors)


def _dodecahedron(edge: float = 3.7498):
    """OpenMM's reduced form for a rhombic dodecahedron.

    The third vector's z component is the edge over root two, which is where
    the usable room actually is.
    """
    return [
        [_Quantity(edge), _Quantity(0.0), _Quantity(0.0)],
        [_Quantity(0.0), _Quantity(edge), _Quantity(0.0)],
        [_Quantity(edge / 2), _Quantity(edge / 2),
         _Quantity(edge / math.sqrt(2))],
    ]


class TestTheBoxIsRecorded:
    def test_the_vectors_and_widths_are_both_kept(self) -> None:
        record = _box_record(_Modeller(_dodecahedron()))
        assert record is not None
        assert len(record["vectors_nm"]) == 3
        assert len(record["perpendicular_widths_nm"]) == 3

    def test_the_smallest_width_is_not_the_edge(self) -> None:
        """For a dodecahedron the edge overstates the usable room by 40%,
        and it is the width the minimum image convention constrains."""
        record = _box_record(_Modeller(_dodecahedron(3.7498)))
        assert record["smallest_width_nm"] == pytest.approx(2.6515, abs=1e-3)
        assert record["smallest_width_nm"] < 3.7498

    def test_the_largest_usable_cutoff_is_half_that(self) -> None:
        record = _box_record(_Modeller(_dodecahedron(3.7498)))
        assert record["largest_usable_cutoff_nm"] == pytest.approx(
            1.3257, abs=1e-3)
        # The run that prompted this used a 1.0 nm cutoff, which had room.
        assert record["largest_usable_cutoff_nm"] > 1.0

    def test_the_volume_is_the_cell_volume(self) -> None:
        """Not the cube of the edge: a dodecahedron holds less."""
        edge = 3.7498
        record = _box_record(_Modeller(_dodecahedron(edge)))
        assert record["volume_nm3"] == pytest.approx(
            edge * edge * edge / math.sqrt(2), rel=1e-6)
        assert record["volume_nm3"] < edge ** 3

    def test_a_cube_is_its_own_smallest_width(self) -> None:
        cube = [[_Quantity(4.0), _Quantity(0.0), _Quantity(0.0)],
                [_Quantity(0.0), _Quantity(4.0), _Quantity(0.0)],
                [_Quantity(0.0), _Quantity(0.0), _Quantity(4.0)]]
        record = _box_record(_Modeller(cube))
        assert record["smallest_width_nm"] == pytest.approx(4.0)
        assert record["volume_nm3"] == pytest.approx(64.0)


class TestAnUnreadableBoxIsNotAFailure:
    """A setup that otherwise succeeded should not fail because its box could
    not be summarised."""

    def test_no_periodic_box_gives_no_record(self) -> None:
        assert _box_record(_Modeller(None)) is None

    def test_vectors_that_are_not_quantities_give_no_record(self) -> None:
        assert _box_record(_Modeller([["x"], ["y"], ["z"]])) is None

    def test_a_topology_without_the_method_gives_no_record(self) -> None:
        assert _box_record(object()) is None


class TestTheLogSaysWhenTheBiasApplies:
    """Metadynamics and a pull are biased just before production and
    equilibrate unbiased; an umbrella window is held from the first step.
    The lines announcing all three are printed where the script is written,
    which is before minimisation, so they used the present tense.

    The umbrella line said "the restraint applies to production only" and was
    accurate: the window equilibrated with nothing holding its coordinate,
    and on a thirty-window study twenty-six began production more than four
    sigma from where they were seeded. `test_a_window_is_held_while_it_
    equilibrates` measures the behaviour; this file watches the wording.
    """

    @pytest.mark.parametrize("phrase", [
        "Metadynamics prepared on",
        "production only; minimisation and equilibration run unbiased",
        "Umbrella window %d prepared",
        "from minimisation onwards",
        "Steered pull prepared",
    ])
    def test_the_wording_states_the_stage(self, phrase: str) -> None:
        from pathlib import Path
        import fastmdxplora.simulation.runner as runner

        source = Path(runner.__file__).read_text(encoding="utf-8")
        assert phrase in source

    @pytest.mark.parametrize("stale", [
        '"Metadynamics biasing %s. %s"',
        '"Umbrella window %d: holding %s at %g with k=%g."',
        # It was true when it was written, and the truth was the bug.
        "The restraint applies to production only.",
    ])
    def test_the_old_present_tense_is_gone(self, stale: str) -> None:
        from pathlib import Path
        import fastmdxplora.simulation.runner as runner

        assert stale not in Path(runner.__file__).read_text(encoding="utf-8")
