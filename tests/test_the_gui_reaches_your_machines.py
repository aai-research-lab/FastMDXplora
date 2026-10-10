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
from pathlib import Path

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
    # Started in the folder it opens, as `fastmdx gui` is: a relative config
    # name is the same file to the check and to the plan.
    monkeypatch.chdir(root)
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


STATE = {"system": "1UBQ", "include_phase": ["setup", "simulation"], "output": "ub-run"}


class TestTheConfigBuilder:
    """Run on a machine from the Config Builder: the study is saved as a
    config beside its results folder, as Run on this machine saves it, and
    that file is what is planned and sent."""

    def test_the_study_is_saved_beside_its_results_and_planned(self, served):
        address, machine = served
        root = machine.study.parent
        status, plan = _ask(address, "/api/remote/plan", {"state": STATE, "machine": "box"})
        assert plan["ok"], plan
        assert plan["config"] == "ub-run.yml" and plan["results"] == "ub-run"
        assert plan["fetched_there"] == ["1UBQ"]
        saved = (root / "ub-run.yml").read_text()
        assert "1UBQ" in saved
        assert not _sent(machine)
        # Planned again as it stands: the same file, not another beside it.
        status, again = _ask(address, "/api/remote/plan", {"state": STATE, "machine": "box"})
        assert again["config"] == "ub-run.yml"
        assert sorted(p.name for p in root.glob("ub-run*.yml")) == ["ub-run.yml"]
        # Changed: a file of its own, the first left as it was.
        status, other = _ask(address, "/api/remote/plan", {
            "state": {**STATE, "include_phase": ["setup"]}, "machine": "box"})
        assert other["config"] == "ub-run-2.yml"
        assert (root / "ub-run.yml").read_text() == saved
        status, sent = _ask(address, "/api/remote/send", {"plan": plan["plan"]})
        assert sent["ok"] and sent["job"]["name"] == "ub-run", sent
        travels._until_finished(machine, "ub-run")

    def test_a_results_folder_outside_the_workspace_is_refused(self, served, tmp_path):
        address, machine = served
        status, said = _ask(address, "/api/remote/plan", {
            "state": {**STATE, "output": str(tmp_path / "elsewhere" / "run")},
            "machine": "box"})
        assert said["ok"] is False and "inside the workspace" in said["error"]
        assert not (tmp_path / "elsewhere").exists()
        assert machine.commands == []

    def test_a_window_reaches_the_folder_it_puts_new_studies_in(self, machine, tmp_path):
        """Given the window's runtime, the routes reach the folder it puts
        new studies in as well as the one it opened, and a bare results
        name is where Run on this machine would write it."""
        root = machine.study.parent
        opened = root / "opened"
        opened.mkdir()
        desk = RemoteDesk(DashboardRuntime(workspace_root=opened, exploration_root=root))
        assert desk.roots == [opened.resolve(), root.resolve()]
        assert desk.inside(str(machine.study)) == machine.study.resolve()
        # A relative name is the opened folder's, as a page gives it.
        assert desk.inside("study.yml") == opened.resolve() / "study.yml"
        saved, refused, _ = desk._saved(STATE)
        assert refused is None and saved == root.resolve() / "ub-run.yml"
        assert desk.inside(str(tmp_path.parent)) is None

    def test_a_machine_never_inspected_leaves_no_config_behind(self, served):
        """First review: a plan refused for its machine still saved a
        config, a new one each time the results folder was left blank."""
        address, machine = served
        root = machine.study.parent
        status, said = _ask(address, "/api/remote/plan", {
            "state": {**STATE, "output": ""}, "machine": "gpu-box"})
        assert said["ok"] is False and "gpu-box" in said["error"]
        assert list(root.glob("*.yml")) == [machine.study]

    def test_a_config_saved_for_a_plan_refused_is_taken_back(self, served):
        address, machine = served
        root = machine.study.parent
        (root / "ub-run").mkdir()
        (root / "ub-run" / "kept.txt").write_text("x")
        status, said = _ask(address, "/api/remote/plan", {"state": STATE, "machine": "box"})
        assert said["ok"] is False and said["code"] == "environment.path.exists"
        assert not (root / "ub-run.yml").exists()
        assert (root / "ub-run" / "kept.txt").read_text() == "x"

    def test_a_link_in_the_config_s_place_is_passed_over_unread(self, served, tmp_path):
        address, machine = served
        root = machine.study.parent
        outside = tmp_path / "outside.yml"
        outside.write_text("output: mine\n")
        (root / "ub-run.yml").symlink_to(outside)
        (root / "ub-run-2.yml").write_bytes(b"\xff\xfe")
        status, plan = _ask(address, "/api/remote/plan", {"state": STATE, "machine": "box"})
        assert plan["ok"], plan
        assert plan["config"] == "ub-run-3.yml"
        assert outside.read_text() == "output: mine\n"
        assert (root / "ub-run-2.yml").read_bytes() == b"\xff\xfe"

    def test_a_results_folder_in_the_home_folder_is_refused_before_saving(
            self, machine, tmp_path, monkeypatch):
        home = machine.study.parent
        monkeypatch.setenv("HOME", str(home))
        desk = RemoteDesk(DashboardRuntime(workspace_root=home, exploration_root=home))
        saved, refused, _ = desk._saved(STATE)
        assert saved is None and "home folder" in refused["error"]
        assert not (home / "ub-run.yml").exists()

    def test_a_window_on_a_folder_in_home_reaches_only_that_folder(
            self, served, machine, tmp_path, monkeypatch):
        """Round 1 of the next round: a GUI opened on a folder directly in
        home puts new studies in home, and its routes reached every folder
        in home with them, a config and the files beside it included."""
        home = tmp_path / "h"
        opened = home / "proj"
        opened.mkdir(parents=True)
        notes = home / "Documents" / "notes"
        notes.mkdir(parents=True)
        (notes / "private.pdb").write_text("ATOM secret\n")
        (notes / "c.yml").write_text(machine.study.read_text())
        monkeypatch.setenv("HOME", str(home))
        desk = RemoteDesk(DashboardRuntime(workspace_root=opened, exploration_root=home))
        assert desk.roots == [opened.resolve()]
        assert desk.inside(str(notes / "c.yml")) is None
        said = desk.plan(str(notes / "c.yml"), "box")
        assert said["ok"] is False
        saved, refused, _ = desk._saved(STATE)
        assert saved is None and "home folder" in refused["error"]
        assert not (home / "ub-run.yml").exists()
        assert not _sent(machine)

    def test_a_window_whose_studies_go_in_home_says_so_before_any_plan(
            self, served, tmp_path, monkeypatch):
        """Round 1 of the next round: Plan the send was offered, and only
        once pressed said to open the GUI on a folder of its own, which it
        already was; it now says which folder to open, before anything."""
        home = tmp_path / "h"
        opened = home / "study"
        opened.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        desk = RemoteDesk(DashboardRuntime(workspace_root=opened, exploration_root=home))
        why = desk.machines()["not_built_here"]
        assert "your home folder" in why and "A config I have" in why
        assert f"`fastmdx gui --output {opened.resolve() / 'first'}`" in why
        saved, refused, _ = desk._saved(STATE)
        assert refused["error"] == why
        # Opened on the home folder itself: the folder above is not called
        # the home folder, and a folder two deeper is named.
        top = RemoteDesk(DashboardRuntime(workspace_root=home, exploration_root=home.parent))
        said = top.machines()["not_built_here"]
        assert "a folder above your home folder" in said and "deeper" not in said
        assert str(home.resolve() / "md" / "first") in said
        # Round 2: opened on a folder outside home whose parent is above it,
        # the folder to open is one inside it, not one moved into home.
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        outside = RemoteDesk(DashboardRuntime(workspace_root=scratch,
                                              exploration_root=Path("/")))
        assert str(scratch.resolve() / "first") in outside.machines()["not_built_here"]
        assert RemoteDesk(DashboardRuntime(workspace_root=opened / "first",
                                           exploration_root=opened)
                          ).machines()["not_built_here"] is None

    def test_a_relative_config_name_checked_elsewhere_is_refused(
            self, served, tmp_path, monkeypatch):
        """Round 1 of the next round: A config I have checks a relative
        name in the folder the GUI was started in, and the plan read it in
        the folder opened, so another file was planned than was checked."""
        address, machine = served
        started = tmp_path / "typed-here"
        started.mkdir()
        (started / "study.yml").write_text(machine.study.read_text())
        monkeypatch.chdir(started)
        status, said = _ask(address, "/api/remote/plan", {"config": "study.yml",
                                                          "machine": "box"})
        assert said["ok"] is False and "full path" in said["error"]
        assert said["code"] == "remote.config.read_elsewhere"
        assert str(started.resolve()) in said["error"]
        status, full = _ask(address, "/api/remote/plan", {"config": str(machine.study),
                                                          "machine": "box"})
        assert full["ok"], full
        assert not _sent(machine)

    def test_the_refusal_of_a_relative_config_names_each_folder_as_it_is(
            self, served, tmp_path, monkeypatch):
        """Round 3 of the last round: a name in a folder was said as checked
        from that folder, not the one the GUI was started in; a name going
        up a folder was said unnormalised; and one the plan would read
        outside the workspace was not said to be."""
        address, machine = served
        root = machine.study.parent.resolve()
        started = tmp_path / "typed-here"
        (started / "sub").mkdir(parents=True)
        (started / "sub" / "study.yml").write_text(machine.study.read_text())
        (root / "sub").mkdir()
        (root / "sub" / "study.yml").write_text(machine.study.read_text())
        monkeypatch.chdir(started)
        _, deeper = _ask(address, "/api/remote/plan", {"config": "sub/study.yml",
                                                       "machine": "box"})
        error = deeper["error"]
        assert error.startswith(
            f"Checked from the folder the GUI was started in ({started.resolve()}), "
            f"sub/study.yml is {started.resolve() / 'sub' / 'study.yml'}; a plan reads it "
            f"from the folder the GUI was opened on ({root}), as {root / 'sub' / 'study.yml'}.")
        monkeypatch.chdir(started / "sub")
        _, up = _ask(address, "/api/remote/plan", {"config": "../up/study.yml",
                                                   "machine": "box"})
        assert f"is {(started / 'up' / 'study.yml').resolve()};" in up["error"]
        assert (f"a plan reads a relative name only inside the folder the GUI "
                f"was opened on ({root}), and ../up/study.yml leads out of it.") in up["error"]
        assert up["code"] == "remote.config.read_elsewhere"
        # Round 1: started in the folder opened, the two readings are one,
        # and the refusal says only that a plan does not read it.
        monkeypatch.chdir(root)
        _, same = _ask(address, "/api/remote/plan", {"config": "../up/study.yml",
                                                     "machine": "box"})
        assert same["error"].startswith(
            f"A plan reads a relative name only inside the folder the GUI was "
            f"opened on ({root})")
        assert "Checked from" not in same["error"]
        # A name no file can have is refused as that, not as a folder gone.
        _, odd = _ask(address, "/api/remote/plan", {"config": "a\x00b.yml", "machine": "box"})
        assert odd["ok"] is False and "is gone" not in odd["error"]
        assert "is not a config file in" in odd["error"]
        assert not _sent(machine)

    def test_a_relative_config_is_refused_once_the_folder_started_in_is_gone(
            self, served, tmp_path, monkeypatch):
        """Round 3 of the last round: with the folder the GUI was started in
        removed, the name was let through to the plan, which read the
        folder opened, unchecked."""
        address, machine = served

        def gone():
            raise FileNotFoundError("No such file or directory")

        monkeypatch.setattr(Path, "cwd", staticmethod(gone))
        status, said = _ask(address, "/api/remote/plan", {"config": machine.study.name,
                                                          "machine": "box"})
        assert said["ok"] is False and said["code"] == "remote.config.read_elsewhere"
        assert "is gone" in said["error"] and "full path" in said["error"]
        assert not _sent(machine)

    def test_a_results_name_written_in_the_form_is_a_bare_name(self, machine, tmp_path):
        """Round 1 of the next round: the empty Results box was filled with
        the results folder's full path, where Run on this machine reads a
        bare name as the same folder."""
        root = machine.study.parent
        opened = root / "opened"
        opened.mkdir()
        desk = RemoteDesk(DashboardRuntime(workspace_root=opened, exploration_root=root))
        output = root.resolve() / "fastmdxplora_1UBQ_20261010"
        assert desk._results_name(output) == "fastmdxplora_1UBQ_20261010"
        assert desk._results_name(root.resolve() / "deep" / "x") == str(
            root.resolve() / "deep" / "x")

    def test_a_window_with_no_home_found_still_opens_and_sends_no_form_study(
            self, machine, tmp_path, monkeypatch):
        """Round 2 of the next round: with no home folder to be found (no
        HOME, no account entry) the remote routes raised, and the GUI built
        with them did not open."""
        root = machine.study.parent
        opened = root / "opened"
        opened.mkdir()

        def nowhere(cls=None):
            raise RuntimeError("Could not determine home directory.")

        def no_entry(uid):
            raise KeyError(uid)

        import pwd

        monkeypatch.setattr(Path, "home", classmethod(nowhere))
        monkeypatch.setattr(pwd, "getpwuid", no_entry)
        runtime = DashboardRuntime(workspace_root=opened, exploration_root=root)
        with _serving(runtime) as address:
            status, said = _ask(address, "/api/remote/machines")
        assert status == 200 and said["ok"], said
        # Round 3 of the last round: it named no remedy, and said "here" twice.
        assert "no home folder was found" in said["not_built_here"]
        assert "set `HOME` and start the GUI again" in said["not_built_here"]
        assert said["not_built_here"].count("here") == 0
        desk = RemoteDesk(runtime)
        assert desk.roots == [opened.resolve()]
        saved, refused, _ = desk._saved(STATE)
        assert saved is None and refused["error"] == said["not_built_here"]

    def test_a_folder_the_disk_reads_as_home_or_a_disk_s_top_is_left_out(
            self, machine, monkeypatch):
        """Round 2 of the next round: home was told by its path's text, so a
        name the disk reads as the home folder (letters in another case, a
        mount of it) and a disk's top were reached as the new-studies
        folder."""
        import os

        from fastmdxplora.remote import inputs

        root = machine.study.parent
        opened = root / "opened"
        opened.mkdir()
        real = os.path.samefile
        monkeypatch.setattr(inputs.os.path, "samefile", lambda a, b: (
            True if (str(a), str(b)) == (str(root.resolve()), str(inputs.Path.home().resolve()))
            else real(a, b)))
        desk = RemoteDesk(DashboardRuntime(workspace_root=opened, exploration_root=root))
        assert desk.roots == [opened.resolve()]
        assert desk.machines()["not_built_here"]
        top = RemoteDesk(DashboardRuntime(workspace_root=opened, exploration_root=Path("/")))
        assert top.roots == [opened.resolve()]
        assert "the top of the disk" in top.machines()["not_built_here"]

    def test_a_wrong_or_empty_home_does_not_let_the_account_s_home_in(
            self, machine, tmp_path, monkeypatch):
        """Round 2 of the last round: home was read from `HOME` alone, so a
        `HOME` naming another folder, or none, let the account's own home
        folder in as the folder new studies go in."""
        import pwd

        root = machine.study.parent
        home = root / "home"
        opened = home / "proj"
        opened.mkdir(parents=True)
        real = pwd.getpwuid(0)
        account = type(real)((real.pw_name, real.pw_passwd, real.pw_uid, real.pw_gid,
                              real.pw_gecos, str(home), real.pw_shell))
        monkeypatch.setattr(pwd, "getpwuid", lambda uid: account)
        for named in (str(tmp_path / "elsewhere"), "", "/", "relative"):
            monkeypatch.setenv("HOME", named)
            desk = RemoteDesk(DashboardRuntime(workspace_root=opened, exploration_root=home))
            assert desk.roots == [opened.resolve()], named
            assert "your home folder" in desk.machines()["not_built_here"]
            saved, refused, _ = desk._saved(STATE)
            assert saved is None and not (home / "ub-run.yml").exists()
            # Round 1: the folder to open is in the account's home, never
            # in a wrong HOME or under the disk's top.
            top = RemoteDesk(DashboardRuntime(workspace_root=home, exploration_root=root))
            said = top.machines()["not_built_here"]
            assert f"`fastmdx gui --output {home.resolve() / 'md' / 'first'}`" in said, named

    def test_the_command_line_keeps_the_account_s_home_out_whatever_home_says(
            self, tmp_path, monkeypatch):
        """Round 2: the account's home is kept out of a send from the command
        line too, where `HOME` names another folder."""
        import pwd

        from fastmdxplora.refusals import refusal_of
        from fastmdxplora.remote.inputs import gather_inputs

        home = tmp_path / "account"
        home.mkdir()
        (home / "top.pdb").write_text("ATOM\n")
        real = pwd.getpwuid(0)
        account = type(real)((real.pw_name, real.pw_passwd, real.pw_uid, real.pw_gid,
                              real.pw_gecos, str(home), real.pw_shell))
        monkeypatch.setattr(pwd, "getpwuid", lambda uid: account)
        monkeypatch.setenv("HOME", str(tmp_path / "elsewhere"))
        with pytest.raises(Exception) as caught:
            gather_inputs({"systems": [{"system": "top.pdb"}]}, home)
        assert refusal_of(caught.value).code == "remote.input.outside"

    def test_a_home_entry_that_cannot_be_read_opens_the_gui(self, machine, tmp_path,
                                                            monkeypatch):
        """Round 2: an account's home that is a loop of links raised out of
        the routes, and the GUI did not open."""
        import pwd

        loop = tmp_path / "loop"
        loop.symlink_to(loop)
        real = pwd.getpwuid(0)
        account = type(real)((real.pw_name, real.pw_passwd, real.pw_uid, real.pw_gid,
                              real.pw_gecos, str(loop), real.pw_shell))
        monkeypatch.setattr(pwd, "getpwuid", lambda uid: account)
        root = machine.study.parent
        desk = RemoteDesk(DashboardRuntime(workspace_root=root, exploration_root=root))
        assert desk.roots == [root.resolve()]

    def test_a_config_is_saved_only_where_the_routes_reach(self, machine, tmp_path):
        """Round 3 of the last round: the form's config was written in the
        window's folder for new studies without asking again whether the
        routes reach it."""
        root = machine.study.parent
        opened = root / "opened"
        opened.mkdir()
        runtime = DashboardRuntime(workspace_root=opened, exploration_root=root)
        desk = RemoteDesk(runtime)
        moved = tmp_path / "moved"
        moved.mkdir()
        runtime.exploration_root = moved
        saved, refused, _ = desk._saved(STATE)
        assert saved is None and "does not send from" in refused["error"]
        assert not list(moved.iterdir())

    def test_a_refusal_names_both_folders_the_routes_reach(self, machine, tmp_path,
                                                           monkeypatch):
        """Round 3 of the last round: refusals called the folder opened the
        workspace, though the routes reach the folder above it as well."""
        root = machine.study.parent.resolve()
        opened = root / "opened"
        opened.mkdir()
        desk = RemoteDesk(DashboardRuntime(workspace_root=opened, exploration_root=root))
        outside = tmp_path / "far" / "c.yml"
        outside.parent.mkdir()
        outside.write_text("output: x\n")
        said = desk.plan(str(outside), "box")
        assert f"the workspace ({opened}, and {root} where new studies go)" in said["error"]
        # And a file that would travel from outside both (a prepared study
        # beside the config's folder, say).
        from types import SimpleNamespace

        from fastmdxplora.remote import api

        far = tmp_path / "far" / "setup"
        planned = SimpleNamespace(inputs=SimpleNamespace(files={"setup": far}))
        monkeypatch.setattr(api, "plan_send", lambda *a, **k: planned)
        sending, refused = desk._planned(opened / "c.yml", "box", opened / "c")
        assert sending is None and refused["code"] == "remote.input.outside"
        assert f"the workspace ({opened}, and {root} where new studies go)" in refused["error"]

    def test_a_name_no_folder_can_have_is_refused(self, machine):
        root = machine.study.parent
        desk = RemoteDesk(DashboardRuntime(workspace_root=root, exploration_root=root))
        saved, refused, _ = desk._saved({**STATE, "output": str(root / "a\0b")})
        assert saved is None and refused["ok"] is False

    def test_the_config_is_saved_where_its_files_are_read_from(self, served):
        """Second review: a results folder named in a subfolder put the
        config there, and a file the study named by a relative path was
        no longer beside it. The window's results name is Run on this
        machine's: a folder beside the others it made."""
        address, machine = served
        root = machine.study.parent
        status, plan = _ask(address, "/api/remote/plan", {
            "state": {**STATE, "output": "newdir/deep"}, "machine": "box"})
        assert plan["ok"], plan
        assert plan["config"] == "newdir_deep.yml" and plan["results"] == "newdir_deep"
        assert (root / "newdir_deep.yml").is_file() and not (root / "newdir").exists()

    def test_a_file_named_relatively_is_sent_only_as_a_run_here_reads_it(
            self, machine, monkeypatch):
        """Third review: a GUI opened on a folder starts Run on this machine
        in the folder above it, so a study naming mine.pdb would have read
        that folder's mine.pdb here and sent the opened folder's. The
        window's routes read it where Run on this machine does; a desk
        given a folder alone refuses the name."""
        from fastmdxplora.gui.server import start_dashboard_session
        from fastmdxplora.remote import api
        from fastmdxplora.remote import send as sending

        parent = machine.study.parent
        opened = parent / "opened"
        opened.mkdir()
        (parent / "mine.pdb").write_text("ATOM  parent\n")
        (opened / "mine.pdb").write_text("ATOM\n")
        monkeypatch.setattr(api, "_link", lambda name, transport: machine.transport())
        monkeypatch.setattr(api, "this_code", lambda: RELEASE)
        monkeypatch.setattr(sending, "this_code", lambda: RELEASE)
        state = {"system": "mine.pdb", "include_phase": ["setup"], "output": "where"}
        session = start_dashboard_session(output=str(opened), host="127.0.0.1", port=0)
        try:
            address = session.url.split("//", 1)[1].rstrip("/")
            status, plan = _ask(address, "/api/remote/plan", {"state": state,
                                                              "machine": "box"})
            assert plan["ok"], plan
            assert [t["from"] for t in plan["travels"]] == [str(parent / "mine.pdb")]
            assert (parent / "where.yml").is_file() and not list(opened.glob("where*.yml"))
            _ask(address, "/api/remote/forget", {"plan": plan["plan"]})
        finally:
            session.server.shutdown()
        saved, said, _ = RemoteDesk(opened)._saved(state)
        assert saved is None and said["code"] == "remote.input.outside"
        assert said["error"].startswith("Run on this machine would read 'mine.pdb' from")
        assert not any(c.startswith("mkdir") for c in machine.commands)

    def test_a_plan_let_go_takes_back_the_config_written_for_it(self, served):
        """Third review: Not now, or a plan taken away as the study changed,
        left its config behind, one more for each blank results name."""
        address, machine = served
        root = machine.study.parent
        status, plan = _ask(address, "/api/remote/plan", {"state": STATE, "machine": "box"})
        assert plan["results_path"] == str((root / "ub-run").resolve())
        status, again = _ask(address, "/api/remote/plan", {"state": STATE, "machine": "box"})
        # Kept while another plan is of it, and taken back with the last.
        assert _ask(address, "/api/remote/forget", {"plan": plan["plan"]})[1]["ok"]
        assert (root / "ub-run.yml").is_file()
        _ask(address, "/api/remote/forget", {"plan": again["plan"]})
        assert not (root / "ub-run.yml").exists()
        status, other = _ask(address, "/api/remote/plan", {
            "state": {**STATE, "output": "other"}, "machine": "box"})
        _ask(address, "/api/remote/forget", {"plan": other["plan"]})
        assert not (root / "other.yml").exists()
        status, sent = _ask(address, "/api/remote/send", {"plan": other["plan"]})
        assert sent["ok"] is False and not _sent(machine)

    def test_a_config_changed_by_hand_or_not_written_for_it_is_kept(self, served):
        address, machine = served
        root = machine.study.parent
        status, plan = _ask(address, "/api/remote/plan", {"state": STATE, "machine": "box"})
        (root / "ub-run.yml").write_text((root / "ub-run.yml").read_text() + "# mine\n")
        _ask(address, "/api/remote/forget", {"plan": plan["plan"]})
        assert (root / "ub-run.yml").read_text().endswith("# mine\n")
        planned = _planned(address)
        _ask(address, "/api/remote/forget", {"plan": planned["plan"]})
        assert machine.study.is_file()
        assert _ask(address, "/api/remote/forget", {"plan": "nothing"})[1] == {"ok": True}

    def test_a_config_another_plan_sent_is_kept_when_this_one_is_let_go(self, served):
        """First review of 1796-1797: two tabs planned one study, the second
        sent it, and Not now in the first deleted the config it was sent as."""
        address, machine = served
        root = machine.study.parent
        status, first = _ask(address, "/api/remote/plan", {"state": STATE, "machine": "box"})
        status, second = _ask(address, "/api/remote/plan", {"state": STATE, "machine": "box"})
        status, sent = _ask(address, "/api/remote/send", {"plan": second["plan"]})
        assert sent["ok"], sent
        _ask(address, "/api/remote/forget", {"plan": first["plan"]})
        assert (root / "ub-run.yml").is_file()
        travels._until_finished(machine, "ub-run")

    def test_a_send_refused_takes_back_the_config_written_for_it(self, served):
        address, machine = served
        root = machine.study.parent
        (root / "mine.pdb").write_text("ATOM  1\n")
        status, plan = _ask(address, "/api/remote/plan", {
            "state": {**STATE, "system": str(root / "mine.pdb")}, "machine": "box"})
        assert plan["ok"], plan
        (root / "mine.pdb").write_text("ATOM  2\n")  # the same size, not the same file
        status, sent = _ask(address, "/api/remote/send", {"plan": plan["plan"]})
        assert sent["code"] == "remote.send.unconfirmed"
        assert not (root / "ub-run.yml").exists() and not _sent(machine)

    def test_a_pdb_id_or_a_word_like_a_folder_is_not_a_file(self, machine, monkeypatch):
        """First review of 1796-1797: a GUI opened on a folder named after
        its protein refused the protein's PDB ID as a file named relatively."""
        from fastmdxplora.gui.server import start_dashboard_session
        from fastmdxplora.remote import api
        from fastmdxplora.remote import send as sending

        parent = machine.study.parent
        opened = parent / "1ubq"
        opened.mkdir()
        (parent / "mine.pdb").write_text("ATOM\n")
        monkeypatch.setattr(api, "_link", lambda name, transport: machine.transport())
        monkeypatch.setattr(api, "this_code", lambda: RELEASE)
        monkeypatch.setattr(sending, "this_code", lambda: RELEASE)
        session = start_dashboard_session(output=str(opened), host="127.0.0.1", port=0)
        try:
            address = session.url.split("//", 1)[1].rstrip("/")
            status, plan = _ask(address, "/api/remote/plan", {"state": {
                "system": "1ubq", "include_phase": ["setup"], "output": "where"},
                "machine": "box"})
            assert plan["ok"], plan
            assert plan["fetched_there"] == ["1ubq"]
            # The study's results, and its config, beside the folder opened,
            # where Run on this machine puts a new study; never inside it.
            assert plan["results_path"] == str((parent / "where").resolve())
            assert not list(opened.iterdir())
        finally:
            session.server.shutdown()
        assert not _sent(machine)


class TestTakingBack:
    def test_only_the_file_written_is_taken_back(self, tmp_path):
        import os

        from fastmdxplora.gui.remote_routes import _sha_of, _taken_back

        written = tmp_path / "a.yml"
        written.write_text("x: 1\n")
        sha = _sha_of(written)
        # Put in its place with the same words: another file, kept.
        other = tmp_path / "b.yml"
        other.write_text("x: 1\n")
        os.replace(other, written)
        _taken_back(written, "0" * 64)
        assert written.is_file()
        sha = _sha_of(written)
        _taken_back(written, sha)
        assert not written.exists()

    def test_a_pipe_in_its_place_is_never_waited_on(self, tmp_path):
        import os

        from fastmdxplora.gui.remote_routes import _sha_of, _taken_back

        pipe = tmp_path / "a.yml"
        os.mkfifo(pipe)
        assert _sha_of(pipe) == ""
        _taken_back(pipe, "0" * 64)
        assert pipe.exists()


class TestTabsAtOnce:
    """Second review of 1796-1798: a plan let go or refused in one tab while
    another tab's send of the same study was still asking its machine took
    back the config that send was sending."""

    @pytest.fixture
    def desk(self, machine, monkeypatch):
        from fastmdxplora.remote import api
        from fastmdxplora.remote import send as sending

        monkeypatch.setattr(api, "_link", lambda name, transport: machine.transport())
        monkeypatch.setattr(api, "this_code", lambda: RELEASE)
        monkeypatch.setattr(sending, "this_code", lambda: RELEASE)
        real = sending.run_here
        monkeypatch.setattr(sending, "run_here", lambda command, runner=None, **more:
                            real(command, runner=machine.local, **more))
        root = machine.study.parent
        return RemoteDesk(DashboardRuntime(workspace_root=root, exploration_root=root)), root

    @staticmethod
    def _held(desk, name):
        """``desk.<name>`` made to wait, once, until let through."""
        asked, through = threading.Event(), threading.Event()
        real = getattr(desk, name)

        def waiting(*args, **kwargs):
            asked.set()
            through.wait(30)
            return real(*args, **kwargs)

        setattr(desk, name, waiting)
        return asked, through, lambda: setattr(desk, name, real)

    def test_a_plan_let_go_while_another_is_sent_leaves_its_config(self, desk, machine):
        desk, root = desk
        first = desk.plan(None, "box", state=STATE)
        second = desk.plan(None, "box", state=STATE)
        asked, through, undo = self._held(desk, "_planned")
        said = {}
        sending = threading.Thread(target=lambda: said.update(desk.send(second["plan"])))
        sending.start()
        assert asked.wait(30)
        undo()
        desk.forget(first["plan"])
        through.set()
        sending.join(60)
        assert said["ok"], said
        assert (root / "ub-run.yml").is_file()
        travels._until_finished(machine, "ub-run")

    def test_a_plan_refused_while_another_of_it_is_kept_leaves_its_config(
            self, desk, machine):
        desk, root = desk
        (root / "ub-run").mkdir()  # in use: the first plan is refused
        (root / "ub-run" / "kept.txt").write_text("x")
        asked, through, undo = self._held(desk, "_plan")
        said = {}
        planning = threading.Thread(
            target=lambda: said.update(desk.plan(None, "box", state=STATE)))
        planning.start()
        assert asked.wait(30)
        undo()
        (root / "ub-run" / "kept.txt").rename(root / "kept.txt")
        kept = desk.plan(None, "box", state=STATE)
        assert kept["ok"], kept
        (root / "kept.txt").rename(root / "ub-run" / "kept.txt")
        through.set()
        planning.join(60)
        assert said["ok"] is False
        assert (root / "ub-run.yml").is_file()
        desk.forget(kept["plan"])
        assert not (root / "ub-run.yml").exists()
        assert not _sent(machine)

    def test_a_plan_dropped_by_age_takes_back_its_config(self, desk, monkeypatch):
        from fastmdxplora.gui import remote_routes

        desk, root = desk
        old = desk.plan(None, "box", state=STATE)
        assert old["ok"] and (root / "ub-run.yml").is_file()
        monkeypatch.setattr(remote_routes, "PLAN_KEPT_S", -1)
        said = desk.send("not-a-plan")
        assert said["code"] == "remote.send.unconfirmed"
        assert not (root / "ub-run.yml").exists()

    def test_a_phase_or_an_analysis_is_a_word_in_a_study_s_folder(self, desk):
        """Second review of 1796-1798: a GUI opened on a finished study's
        folder refused every plan, its `setup` folder read for the phase."""
        desk, root = desk
        (root / "setup").mkdir()
        (root / "rmsd").mkdir()
        said = desk.plan(None, "box", state={
            **STATE, "include_phase": ["setup", "simulation", "analysis"],
            "analysis": {"include": ["rmsd"]}})
        assert said["ok"], said
        assert said["travels"] == []

    def test_a_config_file_plan_refused_takes_back_a_config_let_go_meanwhile(
            self, desk, machine):
        """Round 1 of the next round: the form's plan let go while its
        config was planned as a config file, and that plan then refused,
        left the config behind."""
        desk, root = desk
        first = desk.plan(None, "box", state=STATE)
        asked, through, undo = self._held(desk, "_plan")
        said = {}
        planning = threading.Thread(target=lambda: said.update(
            desk.plan(str(root / "ub-run.yml"), "gpu-box")))
        planning.start()
        assert asked.wait(30)
        undo()
        desk.forget(first["plan"])
        assert (root / "ub-run.yml").is_file()
        through.set()
        planning.join(60)
        assert said["ok"] is False
        assert not (root / "ub-run.yml").exists()

    def test_a_config_file_planned_holds_the_file_the_form_wrote(self, desk, machine):
        """Round 3 of 1796-1799: the form's config planned as a config file
        was taken back by the form's Not now while that plan was made."""
        desk, root = desk
        first = desk.plan(None, "box", state=STATE)
        asked, through, undo = self._held(desk, "_planned")
        said = {}
        planning = threading.Thread(target=lambda: said.update(
            desk.plan(str(root / "ub-run.yml"), "box")))
        planning.start()
        assert asked.wait(30)
        undo()
        desk.forget(first["plan"])
        assert (root / "ub-run.yml").is_file()
        through.set()
        planning.join(60)
        assert said["ok"], said
        desk.forget(said["plan"])
        assert not (root / "ub-run.yml").exists()
