"""A metadynamics study reads its own record after it is analysed.

The analysis phase recorded the reweighting pass among the analyses that
ran, so a biased study's ``resolved_config.yml`` listed ``reweighted``
under ``analysis.include``: `fastmdx report` and `fastmdx analyze --rerun`
then refused the study's own record ("No analysis is called 'reweighted'").
The record lists the analyses alone now, and a record saved with the name
reads as the analyses it names.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from test_report_wiring import _make_traj_files  # noqa: E402
from test_reweighted_averages import _write_run  # noqa: E402

from fastmdxplora import FastMDXplora  # noqa: E402
from fastmdxplora.cli.main import main  # noqa: E402
from fastmdxplora.orchestrator import RUN_PROCESS_FILE  # noqa: E402


@pytest.fixture
def biased(tmp_path) -> Path:
    """A study of a metadynamics run (PLUMED's HILLS and COLVAR beside its
    trajectory), analysed through `explore`."""
    root = _make_traj_files(tmp_path / "study", n_frames=40)
    _write_run(root)
    (root / "analysis").rmdir()
    FastMDXplora(system=str(root / "simulation" / "topology.pdb"), output_dir=root).explore(
        include_phase=["analysis"], options={"analysis": {"include": ["rmsd", "rg"]}})
    (root / RUN_PROCESS_FILE).unlink(missing_ok=True)
    assert (root / "analysis" / "reweighted").is_dir()
    return root


def _include(root: Path) -> list:
    return yaml.safe_load((root / "resolved_config.yml").read_text())["analysis"]["include"]


def test_the_record_lists_the_analyses_alone(biased):
    assert _include(biased) == ["rmsd", "rg"]


def test_report_and_rerun_read_the_study_s_record(biased, capsys):
    assert main(["report", "--output", str(biased)]) == 0
    assert main(["analyze", "--output", str(biased), "--rerun", "--no-explain"]) == 0
    assert "No analysis is called" not in capsys.readouterr().err
    assert (biased / "analysis" / "reweighted").is_dir()


def test_a_record_saved_with_the_name_reads(biased, capsys):
    """As a study analysed before this version saved it."""
    path = biased / "resolved_config.yml"
    saved = yaml.safe_load(path.read_text())
    saved["analysis"]["include"] = ["rmsd", "rg", "reweighted"]
    path.write_text(yaml.safe_dump(saved, sort_keys=False))
    assert main(["report", "--output", str(biased)]) == 0
    assert main(["analyze", "--output", str(biased), "--rerun", "--no-explain"]) == 0
    assert "No analysis is called" not in capsys.readouterr().err
    assert _include(biased) == ["rmsd", "rg"]
    assert (biased / "analysis" / "reweighted").is_dir()


def test_a_config_naming_only_the_pass_reads_as_the_default_plan():
    from fastmdxplora.config.loader import without_the_reweighting_pass

    analysis = {"include": ["reweighted"], "options": {}}
    without_the_reweighting_pass(analysis)
    assert "include" not in analysis
