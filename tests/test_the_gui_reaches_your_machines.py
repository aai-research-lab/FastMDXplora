"""The GUI reaches your machines under the rules every interface keeps.

Its routes (`gui/remote_routes.py`) do what `fastmdx remote` does at a
terminal, for the page: machines only from their records, a send only of
the plan the page showed (a token kept ten minutes, used once, and only
while what would travel is what was shown), a fetch only of the size shown,
everything inside the GUI's workspace, behind the server's gate, and none
of it in a hosted GUI. The machine is this computer standing in, as in
`test_a_study_travels_and_comes_back`.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from fastmdxplora.gui.exploration import DashboardRuntime
from fastmdxplora.gui.remote_routes import RemoteDesk
from fastmdxplora.gui.server import make_handler
from tests import test_a_study_travels_and_comes_back as travels
from tests.test_a_study_travels_and_comes_back import RELEASE

machine = travels.machine
machine_path = travels.machine_path


@pytest.fixture
def served(machine, monkeypatch):
    """A GUI on the study's folder, reaching the stand-in machine."""
    from fastmdxplora.remote import api
    from fastmdxplora.remote import send as sending

    monkeypatch.setattr(api, "_link", lambda name, transport: machine.transport())
    monkeypatch.setattr(api, "this_code", lambda: RELEASE)
    monkeypatch.setattr(sending, "this_code", lambda: RELEASE)
    real = sending.run_here
    monkeypatch.setattr(sending, "run_here", lambda command, runner=None, **more:
                        real(command, runner=machine.local, **more))
    root = machine.study.parent
    with _serving(DashboardRuntime(workspace_root=root, exploration_root=root)) as address:
        yield address, machine


@contextlib.contextmanager
def _serving(runtime: DashboardRuntime, *, allow_control: bool = True):
    handler = make_handler(runtime.workspace_root, runtime=runtime,
                           allow_control=allow_control)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _ask(address: str, path: str, body: dict | None = None,
         headers: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        f"http://{address}{path}", data=data, method="GET" if body is None else "POST",
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.getcode(), json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _planned(address: str) -> dict:
    status, said = _ask(address, "/api/remote/plan", {"config": "study.yml",
                                                      "machine": "box"})
    assert status == 200 and said["ok"], said
    return said


def _sent(machine) -> bool:
    return any(c.startswith("mkdir") for c in machine.commands)


class TestMachinesAndPlans:
    def test_the_machines_are_listed_from_their_records(self, served):
        address, machine = served
        status, said = _ask(address, "/api/remote/machines")
        assert status == 200 and said["ok"]
        assert [m["name"] for m in said["machines"]] == ["box"]
        assert said["jobs"] == []
        assert machine.commands == []

    def test_a_plan_says_what_travels_and_sends_nothing(self, served):
        address, machine = served
        plan = _planned(address)
        assert plan["machine"] == "box" and plan["job"] == "study"
        assert plan["travels"] == [{"name": "inputs/top.pdb", "from": "top.pdb",
                                    "bytes": 5}]
        assert plan["results"] == "study" and plan["kept_s"] == 600
        assert "explore -c study.yml --output run" in plan["script"]
        assert not _sent(machine)

    def test_the_plan_shown_is_sent_once(self, served):
        address, machine = served
        plan = _planned(address)
        status, sent = _ask(address, "/api/remote/send", {"plan": plan["plan"]})
        assert status == 200 and sent["ok"], sent
        assert sent["job"]["name"] == "study" and sent["job"]["machine"] == "box"
        status, again = _ask(address, "/api/remote/send", {"plan": plan["plan"]})
        assert again["ok"] is False and again["code"] == "remote.send.unconfirmed"
        travels._until_finished(machine, "study")

    def test_a_config_changed_after_the_plan_is_not_sent(self, served):
        """The person agreed to what the page showed: a config changed since
        sends something else."""
        address, machine = served
        plan = _planned(address)
        machine.study.write_text(machine.study.read_text() + "simulation:\n"
                                 "  random_seed: 7\n")
        status, said = _ask(address, "/api/remote/send", {"plan": plan["plan"]})
        assert said["ok"] is False and said["code"] == "remote.send.unconfirmed"
        assert "changed after the plan was shown" in said["error"]
        assert not _sent(machine)

    def test_a_plan_older_than_ten_minutes_is_not_sent(self, served, monkeypatch):
        from fastmdxplora.gui import remote_routes

        address, machine = served
        plan = _planned(address)
        later = time.monotonic() + remote_routes.PLAN_KEPT_S + 1
        monkeypatch.setattr(remote_routes.time, "monotonic", lambda: later)
        status, said = _ask(address, "/api/remote/send", {"plan": plan["plan"]})
        assert said["ok"] is False and said["code"] == "remote.send.unconfirmed"
        assert not _sent(machine)

    def test_a_token_not_given_out_sends_nothing(self, served):
        address, machine = served
        status, said = _ask(address, "/api/remote/send", {"plan": "0" * 32})
        assert said["ok"] is False and said["code"] == "remote.send.unconfirmed"
        assert machine.commands == []

    def test_a_config_outside_the_workspace_is_refused(self, served, tmp_path):
        address, machine = served
        outside = tmp_path / "elsewhere.yml"
        outside.write_text(machine.study.read_text())
        status, said = _ask(address, "/api/remote/plan", {"config": str(outside),
                                                          "machine": "box"})
        assert said["ok"] is False and "not a config file in the workspace" in said["error"]
        assert machine.commands == []

    def test_a_results_folder_in_use_is_refused(self, served):
        address, machine = served
        (machine.study.parent / "study").mkdir()
        (machine.study.parent / "study" / "kept.txt").write_text("x")
        status, said = _ask(address, "/api/remote/plan", {"config": "study.yml",
                                                          "machine": "box"})
        assert said["ok"] is False and said["code"] == "environment.path.exists"

    def test_a_machine_without_a_record_is_refused(self, served):
        address, machine = served
        status, said = _ask(address, "/api/remote/plan", {"config": "study.yml",
                                                          "machine": "gpu-box"})
        assert said["ok"] is False and "gpu-box" in said["error"]
        assert machine.commands == []


class TestJobs:
    def _ended(self, served):
        address, machine = served
        plan = _planned(address)
        _ask(address, "/api/remote/send", {"plan": plan["plan"]})
        travels._until_finished(machine, "study")
        return address, machine

    def test_a_job_is_asked_about_and_fetched_at_the_size_shown(self, served):
        address, machine = self._ended(served)
        status, said = _ask(address, "/api/remote/job?job=study")
        assert said["ok"] and said["job"]["state"] == "done"
        status, sizes = _ask(address, "/api/remote/fetch-sizes?job=study")
        assert sizes["ok"] and sizes["run_written"]
        shown = sizes["bringing"]["without"]
        status, wrong = _ask(address, "/api/remote/fetch", {"job": "study",
                                                            "bringing": shown + 1})
        assert wrong["ok"] is False and wrong["code"] == "remote.fetch.unconfirmed"
        assert not (machine.study.parent / "study" / "manifest.json").exists()
        status, fetched = _ask(address, "/api/remote/fetch", {"job": "study",
                                                              "bringing": shown})
        assert fetched["ok"], fetched
        assert (machine.study.parent / "study" / "manifest.json").is_file()

    def test_a_job_of_another_workspace_is_not_reached(self, served):
        address, machine = served
        status, said = _ask(address, "/api/remote/job?job=trial")
        assert said["ok"] is False and said["code"] == "remote.job.unknown"

    def test_a_running_job_is_cancelled(self, served):
        address, machine = served
        machine.env["FAKE_SLEEP"] = "30"
        plan = _planned(address)
        status, sent = _ask(address, "/api/remote/send", {"plan": plan["plan"]})
        try:
            status, said = _ask(address, "/api/remote/cancel", {"job": "study"})
            assert said["ok"] and said["stopped"] and said["job"]["state"] == "abandoned"
        finally:
            from tests.test_remote_from_every_interface import _group_killed

            from fastmdxplora.remote.jobs import load_job
            _group_killed(load_job("study").handle)


class TestTheGate:
    @pytest.mark.parametrize("headers", [
        {"Origin": "https://evil.example"},
        {"Sec-Fetch-Site": "cross-site"},
    ])
    def test_a_page_elsewhere_sends_nothing(self, served, headers):
        address, machine = served
        plan = _planned(address)
        status, said = _ask(address, "/api/remote/send", {"plan": plan["plan"]},
                            headers=headers)
        assert status == 403
        assert not _sent(machine)

    def test_nothing_is_answered_beyond_loopback(self, machine):
        root = machine.study.parent
        with _serving(DashboardRuntime(workspace_root=root, exploration_root=root),
                      allow_control=False) as address:
            status, _ = _ask(address, "/api/remote/machines")
            assert status == 403
            status, _ = _ask(address, "/api/remote/plan", {"config": "study.yml",
                                                           "machine": "box"})
            assert status == 403
        assert machine.commands == []

    def test_a_hosted_gui_reaches_no_machine(self, machine):
        desk = RemoteDesk(machine.study.parent, hosted=True)
        for said in (desk.get("/api/remote/machines", {}),
                     desk.post("/api/remote/plan", {"config": "study.yml",
                                                    "machine": "box"})):
            assert said["ok"] is False and "hosted GUI" in said["error"]
        assert machine.commands == []

    def test_other_routes_are_not_these(self, machine):
        desk = RemoteDesk(machine.study.parent)
        assert desk.get("/api/status", {}) is None
        assert desk.post("/api/run", {}) is None
        assert desk.get("/api/remote/nothing", {}) is None
