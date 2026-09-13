"""A known defect, written down where it cannot be forgotten.

`explore()` turns propagation off on the `fastmdx` logger and does not turn
it back on. Anything calling it in-process has its logging quietly changed
for the rest of the session: records stop reaching root handlers, so a
consumer's own logging configuration silently stops seeing anything from
this package, and `caplog` in their tests stops working.

The mechanism, traced rather than guessed:

  - `FastMDXplora.__init__` has two paths. Given `system=`, it sets
    `output_dir` and calls `_configure_logging()` there and then. Given
    `config_data=` -- which is how a library caller passes a study, and
    how the agent layer does -- it defers, storing
    `_deferred_output_dir` and configuring nothing.
  - On the deferred path `explore()` builds a `BatchExplorer`, and that
    is where `setup_console()` runs. `setup_console` sets
    `propagate = False` unconditionally.
  - Nothing restores it. The CLI does not need to, because the process
    ends.

That is why an earlier attempt to add `configure_logging=False` to the
constructor did nothing: it governed the direct path, and every caller
that hits this is on the deferred one. The attempt was reverted rather
than shipped, and this file is what was learned instead.

Fixing it means threading the choice through `BatchExplorer`, or having
`setup_console` leave propagation alone and the CLI turn it off itself.
The second is tidier and has the wider blast radius: anyone with a root
handler starts seeing this package's records, possibly twice.

Marked `xfail(strict=True)`, so it fails loudly when somebody fixes it and
this file can be deleted rather than quietly outliving the bug.
"""

from __future__ import annotations

import logging
import tempfile

import pytest


@pytest.fixture
def restored_propagation():
    logger = logging.getLogger("fastmdx")
    before = logger.propagate
    yield logger
    logger.propagate = before


def test_the_two_constructor_paths_differ(restored_propagation):
    """Not the bug itself; the thing that makes it hard to see.

    A caller passing `config_data` gets an object with no `output_dir`
    and no logging configured. A caller passing `system` gets both. The
    same class, the same call, two shapes -- and every attempt to reason
    about when logging is configured has to start by knowing which.
    """
    from fastmdxplora import FastMDXplora

    deferred = FastMDXplora(
        config_data={"systems": [{"id": "a", "system": "x.pdb"}]},
        output_dir=tempfile.mkdtemp())
    assert not hasattr(deferred, "output_dir")
    assert hasattr(deferred, "_deferred_output_dir")

    direct = FastMDXplora(system="x.pdb", output_dir=tempfile.mkdtemp())
    assert hasattr(direct, "output_dir")


def test_setup_console_turns_propagation_off(restored_propagation):
    """The unconditional line the leak comes from."""
    from fastmdxplora.utils.logging import setup_console

    restored_propagation.propagate = True
    setup_console(level=logging.INFO)
    assert restored_propagation.propagate is False


@pytest.mark.xfail(strict=True, reason=(
    "explore() leaves propagation off on the fastmdx logger. Known, traced, "
    "unfixed: the fix threads a choice through BatchExplorer or moves the "
    "propagate=False out of setup_console and into the CLI. Delete this file "
    "when it is done."))
@pytest.mark.slow
def test_a_library_caller_keeps_its_own_logging(restored_propagation):
    import pathlib

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
