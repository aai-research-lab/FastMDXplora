"""A ligand found in the structure is prepared without the network.

`heterogens: auto` fetches a component's chemistry from the Chemical
Component Dictionary, completes it with hydrogens and writes the file the
ligand path takes. The fetch is cached by entry, chain, residue and name, so
a cached component needs no network, and that is how these run: the copy of
a fetched ligand into the study, and what setup says about it, were covered
only by the tests that reach RCSB.
"""

from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("rdkit")

from fastmdxplora.setup import ligand as ligand_module  # noqa: E402
from fastmdxplora.setup import pdbfix as pdbfix_module  # noqa: E402
from fastmdxplora.setup import pipeline  # noqa: E402
from fastmdxplora.setup import prepare as prepare_module  # noqa: E402

#: A benzene ring, 1.39 A bonds, centred on (x, 10, 10).
RING = [(1.39 * math.cos(math.pi * k / 3), 1.39 * math.sin(math.pi * k / 3))
        for k in range(6)]


def _posed_sdf(x: float) -> str:
    """Benzene as ModelServer gives a component at its pose: heavy atoms
    only, the component's name first."""
    atoms = "".join(f"{x + dx:10.4f}{10.0 + dy:10.4f}{10.0:10.4f} C   0  0  0  0  0  0  0  0  0  0  0  0\n"
                    for dx, dy in RING)
    bonds = "".join(f"{k + 1:3d}{(k + 1) % 6 + 1:3d}{1 + k % 2:3d}  0\n" for k in range(6))
    return f"BNZ\n  posed\n\n  6  6  0  0  0  0  0  0  0  0999 V2000\n{atoms}{bonds}M  END\n$$$$\n"


def _structure(tmp_path: Path) -> Path:
    """A histidine in each of two chains, and a benzene beside each."""
    lines, n = [], 0
    for chain, x in (("A", 10.0), ("B", 40.0)):
        n += 1
        lines.append(f"ATOM  {n:5d}  NE2 HIS {chain}  10    "
                     f"{x:8.3f}{14.0:8.3f}{10.0:8.3f}  1.00  0.00           N")
        for k, (dx, dy) in enumerate(RING, 1):
            n += 1
            lines.append(f"HETATM{n:5d}  C{k}  BNZ {chain} 500    "
                         f"{x + dx:8.3f}{10.0 + dy:8.3f}{10.0:8.3f}  1.00  0.00           C")
    path = tmp_path / "system.pdb"
    path.write_text("\n".join(lines) + "\nEND\n", encoding="utf-8")
    return path


@pytest.fixture
def cache(tmp_path, monkeypatch) -> Path:
    where = tmp_path / "cache"
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(where))
    return where / "ccd"


def _served(asked: list[str]):
    """RCSB's ModelServer, standing in: each copy at its own pose."""
    def get(url: str) -> str:
        asked.append(url)
        return _posed_sdf(10.0 if "auth_asym_id=A" in url else 40.0)
    return get


def _fetching_with(monkeypatch, fetcher) -> None:
    """Every fetch through ``fetcher``. The default is bound when
    `fetch_chemistry` is defined, so patching `_http_get` reaches nothing."""
    from functools import partial

    from fastmdxplora.setup import ccd

    real = ccd.fetch_chemistry
    monkeypatch.setattr(ccd, "fetch_chemistry", partial(real, fetcher=fetcher))


def _offline(url: str) -> str:
    raise AssertionError(f"reached the network for {url}")


def _counts(path) -> list[str]:
    return Path(path).read_text(encoding="utf-8").splitlines()[3].split()[:2]


def _prepare(tmp_path, params) -> list[str]:
    setup = tmp_path / "setup"
    setup.mkdir(exist_ok=True)
    return pipeline._auto_ligands(params, _structure(tmp_path), setup, "1ABC")


def test_each_copy_is_fetched_completed_and_written(tmp_path, cache, monkeypatch) -> None:
    asked: list[str] = []
    _fetching_with(monkeypatch, _served(asked))
    params = {"heterogens": "auto", "ph": 7.4}
    files = _prepare(tmp_path, params)

    assert len(asked) == 2
    assert [Path(f).name for f in files] == ["BNZ_A500.sdf", "BNZ_B500.sdf"]
    # Hydrogens added: six carbons and six hydrogens, a molecule and not a
    # radical, in the study and in the cache.
    assert [_counts(f) for f in files] == [["12", "12"], ["12", "12"]]
    assert _counts(cache / "1abc_A_500_BNZ.sdf") == ["12", "12"]
    assert params["ligand_name"] == ["BNZ", "BNZ"]
    assert params["ligand_net_charge"] == [0, 0]
    assert params["_reinstated_heterogens"] == ("BNZ",)


def test_a_second_study_needs_no_network(tmp_path, cache, monkeypatch) -> None:
    _fetching_with(monkeypatch, _served([]))
    _prepare(tmp_path, {"heterogens": "auto", "ph": 7.4})
    monkeypatch.undo()
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(cache.parent))
    _fetching_with(monkeypatch, _offline)
    again = tmp_path / "again"
    again.mkdir()
    files = _prepare(again, {"heterogens": "auto", "ph": 7.4})
    assert [_counts(f) for f in files] == [["12", "12"], ["12", "12"]]


def test_a_cached_copy_without_hydrogens_is_completed(tmp_path, cache, monkeypatch) -> None:
    """The file handed on is the one the atoms were counted in."""
    cache.mkdir(parents=True)
    for chain, x in (("A", 10.0), ("B", 40.0)):
        (cache / f"1abc_{chain}_500_BNZ.sdf").write_text(_posed_sdf(x), encoding="utf-8")
    _fetching_with(monkeypatch, _offline)
    files = _prepare(tmp_path, {"heterogens": "auto", "ph": 7.4})
    assert [_counts(f) for f in files] == [["12", "12"], ["12", "12"]]
    assert _counts(cache / "1abc_B_500_BNZ.sdf") == ["12", "12"]


def test_setup_says_where_the_chemistry_came_from(tmp_path, monkeypatch) -> None:
    """What 1072 put beneath the steps: the heterogen decisions explained,
    the chemistry's source named, and the ligand's parameters said."""
    structure = tmp_path / "complex.pdb"
    structure.write_text(_structure(tmp_path).read_text(encoding="utf-8"), encoding="utf-8")
    found = tmp_path / "BNZ.sdf"
    found.write_text(_posed_sdf(10.0), encoding="utf-8")

    def discovered(params, *_args):
        params["ligand_name"] = "BNZ"
        params["_heterogen_decisions"] = [{"resname": "BNZ", "action": "simulate"}]
        return [str(found)]

    monkeypatch.setattr(pipeline, "_auto_ligands", discovered)
    monkeypatch.setattr(ligand_module, "am1bcc_provider", lambda: "AmberTools")
    presenter = MagicMock()
    orchestrator = MagicMock(system=str(structure), _presenter=presenter)
    setup = tmp_path / "setup"
    setup.mkdir()
    made = {"resolved_forcefield": {"ligand": {"forcefield": "openff-2.2.1"}}}
    with patch.object(pdbfix_module, "fix_pdb_with_pdbfixer"), \
            patch.object(prepare_module, "prepare_system", return_value=made):
        pipeline.run(orchestrator=orchestrator, output_dir=setup)

    presenter.explanation.assert_any_call("heterogens")
    steps = [(c.args[0], c.kwargs.get("explain")) for c in presenter.step.call_args_list]
    assert ("Ligand chemistry from the Chemical Component Dictionary: BNZ",
            "ligand_chemistry") in steps
    assert ("Ligand parameters generated (openff-2.2.1)", "ligand_parameters") in steps
