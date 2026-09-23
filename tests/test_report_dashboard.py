from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from fastmdxplora.gui.report_dashboard import build_dashboard
from fastmdxplora.report.region_highlights import (
    RegionHighlight,
    build_pymol_script,
    build_region_highlight_artifacts,
    validate_region_highlights,
)


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01"
    b"\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_static_dashboard_discovers_sections_links_and_dark_assets(tmp_path: Path) -> None:
    root = tmp_path / "run"
    _write_json(
        root / "manifest.json",
        {
            "system": "demo-system",
            "phases": [
                {"name": "analysis", "status": "ok"},
                {"name": "report", "status": "ok"},
            ],
        },
    )
    _write_json(
        root / "analysis" / "analysis_manifest.json",
        {"n_frames": 2, "n_atoms": 12, "results": {"rmsd": {"status": "ok"}}},
    )
    (root / "report").mkdir(parents=True)
    (root / "report" / "report.md").write_text("# Report\n", encoding="utf-8")
    (root / "report" / "slides.pptx").write_bytes(b"pptx")
    (root / "report" / "project_bundle.zip").write_bytes(b"zip")

    _write_plot(root, "analysis/rmsd/rmsd.png", "analysis/rmsd/rmsd.dat", "0 0.10\n1 0.20\n")
    _write_plot(root, "analysis/rg/rg.png", "analysis/rg/rg.dat", "0 1.0\n1 1.2\n")
    _write_plot(root, "analysis/sasa/sasa.png", "analysis/sasa/sasa.dat", "0 20.0\n1 22.0\n")
    _write_plot(root, "analysis/dimred/dimred_pca.png", "analysis/dimred/dimred_pca.dat", "0 0.1 0.2\n1 0.3 0.4\n")
    _write_plot(root, "analysis/cluster/cluster_kmeans.png", "analysis/cluster/cluster_kmeans.dat", "frame,cluster\n0,1\n1,2\n2,1\n")
    _write_plot(root, "analysis/cluster/cluster_kmeans_counts.png", None, None)
    _write_plot(root, "analysis/ss/ss.png", "analysis/ss/ss.dat", "frame,1,2\n0,H,C\n1,E,T\n")
    _write_plot(root, "analysis/qvalue/qvalue.png", "analysis/qvalue/qvalue.dat", "0.9\n0.8\n")
    (root / "report" / "region_highlight_summary.png").write_bytes(PNG_BYTES)

    artifacts = build_dashboard(
        orchestrator=SimpleNamespace(output_dir=root, system="demo-system"),
        output_dir=root / "report",
        title="Demo Dashboard",
        include_bundle_link=True,
    )

    html = (root / "report" / "dashboard.html").read_text(encoding="utf-8")
    assert artifacts == ["dashboard.html"]
    # One section per analysis; the old pooled headings are gone.
    for section in (
        "RMSD",
        "Solvent Accessible Surface Area",
        "Secondary Structure",
        "Dimensionality Reduction",
        "Clustering",
        "Region Highlights",
    ):
        assert section in html
    for link in (
        "report.md",
        "slides.pptx",
        "project_bundle.zip",
        "../analysis/analysis_manifest.json",
        "../analysis/rmsd/rmsd.png",
        "../analysis/cluster/cluster_kmeans_counts.png",
    ):
        assert link in html
    # Every panel shows the analysis figure, tagged with its section.
    assert '<span class="tag">' in html
    assert "Analysis/report workflow from existing trajectory." in html


def test_region_highlights_validate_ranges_and_defaults() -> None:
    regions = validate_region_highlights(
        [{"start": 2, "end": 4}, {"label": "Loop", "start": 5, "end": 5, "color": "red"}],
        np.array([1, 2, 3, 4, 5]),
    )

    assert regions[0] == RegionHighlight("Region 1", 2, 4, "#4E79A7")
    assert regions[1].label == "Loop"
    assert regions[1].color == "red"

    with pytest.raises(ValueError, match="end must be >= start"):
        validate_region_highlights([{"start": 4, "end": 3}], np.array([1, 2, 3, 4]))
    with pytest.raises(ValueError, match="outside the RMSF residue range"):
        validate_region_highlights([{"start": 1, "end": 10}], np.array([2, 3, 4]))
    with pytest.raises(ValueError, match="missing required key"):
        validate_region_highlights([{"end": 3}], np.array([1, 2, 3]))


def test_region_highlight_artifacts_and_dashboard_metadata(tmp_path: Path) -> None:
    root = tmp_path / "run"
    report = root / "report"
    rmsf = root / "analysis" / "rmsf" / "rmsf.dat"
    rmsf.parent.mkdir(parents=True)
    rmsf.write_text("1 0.1\n2 0.3\n3 0.2\n4 0.5\n", encoding="utf-8")
    _write_json(
        root / "manifest.json",
        {"system": "demo", "phases": [{"name": "analysis", "status": "ok"}]},
    )
    _write_json(root / "analysis" / "analysis_manifest.json", {"n_frames": 4})

    artifacts = build_region_highlight_artifacts(
        project_root=root,
        output_dir=report,
        region_highlights=[{"label": "active loop", "start": 2, "end": 3, "color": "#E15759"}],
    )

    manifest = json.loads((report / "region_highlight_manifest.json").read_text(encoding="utf-8"))
    assert "region_highlight_manifest.json" in artifacts
    assert "region_highlight_summary.png" in artifacts
    assert (root / "analysis" / "rmsf" / "rmsf_region_highlights.png").is_file()
    assert (report / "region_highlight_summary.png").is_file()
    assert manifest["status"] == "ok"
    assert manifest["skipped"][0]["artifact"] == "structure_region_highlights.png"

    build_dashboard(
        orchestrator=SimpleNamespace(output_dir=root, system="demo"),
        output_dir=report,
        title="Region Dashboard",
    )
    html = (report / "dashboard.html").read_text(encoding="utf-8")
    assert "Region Highlights" in html
    assert "region_highlight_summary.png" in html
    assert "rmsf_region_highlights.png" in html


def test_region_highlight_pymol_script_contains_region_commands(tmp_path: Path) -> None:
    script = build_pymol_script(
        topology_path=tmp_path / "topology with spaces.pdb",
        output_path=tmp_path / "out.png",
        regions=[
            RegionHighlight("Alpha", 2, 5, "#E15759"),
            RegionHighlight("Beta", 8, 9, "not-a-color"),
        ],
    )

    assert "load " in script
    assert "set_color fastmdx_region_1, [0.8824, 0.3412, 0.3490]" in script
    assert "select fastmdx_sel_1, prot and polymer.protein and resi 2-5" in script
    assert "show sticks, fastmdx_sel_2 and sidechain" in script
    assert "png " in script
    assert "ray 1800, 1200" in script


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _write_plot(root: Path, image_rel: str, data_rel: str | None, data: str | None) -> None:
    image = root / image_rel
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(PNG_BYTES)
    if data_rel is not None and data is not None:
        path = root / data_rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")
