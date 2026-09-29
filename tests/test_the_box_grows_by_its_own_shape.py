"""AUD40: the growth loop used the cube's arithmetic on every box.

`_solvate_with_room_for_the_cutoff` exists to notice a box narrower than
twice the cutoff and re-solvate with more padding. It computed the extra
padding as `shortfall / 2`, which is the relation for a cube: a cube's
narrowest periodic width is `maxSize + 2 * padding`, so 0.30 nm of
shortfall wants 0.15 nm more padding.

A dodecahedron's narrowest width is that quantity over sqrt(2). The same
padding buys sqrt(2) less width, and the loop undershot every non-cubic
box by exactly that factor.

Measured on the run it killed. V3, alanine dipeptide, 1.20 nm padding, 1.0
nm cutoff, dodecahedron: the box came out 1.70 nm across, the loop grew to
1.45 nm intending 2.20, and produced 2.05 against a floor of 2.00. NPT
then contracted the box 1.9% toward real water's density -- which is the
barostat's whole job -- and the run died on the second move with "The
periodic box size has decreased to less than twice the nonbonded cutoff."

Two things were wrong and the second is why the first mattered: the
increment used the wrong factor, and the target was the bare floor rather
than the floor plus the contraction the next phase was about to apply.

The fix took the width to grow by `2 f` per unit of padding, which is right
only while the padding sizes the box. OpenMM sizes a padded box as the
solute's bounding sphere plus the padding (padding counted once), or twice
the padding for a solute smaller than that; for any solute that sizes its
box the width grows by `f`, and the loop undershot by half. The growth is
now worked out from OpenMM's rule, checked here against the rule and
against OpenMM itself.
"""

from __future__ import annotations

import math

import pytest

from fastmdxplora.setup.prepare import (
    NARROWEST_WIDTH_PER_SIZE,
    NPT_CONTRACTION_MARGIN,
    padding_that_reaches,
)

#: The V3 solute's bounding diameter, read off the box OpenMM built for it:
#: 1.70 nm at its narrowest from 1.20 nm of padding in a dodecahedron.
V3_ACROSS = 1.70 / NARROWEST_WIDTH_PER_SIZE["dodecahedron"] - 1.20


def _narrowest(across: float, padding: float, shape: str) -> float:
    """The box OpenMM builds: max(2r + padding, 2 padding), times f."""
    return NARROWEST_WIDTH_PER_SIZE[shape] * max(across + padding, 2.0 * padding)


def _width_after_growing(smallest: float, padding: float, grown: float,
                         shape: str, across: float = V3_ACROSS) -> float:
    return _narrowest(across, grown, shape)


class TestTheFactorsAreTheBoxVectors:
    """Not chosen: read off the matrix OpenMM builds and constrains."""

    def test_they_match_openmm_s_own_vectors(self) -> None:
        pytest.importorskip("openmm")

        expected = {
            "cube": min(1.0, 1.0, 1.0),
            "dodecahedron": min(1.0, 1.0, math.sqrt(2) / 2),
            "octahedron": min(1.0, 2 * math.sqrt(2) / 3, math.sqrt(6) / 3),
        }
        for shape, want in expected.items():
            assert NARROWEST_WIDTH_PER_SIZE[shape] == pytest.approx(want)

    def test_a_dodecahedron_is_the_narrow_one(self) -> None:
        assert (NARROWEST_WIDTH_PER_SIZE["dodecahedron"]
                < NARROWEST_WIDTH_PER_SIZE["octahedron"]
                < NARROWEST_WIDTH_PER_SIZE["cube"])


class TestEveryShapeReachesItsTarget:

    @pytest.mark.parametrize("across", [0.3, 0.9, 1.6, 2.4])
    @pytest.mark.parametrize("shape", sorted(NARROWEST_WIDTH_PER_SIZE))
    def test_the_grown_box_clears_the_floor_with_the_margin(
            self, shape: str, across: float) -> None:
        """For a solute the padding sizes the box around and one that sizes
        it itself: in one step, and no further than it needs."""
        cutoff, padding = 1.0, 0.8
        smallest = _narrowest(across, padding, shape)
        wanted = 2.0 * cutoff * NPT_CONTRACTION_MARGIN
        grown = padding_that_reaches(
            smallest_nm=smallest, padding_nm=padding,
            nonbonded_cutoff_nm=cutoff, box_shape=shape)
        if smallest >= wanted:
            assert grown == padding
        else:
            assert _narrowest(across, grown, shape) == pytest.approx(wanted)

    def test_a_box_that_already_clears_is_not_grown(self) -> None:
        assert padding_that_reaches(
            smallest_nm=3.0, padding_nm=1.2, nonbonded_cutoff_nm=1.0,
            box_shape="dodecahedron") == 1.2

    def test_an_unknown_shape_is_treated_as_a_cube(self) -> None:
        """Conservative in the wrong direction is still wrong, but a shape
        this does not know about should not silently get a factor invented
        for it."""
        assert padding_that_reaches(
            smallest_nm=1.7, padding_nm=1.2, nonbonded_cutoff_nm=1.0,
            box_shape="something-else") == padding_that_reaches(
            smallest_nm=1.7, padding_nm=1.2, nonbonded_cutoff_nm=1.0,
            box_shape="cube")


class TestASoluteThatSizesItsBox:
    """The second defect: `2 f` per unit of padding where the solute sizes the
    box. A solute 1.6 nm across at 1.0 nm of padding in a dodecahedron."""

    def test_the_second_arithmetic_undershot_by_half(self) -> None:
        f = NARROWEST_WIDTH_PER_SIZE["dodecahedron"]
        smallest = _narrowest(1.6, 1.0, "dodecahedron")
        second = 1.0 + (2.2 - smallest) / (2.0 * f)
        assert second == pytest.approx(1.255, abs=1e-3)
        assert _narrowest(1.6, second, "dodecahedron") == pytest.approx(2.02, abs=0.01)

    def test_it_now_reaches_the_target(self) -> None:
        smallest = _narrowest(1.6, 1.0, "dodecahedron")
        grown = padding_that_reaches(smallest_nm=smallest, padding_nm=1.0,
                                     nonbonded_cutoff_nm=1.0, box_shape="dodecahedron")
        assert _narrowest(1.6, grown, "dodecahedron") == pytest.approx(2.2)


class TestOpenMMSizesItThisWay:
    """The rule the arithmetic rests on, asked of the OpenMM installed."""

    @pytest.mark.parametrize("shape,spread", [("dodecahedron", 0.2), ("cube", 1.4),
                                              ("octahedron", 0.9)])
    def test_the_box_is_the_bounding_sphere_plus_the_padding(self, shape, spread) -> None:
        app = pytest.importorskip("openmm.app")
        unit = pytest.importorskip("openmm").unit
        from openmm import Vec3

        # Three water molecules along a line: a solute of a known size.
        topology = app.Topology()
        chain = topology.addChain()
        positions = []
        for k in range(3):
            residue = topology.addResidue("HOH", chain)
            oxygen = topology.addAtom("O", app.element.oxygen, residue)
            h1 = topology.addAtom("H1", app.element.hydrogen, residue)
            h2 = topology.addAtom("H2", app.element.hydrogen, residue)
            topology.addBond(oxygen, h1)
            topology.addBond(oxygen, h2)
            x = spread * k / 2.0
            positions += [Vec3(x, 0, 0), Vec3(x + 0.09572, 0, 0), Vec3(x - 0.024, 0.0927, 0)]
        forcefield = app.ForceField("amber14/tip3p.xml")
        padding = 1.0
        modeller = app.Modeller(topology, positions * unit.nanometer)
        modeller.addSolvent(forcefield, padding=padding * unit.nanometer, boxShape=shape)
        vectors = modeller.topology.getPeriodicBoxVectors()
        smallest = min(vectors[i][i].value_in_unit(unit.nanometer) for i in range(3))

        xyz = [(p.x, p.y, p.z) for p in positions]
        low = [min(c[i] for c in xyz) for i in range(3)]
        high = [max(c[i] for c in xyz) for i in range(3)]
        centre = [(a + b) / 2 for a, b in zip(low, high)]
        radius = max(math.dist(c, centre) for c in xyz)
        assert smallest == pytest.approx(_narrowest(2 * radius, padding, shape), rel=1e-6)

        grown = padding_that_reaches(smallest_nm=smallest, padding_nm=padding,
                                     nonbonded_cutoff_nm=1.2, box_shape=shape)
        again = app.Modeller(topology, positions * unit.nanometer)
        again.addSolvent(forcefield, padding=grown * unit.nanometer, boxShape=shape)
        vectors = again.topology.getPeriodicBoxVectors()
        reached = min(vectors[i][i].value_in_unit(unit.nanometer) for i in range(3))
        assert reached == pytest.approx(2 * 1.2 * NPT_CONTRACTION_MARGIN, rel=1e-6)


class TestTheRunThisKilled:
    """V3, and the numbers are the ones the run logged."""

    LOGGED_SMALLEST = 1.70
    LOGGED_PADDING = 1.20
    CUTOFF = 1.0
    CONTRACTION = 0.98085   # 0.9408 -> 0.997 g/mL, linear

    def test_the_old_arithmetic_undershot_by_root_two(self) -> None:
        old_grown = self.LOGGED_PADDING + (2.0 * self.CUTOFF
                                           - self.LOGGED_SMALLEST) / 2.0 + 0.1
        old_width = _width_after_growing(
            self.LOGGED_SMALLEST, self.LOGGED_PADDING, old_grown,
            "dodecahedron")
        # 2.05 against the 2.0527 the run actually built.
        assert old_width == pytest.approx(2.05, abs=0.01)
        assert old_width < 2.0 * self.CUTOFF * NPT_CONTRACTION_MARGIN

    def test_the_old_box_could_not_survive_the_contraction(self) -> None:
        """This is why it is a defect rather than a tight fit: no
        equilibration of that box to correct density was possible."""
        old_grown = self.LOGGED_PADDING + (2.0 * self.CUTOFF
                                           - self.LOGGED_SMALLEST) / 2.0 + 0.1
        old_width = _width_after_growing(
            self.LOGGED_SMALLEST, self.LOGGED_PADDING, old_grown,
            "dodecahedron")
        assert old_width * self.CONTRACTION < 2.0 * self.CUTOFF + 0.02

    def test_the_new_box_survives_it(self) -> None:
        grown = padding_that_reaches(
            smallest_nm=self.LOGGED_SMALLEST, padding_nm=self.LOGGED_PADDING,
            nonbonded_cutoff_nm=self.CUTOFF, box_shape="dodecahedron")
        width = _width_after_growing(
            self.LOGGED_SMALLEST, self.LOGGED_PADDING, grown, "dodecahedron")
        assert width * self.CONTRACTION > 2.0 * self.CUTOFF

    def test_the_margin_covers_the_measured_contraction(self) -> None:
        """2-3.5% linear on the systems measured. A margin that did not
        cover it would leave the same defect with a bigger number."""
        assert NPT_CONTRACTION_MARGIN > 1.035
