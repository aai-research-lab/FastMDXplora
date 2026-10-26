"""A request the GUI cannot use is answered as the caller's mistake.

Found by sending every route bodies it was not written for. A body that was
not JSON, or JSON that was not an object, was answered 500 "Dashboard request
failed." by every POST route. A study named by a number, a list of sets or
settings given as one value, a config file that is not text or is nested past
what the parser follows: each was a server failure. And `/api/run` given no
structure made a study folder and started `fastmdx explore`, which stopped at
once and left the folder in the workspace's list.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from fastmdxplora.gui.server import start_test_server


@pytest.fixture()
def home(tmp_path: Path):
    server, url = start_test_server(tmp_path / "workspace", home_mode=True)
    try:
        yield url, tmp_path
    finally:
        server.shutdown()
        server.server_close()


def _post(url: str, body: bytes) -> tuple[int, dict]:
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


@pytest.mark.parametrize("body", [b"not json", b"[]", b"null", b'"text"', b"\xff\xfe"])
def test_a_body_that_is_not_an_object_is_refused_as_one(home, body: bytes) -> None:
    url, _ = home
    status, answer = _post(url + "/api/schema", body)
    assert status == 400
    assert answer["ok"] is False
    assert answer["error"] in ("Request body is not JSON.", "JSON body must be an object")


@pytest.mark.parametrize("state", [{}, {"system": 7}, {"systems": [{"system": 7}]}])
def test_a_run_of_nothing_is_refused_before_anything_starts(home, state: dict) -> None:
    url, tmp_path = home
    with patch("fastmdxplora.gui.exploration.subprocess.Popen") as popen:
        status, answer = _post(url + "/api/run", json.dumps(state).encode())
    assert status == 200
    assert answer["ok"] is False
    assert answer["error"]
    popen.assert_not_called()
    studies = [p for p in (tmp_path / "workspace").iterdir()
               if p.is_dir() and not p.name.startswith(".")]
    assert studies == []


@pytest.mark.parametrize("route", ["/api/agent/conversation/new",
                                   "/api/agent/conversation/attach"])
@pytest.mark.parametrize("study", [7, ["a"], {"a": 1}, True])
def test_a_study_named_by_something_not_a_path_is_no_study(home, route: str, study) -> None:
    url, _ = home
    status, answer = _post(url + route, json.dumps({"study": study,
                                                    "from_study": study}).encode())
    assert status == 200
    assert answer == {"ok": False, "error": "No such study."}


def test_settings_to_unset_given_as_one_value_unset_nothing(tmp_path: Path) -> None:
    from fastmdxplora.gui.settings_page import defaults_changed

    said = defaults_changed(tmp_path, {"unset": 7})
    assert said["ok"] is True


@pytest.mark.parametrize("content, said", [
    (b"\xed\xa0\x80 not text", "That is not a text file"),
    (b"x: " + b"[" * 5000 + b"]" * 5000, "nested too deeply"),
])
@pytest.mark.parametrize("route", ["/api/check-config", "/api/load-config"])
def test_a_config_that_cannot_be_read_says_why(home, route: str, content: bytes,
                                               said: str) -> None:
    url, tmp_path = home
    config = tmp_path / "workspace" / "bad.yml"
    config.write_bytes(content)
    status, answer = _post(url + route, json.dumps({"path": str(config)}).encode())
    assert status == 200
    assert answer["ok"] is False
    assert said in answer["error"]


def test_no_config_named_is_said_as_that(home) -> None:
    url, _ = home
    status, answer = _post(url + "/api/check-config", b'{"path": ""}')
    assert status == 200
    assert answer["error"] == "No config was named."
