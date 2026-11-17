"""The public config route and existing file/image boundaries reach the output."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import mdtraj as md
import numpy as np
import pytest

from fastmdxplora.mcp.tools import ToolError, _confined
from fastmdxplora.refusals import StudyError
from fastmdxplora.remote.inputs import gather_inputs


def _write_ensembles(folder: Path) -> tuple[Path, Path, Path, Path]:
    rng = np.random.default_rng(102)
    base = rng.normal(size=(8, 3)).astype(np.float32)
    base -= base.mean(axis=0)
    mode = rng.normal(size=(8, 3)).astype(np.float32)
    mode -= mode.mean(axis=0)
    mode /= np.linalg.norm(mode)
    mode2 = rng.normal(size=(8, 3)).astype(np.float32)
    mode2 -= mode2.mean(axis=0)
    mode2 -= np.sum(mode * mode2) * mode
    mode2 /= np.linalg.norm(mode2)
    frames = [base + q * mode + r * mode2
              for q, r in zip(np.linspace(-0.5, 0.5, 12), np.sin(np.arange(12)) * 0.2)]
    topology = md.Topology()
    chain = topology.add_chain("A")
    for i in range(8):
        topology.add_atom("CA", md.element.carbon,
                          topology.add_residue("ALA", chain, resSeq=i + 1))
    reference = md.Trajectory(np.asarray(frames, dtype=np.float32), topology)
    target = md.Trajectory(np.asarray(frames, dtype=np.float32) + mode, topology)
    wt_pdb, lig_pdb = folder / "wt.pdb", folder / "ligand.pdb"
    wt_xtc, lig_xtc = folder / "wt.xtc", folder / "ligand.xtc"
    reference[0].save_pdb(str(wt_pdb))
    target[0].save_pdb(str(lig_pdb))
    reference.save_xtc(str(wt_xtc))
    target.save_xtc(str(lig_xtc))
    return wt_pdb, wt_xtc, lig_pdb, lig_xtc


def test_documented_config_runs_and_existing_views_find_a_static_figure(tmp_path):
    _write_ensembles(tmp_path)
    config = tmp_path / "common.yml"
    config.write_text(
        """systems:
  - system: wt.pdb
output: common-study
include_phase: [analysis]
analysis:
  trajectory: wt.xtc
  topology: wt.pdb
  include: [dimred]
  options:
    dimred:
      methods: [pca]
      n_components: 2
      selection: protein and name CA
      landscape_bins: 12
      comparison:
        reference: {label: WT, temperature_K: 300, sampling: unknown}
        targets:
          - label: ligand
            trajectory: ligand.xtc
            topology: ligand.pdb
            first: 0
            stride: 1
            temperature_K: 300
            sampling: unknown
""", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "fastmdxplora.cli.main", "analyze", "--config", str(config)],
        cwd=tmp_path, text=True, capture_output=True, check=False, env=os.environ.copy())
    assert completed.returncode == 0, completed.stdout + completed.stderr
    root = tmp_path / "common-study"
    comparison = root / "analysis" / "dimred" / "common_pca_landscapes.png"
    assert comparison.is_file()
    assert comparison.stat().st_size > 10_000
    manifest = json.loads((root / "analysis" / "analysis_manifest.json").read_text())
    assert manifest["results"]["dimred"]["status"] == "ok"
    record = json.loads((root / "analysis" / "dimred" / "options.json").read_text())
    assert record["findings"]["comparison"]["histogrammed_frames"] == [12, 12]
    from fastmdxplora.gui.figure_data import _projection
    from fastmdxplora.gui.report_dashboard import build_dashboard

    assert _projection(root, "pca") is None  # no reference-only frame links
    build_dashboard(orchestrator=SimpleNamespace(output_dir=root, system="fixture"),
                    output_dir=root / "report", title="Common comparison")
    assert "analysis/dimred/common_pca_landscapes.png" in (
        root / "report" / "dashboard.html").read_text(encoding="utf-8")


def test_nested_comparison_paths_keep_existing_confinement_and_gathering():
    worktree = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix=".common_inputs_", dir=worktree) as scratch:
        root = Path(scratch).resolve()
        wt_pdb, wt_xtc, ligand_pdb, ligand_xtc = _write_ensembles(root)
        config = {
            "systems": [{"system": str(wt_pdb)}],
            "analysis": {"trajectory": str(wt_xtc), "topology": str(wt_pdb),
                         "options": {"dimred": {"comparison": {
                             "targets": [{"trajectory": str(ligand_xtc),
                                          "topology": str(ligand_pdb)}]}}}},
        }

        def inside(value):
            path = Path(value)
            path = path if path.is_absolute() else root / path
            try:
                return path.resolve().relative_to(root)
            except ValueError:
                return None

        context = SimpleNamespace(workspace=SimpleNamespace(root=root, inside=inside))
        _confined(context, config)
        gathered = gather_inputs(config, root)
        assert set(gathered.files.values()) == {wt_pdb, wt_xtc, ligand_pdb, ligand_xtc}
        outside = root.parent / f"{root.name}-outside.xtc"
        outside.write_bytes(b"outside")
        try:
            config["analysis"]["options"]["dimred"]["comparison"]["targets"][0][
                "trajectory"] = str(outside)
            with pytest.raises(ToolError, match="outside the workspace"):
                _confined(context, config)
            with pytest.raises(StudyError, match="outside"):
                gather_inputs(config, root)
        finally:
            outside.unlink(missing_ok=True)


def test_the_gui_form_offers_no_box_for_the_comparison_mapping():
    """A mapping of other files is set in the Config, where the checks on
    the files it names read it; the form offers dimred's other options."""
    from fastmdxplora.gui.schema_payload import _analysis_options

    shown = {option["name"] for option in _analysis_options()["analyses"]["dimred"]}
    assert "comparison" not in shown
    assert {"methods", "landscape_bins"} <= shown
