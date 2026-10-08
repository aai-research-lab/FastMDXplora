"""The Analysis page and the Overview say only what the study has.

"3 of 19 means determined, 11 more not shown" was said with ten hidden (the
RMSF tile counted as a mean). RMSF was offered a Convergence that said only
"rmsf recorded no mean over its frames", by its folder's name. A strand
fraction of 0 in every frame was "Not determined" from "1 independent
sample". Find an analysis suggested "residue 189", which finds nothing.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from tests.test_the_analysis_page_reads_as_a_whole import _analysis, _ar1, _manifest


def test_more_not_shown_counts_the_means_not_shown(tmp_path, monkeypatch):
    """"3 of 19 means determined, 11 more not shown" with 10 hidden: the
    RMSF tile, beyond those shown, was counted as a mean."""
    from fastmdxplora.gui import analysis_overview
    from fastmdxplora.gui.overview_view import MOST_TILES, _tiles

    rows = [{"analysis": f"m{k:02d}", "quantities": [
        {"key": "mean", "label": f"m{k:02d}", "value": 1.0, "determined": k % 2 == 0}]}
        for k in range(MOST_TILES + 3)]
    rows.append({"analysis": "rmsf", "quantities": []})
    monkeypatch.setattr(analysis_overview, "overview_of", lambda root: {"rows": rows})
    root = tmp_path / "study"
    _analysis(root, "rmsf", None, rows="1 0.05\n2 0.07\n3 0.06\n")
    said = _tiles(root)
    assert len(said["tiles"]) == MOST_TILES
    assert all(tile["kind"] == "mean" for tile in said["tiles"])
    assert said["means"] == MOST_TILES + 3
    assert said["more"] == 3


def test_convergence_is_said_by_the_page_s_name(tmp_path):
    from fastmdxplora.gui.analysis_overview import convergence_payload

    root = tmp_path / "study"
    _manifest(root, {"rmsf": {"status": "ok"}})
    _analysis(root, "rmsf", None, rows="1 0.05\n2 0.07\n3 0.06\n")
    said = convergence_payload(root, "rmsf")
    assert not said["ok"] and said["reason"].startswith("RMSF ")


def test_convergence_is_offered_only_where_there_is_a_mean():
    script = (Path(__file__).parents[1] / "src" / "fastmdxplora" / "gui" / "static"
              / "analysis-page.js").read_text(encoding="utf-8")
    assert "function hasAMean(name)" in script
    assert "if (!hasAMean(frame.getAttribute(\"data-series\"))) return;" in script
    assert "residue 189" not in script


def test_a_quantity_the_same_in_every_frame_is_said_so(tmp_path):
    from fastmdxplora.gui.analysis_overview import overview_of

    root = tmp_path / "study"
    _manifest(root, {"ss": {"status": "ok"}})
    _analysis(root, "ss", 0.4 + 0.01 * _ar1(400, 0.5, 1), unit="",
              others={"strand_fraction": np.zeros(400)})
    strand = next(q for row in overview_of(root)["rows"] for q in row["quantities"]
                  if q["key"] == "strand_fraction")
    assert strand["said"].startswith("0") and strand["said"].endswith(" in every frame")
    assert strand["samples"] is None and not strand["determined"]
    assert "same in every frame" in strand["why"]
