"""A finished study is packed as one file another FastMDXplora opens.

`fastmdx report --share FILE` packs a finished study as an RO-Crate: its
files as its folder lays them out and `ro-crate-metadata.json`, the packing
list, with each file's role, size and SHA-256. Its records name files by
the path they were written at, so the study's folder, any home folder and
the computer's name are said as placeholders, and looked for again before
the archive is written. What the person keeps for themselves (the Agent's
conversations, tags and notes) and the record of the process that ran it
are never in it.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from fastmdxplora.sharing import HOME, HOST, PROFILE, STUDY, ShareRefused
from fastmdxplora.sharing.crate import METADATA, check
from fastmdxplora.sharing.pack import share_study

AUTHORS = [("Adekunle Aina", "0000-0002-8215-7452")]
ELSEWHERE = "/home/someone/runs/1abc"
COMPUTER = "labbox07"


def _study(root: Path, *, phase_status: str = "ok") -> Path:
    """A finished study as FastMDXplora lays one out, its records written on
    another computer (``ELSEWHERE``, ``COMPUTER``) before it was moved here."""
    md = pytest.importorskip("mdtraj")
    import numpy as np

    for folder in ("setup", "simulation", "analysis/rmsd", "report", "agent/conversations",
                   "simulation/live_frames", "previous/analysis"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    topology = md.Topology()
    residue = topology.add_residue("ALA", topology.add_chain())
    for name in ("N", "CA", "C"):
        topology.add_atom(name, md.element.carbon, residue)
    xyz = np.zeros((4, 3, 3), dtype=np.float32)
    xyz[:, 1, 1] = 0.15
    xyz[:, 2, 2] = 0.15
    trajectory = md.Trajectory(xyz, topology)
    trajectory[0].save_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(root / "simulation" / "production.dcd"))
    trajectory[0].save_pdb(str(root / "setup" / "input.pdb"))
    phases = [{"name": name, "status": phase_status if name == "report" else "ok",
               "output_dir": f"{ELSEWHERE}/{name}",
               "started_at": "2026-10-07T01:00:00+00:00",
               "finished_at": "2026-10-07T02:00:00+00:00",
               "produced_by": {"version": "2.5.9", "host": COMPUTER}}
              for name in ("setup", "simulation", "analysis", "report")]
    (root / "manifest.json").write_text(json.dumps({
        "tool": "FastMDXplora", "version": "2.5.9", "system": "1ABC",
        "output_dir": ELSEWHERE, "phases": phases}), encoding="utf-8")
    (root / "resolved_config.yml").write_text(
        f"systems:\n- system: 1ABC\noutput: {ELSEWHERE}\nsetup:\n  ligand: "
        f"/home/someone/ligands/x.sdf\n", encoding="utf-8")
    (root / "simulation" / "simulation_parameters.json").write_text(json.dumps({
        "parameters": {"timestep_fs": 2.0, "integrator": "langevin_middle"},
        "resolved": {"trajectory_interval_steps": 500, "production_steps": 2000}}),
        encoding="utf-8")
    (root / "simulation" / "live_status.json").write_text(json.dumps({
        "status": "completed", "host": COMPUTER}), encoding="utf-8")
    (root / "analysis" / "analysis_manifest.json").write_text(json.dumps({
        "trajectory_input": f"{ELSEWHERE}/simulation/production.dcd"}), encoding="utf-8")
    (root / "analysis" / "rmsd" / "rmsd.dat").write_text("0 0.1\n1 0.2\n", encoding="utf-8")
    (root / "analysis" / "rmsd" / "rmsd.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(32))
    (root / "analysis" / "rmsd" / "rmsd.svg").write_text("<svg/>", encoding="utf-8")
    (root / "report" / "report.md").write_text(
        "# The 1ABC study\n\n## Summary\n\nThe system was simulated for 4 ps.\n\n"
        f"## Reproducibility\n\n- **Output directory**: `{ELSEWHERE}`\n- on {COMPUTER}\n",
        encoding="utf-8")
    # Never shared, whatever is asked.
    (root / "agent" / "conversations" / "c1.json").write_text("{}", encoding="utf-8")
    (root / "study_tags.json").write_text('{"tags": ["mine"]}', encoding="utf-8")
    (root / ".fastmdxplora_run.json").write_text('{"pid": 1}', encoding="utf-8")
    (root / "simulation" / "live_frames" / "frame_000001.pdb").write_text("END\n")
    (root / "previous" / "analysis" / "old.dat").write_text("1\n")
    # Shared only with --share-all.
    (root / "setup" / "system.xml").write_text("<System/>", encoding="utf-8")
    (root / "simulation" / "state_final.xml").write_text("<State/>", encoding="utf-8")
    return root


def _packed(tmp_path: Path, **options) -> tuple[Path, dict]:
    study = _study(tmp_path / "study")
    out = tmp_path / "shared.zip"
    said = share_study(study, out, authors=AUTHORS, **options)
    return out, said


def test_the_archive_holds_what_the_pages_read(tmp_path):
    out, said = _packed(tmp_path)
    names = set(zipfile.ZipFile(out).namelist())
    assert {"resolved_config.yml", "manifest.json", "simulation/production.dcd",
            "simulation/trajectory_topology.pdb", "analysis/rmsd/rmsd.png",
            "analysis/rmsd/rmsd.dat", "report/report.md", METADATA} <= names
    for never in ("agent/conversations/c1.json", "study_tags.json", ".fastmdxplora_run.json",
                  "simulation/live_frames/frame_000001.pdb", "previous/analysis/old.dat"):
        assert never not in names
    # A figure is shown from its picture; the vector twin goes with --share-all.
    for only_all in ("analysis/rmsd/rmsd.svg", "setup/system.xml",
                     "simulation/state_final.xml"):
        assert only_all not in names
    assert said["package"] == "view" and said["files"] == len(names) - 1


def test_everything_is_shared_when_asked_and_never_the_rest(tmp_path):
    out, said = _packed(tmp_path, everything=True)
    names = set(zipfile.ZipFile(out).namelist())
    assert {"analysis/rmsd/rmsd.svg", "setup/system.xml", "simulation/state_final.xml"} <= names
    assert "agent/conversations/c1.json" not in names and "study_tags.json" not in names
    assert said["crate"]["@graph"][1]["fmx:package"] == "all"


def test_the_packing_list_keeps_the_profile(tmp_path):
    out, _ = _packed(tmp_path)
    with zipfile.ZipFile(out) as archive:
        data = json.loads(archive.read(METADATA))
        listed = check(data)
        for path, expected in listed.items():
            body = archive.read(path)
            assert len(body) == expected["size"]
            assert hashlib.sha256(body).hexdigest() == expected["sha256"]
    graph = {e["@id"]: e for e in data["@graph"]}
    root = graph["./"]
    assert {"@id": PROFILE} in root["conformsTo"]
    assert root["mainEntity"] == {"@id": "resolved_config.yml"}
    assert root["license"] == {"@id": "https://spdx.org/licenses/CC-BY-4.0"}
    assert root["name"] == "The 1ABC study"
    assert root["description"] == "The system was simulated for 4 ps."
    assert graph["https://orcid.org/0000-0002-8215-7452"]["name"] == "Adekunle Aina"
    trajectory = graph["simulation/production.dcd"]
    assert trajectory["fmx:role"] == "trajectory"
    assert trajectory["fmx:readWith"] == {"@id": "simulation/trajectory_topology.pdb"}
    assert (trajectory["fmx:frames"], trajectory["fmx:atoms"]) == (4, 3)
    assert trajectory["fmx:savingIntervalPs"] == pytest.approx(1.0)
    assert graph["simulation/trajectory_topology.pdb"]["fmx:role"] == "topology"
    assert graph["#simulation"]["@type"] == "CreateAction"
    assert {"@id": "simulation/production.dcd"} in graph["#simulation"]["result"]


def test_no_path_home_or_computer_is_left(tmp_path):
    out, said = _packed(tmp_path)
    study = str((tmp_path / "study").resolve())
    with zipfile.ZipFile(out) as archive:
        for name in archive.namelist():
            text = archive.read(name).decode("utf-8", errors="ignore")
            for left in (study, "/home/someone", COMPUTER):
                assert left not in text, (name, left)
        config = archive.read("resolved_config.yml").decode()
        assert f"output: {STUDY}" in config and f"ligand: {HOME}/ligands/x.sdf" in config
        assert HOST in archive.read("report/report.md").decode()
    assert "resolved_config.yml" in said["placeholders"][STUDY]


def test_a_path_or_name_left_stops_the_archive(tmp_path, monkeypatch):
    from fastmdxplora.sharing import pack

    monkeypatch.setattr(pack._Scrubber, "__call__", lambda self, text: (text, set()))
    study = _study(tmp_path / "study")
    with pytest.raises(ShareRefused) as refused:
        share_study(study, tmp_path / "shared.zip", authors=AUTHORS)
    assert refused.value.refusal.code == "environment.share.unscrubbed"
    assert not (tmp_path / "shared.zip").exists()


def test_the_same_study_packs_to_the_same_bytes(tmp_path):
    study = _study(tmp_path / "study")
    first = share_study(study, tmp_path / "a.zip", authors=AUTHORS)
    second = share_study(study, tmp_path / "b.zip", authors=AUTHORS)
    assert first["sha256"] == second["sha256"]


@pytest.mark.parametrize("how", ["stopped", "several", "none"])
def test_a_study_that_did_not_finish_is_not_shared(tmp_path, how):
    study = _study(tmp_path / "study", phase_status="error" if how == "stopped" else "ok")
    if how == "several":
        (study / "batch_manifest.json").write_text("{}", encoding="utf-8")
    if how == "none":
        (study / "manifest.json").unlink()
    with pytest.raises(ShareRefused) as refused:
        share_study(study, tmp_path / "shared.zip", authors=AUTHORS)
    assert refused.value.refusal.code == ("environment.share.not_a_study" if how == "none"
                                          else "environment.share.not_finished")


def test_a_study_named_from_its_own_folder_keeps_its_words(tmp_path, monkeypatch):
    """`--output 1ABC` from the folder above: "1ABC" is the study's name in
    its records too, and was replaced wherever it stood."""
    study = _study(tmp_path / "1ABC")
    monkeypatch.chdir(tmp_path)
    share_study(Path("1ABC"), tmp_path / "shared.zip", authors=AUTHORS)
    with zipfile.ZipFile(tmp_path / "shared.zip") as archive:
        assert json.loads(archive.read("manifest.json"))["system"] == "1ABC"
        assert "system: 1ABC" in archive.read("resolved_config.yml").decode()
    assert study.is_dir()


@pytest.mark.parametrize("text, root, home, expected", [
    # As JSON writes a Windows path.
    (json.dumps({"x": r"D:\runs\3ptb\setup\x.pdb"}), r"D:\runs\3ptb", "/nowhere",
     json.dumps({"x": STUDY + r"\setup\x.pdb"})),
    # A home folder with a space in its name, the whole name taken.
    ("/Users/John Smith/structures/x.pdb", "/data/s", "/nowhere", f"{HOME}/structures/x.pdb"),
    (r"C:\Users\Jane Doe\x.sdf", "/data/s", "/nowhere", HOME + r"\x.sdf"),
    # A home folder that is a word of an address is not replaced inside it.
    ("https://example.org/root/docs", "/data/s", "/root", "https://example.org/root/docs"),
    # The end of a sentence ends a path; a sibling folder is another one.
    ("Results are in /data/s. And /data/s2/x, /data/s.bak", "/data/s", "/nowhere",
     f"Results are in {STUDY}. And /data/s2/x, /data/s.bak"),
])
def test_paths_are_replaced_where_they_are_and_only_there(text, root, home, expected,
                                                          monkeypatch):
    from fastmdxplora.sharing import pack

    monkeypatch.setattr(pack.Path, "home", staticmethod(lambda: Path(home)))
    scrub = pack._Scrubber([root], [])
    said, _ = scrub(text)
    assert said == expected
    assert scrub.left(said) == ""


def test_a_computer_s_name_goes_with_its_domain(tmp_path):
    from fastmdxplora.sharing import pack

    scrub = pack._Scrubber([], ["labbox07"])
    assert scrub("ran on labbox07.example.edu today")[0] == f"ran on {HOST} today"


def test_the_command_needs_an_author_and_a_true_orcid(tmp_path, capsys):
    from fastmdxplora.cli.main import main

    study = _study(tmp_path / "study")
    assert main(["report", "--output", str(study), "--share", str(tmp_path / "s.zip")]) == 2
    assert "--share-author" in capsys.readouterr().err
    assert main(["report", "--output", str(study), "--share", str(tmp_path / "s.zip"),
                 "--share-author", "A Person;1234"]) == 2
    assert "not an ORCID" in capsys.readouterr().err
    assert main(["report", "--output", str(study), "--share", str(tmp_path / "s.zip"),
                 "--share-author", "A Person"]) == 0
    assert (tmp_path / "s.zip").is_file()


def test_the_archive_is_a_zip(tmp_path, capsys):
    from fastmdxplora.cli.main import main

    study = _study(tmp_path / "study")
    assert main(["report", "--output", str(study), "--share", str(tmp_path / "s.tar"),
                 "--share-author", "A Person"]) == 2
    assert "ending in .zip" in capsys.readouterr().err
