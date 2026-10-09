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

    def test_a_pipe_is_never_opened(self, tmp_path):
        """Second review: a pipe named as an input was opened to be read,
        which waits for a writer that never comes, and the plan with it."""
        import os
        import threading

        pipe = tmp_path / "pipe.pdb"
        os.mkfifo(pipe)
        said = []
        reader = threading.Thread(target=lambda: said.append(fingerprint_of(pipe)),
                                  daemon=True)
        reader.start()
        reader.join(5)
        if not said:
            with open(pipe, "wb"):
                pass
            reader.join(5)
            pytest.fail("the pipe was opened")
        assert said[0][0] == 0

    def test_one_entry_unread_leaves_the_rest_of_a_folder_told_apart(
            self, tmp_path, monkeypatch):
        from fastmdxplora.remote import inputs

        folder = tmp_path / "prepared"
        folder.mkdir()
        (folder / "state.xml").write_text("<State/>\n")
        (folder / "locked.xml").write_text("x")
        real = inputs._file_print

        def refused(path, left):
            if path.name == "locked.xml":
                raise PermissionError(path)
            return real(path, left)

        monkeypatch.setattr(inputs, "_file_print", refused)
        before = fingerprint_of(folder)
        (folder / "state.xml").write_text("<Other/>\n")
        assert fingerprint_of(folder)[1] != before[1]

    def test_past_what_is_read_a_file_is_known_by_what_it_is(self, tmp_path, monkeypatch):
        """Past the bytes read for one send, a file is not read, and a copy
        that kept its times is still told apart (its ctime is new)."""
        import os
        import shutil
        import time

        from fastmdxplora.remote import inputs

        monkeypatch.setattr(inputs, "READ_IN_ALL_BYTES", 4)
        file = tmp_path / "big.dcd"
        file.write_bytes(b"0123456789")
        before = fingerprint_of(file)
        time.sleep(0.01)
        twin = tmp_path / "twin.dcd"
        twin.write_bytes(b"9876543210")
        shutil.copystat(file, twin)
        os.replace(twin, file)
        after = fingerprint_of(file)
        assert before[0] == after[0] == 10 and before[1] != after[1]


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
