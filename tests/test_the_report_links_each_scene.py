"""The report links each scene written with the study.

A scene is a view of the study kept as a MolViewSpec file; it opens as it
was shown in any viewer built on Mol*. The report lists them under Scenes,
each linked from where the report is written and said in a line (its frame,
representation, colouring and superposition), rather than embedding a
viewer: the report stays a document, and a scene a file that opens anywhere.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from fastmdxplora.report.document import _scenes_section


def _scene(root: Path, name: str, custom: dict) -> None:
    folder = root / "scenes"
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(folder / f"{name}.mvsx", "w") as archive:
        archive.writestr("index.mvsj", json.dumps(
            {"kind": "single", "root": {"kind": "root", "custom": {"fastmdxplora": custom}}}))


def test_each_scene_is_linked_and_said(tmp_path):
    root = tmp_path / "study"
    _scene(root, "pocket late", {"of": "frames", "frame": 40, "view": {
        "representation": "ballAndStick", "colour": "result:rmsf", "superposed": "pocket"}})
    _scene(root, "apo", {"of": "structure", "frame": None, "view": {"colour": "chain"}})
    (root / "scenes" / "not a scene.txt").write_text("x", encoding="utf-8")
    section = _scenes_section(root, root / "report")
    lines = section.splitlines()
    assert lines[0] == "## Scenes"
    assert "[molstar.org/viewer](https://molstar.org/viewer/)" in section
    assert lines[-2:] == [
        "- [apo](../scenes/apo.mvsx): the structure, coloured by chain.",
        "- [pocket late](../scenes/pocket%20late.mvsx): frame 40, ball and stick, "
        "coloured by rmsf, superposed on the pocket.",
    ]


def test_a_study_without_scenes_has_no_section(tmp_path):
    assert _scenes_section(tmp_path, tmp_path / "report") == ""
    (tmp_path / "scenes").mkdir()
    assert _scenes_section(tmp_path, tmp_path / "report") == ""


def test_a_scene_that_cannot_be_read_is_still_listed(tmp_path):
    folder = tmp_path / "scenes"
    folder.mkdir()
    with zipfile.ZipFile(folder / "odd.mvsx", "w") as archive:
        archive.writestr("index.mvsj", "{not json")
    assert _scenes_section(tmp_path).splitlines()[-1] == (
        "- [odd](../scenes/odd.mvsx): the structure.")


def test_it_sits_after_the_results(tmp_path):
    from types import SimpleNamespace

    from fastmdxplora.report.document import build_document
    from tests.test_report_wiring import _a_reported_run

    root = _a_reported_run(tmp_path, n_frames=400)
    _scene(root, "first look", {"of": "frames", "frame": 1, "view": {}})
    (root / "report").mkdir()
    build_document(orchestrator=SimpleNamespace(output_dir=root), output_dir=root / "report",
                   title="t", author=None, include_methods=False,
                   include_reproducibility=False)
    report = (root / "report" / "report.md").read_text(encoding="utf-8")
    assert report.index("## Results") < report.index("## Scenes") < report.index("## Convergence")
    assert "- [first look](../scenes/first%20look.mvsx): frame 1." in report
