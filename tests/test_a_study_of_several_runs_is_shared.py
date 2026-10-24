"""A study of several runs is shared as one file and opened as the study it was.

Every study of a paper is one of several runs: seeds of one preparation,
arms and mutants, umbrella windows. `fastmdx report --share` refused them, so
each run went alone and what joined them (the record of runs, the table of
members, the comparison) did not go at all. Packed now as its folder lays it
out, each run a dataset of its own files and phases, with a prepared system a
run simulated from carried once: kept outside the study, in `prepared/`, its
paths said as that folder, so a run opened elsewhere finds it as it did here.
"""

from __future__ import annotations

import json
import socket
import zipfile
from pathlib import Path, PurePosixPath

import pytest

from fastmdxplora.sharing import PROFILE, STUDY, ShareRefused
from fastmdxplora.sharing.crate import METADATA, check
from fastmdxplora.sharing.opening import open_shared
from fastmdxplora.sharing.pack import share_study

AUTHORS = [("Adekunle Aina", "0000-0002-8215-7452")]
COMPUTER = "labbox07"


def _trajectory(folder: Path) -> None:
    md = pytest.importorskip("mdtraj")
    import numpy as np

    topology = md.Topology()
    residue = topology.add_residue("ALA", topology.add_chain())
    for name in ("N", "CA", "C"):
        topology.add_atom(name, md.element.carbon, residue)
    xyz = np.zeros((4, 3, 3), dtype=np.float32)
    xyz[:, 1, 1] = 0.15
    xyz[:, 2, 2] = 0.15
    trajectory = md.Trajectory(xyz, topology)
    trajectory[0].save_pdb(str(folder / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(folder / "production.dcd"))


def _prepared(folder: Path) -> Path:
    """A prepared system as setup leaves one, its record naming its files by
    the paths they were written at, as setup's own does."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "system.xml").write_text("<System atoms='3'/>", encoding="utf-8")
    (folder / "state.xml").write_text("<State/>", encoding="utf-8")
    (folder / "topology.pdb").write_text("ATOM      1  N   ALA A   1\nEND\n",
                                         encoding="utf-8")
    (folder / "retained.pdb").write_text("END\n", encoding="utf-8")
    (folder / "setup_parameters.json").write_text(json.dumps({
        "phase": "setup", "topology_pdb": "topology.pdb", "system_xml": "system.xml",
        "parameters": {"forcefield": "amber14", "_retained_pdb": f"{folder}/retained.pdb"},
        "resolved_forcefield": {"ligand": {"files": [f"{folder}/ligands/BEN.sdf"],
                                           "charge": 1}}}), encoding="utf-8")
    (folder / "ligands").mkdir(exist_ok=True)
    (folder / "ligands" / "BEN.sdf").write_text(f"BEN\n  {folder}/ligands/BEN.sdf\n\nM  END\n",
                                                encoding="utf-8")
    return folder


def _run(root: Path, run_id: str, prepared: Path, given: str, *,
         status: str = "ok") -> Path:
    """A run of the study, simulated from ``prepared``, as the batch lays it out."""
    from fastmdxplora.simulation.pipeline import prepared_system_reference

    run = root / "runs" / run_id
    for folder in ("simulation", "analysis/rmsd", "report", "agent/conversations"):
        (run / folder).mkdir(parents=True, exist_ok=True)
    _trajectory(run / "simulation")
    phases = [{"name": "setup", "status": "skipped", "taken_from": str(prepared)}] + [
        {"name": name, "status": status if name == "analysis" else "ok",
         "output_dir": f"{run}/{name}", "started_at": "2026-10-07T01:00:00+00:00",
         "finished_at": f"2026-10-07T0{2 + len(run_id)}:00:00+00:00",
         "produced_by": {"version": "2.6.0", "host": COMPUTER}}
        for name in ("simulation", "analysis", "report")]
    (run / "manifest.json").write_text(json.dumps({
        "tool": "FastMDXplora", "version": "2.6.0", "system": "1ABC",
        "output_dir": str(run), "phases": phases}), encoding="utf-8")
    (run / "resolved_config.yml").write_text(
        f"systems:\n- system: 1ABC\noutput: {run}\nsimulation:\n  setup_from: {given}\n",
        encoding="utf-8")
    (run / "simulation" / "simulation_parameters.json").write_text(json.dumps({
        "parameters": {"timestep_fs": 2.0, "integrator": "langevin_middle",
                       "setup_from": given},
        "resolved": {"trajectory_interval_steps": 500, "production_steps": 2000},
        "prepared_system": prepared_system_reference(prepared, run, given)}),
        encoding="utf-8")
    (run / "simulation" / "live_status.json").write_text(json.dumps({
        "status": "completed", "host": COMPUTER}), encoding="utf-8")
    (run / "analysis" / "analysis_manifest.json").write_text(json.dumps({
        "trajectory_input": f"{run}/simulation/production.dcd"}), encoding="utf-8")
    (run / "analysis" / "rmsd" / "rmsd.dat").write_text("0 0.1\n1 0.2\n", encoding="utf-8")
    (run / "report" / "report.md").write_text(f"# Run {run_id}\n\nRan on {COMPUTER}.\n",
                                              encoding="utf-8")
    # Never shared: a run's conversations, its process, its tags.
    (run / "agent" / "conversations" / "c1.json").write_text("{}", encoding="utf-8")
    (run / ".fastmdxplora_run.json").write_text('{"pid": 1}', encoding="utf-8")
    (run / "study_tags.json").write_text('{"tags": ["mine"]}', encoding="utf-8")
    return run


def _campaign(tmp_path: Path, *, b_status: str = "ok") -> tuple[Path, Path]:
    """Three seeds of one system: `a` and `c` simulated from a preparation
    kept outside the study (`a` naming its study's folder, `c` the system's
    own), `b` from the study's own shared one. Returns the study and the
    preparation's study."""
    root = tmp_path / "campaign"
    elsewhere = _prepared(tmp_path / "preparation" / "setup").parent
    (elsewhere / "manifest.json").write_text(json.dumps({
        "phases": [{"name": "setup", "status": "ok",
                    "produced_by": {"host": "prepbox3"}}]}), encoding="utf-8")
    shared = _prepared(root / "shared_setup" / "setup")
    _run(root, "a", elsewhere / "setup", str(elsewhere))
    _run(root, "b", shared, str(shared), status=b_status)
    _run(root, "c", elsewhere / "setup", str(elsewhere / "setup"))
    (root / "comparison").mkdir()
    (root / "comparison" / "comparison_report.md").write_text(
        f"# Cross-run comparison report\n\nRuns in {root}/runs.\n", encoding="utf-8")
    (root / "members.json").write_text(json.dumps({
        "members": [{"run": "a", "dir": f"{root}/runs/a"},
                    {"run": "b", "dir": f"{root}/runs/b"}]}), encoding="utf-8")
    (root / "resolved_config.yml").write_text(
        f"systems:\n- system: 1ABC\nsweep:\n  simulation.random_seed: [1, 2, 3]\n"
        f"output: {root}\n",
        encoding="utf-8")
    runs = [{"run_id": run, "status": "ok", "output_dir": f"{root}/runs/{run}",
             "output_dir_relative": f"runs/{run}"} for run in ("a", "b", "c")]
    (root / "batch_manifest.json").write_text(json.dumps({
        "tool": "FastMDXplora", "kind": "batch", "version": "2.6.0",
        "config": "/home/someone/configs/seeds.yml", "output_dir": str(root),
        "n_runs": 3, "systems": [{"id": "1ABC", "system": "1ABC"}],
        "planned": [{"run_id": "a"}, {"run_id": "b"}, {"run_id": "c"}], "runs": runs}),
        encoding="utf-8")
    (root / "agent" / "conversations").mkdir(parents=True)
    (root / "agent" / "conversations" / "c0.json").write_text("{}", encoding="utf-8")
    return root, elsewhere


def _shared(tmp_path: Path, **options) -> tuple[Path, dict, Path, Path]:
    root, elsewhere = _campaign(tmp_path)
    out = tmp_path / "out" / "campaign.zip"
    return out, share_study(root, out, authors=AUTHORS, **options), root, elsewhere


def _by_id(crate: dict) -> dict:
    return {e["@id"]: e for e in crate["@graph"]}


def test_a_study_of_several_runs_is_packed_whole(tmp_path):
    out, said, _, _ = _shared(tmp_path)
    names = set(zipfile.ZipFile(out).namelist())
    assert {"batch_manifest.json", "resolved_config.yml", "members.json",
            "comparison/comparison_report.md", METADATA,
            "runs/a/manifest.json", "runs/a/resolved_config.yml",
            "runs/a/simulation/production.dcd", "runs/a/simulation/trajectory_topology.pdb",
            "runs/a/analysis/rmsd/rmsd.dat", "runs/b/report/report.md"} <= names
    # The preparation kept outside the study is carried once, whole enough
    # to be found and started from; the study's own shared one alike.
    assert {"prepared/preparation/setup/system.xml", "prepared/preparation/setup/state.xml",
            "prepared/preparation/setup/topology.pdb",
            "prepared/preparation/setup/setup_parameters.json",
            "prepared/preparation/setup/ligands/BEN.sdf",
            "shared_setup/setup/system.xml", "shared_setup/setup/state.xml"} <= names
    for never in ("runs/a/agent/conversations/c1.json", "agent/conversations/c0.json",
                  "runs/a/.fastmdxplora_run.json", "runs/a/study_tags.json",
                  "prepared/preparation/manifest.json"):
        assert never not in names
    crate = said["crate"]
    root = _by_id(crate)["./"]
    assert {"@id": PROFILE} in root["conformsTo"] and PROFILE.endswith("/1.1")
    assert root["fmx:runs"] == [{"@id": "runs/a/"}, {"@id": "runs/b/"}, {"@id": "runs/c/"}]
    assert "in 3 runs" in root["name"]
    # Two runs of one preparation, named two ways: carried once.
    assert not any(name.startswith("prepared/") and not name.startswith(
        "prepared/preparation/") for name in names)
    listed = check(crate)
    assert set(listed) == names - {METADATA}


def test_each_run_is_a_dataset_of_its_files_and_phases(tmp_path):
    _, said, _, _ = _shared(tmp_path)
    by_id = _by_id(said["crate"])
    member = by_id["runs/a/"]
    assert member["@type"] == "Dataset" and member["fmx:system"] == "1ABC"
    assert member["mainEntity"] == {"@id": "runs/a/resolved_config.yml"}
    assert {"@id": "runs/a/simulation/production.dcd"} in member["hasPart"]
    assert all(part["@id"].startswith("runs/a/") for part in member["hasPart"])
    assert by_id["runs/a/simulation/production.dcd"]["fmx:run"] == "a"
    assert by_id["runs/a/manifest.json"]["fmx:role"] == "manifest"
    assert by_id["batch_manifest.json"]["fmx:role"] == "manifest"
    simulation = by_id["#a/simulation"]
    assert simulation["@type"] == "CreateAction" and simulation["name"] == \
        "Simulation of the run a"
    # Each run's simulation read the prepared system it simulated from.
    assert {"@id": "prepared/preparation/setup/topology.pdb"} in simulation["object"]
    assert {"@id": "shared_setup/setup/topology.pdb"} in by_id["#b/simulation"]["object"]
    assert {"@id": "runs/a/simulation/production.dcd"} in simulation["result"]
    assert {"@id": "#a/simulation"} in member["mentions"]
    carried = by_id["prepared/preparation/"]
    assert carried["fmx:usedBy"] == [{"@id": "runs/a/"}, {"@id": "runs/c/"}]


def test_no_folder_or_computer_of_its_own_is_left(tmp_path):
    out, said, root, elsewhere = _shared(tmp_path)
    with zipfile.ZipFile(out) as archive:
        texts = {name: archive.read(name).decode("utf-8") for name in archive.namelist()
                 if name.endswith((".json", ".yml", ".md", ".sdf"))}
    for name, text in texts.items():
        for left in (str(tmp_path), COMPUTER, "prepbox3", socket.gethostname()):
            assert left not in text, (name, left)
    reference = json.loads(texts["runs/a/simulation/simulation_parameters.json"])
    assert reference["prepared_system"]["resolved"] == f"{STUDY}/prepared/preparation/setup"
    assert reference["parameters"]["setup_from"] == f"{STUDY}/prepared/preparation"
    named_alone = json.loads(texts["runs/c/simulation/simulation_parameters.json"])
    assert named_alone["parameters"]["setup_from"] == f"{STUDY}/prepared/preparation/setup"
    assert "__FASTMDX_HOME__/configs/seeds.yml" in texts["batch_manifest.json"]


def test_it_opens_as_the_study_it_was(tmp_path):
    from fastmdxplora.gui.workspace import study_state
    from fastmdxplora.simulation.pipeline import setup_records_of

    out, said, root, elsewhere = _shared(tmp_path)
    opened = open_shared(str(out), into=tmp_path / "elsewhere")
    batch = json.loads((opened / "batch_manifest.json").read_text(encoding="utf-8"))
    assert batch["output_dir"] == str(opened.resolve())
    # Each run finds the prepared system it simulated from inside the study
    # it is now, the one carried in and the study's own, by its content.
    assert setup_records_of(opened / "runs" / "a") == \
        (opened / "prepared" / "preparation" / "setup").resolve()
    assert setup_records_of(opened / "runs" / "b") == \
        (opened / "shared_setup" / "setup").resolve()
    assert setup_records_of(opened / "runs" / "c") == \
        (opened / "prepared" / "preparation" / "setup").resolve()
    config = (opened / "runs" / "a" / "resolved_config.yml").read_text(encoding="utf-8")
    assert f"setup_from: {opened.resolve()}/prepared/preparation" in config
    assert study_state(opened) == "completed"
    assert json.loads((opened / "shared_from.json").read_text())["files_checked"] == \
        said["files"]


def test_it_packs_to_the_same_bytes_twice(tmp_path):
    root, _ = _campaign(tmp_path)
    first = share_study(root, tmp_path / "a.zip", authors=AUTHORS)
    second = share_study(root, tmp_path / "b.zip", authors=AUTHORS)
    assert first["sha256"] == second["sha256"]


def test_a_run_that_did_not_finish_keeps_the_study_back(tmp_path):
    root, _ = _campaign(tmp_path, b_status="error")
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "shared.zip", authors=AUTHORS)
    assert refused.value.refusal.code == "environment.share.not_finished"
    assert str(refused.value).startswith(f"The run b of the study in {root} did not finish "
                                         "(analysis); share the study once every run has, "
                                         f"after `fastmdx resume {root}`.")
    assert not (tmp_path / "shared.zip").exists()


@pytest.mark.parametrize("planned", [[], [{"run_id": "../x"}]])
def test_a_record_of_runs_that_names_no_run_here_is_refused(tmp_path, planned):
    root, _ = _campaign(tmp_path)
    batch = json.loads((root / "batch_manifest.json").read_text())
    batch["planned"], batch["runs"] = planned, []
    (root / "batch_manifest.json").write_text(json.dumps(batch))
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "shared.zip", authors=AUTHORS)
    assert refused.value.refusal.code == "environment.share.not_a_study"


def test_a_run_s_conversation_in_an_archive_is_never_opened(tmp_path):
    """Packing never writes one; an archive holding one is not opened."""
    out, _, _, _ = _shared(tmp_path)
    crafted = tmp_path / "crafted.zip"
    with zipfile.ZipFile(out) as source, zipfile.ZipFile(crafted, "w") as sink:
        for info in source.infolist():
            sink.writestr(info, source.read(info))
        sink.writestr("runs/a/agent/conversations/c9.json", "{}")
    with pytest.raises(ShareRefused) as refused:
        open_shared(str(crafted), into=tmp_path / "elsewhere")
    assert refused.value.refusal.code == "environment.share.unsafe"
    # Nothing unpacked; the folder made to open it into is not left either.
    assert not (tmp_path / "elsewhere").exists()


def _of_one_run(root: Path, run: str = "a") -> None:
    """``root`` made a study of the one run ``run``."""
    (root / "resolved_config.yml").write_text(f"output: {root}\n", encoding="utf-8")
    (root / "batch_manifest.json").write_text(json.dumps({
        "version": "2.6.0", "output_dir": str(root), "planned": [{"run_id": run}],
        "runs": [{"run_id": run, "status": "ok"}]}), encoding="utf-8")


def test_a_preparation_moved_with_the_study_is_found_as_a_run_finds_it(tmp_path):
    """Review of round 1: a study fetched back or copied with its
    preparation has a stale recorded path, and the run finds its system
    relative to itself; packing looked only where the path said, and carried
    nothing."""
    import shutil

    from fastmdxplora.simulation.pipeline import setup_records_of

    root, elsewhere = _campaign(tmp_path)
    moved = tmp_path / "moved"
    moved.mkdir()
    shutil.move(str(root), moved / "campaign")
    shutil.move(str(elsewhere), moved / "preparation")
    root = moved / "campaign"
    assert setup_records_of(root / "runs" / "a") == (moved / "preparation" / "setup").resolve()
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    names = set(zipfile.ZipFile(out).namelist())
    assert "prepared/preparation/setup/system.xml" in names, sorted(
        n for n in names if "prepared" in n)
    # The study's own, found where it is now, not where it ran.
    assert {"shared_setup/setup/system.xml", "shared_setup/setup/state.xml"} <= names


def test_a_run_whose_preparation_is_not_found_keeps_the_study_back(tmp_path):
    """Review of round 1: shared without it, the run was opened with no setup
    record, so its report said setup never ran."""
    import shutil

    root, elsewhere = _campaign(tmp_path)
    shutil.rmtree(elsewhere)
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    assert refused.value.refusal.code == "environment.share.not_finished"
    # Every run that used it, at once.
    assert str(refused.value).startswith(f"The runs a, c of the study in {root} simulated "
                                         f"the prepared system named {elsewhere / 'setup'}, "
                                         "which is "
                                         "not found here")
    assert not (tmp_path / "o.zip").exists()


def test_a_window_seeded_from_a_pull_outside_carries_the_pull_s_preparation(tmp_path):
    """Review of round 1: a window's seed is in the study and keeps no setup
    record; the preparation it was taken from was outside and not carried."""
    import shutil

    from fastmdxplora.simulation.pipeline import setup_records_of
    from fastmdxplora.simulation.seeding import record_the_preparation

    pull = _prepared(tmp_path / "pull" / "setup")
    root = tmp_path / "umbrella"
    seed = root / "seeds" / "window-00"
    seed.mkdir(parents=True)
    for name in ("system.xml", "state.xml", "topology.pdb"):
        shutil.copy2(pull / name, seed / name)
    record_the_preparation(seed, pull, frame=3, centre=0.5)
    _run(root, "w0", seed, str(seed))
    _of_one_run(root, "w0")
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    names = set(zipfile.ZipFile(out).namelist())
    assert {"seeds/window-00/system.xml", "seeds/window-00/seeded_from.json",
            "prepared/pull/setup/setup_parameters.json"} <= names
    seeded = json.loads(zipfile.ZipFile(out).read("seeds/window-00/seeded_from.json"))
    assert seeded["relative_to_run"] == "../../prepared/pull/setup"
    shutil.rmtree(tmp_path / "pull")
    opened = open_shared(str(out), into=tmp_path / "x")
    assert setup_records_of(opened / "runs" / "w0") == \
        (opened / "prepared" / "pull" / "setup").resolve()


def test_the_way_from_a_run_to_its_preparation_names_no_home_folder(tmp_path, monkeypatch):
    """Review of round 1: a study in scratch simulated from a preparation in
    the home folder recorded `../../../home/<name>/prep/setup`, which kept
    the person's name and no check found."""
    home = tmp_path / "home" / "rosalind"
    monkeypatch.setenv("HOME", str(home))
    prep = _prepared(home / "prep" / "setup").parent
    root = tmp_path / "scratch" / "job" / "campaign"
    root.mkdir(parents=True)
    _run(root, "a", prep / "setup", str(prep) + "/")
    _of_one_run(root)
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    with zipfile.ZipFile(out) as archive:
        texts = {n: archive.read(n).decode("utf-8") for n in archive.namelist()
                 if n.endswith((".json", ".yml", ".sdf"))}
    assert not any("rosalind" in text for text in texts.values())
    reference = json.loads(texts["runs/a/simulation/simulation_parameters.json"])
    assert reference["prepared_system"]["relative_to_run"] == "../../prepared/prep/setup"
    # Named with a slash at its end, as typed.
    assert reference["parameters"]["setup_from"] == f"{STUDY}/prepared/prep/"


def test_a_prepared_system_is_packed_byte_for_byte(tmp_path):
    """Review of round 1: `system.xml` was read for paths and names like any
    text, so a word of it that was the computer's name changed its bytes,
    and the run opened elsewhere no longer found its system by them."""
    from fastmdxplora.simulation.pipeline import prepared_system_reference, setup_records_of

    root, elsewhere = _campaign(tmp_path)
    system = elsewhere / "setup" / "system.xml"
    system.write_text(f"<System name='{COMPUTER}'/>", encoding="utf-8")
    for name in ("a", "c"):
        run = root / "runs" / name
        record = json.loads((run / "simulation" / "simulation_parameters.json").read_text())
        record["prepared_system"] = prepared_system_reference(
            elsewhere / "setup", run, record["prepared_system"]["given"])
        (run / "simulation" / "simulation_parameters.json").write_text(json.dumps(record))
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    assert zipfile.ZipFile(out).read("prepared/preparation/setup/system.xml") == \
        system.read_bytes()
    opened = open_shared(str(out), into=tmp_path / "x")
    assert setup_records_of(opened / "runs" / "a") == \
        (opened / "prepared" / "preparation" / "setup").resolve()


def test_only_the_runs_of_its_record_go_and_its_own_folders_are_kept(tmp_path):
    """Review of round 1: a run's folder the record of runs does not name (an
    earlier attempt) went in with no dataset of its own; a run named with a
    backslash was taken for a folder; and a study's own `prepared/` folder
    could be written over by a preparation carried in."""
    root, _ = _campaign(tmp_path)
    (root / "runs" / "old").mkdir()
    (root / "runs" / "old" / "notes.md").write_text("an earlier attempt\n")
    (root / "prepared" / "preparation").mkdir(parents=True)
    (root / "prepared" / "preparation" / "mine.txt").write_text("the study's own\n")
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    names = set(zipfile.ZipFile(out).namelist())
    assert not any(n.startswith("runs/old/") for n in names)
    assert "prepared/preparation/mine.txt" in names
    assert "prepared/preparation-2/setup/system.xml" in names
    batch = json.loads((root / "batch_manifest.json").read_text())
    batch["planned"] = [{"run_id": "a\\..\\..\\x"}]
    (root / "batch_manifest.json").write_text(json.dumps(batch))
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "p.zip", authors=AUTHORS)
    assert refused.value.refusal.code == "environment.share.not_a_study"


def test_a_run_still_going_is_named(tmp_path):
    """Review of round 1: the refusal said runs were still going, not which."""
    root, _ = _campaign(tmp_path)
    (root / "runs" / "b" / "simulation" / "live_status.json").write_text(
        json.dumps({"status": "running"}), encoding="utf-8")
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    assert str(refused.value) == (f"The run b of the study in {root} is still going; share "
                                  "the study once every run has finished.")


def _texts_of(out: Path) -> dict[str, str]:
    with zipfile.ZipFile(out) as archive:
        return {n: archive.read(n).decode("utf-8") for n in archive.namelist()
                if n.endswith((".json", ".yml", ".md", ".sdf")) and n != METADATA}


def test_a_study_moved_with_its_preparation_keeps_no_path_of_where_it_ran(tmp_path):
    """Review of round 2: moved together after it ran, its records still
    named the folders of the computer it ran on, and its Config named a
    folder that was no longer there."""
    import shutil

    ran = tmp_path / "cluster" / "scratch" / "kunle" / "job7"
    ran.mkdir(parents=True)
    root, elsewhere = _campaign(ran)
    here = tmp_path / "fetched"
    shutil.move(str(ran), here)
    out = tmp_path / "o.zip"
    share_study(here / "campaign", out, authors=AUTHORS)
    texts = _texts_of(out)
    for name, text in texts.items():
        assert str(ran) not in text, name
    opened = open_shared(str(out), into=tmp_path / "x")
    config = (opened / "runs" / "a" / "resolved_config.yml").read_text(encoding="utf-8")
    assert f"setup_from: {opened.resolve()}/prepared/preparation" in config


@pytest.mark.parametrize("where", ["a run's folder not in the record", "a dot folder"])
def test_a_system_in_a_folder_the_archive_leaves_out_is_carried(tmp_path, where):
    """Review of round 2: a run's system inside the study, in a folder the
    archive leaves out, was dropped and the run opened with none; refused
    then (round 3), the refusal's remedy led to another refusal."""
    import shutil

    from fastmdxplora.simulation.pipeline import setup_records_of

    root = tmp_path / "campaign"
    folder = {"a run's folder not in the record": root / "runs" / "old" / "setup",
              "a dot folder": root / ".prep" / "setup"}[where]
    _prepared(folder)
    _run(root, "a", folder, str(folder.parent))
    _of_one_run(root)
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    names = set(zipfile.ZipFile(out).namelist())
    carried = {"a run's folder not in the record": "prepared/old/setup/system.xml",
               "a dot folder": "prepared/prep/setup/system.xml"}[where]
    assert carried in names
    assert not any(n.startswith(("runs/old/", ".prep/")) for n in names)
    shutil.rmtree(folder.parent)
    opened = open_shared(str(out), into=tmp_path / "x")
    assert setup_records_of(opened / "runs" / "a") == \
        (opened / PurePosixPath(carried).parent).resolve()


def test_a_system_inside_the_study_kept_as_links_is_refused(tmp_path):
    """Review of round 2: a run's system kept as links was dropped."""
    root = tmp_path / "campaign"
    real = _prepared(tmp_path / "kept")
    folder = root / "linked"
    folder.mkdir(parents=True)
    for name in ("system.xml", "state.xml", "topology.pdb", "setup_parameters.json"):
        (folder / name).symlink_to(real / name)
    _run(root, "a", folder, str(folder))
    _of_one_run(root)
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    assert refused.value.refusal.code == "environment.share.not_finished"
    # Every link at once, not one a try.
    assert ("its setup_parameters.json, state.xml, system.xml, topology.pdb are links"
            in str(refused.value))
    assert not (tmp_path / "o.zip").exists()


def test_each_run_s_own_recorded_folder_is_said_as_its_place(tmp_path):
    """Review of round 3: a run whose own record gives another folder than
    the record of runs (carried on elsewhere) kept that folder's path."""
    root, _ = _campaign(tmp_path)
    manifest = root / "runs" / "a" / "manifest.json"
    found = json.loads(manifest.read_text())
    found["output_dir"] = "/somewhere/else/ws/clean-own"
    manifest.write_text(json.dumps(found))
    out = tmp_path / "o.zip"
    said = share_study(root, out, authors=AUTHORS)
    texts = _texts_of(out)
    assert "/somewhere/else" not in texts["runs/a/manifest.json"]
    assert json.loads(texts["runs/a/manifest.json"])["output_dir"] == f"{STUDY}/runs/a"
    opened = open_shared(str(out), into=tmp_path / "x")
    assert opened.name == "1abc-in-3-runs"
    assert _by_id(said["crate"])["runs/a/"]["fmx:system"] == "1ABC"


def test_a_home_reached_through_a_link_is_said_as_home(tmp_path, monkeypatch):
    """Review of round 2: only the home folder's path as named was said as
    home, not where a link to it leads, which records written from there
    give."""
    real_home = tmp_path / "gpfs" / "u7x3q"
    real_home.mkdir(parents=True)
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "rosalind").symlink_to(real_home)
    monkeypatch.setenv("HOME", str(tmp_path / "home" / "rosalind"))
    prep = _prepared(real_home / "prep" / "setup").parent
    (prep / "setup" / "setup_parameters.json").write_text(json.dumps({
        "structure": f"{real_home}/structures/x.pdb"}), encoding="utf-8")
    root = tmp_path / "scratch" / "campaign"
    root.mkdir(parents=True)
    _run(root, "a", prep / "setup", str(prep))
    _of_one_run(root)
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    texts = _texts_of(out)
    assert not any("u7x3q" in text for text in texts.values())
    assert "__FASTMDX_HOME__/structures/x.pdb" in \
        texts["prepared/prep/setup/setup_parameters.json"]


def test_a_preparation_named_through_a_link_is_said_as_carried(tmp_path):
    """Review of round 2: a path under a preparation named through a link
    was left as written."""
    real = _prepared(tmp_path / "realprep" / "setup").parent
    (tmp_path / "linkprep").symlink_to(real)
    root = tmp_path / "campaign"
    run = _run(root, "a", real / "setup", str(tmp_path / "linkprep"))
    with (run / "resolved_config.yml").open("a", encoding="utf-8") as config:
        config.write(f"report:\n  structure: {tmp_path}/linkprep/setup/topology.pdb\n")
    _of_one_run(root)
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    config = _texts_of(out)["runs/a/resolved_config.yml"]
    assert str(tmp_path) not in config
    assert f"structure: {STUDY}/prepared/realprep/setup/topology.pdb" in config


def test_one_study_s_two_systems_are_carried_in_one_folder(tmp_path):
    """Review of round 2: a study holding two systems the runs used was
    carried twice, and its name said as one of them only."""
    from fastmdxplora.simulation.pipeline import setup_records_of

    other = tmp_path / "other"
    _prepared(other / "setup")
    _prepared(other / "shared_setup" / "setup")
    root = tmp_path / "campaign"
    _run(root, "a", other / "shared_setup" / "setup", str(other))
    _run(root, "c", other / "setup", str(other / "setup"))
    _of_one_run(root)
    batch = json.loads((root / "batch_manifest.json").read_text())
    batch["planned"] = [{"run_id": "a"}, {"run_id": "c"}]
    (root / "batch_manifest.json").write_text(json.dumps(batch))
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    names = set(zipfile.ZipFile(out).namelist())
    assert {"prepared/other/setup/system.xml",
            "prepared/other/shared_setup/setup/system.xml"} <= names
    assert not any(n.startswith("prepared/other-2") for n in names)
    texts = _texts_of(out)
    assert f"setup_from: {STUDY}/prepared/other\n" in texts["runs/a/resolved_config.yml"]
    opened = open_shared(str(out), into=tmp_path / "x")
    assert setup_records_of(opened / "runs" / "a") == \
        (opened / "prepared" / "other" / "shared_setup" / "setup").resolve()
    assert setup_records_of(opened / "runs" / "c") == \
        (opened / "prepared" / "other" / "setup").resolve()


def test_every_computer_s_name_its_records_give_is_said_as_one(tmp_path):
    """Review of round 2: an umbrella's pull run on another node kept its
    name, and a name too short to be told from a word (`vm`) kept in a
    record's own field for it."""
    root, _ = _campaign(tmp_path)
    (root / "seed_pull").mkdir()
    (root / "seed_pull" / "manifest.json").write_text(json.dumps({"phases": [
        {"name": "simulation", "status": "ok", "produced_by": {"host": "pullnode9"}}]}))
    (root / "seed_pull" / "pull.log").write_text("pulled on pullnode9\n")
    (root / "runs" / "a" / "simulation" / "live_status.json").write_text(
        json.dumps({"status": "completed", "host": "vm"}))
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    with zipfile.ZipFile(out) as archive:
        assert b"pullnode9" not in archive.read("seed_pull/manifest.json")
    live = json.loads(_texts_of(out)["runs/a/simulation/live_status.json"])
    assert live["host"] == "__FASTMDX_HOST__"


def test_a_run_never_started_or_failed_is_said_with_the_command(tmp_path):
    """Review of round 2: the remedy gave `fastmdx resume` with no folder."""
    import shutil

    root, _ = _campaign(tmp_path)
    shutil.rmtree(root / "runs" / "c")
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    assert str(refused.value) == (f"The run c of the study in {root} never ran; run it with "
                                  f"`fastmdx resume {root}`, then share the study.")
    batch = json.loads((root / "batch_manifest.json").read_text())
    batch["planned"] = batch["planned"][:2]
    batch["runs"][1]["status"] = "error"
    batch["runs"] = batch["runs"]
    (root / "batch_manifest.json").write_text(json.dumps(batch))
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    assert str(refused.value) == (f"The run b of the study in {root} ended with an error; "
                                  "share the study once every run has finished, after "
                                  f"`fastmdx resume {root}`.")
    batch["runs"] = 5
    (root / "batch_manifest.json").write_text(json.dumps(batch))
    share_study(root, tmp_path / "p.zip", authors=AUTHORS)  # read without results


def test_a_study_of_one_run_is_said_so(tmp_path):
    root = tmp_path / "campaign"
    _run(root, "a", _prepared(root / "shared_setup" / "setup"),
         str(root / "shared_setup" / "setup"))
    _of_one_run(root)
    said = share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    assert _by_id(said["crate"])["./"]["name"].endswith("in one run")


def test_a_preparation_outside_holding_a_link_is_refused(tmp_path):
    """Review of round 3: a system outside the study whose files are links
    was carried without them, and its runs opened with none."""
    kept = _prepared(tmp_path / "kept")
    prep = tmp_path / "prep" / "setup"
    prep.mkdir(parents=True)
    for name in ("system.xml", "state.xml", "topology.pdb", "setup_parameters.json"):
        (prep / name).symlink_to(kept / name)
    root = tmp_path / "campaign"
    _run(root, "a", prep, str(prep.parent))
    _of_one_run(root)
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    assert refused.value.refusal.code == "environment.share.not_finished"
    assert "which the run a simulated from, holds 4 links" in str(refused.value)
    for name in ("system.xml", "state.xml", "topology.pdb", "setup_parameters.json"):
        assert str(prep / name) in str(refused.value)


def test_a_folder_carried_from_is_one_dataset(tmp_path):
    """Review of round 3: a study's two systems gave two entities of one
    `@id`, each with half the runs that used it."""
    other = tmp_path / "other"
    _prepared(other / "setup")
    _prepared(other / "shared_setup" / "setup")
    root = tmp_path / "campaign"
    _run(root, "a", other / "shared_setup" / "setup", str(other))
    _run(root, "c", other / "setup", str(other / "setup"))
    _of_one_run(root)
    batch = json.loads((root / "batch_manifest.json").read_text())
    batch["planned"] = [{"run_id": "a"}, {"run_id": "c"}]
    (root / "batch_manifest.json").write_text(json.dumps(batch))
    said = share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    ids = [e["@id"] for e in said["crate"]["@graph"]]
    assert len(ids) == len(set(ids))
    carried = _by_id(said["crate"])["prepared/other/"]
    # In the order of the record of runs.
    assert carried["fmx:usedBy"] == [{"@id": "runs/a/"}, {"@id": "runs/c/"}]


def test_a_name_alone_keeps_what_follows_it(tmp_path):
    """Review of round 3: the slash after a study folder's name was taken
    with it before a character that ends a path (`/x/prep/*`)."""
    from fastmdxplora.sharing.pack import _Scrubber

    scrub = _Scrubber(["/data/camp"], [], {"/data/prep": ("prepared/prep", True)})
    assert scrub("copied /data/prep/* here")[0] == f"copied {STUDY}/prepared/prep/* here"
    assert scrub("setup_from: /data/prep\n")[0] == f"setup_from: {STUDY}/prepared/prep\n"
    assert scrub("/data/prep/manifest.json")[0] == "/data/prep/manifest.json"


def test_a_preparation_moved_into_the_study_keeps_no_path_of_where_it_was(tmp_path):
    """Review of round 3: a study fetched back with its preparation copied
    into it kept the path the preparation had where it ran."""
    import shutil

    ran = tmp_path / "cluster" / "job7"
    ran.mkdir(parents=True)
    _campaign(ran)
    here = tmp_path / "fetched"
    shutil.move(str(ran), here)
    root = here / "campaign"
    shutil.move(str(here / "preparation"), root / "preparation")
    for run in ("a", "c"):
        record = root / "runs" / run / "simulation" / "simulation_parameters.json"
        found = json.loads(record.read_text())
        found["prepared_system"]["relative_to_run"] = "../../preparation/setup"
        record.write_text(json.dumps(found))
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    for name, text in _texts_of(out).items():
        assert str(ran) not in text, name
    assert not any(n.startswith("prepared/") for n in zipfile.ZipFile(out).namelist())


def test_a_folder_named_for_the_person_is_not_carried_by_that_name(tmp_path, monkeypatch):
    """Review of round 3: `/scratch/<login>/setup` named by itself was carried
    as `prepared/<login>/`, the person's name in every path of it."""
    import getpass

    monkeypatch.setattr(getpass, "getuser", lambda: "u7x3q")
    prep = _prepared(tmp_path / "scratch" / "u7x3q" / "setup")
    root = tmp_path / "campaign"
    _run(root, "a", prep, str(prep))
    _of_one_run(root)
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    names = zipfile.ZipFile(out).namelist()
    assert not any("u7x3q" in n for n in names)
    assert "prepared/preparation/setup/system.xml" in names


def test_the_recorded_seeds_study_is_shared_and_opened(tmp_path):
    """A study of three replicas as FastMDXplora wrote one (the records of
    tests/data/report_traced/seeds), named for its system, not its file."""
    import shutil

    from fastmdxplora.gui.exploration import runs_of_a_study
    from fastmdxplora.gui.workspace import study_state

    root = tmp_path / "seeds"
    shutil.copytree(Path(__file__).parent / "data" / "report_traced" / "seeds", root)
    said = share_study(root, tmp_path / "seeds.zip", authors=AUTHORS)
    assert _by_id(said["crate"])["./"]["name"] == "A FastMDXplora study of tri-ala, in 3 runs"
    opened = open_shared(str(tmp_path / "seeds.zip"), into=tmp_path / "x")
    assert opened.name == "tri-ala-in-3-runs"
    assert study_state(opened) == "completed"
    assert [r["state"] for r in runs_of_a_study(opened)] == ["completed"] * 3


def test_a_run_s_recorded_folder_never_says_another_run_s_paths(tmp_path):
    """Review of series 2: two runs carried on in one job folder, or a run
    recording another's folder, had one run's paths said as the other's."""
    root, _ = _campaign(tmp_path)
    for run in ("a", "b"):
        manifest = root / "runs" / run / "manifest.json"
        found = json.loads(manifest.read_text())
        found["output_dir"] = "/scratch/job"
        manifest.write_text(json.dumps(found))
        (root / "runs" / run / "analysis" / "analysis_manifest.json").write_text(
            json.dumps({"trajectory_input": "/scratch/job/simulation/production.dcd"}))
    manifest = root / "runs" / "c" / "manifest.json"
    found = json.loads(manifest.read_text())
    found["output_dir"] = str(root / "runs" / "b")
    manifest.write_text(json.dumps(found))
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    texts = _texts_of(out)
    for run in ("a", "b"):
        read = json.loads(texts[f"runs/{run}/analysis/analysis_manifest.json"])
        assert not read["trajectory_input"].startswith(f"{STUDY}/runs/"), run
    assert f"{STUDY}/runs/c" not in texts["runs/b/manifest.json"]


def test_a_preparation_s_name_is_said_as_carried_before_a_run_s_folder(tmp_path):
    """Review of series 2: a run that ran in the folder a preparation is
    named by had that name said as the run's folder."""
    root = tmp_path / "campaign"
    job = tmp_path / "x" / "job"
    prep = _prepared(job / "setup")
    _run(root, "a", prep, str(job))
    _of_one_run(root)
    manifest = root / "runs" / "a" / "manifest.json"
    found = json.loads(manifest.read_text())
    found["output_dir"] = str(job)
    manifest.write_text(json.dumps(found))
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    record = json.loads(_texts_of(out)["runs/a/simulation/simulation_parameters.json"])
    assert record["parameters"]["setup_from"] == f"{STUDY}/prepared/job"


def test_a_system_in_a_folder_set_aside_is_carried(tmp_path):
    """Review of series 2: a system in `previous/` was refused as a link."""
    from fastmdxplora.simulation.pipeline import setup_records_of

    root = tmp_path / "campaign"
    folder = root / "previous" / "prep" / "setup"
    _prepared(folder)
    _run(root, "a", folder, str(folder.parent))
    _of_one_run(root)
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    assert "prepared/prep/setup/system.xml" in zipfile.ZipFile(out).namelist()
    opened = open_shared(str(out), into=tmp_path / "x")
    assert setup_records_of(opened / "runs" / "a") == \
        (opened / "prepared" / "prep" / "setup").resolve()


def test_a_preparation_named_from_home_is_said_as_carried(tmp_path, monkeypatch):
    """Review of series 2: `setup_from: ~/prep` stayed as typed, so the
    Config run again elsewhere read the reader's own `~/prep`."""
    home = tmp_path / "home" / "rosa"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    prep = _prepared(home / "prep" / "setup").parent
    root = tmp_path / "scratch" / "campaign"
    root.mkdir(parents=True)
    _run(root, "a", prep / "setup", "~/prep")
    _of_one_run(root)
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    config = _texts_of(out)["runs/a/resolved_config.yml"]
    assert "~/prep" not in config and f"setup_from: {STUDY}/prepared/prep" in config


def test_a_name_another_computer_s_home_holds_is_said_as_a_refusal_or_found(tmp_path):
    """Review of series 2: `~otheruser/...`, read here, raised an error that
    was no refusal."""
    import shutil

    from fastmdxplora.simulation.pipeline import setup_records_of

    ran = tmp_path / "cluster"
    root = ran / "campaign"
    prep = _prepared(root / "shared_setup" / "setup")
    _run(root, "a", prep, "~nosuchuser42/campaign/shared_setup/setup")
    _of_one_run(root)
    here = tmp_path / "here"
    shutil.move(str(ran), here)
    out = tmp_path / "o.zip"
    share_study(here / "campaign", out, authors=AUTHORS)
    assert not any("nosuchuser42" in text for text in _texts_of(out).values())
    opened = open_shared(str(out), into=tmp_path / "x")
    assert setup_records_of(opened / "runs" / "a") == \
        (opened / "shared_setup" / "setup").resolve()


def test_a_copied_preparation_keeps_no_path_of_where_it_was_made(tmp_path):
    """Review of series 2: a preparation made in one folder and copied to
    another kept, in its own setup record and ligands, the folder it was
    made in."""
    import shutil

    made = tmp_path / "cluster" / "scratch" / "kaina" / "prep7"
    _prepared(made / "setup")
    copy = tmp_path / "preps" / "prep7"
    shutil.copytree(made, copy)
    root = tmp_path / "campaign"
    _run(root, "a", copy / "setup", str(copy))
    _of_one_run(root)
    out = tmp_path / "o.zip"
    printed: list[str] = []
    said = share_study(root, out, authors=AUTHORS, said=printed.append)
    for name, text in _texts_of(out).items():
        assert str(made) not in text, name
    record = json.loads(_texts_of(out)["prepared/prep7/setup/setup_parameters.json"])
    assert record["parameters"]["_retained_pdb"] == f"{STUDY}/prepared/prep7/setup/retained.pdb"
    assert record["resolved_forcefield"]["ligand"]["files"] == [
        f"{STUDY}/prepared/prep7/setup/ligands/BEN.sdf"]
    assert printed[-1].endswith(f"kB), SHA-256 {said['sha256']}.")


def test_a_seed_naming_another_person_s_home_is_shared(tmp_path):
    """Review of series 2: a seed whose record named its preparation by
    `~otheruser/` raised an error that was no refusal."""
    import shutil

    from fastmdxplora.simulation.pipeline import setup_records_of
    from fastmdxplora.simulation.seeding import SEEDED_FROM, record_the_preparation

    pull = _prepared(tmp_path / "pull" / "setup")
    root = tmp_path / "umbrella"
    seed = root / "seeds" / "window-00"
    seed.mkdir(parents=True)
    for name in ("system.xml", "state.xml", "topology.pdb"):
        shutil.copy2(pull / name, seed / name)
    record_the_preparation(seed, pull, frame=3, centre=0.5)
    record = json.loads((seed / SEEDED_FROM).read_text())
    record["given"] = "~nosuchuser42/pull/setup"
    (seed / SEEDED_FROM).write_text(json.dumps(record))
    _run(root, "w0", seed, str(seed))
    _of_one_run(root, "w0")
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    assert not any("nosuchuser42" in text for text in _texts_of(out).values())
    opened = open_shared(str(out), into=tmp_path / "x")
    assert setup_records_of(opened / "runs" / "w0") == \
        (opened / "prepared" / "pull" / "setup").resolve()


def test_a_run_s_folder_recorded_above_the_study_says_no_unrelated_path(tmp_path):
    """Review of series 2: a run recording a folder holding the study had
    every path under that folder said as the run's."""
    work = tmp_path / "work"
    root, _ = _campaign(work)
    manifest = root / "runs" / "a" / "manifest.json"
    found = json.loads(manifest.read_text())
    found["output_dir"] = str(work)
    manifest.write_text(json.dumps(found))
    with (root / "runs" / "a" / "report" / "report.md").open("a") as report:
        report.write(f"\nRead from {work}/pdbs/1abc.pdb.\n")
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    assert f"{work}/pdbs/1abc.pdb" in _texts_of(out)["runs/a/report/report.md"]


def test_a_link_a_run_does_not_need_leaves_a_carried_preparation_shared(tmp_path):
    """Review of series 2: a link in a preparation that is not packed by
    default (a solvated system) refused the share."""
    prep = _prepared(tmp_path / "prep" / "setup")
    (tmp_path / "big").mkdir()
    (tmp_path / "big" / "solvated.pdb").write_text("END\n")
    (prep / "solvated.pdb").symlink_to(tmp_path / "big" / "solvated.pdb")
    root = tmp_path / "campaign"
    _run(root, "a", prep, str(prep.parent))
    _of_one_run(root)
    share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "all.zip", authors=AUTHORS, everything=True)
    assert "solvated.pdb" in str(refused.value)


def test_links_of_two_runs_systems_are_named_at_once(tmp_path):
    """Review of series 2: links in two runs' systems were named one run
    at a time."""
    root = tmp_path / "campaign"
    real = _prepared(tmp_path / "kept")
    for run in ("a", "c"):
        folder = root / f"linked-{run}"
        folder.mkdir(parents=True)
        for name in ("system.xml", "state.xml", "topology.pdb", "setup_parameters.json"):
            (folder / name).symlink_to(real / name)
        _run(root, run, folder, str(folder))
    _of_one_run(root)
    batch = json.loads((root / "batch_manifest.json").read_text())
    batch["planned"] = [{"run_id": "a"}, {"run_id": "c"}]
    (root / "batch_manifest.json").write_text(json.dumps(batch))
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    said = str(refused.value)
    assert "linked-a (the run a)" in said and "linked-c (the run c)" in said


def test_a_name_typed_from_another_computer_s_home_is_said_as_carried(tmp_path):
    """Review of series 2: `setup_from: ~/prep` typed on the computer the
    study ran on stayed as typed, though its system was found and carried."""
    import shutil

    ran = tmp_path / "cluster" / "home" / "kunle"
    root, elsewhere = _campaign(ran)
    for run in ("a", "c"):
        config = root / "runs" / run / "resolved_config.yml"
        config.write_text(config.read_text().replace(f"setup_from: {elsewhere}",
                                                     "setup_from: ~/preparation"))
    here = tmp_path / "fetched"
    shutil.move(str(ran), here)
    out = tmp_path / "o.zip"
    share_study(here / "campaign", out, authors=AUTHORS)
    config = _texts_of(out)["runs/a/resolved_config.yml"]
    assert "~/preparation" not in config
    assert f"setup_from: {STUDY}/prepared/preparation" in config


@pytest.mark.parametrize("kept", ["outside", "inside"])
def test_what_setup_read_is_never_said_as_the_system_it_made(tmp_path, kept):
    """Review of series 2: a structure setup read from another study's
    `setup/` was said, with every path under that folder, as the carried
    system, so the archive said the input was the system's own output."""
    older = tmp_path / "older_study" / "setup"
    older.mkdir(parents=True)
    (older / "topology.pdb").write_text("END\n")
    root = tmp_path / "campaign"
    prep = _prepared(tmp_path / "prep" / "setup" if kept == "outside"
                     else root / "shared_setup" / "setup")
    record = json.loads((prep / "setup_parameters.json").read_text())
    record["input"] = {"system": f"{older}/topology.pdb", "form": "file"}
    (prep / "setup_parameters.json").write_text(json.dumps(record))
    _run(root, "a", prep, str(prep.parent if kept == "outside" else prep))
    _of_one_run(root)
    with (root / "runs" / "a" / "report" / "report.md").open("a") as report:
        report.write(f"\nBuilt from {older}/topology.pdb, see {older}/notes.txt.\n")
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    texts = _texts_of(out)
    carried = ("prepared/prep/setup" if kept == "outside" else "shared_setup/setup")
    assert json.loads(texts[f"{carried}/setup_parameters.json"])["input"]["system"] == \
        f"{older}/topology.pdb"
    assert f"{older}/notes.txt" in texts["runs/a/report/report.md"]


def test_a_seed_holding_its_own_setup_record_is_shared(tmp_path):
    """Review of series 2: a seed with a setup record of its own was refused
    as though its preparation were not found."""
    import shutil

    from fastmdxplora.simulation.seeding import record_the_preparation

    pull = _prepared(tmp_path / "pull" / "setup")
    root = tmp_path / "umbrella"
    seed = root / "seeds" / "window-00"
    seed.mkdir(parents=True)
    for name in ("system.xml", "state.xml", "topology.pdb", "setup_parameters.json"):
        shutil.copy2(pull / name, seed / name)
    record_the_preparation(seed, pull, frame=3, centre=0.5)
    _run(root, "w0", seed, str(seed))
    _of_one_run(root, "w0")
    share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    from fastmdxplora.simulation.pipeline import setup_records_of

    shutil.rmtree(tmp_path / "pull")
    opened = open_shared(str(tmp_path / "o.zip"), into=tmp_path / "x")
    assert setup_records_of(opened / "runs" / "w0") == \
        (opened / "seeds" / "window-00").resolve()


def test_a_linked_system_two_runs_used_names_both(tmp_path):
    """Review of series 2: a system two runs used, kept as links, was said
    as the first run's only."""
    root = tmp_path / "campaign"
    real = _prepared(tmp_path / "kept")
    folder = root / "linked"
    folder.mkdir(parents=True)
    for name in ("system.xml", "state.xml", "topology.pdb", "setup_parameters.json"):
        (folder / name).symlink_to(real / name)
    for run in ("a", "c"):
        _run(root, run, folder, str(folder))
    _of_one_run(root)
    batch = json.loads((root / "batch_manifest.json").read_text())
    batch["planned"] = [{"run_id": "a"}, {"run_id": "c"}]
    (root / "batch_manifest.json").write_text(json.dumps(batch))
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    assert str(refused.value).startswith(f"The runs a, c of the study in {root} simulated "
                                         f"the prepared system in {root / 'linked'}, and its ")


def test_a_ligand_given_from_another_study_is_kept_as_written(tmp_path):
    """Review of series 3: a ligand the person gave from another study's
    `setup/ligands/` made that folder taken for where the preparation was
    made, and every path under it said as the carried system."""
    import shutil

    older = tmp_path / "older_study" / "setup"
    (older / "ligands").mkdir(parents=True)
    (older / "ligands" / "BEN.sdf").write_text("BEN\n\nM  END\n")
    prep = _prepared(tmp_path / "prep" / "setup")
    shutil.copy2(older / "ligands" / "BEN.sdf", prep / "ligands" / "BEN.sdf")
    record = json.loads((prep / "setup_parameters.json").read_text())
    record["resolved_forcefield"]["ligand"]["files"] = [f"{older}/ligands/BEN.sdf"]
    record["input"] = {"system": f"{older}/input.pdb"}
    (prep / "setup_parameters.json").write_text(json.dumps(record))
    root = tmp_path / "campaign"
    _run(root, "a", prep, str(prep.parent))
    _of_one_run(root)
    with (root / "runs" / "a" / "report" / "report.md").open("a") as report:
        report.write(f"\nSee {older}/notes.txt.\n")
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    texts = _texts_of(out)
    assert f"{older}/notes.txt" in texts["runs/a/report/report.md"]
    record = json.loads(texts["prepared/prep/setup/setup_parameters.json"])
    assert record["input"]["system"] == f"{older}/input.pdb"
    assert record["resolved_forcefield"]["ligand"]["files"] == [f"{older}/ligands/BEN.sdf"]


def test_a_copy_of_the_study_s_own_system_takes_none_of_its_paths(tmp_path):
    """Review of series 3: a copy kept outside of the study's own shared
    system, used by one run, had the original's paths said as the copy's."""
    import shutil

    from fastmdxplora.simulation.pipeline import setup_records_of

    root = tmp_path / "campaign"
    shared = _prepared(root / "shared_setup" / "setup")
    copy = tmp_path / "elsewhere" / "setup"
    shutil.copytree(shared, copy)
    _run(root, "a", copy, str(copy))
    _run(root, "b", shared, str(shared))
    _of_one_run(root)
    batch = json.loads((root / "batch_manifest.json").read_text())
    batch["planned"] = [{"run_id": "a"}, {"run_id": "b"}]
    (root / "batch_manifest.json").write_text(json.dumps(batch))
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    texts = _texts_of(out)
    record = json.loads(texts["runs/b/simulation/simulation_parameters.json"])
    assert record["parameters"]["setup_from"] == f"{STUDY}/shared_setup/setup"
    own = json.loads(texts["shared_setup/setup/setup_parameters.json"])
    assert own["parameters"]["_retained_pdb"] == f"{STUDY}/shared_setup/setup/retained.pdb"
    opened = open_shared(str(out), into=tmp_path / "x")
    assert setup_records_of(opened / "runs" / "b") == \
        (opened / "shared_setup" / "setup").resolve()


def test_a_run_carried_on_in_the_home_folder_is_said_as_its_place(tmp_path, monkeypatch):
    """Review of series 3: a run's own recorded folder under the home folder
    was never said as its place, so its records named nothing opened."""
    home = tmp_path / "home" / "kunle"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    root, _ = _campaign(tmp_path / "work")
    manifest = root / "runs" / "a" / "manifest.json"
    found = json.loads(manifest.read_text())
    found["output_dir"] = str(home / "scratch" / "a")
    manifest.write_text(json.dumps(found))
    (root / "runs" / "a" / "analysis" / "analysis_manifest.json").write_text(json.dumps(
        {"trajectory_input": f"{home}/scratch/a/simulation/production.dcd"}))
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    texts = _texts_of(out)
    assert json.loads(texts["runs/a/manifest.json"])["output_dir"] == f"{STUDY}/runs/a"
    assert json.loads(texts["runs/a/analysis/analysis_manifest.json"])[
        "trajectory_input"] == f"{STUDY}/runs/a/simulation/production.dcd"


def test_a_moved_study_s_system_in_a_dot_folder_is_said_as_carried(tmp_path):
    """Review of series 3: moved since it ran, a study's system in a folder
    the archive leaves out kept the old paths, said in a folder not carried."""
    import shutil

    from fastmdxplora.simulation.pipeline import setup_records_of

    ran = tmp_path / "cluster"
    root = ran / "campaign"
    folder = root / ".prep" / "setup"
    _prepared(folder)
    _run(root, "a", folder, str(folder.parent))
    _of_one_run(root)
    here = tmp_path / "here"
    shutil.move(str(ran), here)
    out = tmp_path / "o.zip"
    share_study(here / "campaign", out, authors=AUTHORS)
    texts = _texts_of(out)
    assert f"setup_from: {STUDY}/prepared/prep" in texts["runs/a/resolved_config.yml"]
    assert ".prep" not in texts["prepared/prep/setup/setup_parameters.json"]
    opened = open_shared(str(out), into=tmp_path / "x")
    assert setup_records_of(opened / "runs" / "a") == \
        (opened / "prepared" / "prep" / "setup").resolve()


def test_a_fetched_study_s_preparation_is_found_as_the_run_finds_it(tmp_path):
    """Review of series 3: a study fetched back names its preparation as the
    machine it ran on did (`inputs/prep`), which the run finds through the
    record of what was sent; packing left that name out and refused it."""
    from fastmdxplora.simulation import pipeline
    from fastmdxplora.simulation.pipeline import setup_records_of

    prep = _prepared(tmp_path / "local" / "prep" / "setup")
    root = tmp_path / "campaign"
    run = _run(root, "a", prep, "inputs/prep")
    params = run / "simulation" / "simulation_parameters.json"
    record = json.loads(params.read_text())
    record["prepared_system"].update(given="inputs/prep",
                                     resolved="/remote/job/inputs/prep/setup",
                                     relative_to_run="../../inputs/prep/setup")
    params.write_text(json.dumps(record))
    manifest = run / "manifest.json"
    found = json.loads(manifest.read_text())
    found["phases"][0]["taken_from"] = "/remote/job/inputs/prep/setup"
    manifest.write_text(json.dumps(found))
    _of_one_run(root)
    (root / pipeline.FETCHED_RECORD).write_text(json.dumps(
        {"remote_dir": "/remote/job", "inputs": {"prep": str(prep.parent)}}))
    assert pipeline._the_system_recorded(run, record["prepared_system"]) == prep.resolve()
    share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    opened = open_shared(str(tmp_path / "o.zip"), into=tmp_path / "x")
    assert setup_records_of(opened / "runs" / "a") == \
        (opened / "prepared" / "prep" / "setup").resolve()


def test_a_study_opened_from_a_share_is_shared_again(tmp_path):
    """Review of series 3: a study opened from a share names its computer as
    the placeholder, which was then taken for a computer's name and refused."""
    from fastmdxplora.simulation.pipeline import setup_records_of

    root, _ = _campaign(tmp_path)
    share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    opened = open_shared(str(tmp_path / "o.zip"), into=tmp_path / "x")
    share_study(opened, tmp_path / "o2.zip", authors=AUTHORS)
    names = set(zipfile.ZipFile(tmp_path / "o2.zip").namelist())
    assert "prepared/preparation/setup/system.xml" in names
    again = open_shared(str(tmp_path / "o2.zip"), into=tmp_path / "y")
    for run in "ac":
        assert setup_records_of(again / "runs" / run) == \
            (again / "prepared" / "preparation" / "setup").resolve()
    assert not any(str(tmp_path) in text for text in _texts_of(tmp_path / "o2.zip").values())


def test_a_run_s_own_setup_copied_out_keeps_its_own_place(tmp_path):
    """Review of series 3: a run carried on elsewhere whose own setup was
    copied out for another run had its own setup said as the copy."""
    import shutil

    from fastmdxplora.simulation.pipeline import setup_records_of

    root = tmp_path / "campaign"
    scratch = tmp_path / "scratch" / "a"
    own = _prepared(scratch / "setup")
    ext = tmp_path / "ext" / "prep" / "setup"
    shutil.copytree(own, ext)
    shutil.copytree(own, root / "runs" / "a" / "setup")
    shutil.rmtree(scratch)
    _run(root, "b", ext, str(ext.parent))
    run = _run(root, "a", ext, str(ext.parent))
    manifest = json.loads((run / "manifest.json").read_text())
    manifest["output_dir"] = str(scratch)
    manifest["phases"][0] = {"name": "setup", "status": "ok", "output_dir": f"{scratch}/setup"}
    (run / "manifest.json").write_text(json.dumps(manifest))
    record = json.loads((run / "simulation" / "simulation_parameters.json").read_text())
    record.pop("prepared_system")
    record["parameters"].pop("setup_from")
    (run / "simulation" / "simulation_parameters.json").write_text(json.dumps(record))
    (run / "resolved_config.yml").write_text(f"output: {scratch}\n")
    _of_one_run(root)
    batch = json.loads((root / "batch_manifest.json").read_text())
    batch["planned"] = [{"run_id": "a"}, {"run_id": "b"}]
    (root / "batch_manifest.json").write_text(json.dumps(batch))
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    own_record = json.loads(_texts_of(out)["runs/a/setup/setup_parameters.json"])
    assert own_record["parameters"]["_retained_pdb"] == f"{STUDY}/runs/a/setup/retained.pdb"
    opened = open_shared(str(out), into=tmp_path / "x")
    assert setup_records_of(opened / "runs" / "a") == (opened / "runs" / "a" / "setup").resolve()


def test_a_name_that_is_a_loop_of_links_is_a_refusal_or_found(tmp_path):
    """Review of series 3: a recorded name that is a loop of links raised an
    error that was no refusal."""
    root, elsewhere = _campaign(tmp_path)
    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    config = root / "runs" / "c" / "resolved_config.yml"
    config.write_text(config.read_text().replace(f"setup_from: {elsewhere}/setup",
                                                 f"setup_from: {loop}"))
    try:
        share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    except ShareRefused:
        pass


def test_where_a_fetched_study_was_sent_from_is_not_packed(tmp_path):
    """Review of series 3: `fetched.json`, the record of where on this
    computer a fetched study's inputs were sent from, went as written."""
    test_a_fetched_study_s_preparation_is_found_as_the_run_finds_it(tmp_path)
    assert "fetched.json" not in zipfile.ZipFile(tmp_path / "o.zip").namelist()


def test_a_fetched_study_s_inputs_are_named_as_carried(tmp_path):
    """Review of series 3: a study fetched back kept its preparation named as
    the machine it ran on named it (`inputs/wt-prep`, and that machine's
    folder with the login in it), so its Config named nothing once opened."""
    from fastmdxplora.simulation import pipeline

    remote = "/scratch/kaina/fastmdxplora-jobs/job42"
    prep = _prepared(tmp_path / "preps" / "wt-prep" / "setup")
    root = tmp_path / "studies" / "job42"
    run = _run(root, "a", prep, "inputs/wt-prep")
    record = json.loads((run / "simulation" / "simulation_parameters.json").read_text())
    record["prepared_system"].update(given="inputs/wt-prep",
                                     resolved=f"{remote}/inputs/wt-prep/setup",
                                     relative_to_run="../../../inputs/wt-prep/setup")
    (run / "simulation" / "simulation_parameters.json").write_text(json.dumps(record))
    manifest = json.loads((run / "manifest.json").read_text())
    manifest["output_dir"] = f"{remote}/run/runs/a"
    manifest["phases"][0]["taken_from"] = f"{remote}/inputs/wt-prep/setup"
    (run / "manifest.json").write_text(json.dumps(manifest))
    (run / "resolved_config.yml").write_text(
        f"output: {remote}/run/runs/a\nsimulation:\n  setup_from: inputs/wt-prep\n")
    with (run / "report" / "report.md").open("a") as report:
        report.write(f"\nPrepared in {remote}/inputs/wt-prep.\n")
    _of_one_run(root)
    batch = json.loads((root / "batch_manifest.json").read_text())
    batch["output_dir"] = f"{remote}/run"
    (root / "batch_manifest.json").write_text(json.dumps(batch))
    (root / pipeline.FETCHED_RECORD).write_text(json.dumps(
        {"remote_dir": remote, "machine": "csudh-hpc",
         "inputs": {"wt-prep": str(prep.parent)}}))
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    texts = _texts_of(out)
    assert f"setup_from: {STUDY}/prepared/wt-prep" in texts["runs/a/resolved_config.yml"]
    assert "kaina" not in texts["runs/a/report/report.md"]
    opened = open_shared(str(out), into=tmp_path / "x")
    config = (opened / "runs" / "a" / "resolved_config.yml").read_text()
    assert f"setup_from: {opened.resolve()}/prepared/wt-prep" in config
    assert pipeline.setup_records_of(opened / "runs" / "a") == \
        (opened / "prepared" / "wt-prep" / "setup").resolve()


def test_a_carried_folder_named_for_the_computer_is_not_carried_by_that_name(tmp_path):
    """Review of series 4: a preparation in a folder named as the computer
    kept that name in the archive's own paths, and once the name was said as
    a placeholder in the records, the run opened with no system."""
    from fastmdxplora.simulation.pipeline import setup_records_of

    prep = _prepared(tmp_path / COMPUTER / "setup")
    root = tmp_path / "campaign"
    _run(root, "a", prep, str(prep.parent))
    _of_one_run(root)
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    assert not any(COMPUTER in n for n in zipfile.ZipFile(out).namelist())
    opened = open_shared(str(out), into=tmp_path / "x")
    assert setup_records_of(opened / "runs" / "a") == \
        (opened / "prepared" / "preparation" / "setup").resolve()


def test_a_loop_of_links_in_a_run_is_a_refusal_or_shared(tmp_path):
    """Review of series 4: a run's own `setup` that is a loop of links raised
    an error that was no refusal."""
    root, _ = _campaign(tmp_path)
    loop = root / "runs" / "a" / "setup"
    loop.symlink_to(loop)
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    assert refused.value.code == "environment.share.not_finished"
    assert "runs/a/setup" in str(refused.value)


def test_a_fetched_input_holding_several_preparations_names_each_its_own(tmp_path):
    """Review of series 4: an input naming a folder of preparations was said
    as the folder of all carried ones, so one run's Config named another
    run's system."""
    import yaml

    from fastmdxplora.simulation import pipeline

    remote = "/remote/job"
    wt = _prepared(tmp_path / "local" / "preps" / "wt" / "setup")
    mut = _prepared(tmp_path / "local" / "preps" / "mut" / "setup")
    (mut / "system.xml").write_text("<System atoms='4'/>")
    other = _prepared(tmp_path / "a" / "mut" / "setup")
    (other / "system.xml").write_text("<System atoms='5'/>")
    root = tmp_path / "studies" / "job"
    for run_id, prep, given in (("w", wt, "inputs/preps/wt"), ("m", mut, "inputs/preps/mut"),
                                ("o", other, str(other.parent))):
        run = _run(root, run_id, prep, given)
        if given.startswith("inputs/"):
            path = run / "simulation" / "simulation_parameters.json"
            record = json.loads(path.read_text())
            record["prepared_system"].update(given=given, resolved=f"{remote}/{given}/setup",
                                             relative_to_run=f"../../../{given}/setup")
            path.write_text(json.dumps(record))
            manifest = json.loads((run / "manifest.json").read_text())
            manifest["phases"][0]["taken_from"] = f"{remote}/{given}/setup"
            (run / "manifest.json").write_text(json.dumps(manifest))
    _of_one_run(root, "w")
    batch = json.loads((root / "batch_manifest.json").read_text())
    batch["planned"] = [{"run_id": r} for r in "wmo"]
    (root / "batch_manifest.json").write_text(json.dumps(batch))
    (root / pipeline.FETCHED_RECORD).write_text(json.dumps(
        {"remote_dir": remote, "inputs": {"preps": str(tmp_path / "local" / "preps")}}))
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    opened = open_shared(str(out), into=tmp_path / "x")
    for run in "wmo":
        named = yaml.safe_load((opened / "runs" / run / "resolved_config.yml").read_text())[
            "simulation"]["setup_from"]
        assert pipeline.where_a_prepared_system_sits(Path(named)).resolve() == \
            pipeline.setup_records_of(opened / "runs" / run), run


def test_systems_another_one_now_are_said_with_every_run(tmp_path):
    """Review of series 4: a system that is another one now named only the
    first run that used it."""
    root, elsewhere = _campaign(tmp_path)
    (elsewhere / "setup" / "system.xml").write_text("<System atoms='9'/>")
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    assert str(refused.value).startswith(f"The runs a, c of the study in {root} simulated a "
                                         "prepared system that is another one now")
    assert ".;" not in str(refused.value) and ".)" not in str(refused.value)
    assert str(refused.value).endswith(").")


def _unreadable(monkeypatch, *folders: Path) -> None:
    """``folders`` read as a folder the person may not read, whoever runs the
    tests."""
    import os

    real, real_stat = os.scandir, os.stat
    blocked = {str(f) for f in folders}
    entered = {os.path.join(f, ".") for f in blocked}

    def scandir(path="."):
        if not isinstance(path, int) and os.fspath(path) in blocked:
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real(path)

    def stat(path, *args, **kwargs):
        if not isinstance(path, int) and os.fspath(path) in entered:
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "scandir", scandir)
    monkeypatch.setattr(os, "stat", stat)


def _unopened(monkeypatch, *files: Path) -> None:
    """``files`` read as files the person may not read, whoever runs the
    tests."""
    import builtins
    import io
    import os

    blocked = {str(f) for f in files}

    def guarded(real):
        def opened(file, *args, **kwargs):
            if not isinstance(file, int) and os.fspath(file) in blocked:
                raise PermissionError(13, "Permission denied", os.fspath(file))
            return real(file, *args, **kwargs)
        return opened

    monkeypatch.setattr(io, "open", guarded(io.open))
    monkeypatch.setattr(builtins, "open", guarded(builtins.open))


def test_an_unreadable_folder_of_a_run_is_refused_not_left_out(tmp_path, monkeypatch):
    """Hands-on review of series 4: a folder of a run that could not be read
    was left out without a word, and the study opened as finished without
    it. One never shared is still left out."""
    root, _ = _campaign(tmp_path)
    (root / "runs" / "b" / ".cache").mkdir()
    _unreadable(monkeypatch, root / "runs" / "b" / "report", root / "runs" / "b" / ".cache")
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    assert refused.value.code == "environment.share.not_finished"
    assert str(refused.value).startswith(f"{root}/runs/b/report could not be read")
    assert "Make it readable and share it again." in str(refused.value)
    assert not (tmp_path / "o.zip").exists()
    monkeypatch.undo()
    _unreadable(monkeypatch, root / "runs" / "b" / ".cache")
    share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    assert "runs/b/report/report.md" in zipfile.ZipFile(tmp_path / "o.zip").namelist()


def test_an_unreadable_folder_of_a_carried_system_is_refused(tmp_path, monkeypatch):
    """Hands-on review of series 4: the ligands of a preparation carried in
    went missing when their folder could not be read."""
    root, elsewhere = _campaign(tmp_path)
    _unreadable(monkeypatch, elsewhere / "setup" / "ligands")
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    assert refused.value.code == "environment.share.not_finished"
    assert str(refused.value).startswith(f"{elsewhere}/setup/ligands could not be read")


def test_a_prepared_system_that_cannot_be_read_is_refused(tmp_path, monkeypatch):
    """Hands-on review of series 4: a preparation the person may not read
    ended the share in a traceback."""
    from fastmdxplora.simulation import pipeline

    root, elsewhere = _campaign(tmp_path)
    where = elsewhere / "setup" / "system.xml"

    def denied(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied", str(where))

    monkeypatch.setattr(pipeline, "_the_system_recorded", denied)
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    assert refused.value.code == "environment.share.not_finished"
    assert str(refused.value).startswith(f"{where}, which the study's runs simulated from, "
                                         "could not be read (Permission denied)")


def test_a_record_of_runs_that_cannot_be_read_says_so(tmp_path, monkeypatch):
    """Hands-on review of series 4: an unreadable `batch_manifest.json` was
    said to name no runs."""
    root, _ = _campaign(tmp_path)
    record = root / "batch_manifest.json"
    _unopened(monkeypatch, record)
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    assert refused.value.code == "environment.share.not_finished"
    assert str(refused.value).startswith(f"{record}, the record of its runs, could not be "
                                         "read (Permission denied)")


def test_a_placeholder_in_markdown_shows_as_written(tmp_path):
    """Hands-on review of series 4: the computer's name said in a report
    showed as "**FASTMDX_HOST**", a word in bold. In code it is left as it
    is, as code shows it."""
    from fastmdxplora.gui.report_page import render_markdown

    root, _ = _campaign(tmp_path)
    with (root / "runs" / "a" / "report" / "report.md").open("a", encoding="utf-8") as report:
        report.write(f"\nOn `{COMPUTER}`:\n\n```\nhost {COMPUTER}\n```\n")
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    text = zipfile.ZipFile(out).read("runs/a/report/report.md").decode()
    assert "Ran on \\_\\_FASTMDX_HOST\\_\\_." in text
    assert "On `__FASTMDX_HOST__`:" in text and "host __FASTMDX_HOST__\n" in text
    shown, _ = render_markdown(text)
    assert "<strong>" not in shown and "Ran on __FASTMDX_HOST__." in shown


def test_a_run_folder_that_cannot_be_read_is_refused_by_its_name(tmp_path, monkeypatch):
    """Review of series 4: a run folder the person may not read (a
    colleague's, kept to them alone) ended in a traceback, and a run's
    Manifest that could not be read was said to be no study."""
    root, _ = _campaign(tmp_path)
    _unreadable(monkeypatch, root / "runs" / "b")
    _unopened(monkeypatch, root / "runs" / "b" / "manifest.json")
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    assert refused.value.code == "environment.share.not_finished"
    assert str(refused.value) == (f"{root}/runs/b could not be read (Permission denied); "
                                  "nothing was written. Make it readable and share it again.")
    monkeypatch.undo()
    _unopened(monkeypatch, root / "runs" / "b" / "manifest.json")
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    assert refused.value.code == "environment.share.not_finished"
    assert str(refused.value).startswith(f"{root}/runs/b/manifest.json, the Manifest of the "
                                         "run b, could not be read (Permission denied)")
    # One that can be listed and not entered is said as the folder too.
    import os

    real, real_scandir = os.stat, os.scandir
    inside = str(root / "runs") + "/"

    def shut(path) -> bool:
        return not isinstance(path, int) and os.fspath(path).startswith(inside)

    def stat(path, *args, **kwargs):
        if shut(path):
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real(path, *args, **kwargs)

    def scandir(path="."):
        if shut(path):
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real_scandir(path)

    import builtins
    import io

    def guarded(opener):
        def opened(file, *args, **kwargs):
            if shut(file):
                raise PermissionError(13, "Permission denied", os.fspath(file))
            return opener(file, *args, **kwargs)
        return opened

    monkeypatch.undo()
    monkeypatch.setattr(io, "open", guarded(io.open))
    monkeypatch.setattr(builtins, "open", guarded(builtins.open))
    monkeypatch.setattr(os, "stat", stat)
    monkeypatch.setattr(os, "scandir", scandir)
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    assert str(refused.value).startswith(f"{root}/runs could not be read (Permission denied)")


def test_a_file_that_cannot_be_read_says_why_once(tmp_path, monkeypatch):
    """Review of series 4: a file that could not be read was said with its
    path twice, the second time inside the error's own words."""
    root, _ = _campaign(tmp_path)
    report = root / "runs" / "a" / "report" / "report.md"
    _unopened(monkeypatch, report)
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    assert str(refused.value) == (f"{report} could not be read (Permission denied); nothing "
                                  "was written. Make it readable and share it again.")


def test_a_carried_system_that_cannot_be_read_is_not_said_missing(tmp_path, monkeypatch):
    """Review of series 4: a preparation whose `system.xml` could not be read
    was said not to be there, and putting it back, the remedy given, could
    not be done."""
    root, elsewhere = _campaign(tmp_path)
    system = elsewhere / "setup" / "system.xml"
    _unopened(monkeypatch, system)
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    assert refused.value.code == "environment.share.not_finished"
    assert "whose system.xml could not be read (Permission denied)" in str(refused.value)
    assert "Make it readable and share it again." in str(refused.value)


def test_a_carried_folder_named_with_the_computer_s_name_is_renamed(tmp_path):
    """Review of series 4: a folder named for the computer with more to its
    name (`labbox07.old`) kept it in the archive, and its records named the
    folder as the placeholder, so the run opened with no system."""
    from fastmdxplora.simulation.pipeline import setup_records_of

    for n, folder in enumerate((f"{COMPUTER}.old", f"prep-{COMPUTER.upper()}",
                                f"{Path.home().name}.old")):
        prep = _prepared(tmp_path / str(n) / folder / "setup")
        root = tmp_path / str(n) / "campaign"
        _run(root, "a", prep, str(prep.parent))
        _of_one_run(root)
        out = tmp_path / str(n) / "o.zip"
        share_study(root, out, authors=AUTHORS)
        names = zipfile.ZipFile(out).namelist()
        assert not any(folder.casefold() in name.casefold() for name in names), folder
        opened = open_shared(str(out), into=tmp_path / str(n) / "x")
        assert setup_records_of(opened / "runs" / "a") == \
            (opened / "prepared" / "preparation" / "setup").resolve()


def _fetched(tmp_path: Path, name: str, given: Path, remote: str) -> Path:
    """A study fetched back from where it ran (``remote``), its run
    simulated from the input ``name`` sent from ``given``."""
    from fastmdxplora.simulation import pipeline

    prep = given if (given / "system.xml").is_file() else given / "setup"
    root = tmp_path / "studies" / "job42"
    run = _run(root, "a", prep, f"inputs/{name}")
    inner = "" if prep == given else "/setup"
    record = json.loads((run / "simulation" / "simulation_parameters.json").read_text())
    record["prepared_system"].update(given=f"inputs/{name}",
                                     resolved=f"{remote}/inputs/{name}{inner}",
                                     relative_to_run=f"../../../inputs/{name}{inner}")
    (run / "simulation" / "simulation_parameters.json").write_text(json.dumps(record))
    manifest = json.loads((run / "manifest.json").read_text())
    manifest["output_dir"] = f"{remote}/run/runs/a"
    manifest["phases"][0]["taken_from"] = f"{remote}/inputs/{name}{inner}"
    (run / "manifest.json").write_text(json.dumps(manifest))
    (run / "resolved_config.yml").write_text(
        f"output: {remote}/run/runs/a\nsimulation:\n  setup_from: inputs/{name}\n")
    _of_one_run(root)
    batch = json.loads((root / "batch_manifest.json").read_text())
    batch["output_dir"] = f"{remote}/run"
    (root / "batch_manifest.json").write_text(json.dumps(batch))
    (root / pipeline.FETCHED_RECORD).write_text(json.dumps(
        {"remote_dir": remote, "machine": "csudh-hpc", "inputs": {name: str(given)}}))
    return root


def test_a_fetched_input_that_is_the_system_s_own_folder_is_said_as_carried(tmp_path):
    """Review of series 4: an input sent as the system's own folder
    (`.../wt-prep/setup`), carried in the folder holding it, kept its name as
    sent, so the opened Config named nothing."""
    import yaml

    from fastmdxplora.simulation import pipeline

    prep = _prepared(tmp_path / "preps" / "wt-prep" / "setup")
    root = _fetched(tmp_path, "wt-setup", prep, "/scratch/kaina/fastmdxplora-jobs/job42")
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    texts = _texts_of(out)
    assert f"setup_from: {STUDY}/prepared/wt-prep/setup" in texts["runs/a/resolved_config.yml"]
    opened = open_shared(str(out), into=tmp_path / "x")
    named = yaml.safe_load((opened / "runs" / "a" / "resolved_config.yml").read_text())[
        "simulation"]["setup_from"]
    assert pipeline.where_a_prepared_system_sits(Path(named)).resolve() == \
        pipeline.setup_records_of(opened / "runs" / "a")


def test_a_link_among_the_study_s_files_is_refused_not_left_out(tmp_path):
    """Review of series 4: a run's trajectory or folder kept as a link (to
    scratch, as on a cluster) was left out without a word, and the study
    opened without it."""
    root, _ = _campaign(tmp_path)
    trajectory = root / "runs" / "a" / "simulation" / "production.dcd"
    kept = tmp_path / "scratch" / "production.dcd"
    kept.parent.mkdir()
    trajectory.rename(kept)
    trajectory.symlink_to(kept)
    (root / "runs" / "b" / "agent" / "notes").symlink_to(tmp_path / "scratch")
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    assert refused.value.code == "environment.share.not_finished"
    assert str(refused.value).startswith(
        f"The study in {root} holds a link, which a shared study does not carry: "
        "runs/a/simulation/production.dcd. Put the files they lead to in their place")


def test_code_in_markdown_is_left_as_code_shows_it():
    """Review of series 4: an indented block of code had its placeholders
    escaped, so it showed the escapes; a `.markdown` file was not escaped."""
    from fastmdxplora.sharing.pack import _markdown_plain

    text = "Ran on __FASTMDX_HOST__:\n\n    host __FASTMDX_HOST__\n\nOn __FASTMDX_HOST__.\n"
    assert _markdown_plain(text) == ("Ran on \\_\\_FASTMDX_HOST\\_\\_:\n\n"
                                     "    host __FASTMDX_HOST__\n\n"
                                     "On \\_\\_FASTMDX_HOST\\_\\_.\n")


def test_a_markdown_file_by_its_long_suffix_is_escaped_too(tmp_path):
    root, _ = _campaign(tmp_path)
    (root / "runs" / "a" / "report" / "notes.markdown").write_text(f"On {COMPUTER}.\n")
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    assert zipfile.ZipFile(out).read("runs/a/report/notes.markdown").decode() == \
        "On \\_\\_FASTMDX_HOST\\_\\_.\n"


def test_where_a_fetched_study_came_from_that_cannot_be_read_is_refused(tmp_path, monkeypatch):
    """Hands-on review of series 4: an unreadable `fetched.json` was passed
    over without a word, and its runs' systems were then said not found at
    the other machine's folders, where none could be put back."""
    from fastmdxplora.simulation import pipeline

    prep = _prepared(tmp_path / "preps" / "wt-prep" / "setup")
    root = _fetched(tmp_path, "wt-prep", prep.parent, "/scratch/kaina/fastmdxplora-jobs/job42")
    _unopened(monkeypatch, root / pipeline.FETCHED_RECORD)
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    assert refused.value.code == "environment.share.not_finished"
    assert str(refused.value).startswith(
        f"{root}/fetched.json, where the study was fetched back from, could not be read")


def test_a_study_shared_again_lists_every_placeholder_it_holds(tmp_path):
    """Hands-on review of series 4: shared again, a study opened from a share
    listed only `__FASTMDX_STUDY__`, though its files held the others."""
    out, _, _, _ = _shared(tmp_path)
    opened = open_shared(str(out), into=tmp_path / "x")
    again = tmp_path / "again.zip"
    share_study(opened, again, authors=AUTHORS)
    crate = json.loads(zipfile.ZipFile(again).read(METADATA))
    root = next(e for e in crate["@graph"] if e.get("@id") == "./")
    listed = {p["fmx:placeholder"]: [i["@id"] for i in p["fmx:in"]]
              for p in root["fmx:placeholders"]}
    assert "runs/a/report/report.md" in listed["__FASTMDX_HOST__"]


def test_an_archive_that_cannot_be_read_says_so(tmp_path, monkeypatch):
    """Hands-on review of series 4: an archive the person may not read was
    said to be no zip archive."""
    out, _, _, _ = _shared(tmp_path)
    _unopened(monkeypatch, out)
    with pytest.raises(ShareRefused) as refused:
        open_shared(str(out), into=tmp_path / "x")
    assert str(refused.value) == (f"{out} could not be read (Permission denied); nothing was "
                                  "opened. Make it readable and open it again.")


def test_a_link_only_to_what_is_never_shared_does_not_stop_it(tmp_path):
    """Review of series 4: a link to a folder a shared study never carries
    (the Agent's, `previous/`, the Viewer's snapshots), or to a file it
    leaves out unless all is asked for, refused the study."""
    root, _ = _campaign(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (root / "runs" / "a" / "previous").symlink_to(scratch)
    (root / "runs" / "a" / "simulation" / "live_frames").symlink_to(scratch)
    (root / "agent" / "conversations").rename(scratch / "conversations")
    (root / "agent" / "conversations").symlink_to(scratch / "conversations")
    (scratch / "cluster_medoid_0.pdb").write_text("END\n")
    (root / "runs" / "a" / "analysis" / "cluster_medoid_0.pdb").symlink_to(
        scratch / "cluster_medoid_0.pdb")
    (root / "runs" / "a" / "simulation" / "x.tmp").symlink_to(scratch / "cluster_medoid_0.pdb")
    share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "all.zip", authors=AUTHORS, everything=True)
    assert "runs/a/analysis/cluster_medoid_0.pdb" in str(refused.value)
    assert "previous" not in str(refused.value) and "x.tmp" not in str(refused.value)


def test_a_carried_folder_that_cannot_be_entered_is_said_by_its_name(tmp_path, monkeypatch):
    """Review of series 4: a preparation's folder that could not be entered
    was said by the `system.xml` in it, which cannot be made readable while
    the folder holding it cannot be entered."""
    import os

    root, elsewhere = _campaign(tmp_path)
    folder = str(elsewhere / "setup")
    real_stat, real_scandir = os.stat, os.scandir

    def shut(path) -> bool:
        return not isinstance(path, int) and (
            os.fspath(path) in (folder, os.path.join(folder, "."))
            or os.fspath(path).startswith(folder + "/"))

    def stat(path, *args, **kwargs):
        if shut(path) and os.fspath(path) != folder:
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real_stat(path, *args, **kwargs)

    def scandir(path="."):
        if shut(path):
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "stat", stat)
    monkeypatch.setattr(os, "scandir", scandir)
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    assert str(refused.value).startswith(f"{folder}, which the study's runs simulated from, "
                                         "could not be read (Permission denied)")


def test_a_fetched_input_beside_the_system_is_not_said_as_carried(tmp_path):
    """Review of series 4: an input sent from the folder carried but not from
    the system in it (a ligand beside it) was said as a path in the archive
    that holds nothing."""
    from fastmdxplora.simulation import pipeline

    prep = _prepared(tmp_path / "preps" / "wt-prep" / "setup")
    (prep.parent / "ligs").mkdir()
    (prep.parent / "ligs" / "x.sdf").write_text("X\n\nM  END\n")
    root = _fetched(tmp_path, "wt-prep", prep.parent, "/scratch/kaina/fastmdxplora-jobs/job42")
    record = json.loads((root / pipeline.FETCHED_RECORD).read_text())
    record["inputs"]["x.sdf"] = str(prep.parent / "ligs" / "x.sdf")
    (root / pipeline.FETCHED_RECORD).write_text(json.dumps(record))
    with (root / "runs" / "a" / "resolved_config.yml").open("a") as config:
        config.write("ligand: inputs/x.sdf\n")
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    assert "ligand: inputs/x.sdf\n" in _texts_of(out)["runs/a/resolved_config.yml"]


def test_indented_code_after_a_heading_is_left_as_code_shows_it():
    from fastmdxplora.sharing.pack import _markdown_plain

    text = "## Host\n    host __FASTMDX_HOST__\n"
    assert _markdown_plain(text) == text


def test_a_record_that_cannot_be_read_is_named_not_a_folder_above_the_study(tmp_path,
                                                                            monkeypatch):
    """Review of series 5: a record that could not be read, in a study kept
    under a folder that cannot be listed (a cluster's home), was said as that
    folder, which the person cannot change."""
    root, _ = _campaign(tmp_path)
    record = root / "batch_manifest.json"
    _unopened(monkeypatch, record)
    import os

    real = os.scandir

    def scandir(path="."):
        if not isinstance(path, int) and os.fspath(path) == str(tmp_path):
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real(path)

    # Another person's folder above the study, at 711: entered, not listed.
    monkeypatch.setattr(os, "scandir", scandir)
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    assert str(refused.value).startswith(f"{record}, the record of its runs, could not be read")


def test_a_fetched_input_holding_the_system_is_said_as_carried(tmp_path):
    """Review of series 5: an input sent as the folder holding the system
    (`.../wt-prep/shared_setup`, the system in its `setup/`) kept its name as
    sent, so the opened Config named nothing."""
    import yaml

    from fastmdxplora.simulation import pipeline

    prep = _prepared(tmp_path / "preps" / "wt-prep" / "shared_setup" / "setup")
    root = _fetched(tmp_path, "shared", prep.parent, "/scratch/kaina/fastmdxplora-jobs/job42")
    out = tmp_path / "o.zip"
    share_study(root, out, authors=AUTHORS)
    config = _texts_of(out)["runs/a/resolved_config.yml"]
    assert "inputs/shared" not in config
    opened = open_shared(str(out), into=tmp_path / "x")
    named = yaml.safe_load((opened / "runs" / "a" / "resolved_config.yml").read_text())[
        "simulation"]["setup_from"]
    assert pipeline.where_a_prepared_system_sits(Path(named)).resolve() == \
        pipeline.setup_records_of(opened / "runs" / "a")


def test_a_link_leading_nowhere_where_nothing_is_shared_does_not_stop_it(tmp_path):
    """Review of series 5: a link to scratch since emptied, at a place a
    shared study never carries (`previous/`, a run the record of runs does
    not name), refused the study."""
    root, elsewhere = _campaign(tmp_path)
    gone = tmp_path / "purged"
    for link in (root / "previous", root / "runs" / "a" / "previous",
                 root / "runs" / "a" / "simulation" / "live_frames", root / "runs" / "zz",
                 elsewhere / "setup" / "x.tmp"):
        link.symlink_to(gone)
    share_study(root, tmp_path / "o.zip", authors=AUTHORS)
    (root / "runs" / "a" / "report" / "notes.md").symlink_to(gone)
    with pytest.raises(ShareRefused) as refused:
        share_study(root, tmp_path / "again.zip", authors=AUTHORS)
    assert "runs/a/report/notes.md" in str(refused.value)


def test_indented_code_after_a_setext_heading_or_a_fence_is_left_as_it_is():
    from fastmdxplora.sharing.pack import _markdown_plain

    text = ("Host\n----\n    host __FASTMDX_HOST__\n"
            "```\nx\n```\n    host __FASTMDX_HOST__\n")
    assert _markdown_plain(text) == text
