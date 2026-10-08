"""A paper's MD studies from the GUI, the Agent and an AI app.

The Config Builder's From a paper asks the server to read a paper with the
person's AI model, and to give the chosen configs as a file or a zip; a
hosted GUI reads none. The Agent reads one with its tool
``studies_in_paper``, and gives one study's config when asked. An AI app
reads the paper itself (``read_paper``) and hands its reading to
``check_paper_studies``, which checks every value against the paper's words
as any reading is checked.
"""

from __future__ import annotations

import copy
import io
import json
import urllib.error
import urllib.request
import zipfile
from types import SimpleNamespace

import pytest
import yaml

from fastmdxplora.paper import studies as paper_studies

from tests._a_paper import CLAIMS, PROTOCOL, STUDIES, jats, scripted


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


def test_the_agent_reads_a_paper_and_gives_a_study_s_config(paper_file):
    from fastmdxplora.agent.tools import Toolbox

    box = Toolbox()
    listed = box.use("studies_in_paper", {"paper": str(paper_file)})
    assert listed.ok, listed.said
    assert "S1  Ubiquitin, wild type" in listed.said and "S2  Ubiquitin L50A" in listed.said
    one = box.use("studies_in_paper", {"paper": str(paper_file), "study": "2"})
    assert one.ok and "```yaml" in one.said and "L50A" in one.said
    missing = box.use("studies_in_paper", {"paper": str(paper_file), "study": "S7"})
    assert not missing.ok and "no study S7" in missing.said


def test_the_agent_s_tool_is_held_to_the_workspace(paper_file):
    from fastmdxplora.agent.tools import Toolbox

    box = Toolbox(path_for=lambda given: None)
    refused = box.use("studies_in_paper", {"paper": str(paper_file)})
    assert not refused.ok and "outside the workspace" in refused.said


def _context(workspace):
    from fastmdxplora.mcp.tools import Context
    from fastmdxplora.mcp.workspace import Workspace

    return Context(workspace=Workspace(workspace), call=SimpleNamespace(client_name="Test App"))


def test_an_ai_app_reads_the_paper_and_its_reading_is_checked(tmp_path):
    from fastmdxplora.mcp.tools import TOOLS

    workspace = tmp_path / "studies"
    workspace.mkdir()
    (workspace / "paper.xml").write_bytes(jats())
    tools = {tool.name: tool for tool in TOOLS}
    ctx = _context(workspace)
    page = tools["read_paper"].run(ctx, {"paper": "paper.xml"})
    assert "page 1 of 1" in page and "[[Methods: Molecular dynamics simulations]]" in page
    assert "check_paper_studies" in page
    reading = {"studies": copy.deepcopy(STUDIES), "protocol_fields": {"P1": PROTOCOL},
               "claims": CLAIMS}
    reading["protocol_fields"]["P1"] = dict(PROTOCOL, temperature={
        "value": 310, "unit": "K", "quote": "simulated at 310 K"})
    checked = tools["check_paper_studies"].run(ctx, {"paper": "paper.xml",
                                                     "reading": json.dumps(reading)})
    assert "S1  Ubiquitin, wild type" in checked
    assert "temperature (not found)" in checked
    assert "```yaml" in checked and "read_by: Test App" in checked


def test_an_ai_app_reads_nothing_outside_the_workspace(tmp_path, paper_file):
    from fastmdxplora.mcp.tools import TOOLS, ToolError

    workspace = tmp_path / "studies"
    workspace.mkdir()
    tools = {tool.name: tool for tool in TOOLS}
    with pytest.raises(ToolError, match="outside the workspace"):
        tools["read_paper"].run(_context(workspace), {"paper": str(paper_file)})


def test_a_reading_that_is_not_json_is_refused(tmp_path):
    from fastmdxplora.mcp.tools import TOOLS, ToolError

    workspace = tmp_path / "studies"
    workspace.mkdir()
    (workspace / "paper.xml").write_bytes(jats())
    tools = {tool.name: tool for tool in TOOLS}
    with pytest.raises(ToolError, match="not JSON"):
        tools["check_paper_studies"].run(_context(workspace),
                                         {"paper": "paper.xml", "reading": "S1 is wild type"})
