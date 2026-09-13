"""Running a study leaves the caller's logging as it found it.

This file used to document the opposite. `explore()` turned propagation
off on the `fastmdx` logger and never turned it back on, so anything
importing this package and running one study had its own logging silently
cut off from ours for the rest of the session -- records stopped reaching
its handlers, and `caplog` stopped working in its tests.

The cause was one line inside `setup_console`, which is a function for
attaching a console handler and had no business deciding who owns the
process. It ran on the command-line path and the library path alike.

The decision now has a name and one caller. `own_the_console()` stops
propagation, and the CLI is the only thing that calls it -- correct there,
because the CLI owns the terminal and the process ends when the run does.

The trade is deliberate and worth stating, because it is not free. A
library caller who has configured root handlers will now see this
package's records twice: once through our handler, once through theirs.
That is the better failure of the two available. Doubled output is visible
and they can turn our handler off; silent swallowing is invisible and they
cannot do anything about what they cannot see.
"""

from __future__ import annotations

import logging
import pathlib
import tempfile

import pytest


@pytest.fixture
def restored_propagation():
    logger = logging.getLogger("fastmdx")
    before = logger.propagate
    yield logger
    logger.propagate = before


def test_attaching_a_console_does_not_claim_the_process(restored_propagation):
    # The line this whole file was written about. `setup_console` attaches
    # a handler; that is all it does now.
    from fastmdxplora.utils.logging import setup_console

    restored_propagation.propagate = True
    setup_console(level=logging.INFO)
    assert restored_propagation.propagate is True


def test_claiming_it_is_a_thing_you_say_out_loud(restored_propagation):
    from fastmdxplora.utils.logging import own_the_console, release_the_console

    restored_propagation.propagate = True
    own_the_console()
    assert restored_propagation.propagate is False
    release_the_console()
    assert restored_propagation.propagate is True


def test_only_the_cli_claims_it(restored_propagation):
    # Asserted by counting call sites rather than by running everything.
    # A second caller appearing is the thing that would bring the bug back,
    # and it would appear in a diff long before it appeared in a run.
    import fastmdxplora

    root = pathlib.Path(fastmdxplora.__file__).parent
    callers = sorted(
        path.relative_to(root)
        for path in root.rglob("*.py")
        if "own_the_console()" in path.read_text(encoding="utf-8")
        and path.name != "logging.py")
    assert [str(p) for p in callers] == ["cli/main.py"], (
        f"own_the_console() is called from {callers}. Only the CLI should "
        "claim the process; anything else takes a library caller's logging "
        "away from them.")


@pytest.mark.slow
def test_a_library_caller_keeps_its_own_logging(restored_propagation):
    """The bug, now a passing test.

    Ran as an xfail for one commit while the fix was thought through. It
    passes now, which is what `xfail(strict=True)` is for: it failed the
    moment the behaviour changed and said to come back here.
    """
    from fastmdxplora import FastMDXplora

    pdb = pathlib.Path(tempfile.mkdtemp()) / "x.pdb"
    pdb.write_text("ATOM      1  CA  ALA A   1       0.000   0.000   0.000"
                   "  1.00  0.00           C\nTER\nEND\n")
    restored_propagation.propagate = True
    try:
        FastMDXplora(
            config_data={"systems": [{"id": "a", "system": str(pdb)}],
                         "include": ["setup"]},
            output_dir=tempfile.mkdtemp()).explore()
    except Exception:  # noqa: BLE001 - the run failing is not the point
        pass
    assert restored_propagation.propagate is True, (
        "running a study changed this process's logging and did not change "
        "it back")


def test_both_constructor_paths_declare_where_output_goes(restored_propagation):
    """The surprise behind the logging bug, now removed.

    `FastMDXplora.__init__` still has two branches -- `system=` settles the
    output directory immediately, `config_data=` leaves it to the batch
    layer -- and that difference is real and worth keeping. What is gone is
    the *shape* difference: the deferred branch used to return without
    setting `output_dir` at all, so the same class had two attribute sets
    and code asking where the output goes had to know which, or find out
    through an AttributeError.

    It is now `None` on the deferred path, which says what the absence said
    -- not settled yet -- in a form that can be read rather than caught.
    `explore()` fills it in.

    This is not bookkeeping. An attempt to fix the logging leak landed on
    the immediate branch and did nothing, because every affected caller was
    on the deferred one and nothing made the difference visible.
    """
    from fastmdxplora import FastMDXplora

    deferred = FastMDXplora(
        config_data={"systems": [{"id": "a", "system": "x.pdb"}]},
        output_dir=tempfile.mkdtemp())
    direct = FastMDXplora(system="x.pdb", output_dir=tempfile.mkdtemp())

    assert hasattr(deferred, "output_dir")
    assert hasattr(direct, "output_dir")
    assert deferred.output_dir is None
    assert direct.output_dir is not None
