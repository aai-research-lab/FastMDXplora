"""An umbrella study's free energy is shown on its Analysis page.

It is written beside the study's windows (`free_energy/pmf/`), where the
page did not look: the page said the windows were "compared below" and
showed nothing, though the profile was the study's result. A study that
refused its profile shows no figure, whatever an earlier run left.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastmdxplora.gui.server import FREE_ENERGY_TITLE, _results_payload


def _an_umbrella_study(where: Path, *, refused: str | None = None) -> Path:
    root = where / "umbrella"
    (root / "free_energy" / "pmf").mkdir(parents=True)
    (root / "batch_manifest.json").write_text(
        json.dumps({"kind": "batch", "n_runs": 0, "runs": []}), encoding="utf-8")
    (root / "pmf.json").write_text(json.dumps({"refused": refused}), encoding="utf-8")
    for name in ("pmf.png", "pmf.svg", "pmf.dat"):
        (root / "free_energy" / "pmf" / name).write_bytes(b"x")
    # A figure of another analysis, which the profile is ranked before.
    (root / "analysis" / "rmsd").mkdir(parents=True)
    (root / "analysis" / "rmsd" / "rmsd.png").write_bytes(b"x")
    return root


def test_the_profile_is_a_figure_of_the_study(tmp_path):
    payload = _results_payload(_an_umbrella_study(tmp_path))
    figures = [plot for plot in payload["plots"] if plot["title"] == FREE_ENERGY_TITLE]
    assert len(figures) == 1
    assert figures[0]["path"] == "free_energy/pmf/pmf.png"
    assert figures[0]["category"] == "Free energy"
    assert payload["has_analysis"]
    # The study's result comes first, is a key figure, and its SVG is among
    # those "Download all SVG figures" gives.
    assert [plot["title"] for plot in payload["plots"]][0] == FREE_ENERGY_TITLE
    assert FREE_ENERGY_TITLE in [plot["title"] for plot in payload["key_plots"]]
    assert payload["svg_figure_count"] == 1


def test_a_refused_profile_shows_no_figure(tmp_path):
    payload = _results_payload(_an_umbrella_study(
        tmp_path, refused="windows 3 and 4 do not overlap"))
    assert not [plot for plot in payload["plots"] if plot["title"] == FREE_ENERGY_TITLE]
    assert payload["svg_figure_count"] == 0


def test_a_refused_profile_s_files_are_still_listed(tmp_path):
    """Review of the round: hiding the refused profile's figure took its
    files off the list of the study's files too, though they are on disk."""
    payload = _results_payload(_an_umbrella_study(
        tmp_path, refused="windows 3 and 4 do not overlap"))
    listed = {record["path"] for record in payload["artifacts"]}
    assert "free_energy/pmf/pmf.dat" in listed
    assert not [plot for plot in payload["plots"] if plot["title"] == FREE_ENERGY_TITLE]
