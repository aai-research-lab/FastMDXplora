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
    answer = json.loads(capfd.readouterr().out.strip().splitlines()[-1])
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


@needs_backends
def test_a_study_stopped_after_its_join_by_an_older_version_is_analysed(extended) -> None:
    from fastmdxplora.simulation.resume import resume_study

    study, pristine = extended
    _put_back(study, pristine)
    patches = _points()["joined.json"]()
    try:
        for patch in patches:
            patch.start()
        with pytest.raises(_Killed):
            _join_the_extension(study)
    finally:
        for patch in patches:
            patch.stop()
    # A version before the marker wrote none.
    (study / "joined" / "analysis_owed.json").unlink()

    answer = resume_study(study)
    # It was read as finished: "nothing", its analysis of the first piece.
    assert (answer["ok"], answer["did"]) == (True, "analysed"), answer
    analysed = json.loads((study / "analysis" / "analysis_manifest.json").read_text())
    assert Path(analysed["trajectory_input"]) == study / "joined" / "production.dcd"
    assert analysed["n_frames"] == 6


@needs_backends
def test_its_join_analysed_again_by_hand_owes_nothing(extended, monkeypatch, capfd) -> None:
    from types import SimpleNamespace

    import fastmdxplora
    from fastmdxplora.cli.main import main
    from fastmdxplora.simulation.resume import resume_study

    study, pristine = extended
    _put_back(study, pristine)
    # The join's analysis ends with an answer: the marker stays.
    with mock.patch.object(fastmdxplora.FastMDXplora, "explore",
                           lambda self, **_: [SimpleNamespace(status="error",
                                                              message="stood in")]):
        assert _join_the_extension(study)["ok"] is False
    assert (study / "joined" / "analysis_owed.json").is_file()

    # Analysed again by hand, from beside the study, by its name alone.
    monkeypatch.chdir(study.parent)
    capfd.readouterr()
    assert main(["analyze", "--output", study.name, "--rerun"]) == 0
    printed = "".join(capfd.readouterr())
    assert not (study / "joined" / "analysis_owed.json").exists()
    assert "so saved in previous/report" in printed
    config = _config(study)
    assert config["output"] == str(study)
    assert config["analysis"]["trajectory"] == str(study / "joined" / "production.dcd")
    # Its report written again as the line says, it is finished: the marker
    # left, every resume joined and analysed it again.
    assert main(["report", "--output", study.name]) == 0
    assert resume_study(study)["did"] == "nothing"


def _the_person_s_analysis(study: Path) -> None:
    """The person's own analysis: the first piece alone, every 2nd frame."""
    from fastmdxplora.cli.main import main

    first = study / "simulation" / "production.dcd"
    assert main(["analyze", "--output", str(study), "--rerun", "--trajectory", str(first),
                 "--stride", "2"]) == 0


def _saved(study: Path) -> dict:
    return json.loads((study / "previous" / "analysis" / "analysis_manifest.json").read_text())


@needs_backends
def test_an_analysis_the_person_chose_is_neither_owed_nor_lost(extended, capfd) -> None:
    from fastmdxplora.cli.main import main
    from fastmdxplora.gui.telemetry import status_as_it_stands
    from fastmdxplora.remedies import _interrupted
    from fastmdxplora.simulation.resume import analysis_owed, resume_study

    study, pristine = extended
    _put_back(study, pristine)
    assert _join_the_extension(study)["ok"] is True
    assert not (study / "joined" / "analysis_owed.json").exists()
    _the_person_s_analysis(study)
    # Chosen, not owed: it was said interrupted, "Its analysis is owed".
    assert not analysis_owed(study)
    assert status_as_it_stands(study)["status"] == "completed"
    assert not (study / "report").exists()
    # Its report moved aside by the rerun, a resume writes the report of the
    # person's analysis, as the GUI's Completed says: the join's analyses
    # were run over it.
    capfd.readouterr()
    assert main(["resume", str(study)]) == 0
    printed = capfd.readouterr().out
    assert "its report was written over it" in printed, printed
    assert "saved in previous" not in printed
    analysed = json.loads((study / "analysis" / "analysis_manifest.json").read_text())
    assert analysed["load_kwargs"]["stride"] == 2
    assert Path(analysed["trajectory_input"]) == study / "simulation" / "production.dcd"
    assert (study / "report" / "report.md").is_file()
    # The join's, saved by the person's rerun, is where it was.
    assert Path(_saved(study)["trajectory_input"]) == study / "joined" / "production.dcd"
    assert _config(study)["analysis"]["trajectory"] == str(study / "simulation" / "production.dcd")
    # The GUI and resume agree: completed, nothing to fix, nothing to do.
    assert status_as_it_stands(study)["status"] == "completed"
    assert _interrupted(study) is None
    assert resume_study(study)["did"] == "nothing"


@needs_backends
def test_an_analysis_chosen_while_owed_is_saved_by_the_resume(extended, capfd) -> None:
    from types import SimpleNamespace

    import fastmdxplora
    from fastmdxplora.simulation.resume import analysis_owed, resume_study

    study, pristine = extended
    _put_back(study, pristine)
    with mock.patch.object(fastmdxplora.FastMDXplora, "explore",
                           lambda self, **_: [SimpleNamespace(status="error",
                                                              message="stood in")]):
        assert _join_the_extension(study)["ok"] is False
    _the_person_s_analysis(study)
    # The join's analysis has not run: owed by its marker.
    assert analysis_owed(study)
    answer = resume_study(study)
    assert (answer["ok"], answer["did"]) == (True, "analysed"), answer
    analysed = json.loads((study / "analysis" / "analysis_manifest.json").read_text())
    assert Path(analysed["trajectory_input"]) == study / "joined" / "production.dcd"
    assert _saved(study)["load_kwargs"]["stride"] == 2
    assert answer["saved_in_previous"] == ["analysis"]


@needs_backends
def test_resume_says_which_of_its_phases_it_saved(extended, capfd) -> None:
    from types import SimpleNamespace

    import fastmdxplora
    from fastmdxplora.cli.main import main

    study, pristine = extended
    _put_back(study, pristine)
    with mock.patch.object(fastmdxplora.FastMDXplora, "explore",
                           lambda self, **_: [SimpleNamespace(status="error",
                                                              message="stood in")]):
        assert _join_the_extension(study)["ok"] is False
    # The person's rerun moved the report aside: only the analysis is saved.
    _the_person_s_analysis(study)
    assert not (study / "report").exists()
    capfd.readouterr()
    assert main(["resume", str(study)]) == 0
    printed = capfd.readouterr().out
    assert "Its analysis from before is saved in previous/analysis." in printed, printed
    assert "report it had before" not in printed


def _stopped_in_its_analyses(study: Path, *, watched: bool = False) -> str:
    """`fastmdx resume` of ``study`` in a process of its own, killed once its
    analyses have written their folders and before their manifest, as a
    wall time lands; what it printed. ``watched``: run as the GUI's Run it
    runs it, its record of the study written as it goes."""
    import os
    import signal
    import subprocess
    import sys

    script = (
        "import sys, time\n"
        "from fastmdxplora.analysis.orchestrator import AnalysisOrchestrator\n"
        "def stopped(self):\n"
        "    print('analyses written', flush=True)\n"
        "    time.sleep(600)\n"
        "AnalysisOrchestrator._write_manifest = stopped\n"
        "from fastmdxplora.cli.main import main\n"
        "sys.exit(main(['resume', sys.argv[1]]))\n")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        [str(Path(__file__).resolve().parents[1] / "src"), os.environ.get("PYTHONPATH", "")])}
    if watched:
        env.update(FASTMDX_DASHBOARD_ACTIVE="1", FASTMDX_DASHBOARD_OUTPUT=str(study))
    process = subprocess.Popen([sys.executable, "-c", script, str(study)], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines = []
    try:
        for line in process.stdout:
            lines.append(line)
            if line.strip() == "analyses written":
                break
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGKILL)
        process.wait(timeout=60)
    printed = "".join(lines)
    assert printed.rstrip().endswith("analyses written"), printed
    return printed


@needs_backends
def test_a_resume_stopped_in_its_analyses_and_run_again_leaves_the_person_s_saved(
        extended, capfd) -> None:
    from types import SimpleNamespace

    import fastmdxplora
    from fastmdxplora.cli.main import main

    study, pristine = extended
    _put_back(study, pristine)
    with mock.patch.object(fastmdxplora.FastMDXplora, "explore",
                           lambda self, **_: [SimpleNamespace(status="error",
                                                              message="stood in")]):
        assert _join_the_extension(study)["ok"] is False
    _the_person_s_analysis(study)

    # The first resume saves the person's analysis, and is killed in its own.
    printed = _stopped_in_its_analyses(study)
    assert "saved in previous/analysis" in printed, printed
    assert (study / "analysis" / "rmsd").is_dir()
    assert not (study / "analysis" / "analysis_manifest.json").exists()
    assert _saved(study)["load_kwargs"]["stride"] == 2

    # Run again, it finishes; the unfinished analysis is not saved over the
    # person's, and the answer says nothing was saved this time.
    capfd.readouterr()
    assert main(["resume", str(study)]) == 0
    printed = capfd.readouterr().out
    assert (study / "previous" / "analysis" / "analysis_manifest.json").is_file()
    assert _saved(study)["load_kwargs"]["stride"] == 2
    assert Path(_saved(study)["trajectory_input"]) == study / "simulation" / "production.dcd"
    assert "saved in previous" not in printed, printed
    analysed = json.loads((study / "analysis" / "analysis_manifest.json").read_text())
    assert Path(analysed["trajectory_input"]) == study / "joined" / "production.dcd"


@needs_backends
def test_analysed_again_after_a_stopped_resume_it_leaves_the_person_s_saved(
        extended, capfd) -> None:
    from types import SimpleNamespace

    import fastmdxplora
    from fastmdxplora.cli.main import main

    study, pristine = extended
    _put_back(study, pristine)
    with mock.patch.object(fastmdxplora.FastMDXplora, "explore",
                           lambda self, **_: [SimpleNamespace(status="error",
                                                              message="stood in")]):
        assert _join_the_extension(study)["ok"] is False
    _the_person_s_analysis(study)
    _stopped_in_its_analyses(study)
    assert not (study / "analysis" / "analysis_manifest.json").exists()

    # `fastmdx analyze --rerun`, as the GUI's Analyze again runs it: the
    # unfinished analysis is not saved over the person's finished one.
    capfd.readouterr()
    assert main(["analyze", "--output", str(study), "--rerun"]) == 0
    printed = capfd.readouterr().out
    assert (study / "previous" / "analysis" / "analysis_manifest.json").is_file()
    assert _saved(study)["load_kwargs"]["stride"] == 2
    assert Path(_saved(study)["trajectory_input"]) == study / "simulation" / "production.dcd"
    assert (study / "analysis" / "analysis_manifest.json").is_file()
    assert "removed, as it did not finish" in printed, printed


@needs_backends
def test_a_resume_stopped_from_the_gui_in_its_analyses_offers_resume(extended) -> None:
    from fastmdxplora.gui.exploration import _the_stop_recorded
    from fastmdxplora.gui.fixes_view import fixes_payload
    from fastmdxplora.gui.telemetry import ANALYSIS_OWED, analyze_health, status_as_it_stands

    study, pristine = extended
    _put_back(study, pristine)
    # Run it, as the GUI runs it, and Stop the run while it analyses the join.
    _stopped_in_its_analyses(study, watched=True)
    assert status_as_it_stands(study)["stage"] == "analysis"
    _the_stop_recorded(study)
    status = status_as_it_stands(study)
    # Owed, with `fastmdx resume` to run: it read Stopped, with no fix.
    assert (status["status"], status.get("ended_by")) == ("interrupted", ANALYSIS_OWED), status
    assert analyze_health(status, [], root=study)["headline"] == "Its analysis is owed"
    fixes = fixes_payload(study)["fixes"]
    assert [fix["argv"] for fix in fixes] == [["resume", str(study)]], fixes
    assert fixes[0]["runnable"] is True


@needs_backends
def test_a_resume_stopped_from_the_gui_and_carried_on_reads_completed(extended) -> None:
    from fastmdxplora.gui.exploration import _the_stop_recorded
    from fastmdxplora.gui.telemetry import analyze_health, status_as_it_stands
    from fastmdxplora.simulation.resume import resume_study

    study, pristine = extended
    _put_back(study, pristine)
    _stopped_in_its_analyses(study, watched=True)
    _the_stop_recorded(study)
    live = study / "simulation" / "live_status.json"
    assert json.loads(live.read_text(encoding="utf-8"))["status"] == "stopped"
    # Carried on from the command line, it reads as it ended: completed. It
    # read "Stopped here in the analysis, as asked" after the analyses ran.
    assert resume_study(study)["ok"] is True
    status = status_as_it_stands(study)
    assert (status["status"], status.get("latest_error")) == ("completed", None), status
    assert analyze_health(status, [], root=study).get("headline") != "Stopped"


def _an_older_analyze_s_lengths(study: Path) -> None:
    """The step count of the study's first piece beside its whole duration,
    as `fastmdx analyze --rerun --trajectory` by an earlier version wrote
    them in the study's config over an extended study."""
    config = _config(study)
    assert config["simulation"].get("production_steps") is None
    config["simulation"]["production_steps"] = 200
    (study / "resolved_config.yml").write_text(yaml.safe_dump(config, sort_keys=False),
                                               encoding="utf-8")


@needs_backends
def test_the_report_of_an_analysis_an_older_version_chose_is_written(
        extended, capfd) -> None:
    from fastmdxplora.cli.main import main
    from fastmdxplora.simulation.resume import analysis_owed

    study, pristine = extended
    _put_back(study, pristine)
    assert _join_the_extension(study)["ok"] is True
    _the_person_s_analysis(study)
    _an_older_analyze_s_lengths(study)
    capfd.readouterr()
    # It crashed: a ConfigError's traceback, nothing on stdout under --json.
    assert main(["resume", "--json", str(study)]) == 0
    answer = json.loads(capfd.readouterr().out.strip().splitlines()[-1])
    assert (answer["ok"], answer["did"]) == (True, "reported"), answer
    assert (study / "report" / "report.md").is_file()
    assert _config(study)["simulation"].get("production_steps") is None
    assert _config(study)["analysis"]["trajectory"] == str(study / "simulation" / "production.dcd")
    analysed = json.loads((study / "analysis" / "analysis_manifest.json").read_text())
    assert analysed["load_kwargs"]["stride"] == 2
    assert not analysis_owed(study)


@needs_backends
def test_a_report_the_config_refuses_is_answered_not_raised(extended, capfd) -> None:
    from fastmdxplora.cli.main import main

    study, pristine = extended
    _put_back(study, pristine)
    assert _join_the_extension(study)["ok"] is True
    _the_person_s_analysis(study)
    config = _config(study)
    # Edited by hand since, as the config's check refuses it.
    config["report"]["pdf"] = "sometimes"
    (study / "resolved_config.yml").write_text(yaml.safe_dump(config, sort_keys=False),
                                               encoding="utf-8")
    capfd.readouterr()
    assert main(["resume", "--json", str(study)]) == 1
    answer = json.loads(capfd.readouterr().out.strip().splitlines()[-1])
    assert (answer["ok"], answer.get("stage")) == (False, "report"), answer
    assert answer["refusal"]["code"] and "'pdf'" in answer["error"], answer


@needs_backends
def test_the_report_only_answer_says_it_reported(extended, capfd) -> None:
    from fastmdxplora.cli.main import main

    study, pristine = extended
    _put_back(study, pristine)
    assert _join_the_extension(study)["ok"] is True
    _the_person_s_analysis(study)
    capfd.readouterr()
    assert main(["resume", "--json", str(study)]) == 0
    answer = json.loads(capfd.readouterr().out.strip().splitlines()[-1])
    # Read "analysed" (the analyses and report run) where only the report was.
    assert answer["did"] == "reported", answer
    assert "fastmdx analyze --rerun --trajectory" in answer["detail"], answer


def _copied_child_by_child(study: Path, to: Path, first: list[str]) -> Path:
    """``study`` copied by `cp -r`, child by child, ``first`` first: the
    files' times are of the copy, in its order, as `scp -r` or a download
    without them leaves them."""
    import os
    import subprocess
    import time

    to.mkdir()
    rest = sorted(name for name in os.listdir(study) if name not in first)
    for name in [*first, *rest]:
        subprocess.run(["cp", "-r", str(study / name), str(to / name)], check=True)
        time.sleep(0.02)
    return to


def _written_later(later: Path, earlier: Path) -> bool:
    return later.stat().st_mtime_ns > earlier.stat().st_mtime_ns


@needs_backends
def test_a_copy_of_an_analysis_chosen_after_the_join_is_not_owed(extended) -> None:
    from fastmdxplora.gui.telemetry import status_as_it_stands
    from fastmdxplora.simulation.resume import analysis_owed

    study, pristine = extended
    copies = Path(tempfile.mkdtemp())
    try:
        # The person's analysis of the first piece, chosen after the join;
        # copied analysis first, so its file is older than the join's.
        _put_back(study, pristine)
        assert _join_the_extension(study)["ok"] is True
        _the_person_s_analysis(study)
        assert not analysis_owed(study)
        chosen = _copied_child_by_child(study, copies / "chosen", ["analysis", "joined"])
        assert _written_later(chosen / "joined" / "joined.json",
                              chosen / "analysis" / "analysis_manifest.json")
        assert not analysis_owed(chosen)
        assert status_as_it_stands(chosen)["status"] == "completed"
    finally:
        shutil.rmtree(copies, ignore_errors=True)


@needs_backends
def test_a_copy_of_an_analysis_from_before_the_join_is_owed(extended) -> None:
    from fastmdxplora.gui.telemetry import status_as_it_stands
    from fastmdxplora.simulation.resume import analysis_owed

    study, pristine = extended
    copies = Path(tempfile.mkdtemp())
    try:
        # Stopped after its join by a version that wrote no marker and no
        # time in the join's record, its config naming its first piece as
        # that version's first run wrote it: the analysis of the first piece
        # is owed. Copied join first, so its analysis's file is the newer.
        _put_back(study, pristine)
        patches = _points()["joined.json"]()
        try:
            for patch in patches:
                patch.start()
            with pytest.raises(_Killed):
                _join_the_extension(study)
        finally:
            for patch in patches:
                patch.stop()
        (study / "joined" / "analysis_owed.json").unlink()
        joined = study / "joined" / "joined.json"
        record = json.loads(joined.read_text())
        joined.write_text(json.dumps({k: v for k, v in record.items() if k != "joined_at"}))
        config = _config(study)
        config["analysis"] = {**(config.get("analysis") or {}),
                              "trajectory": str(study / "simulation" / "production.dcd")}
        (study / "resolved_config.yml").write_text(yaml.safe_dump(config), encoding="utf-8")
        assert analysis_owed(study)
        older = _copied_child_by_child(study, copies / "older", ["joined", "analysis"])
        assert _written_later(older / "analysis" / "analysis_manifest.json",
                              older / "joined" / "joined.json")
        assert analysis_owed(older)
        assert status_as_it_stands(older)["status"] == "interrupted"
    finally:
        shutil.rmtree(copies, ignore_errors=True)


def test_a_continued_answer_plans_its_extension(tmp_path, monkeypatch) -> None:
    from fastmdxplora.simulation import resume

    study = tmp_path / "s"
    study.mkdir()
    (study / "resolved_config.yml").write_text(yaml.safe_dump({
        "systems": [{"id": "tri", "system": "tri.pdb"}],
        "simulation": {"duration_ns": 0.006, "timestep_fs": 2.0}}), encoding="utf-8")
    monkeypatch.setattr(resume, "_finished_record", lambda root, config: (False, None))
    monkeypatch.setattr(resume, "survey_segments", lambda root: [], raising=False)
    # An extension of 0.002 ns stopped at its checkpoint, 0.0008 ns in: 0.0012 owed.
    monkeypatch.setattr(resume, "_owed_by_the_last_piece", lambda root: 0.0012)
    monkeypatch.setattr(resume, "extension_of", lambda study, **_: resume.Continuation(
        parent=str(study), checkpoint="", production_done_ns=0.0068,
        production_planned_ns=0.006, config={}))
    monkeypatch.setattr(resume, "extend_study", lambda study, **_: {"ok": True})
    answer = resume.resume_study(study)
    assert answer["did"] == "continued"
    # The study's plan before the extension was 0.006, beside 0.008 done.
    assert answer["production_planned_ns"] == pytest.approx(0.008, abs=1e-9)


def test_each_run_of_a_study_of_several_gives_its_production(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    import fastmdxplora.batch.explorer as explorer
    from fastmdxplora.simulation import resume

    study = tmp_path / "seeds"
    runs = []
    for name, ns in (("seed-1", 0.04), ("seed-2", 0.03)):
        run = study / "runs" / name / "simulation"
        run.mkdir(parents=True)
        (run / "simulation_parameters.json").write_text(json.dumps({
            "parameters": {"timestep_fs": 2.0, "duration_ns": ns},
            "duration_ns_actual": ns}), encoding="utf-8")
        runs.append(SimpleNamespace(run_id=name, status="ok", message="finished",
                                    output_dir=run.parent))
    (study / "resolved_config.yml").write_text("systems: []\n", encoding="utf-8")

    class Batch:
        def __init__(self, **_):
            pass

        def run(self):
            return runs

    monkeypatch.setattr(explorer, "BatchExplorer", Batch)
    answer = resume.resume_batch(study)
    assert answer["did"] == "runs"
    assert [run.get("production_done_ns") for run in answer["runs"]] == [0.04, 0.03]
    assert {run["production_done_source"] for run in answer["runs"]} == {"recorded"}


def test_a_copied_study_s_config_names_no_frames_of_the_original(tmp_path, monkeypatch) -> None:
    from fastmdxplora.simulation import resume

    original = tmp_path / "full"
    study = tmp_path / "copy"
    study.mkdir()
    kept = yaml.safe_dump({
        "output": str(original),
        "simulation": {"duration_ns": 0.006, "timestep_fs": 2.0},
        "analysis": {"include": ["rmsd"],
                     "trajectory": str(original / "simulation" / "production.dcd"),
                     "topology": str(original / "simulation" / "trajectory_topology.pdb")}})
    monkeypatch.setattr(resume, "production_done_ns", lambda root: 0.008)
    resume._keep_the_study_s_config(study, kept)
    analysis = _config(study)["analysis"]
    # The copy's analysis read the original's first piece.
    assert "trajectory" not in analysis and "topology" not in analysis
    assert analysis["include"] == ["rmsd"]
