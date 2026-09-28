"""A dry run says what each run will do about setup, as the run decides it.

A run naming a prepared system in `setup_from` prepares nothing, and umbrella
windows share one preparation, but a campaign's dry run listed setup for
every run either way: the plan a person checks before spending GPU days said
each replica would solvate a box of its own.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fastmdxplora import FastMDXplora
from tests.test_a_run_given_setup_from_prepares_nothing import _placeholders


@pytest.fixture
def stub_pdb(tmp_path: Path) -> Path:
    """A tripeptide: nothing here is prepared, only planned."""
    path = tmp_path / "protein.pdb"
    path.write_text(
        "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n"
        "ATOM      2  CA  ALA A   1       1.458   0.000   0.000  1.00  0.00           C\n"
        "ATOM      3  O   ALA A   1       2.400   0.600   0.000  1.00  0.00           O\n"
        "END\n", encoding="utf-8")
    return path


def _planned(tmp_path: Path, capsys, **simulation) -> str:
    FastMDXplora(config_data={
        "output": str(tmp_path / "study"),
        "systems": [{"id": "a", "system": str(tmp_path / "protein.pdb")}],
        "simulation": simulation,
        "sweep": {"simulation.random_seed": [1, 2]},
    }).explore(dry_run=True)
    return capsys.readouterr().out


def _phases(out: str) -> str:
    [line] = [line for line in out.splitlines() if line.strip().startswith("phases:")]
    return line


class TestSetupFrom:

    def test_a_named_system_is_simulated_and_nothing_prepared(
            self, tmp_path, stub_pdb, capsys) -> None:
        prepared = _placeholders(tmp_path / "prepared" / "setup")
        out = _planned(tmp_path, capsys, setup_from=str(tmp_path / "prepared"))
        assert "setup" not in _phases(out)
        assert "simulation" in _phases(out)
        assert f"not run; simulates the system prepared in {prepared}" in out

    def test_a_name_with_nothing_there_is_said(self, tmp_path, stub_pdb, capsys) -> None:
        out = _planned(tmp_path, capsys, setup_from=str(tmp_path / "nowhere"))
        # Setup still runs, as it would: the simulation is what refuses.
        assert "setup" in _phases(out)
        assert "where there is no prepared system" in out
        assert "the simulation will refuse" in out

    def test_without_it_the_plan_is_as_before(self, tmp_path, stub_pdb, capsys) -> None:
        out = _planned(tmp_path, capsys)
        assert "setup → simulation" in _phases(out)
        assert "setup: " not in out

    def test_nothing_is_written(self, tmp_path, stub_pdb, capsys) -> None:
        _placeholders(tmp_path / "prepared" / "setup")
        _planned(tmp_path, capsys, setup_from=str(tmp_path / "prepared"))
        assert not (tmp_path / "study").exists()


def test_umbrella_windows_share_one_preparation(tmp_path, stub_pdb, capsys,
                                                caplog) -> None:
    """And they are not told they will each solvate a box of their own."""
    FastMDXplora(config_data={
        "output": str(tmp_path / "pmf"),
        "systems": [{"id": "a", "system": str(stub_pdb)}],
        "simulation": {"umbrella": {
            "collective_variable": "distance",
            "select_atoms_a": "name N", "select_atoms_b": "name O",
            "force_constant": 1000.0, "from": 0.3, "to": 0.6, "n_windows": 3}},
    }).explore(dry_run=True)
    out = capsys.readouterr().out
    assert "setup" not in _phases(out)
    assert f"prepared once, in {tmp_path / 'pmf' / 'shared_setup'}, for every window" in out
    assert "solvate independently" not in caplog.text


def test_a_seed_sweep_is_still_told_it_will_not_share_water(
        tmp_path, stub_pdb, capsys, caplog) -> None:
    _planned(tmp_path, capsys)
    assert "solvate independently" in caplog.text
