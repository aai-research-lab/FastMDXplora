"""A first page load is answered whole, however its requests arrive.

The routes import what they need when first asked. A page asks for many
things at once, the server answers each in a thread of its own, and the
package's imports go round in a circle (`fastmdxplora.analysis` imports every
analysis, each of which imports `analysis.plotting`). Two threads importing
into that circle together is refused by Python's import locks with "deadlock
detected", so a first page load answered 500 to whichever route lost: the
Agent's plan was found without its cost lines that way. The server now
imports them before it serves anything.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap


def _in_a_fresh_interpreter(code: str) -> str:
    done = subprocess.run([sys.executable, "-c", textwrap.dedent(code)],
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-3000:]
    return done.stdout


def test_what_the_routes_import_is_imported_before_serving(tmp_path) -> None:
    out = _in_a_fresh_interpreter(f"""
        import sys
        from fastmdxplora.gui.server import _IMPORTED_BY_THE_ROUTES, start_dashboard_session
        assert not any(name in sys.modules for name in _IMPORTED_BY_THE_ROUTES[:1])
        session = start_dashboard_session(output={str(tmp_path)!r}, host="127.0.0.1", port=0)
        print(sorted(name for name in _IMPORTED_BY_THE_ROUTES if name not in sys.modules))
        session.server.shutdown()
    """)
    assert out.strip().splitlines()[-1] == "[]"


def test_requests_arriving_together_are_all_answered(tmp_path) -> None:
    out = _in_a_fresh_interpreter(f"""
        import json, threading, urllib.error, urllib.request
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
                    statuses.append(reply.status)
            except urllib.error.HTTPError as error:
                statuses.append(error.code)
        threads = [threading.Thread(target=ask, args=(p, b)) for p, b in posts.items()]
        threads += [threading.Thread(target=ask, args=(p,)) for p in gets]
        for t in threads: t.start()
        for t in threads: t.join()
        session.server.shutdown()
        print(sorted(statuses))
    """)
    statuses = eval(out.strip().splitlines()[-1])  # noqa: S307 - our own print
    assert len(statuses) == 7 and 500 not in statuses
