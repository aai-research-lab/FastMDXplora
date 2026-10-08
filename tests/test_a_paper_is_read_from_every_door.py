"""A paper's MD studies from the GUI.

The Config Builder's From a paper asks the server to read a paper with the
person's AI model, and to give the chosen configs as a file or a zip; a
hosted GUI reads none.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
import zipfile

import pytest
import yaml

from fastmdxplora.paper import studies as paper_studies

from tests._a_paper import jats, scripted


@pytest.fixture(autouse=True)
def _own_cache_and_model(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMDXPLORA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(paper_studies, "the_ai_model", lambda: (scripted(), "test/model"))


@pytest.fixture
def paper_file(tmp_path):
    path = tmp_path / "paper.xml"
    path.write_bytes(jats())
    return path


def _post(session, route, body):
    request = urllib.request.Request(session.url + route, data=json.dumps(body).encode(),
                                     method="POST", headers={"Content-Type": "application/json"})
    try:
        response = urllib.request.urlopen(request, timeout=60)
        return response.read(), response.headers
    except urllib.error.HTTPError as exc:
        return exc.read(), exc.headers


def test_the_gui_reads_a_paper_and_gives_its_configs(tmp_path, paper_file):
    from fastmdxplora.gui.server import start_dashboard_session

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = start_dashboard_session(output=str(workspace), host="127.0.0.1", port=0)
    try:
        body, _ = _post(session, "/api/paper/read", {"source": str(paper_file)})
        read = json.loads(body)
        assert read["ok"], read
        assert [plan["id"] for plan in read["plans"]] == ["S1", "S2"]
        assert read["plans"][0]["length"] == "3 x 100 ns"
        assert read["model"] == "test/model"
        one, headers = _post(session, "/api/paper/download", {"configs": [
            {"id": "S1", "label": "x", "state": "ready", "config": read["plans"][0]["config"]}]})
        assert 'filename="paper-s1.yml"' in headers["Content-Disposition"]
        assert yaml.safe_load(one)["paper"]["study"] == "S1"
        both, headers = _post(session, "/api/paper/download", {"configs": [
            {"id": plan["id"], "config": plan["config"]} for plan in read["plans"]]})
        assert headers["Content-Type"] == "application/zip"
        assert sorted(zipfile.ZipFile(io.BytesIO(both)).namelist()) == [
            "paper-s1.yml", "paper-s2.yml"]
        empty, _ = _post(session, "/api/paper/read", {})
        assert json.loads(empty)["ok"] is False
    finally:
        session.server.shutdown()


def test_a_hosted_gui_reads_no_paper(paper_file):
    from fastmdxplora.gui.paper_view import read_paper_studies

    said = read_paper_studies({"source": str(paper_file)}, hosted=True)
    assert said["ok"] is False and "your own computer" in said["error"]
