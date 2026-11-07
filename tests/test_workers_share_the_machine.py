"""Parallel runs divide the machine rather than fight over it.

OpenMM's CPU platform takes every core it can see, and a pool of workers each
doing that oversubscribes by the worker count. Six runs on eight cores is six
times the threads the machine has, and the study finishes slower than three
would have -- so `workers` could not be used well on CPU at all, which is an
odd state for a setting the software offers.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from fastmdxplora.batch.explorer import (
    _give_this_worker_its_share, _threads_for_each)


class TestTheCoresAreDividedUp:
    @pytest.mark.parametrize("cores,workers,expected", [
        (8, 1, 8), (8, 2, 4), (8, 4, 2), (8, 8, 1),
        (16, 3, 5), (10, 2, 5),
    ])
    def test_each_worker_gets_its_share(self, cores, workers, expected) -> None:
        with patch.object(os, "cpu_count", return_value=cores):
            assert _threads_for_each(workers) == expected

    def test_more_workers_than_cores_still_get_one(self) -> None:
        """A worker with no threads runs nothing, and asking for more workers
        than cores is a scheduling choice rather than an error."""
        with patch.object(os, "cpu_count", return_value=4):
            assert _threads_for_each(12) == 1

    def test_no_workers_is_not_a_division_by_zero(self) -> None:
        with patch.object(os, "cpu_count", return_value=8):
            assert _threads_for_each(0) == 8

    def test_a_machine_that_will_not_say_gets_one(self) -> None:
        with patch.object(os, "cpu_count", return_value=None):
            assert _threads_for_each(4) == 1

    def test_the_shares_do_not_exceed_the_machine(self) -> None:
        """The point: together they fill it once."""
        for cores in (4, 8, 12, 16):
            with patch.object(os, "cpu_count", return_value=cores):
                for workers in range(1, cores + 1):
                    assert _threads_for_each(workers) * workers <= cores


@pytest.fixture(autouse=True)
def _this_process_is_no_pool_worker():
    """The initializer marks the process a pool worker (its parent says a
    stop's lines); called here in the test process, every test after it
    read this one as a worker and said none."""
    from fastmdxplora.simulation import runner

    was = runner._IN_A_POOL_WORKER
    yield
    runner._IN_A_POOL_WORKER = was


class TestTheShareReachesTheLibraries:
    def test_openmm_is_told(self, monkeypatch) -> None:
        monkeypatch.delenv("OPENMM_CPU_THREADS", raising=False)
        _give_this_worker_its_share(3)
        assert os.environ["OPENMM_CPU_THREADS"] == "3"

    @pytest.mark.parametrize("variable", [
        "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
    ])
    def test_the_others_underneath_are_too(self, variable, monkeypatch) -> None:
        """OpenMM is not the only thing in the process that helps itself to
        every core; the numerical libraries under MDTraj do the same."""
        monkeypatch.delenv(variable, raising=False)
        _give_this_worker_its_share(2)
        assert os.environ[variable] == "2"

    def test_it_is_set_in_the_worker_not_the_parent(self) -> None:
        """Passed as a pool initializer, because these are read once at
        import and a value set after that changes nothing."""
        from pathlib import Path
        import fastmdxplora.batch.explorer as explorer

        source = Path(explorer.__file__).read_text(encoding="utf-8")
        assert "initializer=_give_this_worker_its_share" in source
        assert "initargs=(threads_each,)" in source
