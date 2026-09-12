"""A cone needs an axis and a half-angle, and neither is a preference.

The axis is the direction the ligand actually leaves by. It is rarely the line
from the protein's centre through the site -- on the study this was written
from, the two are 42 to 61 degrees apart, and a cone built on the wrong one
either cuts the bound state or has to open so wide it restrains nothing. The
half-angle has to clear the path's own wandering, which is a property of the
path.

The windows of a finished study cannot supply either. Each is restrained in
distance and free in angle, so each drifts into its own patch of its own
shell: those per-window directions scattered over 20 to 96 degrees with no
common centre. That scatter is the same fact as the recombination's tail
failing to reach bulk -- window `i` measured the free energy of patch `i`.

The pull does supply both. It is one continuous trajectory from the site to
bulk, and every window is seeded from it, so a cone measured there is a cone
every window starts inside.

These tests build a pull whose way out is known: a ligand leaving along one
direction and swinging by a set angle to another, inside a protein that
tumbles and translates the whole time, in a box it crosses. The measurement
has to return the angle that was built in.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

from fastmdxplora.simulation.seeding import (  # noqa: E402
    GROUP_SIZES,
    _name_the_group,
    _rotations_onto_the_first,
    angles_at_the_site,
    atoms_opposite,
    centres_of,
    measure_along,
    measure_the_cone,
    path_along,
    separation,
)
from fastmdxplora.simulation.umbrella import (  # noqa: E402
    Cone,
    ConeToMeasure,
    cone_from_config,
    cone_the_windows_ran_under,
    narrowest_cone,
)

SITE = "resSeq 190 to 192 and name CA"


# ---------------------------------------------------------------------------
# A pull whose answer is known
# ---------------------------------------------------------------------------
def turn_about(axis, angle_rad: float) -> np.ndarray:
    """A rotation matrix, so a path can be bent by a stated number."""
    axis = np.asarray(axis, dtype=float)
    axis = axis / np.linalg.norm(axis)
    cross = np.array([[0.0, -axis[2], axis[1]],
                      [axis[2], 0.0, -axis[0]],
                      [-axis[1], axis[0], 0.0]])
    return (np.eye(3) + math.sin(angle_rad) * cross
            + (1.0 - math.cos(angle_rad)) * (cross @ cross))


def a_pull(frames: int = 240, turn_deg: float = 40.0,
           jitter_deg: float = 4.0, *, tumbling: bool = True,
           box_nm: float = 12.0, seed: int = 0):
    """A ligand leaving a site along a known path, in a tumbling protein.

    The protein is alpha carbons on a shell around the site, which is what an
    axis has to be anchored to. The ligand starts 0.4 nm out along `u0` and
    ends 2.0 nm out along `u1`, `turn_deg` away from it -- so the narrowest
    cone that holds the whole path is about half the turn, and that is the
    number the measurement has to find.
    """
    from mdtraj.core import element

    rng = np.random.default_rng(seed)
    topology = md.Topology()
    chain = topology.add_chain()
    site = [topology.add_atom(
        "CA", element.carbon,
        topology.add_residue("ALA", chain, resSeq=190 + i)) for i in range(3)]
    shell = 240
    for i in range(shell):
        topology.add_atom("CA", element.carbon,
                          topology.add_residue("ALA", chain, resSeq=300 + i))
    ligand_residue = topology.add_residue("BEN", topology.add_chain(),
                                          resSeq=1)
    for i in range(4):
        topology.add_atom(f"C{i}", element.carbon, ligand_residue)

    site_xyz = np.array([[0.0, 0.0, 0.05], [0.05, 0.0, -0.03],
                         [-0.05, 0.03, 0.0]])
    out = rng.normal(size=(shell, 3))
    out /= np.linalg.norm(out, axis=1)[:, None]
    shell_xyz = out * rng.uniform(0.5, 2.2, size=shell)[:, None]

    first = np.array([0.0, 0.0, 1.0])
    last = turn_about([1.0, 0.0, 0.0], math.radians(turn_deg)) @ first
    along = np.linspace(0.0, 1.0, frames)
    walk = ((1 - along)[:, None] * first + along[:, None] * last)
    walk /= np.linalg.norm(walk, axis=1)[:, None]
    wobble = rng.normal(scale=math.radians(jitter_deg) / math.sqrt(2.0),
                        size=(frames, 3))
    walk = walk + np.cross(wobble, walk)
    walk /= np.linalg.norm(walk, axis=1)[:, None]
    ligand_xyz = walk * (0.4 + 1.6 * along)[:, None]

    xyz = np.zeros((frames, topology.n_atoms, 3), dtype=np.float32)
    for f in range(frames):
        rotation = (np.eye(3) if (f == 0 or not tumbling)
                    else turn_about(rng.normal(size=3),
                                    rng.uniform(0.0, 2.0 * math.pi)))
        here = np.vstack([
            site_xyz,
            shell_xyz + rng.normal(scale=0.01, size=shell_xyz.shape),
            ligand_xyz[f] + np.array([[0.03, 0.0, 0.0], [-0.03, 0.0, 0.0],
                                      [0.0, 0.03, 0.0], [0.0, -0.03, 0.0]]),
        ])
        xyz[f] = (here @ rotation.T) + (0.5 * box_nm)

    trajectory = md.Trajectory(xyz, topology)
    trajectory.unitcell_vectors = np.tile(np.eye(3) * box_nm, (frames, 1, 1))
    return trajectory, first, last, [int(a.index) for a in site]


@pytest.fixture(scope="module")
def pull():
    return a_pull()


def degrees_between(a, b) -> float:
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    return float(np.degrees(np.arccos(np.clip(
        (a @ b) / (np.linalg.norm(a) * np.linalg.norm(b)), -1.0, 1.0))))


# ---------------------------------------------------------------------------
class TestTheDirectionIsMeasuredInTheProteinsFrame:

    def test_the_path_comes_back_the_way_it_was_built(self, pull):
        """First frame along `u0`, last along `u1`, through a tumbling
        protein."""
        trajectory, first, last, _ = pull
        _, direction = path_along(trajectory, "BEN", SITE)
        assert degrees_between(direction[0], first) < 9.0
        assert degrees_between(direction[-1], last) < 9.0

    def test_the_turn_is_the_turn_that_was_built(self, pull):
        trajectory, _, _, _ = pull
        _, direction = path_along(trajectory, "BEN", SITE)
        assert degrees_between(direction[0], direction[-1]) == pytest.approx(
            40.0, abs=6.0)

    def test_the_distance_agrees_with_the_one_the_seeder_uses(self, pull):
        """Same two centres, same minimum image -- a second answer here would
        mean the windows were seeded at distances the cone was not measured
        at."""
        trajectory, _, _, _ = pull
        distance, _ = path_along(trajectory, "BEN", SITE)
        assert np.allclose(distance, measure_along(trajectory, "BEN", SITE),
                           atol=1e-5)

    def test_the_protein_tumbling_is_taken_out(self):
        """The same path, with and without the protein turning under it.

        Left in, the protein's rotation is written into every angle and a
        ligand sitting still appears to swing about the site -- which would
        set the width of a restraint that has to hold a study.
        """
        still, _, _, _ = a_pull(tumbling=False, seed=3)
        turning, _, _, _ = a_pull(tumbling=True, seed=3)
        _, quiet = path_along(still, "BEN", SITE)
        _, spun = path_along(turning, "BEN", SITE)
        apart = [degrees_between(a, b) for a, b in zip(quiet, spun)]
        assert max(apart) < 6.0

    def test_a_laboratory_frame_would_have_given_a_useless_cone(self, pull):
        """The measurement is worth making: unrotated, the same path needs a
        cone several times wider."""
        trajectory, _, _, _ = pull
        ligand = trajectory.topology.select("resname BEN")
        site = trajectory.topology.select(SITE)
        raw = separation(trajectory, ligand, site)
        raw = raw / np.linalg.norm(raw, axis=1)[:, None]
        _, wide = narrowest_cone(raw)
        _, direction = path_along(trajectory, "BEN", SITE)
        _, narrow = narrowest_cone(direction)
        assert wide > 3.0 * narrow

    def test_a_ligand_across_the_boundary_is_still_beside_the_site(self):
        """Minimum image first, rotation after.

        Superposing rotates the coordinates and leaves the box vectors
        behind, so a minimum image taken after alignment is taken in a box
        that no longer matches the molecule. Measured that way this came back
        126 degrees off axis -- a direction that would put the ligand inside
        the protein.
        """
        trajectory, first, last, _ = a_pull(box_nm=5.0, seed=5)
        wrapped = trajectory[:]
        ligand = wrapped.topology.select("resname BEN")
        # Put the ligand in the neighbouring image, as a wrapped frame does.
        wrapped.xyz[:, ligand, :] += np.array([0.0, 0.0, 5.0],
                                              dtype=np.float32)
        before, straight = path_along(trajectory, "BEN", SITE)
        after, across = path_along(wrapped, "BEN", SITE)
        assert np.allclose(before, after, atol=1e-4)
        assert max(degrees_between(a, b)
                   for a, b in zip(straight, across)) < 1.0

    def test_a_frame_needs_three_atoms_to_be_fixed_to(self, pull):
        trajectory, _, _, _ = pull
        with pytest.raises(ValueError, match="three are needed"):
            path_along(trajectory, "BEN", SITE, frame_selection="resSeq 190")

    def test_the_alignment_is_a_rotation_and_not_a_reflection(self, pull):
        """A reflection superposes beautifully and mirrors the ligand onto the
        wrong side of the protein."""
        trajectory, _, _, _ = pull
        atoms = trajectory.topology.select("name CA")
        rotations = _rotations_onto_the_first(trajectory.xyz[:, atoms, :])
        determinants = np.linalg.det(rotations)
        assert np.allclose(determinants, 1.0, atol=1e-5)
        assert np.allclose(rotations[0], np.eye(3), atol=1e-5)


# ---------------------------------------------------------------------------
class TestTheAxisIsSearchedForRatherThanAveraged:

    def test_a_turning_path_beats_its_own_mean(self, pull):
        """The mean of a path that bends sits in the middle of the bend and is
        far from both ends -- which is a wider cone than necessary, or a
        narrower one than fits."""
        trajectory, _, _, _ = pull
        _, direction = path_along(trajectory, "BEN", SITE)
        axis, held = narrowest_cone(direction)
        mean = direction.mean(axis=0)
        mean = mean / np.linalg.norm(mean)
        by_the_mean = float(np.percentile(np.degrees(np.arccos(np.clip(
            direction @ mean, -1.0, 1.0))), 98.0))
        assert held <= by_the_mean + 1e-9

    def test_it_finds_the_middle_of_the_turn(self, pull):
        """Half the turn away from each end, which is where the smallest cone
        holding both has to point."""
        trajectory, first, last, _ = pull
        _, direction = path_along(trajectory, "BEN", SITE)
        axis, held = narrowest_cone(direction)
        assert degrees_between(axis, first) == pytest.approx(20.0, abs=6.0)
        assert degrees_between(axis, last) == pytest.approx(20.0, abs=6.0)
        assert held == pytest.approx(20.0, abs=6.0)

    def test_a_path_that_does_not_turn_needs_almost_no_cone(self):
        trajectory, first, _, _ = a_pull(turn_deg=0.0, jitter_deg=2.0, seed=7)
        _, direction = path_along(trajectory, "BEN", SITE)
        axis, held = narrowest_cone(direction)
        assert degrees_between(axis, first) < 5.0
        assert held < 8.0


# ---------------------------------------------------------------------------
class TestTheAxisIsAGroupOfAtoms:

    def test_the_group_sits_behind_the_site(self, pull):
        """PLUMED's cone is an angle at the site that a *lower* wall holds
        open, so the atoms have to be on the far side from the way out."""
        trajectory, first, last, site_atoms = pull
        _, direction = path_along(trajectory, "BEN", SITE)
        axis, _ = narrowest_cone(direction)
        group = atoms_opposite(trajectory, SITE, axis, count=12)
        towards = separation(trajectory, np.asarray(group),
                             np.asarray(site_atoms))[0]
        assert degrees_between(-towards, axis) < 15.0

    def test_it_takes_alpha_carbons_and_not_the_ligand(self, pull):
        trajectory, _, _, _ = pull
        _, direction = path_along(trajectory, "BEN", SITE)
        axis, _ = narrowest_cone(direction)
        group = atoms_opposite(trajectory, SITE, axis, count=16)
        names = {trajectory.topology.atom(i).name for i in group}
        residues = {trajectory.topology.atom(i).residue.name for i in group}
        assert names == {"CA"}
        assert "BEN" not in residues

    def test_atoms_across_the_protein_are_left_out(self, pull):
        """A residue 4 nm away says more about the protein's shape than about
        the way out of this site."""
        trajectory, _, _, site_atoms = pull
        _, direction = path_along(trajectory, "BEN", SITE)
        axis, _ = narrowest_cone(direction)
        group = atoms_opposite(trajectory, SITE, axis, count=48,
                               band_nm=(0.4, 1.0))
        away = np.linalg.norm(
            separation(trajectory, np.asarray(group),
                       np.asarray(site_atoms))[0])
        assert away <= 1.0

    def test_an_empty_band_says_so(self, pull):
        trajectory, _, _, _ = pull
        _, direction = path_along(trajectory, "BEN", SITE)
        axis, _ = narrowest_cone(direction)
        with pytest.raises(ValueError, match="alpha carbon"):
            atoms_opposite(trajectory, SITE, axis, band_nm=(9.0, 9.5))

    def test_the_angle_is_the_one_plumed_will_measure(self, pull):
        """`ANGLE ATOMS=cone_axis,site,lig`, worked out here the long way."""
        trajectory, _, _, site_atoms = pull
        _, direction = path_along(trajectory, "BEN", SITE)
        axis, _ = narrowest_cone(direction)
        group = np.asarray(atoms_opposite(trajectory, SITE, axis, count=12))
        ligand = trajectory.topology.select("resname BEN")
        site = np.asarray(site_atoms)
        ours = angles_at_the_site(trajectory, ligand, site, list(group))

        middle = centres_of(trajectory, site)
        to_ligand = centres_of(trajectory, ligand) - middle
        to_axis = centres_of(trajectory, group) - middle
        by_hand = np.array([
            180.0 - degrees_between(a, b)
            for a, b in zip(to_ligand, to_axis)])
        assert np.allclose(ours, by_hand, atol=1e-3)


# ---------------------------------------------------------------------------
class TestTheHalfAngleIsWhatThePathNeeds:

    def test_it_is_the_percentile_widened_by_the_margin(self, pull):
        trajectory, _, _, _ = pull
        record = measure_the_cone(trajectory, "BEN", SITE)
        assert record["half_angle_deg"] == math.ceil(
            record["held_within_deg"] * record["margin"])

    def test_it_clears_the_turn_that_was_built(self, pull):
        """About half the turn, plus the path's own wandering, plus a fifth."""
        trajectory, _, _, _ = pull
        record = measure_the_cone(trajectory, "BEN", SITE)
        assert 22.0 <= record["half_angle_deg"] <= 34.0

    def test_a_sharper_turn_needs_a_wider_cone(self):
        straight = measure_the_cone(a_pull(turn_deg=10.0, seed=11)[0], "BEN",
                                    SITE)
        bent = measure_the_cone(a_pull(turn_deg=80.0, seed=11)[0], "BEN", SITE)
        assert bent["half_angle_deg"] > straight["half_angle_deg"] + 20.0

    def test_the_margin_opens_it_and_nothing_else(self, pull):
        trajectory, _, _, _ = pull
        tight = measure_the_cone(trajectory, "BEN", SITE, margin=1.0)
        loose = measure_the_cone(trajectory, "BEN", SITE, margin=1.5)
        assert tight["held_within_deg"] == pytest.approx(
            loose["held_within_deg"])
        assert loose["half_angle_deg"] > tight["half_angle_deg"]

    def test_keeping_less_of_the_path_gives_a_narrower_cone(self, pull):
        trajectory, _, _, _ = pull
        most = measure_the_cone(trajectory, "BEN", SITE, keep=98.0)
        half = measure_the_cone(trajectory, "BEN", SITE, keep=50.0)
        assert half["half_angle_deg"] < most["half_angle_deg"]

    def test_the_group_is_chosen_by_what_it_achieves(self, pull):
        """Its size is a result, not a constant somebody picked: no fixed
        count does better than the one the search kept."""
        trajectory, _, _, _ = pull
        record = measure_the_cone(trajectory, "BEN", SITE)
        _, direction = path_along(trajectory, "BEN", SITE)
        axis, _ = narrowest_cone(direction)
        ligand = trajectory.topology.select("resname BEN")
        site = trajectory.topology.select(SITE)
        distance, _ = path_along(trajectory, "BEN", SITE)
        best = []
        for size in GROUP_SIZES:
            group = atoms_opposite(trajectory, SITE, axis, count=size)
            best.append(float(np.percentile(
                angles_at_the_site(trajectory, ligand, site, group), 98.0)))
        assert record["held_within_deg"] == pytest.approx(min(best), abs=0.01)

    def test_the_wall_is_clear_of_the_bound_end(self, pull):
        """The correction assumes the bound pose fits inside the cone. If the
        bound end of the path does not, that is visible here rather than after
        a week of sampling."""
        trajectory, _, _, _ = pull
        record = measure_the_cone(trajectory, "BEN", SITE)
        assert record["bound_end_deg"] < record["half_angle_deg"]

    def test_a_pull_that_never_left_is_refused(self):
        """A cone read from a ligand that stayed put is a cone around its
        rattling."""
        trajectory, _, _, _ = a_pull(seed=13)
        still = trajectory[:]
        ligand = still.topology.select("resname BEN")
        site = still.topology.select(SITE)
        still.xyz[:, ligand, :] = still.xyz[:, site, :].mean(
            axis=1, keepdims=True)
        with pytest.raises(ValueError, match="no path to read a direction"):
            measure_the_cone(still, "BEN", SITE)


# ---------------------------------------------------------------------------
class TestAStudyCanNameTheGroupItself:

    def named_from_the_search(self, trajectory) -> str:
        """A selection naming the residues the search would have chosen."""
        searched = measure_the_cone(trajectory, "BEN", SITE)
        numbers = sorted({trajectory.topology.atom(i).residue.resSeq
                          for i in searched["axis_atoms"]})
        return f"resSeq {' '.join(str(n) for n in numbers)} and name CA"

    def test_a_named_axis_is_used_and_the_angle_still_measured(self, pull):
        trajectory, _, _, _ = pull
        named = self.named_from_the_search(trajectory)
        record = measure_the_cone(trajectory, "BEN", SITE,
                                  axis_selection=named)
        assert record["axis_selection"] == named
        assert record["axis_atoms"] == sorted(
            int(i) for i in trajectory.topology.select(named))
        assert record["half_angle_deg"] == math.ceil(
            record["held_within_deg"] * 1.2)

    def test_a_named_axis_that_matches_nothing_says_so(self, pull):
        trajectory, _, _, _ = pull
        with pytest.raises(ValueError, match="matched no atoms"):
            measure_the_cone(trajectory, "BEN", SITE,
                             axis_selection="resSeq 99999 and name CA")

    def test_a_group_on_the_wrong_side_is_refused_rather_than_clipped(
            self, pull):
        """The angle opens *away* from the named group, so a group sitting
        where the ligand goes asks for a cone of most of a sphere -- which
        restrains nothing and would earn a correction of nothing while looking
        like a study that had one."""
        trajectory, _, _, _ = pull
        _, direction = path_along(trajectory, "BEN", SITE)
        axis, _ = narrowest_cone(direction)
        ahead = atoms_opposite(trajectory, SITE, -axis, count=8)
        numbers = sorted({trajectory.topology.atom(i).residue.resSeq
                          for i in ahead})
        named = f"resSeq {' '.join(str(n) for n in numbers)} and name CA"
        with pytest.raises(ValueError, match="behind the site"):
            measure_the_cone(trajectory, "BEN", SITE, axis_selection=named)

    def test_a_path_with_no_settled_direction_is_refused(self):
        """A ligand that leaves by several routes needs a coordinate that
        follows one of them, not a wall around all of them."""
        trajectory, _, _, _ = a_pull(tumbling=False, seed=17)
        scattered = trajectory[:]
        ligand = scattered.topology.select("resname BEN")
        site = scattered.topology.select(SITE)
        rng = np.random.default_rng(17)
        every_way = rng.normal(size=(scattered.n_frames, 3))
        every_way /= np.linalg.norm(every_way, axis=1)[:, None]
        radius = np.linspace(0.4, 2.0, scattered.n_frames)[:, None]
        middle = scattered.xyz[:, site, :].mean(axis=1)
        scattered.xyz[:, ligand, :] = (
            middle + every_way * radius)[:, None, :].astype(np.float32)
        with pytest.raises(ValueError, match="no settled direction"):
            measure_the_cone(scattered, "BEN", SITE)


# ---------------------------------------------------------------------------
class TestTheRecordSaysWhatRan:

    def test_the_atoms_are_what_the_restraint_uses(self, pull):
        trajectory, _, _, _ = pull
        record = measure_the_cone(trajectory, "BEN", SITE)
        cone = Cone.from_record(record)
        assert cone.axis_atoms == tuple(record["axis_atoms"])
        assert cone.plumed_lines()[0].startswith("cone_axis: COM ATOMS=")

    def test_the_selection_it_writes_down_picks_the_same_atoms(self, pull):
        """The string is documentation and `axis_atoms` is the restraint, so
        the string is only written when it is true."""
        trajectory, _, _, _ = pull
        record = measure_the_cone(trajectory, "BEN", SITE)
        named = record["axis_selection"]
        if named.startswith("resSeq"):
            assert sorted(int(i) for i in
                          trajectory.topology.select(named)) == record[
                              "axis_atoms"]

    def test_an_ambiguous_numbering_is_described_rather_than_claimed(self):
        """3PTB carries chymotrypsin numbering, where 184 and 184A are
        different residues that `resSeq 184` matches together."""
        from mdtraj.core import element

        topology = md.Topology()
        chain = topology.add_chain()
        atoms = [topology.add_atom(
            "CA", element.carbon,
            topology.add_residue("ALA", chain, resSeq=184)) for _ in range(2)]
        named = _name_the_group(topology, [int(atoms[0].index)])
        assert not named.startswith("resSeq")
        assert "184" in named

    def test_a_cone_survives_a_trip_through_a_config_block(self, pull):
        trajectory, _, _, _ = pull
        record = measure_the_cone(trajectory, "BEN", SITE)
        block = {k: record[k] for k in ("half_angle_deg", "force_constant",
                                        "axis_selection", "axis_atoms")}
        cone = cone_from_config(block)
        assert isinstance(cone, Cone)
        assert cone.axis_atoms == tuple(record["axis_atoms"])
        assert cone.half_angle_deg == record["half_angle_deg"]

    def test_a_cone_still_to_be_measured_is_not_a_cone(self):
        assert Cone.from_record(ConeToMeasure().as_record()) is None
        assert Cone.from_record(None) is None

    def test_an_unmeasured_cone_cannot_write_a_restraint(self):
        with pytest.raises(ValueError, match="group of atoms"):
            Cone(half_angle_deg=30.0).plumed_lines()


# ---------------------------------------------------------------------------
class TestTheAnalysisTakesTheConeTheRunsHad:

    def write_window(self, directory, cone):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "umbrella_window.json").write_text(
            json.dumps({"cone": cone}), encoding="utf-8")
        return directory

    def test_it_is_read_back_from_the_windows(self, tmp_path):
        """Not from the config: a cone measured from the pull was never
        written in the config at all."""
        record = Cone(half_angle_deg=73.0, axis_atoms=(4, 5, 6)).as_record()
        directories = {i: self.write_window(tmp_path / f"w{i}", record)
                       for i in range(3)}
        cone = cone_the_windows_ran_under(directories)
        assert cone.half_angle_deg == 73.0
        assert cone.axis_atoms == (4, 5, 6)

    def test_windows_without_a_cone_give_none(self, tmp_path):
        directories = {i: self.write_window(tmp_path / f"w{i}", None)
                       for i in range(3)}
        assert cone_the_windows_ran_under(directories) is None
        assert cone_the_windows_ran_under({}) is None

    def test_two_different_cones_cannot_be_recombined(self, tmp_path):
        """A curve stitched across them would be stitched across two
        different reference states."""
        directories = {
            0: self.write_window(tmp_path / "w0",
                                 Cone(half_angle_deg=30.0).as_record()),
            1: self.write_window(tmp_path / "w1",
                                 Cone(half_angle_deg=45.0).as_record()),
        }
        with pytest.raises(ValueError, match="not all run under the same"):
            cone_the_windows_ran_under(directories)

    def test_an_unmeasured_cone_has_no_way_to_reach_plumed(self):
        """Held as its own type rather than as a `Cone` with holes in it: what
        a window needs is an angle and a group of atoms, and there is no
        sensible default for either."""
        assert not hasattr(ConeToMeasure(), "plumed_lines")
        assert isinstance(cone_from_config("auto"), ConeToMeasure)

    def test_the_correction_follows_the_measured_angle(self, pull):
        """Wider cone, smaller correction -- and it is the measured cap, not
        the textbook one, because the wall is soft."""
        trajectory, _, _, _ = pull
        narrow = Cone.from_record(
            measure_the_cone(trajectory, "BEN", SITE, margin=1.0))
        wide = Cone.from_record(
            measure_the_cone(trajectory, "BEN", SITE, margin=1.6))
        assert wide.correction_kjmol() < narrow.correction_kjmol()
        assert wide.solid_angle() > narrow.solid_angle()


# ---------------------------------------------------------------------------
class TestTheMeasurementReachesTheWindows:
    """From `cone: auto` in a config to an angle on every window's restraint.

    Two places, because there are two readers. The windows run under PLUMED
    and need the atoms; the recombination needs the cap, and it rebuilds the
    plan from the expanded systems rather than from anything the run carried.
    A study whose measurement reached only the first would run inside a cone
    and correct for none.
    """

    def a_study(self, tmp_path, cone: str):
        from fastmdxplora.batch.explorer import BatchExplorer

        config = tmp_path / "study.yml"
        config.write_text(
            "output: out\n"
            "systems:\n"
            "  - system: 181L\n"
            "simulation:\n"
            "  umbrella:\n"
            "    collective_variable: ligand_distance\n"
            "    ligand_name: BEN\n"
            '    select_atoms: "name CA"\n'
            "    centres: [0.4, 0.6, 0.8]\n"
            "    force_constant: 3000\n"
            f"{cone}",
            encoding="utf-8")
        return BatchExplorer(config=config, output_dir=str(tmp_path / "out"))

    def wrote(self, study, record):
        seeds = Path(study.output_dir) / "seeds"
        seeds.mkdir(parents=True, exist_ok=True)
        (seeds / "cone.json").write_text(json.dumps(record), encoding="utf-8")

    def cones_on_the_windows(self, study):
        from fastmdxplora.simulation.umbrella import plan_from_expanded

        planned = plan_from_expanded(study._raw or {}).cone
        per_window = [
            ((spec.options.get("simulation") or {}).get("umbrella") or {}
             ).get("cone") for spec in study.run_specs]
        return planned, per_window

    def test_a_config_asking_for_one_starts_without_it(self, tmp_path):
        study = self.a_study(tmp_path, "    cone: auto\n")
        planned, per_window = self.cones_on_the_windows(study)
        assert isinstance(planned, ConeToMeasure)
        assert all(w == "auto" for w in per_window)

    def test_the_measurement_lands_on_every_window_and_on_the_plan(
            self, tmp_path):
        study = self.a_study(tmp_path, "    cone: auto\n")
        self.wrote(study, {"half_angle_deg": 73.0, "force_constant": 5000.0,
                           "axis_selection": "resSeq 66 and name CA",
                           "axis_atoms": [11, 22, 33],
                           "held_within_deg": 61.0})

        study._give_each_window_its_cone()

        planned, per_window = self.cones_on_the_windows(study)
        assert isinstance(planned, Cone)
        assert planned.half_angle_deg == 73.0
        assert planned.axis_atoms == (11, 22, 33)
        assert all(w["half_angle_deg"] == 73.0 for w in per_window)
        assert all(w["axis_atoms"] == [11, 22, 33] for w in per_window)

    def test_nothing_beyond_the_cone_travels_with_it(self, tmp_path):
        """The diagnostics belong in the record, not in a restraint's
        settings: `cone_from_config` refuses a key it does not have, and it is
        right to."""
        study = self.a_study(tmp_path, "    cone: auto\n")
        self.wrote(study, {"half_angle_deg": 73.0, "force_constant": 5000.0,
                           "axis_selection": "resSeq 66 and name CA",
                           "axis_atoms": [11], "worst_deg": 96.0,
                           "frames": 2000, "spans_nm": [0.4, 2.0]})

        study._give_each_window_its_cone()

        _, per_window = self.cones_on_the_windows(study)
        assert set(per_window[0]) == {"half_angle_deg", "force_constant",
                                      "axis_selection", "axis_atoms"}

    def test_without_a_measurement_nothing_is_invented(self, tmp_path):
        study = self.a_study(tmp_path, "    cone: auto\n")

        study._give_each_window_its_cone()

        planned, per_window = self.cones_on_the_windows(study)
        assert isinstance(planned, ConeToMeasure)
        assert all(w == "auto" for w in per_window)

    def test_a_cone_the_study_wrote_out_is_left_alone(self, tmp_path):
        """A half-angle in the config is a choice, and a measurement does not
        overrule it."""
        study = self.a_study(
            tmp_path,
            "    cone:\n"
            "      half_angle_deg: 30\n"
            "      axis_selection: protein\n")
        self.wrote(study, {"half_angle_deg": 73.0, "force_constant": 5000.0,
                           "axis_selection": "resSeq 66 and name CA",
                           "axis_atoms": [11]})

        study._give_each_window_its_cone()

        planned, _ = self.cones_on_the_windows(study)
        assert planned.half_angle_deg == 30.0
        assert planned.axis_atoms is None

    def test_a_study_without_a_cone_is_untouched(self, tmp_path):
        study = self.a_study(tmp_path, "")

        study._give_each_window_its_cone()

        planned, per_window = self.cones_on_the_windows(study)
        assert planned is None
        assert per_window == [None, None, None]


# ---------------------------------------------------------------------------
class TestTheMeasurementIsWrittenDownBesideTheSeeds:

    def test_the_seeder_leaves_the_cone_it_measured(self, tmp_path, pull,
                                                    capsys):
        """Written rather than returned: the analysis needs it days later, and
        reading the pull a second time to find it would mean loading tens of
        thousands of atoms twice."""
        from fastmdxplora.simulation.seeding import _measure_the_cone_here

        trajectory, _, _, _ = pull
        _measure_the_cone_here(trajectory, "BEN", SITE, tmp_path / "seeds",
                               ConeToMeasure().as_asked())

        record = json.loads(
            (tmp_path / "seeds" / "cone.json").read_text(encoding="utf-8"))
        assert record["half_angle_deg"] == measure_the_cone(
            trajectory, "BEN", SITE)["half_angle_deg"]
        assert Cone.from_record(record).axis_atoms
        assert "Cone:" in capsys.readouterr().out

    def test_what_it_asks_for_is_what_it_measures(self, tmp_path, pull):
        from fastmdxplora.simulation.seeding import _measure_the_cone_here

        trajectory, _, _, _ = pull
        asked = ConeToMeasure(force_constant=2000.0, keep=90.0,
                              margin=1.4).as_asked()
        _measure_the_cone_here(trajectory, "BEN", SITE, tmp_path / "seeds",
                               asked)

        record = json.loads(
            (tmp_path / "seeds" / "cone.json").read_text(encoding="utf-8"))
        assert record["force_constant"] == 2000.0
        assert record["keep"] == 90.0
        assert record["margin"] == 1.4
