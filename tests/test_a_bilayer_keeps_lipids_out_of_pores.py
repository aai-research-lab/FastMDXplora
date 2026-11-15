"""A bilayer keeps its lipids out of the protein's pores.

OpenMM's `Modeller.addMembrane` shrinks the protein to half its width, removes
only the lipids that overlap the half-width protein, and grows the protein back
by position. 2POR is a trimer of hollow barrels: shrunk about the trimer's
axis, each lumen shrinks and moves inward, lipids are left where the pores will
be, and the growing walls cut through them. Four of five packings ran away to
"Particle coordinate is NaN", each on a lipid that had started in a pore, and
the one that packed held lipids in its pores. The lipids OpenMM would leave in
a pore are now removed from the patch before it is packed, and the pores are
filled with water after.

These tests need OpenMM's patch file and no force field: the protein is a ring
of carbon atoms spanning the slab, and the fill is run against a one-atom
template beside OpenMM's TIP3P.
"""

from __future__ import annotations

import io
import math

import numpy as np
import pytest

openmm = pytest.importorskip("openmm")

from openmm import Vec3, unit  # noqa: E402
from openmm.app import Modeller, Topology, element  # noqa: E402

from fastmdxplora.setup import membrane  # noqa: E402

#: The lipids' z range in OpenMM's POPC patch, as it lands about z = 0.
_Z_LOW, _Z_HIGH = -2.7, 3.3


def _protein(points: np.ndarray) -> tuple[Topology, list]:
    topology = Topology()
    chain = topology.addChain()
    for i, _ in enumerate(points):
        residue = topology.addResidue("RNG", chain, str(i + 1))
        topology.addAtom("C", element.carbon, residue)
    return topology, [Vec3(*map(float, p)) for p in points] * unit.nanometer


def _ring(radius: float = 2.0, spacing: float = 0.3) -> np.ndarray:
    """A hollow tube of carbon atoms along z, spacing nm apart, spanning the slab."""
    around = int(math.ceil(2 * math.pi * radius / spacing))
    angles = np.arange(around) * 2 * math.pi / around
    rows = [np.column_stack([radius * np.cos(angles), radius * np.sin(angles),
                             np.full(around, z)])
            for z in np.arange(_Z_LOW, _Z_HIGH + 1e-9, spacing)]
    return np.concatenate(rows)


def _cylinder(radius: float = 2.0, spacing: float = 0.3) -> np.ndarray:
    """The same tube filled: a solid protein of the same size, with no pore."""
    grid = np.arange(-radius, radius + 1e-9, spacing)
    disk = [(x, y) for x in grid for y in grid if x * x + y * y <= radius * radius]
    return np.array([(x, y, z) for z in np.arange(_Z_LOW, _Z_HIGH + 1e-9, spacing)
                     for x, y in disk])


def _lipids_in_pores(clearing: membrane.PoreClearing, points: np.ndarray,
                     padding: float = 2.0) -> int:
    """Lipids of the patch OpenMM leaves in place with a heavy atom in a pore, where it
    puts them: those within its 0.22 nm of the half-width protein it removes
    itself."""
    from scipy.spatial import cKDTree

    patch = clearing.patch
    xyz = membrane._in_nm(patch.positions)
    low, high = points.min(axis=0), points.max(axis=0)
    centre = np.array([(low[0] + high[0]) / 2, (low[1] + high[1]) / 2, 0.0])
    shift = centre - (xyz.min(axis=0) + xyz.max(axis=0)) / 2
    half = points - centre
    half[:, :2] *= 0.5
    near_half = cKDTree(half + centre)
    count = 0
    for residue in patch.topology.residues():
        if residue.name == "HOH":
            continue
        every = [a.index for a in residue.atoms()]
        if near_half.query_ball_point(xyz[every] + shift, 0.22, return_length=True).any():
            continue
        heavy = [a.index for a in residue.atoms() if a.element.symbol != "H"]
        count += membrane.lipid_in_a_pore(clearing.pores, xyz[heavy] + shift)
    return count


def test_a_ring_spanning_the_slab_loses_its_lumen_lipids_with_the_leaflets_equal():
    points = _ring()
    topology, positions = _protein(points)
    clearing = membrane.clear_the_pores(topology, positions, "POPC", padding_nm=2.0)

    assert clearing.pores, "the ring's lumen was not mapped as a pore"
    assert clearing.fallback is None
    assert clearing.patch is not None
    assert sum(clearing.pore_lipids) > 0
    upper = clearing.pore_lipids[0] + clearing.balancing_lipids[0]
    lower = clearing.pore_lipids[1] + clearing.balancing_lipids[1]
    assert upper == lower
    assert 0 in clearing.balancing_lipids
    assert _lipids_in_pores(clearing, points) == 0
    # The whole tiling, given a cell OpenMM tiles once.
    cell = clearing.patch.topology.getUnitCellDimensions().value_in_unit(unit.nanometer)
    assert cell[0] >= np.ptp(points[:, 0]) + 4.0 and cell[1] >= np.ptp(points[:, 1]) + 4.0


def test_a_solid_cylinder_loses_no_lipid_and_packs_from_the_lipid_s_name():
    topology, positions = _protein(_cylinder())
    clearing = membrane.clear_the_pores(topology, positions, "POPC", padding_nm=2.0)

    assert not clearing.pores
    assert clearing.patch is None and clearing.fallback is None
    assert clearing.pore_lipids == clearing.balancing_lipids == [0, 0]


def test_the_pore_map_finds_a_lumen_and_not_a_groove():
    """A C-shaped wall, open on one side, encloses nothing: the groove between
    subunits is open to the bilayer and is not a pore."""
    ring = _ring()
    angle = np.degrees(np.arctan2(ring[:, 1], ring[:, 0]))
    groove = ring[np.abs(angle) > 25]
    heavy = np.ones(len(ring), bool)
    lumen = membrane.pore_map(ring, heavy, -2.0, 2.0)
    open_side = membrane.pore_map(groove, heavy[:len(groove)], -2.0, 2.0)

    assert lumen and not open_side
    inner = math.pi * (2.0 - membrane.PORE_ATOM_RADIUS_NM) ** 2
    assert all(abs(area - inner) < 0.15 * inner for area in lumen.area_nm2())
    assert lumen.holds([[0, 0, 0]]).all() and not lumen.holds([[0, 0, 2.5]]).any()
    assert not lumen.holds([[3.0, 0, 0]]).any()
    # A lumen of about 3 nm2, a porin's at its narrowest above the eyelet,
    # is a pore too; a gap of a few cells between packed atoms is not.
    narrow = _ring(radius=1.2, spacing=0.25)
    small = membrane.pore_map(narrow, np.ones(len(narrow), bool), -2.0, 2.0)
    assert small and all(1.5 <= area < 4.0 for area in small.area_nm2() if area)
    tight = _ring(radius=0.55, spacing=0.25)
    assert not membrane.pore_map(tight, np.ones(len(tight), bool), -2.0, 2.0)


def test_the_patch_lands_where_openmm_s_own_tiling_puts_it():
    """OpenMM's own `addMembrane`, stopped where its relaxation would begin,
    with the lipid's name and with the cleared patch: the lipids it places from
    the patch are where it puts them from its own tiling, the water is the
    same, and only the lipids cleared are missing."""
    pytest.importorskip("openmm")
    from openmm.app import modeller as module

    points = _ring()
    topology, positions = _protein(points)
    clearing = membrane.clear_the_pores(topology, positions, "POPC", padding_nm=2.0)

    class Stop(Exception):
        pass

    class Context:
        def __init__(self, system, *args):
            self.size = system.getNumParticles()

        def setPositions(self, positions):
            started.append(np.array([[p[0], p[1], p[2]] for p in positions]))
            raise Stop

    class NoForceField:
        """Enough of a ForceField for OpenMM to reach its relaxation."""

        def createSystem(self, topology, **kwargs):
            system = openmm.System()
            nonbonded = openmm.NonbondedForce()
            for _ in topology.atoms():
                system.addParticle(1.0)
                nonbonded.addParticle(0.0, 0.3, 0.0)
            system.addForce(nonbonded)
            made.append(topology)
            return system

    def start(lipid_type):
        made.clear()
        started.clear()
        modeller = Modeller(topology, positions)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(module, "Context", Context)
            with pytest.raises(Stop):
                modeller.addMembrane(NoForceField(), lipidType=lipid_type,
                                     minimumPadding=2.0 * unit.nanometer)
        built = made[0]
        xyz = started[0][:built.getNumAtoms()]
        lipids, waters = {}, []
        for residue in built.residues():
            index = [a.index for a in residue.atoms()]
            if residue.name == "HOH":
                waters.append(xyz[index[0]])
            else:
                lipids[tuple(np.round(xyz[index].mean(axis=0), 6))] = xyz[index]
        return lipids, np.array(waters), built.getUnitCellDimensions()

    made: list = []
    started: list = []
    plain, plain_water, plain_box = start("POPC")
    cleared, cleared_water, cleared_box = start(clearing.patch)

    assert set(cleared) <= set(plain)
    assert len(plain) - len(cleared) == (sum(clearing.pore_lipids)
                                         + sum(clearing.balancing_lipids))
    assert max(np.abs(plain[k] - cleared[k]).max() for k in cleared) < 1e-9
    assert len(plain_water) == len(cleared_water)
    assert np.abs(np.sort(plain_water, axis=0) - np.sort(cleared_water, axis=0)).max() < 1e-9
    assert np.allclose(plain_box.value_in_unit(unit.nanometer),
                       cleared_box.value_in_unit(unit.nanometer))
    z = np.array([k[2] for k in cleared])
    assert (z >= z.mean()).sum() == (z < z.mean()).sum()


def test_the_patch_is_not_used_where_it_would_land_elsewhere(monkeypatch):
    """The tiling copies OpenMM's arithmetic. Where the check finds it would
    not land where OpenMM's own does, no patch is handed over and the bilayer
    is packed from the lipid's name: never silently from a wrong patch."""
    topology, positions = _protein(_ring())
    monkeypatch.setattr(membrane, "_lands_where_openmm_tiles",
                        lambda *a, **k: "its centre moved")
    clearing = membrane.clear_the_pores(topology, positions, "POPC", padding_nm=2.0)

    assert clearing.patch is None
    assert clearing.fallback == "its centre moved"
    assert sum(clearing.pore_lipids) > 0


def test_the_check_finds_a_tiling_that_moved():
    topology, positions = _protein(_ring())
    clearing = membrane.clear_the_pores(topology, positions, "POPC", padding_nm=2.0)
    patch = clearing.patch
    xyz = membrane._in_nm(patch.positions)
    original = membrane._in_nm(membrane._openmm_patch("POPC").positions)
    kept = []
    every = np.concatenate([[a.index for a in r.atoms()] for r in patch.topology.residues()
                            if r.name != "HOH"])
    mean_z = xyz[every, 2].mean()
    for residue in patch.topology.residues():
        if residue.name != "HOH":
            index = [a.index for a in residue.atoms()]
            kept.append((residue, 0 if xyz[index, 2].mean() < mean_z else 1))
    size = np.ptp(_ring(), axis=0)
    centre = (original.min(axis=0) + original.max(axis=0)) / 2
    z = (float(original[:, 2].min()), float(original[:, 2].max()))

    def check(moved, grown=size):
        return membrane._lands_where_openmm_tiles(
            moved, patch.topology, kept, patch_centre=centre, patch_z=z,
            size=grown, padding_nm=2.0)

    assert check(xyz) is None
    shifted = xyz.copy()
    shifted[np.argmax(shifted[:, 0]), 0] += 0.5
    assert check(shifted) == "its centre moved"
    assert "not once" in check(xyz, grown=size + np.array([20.0, 0, 0]))
    flipped = xyz.copy()
    first = [a.index for a in kept[0][0].atoms()]
    flipped[first, 2] = 2 * mean_z - flipped[first, 2]
    assert check(flipped) == "a lipid changed leaflet"


_ONE_ATOM = """<ForceField>
 <AtomTypes><Type name="RNG-C" class="RNG-C" element="C" mass="12.011"/></AtomTypes>
 <Residues><Residue name="RNG"><Atom name="C" type="RNG-C"/></Residue></Residues>
 <NonbondedForce coulomb14scale="0.8333333333333334" lj14scale="0.5">
  <Atom type="RNG-C" charge="0" sigma="0.34" epsilon="0.36"/>
 </NonbondedForce>
</ForceField>"""


def test_the_fill_puts_water_only_in_the_pore():
    pytest.importorskip("openmm")
    from openmm.app import ForceField

    points = _ring()
    topology, positions = _protein(points)
    topology.setUnitCellDimensions(Vec3(7.0, 7.0, 9.0) * unit.nanometer)
    modeller = Modeller(topology, positions)
    forcefield = ForceField("amber14/tip3p.xml", io.StringIO(_ONE_ATOM))
    pores = membrane.pore_map(points, np.ones(len(points), bool), -2.3, 3.1)

    added = membrane.fill_the_pores(modeller, forcefield, pores)

    xyz = np.asarray(modeller.positions.value_in_unit(unit.nanometer))
    oxygen = [a.index for a in modeller.topology.atoms()
              if a.residue.name == "HOH" and a.element.symbol == "O"]
    assert added == len(oxygen) > 50
    assert pores.holds(xyz[oxygen]).all()
    nearest = np.min(np.linalg.norm(xyz[oxygen][:, None, :] - points[None, :, :], axis=2))
    assert nearest > 0.25
    assert membrane.fill_the_pores(modeller, forcefield, membrane.PoreMap()) == 0
    assert modeller.topology.getNumAtoms() == len(points) + 3 * added


def test_the_methods_say_the_pores_were_cleared_only_where_they_were():
    from fastmdxplora.report.methods import _bilayer_sentence

    built = {"lipid": "POPC", "placed_by": "fitted orientation and centre",
             "lipids": 406, "lipids_per_leaflet": [203, 203]}
    plain = _bilayer_sentence(dict(built, pore_lipids_removed=[0, 0],
                                   pore_balancing_lipids_removed=[0, 0],
                                   pore_waters_added=0), 2.0, "Na+", "Cl-", 0.15)
    assert "pore" not in plain
    assert plain == _bilayer_sentence(built, 2.0, "Na+", "Cl-", 0.15)

    cleared = _bilayer_sentence(dict(built, pore_lipids_removed=[4, 20],
                                     pore_balancing_lipids_removed=[16, 0],
                                     pore_waters_added=465), 2.0, "Na+", "Cl-", 0.15)
    assert cleared.startswith(plain)
    assert cleared[len(plain):] == (
        " Before packing, the 24 lipids that would have started inside the "
        "protein's pores were removed, with 16 more removed from the other "
        "leaflet far from the protein so that the leaflets stayed equal, and "
        "the pores were then filled with 465 waters.")


def test_a_change_in_openmm_s_own_tiling_falls_back_to_the_lipid_s_name(monkeypatch):
    """The patch is checked against OpenMM itself, run as far as its
    relaxation: an OpenMM that tiled one copy more each way (a margin tile)
    would place the cleared patch where its holes miss the pores, and the
    guard sees it and the bilayer is packed from the lipid's name."""
    pytest.importorskip("openmm")
    from openmm.app import modeller as module

    topology, positions = _protein(_ring())
    monkeypatch.setattr(module, "ceil", lambda value: math.ceil(value) + 1)
    clearing = membrane.clear_the_pores(topology, positions, "POPC", padding_nm=2.0)

    assert clearing.patch is None
    assert clearing.fallback and "OpenMM" in clearing.fallback
    assert sum(clearing.pore_lipids) > 0


def test_an_openmm_that_cannot_be_asked_falls_back_to_the_lipid_s_name(monkeypatch):
    topology, positions = _protein(_ring())

    def broken(*args, **kwargs):
        raise TypeError("addMembrane() got an unexpected keyword argument")

    monkeypatch.setattr(membrane, "_openmm_starts_from", broken)
    clearing = membrane.clear_the_pores(topology, positions, "POPC", padding_nm=2.0)
    assert clearing.patch is None
    assert clearing.fallback.startswith("OpenMM could not be asked (TypeError")


def test_the_lipids_balanced_away_are_the_farthest_spread_and_hold_no_extreme():
    """Farthest from the protein first, not nearest: the nearest are the
    annulus round the protein. None within the spacing of another in its
    leaflet, so the far bilayer does not lose a corner of one leaflet. None
    holding an extreme of the tiling, which fixes where OpenMM centres it."""
    box = (40.0, 40.0)
    # (distance, tile, residue, leaf, x, y, holds_extreme), 4 nm apart in x.
    candidates = [(float(d), 0, d, 1, 4.0 * d, 0.0, False) for d in range(1, 9)]
    chosen = membrane._pick_balancing(candidates, [0, 3], box)
    assert [c[0] for c in chosen] == [8.0, 7.0, 6.0]

    held = [c[:6] + (c[0] == 8.0,) for c in candidates]
    chosen = membrane._pick_balancing(held, [0, 3], box)
    assert [c[0] for c in chosen] == [7.0, 6.0, 5.0]

    # Two far lipids side by side: the second waits for the spacing to halve,
    # so the next far lipid 4 nm away is taken first.
    crowded = candidates + [(7.5, 0, 99, 1, 32.5, 0.0, False)]
    chosen = membrane._pick_balancing(crowded, [0, 2], box)
    assert [c[2] for c in chosen] == [8, 7]
    # With nothing else left, it is taken once the spacing has halved.
    pair = [candidates[-1], crowded[-1]]
    assert [c[2] for c in membrane._pick_balancing(pair, [0, 2], box)] == [8, 99]
    assert membrane._pick_balancing(crowded, [0, 0], box) == []
    # Only the leaflet that lost fewer to the pores loses more.
    assert all(c[3] == 1 for c in membrane._pick_balancing(crowded, [0, 4], box))


def test_a_pore_in_one_leaflet_is_recorded_in_that_leaflet():
    """A ring spanning only the upper leaflet: its lipids are the upper's,
    and the lower leaflet loses as many to the balance. The record's order
    is [upper, lower], as `lipids_per_leaflet` is."""
    ring = _ring()
    upper = ring[ring[:, 2] >= 1.2]
    topology, positions = _protein(upper)
    clearing = membrane.clear_the_pores(topology, positions, "POPC", padding_nm=2.0)

    assert clearing.patch is not None
    assert clearing.pore_lipids[0] > 0 and clearing.pore_lipids[1] == 0
    assert clearing.balancing_lipids == [0, clearing.pore_lipids[0]]


def test_the_check_finds_z_extremes_that_moved():
    """The z extremes decide where OpenMM cuts the water it adds beyond the
    patch; moved apart about the same centre, the check sees it."""
    topology, positions = _protein(_ring())
    clearing = membrane.clear_the_pores(topology, positions, "POPC", padding_nm=2.0,
                                        probe=False)
    patch = clearing.patch
    xyz = membrane._in_nm(patch.positions)
    original = membrane._in_nm(membrane._openmm_patch("POPC").positions)
    centre = (original.min(axis=0) + original.max(axis=0)) / 2
    z = (float(original[:, 2].min()), float(original[:, 2].max()))
    kept = [(r, 0) for r in list(patch.topology.residues())[:0]]
    moved = xyz.copy()
    moved[np.argmin(moved[:, 2]), 2] -= 0.5
    moved[np.argmax(moved[:, 2]), 2] += 0.5
    assert np.allclose((moved.min(axis=0) + moved.max(axis=0)) / 2, centre, atol=1e-6)
    assert membrane._lands_where_openmm_tiles(
        moved, patch.topology, kept, patch_centre=centre, patch_z=z,
        size=np.ptp(_ring(), axis=0), padding_nm=2.0) == "its z extremes moved"


# The wiring in prepare_system, with OpenMM's packing and the pore clearing
# stood in for: which patch is packed, when the pores are filled, what the
# record and the log say.

def _prepare_with(monkeypatch, tmp_path, clearing):
    pytest.importorskip("openmm")
    from openmm.app import Modeller as OpenMMModeller

    from fastmdxplora.setup import prepare
    from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE

    asked: list = []
    filled: list = []
    said: list[tuple[int, str]] = []

    def add_membrane(self, forcefield, **kwargs):
        asked.append(kwargs["lipidType"])
        self.addHydrogens(forcefield)
        self.topology.setUnitCellDimensions(Vec3(4.0, 4.0, 4.0) * unit.nanometer)

    def fill(modeller, forcefield, pores):
        filled.append(pores)
        return 11

    monkeypatch.setattr(membrane, "place_for_membrane", lambda t, p, **k: membrane.Placement(
        positions=p, record={"lipid": "POPC", "placed_by": "a stand-in"}))
    monkeypatch.setattr(membrane, "check_chains_point_the_same_way", lambda *a, **k: None)
    monkeypatch.setattr(membrane, "clear_the_pores", lambda *a, **k: clearing)
    monkeypatch.setattr(membrane, "fill_the_pores", fill)
    monkeypatch.setattr(OpenMMModeller, "addMembrane", add_membrane)

    import logging

    class Heard(logging.Handler):
        def emit(self, record):
            said.append((record.levelno, record.getMessage()))

    log = logging.getLogger("fastmdx.setup.prepare")
    heard = Heard()
    log.addHandler(heard)
    level = log.level
    log.setLevel(logging.INFO)
    structure = tmp_path / "peptide.pdb"
    structure.write_text(TRI_ALANINE, encoding="utf-8")
    try:
        result = prepare.prepare_system(
            prepared_pdb=str(structure), output_dir=str(tmp_path / "setup"),
            membrane="POPC", force_field=["amber14-all.xml", "amber14/tip3p.xml"],
            nonbonded_method="NoCutoff")
    finally:
        log.removeHandler(heard)
        log.setLevel(level)
    return result["membrane"], asked, filled, said


def test_prepare_packs_the_cleared_patch_and_fills_the_pores(monkeypatch, tmp_path):
    import logging

    patch = object()
    wet = membrane.PoreMap()
    clearing = membrane.PoreClearing(pores=membrane.PoreMap(), patch=patch,
                                     pore_lipids=[2, 5], balancing_lipids=[3, 0],
                                     to_fill=wet, left_dry=1)
    record, asked, filled, said = _prepare_with(monkeypatch, tmp_path, clearing)

    assert asked == [patch]
    assert len(filled) == 1 and filled[0] is wet
    assert record["pore_regions_left_dry"] == 1
    assert record["pore_lipids_removed"] == [2, 5]
    assert record["pore_balancing_lipids_removed"] == [3, 0]
    assert record["pore_waters_added"] == 11
    assert "pore_lipids_left_in" not in record
    infos = [m for level, m in said if level == logging.INFO]
    assert any(m.startswith("Removed 7 POPC lipids from the protein's pores (2 and 5 per "
                            "leaflet), and 3 more") for m in infos)
    assert any("11 waters added in the protein's pores" in m for m in infos)


def test_prepare_with_no_lipid_in_a_pore_packs_from_the_name(monkeypatch, tmp_path):
    clearing = membrane.PoreClearing(pores=membrane.PoreMap())
    record, asked, filled, said = _prepare_with(monkeypatch, tmp_path, clearing)

    assert asked == ["POPC"]
    assert filled == []
    assert record["pore_lipids_removed"] == [0, 0]
    assert record["pore_waters_added"] == 0
    assert not any("pore" in m for _, m in said)


def test_prepare_falls_back_with_a_warning_and_says_what_was_left(monkeypatch, tmp_path):
    import logging

    from fastmdxplora.report.methods import _bilayer_sentence

    clearing = membrane.PoreClearing(pores=membrane.PoreMap(), pore_lipids=[4, 20],
                                     balancing_lipids=[16, 0],
                                     fallback="OpenMM gives it a different box")
    record, asked, filled, said = _prepare_with(monkeypatch, tmp_path, clearing)

    assert asked == ["POPC"]
    assert filled == []
    assert record["pore_lipids_removed"] == [0, 0]
    assert record["pore_lipids_left_in"] == 24
    warnings = [m for level, m in said if level == logging.WARNING]
    assert any("The protein's pores would start with 24 POPC lipids in them" in m
               and "OpenMM gives it a different box" in m for m in warnings)
    sentence = _bilayer_sentence(dict(record, lipids=124, lipids_per_leaflet=[62, 62]),
                                 2.0, "Na+", "Cl-", 0.15)
    assert sentence.endswith(" The 24 lipids that started inside the protein's "
                             "pores were left there, as OpenMM packed them.")


def test_a_hole_closed_only_across_a_slice_edge_is_found():
    """A wall whose atoms alternate between two heights 0.3 nm apart closes
    only in a slice that holds both. Laid once, the slices put the heights
    in different slices and the wall stays open; laid twice, half a slice
    apart, one set holds both."""
    radius, around = 2.0, 42
    angles = np.arange(around) * 2 * math.pi / around
    z = np.where(np.arange(around) % 2 == 0, 0.45, 0.75)
    wall = np.column_stack([radius * np.cos(angles), radius * np.sin(angles), z])
    heavy = np.ones(len(wall), bool)

    once = membrane.pore_map(wall, heavy, 0.0, 1.2, phases=1)
    twice = membrane.pore_map(wall, heavy, 0.0, 1.2)
    assert not once.holds([[0, 0, 0.6]]).any()
    assert twice.holds([[0, 0, 0.6]]).all()


def test_a_lipid_is_in_a_pore_with_one_heavy_atom_in_one():
    """A tail tip in a lumen is cut by the growing wall as surely as a whole
    tail: with three atoms needed, 2POR ran away in three packings with a
    packing seed that had packed with one."""
    ring = _ring()
    pores = membrane.pore_map(ring, np.ones(len(ring), bool), -2.0, 2.0)
    inside, outside = [0.0, 0.0, 0.0], [6.0, 0.0, 0.0]
    assert not membrane.lipid_in_a_pore(pores, [outside, outside, outside])
    assert membrane.lipid_in_a_pore(pores, [inside, outside, outside])


def test_only_the_pores_that_held_a_removed_lipid_and_are_lined_as_water_s_are_filled():
    """Three lumens: one lined by serine oxygens that held a removed lipid,
    one lined by carbons that held one too (a lipid's site, as
    bacteriorhodopsin's central cavity is), and one lined by oxygens that
    held none. Only the first is filled."""
    rings = [_ring() + np.array([dx, 0.0, 0.0]) for dx in (-5.0, 0.0, 5.0)]
    rings = [r[: 2 * (len(r) // 2)] for r in rings]
    names = [("OG", "O"), ("CB", "C"), ("OG", "O")]
    topology = Topology()
    points = np.concatenate(rings)
    chain = topology.addChain()
    for ring, (name, symbol) in zip(rings, names):
        for _ in range(len(ring) // 2):
            residue = topology.addResidue("SER", chain)
            for _ in range(2):
                topology.addAtom(name, element.get_by_symbol(symbol), residue)
    pores = membrane.pore_map(points, np.ones(len(points), bool), -2.0, 2.0)
    held = np.array([[-5.0, 0.0, 0.1], [0.0, 0.0, 0.1]])

    wet, dry = membrane._pores_to_fill(pores, held, topology, points)

    assert dry == 1
    assert wet.holds([[-5.0, 0.0, 0.5]]).all()
    assert not wet.holds([[0.0, 0.0, 0.5]]).any()
    assert not wet.holds([[5.0, 0.0, 0.5]]).any()
    assert pores.holds([[5.0, 0.0, 0.5]]).all()


def test_the_methods_say_one_lipid_as_one():
    from fastmdxplora.report.methods import _pore_sentence

    one = _pore_sentence({"pore_lipids_removed": [0, 1],
                          "pore_balancing_lipids_removed": [1, 0],
                          "pore_waters_added": 1})
    assert one == (" Before packing, the one lipid that would have started inside "
                   "the protein's pores was removed, with 1 more removed from the "
                   "other leaflet far from the protein so that the leaflets stayed "
                   "equal, and the pores were then filled with one water.")
    dry = _pore_sentence({"pore_lipids_removed": [0, 1],
                          "pore_balancing_lipids_removed": [1, 0],
                          "pore_waters_added": 0})
    assert "filled" not in dry
    assert _pore_sentence({"pore_lipids_left_in": 1}) == (
        " One lipid that started inside the protein's pores was left there, as "
        "OpenMM packed it.")


def test_an_openmm_that_makes_its_context_otherwise_is_not_probed(monkeypatch):
    """The stand-in stops OpenMM by replacing the module's `Context`. Where
    `addMembrane` no longer names it, OpenMM would run its whole relaxation
    on the stand-in system, so the probe is not run and the patch not used."""
    pytest.importorskip("openmm")
    from openmm.app import Modeller as OpenMMModeller

    def elsewhere(self, forcefield, **kwargs):
        return forcefield

    topology, positions = _protein(_ring())
    monkeypatch.setattr(OpenMMModeller, "addMembrane", elsewhere)
    clearing = membrane.clear_the_pores(topology, positions, "POPC", padding_nm=2.0)
    assert clearing.patch is None
    assert "no longer makes its context" in clearing.fallback


def test_a_probe_that_runs_too_long_is_given_up(monkeypatch):
    import time

    pytest.importorskip("openmm")
    from openmm.app import Modeller as OpenMMModeller

    def slow(self, forcefield, **kwargs):
        forcefield.createSystem, openmm.Context  # noqa: B018 - the names it checks
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            time.sleep(0.05)

    topology, positions = _protein(_ring())
    monkeypatch.setattr(membrane, "PROBE_SECONDS", 0.5)
    monkeypatch.setattr(OpenMMModeller, "addMembrane", slow)
    started = time.monotonic()
    clearing = membrane.clear_the_pores(topology, positions, "POPC", padding_nm=2.0)
    assert time.monotonic() - started < 20
    assert clearing.patch is None
    assert "ran past 0.5 s" in clearing.fallback


def test_a_timer_running_before_the_probe_runs_on_after_it(monkeypatch):
    """The probe's own time limit used the process's one real timer, and
    cancelled any timer running before it: a test's time limit, say."""
    import signal
    import threading
    import time

    if not hasattr(signal, "setitimer") or threading.current_thread() is not threading.main_thread():
        pytest.skip("the probe is timed only on the main thread where setitimer exists")
    pytest.importorskip("openmm")
    from openmm.app import Modeller as OpenMMModeller

    def slow(self, forcefield, **kwargs):
        forcefield.createSystem, openmm.Context  # noqa: B018 - the names it checks
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            time.sleep(0.05)

    fired: list[int] = []

    def mine(*_):
        fired.append(1)

    before = signal.signal(signal.SIGALRM, mine)
    saved = signal.setitimer(signal.ITIMER_REAL, 60.0)
    try:
        topology, positions = _protein(_ring())
        monkeypatch.setattr(membrane, "PROBE_SECONDS", 0.5)
        monkeypatch.setattr(OpenMMModeller, "addMembrane", slow)
        clearing = membrane.clear_the_pores(topology, positions, "POPC", padding_nm=2.0)
        left = signal.getitimer(signal.ITIMER_REAL)[0]
        handler = signal.getsignal(signal.SIGALRM)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, before)
        if saved[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *saved)
    assert "ran past 0.5 s" in clearing.fallback
    assert 0 < left < 60.0
    assert handler is mine and fired == []


def test_a_timer_whose_handler_is_not_python_s_is_left_alone(monkeypatch):
    """A SIGALRM handler installed from C reads as None and could not be put
    back: the probe then runs untimed, and gives up once it ends past its
    limit, with the handler and the timer as they were."""
    import signal
    import threading
    import time

    if not hasattr(signal, "setitimer") or threading.current_thread() is not threading.main_thread():
        pytest.skip("the probe is timed only on the main thread where setitimer exists")
    pytest.importorskip("openmm")
    from openmm.app import Modeller as OpenMMModeller

    def slow(self, forcefield, **kwargs):
        forcefield.createSystem, openmm.Context  # noqa: B018 - the names it checks
        time.sleep(1.0)

    fired: list[int] = []

    def mine(*_):
        fired.append(1)

    getsignal, install = signal.getsignal, signal.signal
    before = signal.signal(signal.SIGALRM, mine)
    saved = signal.setitimer(signal.ITIMER_REAL, 60.0)
    try:
        topology, positions = _protein(_ring())
        monkeypatch.setattr(membrane, "PROBE_SECONDS", 0.5)
        monkeypatch.setattr(OpenMMModeller, "addMembrane", slow)
        monkeypatch.setattr(signal, "getsignal",
                            lambda n: None if n == signal.SIGALRM else getsignal(n))
        monkeypatch.setattr(signal, "signal",
                            lambda n, h: install(n, h) if n != signal.SIGALRM
                            else (install(n, h), None)[1])
        clearing = membrane.clear_the_pores(topology, positions, "POPC", padding_nm=2.0)
        monkeypatch.undo()
        left = signal.getitimer(signal.ITIMER_REAL)
        handler = getsignal(signal.SIGALRM)
    finally:
        monkeypatch.undo()
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, before)
        if saved[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *saved)
    assert "ran past 0.5 s" in clearing.fallback
    assert 0 < left[0] < 60.0 and left[1] == 0
    assert handler is mine and fired == []


def test_the_probe_counts_the_lipids_removed_and_finds_one_left_in_a_pore():
    """The probe's own checks, each made to fail: a count of removed lipids
    that is not what OpenMM leaves out, and a map in which a lipid it keeps
    lies in a pore."""
    points = _ring()
    topology, positions = _protein(points)
    clearing = membrane.clear_the_pores(topology, positions, "POPC", padding_nm=2.0,
                                        probe=False)
    removed = sum(clearing.pore_lipids) + sum(clearing.balancing_lipids)

    def ask(pores, count):
        return membrane._openmm_places_it(
            topology, positions, "POPC", clearing.patch, pores, padding_nm=2.0,
            center_z_nm=0.0, removed=count)

    assert ask(clearing.pores, removed) is None
    assert ask(clearing.pores, removed + 1) == (
        f"OpenMM leaves {removed} fewer lipids, not the {removed + 1} removed")
    everywhere = membrane.PoreMap(slices=[(-5.0, 5.0, np.array([-20.0, -20.0]),
                                           np.ones((400, 400), bool))])
    assert ask(everywhere, removed).startswith("OpenMM still starts ")


def test_a_wrapped_add_membrane_is_still_probed(monkeypatch):
    """A tool that wraps `addMembrane` with `functools.wraps` (a timer, a
    logger) keeps the name check on the function it wraps."""
    import functools

    pytest.importorskip("openmm")
    from openmm.app import Modeller as OpenMMModeller

    original = OpenMMModeller.addMembrane

    @functools.wraps(original)
    def timed(self, *args, **kwargs):
        return original(self, *args, **kwargs)

    topology, positions = _protein(_ring())
    monkeypatch.setattr(OpenMMModeller, "addMembrane", timed)
    clearing = membrane.clear_the_pores(topology, positions, "POPC", padding_nm=2.0)
    assert clearing.fallback is None and clearing.patch is not None


def test_a_point_in_a_pore_of_any_overlapping_slice_is_in_a_pore():
    """The two sets of slices overlap, and a point is in a pore where any
    slice holding it says so, whichever comes last."""
    open_cell = np.ones((10, 10), bool)
    closed = np.zeros((10, 10), bool)
    origin = np.array([-0.5, -0.5])
    pores = membrane.PoreMap(slices=[(0.0, 0.6, origin, open_cell),
                                     (0.3, 0.9, origin, closed)])
    assert pores.holds([[0.0, 0.0, 0.4]]).all()
    assert pores.holds([[0.0, 0.0, 0.1]]).all()
    assert not pores.holds([[0.0, 0.0, 0.7]]).any()
