"""A run's length in the Methods paragraph, as a reader writes it.

Said with `.3g`, a microsecond of production was "1e+03 ns" and a run a
hair under a nanosecond "1e+03 ps", in the paragraph meant to be pasted
into a paper.
"""

from __future__ import annotations

import pytest

from fastmdxplora.report.methods import _ns_said, _steps_to_ns


@pytest.mark.parametrize("ns, said", [
    (1000.0, "1000 ns"),
    (1500.0, "1500 ns"),
    (12.345, "12.3 ns"),
    (2.0, "2 ns"),
    (0.5, "500 ps"),
    (0.9996, "1 ns"),        # rounded before its unit is chosen
    (0.0012, "1.2 ps"),
])
def test_a_length_is_said_in_plain_figures(ns, said):
    assert _ns_said(ns) == said


def test_a_microsecond_of_steps_is_said_in_ns():
    assert _steps_to_ns(500_000_000, 2.0) == "1000 ns"
    assert _steps_to_ns(1_000_000, 2.0) == "2 ns"
