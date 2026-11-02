"""A study stopped after its pieces are joined and before their analysis and
report have run is analysed when it is carried on.

An extended study's pieces are joined, then the study's analyses and report
run again over the join. A study stopped anywhere between (a wall-time stop
lands where it lands) was read as finished: its Manifest said every phase
done, its joined record took in the last piece, and `fastmdx resume`
answered "nothing" while its report was of the first piece alone (0.4 ps of
0.6). The join now says, before it is written, that the analysis is owed,
and only the analyses and report having run take that back.

A resume's answer gives the study's production before and after it by one
reading, in every answer that ends well: a variable-step study's by its
clock (it gave 0.005 ns before, its steps times the timestep, beside 0.00649
ns after by the clock, so their difference was not what the resume ran).

A real study is run and extended by 100 steps, and the extension is stopped
once its piece has run. From that state, the join and its analysis are run
again and stopped at each point between the join and the end of the report,
as a kill leaves them; `fastmdx resume` is then run on what is left.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

import pytest
import yaml

try:
    import mdtraj  # noqa: F401
    import openmm  # noqa: F401
    import pdbfixer  # noqa: F401
    HAS_BACKENDS = True
except ImportError:  # pragma: no cover - the backends are optional
    HAS_BACKENDS = False

needs_backends = pytest.mark.skipif(not HAS_BACKENDS, reason="OpenMM, PDBFixer and MDTraj are needed")


class _Killed(BaseException):
    """The process killed: nothing after it runs, no handler sees it."""


def _config(study: Path) -> dict:
    return yaml.safe_load((study / "resolved_config.yml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def extended():
    """A study of 200 production steps extended by 100, stopped once its
    piece ran and before the pieces were joined: the study's folder, and a
    copy of it as it was then to put back before each case."""
    from fastmdxplora import FastMDXplora
    from fastmdxplora.simulation import resume
    from tests.test_a_real_study_runs_end_to_end import TRI_ALANINE

    root = Path(tempfile.mkdtemp())
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

    def stopped(*_args, **_kwargs):
        raise _Killed()

    with mock.patch.object(resume, "_join_and_analyse", stopped):
        with pytest.raises(_Killed):
            resume.extend_study(study, more_ns=0.0002)
    assert (study / "segment-001" / "simulation" / "state_final.xml").is_file()
    pristine = root / "pristine"
    shutil.copytree(study, pristine, symlinks=True)
    yield study, pristine
    shutil.rmtree(root, ignore_errors=True)


def _put_back(study: Path, pristine: Path) -> None:
    shutil.rmtree(study)
    shutil.copytree(pristine, study, symlinks=True)


def _join_the_extension(study: Path) -> dict:
    """What the extension does once its piece has run (`_extend_study`)."""
    from fastmdxplora.simulation import resume

    piece = study / "segment-001"
    config = yaml.safe_load(piece.with_name("segment-001.yml").read_text(encoding="utf-8"))
    return resume._join_and_analyse(study, config, segment=piece, keep_frames=None)


def _after_writing(name: str, nth: int, *, once_analysed: bool = False):
    """Stopped just after the ``nth`` whole write of a file called ``name``
    (``once_analysed``: of those after the analyses and report ran)."""
    import fastmdxplora

    real_write, real_replace = Path.write_text, Path.replace
    real_explore = fastmdxplora.FastMDXplora.explore
    count = {"n": 0, "analysed": not once_analysed}

    def counted(target: Path) -> None:
        if target.name == name and count["analysed"]:
            count["n"] += 1
            if count["n"] == nth:
                raise _Killed()

    def write_text(self, *args, **kwargs):
        written = real_write(self, *args, **kwargs)
        if not self.name.endswith(".new"):
            counted(self)
        return written

    def replace(self, target):
        moved = real_replace(self, target)
        counted(Path(target))
        return moved

    def explore(self, *args, **kwargs):
        ran = real_explore(self, *args, **kwargs)
        count["analysed"] = True
        return ran

    return [mock.patch.object(Path, "write_text", write_text),
            mock.patch.object(Path, "replace", replace),
            mock.patch.object(fastmdxplora.FastMDXplora, "explore", explore)]


def _at_entry(owner, name: str, *, after: bool = False):
    real = getattr(owner, name)

    def stopped(*args, **kwargs):
        if after:
            real(*args, **kwargs)
        raise _Killed()

    return [mock.patch.object(owner, name, stopped)]


def _points():
    import fastmdxplora
    import fastmdxplora.report as report
    from fastmdxplora.simulation import resume

    return {
        # The joined record written, nothing of the analysis begun.
        "joined.json": lambda: _after_writing("joined.json", 1),
        # The study's own config put back, then at its whole length.
        "config put back": lambda: _after_writing("resolved_config.yml", 1),
        "config at its whole length": lambda: _after_writing("resolved_config.yml", 2),
        "copy of the config": lambda: _after_writing("study_resolved_config.yml", 1),
        "analysis begun": lambda: _at_entry(fastmdxplora.FastMDXplora, "explore"),
        "report begun": lambda: _at_entry(report, "run"),
        # The analysis and report ended, the study's config written back.
        "config written back": lambda: _after_writing("resolved_config.yml", 1,
                                                      once_analysed=True),
        "production recorded": lambda: _at_entry(resume, "_production_carried_on"),
        "after it": lambda: _at_entry(resume, "_production_carried_on", after=True),
    }


#: Stopped once the analyses and report had run: analysing again is not
#: owed, and does no harm.
_ANALYSED = ("production recorded", "after it")


@needs_backends
@pytest.mark.parametrize("point", list(_points()))
def test_a_study_stopped_there_is_analysed_whole_when_carried_on(extended, point) -> None:
    from fastmdxplora.simulation.resume import resume_study

    study, pristine = extended
    _put_back(study, pristine)
    patches = _points()[point]()
    try:
        for patch in patches:
            patch.start()
        with pytest.raises(_Killed):
            _join_the_extension(study)
    finally:
        for patch in patches:
            patch.stop()

    answer = resume_study(study)
    assert answer["ok"], answer.get("error")
    assert answer["did"] in (("analysed", "nothing") if point in _ANALYSED
                             else ("analysed",)), answer
    assert answer["production_done_ns"] == pytest.approx(0.0006)

    analysed = json.loads((study / "analysis" / "analysis_manifest.json").read_text())
    assert Path(analysed["trajectory_input"]) == study / "joined" / "production.dcd"
    assert analysed["n_frames"] == 6
    report = (study / "report" / "report.md").read_text(encoding="utf-8")
    assert "Production dynamics were run for 0.6 ps" in report
    simulation = _config(study)["simulation"]
    assert simulation["duration_ns"] == pytest.approx(0.0006)
    assert simulation.get("production_steps") is None
    assert not simulation.get("resume_from")
    assert (simulation["nvt_steps"], simulation["npt_steps"]) == (50, 50)
    assert not (study / "joined" / "analysis_owed.json").exists()
    assert not (study / "joined" / "study_resolved_config.yml").exists()

    # Carried on, it is finished; an edit made since is the study's.
    resolved = study / "resolved_config.yml"
    resolved.write_text(resolved.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    again = resume_study(study)
    assert (again["ok"], again["did"]) == (True, "nothing")
    assert resolved.read_text(encoding="utf-8").endswith("# edited\n")


@needs_backends
def test_a_study_config_that_does_not_read_is_left_as_it_is(extended) -> None:
    from fastmdxplora.simulation import resume

    study, pristine = extended
    _put_back(study, pristine)
    resolved = study / "resolved_config.yml"
    broken = "simulation: [duration_ns: 0.0004\n"
    resolved.write_text(broken, encoding="utf-8")
    answer = _join_the_extension(study)
    assert answer["ok"] is False and answer["stage"] == "analysis"
    assert "does not read" in answer["error"]
    # Run from the piece's config, the analysis wrote it over the study's.
    assert resolved.read_text(encoding="utf-8") == broken
    assert not (study / "joined" / resume.STUDY_CONFIG_COPY).exists()
    assert resume.analysis_owed(study)


def _a_variable_step_study_stopped_in_its_extension(tmp_path: Path) -> Path:
    """The records a variable-step tri-alanine study left: 2,000 steps by
    its clock 3.208 ps, extended and stopped at its checkpoint at step 2,500
    (4.123 ps by the checkpoint's record)."""
    from tests.test_a_piece_stopped_off_an_energy_row_is_read_by_its_clock import (
        VARIABLE,
        _checkpoint,
        _energy,
        _piece,
        _record,
        _state,
    )

    root = tmp_path / "varyc"
    own = _piece(root, None)
    _record(own, {**VARIABLE, "production_steps": 2000, "nvt_steps": 100, "npt_steps": 100},
            {"nvt_steps": 100, "npt_steps": 100, "production_steps": 2000})
    _state(own, 2000, 3.208167103966084)
    _checkpoint(own, 2000, finished=True, time_ps=3.208167103966084)
    stopped = _piece(root, "segment-001")
    _checkpoint(stopped, 2500, finished=False, time_ps=4.123377771744367)
    _energy(stopped, [(1000, 1.6534028202403404), (2000, 3.3005176213547744)])
    (root / "resolved_config.yml").write_text(yaml.safe_dump({
        "systems": [{"id": "tri", "system": "tri.pdb"}],
        "simulation": {**VARIABLE, "production_steps": 2000,
                       "nvt_steps": 100, "npt_steps": 100}}), encoding="utf-8")
    return root


def test_a_resume_gives_before_and_after_by_the_clock(tmp_path, monkeypatch, capfd) -> None:
    from fastmdxplora.cli.main import main
    from fastmdxplora.simulation import resume
    from tests.test_a_piece_stopped_off_an_energy_row_is_read_by_its_clock import (
        VARIABLE,
        _checkpoint,
        _piece,
        _record,
        _state,
    )

    root = _a_variable_step_study_stopped_in_its_extension(tmp_path)
    # Steps times the timestep, as the plan counts them: 2,500 x 2 fs.
    monkeypatch.setattr(resume, "extension_of", lambda study, **_: resume.Continuation(
        parent=str(study), checkpoint="", production_done_ns=0.005,
        production_planned_ns=0.012, config={}))

    def carried_on(study, **_):
        # The rest, 3,500 steps, 5.749 ps by its clock.
        last = _piece(Path(study), "segment-002")
        _record(last, {**VARIABLE, "duration_ns": 0.007, "nvt_steps": 0, "npt_steps": 0},
                {"nvt_steps": 0, "npt_steps": 0, "production_steps": 3500})
        _state(last, 3500, 5.749152079363507)
        _checkpoint(last, 3500, finished=True, time_ps=5.749152079363507)
        return {"ok": True, "segment": str(last)}

    monkeypatch.setattr(resume, "extend_study", carried_on)
    assert main(["resume", str(root), "--json"]) == 0
    answer = json.loads(capfd.readouterr().out)
    assert answer["did"] == "continued"
    # 3.208 + 4.123 ps before; and 5.749 ps more after.
    assert answer["production_done_before_ns"] == pytest.approx(0.007331545, abs=1e-9)
    assert answer["production_done_ns"] == pytest.approx(0.013080697, abs=1e-9)
    assert answer["production_done_before_source"] == answer["production_done_source"]
    assert (answer["production_done_ns"] - answer["production_done_before_ns"]
            == pytest.approx(0.005749152, abs=1e-9))


class _Ran:
    """What a resume runs, stood in for: it ends well."""

    def __init__(self, *, config_data, output_dir):
        self.output = output_dir

    def explore(self, force=False):
        from types import SimpleNamespace

        return [SimpleNamespace(status="ok", message="")]


@pytest.mark.parametrize("did", ["nothing", "analysed", "restarted"])
def test_every_answer_gives_the_production_done(tmp_path, monkeypatch, did) -> None:
    import fastmdxplora
    from fastmdxplora.simulation import resume

    study = tmp_path / "s"
    study.mkdir()
    (study / "resolved_config.yml").write_text(yaml.safe_dump({
        "systems": [{"id": "tri", "system": "tri.pdb"}],
        "simulation": {"duration_ns": 0.001, "timestep_fs": 2.0}}), encoding="utf-8")
    if did == "nothing":
        (study / "manifest.json").write_text(json.dumps({"phases": [
            {"name": p, "status": "ok"}
            for p in ("setup", "simulation", "analysis", "report")]}), encoding="utf-8")
    monkeypatch.setattr(fastmdxplora, "FastMDXplora", _Ran)
    done = {"analysed": 0.001, "restarted": 0.0}.get(did, 0.001)
    monkeypatch.setattr(resume, "extension_of", lambda study, **_: resume.Continuation(
        parent=str(study), checkpoint="", production_done_ns=done,
        production_planned_ns=0.001, config={}, refusal="no checkpoint to carry it on"))
    answer = resume.resume_study(study)
    assert (answer["ok"], answer["did"]) == (True, did)
    assert "production_done_ns" in answer and "production_done_source" in answer
