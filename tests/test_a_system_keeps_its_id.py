"""A study's resolved config keeps the `id` it gave each system.

A study that said `id: trpcage` was planned as "System: trpcage" and then
recorded `id: s1` in its resolved_config.yml, the config `read_study` shows
as the one the run used: each run is built from the batch layer's spec,
which knew the id, without it, and the writer fell back to s1. Every run of
a study of several systems was recorded the same way. Found running
`fastmdx mcp` from an AI app (2026-10-01).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from fastmdxplora import FastMDXplora


def _structure(tmp_path: Path) -> Path:
    path = tmp_path / "x.pdb"
    path.write_text("ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00"
                    "           C\nEND\n", encoding="utf-8")
    return path


def _ids(folder: Path) -> list[str]:
    config = yaml.safe_load((folder / "resolved_config.yml").read_text(encoding="utf-8"))
    return [entry["id"] for entry in config["systems"]]


def test_one_system_keeps_its_id(tmp_path):
    out = tmp_path / "one"
    FastMDXplora(config_data={
        "systems": [{"system": str(_structure(tmp_path)), "id": "trpcage"}],
        "include_phase": ["report"], "output": str(out)}).explore()
    assert _ids(out) == ["trpcage"]


def test_each_run_of_several_systems_keeps_its_own(tmp_path):
    structure = str(_structure(tmp_path))
    out = tmp_path / "two"
    FastMDXplora(config_data={
        "systems": [{"system": structure, "id": "wild"}, {"system": structure, "id": "mutant"}],
        "include_phase": ["report"], "output": str(out)}).explore()
    assert {run.name: _ids(run) for run in (out / "runs").iterdir()} == {
        "wild": ["wild"], "mutant": ["mutant"]}


def test_a_system_given_no_id_is_s1_as_before(tmp_path):
    out = tmp_path / "plain"
    FastMDXplora(config_data={"systems": [{"system": str(_structure(tmp_path))}],
                              "include_phase": ["report"], "output": str(out)}).explore()
    assert _ids(out) == ["s1"]
