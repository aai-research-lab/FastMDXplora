"""The console and the GUI say a binding free energy's 95% interval, or why
there is none.

They said only the number +/- its standard error. With no interval (one
resample, no bootstrap, resamples with no spread) that was a bare number,
and the GUI still called its missing error "a standard error"; the reason,
saved as `delta_g_no_interval`, was said nowhere. A record from before the
interval was t's has no `delta_g_confidence_level`, and reads as it did.
"""

from __future__ import annotations

import json

import pytest

from fastmdxplora.batch.explorer import _finite_or_null, _what_the_binding_says
from fastmdxplora.gui import records_answer
from fastmdxplora.simulation.binding import _what_the_resamples_said

UNCORRECTED = -16.9
DELTA_G = -10.364


def record(standard_error, resamples, low=-0.57, high=0.29):
    """A binding record as `binding_free_energy` saves it, from the
    bootstrap's own `derived` entry."""
    if resamples is None:
        resampled = None
    else:
        resampled = {
            "value": UNCORRECTED, "standard_error": standard_error,
            "confidence_low": UNCORRECTED + low,
            "confidence_high": UNCORRECTED + high,
            "resamples": resamples, "resamples_with_a_number": resamples,
            "resamples_without_a_number": 0, "correlation_resolved": True,
            "note": None}
    return {"delta_g_kjmol": DELTA_G,
            **_what_the_resamples_said(resampled, DELTA_G - UNCORRECTED)}


FORTY = record(0.25635, 40)
ONE = record(float("nan"), 1, low=0.0, high=0.0)
NONE = record(None, None)
NO_SPREAD = record(3.6e-15, 40, low=0.0, high=0.0)


def gui(tmp_path, binding):
    (tmp_path / "pmf.json").write_text(
        json.dumps(_finite_or_null({"pmf": {}, "binding": binding})),
        encoding="utf-8")
    return records_answer._free_energy(tmp_path)


@pytest.mark.parametrize("binding, said", [
    (FORTY, ["Binding:        -10.4 +/- 0.3 kJ/mol (standard state, 1 M)",
             "95% interval:   -10.9 to -9.8 kJ/mol"]),
    (ONE, ["Binding:        -10.4 kJ/mol (standard state, 1 M)",
           "No interval:    1 resample gave a binding free energy, and a "
           "standard error needs at least two (`bootstrap_resamples` sets "
           "how many are taken)."]),
    (NONE, ["Binding:        -10.4 kJ/mol (standard state, 1 M)",
            "No interval:    No bootstrap was run (as with "
            "`bootstrap_resamples: 0`), so there is no standard error."]),
    (NO_SPREAD, ["Binding:        -10.4 kJ/mol (standard state, 1 M)",
                 "No interval:    Every resample gave the same binding free "
                 "energy, so there is no spread."]),
], ids=["40 resamples", "1 resample", "no bootstrap", "no spread"])
def test_the_console_says_the_interval_or_why_there_is_none(binding, said):
    assert _what_the_binding_says(binding) == said


def test_the_console_says_an_interval_at_its_own_level():
    said = _what_the_binding_says({**FORTY, "delta_g_confidence_level": 0.9})

    assert said[1].startswith("90% interval:   ")


def test_the_console_gives_no_error_that_is_not_a_number():
    said = _what_the_binding_says(
        {"delta_g_kjmol": -10.364, "delta_g_standard_error_kjmol": float("nan")})

    assert said == ["Binding:        -10.4 kJ/mol (standard state, 1 M)"]


def test_the_console_reads_a_record_from_before_the_interval_as_it_did():
    """A guard, passing before this round too: the percentile interval is
    not called 95%."""
    said = _what_the_binding_says(
        {"delta_g_kjmol": -10.364, "delta_g_standard_error_kjmol": 0.25635,
         "delta_g_confidence_kjmol": [-10.93, -10.07]})

    assert said == ["Binding:        -10.4 +/- 0.3 kJ/mol (standard state, 1 M)"]


@pytest.mark.parametrize("binding, said", [
    (FORTY, "-10.36 ± 0.26 kJ/mol (standard state, 1 M; the error a standard "
            "error), with a 95% interval of -10.88 to -9.85 kJ/mol."),
    (ONE, "-10.36 kJ/mol (standard state, 1 M), with no interval: 1 resample "
          "gave a binding free energy, and a standard error needs at least two "
          "(`bootstrap_resamples` sets how many are taken)."),
    (NONE, "-10.36 kJ/mol (standard state, 1 M), with no interval: no "
           "bootstrap was run (as with `bootstrap_resamples: 0`), so there is "
           "no standard error."),
    (NO_SPREAD, "-10.36 kJ/mol (standard state, 1 M), with no interval: every "
                "resample gave the same binding free energy, so there is no "
                "spread."),
], ids=["40 resamples", "1 resample", "no bootstrap", "no spread"])
def test_the_gui_says_the_interval_or_why_there_is_none(tmp_path, binding, said):
    assert gui(tmp_path, binding) == (
        "Its windows give a binding free energy of " + said)


def test_the_gui_reads_a_record_from_before_the_interval_as_it_did(tmp_path):
    """A guard, passing before this round too."""
    assert gui(tmp_path, {
        "delta_g_kjmol": -10.364, "delta_g_standard_error_kjmol": 0.25635,
        "delta_g_confidence_kjmol": [-10.93, -10.07]}) == (
        "Its windows give a binding free energy of -10.36 ± 0.26 kJ/mol "
        "(standard state, 1 M; the error a standard error).")


@pytest.mark.parametrize("error", [None, 3.6e-15])
def test_the_gui_calls_no_error_a_standard_error_where_there_is_none(
        tmp_path, error):
    """Records from before this round: one resample saved a null error, and
    resamples with no spread an error of rounding, which was printed to 16
    decimal places."""
    assert gui(tmp_path, {
        "delta_g_kjmol": -10.364, "delta_g_standard_error_kjmol": error,
        "delta_g_confidence_kjmol": [-10.364, -10.364]}) == (
        "Its windows give a binding free energy of -10.36 kJ/mol "
        "(standard state, 1 M).")
