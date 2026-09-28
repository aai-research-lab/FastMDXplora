"""The fitted membrane is where OPM puts it.

`membrane_orient` rotates a structure onto a membrane normal fitted from where
its lipid-facing surface is apolar, and centres the bilayer where the fit
says. It was built against OPM's orientations for 40 proteins and checked on
25 more; these are a few of each kind, fetched from OPM, turned to a random
frame, and fitted again. A change to the fit that loses any of them fails
here.

Run deliberately, because they fetch from OPM and RCSB:

    pytest -m network tests/validation/test_the_membrane_is_where_opm_puts_it.py
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.network

md = pytest.importorskip("mdtraj")

from fastmdxplora.setup.membrane_fit import fit_membrane  # noqa: E402

OPM = "https://opm-assets.storage.googleapis.com/pdb/{}.pdb"
RCSB = "https://files.rcsb.org/download/{}.pdb"

#: One of each kind the fit has to handle: a helix dimer, a channel, a GPCR
#: with a fusion partner and one with a G protein (where the longest axis is
#: not the normal), a small barrel, and a porin.
MEMBRANE = ("1afo", "1bl8", "2rh1", "3sn6", "1qj8", "2por")

#: The fit came within 21 degrees of OPM's normal in all 170 fits it was
#: checked on, and within 15 in all but two.
NORMAL_TOLERANCE_DEG = 21.0
CENTRE_TOLERANCE_NM = 0.8


def _fetch(url: str, path: Path) -> Path:
    if not path.exists():
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                path.write_bytes(response.read())
        except OSError as exc:  # unreachable is not a failure of the fit
            pytest.skip(f"could not fetch {url}: {exc}")
    return path


def _protein(path: Path):
    """The first model's protein heavy atoms, OPM's membrane markers left out."""
    lines = []
    for line in path.read_text().splitlines():
        if line.startswith("ENDMDL"):
            break
        if line.startswith(("ATOM", "HETATM")) and line[17:20] != "DUM":
            lines.append(line)
    clean = path.with_suffix(".clean.pdb")
    clean.write_text("\n".join(lines) + "\nEND\n")
    structure = md.load_pdb(str(clean))
    keep = [a.index for a in structure.topology.atoms
            if a.residue.is_protein and a.element is not None and a.element.symbol != "H"]
    return structure.atom_slice(keep)


def _turned(xyz: np.ndarray, seed: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A random rotation and shift, and where OPM's normal and centre went."""
    rng = np.random.default_rng(seed)
    q = rng.normal(size=4)
    a, b, c, d = q / np.linalg.norm(q)
    rotation = np.array([
        [a*a + b*b - c*c - d*d, 2*(b*c - a*d), 2*(b*d + a*c)],
        [2*(b*c + a*d), a*a - b*b + c*c - d*d, 2*(c*d - a*b)],
        [2*(b*d - a*c), 2*(c*d + a*b), a*a - b*b - c*c + d*d]])
    shift = rng.normal(size=3) * 3.0
    return xyz @ rotation.T + shift, rotation @ np.array([0.0, 0.0, 1.0]), shift


@pytest.fixture(scope="module")
def downloads(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("opm")


@pytest.mark.parametrize("entry", MEMBRANE)
def test_the_normal_and_centre_are_opm_s(entry: str, downloads: Path) -> None:
    structure = _protein(_fetch(OPM.format(entry), downloads / f"{entry}.pdb"))
    moved, normal, centre = _turned(structure.xyz[0].astype(float), seed=len(entry) + ord(entry[0]))
    fit = fit_membrane(structure.topology, moved)
    assert fit is not None and fit.looks_like_a_membrane_protein

    angle = np.degrees(np.arccos(min(1.0, abs(float(np.dot(fit.normal, normal))))))
    assert angle < NORMAL_TOLERANCE_DEG, f"{entry}: normal {angle:.1f} degrees from OPM's"
    offset = abs(float(np.dot(np.asarray(fit.plane_point) - centre, normal)))
    assert offset < CENTRE_TOLERANCE_NM, f"{entry}: centre {offset:.2f} nm from OPM's"


@pytest.mark.parametrize("entry", ("1UBQ", "1LYZ", "4AKE"))
def test_a_soluble_protein_is_not_taken_for_one(entry: str, downloads: Path) -> None:
    structure = _protein(_fetch(RCSB.format(entry), downloads / f"{entry}.pdb"))
    fit = fit_membrane(structure.topology, structure.xyz[0].astype(float))
    assert fit is None or not fit.looks_like_a_membrane_protein
