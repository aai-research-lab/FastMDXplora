"""A platform that is not there is worth a sentence.

OpenMM skips a platform it cannot find, and the skip is silent. On a cluster
with two L40S, it offered `Reference` alone and reported no load failures --
because with `OPENMM_PLUGIN_DIR` unset it had not looked, so there was nothing
to fail. A run would have finished, correct and about a hundred times slower,
with nothing anywhere saying why.

That is the same shape as the defects this software has had: an answer that
looks like an answer. The remedy is the same too -- say what was found, and
where something is missing, say what is known about why.
"""

from __future__ import annotations

import logging

import pytest


from fastmdxplora.simulation.runner import _say_what_is_available


class _Platform:
    def __init__(self, names, failures):
        self._names, self._failures = names, failures

    def getNumPlatforms(self):
        return len(self._names)

    def getPlatform(self, index):
        name = self._names[index]
        return type("P", (), {"getName": lambda self, n=name: n})()

    def getPluginLoadFailures(self):
        return self._failures


def _openmm(names, failures=()):
    return {"openmm": type("MM", (), {"Platform": _Platform(names, list(failures))})()}


WANTED = ["CUDA", "OpenCL", "CPU"]


class TestItNamesWhatWasFound:
    def test_the_available_platforms_are_listed(self, caplog) -> None:
        with caplog.at_level(logging.INFO):
            _say_what_is_available(_openmm(["Reference", "CPU", "CUDA"]), WANTED)
        assert "Reference, CPU, CUDA" in caplog.text

    def test_nothing_more_is_said_when_nothing_is_missing(self, caplog) -> None:
        with caplog.at_level(logging.INFO):
            _say_what_is_available(_openmm(["Reference", "CPU", "CUDA"]), WANTED)
        assert "did not load" not in caplog.text
        assert "OPENMM_PLUGIN_DIR" not in caplog.text


class TestItSaysWhyOneIsMissing:
    def test_a_load_failure_is_repeated_verbatim(self, caplog) -> None:
        """The line that ends the investigation: on the cluster this was
        `libcuda.so.1: cannot open shared object file`, which says the driver
        is absent -- correct on a head node, and the whole answer."""
        failure = ("Error loading library /opt/.../libOpenMMCUDA.so: "
                   "libcuda.so.1: cannot open shared object file")
        with caplog.at_level(logging.INFO):
            _say_what_is_available(_openmm(["Reference", "CPU"], [failure]), WANTED)
        assert "libcuda.so.1" in caplog.text

    def test_no_failures_and_no_cpu_means_nothing_was_tried(self, caplog) -> None:
        """The case that cost an hour. An absent CPU platform with no
        reported failure is not a missing library -- it is a directory that
        was never read."""
        with caplog.at_level(logging.INFO):
            _say_what_is_available(_openmm(["Reference"]), WANTED)
        assert "OPENMM_PLUGIN_DIR" in caplog.text
        assert "none was tried" in caplog.text

    def test_a_gpu_absent_on_its_own_says_nothing_about_the_path(
            self, caplog) -> None:
        """CPU present and CUDA absent on a machine with no GPU is ordinary.
        Suggesting a plugin path there would send somebody after nothing."""
        with caplog.at_level(logging.INFO):
            _say_what_is_available(_openmm(["Reference", "CPU"]), WANTED)
        assert "OPENMM_PLUGIN_DIR" not in caplog.text


class TestItNeverStopsARunItCannotDescribe:
    def test_no_openmm_is_not_an_error(self) -> None:
        _say_what_is_available({}, WANTED)

    def test_a_toolkit_that_will_not_enumerate_is_not_an_error(self) -> None:
        class _Broken:
            @property
            def Platform(self):
                raise RuntimeError("no")

        _say_what_is_available({"openmm": _Broken()}, WANTED)

    def test_it_runs_before_a_platform_is_chosen(self, caplog) -> None:
        """So the listing appears whether or not the selection then
        succeeds -- a run that finds nothing usable is exactly the one that
        needs it. Nothing usable here, and the listing is said all the same."""
        from fastmdxplora.refusals import BackendUnavailable
        from fastmdxplora.simulation.runner import select_platform

        with caplog.at_level(logging.INFO), pytest.raises(BackendUnavailable):
            select_platform(_openmm(["Reference"]), requested="auto")
        assert "OpenMM platforms available: Reference" in caplog.text

