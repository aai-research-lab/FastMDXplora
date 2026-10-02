"""FastMDXplora for AI apps, over the Model Context Protocol.

``fastmdx mcp`` lets an AI app (an AI chat app, an AI coding tool, an AI
agent a lab builds) design, check, run and read studies on this machine,
inside one workspace folder. The AI app's model writes a study's config and
the validator judges it, as it judges one written by hand; the tools say
what the software finds. ``ask_agent``, the FastMDXplora Agent as the GUI
has it, is there for a person who asks for it: it writes with the AI app's
model where the AI app lends it, and otherwise with the person's own AI
model, on their own key. Nothing bypasses the checks: a study runs only from
a config the validator accepted, whose plan was checked, with the person's
go-ahead.

See ``docs/mcp.md``.
"""

from __future__ import annotations

import os
import sys

from fastmdxplora.mcp.app import App
from fastmdxplora.mcp.workspace import Workspace

__all__ = ["App", "Workspace", "serve_stdio"]


def serve_stdio(workspace: str | os.PathLike[str], *, runs: bool = True) -> int:
    """Serve on this process's standard streams until the AI app closes them.

    Standard output carries the protocol and nothing else, so it is taken
    for the protocol first and everything else that would print there (a
    log line, a library's progress, a C extension's own output) is sent to
    standard error, which the protocol leaves for logging.
    """
    from fastmdxplora.agent.models import completion_for
    from fastmdxplora.utils.logging import setup_console

    place = Workspace.at(workspace)
    # Both streams are the protocol's: taken for it here, and what is left on
    # them for everything else is standard error and nothing, so neither a
    # line printed nor a program the tools start can touch the protocol.
    protocol_in = os.fdopen(os.dup(sys.stdin.fileno()), "rb")
    protocol_out = os.fdopen(os.dup(sys.stdout.fileno()), "wb")
    sys.stdout.flush()
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    with open(os.devnull, "rb") as nothing:
        os.dup2(nothing.fileno(), sys.stdin.fileno())
    sys.stdout = sys.stderr
    setup_console()  # the log follows standard output, which is standard error now
    # Paths in a config are the workspace's, for the checks here and the runs.
    os.chdir(place.root)
    print(f"FastMDXplora MCP server: workspace {place.root}"
          + ("" if runs else " (read and check only)"), file=sys.stderr)
    app = App(place, runs=runs, complete_for=completion_for)
    try:
        app.server().serve(protocol_in, protocol_out)
    except KeyboardInterrupt:
        pass
    return 0
