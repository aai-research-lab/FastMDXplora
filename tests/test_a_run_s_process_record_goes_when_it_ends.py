"""A run's process record goes when the run ends, not when its process does.

The record (`.fastmdxplora_run.json`) names the process a study runs in, so
a GUI opened on the folder finds the run going and can stop it. It was
removed only when the process exited, so a run made in a process that went
on (a notebook, a test, `fastmdx run --dashboard` whose GUI stays up) left
a record naming that process: a GUI opened in it on the study took itself
for the study's run, said it was running, and refused to start another
("A FastMDXplora workflow is already running."). It is removed when the run
ends, however it ends, where this process wrote it; a record of another
process is left alone; and while the run goes, a GUI in its process still
finds it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fastmdxplora.batch import explorer as explorer_module
from fastmdxplora.batch.explorer import BatchExplorer
from fastmdxplora.gui.exploration import DashboardRuntime
from fastmdxplora.orchestrator import (
    RUN_PROCESS_FILE,
    FastMDXplora,
    PhaseResult,
    RunResult,
    _record_run_process,
    process_start_ticks,
    this_machine,
)
from fastmdxplora.refusals import RunStopped, StudyError

pytestmark = pytest.mark.skipif(not Path("/proc/self/stat").is_file(),
                                reason="a process's start is read from /proc")


def _runtime(root: Path) -> DashboardRuntime:
    """A GUI opened in this process on the study."""
    return DashboardRuntime(workspace_root=root.parent, exploration_root=root.parent,
                            active_root=root)


def _records_of_this_process(root: Path) -> list[str]:
    found = []
    for record in sorted(root.rglob(RUN_PROCESS_FILE)):
        if json.loads(record.read_text(encoding="utf-8")).get("pid") == os.getpid():
            found.append(str(record.relative_to(root)))
    return found


@pytest.fixture
def another():
    """A live process of someone else's, as a run started elsewhere is."""
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        yield process
    finally:
        process.kill()
        process.wait()


def _its_record(process) -> dict:
    return {"pid": process.pid, "argv": ["fastmdx", "explore"],
            "started_at": "2026-10-27T00:00:00+00:00", **this_machine(),
            "start_ticks": process_start_ticks(process.pid)}


def _phase_that(ended: str, seen: list):
    def phase(self, name, options):
        # While it runs: the record names this process, and a GUI opened
        # in it finds the run going, so its Stop reaches it.
        seen.append((_records_of_this_process(Path(self.output_dir)),
                     _runtime(Path(self.output_dir)).process is not None))
        if ended == "stopped":
            raise RunStopped("SIGTERM")
        if ended == "interrupted":
            raise KeyboardInterrupt
        return PhaseResult(name=name, status="error" if ended == "failed" else "ok",
                           output_dir=Path(self.output_dir) / name)
    return phase


@pytest.mark.parametrize("ended", ["finished", "failed", "stopped", "interrupted", "refused"])
def test_a_study_run_here_leaves_no_record_of_this_process(tmp_path, monkeypatch, ended):
    root = tmp_path / "study"
    seen: list = []
    monkeypatch.setattr(FastMDXplora, "_run_phase", _phase_that(ended, seen))
    if ended == "refused":
        def refuse(self, plan, *, force):
            seen.append((_records_of_this_process(Path(self.output_dir)), None))
            raise StudyError("refused", code="environment.workspace.run_going")
        monkeypatch.setattr(FastMDXplora, "_refuse_to_overwrite", refuse)
    study = FastMDXplora("1L2Y", output_dir=str(root))
    try:
        study.explore(include_phase=["analysis"])
    except (StudyError, RunStopped, KeyboardInterrupt):
        pass
    assert seen and seen[0][0] == [RUN_PROCESS_FILE]
    if ended != "refused":
        assert seen[0][1] is True
    assert _records_of_this_process(root) == []
    assert _runtime(root).process is None


def test_a_study_run_twice_here_is_found_running_both_times(tmp_path, monkeypatch):
    root = tmp_path / "study"
    seen: list = []
    monkeypatch.setattr(FastMDXplora, "_run_phase", _phase_that("finished", seen))
    monkeypatch.setattr(FastMDXplora, "_refuse_to_overwrite", lambda *a, **k: None)
    study = FastMDXplora("1L2Y", output_dir=str(root))
    study.explore(include_phase=["analysis"])
    study.analyze()
    assert seen == [([RUN_PROCESS_FILE], True)] * 2
    assert _records_of_this_process(root) == []


def _sweep(tmp_path: Path) -> BatchExplorer:
    return BatchExplorer(config_data={"systems": [{"system": "1UBQ"}],
                                      "sweep": {"simulation.temperature_K": [300, 310, 320]},
                                      "report": {"comparison": False}},
                         output_dir=str(tmp_path / "sweep"))


@pytest.mark.parametrize("ended", ["finished", "failed", "stopped"])
def test_a_study_of_runs_run_here_leaves_no_record_of_this_process(tmp_path, monkeypatch,
                                                                     another, ended):
    """Each run made in this process wrote its record, and the study its
    own; none is left. The second run's record names another process (a
    worker's, or a run started there since), and is."""
    study = _sweep(tmp_path)
    root = study.output_dir
    seen: list = []

    def run(spec, run_out, *args, **kwargs):
        out = Path(run_out)
        out.mkdir(parents=True, exist_ok=True)
        seen.append(_runtime(root).process is not None)
        if spec["run_id"] == study.run_specs[1].run_id:
            (out / RUN_PROCESS_FILE).write_text(json.dumps(_its_record(another)),
                                                encoding="utf-8")
            if ended == "stopped":
                raise RunStopped("SIGTERM")
            if ended == "failed":
                raise RuntimeError("the run failed")
        else:
            # As a run made in this process records itself, and left it.
            _record_run_process(out)
        return RunResult(run_id=spec["run_id"], system=spec["system"], status="ok",
                         output_dir=out)

    monkeypatch.setattr(explorer_module, "_execute_run", run)
    monkeypatch.setattr(BatchExplorer, "_maybe_build_comparison", lambda self: None)
    try:
        study.run()
    except (RunStopped, RuntimeError):
        pass
    assert seen and all(seen)
    assert _records_of_this_process(root) == []
    theirs = root / "runs" / study.run_specs[1].run_id / RUN_PROCESS_FILE
    assert json.loads(theirs.read_text(encoding="utf-8"))["pid"] == another.pid
    assert _runtime(root).process is None


def test_only_this_process_s_own_record_is_removed(tmp_path, another):
    from fastmdxplora.orchestrator import _forget_run_process

    def left(record: dict) -> bool:
        (tmp_path / RUN_PROCESS_FILE).write_text(json.dumps(record), encoding="utf-8")
        _forget_run_process(tmp_path)
        return (tmp_path / RUN_PROCESS_FILE).is_file()

    _record_run_process(tmp_path)
    ours = json.loads((tmp_path / RUN_PROCESS_FILE).read_text(encoding="utf-8"))
    assert not left(ours)
    # Another process's; this number given to a process before this one; a
    # record of this number written on another machine, or by hand.
    assert left(_its_record(another))
    assert left({**ours, "start_ticks": ours["start_ticks"] - 1})
    assert left({**ours, "host": ours["host"] + "-elsewhere"})
    assert left({"pid": os.getpid()})
