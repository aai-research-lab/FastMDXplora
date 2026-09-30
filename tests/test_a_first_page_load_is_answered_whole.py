"""A first page load is answered whole, however its requests arrive.

The routes import what they need when first asked. A page asks for many
things at once, the server answers each in a thread of its own, and the
package's imports go round in a circle (`fastmdxplora.analysis` imports every
analysis, each of which imports `analysis.plotting`). Two threads importing
into that circle together is refused by Python's import locks with "deadlock
detected", so a first page load answered 500 to whichever route lost: the
Agent's plan was found without its cost lines that way. The server now
imports them before it serves anything.

The list of them was kept by hand and trailed the routes: it lacked
`gui.schema_payload`, and a first page load on macOS answered 500 to
`/api/schema`. It is read from the source now (`gui/route_imports.py`).
"""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "src" / "fastmdxplora" / "gui" / "server.py"


def _run_in_a_fresh_interpreter(code: str) -> subprocess.CompletedProcess:
    done = subprocess.run([sys.executable, "-c", textwrap.dedent(code)],
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-3000:]
    return done


def _in_a_fresh_interpreter(code: str) -> str:
    return _run_in_a_fresh_interpreter(code).stdout


def test_what_the_routes_import_is_imported_before_serving(tmp_path) -> None:
    out = _in_a_fresh_interpreter(f"""
        import sys
        from fastmdxplora.gui.server import start_dashboard_session
        assert "fastmdxplora.gui.schema_payload" not in sys.modules
        session = start_dashboard_session(output={str(tmp_path)!r}, host="127.0.0.1", port=0)
        from fastmdxplora.gui.server import _imported_by_the_routes
        print(sorted(name for name in _imported_by_the_routes() if name not in sys.modules))
        print("fastmdxplora.gui.schema_payload" in sys.modules,
              "fastmdxplora.gui.starters" in sys.modules)
        session.server.shutdown()
    """)
    *_, missing, schema = out.strip().splitlines()
    assert missing == "[]"
    # The schema route's module, and what it imports inside a function.
    assert schema == "True True"


def test_every_module_a_route_imports_is_in_the_list() -> None:
    from fastmdxplora.gui.server import _imported_by_the_routes

    named = {node.module for node in ast.walk(ast.parse(SERVER.read_text(encoding="utf-8")))
             if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("fastmdxplora")}
    assert named - set(_imported_by_the_routes()) - {"fastmdxplora.gui.server"} == set()


def test_imports_read_from_a_file(tmp_path) -> None:
    from fastmdxplora.gui.route_imports import _named_in, modules_reached_from

    source = tmp_path / "probe.py"
    source.write_text("import os\n"
                      "import fastmdxplora.gui.plan\n"
                      "from fastmdxplora.gui import series\n"
                      "from fastmdxplora.analysis import (  # noqa: F401, E402\n"
                      "    area_per_lipid as _area,\n"
                      "    rdf,\n"
                      ")\n"
                      "def route():\n"
                      "    from . import measure\n"
                      "    from ..config import diff\n", encoding="utf-8")
    named = _named_in("fastmdxplora.gui.probe", source)
    assert {"fastmdxplora.gui.plan", "fastmdxplora.gui.series", "fastmdxplora.gui.measure",
            "fastmdxplora.config.diff", "fastmdxplora.analysis.area_per_lipid",
            "fastmdxplora.analysis.rdf"} <= named
    assert "os" not in named
    reached = modules_reached_from("fastmdxplora.gui.server")
    # A function named in an import is not a module, and the server is not
    # its own route.
    assert "fastmdxplora.gui.plan.plan_of" not in reached
    assert "fastmdxplora.gui.server" not in reached
    assert modules_reached_from("fastmdxplora.no_such_module") == ()


def test_requests_arriving_together_are_all_answered(tmp_path) -> None:
    done = _run_in_a_fresh_interpreter(f"""
        import json, logging, threading, urllib.error, urllib.request
        # What a failing route raised, with its traceback, on stderr.
        logging.basicConfig(level=logging.DEBUG)
        from fastmdxplora.gui.server import start_dashboard_session
        session = start_dashboard_session(output={str(tmp_path)!r}, host="127.0.0.1", port=0)
        base = session.url.rstrip("/")
        posts = {{"/api/load-config": {{"config": {{"systems": [{{"system": "x.pdb"}}]}}}},
                  "/api/config": {{"system": "x.pdb"}},
                  "/api/preview-system": {{"system": ""}}}}
        gets = ["/api/series?analysis=rmsd", "/api/report", "/api/results", "/api/schema"]
        statuses = []
        def ask(path, body=None):
            data = None if body is None else json.dumps(body).encode()
            request = urllib.request.Request(base + path, data=data, method="POST" if data else "GET",
                                             headers={{"Content-Type": "application/json",
                                                      "Origin": base}})
            try:
                with urllib.request.urlopen(request, timeout=120) as reply:
                    statuses.append((reply.status, path, ""))
            except urllib.error.HTTPError as error:
                statuses.append((error.code, path, error.read()[:400].decode("utf-8", "replace")))
        threads = [threading.Thread(target=ask, args=(p, b)) for p, b in posts.items()]
        threads += [threading.Thread(target=ask, args=(p,)) for p in gets]
        for t in threads: t.start()
        for t in threads: t.join()
        session.server.shutdown()
        print(sorted(statuses))
    """)
    statuses = eval(done.stdout.strip().splitlines()[-1])  # noqa: S307 - our own print
    # Which route, and what it said: one answered 500 once in a full run
    # under load, and a bare list of codes could not say which.
    failed = [(path, said) for code, path, said in statuses if code == 500]
    said = [line for line in done.stderr.splitlines() if "fastmdxplora" in line or "Error" in line]
    assert len(statuses) == 7 and not failed, (failed, said[-40:])
