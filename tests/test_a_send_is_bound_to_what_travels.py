"""A send is bound to the contents of what travels, not only its sizes.

The person agrees to a plan that names each file sent with it. A file
rewritten after that with the same number of bytes, an empty file added to a
folder that travels, or two files of one size swapped in it, is something
else: the GUI's send refuses it, and an AI app's yes, where the file changed
while the person was asked, sends nothing.
"""

from __future__ import annotations

import pytest

from fastmdxplora.remote.inputs import fingerprint_of, size_of
from tests import test_remote_from_every_interface as every
from tests import test_the_gui_reaches_your_machines as gui
from tests.test_the_gui_reaches_your_machines import _ask, _planned, _sent

machine = gui.machine
machine_path = gui.machine_path
served = gui.served
app = every.app


class TestAFingerprint:
    def test_the_same_bytes_give_the_same_print(self, tmp_path):
        (tmp_path / "a.pdb").write_text("ATOM 1\n")
        first = fingerprint_of(tmp_path / "a.pdb")
        assert first == fingerprint_of(tmp_path / "a.pdb")
        assert first[0] == size_of(tmp_path / "a.pdb") == 7

    def test_a_file_changed_at_the_same_size_is_told_apart(self, tmp_path):
        file = tmp_path / "a.pdb"
        file.write_text("ATOM 1\n")
        before = fingerprint_of(file)
        file.write_text("ATOM 2\n")
        after = fingerprint_of(file)
        assert before[0] == after[0] and before[1] != after[1]

    @pytest.mark.parametrize("change", ["empty file", "renamed", "swapped"])
    def test_a_folder_changed_at_the_same_size_is_told_apart(self, tmp_path, change):
        folder = tmp_path / "prepared"
        folder.mkdir()
        (folder / "system.xml").write_text("<System/>\n")
        (folder / "state.xml").write_text("<State/>\n")
        before = fingerprint_of(folder)
        if change == "empty file":
            (folder / "notes.txt").write_text("")
        elif change == "renamed":
            (folder / "state.xml").rename(folder / "other.xml")
        else:
            (folder / "system.xml").write_text("<State/>\n")
            (folder / "state.xml").write_text("<System/>\n")
        after = fingerprint_of(folder)
        assert before[0] == after[0] == size_of(folder)
        assert before[1] != after[1]

    def test_what_cannot_be_read_gives_no_size(self, tmp_path):
        assert fingerprint_of(tmp_path / "missing.pdb")[0] == 0


class TestTheGui:
    def test_a_file_rewritten_at_the_same_size_is_not_sent(self, served):
        address, machine = served
        plan = _planned(address)
        top = machine.study.parent / "top.pdb"
        assert plan["travels"][0]["bytes"] == top.stat().st_size
        top.write_bytes(bytes(b ^ 1 for b in top.read_bytes()))
        status, said = _ask(address, "/api/remote/send", {"plan": plan["plan"]})
        assert said["ok"] is False and said["code"] == "remote.send.unconfirmed"
        assert not _sent(machine)

    def test_a_fetch_is_of_the_size_there_now(self, served):
        """Sizes shown a moment ago are asked again: results grown since
        are not brought under the size the person was shown."""
        from pathlib import Path

        from fastmdxplora.remote.jobs import load_job
        from tests import test_a_study_travels_and_comes_back as travels

        address, machine = served
        plan = _planned(address)
        _ask(address, "/api/remote/send", {"plan": plan["plan"]})
        travels._until_finished(machine, "study")
        status, sizes = _ask(address, "/api/remote/fetch-sizes?job=study")
        shown = sizes["bringing"]["without"]
        (Path(load_job("study").remote_dir) / "run" / "grown.txt").write_text("x" * 5000)
        status, said = _ask(address, "/api/remote/fetch", {"job": "study",
                                                           "bringing": shown})
        assert said["ok"] is False and said["code"] == "remote.fetch.unconfirmed"
        assert said["bringing"] > shown
        assert not (machine.study.parent / "study" / "manifest.json").exists()

    def test_a_config_not_in_utf_8_is_said_not_failed(self, served):
        address, machine = served
        (machine.study.parent / "bad.yml").write_bytes(b"\xff\xfe output: x\n")
        status, said = _ask(address, "/api/remote/plan", {"config": "bad.yml",
                                                          "machine": "box"})
        assert status == 200 and said["ok"] is False
        assert "bad.yml could not be read" in said["error"]
        assert machine.commands == []

    def test_a_hosted_gui_finds_no_other_route_under_remote(self, machine):
        from fastmdxplora.gui.remote_routes import RemoteDesk

        desk = RemoteDesk(machine.study.parent, hosted=True)
        assert desk.get("/api/remote/nothing", {}) is None
        assert desk.post("/api/remote/nothing", {}) is None


class TestAnAIApp:
    def test_a_file_changed_while_the_person_was_asked_is_not_sent(
            self, app, monkeypatch):
        from fastmdxplora.mcp import remote_tools

        structure = app.root / "ghg.pdb"
        asked = []

        def changed_then_yes(ctx, key, message, bound_to):
            asked.append(message)
            structure.write_bytes(bytes(b ^ 1 for b in structure.read_bytes()))
            return True

        monkeypatch.setattr(remote_tools, "_went_ahead", changed_then_yes)
        first, _ = every._start(app)
        assert asked and "ghg.pdb" in asked[0]
        assert first.get("isError")
        said = every._text(first)
        assert "changed while the person was asked" in said
        assert not every._sent(app)
