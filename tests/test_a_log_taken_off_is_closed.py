"""The equilibration log is closed when production takes over.

It is taken off the simulation when production begins, and was "closed" by
calling the reporter's close(), which OpenMM's StateDataReporter does not
have. So its file stayed open until the reporter was collected: in a study
of several runs in one process, never, one file for every run.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from fastmdxplora.simulation.runner import _close_one_reporter


class _LikeOpenMMs:
    """A reporter as OpenMM writes one: its file, and no close()."""

    def __init__(self, path: Path, *, opened_here: bool = True) -> None:
        self._out = open(path, "w", encoding="utf-8") if opened_here else sys.stdout
        self._openedFile = opened_here


def test_a_reporter_without_close_has_its_file_closed(tmp_path) -> None:
    reporter = _LikeOpenMMs(tmp_path / "equilibration_energy.csv")
    simulation = SimpleNamespace(reporters=[reporter])
    _close_one_reporter(simulation, reporter)
    assert reporter._out.closed
    assert simulation.reporters == []


def test_a_stream_it_was_handed_is_left_open(tmp_path) -> None:
    reporter = _LikeOpenMMs(tmp_path / "x.csv", opened_here=False)
    _close_one_reporter(SimpleNamespace(reporters=[reporter]), reporter)
    assert not sys.stdout.closed


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="reads /proc")
def test_a_real_run_leaves_no_log_open() -> None:
    pytest.importorskip("openmm")
    from fastmdxplora.simulation import runner
    from tests._the_phase import a_prepared_water_box

    root = Path(tempfile.mkdtemp())
    runner.run_simulation(**a_prepared_water_box(root), output_dir=str(root / "out"),
                          production_steps=10, nvt_steps=10, npt_steps=0,
                          minimize=False, platform="CPU")
    held = []
    for fd in os.listdir("/proc/self/fd"):
        try:
            held.append(os.readlink(f"/proc/self/fd/{fd}"))
        except OSError:
            continue
    assert not [path for path in held if path.startswith(str(root))], held


def test_a_close_that_fails_does_not_stop_the_run(tmp_path) -> None:
    from fastmdxplora.simulation.runner import _detach_all_reporters

    class _Refuses:
        def close(self):
            raise OSError("already gone")

    class _FileRefuses:
        _openedFile = True

        class _out:  # noqa: N801 - a stand-in for a file object
            @staticmethod
            def close():
                raise OSError("disk went away")

    simulation = SimpleNamespace(reporters=[_Refuses(), _FileRefuses()])
    _detach_all_reporters(simulation)
    assert simulation.reporters == []
