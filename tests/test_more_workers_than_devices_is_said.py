"""More workers than listed devices is said, not done silently.

With `devices: [0]` and `workers: 3`, three runs share one card, each slower
than alone, and nothing said so. A device listed twice is how two runs on one
card are asked for, and that is not warned about.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from fastmdxplora.batch.explorer import BatchExplorer


@pytest.fixture
def structure(tmp_path: Path) -> Path:
    path = tmp_path / "protein.pdb"
    path.write_text(
        "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n"
        "END\n", encoding="utf-8")
    return path


def _campaign(tmp_path: Path, structure: Path, **execution) -> BatchExplorer:
    return BatchExplorer(config_data={
        "output": str(tmp_path / "out"),
        "systems": [{"id": "a", "system": str(structure)}],
        "sweep": {"simulation.random_seed": [1, 2, 3]},
        "execution": {"mode": "parallel", **execution},
    })


@pytest.mark.parametrize("execution, said", [
    ({"workers": 3, "devices": [0]}, True),
    ({"workers": 2, "devices": [0, 1]}, False),
    ({"workers": 2, "devices": [0, 0]}, False),
    ({"devices": [0, 1]}, False),
    ({"workers": 2}, False),
])
def test_it_is_said_only_where_runs_share_a_card(tmp_path, structure, execution, said):
    note = _campaign(tmp_path, structure, **execution)._devices_shared()
    assert bool(note) is said
    if said:
        assert "3 workers over 1 listed device slot(s) (0)" in note
        assert "up to 3 runs will share a card" in note


def test_the_dry_run_says_it(tmp_path, structure, capsys) -> None:
    _campaign(tmp_path, structure, workers=3, devices=[0]).dry_run()
    assert "will share a card" in capsys.readouterr().out


def test_the_run_says_it(tmp_path, structure, monkeypatch, caplog) -> None:
    campaign = _campaign(tmp_path, structure, workers=3, devices=[0])
    # Nothing is run: the pool is refused as it is asked for.
    from fastmdxplora.batch import explorer

    def no_pool(*args, **kwargs):
        raise RuntimeError("no pool here")

    monkeypatch.setattr(explorer, "ProcessPoolExecutor", no_pool, raising=False)
    with caplog.at_level(logging.WARNING, logger="fastmdx"), \
            pytest.raises(RuntimeError, match="no pool here"):
        campaign._run_parallel(None, None)
    assert "will share a card" in caplog.text
