"""Helix tilt and kink, held against ideal helices built at known angles,
and against KcsA's own helices."""

from __future__ import annotations

import gzip
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

DATA = Path(__file__).parent / "data" / "assemblies"


def _ideal(residues: int, direction=(0.0, 0.0, 1.0), start=(0.0, 0.0, 0.0)) -> np.ndarray:
    """Alpha carbons of an ideal alpha helix: 0.23 nm radius, 100 degrees and
    0.15 nm a residue, along ``direction``."""
    k = np.arange(residues)
    local = np.stack([0.23 * np.cos(np.radians(100 * k)),
                      0.23 * np.sin(np.radians(100 * k)), 0.15 * k], axis=1)
    axis = np.asarray(direction, dtype=float)
    axis /= np.linalg.norm(axis)
    across = np.cross(axis, np.eye(3)[int(np.argmin(np.abs(axis)))])
    across /= np.linalg.norm(across)
    turn = np.stack([across, np.cross(axis, across), axis], axis=1)
    return local @ turn.T + np.asarray(start)


def _kinked(angle: float, residues: int = 24) -> np.ndarray:
    """Two ideal halves, the second leaving the first's axis by ``angle``."""
    half = residues // 2
    first = _ideal(half)
    second_axis = (np.sin(np.radians(angle)), 0.0, np.cos(np.radians(angle)))
    second = _ideal(residues - half, second_axis)
    # Carry on from where the first half's axis ends.
    second += first[-1] - second[0] + 0.15 * np.asarray(second_axis)
    return np.concatenate([first, second])


def _trajectory(ca: np.ndarray, frames: int = 30, noise: float = 0.01):
    topology = md.Topology()
    chain = topology.add_chain()
    for k in range(len(ca)):
        topology.add_atom("CA", md.element.carbon,
                          topology.add_residue("ALA", chain, resSeq=k + 1))
    rng = np.random.default_rng(0)
    xyz = ca[None] + rng.normal(0, noise, (frames,) + ca.shape)
    return md.Trajectory(xyz.astype(np.float32), topology)


@pytest.mark.parametrize("tilt", [0.0, 20.0, 45.0, 80.0, 140.0, 180.0])
def test_an_ideal_helix_is_tilted_as_it_was_built(tilt):
    from fastmdxplora.analysis.helix_geometry import helix_angles

    direction = (np.sin(np.radians(tilt)), 0.0, np.cos(np.radians(tilt)))
    ca = _ideal(20, direction)[None]
    found_tilt, kink = helix_angles(ca, np.array([0.0, 0.0, 1.0]))
    # Either way along the normal is the same tilt.
    assert found_tilt[0] == pytest.approx(min(tilt, 180.0 - tilt), abs=1.0)
    assert kink[0] < 1.0


@pytest.mark.parametrize("angle", [15.0, 30.0, 50.0])
def test_a_kinked_helix_is_kinked_by_its_angle(angle):
    from fastmdxplora.analysis.helix_geometry import helix_angles

    _, kink = helix_angles(_kinked(angle)[None], None)
    # The local axes across the joint lie between the two halves.
    assert kink[0] == pytest.approx(angle, abs=0.2 * angle + 2.0)


def test_the_axes_run_from_the_first_residue_to_the_last():
    from fastmdxplora.analysis.helix_geometry import local_axes

    axes = local_axes(_ideal(16, (0, 0, -1.0))[None])
    assert np.all(axes[..., 2] < -0.99)


def test_named_helices_are_followed_with_a_named_axis(tmp_path):
    from fastmdxplora.analysis.helix_geometry import HelixGeometry

    ca = np.concatenate([_ideal(14, (1.0, 0.0, 1.0)),
                         _ideal(14, (0.0, 0.0, 1.0), start=(2.0, 0.0, 0.0))])
    analysis = HelixGeometry(output_dir=tmp_path, reference_axis="z",
                             helices="resid 0 to 13; resid 14 to 27")
    result = analysis.run(_trajectory(ca))
    assert result.status == "ok", result.message
    first, second = analysis.findings["helices"]
    assert first["tilt_deg"]["mean"] == pytest.approx(45.0, abs=3.0)
    assert second["tilt_deg"]["mean"] == pytest.approx(0.0, abs=4.0)
    assert first["residues"] == "ALA1 to ALA14" and first["length"] == 14
    assert list(result.data.columns) == ["helix_1_tilt_deg", "helix_1_kink_deg",
                                         "helix_2_tilt_deg", "helix_2_kink_deg"]
    assert analysis.findings["tilt_from"] == "z, as asked"


def test_without_a_bilayer_or_an_axis_there_is_no_tilt(tmp_path):
    from fastmdxplora.analysis.helix_geometry import HelixGeometry

    analysis = HelixGeometry(output_dir=tmp_path, helices=["all"])
    result = analysis.run(_trajectory(_kinked(30.0)))
    assert result.status == "ok", result.message
    (helix,) = analysis.findings["helices"]
    assert helix["tilt_deg"] is None
    assert helix["kink_deg"]["mean"] == pytest.approx(30.0, abs=8.0)
    assert list(result.data.columns) == ["helix_1_kink_deg"]
    assert "nothing for a helix to be tilted from" in analysis.findings["tilt_from"]


def test_kcsa_s_helices_are_found_by_dssp(tmp_path):
    """KcsA's chain A: its outer and inner helices, and its pore helix."""
    from tempfile import TemporaryDirectory

    from fastmdxplora.analysis.helix_geometry import HelixGeometry

    with TemporaryDirectory() as folder:
        path = Path(folder) / "1BL8.pdb"
        path.write_bytes(gzip.decompress((DATA / "1BL8.pdb.gz").read_bytes()))
        whole = md.load_pdb(str(path))
    protein = whole.atom_slice(whole.topology.select("protein and chainid 0"))
    analysis = HelixGeometry(output_dir=tmp_path, reference_axis="z")
    result = analysis.run(md.join([protein] * 3))
    assert result.status == "ok", result.message
    helices = analysis.findings["helices"]
    assert len(helices) >= 2
    assert all(h["length"] >= 8 for h in helices)
    assert all(h["still_helical_share_of_frames"] == 1.0 for h in helices)


@pytest.mark.parametrize("options, said", [
    ({"reference_axis": "w"}, "`reference_axis` is one of"),
    ({"min_length": 3}, "at least 5"),
])
def test_options_that_mean_nothing_are_refused(options, said, tmp_path):
    from fastmdxplora.analysis.helix_geometry import HelixGeometry
    from fastmdxplora.refusals import StudyError

    with pytest.raises(StudyError, match=said):
        HelixGeometry(output_dir=tmp_path, **options)


def test_a_protein_with_no_helix_is_refused(tmp_path):
    from fastmdxplora.analysis.helix_geometry import HelixGeometry

    straight = np.stack([np.arange(12) * 0.38, np.zeros(12), np.zeros(12)], axis=1)
    result = HelixGeometry(output_dir=tmp_path).run(_trajectory(straight))
    assert result.status == "error" and "finds no helix" in result.message


def test_a_named_helix_too_short_is_refused(tmp_path):
    from fastmdxplora.analysis.helix_geometry import HelixGeometry

    result = HelixGeometry(output_dir=tmp_path, helices=["resid 0 to 2"]).run(
        _trajectory(_ideal(12)))
    assert result.status == "error" and "need five at least" in result.message
