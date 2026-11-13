"""The bilayer tests run against this checkout.

`fastmdxplora-membrane` is required by FastMDXplora, so `pip` installs the
released one from PyPI unless it is given this folder. The tests beside this
file would then measure the release, and a change made here would not be the
change under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

fastmdxplora_membrane = pytest.importorskip("fastmdxplora_membrane")

IN_TREE = Path(__file__).resolve().parents[1] / "src" / "fastmdxplora_membrane" / "__init__.py"


def test_the_package_under_test_is_the_one_in_this_tree() -> None:
    imported = Path(fastmdxplora_membrane.__file__).resolve()
    assert imported == IN_TREE.resolve(), (
        f"These tests are running against {imported}\n"
        f"but this checkout is           {IN_TREE}\n\n"
        "Install the one in this checkout with:\n\n"
        "    pip install -e packages/fastmdxplora-membrane\n")
