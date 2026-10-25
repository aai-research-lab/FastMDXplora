"""A study run from Python is not running once its run has ended.

The record of which process runs a study was removed when the process
exited. Run from a notebook or a script that went on, a finished study kept
it for as long as Python stayed open; on Linux, where the process's start is
matched, a GUI opened on the folder took the notebook for the run. It showed
the study as running, refused every fix as "A FastMDXplora workflow is
already running.", and Stop would have signalled the notebook. Found by
`test_the_agent_runs_windows_again.py::test_the_route_runs_it`, which failed
on every Linux job and passed on macOS, where a command line is matched.
"""

from __future__ import annotations

import os

from fastmdxplora import orchestrator
from fastmdxplora.batch import explorer as explorer_module
from fastmdxplora.batch.explorer import BatchExplorer
from fastmdxplora.orchestrator import RUN_PROCESS_FILE
from tests.test_a_window_runs_again_in_place import _config, _stand_in

PDB = ("ATOM      1  N   GLY A   1       0.000   0.000   0.000  1.00  0.00           N\n"
       "ATOM      2  CA  GLY A   1       1.450   0.000   0.000  1.00  0.00           C\n"
       "END\n")


def test_a_study_of_several_runs_leaves_no_record_once_they_end(tmp_path, monkeypatch):
    seen = []

    def run(*args, **kwargs):
        seen.append((out / RUN_PROCESS_FILE).is_file())
        return stand_in(*args, **kwargs)

    stand_in = _stand_in([])
    monkeypatch.setattr(explorer_module, "_execute_run", run)
    monkeypatch.setattr(BatchExplorer, "_maybe_build_comparison", lambda self: None)
    out = tmp_path / "out"
    BatchExplorer(config=_config(tmp_path, [2000] * 5), output_dir=str(out)).run()
    # Recorded while the runs ran, and gone once they had.
    assert seen and all(seen)
    assert not (out / RUN_PROCESS_FILE).exists()


def test_a_study_is_recorded_only_while_a_call_runs_it(tmp_path, monkeypatch):
    structure = tmp_path / "gly.pdb"
    structure.write_text(PDB, encoding="utf-8")
    folder = tmp_path / "study"
    study = orchestrator.FastMDXplora(system=str(structure), output_dir=str(folder))
    assert not (folder / RUN_PROCESS_FILE).exists()

    seen = []

    def phase(self, name, settings):
        record = folder / RUN_PROCESS_FILE
        seen.append(record.is_file() and f'"pid": {os.getpid()}' in record.read_text())
        # A call inside the call records nothing of its own, and leaves the
        # outer call's record where it is.
        if name == "setup":
            self.analyze()
            seen.append(record.is_file())
        return None

    monkeypatch.setattr(orchestrator.FastMDXplora, "_run_phase", phase)
    study.setup()
    assert seen == [True, True, True]
    assert not (folder / RUN_PROCESS_FILE).exists()
