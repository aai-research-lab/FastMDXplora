"""What an empty box means is said in the unit the box is in.

The NVT equilibration's box is in ns, and its placeholder said "Not set:
500 ps": someone who typed the 500 they read asked for 500 ns.
"""

from __future__ import annotations

import re

import pytest

from fastmdxplora.config.schema import UNSET_MEANS
from fastmdxplora.simulation.lengths import DEFAULT_NVT_NS

_IN_NS = sorted(key for key in UNSET_MEANS if key.endswith("_ns"))


@pytest.mark.parametrize("key", _IN_NS)
def test_a_length_in_ns_is_said_in_ns_first(key):
    assert re.match(r"^\d+(\.\d+)? ns\b", UNSET_MEANS[key]), UNSET_MEANS[key]


def test_the_nvt_length_said_is_the_one_used():
    assert float(UNSET_MEANS["simulation.nvt_duration_ns"].split()[0]) == pytest.approx(DEFAULT_NVT_NS)
