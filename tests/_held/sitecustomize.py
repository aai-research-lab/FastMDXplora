"""Holds a test's study at a known point, so a fixture signals or kills it
there whatever the machine's speed or load.

On the PYTHONPATH of a study a test starts (and of the workers it spawns,
which inherit both), with ``FASTMDX_TEST_HOLD`` naming a folder:

- ``FASTMDX_TEST_HOLD_PIECE`` (a piece's folder name, ``segment-001``): once
  that piece's production has run ``FASTMDX_TEST_HOLD_AFTER`` steps (and, with
  ``FASTMDX_TEST_HOLD_CHECKPOINT`` set, written its checkpoint), its run's
  name is written in the folder (``<run>.held``, the process id in it), and
  it waits there until a stop is heard, or 300 s pass. ``FASTMDX_TEST_HOLD_RUNS``
  (comma separated) limits it to those runs' pieces.
- ``FASTMDX_TEST_HOLD_CALL`` (``module:function``): as that function is first
  called, ``call.held`` is written and it waits the same way.

Nothing is held without ``FASTMDX_TEST_HOLD``; a study run by a person never
has it.
"""

import os

_FOLDER = os.environ.get("FASTMDX_TEST_HOLD")


def _wait(marker, heard):
    import time

    marker.write_text(str(os.getpid()))
    until = time.monotonic() + 300
    while time.monotonic() < until and not heard():
        time.sleep(0.05)


def _install():
    import importlib
    from pathlib import Path

    folder = Path(_FOLDER)
    from fastmdxplora.simulation import runner

    def heard():
        return any(getattr(listener, "signal", None) is not None
                   for listener in list(runner._LISTENING))

    piece = os.environ.get("FASTMDX_TEST_HOLD_PIECE")
    if piece:
        after = int(os.environ.get("FASTMDX_TEST_HOLD_AFTER") or 0)
        checkpoint = bool(os.environ.get("FASTMDX_TEST_HOLD_CHECKPOINT"))
        runs = [name for name in (os.environ.get("FASTMDX_TEST_HOLD_RUNS") or "").split(",")
                if name]
        real = runner._cap_for_stopping
        held = []

        def cap(chunk, done, elapsed):
            chunk = min(real(chunk, done, elapsed), 50)
            listener = runner._LISTENING[-1] if runner._LISTENING else None
            where = Path(getattr(listener, "folder", None) or ".")
            if (not held and where.name == piece and (not runs or where.parent.name in runs)
                    and done >= after
                    and (not checkpoint
                         or (where / "simulation" / "checkpoint.chk").is_file())):
                held.append(1)
                _wait(folder / f"{where.parent.name}.held", heard)
            return chunk

        runner._cap_for_stopping = cap

    call = os.environ.get("FASTMDX_TEST_HOLD_CALL")
    if call:
        module_name, name = call.split(":")
        module = importlib.import_module(module_name)
        real_call = getattr(module, name)
        called = []

        def holding(*args, **kwargs):
            if not called:
                called.append(1)
                _wait(folder / "call.held", heard)
            return real_call(*args, **kwargs)

        setattr(module, name, holding)


if _FOLDER:
    _install()
