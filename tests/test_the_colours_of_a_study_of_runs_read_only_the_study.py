"""The colours by residue of a study of runs read only the study.

`/api/residue-values` for a study of several runs read its
`batch_manifest.json` and, to find the run played and the runs of its
atoms, each run's `joined/joined.json` and `simulation/live_frame_history.json`
with plain reads: linked out of the study, they were opened where they led,
and the answer said nothing was not read. Each is read in the held read,
and a record refused is named.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.test_a_record_not_there_is_not_said_not_read import _runs
from tests.test_the_study_pages_read_only_the_study import (
    _OPENED,
    _WATCHED,
    _ask,
    _link,
    _watch,
    _write,
)

PUBLIC = pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])


def _asked(root: Path, tmp_path: Path, outside: Path, public: bool) -> tuple[dict, list[str]]:
    """`/api/residue-values` as a page asks it, and what it opened outside."""
    _watch(outside)
    try:
        status, body = _ask(root, tmp_path, "/api/residue-values", public=public)
        opened = sorted(set(_OPENED))
    finally:
        _WATCHED.clear()
    assert status == 200
    return json.loads(body), opened


@PUBLIC
def test_a_batch_manifest_linked_out_is_not_read_and_is_named(tmp_path, public):
    root = _runs(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.move(root / "batch_manifest.json", outside / "batch_manifest.json")
    _link(root / "batch_manifest.json", outside / "batch_manifest.json")
    answer, opened = _asked(root, tmp_path, outside, public)
    assert opened == []
    assert answer["unavailable"] == ["batch_manifest.json"]
    assert answer["not_read"].startswith("batch_manifest.json was not read")


@PUBLIC
def test_a_run_linked_out_is_not_read_to_find_the_run_played(tmp_path, public):
    """The first run has written nothing, so the second is looked in for the
    run played: linked out, its join record and snapshots are not opened."""
    root = _runs(tmp_path)
    outside = tmp_path / "outside"
    _write(root, "runs/r2/joined/joined.json", {"topology": "topology.pdb", "frames": 2})
    _write(root, "runs/r2/simulation/live_frame_history.json",
           {"frames": [{"sequence": 1}, {"sequence": 2}], "count": 2})
    shutil.move(root / "runs" / "r2", outside)
    _link(root / "runs" / "r2", outside)
    answer, opened = _asked(root, tmp_path, outside, public)
    assert opened == []
    assert answer["properties"] == []
    # A run's folder linked out is named as a folder, with its "/".
    assert "runs/r2/" in answer["unavailable"]
    assert "runs/r2/ is a link out of the study" in answer["not_read"]


def test_replicas_unlinked_are_coloured_by_their_mean_as_before(tmp_path):
    """Nothing linked: the held read finds the run played and the runs of
    its atoms as a read outside one does, and says nothing not read."""
    pytest.importorskip("mdtraj")
    from fastmdxplora.gui.by_residue import values_by_residue
    from fastmdxplora.gui.server import _confined
    from tests.test_replicas_colour_the_structure_by_their_mean import _analysed

    study = _analysed(tmp_path / "study")
    held = _confined(study, values_by_residue)
    assert "unavailable" not in held
    assert held["properties"][0]["label"] == "RMSF, mean of 3 runs"
    assert json.loads(json.dumps(held)) == json.loads(json.dumps(values_by_residue(study)))
