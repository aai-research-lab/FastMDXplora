"""The console is wherever the console is now.

Both output layers cached the stream they were first handed. `sys.stdout` is
not fixed for the life of a process, so anything that redirected it
afterwards kept having output written to the stream it had replaced --
invisible wherever it went, and a `ValueError: I/O operation on closed file`
raised from the print itself if that stream had since been closed.

One defect in two places, which is the shape this package keeps finding:
`utils.logging` cached it in a module-level handler, `utils.presenter` in a
singleton's attribute, and each was written without the other in view.
"""

from __future__ import annotations

import io
import logging
import sys


class TestThePresenterFollowsARedirect:

    def test_a_redirect_after_construction_is_honoured(self):
        from fastmdxplora.utils.presenter import SessionPresenter

        presenter = SessionPresenter()          # binds to the current stdout
        buffer = io.StringIO()
        original = sys.stdout
        sys.stdout = buffer
        try:
            presenter.info("this line was written after the redirect")
        finally:
            sys.stdout = original

        assert "after the redirect" in buffer.getvalue()

    def test_a_closed_stream_does_not_raise_from_the_next_print(self):
        """The symptom, rather than the mechanism.

        A stream that is replaced and then closed is the ordinary case: it is
        what a captured stream does at the end of the block that captured it.
        """
        from fastmdxplora.utils.presenter import SessionPresenter

        presenter = SessionPresenter()
        closed = io.StringIO()
        original = sys.stdout
        sys.stdout = closed
        presenter.info("written while the substitute was open")
        closed.close()
        sys.stdout = original

        presenter.info("written after it was closed")  # must not raise

    def test_an_explicit_stream_is_still_honoured(self):
        """Passing one means it is used, redirect or not."""
        from fastmdxplora.utils.presenter import SessionPresenter

        given = io.StringIO()
        presenter = SessionPresenter(stream=given)
        elsewhere = io.StringIO()
        original = sys.stdout
        sys.stdout = elsewhere
        try:
            presenter.info("goes to the stream it was given")
        finally:
            sys.stdout = original

        assert "goes to the stream it was given" in given.getvalue()
        assert elsewhere.getvalue() == ""

    def test_colour_is_decided_against_the_stream_being_written_to(self):
        from fastmdxplora.utils.presenter import SessionPresenter

        presenter = SessionPresenter(stream=io.StringIO())
        assert presenter._color is False, (
            "A StringIO is not a terminal, so nothing should be coloured.")

        presenter._color = True
        assert presenter._color is True, (
            "An explicit choice must still win over what is derived.")


class TestTheLoggerFollowsARedirect:

    def test_the_console_handler_moves_with_stdout(self):
        from fastmdxplora.utils.logging import get_logger, setup_console

        setup_console()
        buffer = io.StringIO()
        original = sys.stdout
        sys.stdout = buffer
        try:
            setup_console()                     # what the CLI calls each run
            get_logger("test.redirect").warning("after the redirect")
        finally:
            sys.stdout = original

        assert "after the redirect" in buffer.getvalue()

    def test_rebinding_away_from_a_closed_stream_does_not_raise(self):
        """`setStream` flushes the outgoing stream, which is the closed one.

        Assigning the new stream directly is what avoids raising on exactly
        the stream this is trying to get away from.
        """
        from fastmdxplora.utils.logging import setup_console

        closed = io.StringIO()
        original = sys.stdout
        sys.stdout = closed
        setup_console()
        closed.close()
        sys.stdout = original

        setup_console()   # must not raise

    def test_the_handler_is_not_duplicated_by_rebinding(self):
        """Following a redirect must not add a second handler each time."""
        from fastmdxplora.utils.logging import setup_console

        base = setup_console()
        before = len(base.handlers)
        for _ in range(3):
            buffer = io.StringIO()
            original = sys.stdout
            sys.stdout = buffer
            try:
                setup_console()
            finally:
                sys.stdout = original

        assert len(logging.getLogger(base.name).handlers) == before
