"""A residue is named by its chain wherever a structure has several.

The deposited number alone named every residue, so on a structure with
several copies of one chain four residues answered to each number: per-
residue SASA could not be tabulated, and RMSF, secondary structure and
dihedrals reported success over tables in which a number meant four
residues. Setup now builds such structures by default -- 1STP as the
streptavidin tetramer -- so these run on that, built from the deposition.
One chain is named exactly as before.
"""

from __future__ import annotations

import gzip
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

DEPOSITED = Path(__file__).parent / "data" / "assemblies"


def _trajectory(tmp_path: Path, entry: str, keep: str) -> "md.Trajectory":
    """A short trajectory of what setup would simulate from ``entry``."""
    from fastmdxplora.setup.pipeline import _the_chains_to_simulate

    source = tmp_path / entry / "input.pdb"
    source.parent.mkdir(parents=True)
    source.write_bytes(gzip.decompress((DEPOSITED / f"{entry}.pdb.gz").read_bytes()))
    built = _the_chains_to_simulate(SimpleNamespace(_assembly=None), source, "pdb_id", {})
    structure = md.load(str(built))
    structure = structure.atom_slice(structure.topology.select(keep))
    shake = np.random.default_rng(0).normal(0.0, 0.02, (6,) + structure.xyz.shape[1:])
    return md.Trajectory((structure.xyz + shake).astype(np.float32), structure.topology)


@pytest.fixture(scope="module")
def tetramer(tmp_path_factory):
    return _trajectory(tmp_path_factory.mktemp("stp"), "1STP", "protein or resname BTN")


@pytest.fixture(scope="module")
def monomer(tmp_path_factory):
    # 1AKE as setup now takes it: chain A, with its inhibitor as a chain of
    # its own, which must not count as a second chain.
    return _trajectory(tmp_path_factory.mktemp("ake"), "1AKE", "protein or resname AP5")


@pytest.mark.parametrize("name, keys", [
    ("rmsf", ["chain", "residue"]),
    ("sasa_residue", ["frame", "chain", "residue"]),
    ("sasa_average", ["chain", "residue"]),
    ("dihedrals", ["frame", "chain", "residue"]),
])
def test_every_residue_of_a_tetramer_is_named_once(tmp_path, tetramer, name, keys) -> None:
    from fastmdxplora.analysis.dihedrals import Dihedrals
    from fastmdxplora.analysis.rmsf import RMSF
    from fastmdxplora.analysis.sasa import SASA

    protein = tetramer.atom_slice(tetramer.topology.select("protein"))
    analysis = {"rmsf": lambda: RMSF(output_dir=tmp_path),
                "sasa_residue": lambda: SASA(mode="residue", output_dir=tmp_path),
                "sasa_average": lambda: SASA(mode="average_residue", output_dir=tmp_path),
                "dihedrals": lambda: Dihedrals(output_dir=tmp_path)}[name]()
    assert analysis.run(protein).status == "ok"
    table = analysis.compute(protein)
    assert sorted(table["chain"].unique()) == ["A", "B", "C", "D"]
    assert not table.duplicated(subset=keys).any()


def test_secondary_structure_has_a_column_per_residue(tmp_path, tetramer) -> None:
    from fastmdxplora.analysis.ss import SS

    protein = tetramer.atom_slice(tetramer.topology.select("protein"))
    table = SS(output_dir=tmp_path).compute(protein)
    residues = [c for c in table.columns if c != "frame"]
    assert len(residues) == len(set(residues)) == 4 * 121
    assert residues[0] == "A:13" and "D:133" in residues


def test_each_copy_of_a_binding_site_is_its_own_row(tmp_path, tetramer) -> None:
    # Four biotins, one per subunit: the same residue of two copies can hold
    # a ligand, and the table has to say which copy.
    from fastmdxplora.analysis.contacts import Contacts

    contacts = Contacts(ligand_resname="BTN", output_dir=tmp_path)
    contacts.compute(tetramer)
    labels = list(contacts._per_residue["residue"])
    assert len(labels) == len(set(labels))
    assert {label.split(":")[0] for label in labels} == {"A", "B", "C", "D"}


def test_one_chain_is_named_as_it_always_was(tmp_path, monomer) -> None:
    from fastmdxplora.analysis.rmsf import RMSF
    from fastmdxplora.analysis.sasa import SASA
    from fastmdxplora.analysis.ss import SS

    assert monomer.topology.n_chains > 1  # the inhibitor is a chain of its own
    protein = monomer.atom_slice(monomer.topology.select("protein"))
    rmsf = RMSF(output_dir=tmp_path).compute(monomer)
    assert isinstance(rmsf, np.ndarray) and rmsf.shape[1] == 2
    assert "chain" not in SASA(mode="average_residue", output_dir=tmp_path).compute(monomer)
    assert all(isinstance(c, int) for c in SS(output_dir=tmp_path).compute(protein).columns[1:])


def test_the_readers_of_rmsf_read_a_tetramer(tmp_path, tetramer) -> None:
    # rmsf.dat is read by the report's region highlights and the dashboard.
    # By position, the first took the chain for the residue and the second
    # dropped every row holding a chain name, reading no RMSF at all.
    from fastmdxplora.analysis.rmsf import RMSF
    from fastmdxplora.gui.report_dashboard import _numeric_series
    from fastmdxplora.report.region_highlights import _load_rmsf, _plot_rmsf_regions, validate_region_highlights

    protein = tetramer.atom_slice(tetramer.topology.select("protein"))
    RMSF(output_dir=tmp_path).run(protein)
    written = next(tmp_path.rglob("rmsf.dat"))
    data, chains = _load_rmsf(written)
    assert data.shape == (4 * 121, 2) and set(chains) == {"A", "B", "C", "D"}
    assert np.array_equal(np.unique(data[:, 0]), np.arange(13, 134))
    regions = validate_region_highlights([{"start": 45, "end": 52, "label": "loop"}], data[:, 0])
    _plot_rmsf_regions(data, regions, tmp_path / "regions.png", chains=chains)
    assert (tmp_path / "regions.png").is_file()
    assert len(_numeric_series(written)) == 4 * 121
