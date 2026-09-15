"""A shell count, checked against geometries whose answer is known by hand."""

from __future__ import annotations

import mdtraj as md
import numpy as np
import pytest

from fastmdxplora.analysis.coordination_number import CoordinationNumber
from fastmdxplora.refusals import StudyError


def _octahedron(d=0.30, box=4.0, frames=3, far=0.9):
    """One centre with six neighbours at ``d`` and six more at ``far``.

    Simple cubic coordination: the answer at any cutoff between ``d`` and
    ``far`` is exactly six, by counting rather than by comparison against
    another implementation.
    """
    top = md.Topology()
    chain = top.add_chain()
    centre_res = top.add_residue("CEN", chain, resSeq=1)
    top.add_atom("X", md.element.carbon, centre_res)

    shell = top.add_chain()
    positions = [np.zeros(3)]
    for radius in (d, far):
        for axis in range(3):
            for sign in (+1, -1):
                offset = np.zeros(3)
                offset[axis] = sign * radius
                positions.append(offset)
    for i in range(len(positions) - 1):
        res = top.add_residue("HOH", shell, resSeq=i + 1)
        top.add_atom("O", md.element.oxygen, res)

    centre = np.full(3, box / 2.0)
    xyz = np.tile(
        np.asarray([centre + p for p in positions], dtype=np.float32),
        (frames, 1, 1))
    traj = md.Trajectory(xyz, top)
    traj.unitcell_lengths = np.tile([box, box, box], (frames, 1))
    traj.unitcell_angles = np.tile([90.0, 90.0, 90.0], (frames, 1))
    return traj


def _gas(n_a=30, n_b=300, box=4.0, frames=20, seed=5):
    """Points placed at random: no shell to find."""
    rng = np.random.default_rng(seed)
    top = md.Topology()
    solute = top.add_chain()
    for i in range(n_a):
        res = top.add_residue("ALA", solute, resSeq=i + 1)
        top.add_atom("CA", md.element.carbon, res)
    water = top.add_chain()
    for i in range(n_b):
        res = top.add_residue("HOH", water, resSeq=i + 1)
        top.add_atom("O", md.element.oxygen, res)
    xyz = rng.random((frames, n_a + n_b, 3)) * box
    traj = md.Trajectory(xyz.astype(np.float32), top)
    traj.unitcell_lengths = np.tile([box, box, box], (frames, 1))
    traj.unitcell_angles = np.tile([90.0, 90.0, 90.0], (frames, 1))
    return traj


def _hydrated(n_solute=25, shell_n=6, d=0.29, n_bulk=900, box=4.0,
              frames=25, seed=11):
    """Solute atoms each carrying six waters at ``d``, in a bulk of others.

    A first shell that is actually there, so the cutoff search has
    something to find and the refusal above is shown to be about the
    absence of structure rather than about the search failing generally.
    """
    rng = np.random.default_rng(seed)
    top = md.Topology()
    solute = top.add_chain()
    for i in range(n_solute):
        res = top.add_residue("ALA", solute, resSeq=i + 1)
        top.add_atom("CA", md.element.carbon, res)
    water = top.add_chain()
    for i in range(n_solute * shell_n + n_bulk):
        res = top.add_residue("HOH", water, resSeq=i + 1)
        top.add_atom("O", md.element.oxygen, res)

    frames_xyz = []
    for _ in range(frames):
        centres = rng.random((n_solute, 3)) * box
        shell = []
        for centre in centres:
            direction = rng.normal(size=(shell_n, 3))
            direction /= np.linalg.norm(direction, axis=1)[:, None]
            radius = d + rng.normal(0.0, 0.01, size=(shell_n, 1))
            shell.append(centre + direction * radius)
        bulk = rng.random((n_bulk, 3)) * box
        frames_xyz.append(
            np.vstack([centres, np.vstack(shell), bulk]) % box)

    traj = md.Trajectory(np.asarray(frames_xyz, dtype=np.float32), top)
    traj.unitcell_lengths = np.tile([box, box, box], (frames, 1))
    traj.unitcell_angles = np.tile([90.0, 90.0, 90.0], (frames, 1))
    return traj


class TestTheCount:
    """Six neighbours is six neighbours."""

    def test_a_cutoff_inside_the_gap_counts_the_first_shell(self):
        result = CoordinationNumber(
            selection_a="name X", selection_b="name O",
            cutoff=0.45).compute(_octahedron())

        assert result.shape == (3,)
        assert np.allclose(result, 6.0)

    def test_a_wider_cutoff_reaches_the_second_shell(self):
        result = CoordinationNumber(
            selection_a="name X", selection_b="name O",
            cutoff=0.95).compute(_octahedron())

        assert np.allclose(result, 12.0)

    def test_a_cutoff_below_the_first_shell_counts_nothing(self):
        result = CoordinationNumber(
            selection_a="name X", selection_b="name O",
            cutoff=0.20).compute(_octahedron())

        assert np.allclose(result, 0.0)

    def test_per_atom_divides_by_the_first_selection(self):
        """Counting pairs instead would scale with how many were selected."""
        traj = _octahedron()
        total = CoordinationNumber(
            selection_a="name O", selection_b="name X",
            cutoff=0.45, per_atom=False).compute(traj)
        per_atom = CoordinationNumber(
            selection_a="name O", selection_b="name X",
            cutoff=0.45, per_atom=True).compute(traj)

        n_o = len(traj.topology.select("name O"))
        assert np.allclose(total, 6.0)
        assert np.allclose(per_atom, 6.0 / n_o)

    def test_an_atom_does_not_coordinate_itself(self):
        """Overlapping selections would otherwise count a zero separation."""
        result = CoordinationNumber(
            selection_a="name O", selection_b="name O",
            cutoff=0.45, per_atom=False).compute(_octahedron())

        # Each inner-shell oxygen sees the four other inner-shell oxygens
        # that are not diametrically opposite it, at d*sqrt(2) = 0.424 nm,
        # and none of them is itself.
        assert float(result[0]) == pytest.approx(24.0)


class TestTheCutoffIsTheMeasurement:
    """The number depends entirely on the radius, so it is never assumed."""

    def test_the_default_reads_the_cutoff_off_this_run(self):
        """A default that measures, rather than one that assumes a number.

        The setting can say what it would do -- which is what a form, a
        config template and the help all need -- without any literal radius
        being chosen on the system's behalf.
        """
        analysis = CoordinationNumber(
            selection_a="name X", selection_b="name O")

        assert analysis.cutoff == "rdf"

    def test_an_emptied_cutoff_is_refused(self):
        """There is no number to fall back to if the word is removed."""
        with pytest.raises(StudyError) as raised:
            CoordinationNumber(
                selection_a="name X", selection_b="name O", cutoff=None)

        assert raised.value.code == "analysis.option.missing_companion"

    def test_it_is_left_out_of_the_automatic_plan(self):
        """A shell round a pair nobody named is not a question anyone asked."""
        assert CoordinationNumber.requires_naming is True

    def test_a_word_that_is_not_rdf_is_refused(self):
        with pytest.raises(StudyError) as raised:
            CoordinationNumber(
                selection_a="name X", selection_b="name O", cutoff="first")

        assert raised.value.code == "analysis.option.not_permitted"

    def test_a_negative_cutoff_is_refused(self):
        with pytest.raises(StudyError) as raised:
            CoordinationNumber(
                selection_a="name X", selection_b="name O", cutoff=-0.3)

        assert raised.value.code == "analysis.option.out_of_range"

    def test_an_explicit_cutoff_is_recorded_as_given(self):
        analysis = CoordinationNumber(
            selection_a="name X", selection_b="name O", cutoff=0.45)
        analysis.compute(_octahedron())

        assert analysis.findings["shell"]["cutoff_nm"] == pytest.approx(0.45)
        assert "cutoff_from_rdf_nm" not in analysis.findings["shell"]


class TestWhatItRefuses:
    def test_a_cutoff_past_half_the_box_is_refused(self):
        """Past L/2 the shell is a partial sphere wearing a whole one's name."""
        analysis = CoordinationNumber(
            selection_a="name X", selection_b="name O", cutoff=3.0)

        with pytest.raises(StudyError) as raised:
            analysis.compute(_octahedron(box=4.0))

        assert "half the smallest box" in str(raised.value)
        assert raised.value.code == "analysis.option.out_of_range"

    def test_a_trajectory_without_a_box_is_refused(self):
        traj = _octahedron()
        traj.unitcell_lengths = None
        traj.unitcell_angles = None

        with pytest.raises(StudyError) as raised:
            CoordinationNumber(
                selection_a="name X", selection_b="name O",
                cutoff=0.45).compute(traj)

        assert raised.value.code == "analysis.system.inapplicable"

    def test_an_empty_selection_is_refused(self):
        with pytest.raises(StudyError) as raised:
            CoordinationNumber(
                selection_a="resname NOPE", selection_b="name O",
                cutoff=0.45).compute(_octahedron())

        assert raised.value.code == "analysis.selection.empty"

    def test_a_structureless_run_has_no_shell_to_read_a_cutoff_from(self):
        """`cutoff="rdf"` refuses rather than returning the argmin of noise.

        Randomly placed points have a tallest bin like any other curve, and
        at small r it stands well above the *bulk* scatter -- which is how a
        gas comes to report a hydration shell. The scatter is taken at the
        peak's own radius instead, where counting noise is far larger.
        """
        with pytest.raises(StudyError) as raised:
            CoordinationNumber(
                selection_a="name CA", selection_b="name O",
                cutoff="rdf").compute(_gas())

        assert "no first shell resolved" in str(raised.value)
        assert raised.value.code == "analysis.sampling.no_variance"


class TestReadingTheCutoffOffTheCurve:
    """`cutoff="rdf"` on a run that has a shell to find."""

    def test_the_cutoff_lands_just_past_the_shell(self):
        analysis = CoordinationNumber(
            selection_a="name CA", selection_b="name O", cutoff="rdf")
        analysis.compute(_hydrated())

        cutoff = analysis.findings["shell"]["cutoff_from_rdf_nm"]
        # The shell sits at 0.29 nm with a 0.01 nm spread, so the first
        # minimum is just outside it and well before the bulk.
        assert 0.30 < cutoff < 0.40

    def test_the_count_recovers_the_shell_plus_its_background(self):
        """A coordination number counts everything inside the radius.

        Six waters were placed around each solute, and the number that
        comes out is larger -- because the rest of the box is also in there.
        Integrating g(r) to a cutoff never subtracts the bulk, so the
        expected count is the placed shell plus the uniform background
        occupying the same sphere. Checking against the shell alone would
        have marked a correct implementation wrong.
        """
        box, n_solute, shell_n, n_bulk = 4.0, 25, 6, 900
        analysis = CoordinationNumber(
            selection_a="name CA", selection_b="name O", cutoff="rdf")
        result = analysis.compute(
            _hydrated(n_solute=n_solute, shell_n=shell_n, n_bulk=n_bulk,
                      box=box))

        cutoff = analysis.findings["shell"]["cutoff_from_rdf_nm"]
        # Every water except this solute's own six is background.
        others = (n_solute * shell_n + n_bulk) - shell_n
        background = (others / box ** 3) * (4.0 / 3.0) * np.pi * cutoff ** 3
        expected = shell_n + background

        assert float(np.mean(result)) == pytest.approx(expected, rel=0.05)

    def test_it_records_where_the_cutoff_came_from(self):
        """A derived cutoff that did not say so would read as a given one."""
        analysis = CoordinationNumber(
            selection_a="name CA", selection_b="name O", cutoff="rdf")
        analysis.compute(_hydrated())

        assert "first minimum of g(r)" in analysis.findings["shell"]["read_from"]


class TestItJoinsTheRegisters:
    def test_the_schema_names_it(self):
        from fastmdxplora.config.schema import ANALYSIS_NAMES

        assert "coordination_number" in ANALYSIS_NAMES

    def test_it_declares_that_it_ignores_the_scope_selection(self):
        assert CoordinationNumber.honours_selection is False

    def test_it_declares_itself_a_time_series(self):
        """One value per frame, so the mean gets its equilibration check."""
        assert CoordinationNumber.time_series is True
