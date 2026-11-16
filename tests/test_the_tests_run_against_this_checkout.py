"""The tests have to be exercising the tree they sit in.

`pip install -U fastmdxplora==2.5.1` into the same environment replaces an
editable install with an ordinary one, and says nothing. Every `pytest` after
that imports from site-packages, so a change sitting in the tree is not the
change under test.

What that looks like from the inside is the reason this is worth a test rather
than a paragraph: the failures are `AttributeError` on code that is visibly
right there in the file you are reading. Nothing points at the import path, so
the search goes to the code, and the code is fine. An hour went into three
wrong explanations of a passing test suite before anybody printed
`fastmdxplora.__file__`.

This is that line, run first and for free.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import fastmdxplora

#: tests/ sits at the top of the checkout.
REPO = Path(__file__).resolve().parents[1]
IN_TREE = REPO / "src" / "fastmdxplora" / "__init__.py"


def _what_to_do(imported: Path) -> str:
    """The message this test exists to print."""
    return (
        f"These tests are running against {imported}\n"
        f"but this checkout is           {IN_TREE}\n"
        "\n"
        "So a change in the tree is not the change under test, and the "
        "failures that follow will look like bugs in code that is correct.\n"
        "\n"
        "This happens when a released version is installed into the same "
        "environment: `pip install -U fastmdxplora` replaces an editable "
        "install with an ordinary one and reports success either way. "
        "Restore it with:\n"
        "\n"
        '    pip install -e ".[dev]" -e packages/fastmdxplora-membrane\n'
    )


class TestTheTestsRunAgainstThisCheckout:
    def test_the_package_under_test_is_the_one_in_this_tree(self) -> None:
        if not IN_TREE.exists():
            # No checkout beside these tests, so they were installed with the
            # package and are being run against it on purpose -- a conda-forge
            # recipe does exactly this. There is nothing here to disagree with.
            pytest.skip("not run from a source checkout")

        imported = Path(fastmdxplora.__file__).resolve()
        assert imported == IN_TREE.resolve(), _what_to_do(imported)

    def test_the_message_says_what_to_do(self) -> None:
        """A bare assertion here would report a mismatch of two long paths,
        which is the same hour again: true, and not enough to act on."""
        said = _what_to_do(Path("/usr/lib/python3/site-packages/fastmdxplora/__init__.py"))
        assert 'pip install -e ".[dev]"' in said
        assert "not the change under test" in said
