"""A study sent to another machine takes only its own folder's files.

Every string in a config that names an existing file used to travel, from
anywhere on the computer, because the person wrote the config. A config an
AI model wrote, or one copied from elsewhere, naming ``~/.ssh/id_ed25519``
would have copied the key to the machine. Now only files in the folder
holding the config travel, links resolved first, and a folder is refused if
a link in it leads out, since the copy follows links. A prepared study
beside the folder, named by ``setup_from`` or ``resume_from``, is the one
exception, when its manifest reads as a study's.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from fastmdxplora.refusals import refusal_of
from fastmdxplora.remote.inputs import gather_inputs
from fastmdxplora.remote.send import prepare, send
from tests import test_a_study_travels_and_comes_back as travels
from tests.test_a_study_travels_and_comes_back import RELEASE, _send

# The stand-in machine of the sending tests, as fixtures here.
machine = travels.machine
machine_path = travels.machine_path

needs_links = pytest.mark.skipif(os.name == "nt", reason="links need privileges on Windows")


def _refused(config, base) -> dict:
    with pytest.raises(ValueError) as caught:
        gather_inputs(config, base)
    found = refusal_of(caught.value)
    assert found.code == "remote.input.outside"
    return {"message": found.message, **found.details}


@pytest.fixture
def folders(tmp_path):
    study = tmp_path / "study"
    study.mkdir()
    (study / "top.pdb").write_text("ATOM\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "id_ed25519").write_text("secret\n")
    return study, elsewhere


class TestOutsideIsRefused:
    def test_an_absolute_path_outside(self, folders):
        study, elsewhere = folders
        said = _refused({"setup": {"ligand": str(elsewhere / "id_ed25519")}}, study)
        assert said["where"] == "setup.ligand"
        assert "outside the study's folder" in said["message"]
        assert "Copy it into that folder" in said["message"]

    def test_a_path_climbing_out(self, folders):
        study, _ = folders
        said = _refused({"systems": [{"system": "../elsewhere/id_ed25519"}]}, study)
        assert said["where"] == "systems[0].system"
        assert "PDB identifier" in said["message"]

    def test_a_path_from_the_home_folder(self, folders, monkeypatch):
        study, elsewhere = folders
        monkeypatch.setenv("HOME", str(elsewhere))
        _refused({"analysis": {"topology": "~/id_ed25519"}}, study)

    def test_the_whole_computer(self, folders):
        study, _ = folders
        _refused({"analysis": {"trajectory": "/"}}, study)

    @needs_links
    def test_a_link_in_the_folder_leading_out(self, folders):
        study, elsewhere = folders
        (study / "lig.sdf").symlink_to(elsewhere / "id_ed25519")
        said = _refused({"setup": {"ligand": "lig.sdf"}}, study)
        assert str((elsewhere / "id_ed25519").resolve()) in said["message"]

    @needs_links
    def test_a_folder_holding_a_link_leading_out(self, folders):
        study, elsewhere = folders
        (study / "ff").mkdir()
        (study / "ff" / "key").symlink_to(elsewhere / "id_ed25519")
        said = _refused({"setup": {"forcefield_files": ["ff"]}}, study)
        assert "is a link leading out of" in said["message"]
        assert str(study / "ff" / "key") in said["message"]

    @needs_links
    def test_a_link_leading_nowhere(self, folders):
        study, _ = folders
        (study / "ff").mkdir()
        (study / "ff" / "gone").symlink_to(study / "missing")
        _refused({"setup": {"forcefield_files": ["ff"]}}, study)


class TestWhatTravels:
    def test_files_in_the_folder_and_below(self, folders):
        study, _ = folders
        (study / "ligands").mkdir()
        (study / "ligands" / "a.sdf").write_text("x")
        found = gather_inputs({"systems": [{"system": "top.pdb"}],
                               "setup": {"ligand": "ligands/a.sdf"}}, study)
        assert set(found.files) == {"top.pdb", "a.sdf"}

    @needs_links
    def test_a_link_staying_inside(self, folders):
        study, _ = folders
        (study / "ff").mkdir()
        (study / "ff" / "same").symlink_to(study / "top.pdb")
        (study / "alias.pdb").symlink_to(study / "top.pdb")
        found = gather_inputs({"setup": {"forcefield_files": ["ff"]},
                               "systems": [{"system": "alias.pdb"}]}, study)
        assert found.files["top.pdb"] == (study / "top.pdb").resolve()

    def test_where_results_go_is_not_read(self, folders):
        study, elsewhere = folders
        found = gather_inputs({"output": str(elsewhere)}, study)
        assert found.files == {} and found.config["output"] == str(elsewhere)


def _prepared(where: Path, manifest: bool = True) -> Path:
    (where / "setup").mkdir(parents=True)
    (where / "setup" / "system.xml").write_text("<system/>")
    if manifest:
        (where / "manifest.json").write_text(json.dumps({"version": "1.0", "phases": []}))
    return where


class TestAPreparedStudyBeside:
    def test_setup_from_a_study_beside_travels(self, folders):
        study, _ = folders
        reference = _prepared(study.parent / "reference")
        found = gather_inputs({"simulation": {"setup_from": "../reference"}}, study)
        assert found.files == {"reference": reference.resolve()}
        assert found.config["simulation"]["setup_from"] == "inputs/reference"

    def test_its_setup_folder_named_directly_travels(self, folders):
        study, _ = folders
        reference = _prepared(study.parent / "reference")
        found = gather_inputs({"simulation": {"setup_from": "../reference/setup"}}, study)
        assert found.files == {"setup": (reference / "setup").resolve()}

    def test_resume_from_a_study_beside_travels(self, folders):
        study, _ = folders
        _prepared(study.parent / "reference")
        found = gather_inputs({"simulation": {"resume_from": "../reference"}}, study)
        assert list(found.files) == ["reference"]

    def test_a_folder_without_a_manifest_is_refused(self, folders):
        study, _ = folders
        _prepared(study.parent / "reference", manifest=False)
        _refused({"simulation": {"setup_from": "../reference"}}, study)

    def test_a_manifest_another_program_wrote_is_refused(self, folders):
        study, _ = folders
        reference = _prepared(study.parent / "reference", manifest=False)
        (reference / "manifest.json").write_text(json.dumps({"name": "a web app"}))
        _refused({"simulation": {"setup_from": "../reference"}}, study)

    def test_a_study_far_from_the_folder_is_refused(self, folders, tmp_path_factory):
        study, _ = folders
        far = _prepared(tmp_path_factory.mktemp("far") / "reference")
        _refused({"simulation": {"setup_from": str(far)}}, study)

    def test_a_study_further_down_beside_it_travels(self, folders):
        study, _ = folders
        reference = _prepared(study.parent / "runs" / "reference")
        found = gather_inputs({"simulation": {"setup_from": "../runs/reference"}}, study)
        assert found.files == {"reference": reference.resolve()}

    def test_the_name_alone_elsewhere_is_not_the_setting(self, folders):
        study, _ = folders
        _prepared(study.parent / "reference")
        _refused({"notes": {"setup_from": "../reference"}}, study)

    def test_a_manifest_that_is_not_one_is_refused(self, folders):
        study, _ = folders
        reference = _prepared(study.parent / "reference", manifest=False)
        (reference / "manifest.json").write_text("not json")
        _refused({"simulation": {"setup_from": "../reference"}}, study)

    def test_a_checkpoint_file_beside_is_refused(self, folders):
        study, _ = folders
        reference = _prepared(study.parent / "reference")
        (reference / "state.chk").write_text("x")
        _refused({"simulation": {"resume_from": "../reference/state.chk"}}, study)

    def test_only_those_settings_may_name_one(self, folders):
        study, _ = folders
        _prepared(study.parent / "reference")
        _refused({"analysis": {"trajectory": "../reference"}}, study)

    @needs_links
    def test_a_link_in_it_leading_out_of_it_is_refused(self, folders):
        study, elsewhere = folders
        reference = _prepared(study.parent / "reference")
        (reference / "setup" / "key").symlink_to(elsewhere / "id_ed25519")
        said = _refused({"simulation": {"setup_from": "../reference"}}, study)
        assert said["folder"] == str(reference.resolve())


# ---------------------------------------------------------------------------
# On the way to a machine
# ---------------------------------------------------------------------------
def test_a_send_naming_a_file_outside_reaches_no_machine(machine, tmp_path):
    secret = tmp_path / "id_ed25519"
    secret.write_text("secret\n")
    machine.study.write_text(f"systems:\n  - system: top.pdb\nanalysis:\n  topology: {secret}\n")
    with pytest.raises(ValueError) as caught:
        prepare(machine.study, "box", output=str(machine.back), code=RELEASE,
                transport=machine.transport())
    assert refusal_of(caught.value).code == "remote.input.outside"
    assert machine.commands == []


@needs_links
def test_a_link_made_after_the_check_is_not_followed(machine, tmp_path):
    (machine.study.parent / "ff").mkdir()
    (machine.study.parent / "ff" / "a.xml").write_text("<ff/>")
    machine.study.write_text("systems:\n  - system: top.pdb\n"
                             "setup:\n  forcefield_files: [ff]\n")
    sending = prepare(machine.study, "box", output=str(machine.back), code=RELEASE,
                      transport=machine.transport())
    secret = tmp_path / "id_ed25519"
    secret.write_text("secret\n")
    (machine.study.parent / "ff" / "key").symlink_to(secret)
    with pytest.raises(ValueError) as caught:
        send(sending, transport=machine.transport(), local_runner=machine.local,
             code=RELEASE)
    assert refusal_of(caught.value).code == "remote.input.outside"
    assert not any("mkdir" in c for c in machine.commands)


def test_a_study_of_its_own_files_is_sent_and_sizes_are_shown(machine):
    from fastmdxplora.remote.send import describe_sending

    sending = prepare(machine.study, "box", output=str(machine.back), code=RELEASE,
                      transport=machine.transport())
    assert any(line.strip().startswith("inputs") and "top.pdb (0 kB)" in line
               for line in describe_sending(sending))
    job = _send(machine)
    assert (Path(job.remote_dir) / "inputs" / "top.pdb").is_file()
    travels._until_finished(machine, job.name)
    shutil.rmtree(job.remote_dir)


@needs_links
class TestLinksFollowedAsTheCopyFollowsThem:
    def test_a_link_inside_to_a_folder_whose_link_leads_out(self, folders):
        study, elsewhere = folders
        (study / "ff").mkdir()
        (study / "other").mkdir()
        (study / "ff" / "sub").symlink_to(study / "other")
        (study / "other" / "key").symlink_to(elsewhere / "id_ed25519")
        said = _refused({"setup": {"forcefield_files": ["ff"]}}, study)
        assert "key" in said["message"]

    def test_a_link_back_up_to_the_folder_holding_a_link_out(self, folders):
        study, elsewhere = folders
        (study / "ff").mkdir()
        (study / "ff" / "up").symlink_to(study)
        (study / "loose").symlink_to(elsewhere / "id_ed25519")
        _refused({"setup": {"forcefield_files": ["ff"]}}, study)

    def test_a_link_into_itself_is_refused_not_followed_for_ever(self, folders):
        study, _ = folders
        (study / "ff").mkdir()
        (study / "ff" / "again").symlink_to(study / "ff")
        _refused({"setup": {"forcefield_files": ["ff"]}}, study)

    def test_a_folder_linked_twice_inside_travels(self, folders):
        from fastmdxplora.remote.inputs import size_of

        study, _ = folders
        (study / "ff").mkdir()
        (study / "data").mkdir()
        (study / "data" / "a.xml").write_text("x" * 2000)
        (study / "ff" / "one").symlink_to(study / "data")
        (study / "ff" / "two").symlink_to(study / "data")
        found = gather_inputs({"setup": {"forcefield_files": ["ff"]}}, study)
        assert list(found.files) == ["ff"]
        # As much as the copy sends: the file twice, through each link.
        assert size_of(found.files["ff"]) == 4000
