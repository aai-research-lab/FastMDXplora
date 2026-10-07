"""Copy path copies the file's full path on the person's own computer.

The Files page copied `file.absolute_path || file.path`, and no record
carried an `absolute_path`, so it copied `simulation/production.dcd`: of no
use in a terminal, a script or another program. Beyond loopback the path on
the server is not the caller's to know, and the path inside the study is
what is given there.
"""

from __future__ import annotations

import contextlib
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from fastmdxplora.gui.exploration import DashboardRuntime
from fastmdxplora.gui.server import make_handler


@contextlib.contextmanager
def _serving(study: Path, *, allow_control: bool):
    runtime = DashboardRuntime(workspace_root=study, exploration_root=study.parent,
                               active_root=study)
    handler = make_handler(study, runtime=runtime, allow_control=allow_control)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read())


def _study(tmp_path: Path) -> Path:
    study = tmp_path / "study"
    (study / "simulation").mkdir(parents=True)
    (study / "manifest.json").write_text("{}", encoding="utf-8")
    (study / "simulation" / "production.dcd").write_bytes(b"x")
    return study.resolve()


def test_on_this_computer_each_file_says_where_it_is(tmp_path):
    study = _study(tmp_path)
    with _serving(study, allow_control=True) as url:
        for route in ("/api/artifacts", "/api/files", "/api/results"):
            records = {r["path"]: r for r in _json(url + route)["artifacts"]}
            assert records["simulation/production.dcd"]["absolute_path"] == str(
                study / "simulation" / "production.dcd"), route


def test_beyond_loopback_the_server_s_paths_are_not_given(tmp_path):
    study = _study(tmp_path)
    with _serving(study, allow_control=False) as url:
        for route in ("/api/artifacts", "/api/results"):
            records = _json(url + route)["artifacts"]
            assert records and all("absolute_path" not in r for r in records), route
