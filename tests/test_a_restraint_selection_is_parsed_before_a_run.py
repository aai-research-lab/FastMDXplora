"""A restraint's selection is parsed with MDTraj before a run.

`restrain: "protein and resnamez ALA"` passed `validate_config` and every
check made before a run, and was found only when the simulation began,
after setup had run: the check before a run parsed the analyses' selections
and `save_selection`, and not the atoms a restraint holds.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from test_report_wiring import _make_traj_files  # noqa: E402

from fastmdxplora.cli.main import main  # noqa: E402
from fastmdxplora.config import ConfigError, refuse_what_can_be_seen  # noqa: E402

BAD = "protein and resnamez ALA"


@pytest.fixture(autouse=True)
def _own_folders(monkeypatch, tmp_path):
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("FASTMDXPLORA_CONFIG_DIR", str(tmp_path / "settings"))
    monkeypatch.setenv("HOME", str(tmp_path))


def _study(**simulation):
    return {"systems": [{"id": "a", "system": "1L2Y"}], "simulation": dict(simulation)}


@pytest.mark.parametrize("restrain, setting", [
    (BAD, "simulation.restrain"),
    (["protein and name CA", BAD], "simulation.restrain[2]"),
    ({"kind": "position", "selection": BAD}, "simulation.restrain[1].selection"),
    ([{"kind": "distance", "selection": "index 1 2", "force_constant": 500.0,
       "target": 0.4},
      {"kind": "position", "selection": BAD}], "simulation.restrain[2].selection"),
], ids=["short-form", "a-list", "a-block", "the-second-block"])
def test_it_is_refused_with_the_selection_and_mdtraj_s_reason(restrain, setting):
    with pytest.raises(ConfigError) as caught:
        refuse_what_can_be_seen(_study(restrain=restrain))
    said = str(caught.value)
    assert said.startswith(f"{setting}: {BAD!r} is not an MDTraj selection (")
    # MDTraj's own reason, in the brackets.
    assert "Cannot use literals as truth" in said
    refusal = caught.value.refusal
    assert refusal.code == "config.option.not_permitted"
    assert refusal.details["option"] == setting
    assert refusal.details["given"] == BAD


def test_a_system_s_own_restraint():
    config = {"systems": [{"id": "a", "system": "1L2Y",
                           "simulation": {"restrain": BAD}}]}
    with pytest.raises(ConfigError, match=r"^systems\[a\]\.simulation\.restrain: "):
        refuse_what_can_be_seen(config)


@pytest.mark.parametrize("restrain", [
    "protein and not element H",
    [{"kind": "torsion", "selection": "index 4 6 8 14", "force_constant": 50.0}],
    ["backbone", {"kind": "angle", "selection": "index 1 2 3", "force_constant": 100.0,
                  "target": 1.9}],
])
def test_one_mdtraj_reads_is_not(restrain):
    refuse_what_can_be_seen(_study(restrain=restrain))


def test_a_run_is_refused_before_it_writes_anything(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _make_traj_files(tmp_path / "made")
    here = str(tmp_path / "made" / "simulation" / "topology.pdb")
    path = tmp_path / "s.yml"
    path.write_text(yaml.safe_dump({
        "systems": [{"id": "a", "system": here}], "output": str(tmp_path / "out"),
        "include_phase": ["setup"], "simulation": {"restrain": BAD}}), encoding="utf-8")
    assert main(["explore", "--config", str(path), "--dry-run"]) == 2
    assert f"simulation.restrain: {BAD!r} is not an MDTraj selection" in capsys.readouterr().err
    assert main(["explore", "--config", str(path)]) == 2
    assert "is not an MDTraj selection" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_check_it_refuses_it(tmp_path):
    from fastmdxplora.gui.config_builder import check_config_file

    path = tmp_path / "s.yml"
    path.write_text(yaml.safe_dump(_study(restrain=BAD)), encoding="utf-8")
    checked = check_config_file(str(path), to_run=True, base=tmp_path)
    assert checked["ok"] is False
    assert "simulation.restrain" in checked["error"]
    assert checked["refusal"]["code"] == "config.option.not_permitted"


@pytest.mark.parametrize("axis, values, setting", [
    ("simulation.restrain", ["backbone", BAD], "sweep.simulation.restrain[2]"),
    ("simulation.restrain", [[{"kind": "position", "selection": BAD}]],
     "sweep.simulation.restrain[1][1].selection"),
    ("simulation.save_selection", ["protein", BAD], "sweep.simulation.save_selection[2]"),
], ids=["a-swept-restraint", "a-swept-block", "a-swept-save-selection"])
def test_a_swept_selection_is_refused_naming_its_value(axis, values, setting):
    config = {"systems": [{"id": "a", "system": "1L2Y"}], "sweep": {axis: values}}
    with pytest.raises(ConfigError) as caught:
        refuse_what_can_be_seen(config)
    assert str(caught.value).startswith(f"{setting}: {BAD!r} is not an MDTraj selection (")
    assert caught.value.refusal.details["option"] == setting


def test_a_sweep_of_selections_mdtraj_reads_is_not():
    refuse_what_can_be_seen({"systems": [{"id": "a", "system": "1L2Y"}], "sweep": {
        "simulation.restrain": ["backbone", "protein and not element H"],
        "simulation.temperature_K": [300, 310]}})


def test_a_block_s_selection_that_is_not_text_is_refused_with_the_config():
    """`selection: 5` was read as the selection "5" and refused only when
    the simulation started."""
    from fastmdxplora.config.loader import validate_config

    with pytest.raises(ConfigError, match="Restraint 1's `selection` is 5") as caught:
        validate_config(_study(restrain=[{"kind": "position", "selection": 5}]))
    assert caught.value.refusal.code == "config.option.wrong_type"
