"""Fitting the bilayer, rather than assuming one and checking afterwards.

Orienting by principal axes asks which way a structure is longest, which is
the wrong question wherever a soluble domain carries the shape: 6B73's
longest axis runs through its G protein. And every check of a supplied
orientation compares populations across the whole molecule, so a T4L fusion
or a beta-barrel's polar lumen swamps the belt being looked for -- which is
why the calibration set overlapped, with a porin refused at 1.17 and
adenylate kinase refused at 0.91.

These tests exercise the arithmetic and the chemistry on constructed
structures, which need neither OpenMM nor a download. The measurement
against OPM's orientations is `tests/validation/test_the_membrane_is_where_opm_puts_it.py`.
"""

from __future__ import annotations

import numpy as np
import pytest

from fastmdxplora.setup.membrane_fit import (
    fit_membrane_slab,
    hemisphere_directions,
    rotation_onto_z,
)


def _belted(normal, count=1200, radius=1.6, half=1.5, seed=0):
    """A cylinder with an apolar band around its middle and polar ends."""
    rng = np.random.default_rng(seed)
    normal = np.asarray(normal, dtype=float)
    normal = normal / np.linalg.norm(normal)
    across = np.cross(normal, [1.0, 0.0, 0.0])
    if np.linalg.norm(across) < 1e-6:
        across = np.cross(normal, [0.0, 1.0, 0.0])
    across = across / np.linalg.norm(across)
    other = np.cross(normal, across)

    along = rng.uniform(-2.0 * half, 2.0 * half, count)
    angle = rng.uniform(0.0, 2.0 * np.pi, count)
    points = (np.outer(along, normal)
              + radius * (np.outer(np.cos(angle), across)
                          + np.outer(np.sin(angle), other)))
    inside = np.abs(along) < half
    return points, np.where(inside, 0.5, 0.02), np.where(inside, 0.02, 0.5)


def _globular(count=1200, radius=2.2, apolar_fraction=0.35, seed=1):
    """A soluble protein: exposed surface mixed, no band anywhere."""
    rng = np.random.default_rng(seed)
    direction = rng.normal(size=(count, 3))
    direction /= np.linalg.norm(direction, axis=1)[:, None]
    points = direction * radius
    apolar = rng.random(count) < apolar_fraction
    return points, np.where(apolar, 0.5, 0.0), np.where(apolar, 0.0, 0.5)


class TestTheSearchCoversDirections:
    def test_they_are_unit_vectors(self) -> None:
        directions = hemisphere_directions(500)
        assert np.allclose(np.linalg.norm(directions, axis=1), 1.0)

    def test_a_hemisphere_not_a_sphere(self) -> None:
        """A slab is unchanged by flipping its normal, so searching both
        halves does the same work twice."""
        assert (hemisphere_directions(500)[:, 2] >= 0).all()

    def test_they_are_spread_rather_than_bunched(self) -> None:
        directions = hemisphere_directions(500)
        separations = directions @ directions.T
        np.fill_diagonal(separations, -1.0)
        # No two directions closer than a few degrees.
        assert separations.max() < 0.999


class TestTheFitFindsTheMembrane:
    @pytest.mark.parametrize(
        "normal", [(0, 0, 1), (1, 0, 0), (0.3, -0.5, 0.8), (-0.6, 0.6, 0.5)])
    def test_the_normal_is_recovered_from_any_frame(self, normal) -> None:
        """The fit decides which way is up; it does not need to be told."""
        fit = fit_membrane_slab(*_belted(normal))
        wanted = np.asarray(normal, dtype=float)
        wanted /= np.linalg.norm(wanted)
        assert abs(float(np.dot(fit.normal, wanted))) > 0.98

    def test_the_thickness_is_the_one_that_was_there(self) -> None:
        fit = fit_membrane_slab(*_belted((0, 0, 1), half=1.5))
        assert fit.thickness_nm == pytest.approx(3.0, abs=0.3)

    def test_the_slab_is_mostly_apolar(self) -> None:
        fit = fit_membrane_slab(*_belted((0, 0, 1)))
        assert fit.hydrophobic_fraction > 0.9

    def test_a_thinner_belt_is_found_thinner(self) -> None:
        thin = fit_membrane_slab(*_belted((0, 0, 1), half=1.1))
        thick = fit_membrane_slab(*_belted((0, 0, 1), half=1.9))
        assert thin.thickness_nm < thick.thickness_nm


class TestASolubleProteinDoesNotFit:
    def test_the_best_slab_still_scores_badly(self) -> None:
        """There is always a best slab. What distinguishes a membrane protein
        is that its best slab buries more apolar surface than polar, and a
        soluble protein's does not."""
        fit = fit_membrane_slab(*_globular())
        assert fit.score_nm2 < 0

    def test_and_covers_an_ordinary_surface(self) -> None:
        fit = fit_membrane_slab(*_globular(apolar_fraction=0.35))
        assert fit.hydrophobic_fraction < 0.55

    def test_the_two_are_far_apart(self) -> None:
        """The measure they replace put a porin and a kinase within 0.3 of
        each other, on the wrong sides of the line."""
        membrane = fit_membrane_slab(*_belted((0, 0, 1)))
        soluble = fit_membrane_slab(*_globular())
        assert membrane.score_nm2 > 0 > soluble.score_nm2
        assert membrane.hydrophobic_fraction - soluble.hydrophobic_fraction > 0.4


class TestTheFitRefusesToGuess:
    def test_too_few_atoms(self) -> None:
        points, apolar, polar = _belted((0, 0, 1), count=10)
        assert fit_membrane_slab(points, apolar, polar) is None

    def test_mismatched_arrays(self) -> None:
        points, apolar, polar = _belted((0, 0, 1))
        assert fit_membrane_slab(points, apolar[:-5], polar) is None

    def test_no_surface_at_all(self) -> None:
        points, apolar, polar = _belted((0, 0, 1))
        assert fit_membrane_slab(points, apolar * 0, polar * 0) is None


class TestTheRotation:
    @pytest.mark.parametrize(
        "normal", [(0, 0, 1), (0, 0, -1), (1, 0, 0), (0.3, -0.5, 0.8)])
    def test_it_puts_the_normal_on_z(self, normal) -> None:
        rotation = rotation_onto_z(normal)
        wanted = np.asarray(normal, dtype=float)
        wanted /= np.linalg.norm(wanted)
        assert np.allclose(rotation @ wanted, [0.0, 0.0, 1.0], atol=1e-9)

    def test_it_is_a_rotation(self) -> None:
        """Not a reflection: a mirrored protein is a different molecule."""
        for normal in [(0, 0, 1), (1, 0, 0), (0.3, -0.5, 0.8), (-0.2, 0.9, 0.1)]:
            rotation = rotation_onto_z(normal)
            assert np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-9)
            assert float(np.linalg.det(rotation)) == pytest.approx(1.0)

    def test_the_fit_and_the_rotation_agree(self) -> None:
        points, apolar, polar = _belted((0.3, -0.5, 0.8))
        fit = fit_membrane_slab(points, apolar, polar)
        rotated = points @ rotation_onto_z(fit.normal).T
        # After rotating, the apolar band lies about the fitted centre in z.
        band = rotated[apolar > 0.1][:, 2]
        assert abs(float(np.median(band)) - fit.centre_nm) < 0.4


def _protein(parts, *, normal=(0, 0, 1), seed=0):
    """A protein built from (residue, atom, element, coordinates) parts,
    rotated so that z in the parts lands on ``normal``."""
    import mdtraj as md

    from fastmdxplora.setup.membrane_fit import rotation_onto_z

    top = md.Topology()
    chain = top.add_chain()
    points = []
    for resname, atom, element, xyz in parts:
        residue = top.add_residue(resname, chain)
        top.add_atom(atom, getattr(md.element, element), residue)
        points.append(xyz)
    rotation = rotation_onto_z(normal).T
    return top, np.asarray(points, dtype=float) @ rotation.T


def _ring(radius, z, count):
    angles = np.linspace(0.0, 2.0 * np.pi, count, endpoint=False)
    return [(radius * np.cos(a), radius * np.sin(a), z) for a in angles]


def _barrel(*, lumen="charged", normal=(0, 0, 1)):
    """A beta-barrel as wide as a porin's: an apolar outer belt 3 nm high,
    charged rims and loops above and below it, and a lumen lined with
    charged groups."""
    parts = []
    for z in np.arange(-1.4, 1.41, 0.35):
        parts += [("LEU", "CD1", "carbon", p) for p in _ring(2.6, z, 48)]
        lining = ("LYS", "NZ", "nitrogen") if lumen == "charged" else ("LEU", "CD1", "carbon")
        parts += [(*lining, p) for p in _ring(1.9, z, 36)]
    for z in (-2.4, -2.1, -1.8, 1.8, 2.1, 2.4):
        parts += [("GLU", "OE1", "oxygen", p) for p in _ring(2.6, z, 48)]
    return _protein(parts, normal=normal)


def _globule(seed=3, count=900):
    rng = np.random.default_rng(seed)
    direction = rng.normal(size=(count, 3))
    direction /= np.linalg.norm(direction, axis=1)[:, None]
    kinds = rng.choice(3, size=count, p=[0.45, 0.35, 0.20])
    table = {0: ("LEU", "CD1", "carbon"), 1: ("SER", "OG", "oxygen"),
             2: ("LYS", "NZ", "nitrogen")}
    parts = [(*table[k], tuple(2.2 * d)) for k, d in zip(kinds, direction)]
    return _protein(parts)


class TestSurfaceIsWeighedByWhatItCostsABilayer:
    """A charged group in a hydrocarbon core costs several times a neutral
    polar one; a backbone N or O in a transmembrane helix is hydrogen-bonded
    in the helix. Weighted alike, a slab lying across a compact helix bundle
    scored as well as the true one."""

    def test_each_atom_has_its_class(self) -> None:
        import mdtraj as md

        from fastmdxplora.setup.membrane_fit import (
            APOLAR, BACKBONE_POLAR, CHARGED, SIDE_CHAIN_POLAR, surface_classes)

        top = md.Topology()
        chain = top.add_chain()
        wanted = []
        for resname, atom, element, cls in [
                ("LYS", "NZ", "nitrogen", CHARGED), ("ARG", "NH1", "nitrogen", CHARGED),
                ("ASP", "OD2", "oxygen", CHARGED), ("GLU", "OE1", "oxygen", CHARGED),
                ("ALA", "OXT", "oxygen", CHARGED), ("ALA", "N", "nitrogen", BACKBONE_POLAR),
                ("ALA", "O", "oxygen", BACKBONE_POLAR), ("SER", "OG", "oxygen", SIDE_CHAIN_POLAR),
                ("ASN", "ND2", "nitrogen", SIDE_CHAIN_POLAR), ("LEU", "CD1", "carbon", APOLAR),
                ("MET", "SD", "sulfur", APOLAR)]:
            residue = top.add_residue(resname, chain)
            top.add_atom(atom, getattr(md.element, element), residue)
            wanted.append(cls)
        assert list(surface_classes(top)) == wanted


class TestALipidReachesOnlyTheOutside:
    def test_a_lumen_is_not_lipid_facing(self) -> None:
        """A porin's lumen is lined with charged groups and filled with
        water; counted as surface the bilayer covers, it pushed the fit
        sideways for every trimeric porin."""
        from fastmdxplora.setup.membrane_fit import lipid_facing

        top, points = _barrel()
        facing = lipid_facing(points, (0, 0, 1))
        radius = np.hypot(points[:, 0], points[:, 1])
        belt = np.abs(points[:, 2]) < 1.5
        assert facing[belt & (radius > 2.5)].all()
        assert not facing[belt & (radius < 2.0)].any()


class TestAProteinIsFitted:
    @pytest.mark.parametrize("normal", [(0, 0, 1), (0.6, -0.3, 0.74), (1, 0, 0)])
    def test_a_barrel_is_found_whatever_its_lumen_holds(self, normal) -> None:
        from fastmdxplora.setup.membrane_fit import fit_membrane

        top, points = _barrel(normal=normal)
        fit = fit_membrane(top, points)
        wanted = np.asarray(normal, dtype=float) / np.linalg.norm(normal)
        # Within what the fit resolves: its finest grid is a few degrees, and
        # where between two neighbouring directions it settles moves with
        # the last digits of the arithmetic, which differ between platforms.
        assert abs(float(fit.normal @ wanted)) > np.cos(np.radians(8))
        assert fit.thickness_nm == pytest.approx(3.0, abs=0.5)
        assert fit.looks_like_a_membrane_protein
        assert fit.details["lipid_facing_share"] < 0.9

    def test_the_plane_is_placed_where_the_belt_is(self) -> None:
        from fastmdxplora.setup.membrane_fit import fit_membrane

        top, points = _barrel()
        shifted = points + np.array([3.0, -2.0, 5.0])
        fit = fit_membrane(top, shifted)
        assert fit.plane_point @ fit.normal == pytest.approx(
            np.array([3.0, -2.0, 5.0]) @ fit.normal, abs=0.2)

    def test_a_soluble_protein_does_not_look_like_one(self) -> None:
        from fastmdxplora.setup.membrane_fit import fit_membrane

        top, points = _globule()
        fit = fit_membrane(top, points)
        assert fit is not None and not fit.looks_like_a_membrane_protein

    def test_too_little_protein_is_no_answer(self) -> None:
        from fastmdxplora.setup.membrane_fit import fit_membrane

        top, points = _protein([("ALA", "CB", "carbon", (0, 0, i * 0.1)) for i in range(5)])
        assert fit_membrane(top, points) is None

    def test_only_protein_heavy_atoms_are_fitted(self) -> None:
        """Measured on heavy atoms, as a deposited structure has them;
        hydrogens added by preparation, and water, are left out."""
        import mdtraj as md

        from fastmdxplora.setup.membrane_fit import fit_membrane

        top, points = _barrel()
        heavy = fit_membrane(top, points)
        chain = top.add_chain()
        water = top.add_residue("HOH", chain)
        top.add_atom("O", md.element.oxygen, water)
        hydrogen = top.add_residue("LEU", chain)
        top.add_atom("HD11", md.element.hydrogen, hydrogen)
        more = np.vstack([points, [[0.0, 0.0, 0.0], [1.9, 0.0, 0.1]]])
        again = fit_membrane(top, more)
        assert again.score_nm2 == pytest.approx(heavy.score_nm2)


class TestAKnownNormal:
    def test_the_centre_is_found_along_it(self) -> None:
        from fastmdxplora.setup.membrane_fit import fit_along

        top, points = _barrel()
        fit = fit_along(top, points + np.array([0.0, 0.0, 2.5]), (0, 0, 1))
        assert float(fit.plane_point[2]) == pytest.approx(2.5, abs=0.2)
        assert fit.thickness_nm == pytest.approx(3.0, abs=0.5)

    def test_the_tilt_is_either_way_up(self) -> None:
        from fastmdxplora.setup.membrane_fit import SlabFit, tilt_deg

        def fit(normal):
            return SlabFit(normal=np.asarray(normal, dtype=float), centre_nm=0.0,
                           half_thickness_nm=1.5, buried_hydrophobic_nm2=1.0,
                           buried_polar_nm2=0.0, score_nm2=1.0)

        assert tilt_deg(fit((0, 0, -1))) == pytest.approx(0.0)
        assert tilt_deg(fit((1, 0, 0))) == pytest.approx(90.0)
        assert tilt_deg(fit((0, np.sin(np.radians(30)), np.cos(np.radians(30))))) == pytest.approx(30.0)
