"""Two refusals in preparation, exercised without building a system.

Both were written after a failure that cost time in a way the message
now prevents: a structure whose rebuilt termini walked off in every
direction, discovered as a NaN ten minutes into packing lipids; and a
misspelled option, discovered as a KeyError from inside OpenMM. Neither
needs a prepared system to reach, which is why they can be tested here
rather than only on a cluster.
"""

from __future__ import annotations

import numpy as np
import pytest

from fastmdxplora.setup.prepare import (
    _refuse_an_implausible_structure,
    _resolve_constraints,
)


class _Unit:
    """Just enough of openmm.unit for these two guards.

    ``nanometer`` is 1.0 rather than a label because the code multiplies a
    padding by it before handing it to OpenMM, so a sentinel that cannot
    be multiplied would fail the fixture rather than the function.
    """

    nanometer = 1.0


class _Topology:
    def __init__(self, n_residues: int) -> None:
        self._n = n_residues

    def residues(self):
        return iter(range(self._n))


class TestAStructureThatIsNotOneObject:
    """A folded chain's longest dimension grows as the cube root of its
    residue count. Something far outside that is not a fold."""

    def test_a_compact_structure_passes(self):
        positions = np.random.default_rng(0).random((300, 3)) * 3.0
        assert _refuse_an_implausible_structure(
            _Topology(100), positions, _Unit) is None

    def test_a_structure_strung_across_the_universe_is_refused(self):
        """The case that used to surface as a NaN inside addMembrane.

        Ten minutes of lipid packing later, with a message naming none of
        the cause.
        """
        positions = np.zeros((300, 3))
        positions[-1] = [400.0, 0.0, 0.0]

        with pytest.raises(ValueError, match="far from everything else"):
            _refuse_an_implausible_structure(_Topology(100), positions, _Unit)

    def test_the_message_names_both_numbers(self):
        """What it measured and what it expected.

        A refusal that says only "implausible" leaves the reader to guess
        whether their fibril is the exception.
        """
        positions = np.zeros((30, 3))
        positions[-1] = [500.0, 0.0, 0.0]

        with pytest.raises(ValueError) as raised:
            _refuse_an_implausible_structure(_Topology(10), positions, _Unit)
        message = str(raised.value)
        assert "500 nm across" in message
        assert "10 residues" in message

    def test_an_elongated_but_real_structure_is_allowed(self):
        """A coiled coil is long and is still one object.

        The bound is several times the cube-root estimate so that shape
        passes; a guard that refused it would be refusing real work.
        """
        positions = np.zeros((3000, 3))
        positions[:, 0] = np.linspace(0.0, 30.0, 3000)
        assert _refuse_an_implausible_structure(
            _Topology(1000), positions, _Unit) is None

    def test_positions_it_cannot_read_are_left_alone(self):
        """A guard is not a place to invent a second failure.

        Whatever could not be measured is somebody else's error to
        report, with the context this function does not have.
        """
        assert _refuse_an_implausible_structure(
            _Topology(10), object(), _Unit) is None
        assert _refuse_an_implausible_structure(
            _Topology(0), np.zeros((3, 3)), _Unit) is None


class TestAMisspelledConstraint:
    def test_the_documented_options_resolve(self):
        omm = {"HBonds": "HBONDS"}
        assert _resolve_constraints(omm, "hbonds") == "HBONDS"
        assert _resolve_constraints(omm, "HBonds") == "HBONDS"

    def test_none_means_none(self):
        assert _resolve_constraints({"HBonds": object()}, None) is None
        assert _resolve_constraints({"HBonds": object()}, "None") is None

    def test_an_unknown_option_is_refused_by_name(self):
        """Rather than reaching OpenMM as a KeyError.

        The valid values are listed, because the usual cause is a spelling
        and the reader needs the spelling rather than a stack trace.
        """
        with pytest.raises(ValueError, match="Unknown constraints option"):
            _resolve_constraints({"HBonds": object()}, "hbond")

        with pytest.raises(ValueError) as raised:
            _resolve_constraints({"HBonds": object()}, "everything")
        assert "HBonds" in str(raised.value)


class _CapturedLog:
    """Records from one logger, whatever it does about propagation.

    `caplog` attaches to the root logger, and the package sets
    `propagate = False` on `fastmdx` as soon as anything configures
    logging. Run on its own this test passed because nothing had; run
    after a test that had, the record never reached root and the
    assertion failed on one CI leg only. Attaching to the logger that
    emits removes the ordering from the question.
    """

    def __init__(self, name: str, level: int) -> None:
        import logging

        self._logger = logging.getLogger(name)
        self._level = level
        self.records: list[str] = []

    def __enter__(self):
        import logging

        captured = self

        class _Handler(logging.Handler):
            def emit(self, record):
                captured.records.append(record.getMessage())

        self._handler = _Handler(level=self._level)
        self._previous = self._logger.level
        self._logger.setLevel(self._level)
        self._logger.addHandler(self._handler)
        return self

    def __exit__(self, *exc):
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._previous)
        return False


class _Quantity:
    def __init__(self, value: float) -> None:
        self._value = value

    def value_in_unit(self, _unit) -> float:
        return self._value


class _Modeller:
    """Enough of a Modeller to watch the padding grow.

    Records what padding each attempt used and builds the box OpenMM would
    for a cube: the solute's bounding sphere plus the padding, or twice the
    padding for a solute smaller than that. The first version grew the box
    as a fixed multiple of the padding, which no OpenMM builds, and the
    loop's arithmetic was checked against that (the rule: a fixture from the
    producing code, not from the consumer's expectations).
    """

    def __init__(self, across_nm: float = 1.0, *, empty: bool = False):
        self.attempts: list[float] = []
        self._across = across_nm
        self._empty = empty
        self.topology = self

    def deleteWater(self):  # noqa: N802 - OpenMM's spelling
        """Called between attempts, because the previous shell is stale.

        Recorded rather than ignored: growing the padding without removing
        the water already placed would solvate the solvent.
        """
        self.deleted = getattr(self, "deleted", 0) + 1

    def addSolvent(self, _ff, **kwargs):  # noqa: N802 - OpenMM's spelling
        padding = float(kwargs.get("padding"))
        self.attempts.append(padding)
        self._box = 0.0 if self._empty else max(self._across + padding, 2.0 * padding)

    def getPeriodicBoxVectors(self):  # noqa: N802 - OpenMM's spelling
        size = getattr(self, "_box", 0.0)
        return [[_Quantity(size if i == j else 0.0) for j in range(3)]
                for i in range(3)]


class TestABoxBigEnoughForItsCutoff:
    """A periodic cutoff must be at most half the smallest box dimension.

    Below that a particle sees its own image, which is why OpenMM refuses
    outright: the physics is wrong rather than approximate.
    """

    def _solvate(self, modeller, *, cutoff, padding, method="PME", **kw):
        from fastmdxplora.setup.prepare import _solvate_with_room_for_the_cutoff

        return _solvate_with_room_for_the_cutoff(
            modeller, object(), {"padding": padding},
            nonbonded_cutoff_nm=cutoff, padding_nm=padding,
            nonbonded_method=method, unit=_Unit, **kw)

    def test_a_box_already_large_enough_is_solvated_once(self):
        modeller = _Modeller(across_nm=4.0)
        assert self._solvate(modeller, cutoff=0.9, padding=1.0) == 1.0
        assert modeller.attempts == [1.0], "no growing was needed"

    def test_a_box_too_small_grows_once_to_what_it_needs(self):
        modeller = _Modeller(across_nm=1.0)
        used = self._solvate(modeller, cutoff=1.0, padding=1.0)

        assert len(modeller.attempts) == 2 and used == modeller.attempts[1] > 1.0
        assert modeller._box == pytest.approx(2 * 1.0 * 1.10)
        assert getattr(modeller, "deleted", 0) >= 1, (
            "the previous shell must go before a larger box is filled")

    def test_a_non_periodic_method_is_left_alone(self):
        """Without periodicity there is no image for a particle to see."""
        modeller = _Modeller(across_nm=0.1)
        self._solvate(modeller, cutoff=5.0, padding=1.0, method="NoCutoff")
        assert modeller.attempts == [1.0]

    def test_a_solute_that_sizes_the_box_is_not_grown_past_a_little(self):
        """And says what it tried.

        Silent, it advised raising the padding without mentioning that a
        larger value had already been attempted and was still short, so
        the obvious next guess failed the same way.
        """
        import logging

        modeller = _Modeller(across_nm=2.0)
        with _CapturedLog("fastmdx.setup.prepare", logging.INFO) as log:
            used = self._solvate(modeller, cutoff=1.5, padding=0.4,
                                 most_it_may_grow_nm=0.5)

        assert len(modeller.attempts) == 1 and used == 0.4
        assert any("Stopping at" in message for message in log.records), (
            f"nothing said why it stopped; recorded: {log.records}")

    def test_a_small_solute_gets_the_smallest_box_its_cutoff_allows(self):
        """However much padding that takes: it is the least water any solute
        that small can have under this cutoff. The defaults (1.0 nm padding,
        1.0 nm cutoff, dodecahedron) needed 0.56 nm more for a dipeptide, and
        refusing it stopped every such study at setup."""
        modeller = _Modeller(across_nm=0.3)
        used = self._solvate(modeller, cutoff=1.5, padding=0.4, most_it_may_grow_nm=0.5)
        assert used == pytest.approx(1.5 * 1.10)
        assert modeller._box == pytest.approx(2 * 1.5 * 1.10)

    def test_a_box_of_no_size_is_not_a_box_to_pad(self):
        modeller = _Modeller(empty=True)
        self._solvate(modeller, cutoff=0.9, padding=1.0)
        assert modeller.attempts == [1.0]

    def test_a_box_it_cannot_measure_is_left_as_built(self):
        """Vectors that are not lengths say nothing about the box's width;
        the padding asked for stands and the check further down decides."""
        modeller = _Modeller(across_nm=0.1)
        modeller.getPeriodicBoxVectors = lambda: [[None] * 3] * 3
        assert self._solvate(modeller, cutoff=1.5, padding=1.0) == 1.0
        assert modeller.attempts == [1.0]


class TestSurfaceAreaIsOfTheSolute:
    """Solvent-accessible area computed with the solvent present is wrong.

    The probe stands for a solvent molecule, so including the actual water
    occludes the surface whose accessibility is being measured. The
    orchestrator's scope selection resolves to the solute and never met
    this; a direct call did, and paid for it in both senses.
    """

    @staticmethod
    def _solvated(n_water=800, seed=0):
        import mdtraj as md
        import numpy as np

        rng = np.random.default_rng(seed)
        top = md.Topology()
        chain = top.add_chain()
        positions = []
        for i in range(40):
            res = top.add_residue("ALA", chain, resSeq=i + 1)
            for k, (name, element) in enumerate((
                    ("N", md.element.nitrogen), ("CA", md.element.carbon),
                    ("C", md.element.carbon), ("O", md.element.oxygen))):
                top.add_atom(name, element, res)
                positions.append([i * 0.38, 0.12 * k, 0.0])
        water = top.add_chain()
        for i in range(n_water):
            res = top.add_residue("HOH", water, resSeq=i + 1)
            top.add_atom("O", md.element.oxygen, res)
            positions.append(list(rng.random(3) * 6.0 - np.array([0, 3, 3])))
        xyz = np.tile(np.array(positions)[None], (3, 1, 1))
        xyz = xyz + rng.normal(scale=0.002, size=xyz.shape)
        return md.Trajectory(xyz.astype("float32"), top)

    def test_the_default_leaves_the_solvent_out(self):
        from fastmdxplora.analysis.sasa import SASA

        assert SASA().selection == "protein"

    def test_the_solvent_would_occlude_the_surface(self):
        """Not merely slower: a different and smaller number.

        With water in the system the protein's own atoms are buried by it,
        so the area reported is not the one anyone means by SASA.
        """
        import numpy as np

        from fastmdxplora.analysis.sasa import SASA

        traj = self._solvated()
        protein_only = SASA(selection="protein").compute(traj)
        everything = SASA(selection="all").compute(traj)

        solute_area = float(np.mean(protein_only["sasa_nm2"]))
        whole_area = float(np.mean(everything["sasa_nm2"]))
        assert solute_area > 0
        assert not np.isclose(solute_area, whole_area, rtol=0.05), (
            "including the solvent must change the answer, or this test "
            "is not testing anything")
