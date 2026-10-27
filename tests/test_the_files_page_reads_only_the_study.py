"""The Files page reads only the study's own records.

Its tiles say how many frames the trajectory holds over how long, from the
run's parameters, and each analysis row its status from the analysis
manifest. Both were read by name, so a record linked out of the study had
the page say what the file outside said, on a public bind too. Each is now
read held to the study (`record_access`): a record linked out of it is not
opened, its tile or row says it was not read, and the answer names it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_the_study_pages_read_only_the_study import _OPENED, _WATCHED, _ask, _link, _watch

pytest.importorskip("mdtraj")

#: Each record the page reads, what the study's own says, what the file
#: outside it says, and what the page would show of that.
RECORDS = {
    "simulation/simulation_parameters.json": (
        {"n_production_frames": 12, "duration_ns_actual": 0.012},
        {"n_production_frames": 4242, "duration_ns_actual": 77.7},
        ("4,242 frames", "77.7 ns")),
    "analysis/analysis_manifest.json": (
        {"phase": "analysis", "results": {"rmsf": {"status": "ok"}}},
        {"phase": "analysis", "results": {"rmsf": {"status": "failed",
                                                   "message": "MARKER-OUTSIDE-777"}}},
        ("MARKER-OUTSIDE-777",)),
}


def _study(tmp_path: Path) -> Path:
    from tests.test_every_route_reads_only_the_study import _study as written

    root = written(tmp_path)
    for relative, (own, _, _) in RECORDS.items():
        (root / relative).write_text(json.dumps(own), encoding="utf-8")
    return root


def _page(root: Path, tmp_path: Path, public: bool) -> dict:
    status, body = _ask(root, tmp_path, "/api/files-page", public=public)
    assert status == 200, body[:300]
    return json.loads(body)


@pytest.mark.parametrize("public", [False, True], ids=["loopback", "public"])
@pytest.mark.parametrize("linked", [*sorted(RECORDS), "both"])
def test_a_record_linked_out_is_said_not_read_on_the_files_page(tmp_path, linked, public):
    root = _study(tmp_path)
    page = _page(root, tmp_path, public)
    assert "12 frames" in page["html"] and "not_read" not in page, page.get("not_read")
    outside = tmp_path / "outside"
    names = sorted(RECORDS) if linked == "both" else [linked]
    for relative in names:
        target = outside / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(RECORDS[relative][1]), encoding="utf-8")
        (root / relative).unlink()
        _link(root / relative, target)

    _watch(outside)
    try:
        page = _page(root, tmp_path, public)
        opened = sorted(set(_OPENED))
    finally:
        _WATCHED.clear()

    assert opened == []
    for relative in names:
        for marker in RECORDS[relative][2]:
            assert marker not in page["html"], marker
        assert f"{Path(relative).name} not read" in page["html"]
        assert f"{relative} was not read" in page["not_read"], page["not_read"]
    assert str(outside) not in json.dumps(page)
    if "simulation/simulation_parameters.json" not in names:
        assert "12 frames" in page["html"]
