"""A metadynamics study reads its own record after it is analysed.

The analysis phase recorded the reweighting pass among the analyses that
ran, so a biased study's ``resolved_config.yml`` listed ``reweighted``
under ``analysis.include``: `fastmdx report` and `fastmdx analyze --rerun`
then refused the study's own record ("No analysis is called 'reweighted'").
The record lists the analyses alone now, and a record saved with the name
reads as the analyses it names: in `report` and `analyze --rerun`, in
`resume`, in an extension and its join, and where a study of several runs
writes a run's report again.
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


@pytest.mark.parametrize("key", ["include", "exclude"])
def test_a_config_that_names_the_pass_is_refused_by_name(key):
    """Dropped from a study's record alone: `include: [reweighted]` in a
    Config read as the default plan, every analysis, with no word."""
    from fastmdxplora.config import ConfigError
    from fastmdxplora.config.loader import validate_config

    with pytest.raises(ConfigError) as raised:
        validate_config({"analysis": {key: ["reweighted"]}})
    said = str(raised.value)
    assert said.startswith("No analysis is called 'reweighted'")
    assert "it runs wherever a bias applies" in said
    recorded = {"analysis": {key: ["rmsd", "reweighted"]}}
    validate_config(recorded, as_record=True)
    assert recorded["analysis"][key] == ["rmsd"]


def test_resume_reads_a_record_saved_with_the_name(biased, capsys):
    """An analysis cut short (no Manifest, as a killed run leaves it) of a
    study whose record was saved by an earlier version: `resume` gave the
    record to the study as a Config, which refused the name (exit 1, a
    traceback)."""
    import shutil

    path = biased / "resolved_config.yml"
    saved = yaml.safe_load(path.read_text())
    saved["analysis"]["include"] = ["rmsd", "rg", "reweighted"]
    path.write_text(yaml.safe_dump(saved, sort_keys=False))
    (biased / "manifest.json").unlink()
    shutil.rmtree(biased / "analysis" / "reweighted")
    assert main(["resume", str(biased)]) == 0
    assert "No analysis is called" not in capsys.readouterr().err
    assert (biased / "analysis" / "reweighted" / "reweighted_averages.json").is_file()
    assert _include(biased) == ["rmsd", "rg"]


# ---------------------------------------------------------------------------
# The extension, the join and a study of several runs read the record too
# ---------------------------------------------------------------------------

try:
    import mdtraj  # noqa: F401
    import openmm  # noqa: F401
    import pdbfixer  # noqa: F401
    HAS_BACKENDS = True
except ImportError:  # pragma: no cover - the backends are optional
    HAS_BACKENDS = False

needs_backends = pytest.mark.skipif(not HAS_BACKENDS,
                                    reason="OpenMM, PDBFixer and MDTraj are needed")


class _Killed(BaseException):
    """The process killed: nothing after it runs, no handler sees it."""


def _name_it(study: Path) -> None:
    """The study's record, and the copy a join leaves of it, listing the
    reweighting pass, as main saves an umbrella window's."""
    for path in (study / "resolved_config.yml", study / "joined" / "study_resolved_config.yml"):
        if not path.is_file():
            continue
        saved = yaml.safe_load(path.read_text(encoding="utf-8"))
        saved["analysis"] = {**(saved.get("analysis") or {}), "include": ["rmsd", "reweighted"]}
        path.write_text(yaml.safe_dump(saved, sort_keys=False), encoding="utf-8")


@pytest.fixture(scope="module")
def ran(tmp_path_factory):
    """A real study of 200 production steps, analysed and reported: the
    study's folder, and a copy of it as it was then."""
    import shutil

    from test_a_real_study_runs_end_to_end import TRI_ALANINE

    # Resolved: a study records its folder resolved (/var is a link on macOS).
    root = tmp_path_factory.mktemp("ran").resolve()
    pdb = root / "tri-ala.pdb"
    pdb.write_text(TRI_ALANINE)
    study = root / "study"
    FastMDXplora(config_data={
        "systems": [{"id": "tri", "system": str(pdb)}],
        "setup": {"ph": 7.0, "solvent_padding_nm": 1.0, "nonbonded_cutoff_nm": 0.9},
        "simulation": {"platform": "CPU", "production_steps": 200,
                       "nvt_steps": 50, "npt_steps": 50, "timestep_fs": 2,
                       "trajectory_interval_steps": 50,
                       "checkpoint_interval_steps": 100},
        "analysis": {"include": ["rmsd"]},
        "report": {"document": True, "slides": False, "pdf": False, "bundle": False},
    }, output_dir=str(study)).explore()
    assert (study / "analysis" / "analysis_manifest.json").is_file()
    pristine = root / "pristine"
    shutil.copytree(study, pristine, symlinks=True)
    return study, pristine


@pytest.fixture
def study(ran) -> Path:
    """The study as it was run, put back in its own folder."""
    import shutil

    study, pristine = ran
    shutil.rmtree(study)
    shutil.copytree(pristine, study, symlinks=True)
    return study


def _analysed_whole(study: Path) -> None:
    import json

    analysed = json.loads((study / "analysis" / "analysis_manifest.json").read_text())
    assert Path(analysed["trajectory_input"]) == study / "joined" / "production.dcd"
    assert analysed["results"]["rmsd"]["status"] == "ok"


@needs_backends
def test_an_extension_reads_a_record_saved_with_the_name(study):
    """`explore` with `resume_from` and `extra_ns` (`extend_study`): the
    segment was given the record as a Config, which refused the name."""
    from fastmdxplora.simulation.resume import extend_study

    _name_it(study)
    answer = extend_study(study, more_ns=0.0002)
    assert answer["ok"] is True, answer
    assert (study / "segment-001" / "simulation" / "state_final.xml").is_file()
    _analysed_whole(study)


@needs_backends
def test_resume_after_the_extension_s_piece_reads_a_record_saved_with_the_name(study, capsys):
    """An extension begun before the upgrade, stopped once its piece ran:
    the join gave the record to the study as a Config (exit 1, the study's
    analysis set aside in previous/ and none in its place)."""
    from unittest import mock

    from fastmdxplora.simulation import resume

    def stopped(*_args, **_kwargs):
        raise _Killed()

    with mock.patch.object(resume, "_join_and_analyse", stopped):
        with pytest.raises(_Killed):
            resume.extend_study(study, more_ns=0.0002)
    _name_it(study)
    capsys.readouterr()
    assert main(["resume", str(study)]) == 0
    assert "No analysis is called" not in capsys.readouterr().err
    _analysed_whole(study)


@needs_backends
def test_resume_writes_the_report_of_a_chosen_analysis_from_a_record_saved_with_the_name(
        study, capsys):
    """The person's analysis chosen after the join, its report moved aside:
    `resume` writes the report from the record as a Config."""
    from fastmdxplora.simulation.resume import extend_study

    assert extend_study(study, more_ns=0.0002)["ok"] is True
    first = study / "simulation" / "production.dcd"
    assert main(["analyze", "--output", str(study), "--rerun", "--trajectory", str(first),
                 "--stride", "2"]) == 0
    assert not (study / "report").exists()
    _name_it(study)
    capsys.readouterr()
    assert main(["resume", str(study)]) == 0
    said = capsys.readouterr()
    assert "its report was written over it" in said.out, said.out
    assert "No analysis is called" not in said.err
    assert (study / "report" / "report.md").is_file()


def test_a_run_s_report_written_again_reads_a_record_saved_with_the_name(tmp_path):
    """A study of several runs writes a run's report again from its record
    (`BatchExplorer._report_again`), given to the run as a Config: refused,
    the report was left as it was. The record is put back as it was."""
    from test_the_report_reads_the_records import (
        _as_recorded,
        _recorded,
        _replicas,
        _said_in_its_report,
    )

    from fastmdxplora.batch.explorer import compare_the_runs_again

    members = _replicas(tmp_path)
    for run in members:
        _name_it(run)
    saved = {run: (run / "resolved_config.yml").read_bytes() for run in members}
    before = [_recorded(run) for run in members]
    compare_the_runs_again(tmp_path)
    after = [_recorded(run) for run in members]
    assert any(new["discard"] != old["discard"] for old, new in zip(before, after)), \
        "the shared start moved no run's start: the test shows nothing"
    for run, record in zip(members, after):
        assert _as_recorded(record) in _said_in_its_report(run), run.name
        assert (run / "resolved_config.yml").read_bytes() == saved[run]
