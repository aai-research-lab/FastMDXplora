"""Four significant figures keep their trailing zeros: 1.0004 nm read
"1 nm", a value given to one figure, beside 1.026 nm given to four."""

from __future__ import annotations

import pytest

from fastmdxplora.statistics import four_figures, with_its_error


@pytest.mark.parametrize("value, said", [
    (1.0004, "1.000"), (0.020003, "0.02000"), (300.0, "300.0"), (0.5, "0.5000"),
    (9.9996, "10.00"), (-0.99996, "-1.000"), (1.0e-5, "1.000e-05"),
    (1_000_000.0, "1.000e+06"), (-45472.31, "-45,472"), (1.02617, "1.026"),
])
def test_four_figures_are_four(value, said):
    assert four_figures(value) == said


def test_an_error_of_zero_keeps_the_value_s_figures():
    assert with_its_error(1.0004, 0.0) == "1.000 ± 0"
