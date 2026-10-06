"""A view of a study is written as a scene file, in MolViewSpec.

A view in the Viewer lived in the page and in ``viewer_views.json``, which
nothing else reads. A scene is the same view as a MolViewSpec archive
(``.mvsx``): the atoms shown at the frame shown, the study's DSSP as HELIX
and SHEET records, the colouring (a result on the Viewer's scale), the parts
shown, the selections named and the camera, which any viewer built on Mol*
opens. It is checked here against MolViewSpec's own schema (the molviewspec
package, in the tests only) and by Mol* loading it.

Haemoglobin (1HHO), chain A stretched out of its helices in the second half
of its frames and analysed for RMSF; trypsin and benzamidine (3PTB) for the
ligand and its pocket.
"""

from __future__ import annotations

import gzip
import io
import json
import math
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pytest

md = pytest.importorskip("mdtraj")

from fastmdxplora.scenes import (  # noqa: E402
    MOST_LABELS,
    build_scene,
    read_scene,
    scene_bytes,
    scenes_of,
    write_scene,
)

from tests import viewer_hooks as hooks  # noqa: E402

DATA = Path(__file__).parent / "data" / "assemblies"
CAMERA = {"position": [12.0, -4.0, 90.0], "target": [2.0, 1.0, -3.0], "up": [0.0, 1.0, 0.0],
          "fov": math.pi / 4, "mode": "perspective"}


@pytest.fixture(scope="module")
def helical(tmp_path_factory) -> Path:
    from fastmdxplora.gui.trajectory_frames import frames_info
    from tests.test_the_cartoon_is_dssp_of_each_frame import _helical_study

    root = _helical_study(tmp_path_factory.mktemp("scene") / "study", analyse=True)
    assert frames_info(root)["available"]
    return root


@pytest.fixture(scope="module")
def trypsin(tmp_path_factory) -> Path:
    from fastmdxplora.gui.trajectory_frames import frames_info

    root = tmp_path_factory.mktemp("scene-ligand") / "study"
    (root / "setup").mkdir(parents=True)
    (root / "simulation").mkdir()
    raw = root / "setup" / "input.pdb"
    raw.write_bytes(gzip.decompress((DATA / "3PTB.pdb.gz").read_bytes()))
    whole = md.load_pdb(str(raw))
    complex_ = whole.atom_slice(whole.topology.select("protein or resname BEN"))
    complex_.save_pdb(str(root / "setup" / "topology.pdb"))
    xyz = np.repeat(complex_.xyz, 3, axis=0)
    xyz += np.random.default_rng(2).normal(0, 0.01, xyz.shape).astype(np.float32)
    trajectory = md.Trajectory(xyz, complex_.topology)
    trajectory[0].save_pdb(str(root / "simulation" / "trajectory_topology.pdb"))
    trajectory.save_dcd(str(root / "simulation" / "production.dcd"))
    (root / "simulation" / "live_status.json").write_text(json.dumps(
        {"status": "completed", "stage": "production"}), encoding="utf-8")
    assert frames_info(root)["available"]
    return root


def _components(state: dict) -> list[dict]:
    structure = state["root"]["children"][0]["children"][0]["children"][0]
    assert structure["kind"] == "structure"
    return structure.get("children", [])


def _validated(state: dict) -> None:
    nodes = pytest.importorskip("molviewspec.nodes")
    nodes.State.model_validate(state)


def _atoms(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith(("ATOM", "HETATM"))]


def _records(text: str) -> dict[str, set]:
    """The residues the HELIX and SHEET records cover."""
    atoms = _atoms(text)
    order = []
    for line in atoms:
        key = (line[21].strip(), int(line[22:26]), line[26].strip())
        if not order or order[-1] != key:
            order.append(key)
    covered: dict[str, set] = {"H": set(), "E": set()}
    for line in text.splitlines():
        if line.startswith("HELIX "):
            begin = (line[19].strip(), int(line[21:25]), line[25].strip())
            end = (line[31].strip(), int(line[33:37]), line[37].strip())
            kind = "H"
        elif line.startswith("SHEET "):
            begin = (line[21].strip(), int(line[22:26]), line[26].strip())
            end = (line[32].strip(), int(line[33:37]), line[37].strip())
            kind = "E"
        else:
            continue
        a, b = order.index(begin), order.index(end)
        covered[kind].update(order[a:b + 1])
    return covered


# --------------------------------------------------------------------------
# What a scene holds
# --------------------------------------------------------------------------

def test_a_frame_s_scene_holds_its_atoms_cartoon_and_camera(helical):
    from fastmdxplora.gui.by_residue import secondary_structure

    view = {"camera": CAMERA, "frame": 4, "representation": "cartoon", "colour": "chain"}
    scene = build_scene(helical, view)
    assert scene["ok"] and scene["of"] == "frames" and scene["frame"] == 4
    _validated(scene["state"])
    text = scene["files"]["structure.pdb"]
    frames = md.load_dcd(str(helical / "simulation" / "frames.dcd"),
                         top=str(helical / "simulation" / "frames_topology.pdb"))
    written = np.array([(float(line[30:38]), float(line[38:46]), float(line[46:54]))
                        for line in _atoms(text)])
    assert written == pytest.approx(frames.xyz[4] * 10, abs=1e-3)
    # The study's DSSP of that frame, not Mol*'s own.
    said = secondary_structure(helical, "frames")
    codes = said["frames"][4]
    expected = {kind: {(row[0], row[1], row[2]) for row, code in zip(said["residues"], codes)
                       if code == kind} for kind in ("H", "E")}
    assert _records(text) == expected and expected["H"]
    # MolViewSpec's camera: the position brought to the field of view's
    # normalised distance, 2 sin(fov / 2) of the Viewer's.
    camera = next(n for n in scene["state"]["root"]["children"] if n["kind"] == "camera")
    assert camera["params"]["target"] == CAMERA["target"]
    scale = 2 * math.sin(math.pi / 8)
    assert camera["params"]["position"] == pytest.approx(
        [t + (p - t) * scale for p, t in zip(CAMERA["position"], CAMERA["target"])])
    polymer = _components(scene["state"])[0]
    assert polymer["params"]["selector"] == "polymer"
    colour = polymer["children"][0]["children"][0]
    assert colour["custom"] == {"molstar_color_theme_name": "chain-id"}
    assert scene["state"]["root"]["custom"]["fastmdxplora"]["frame"] == 4


def test_the_frames_late_helices_are_not_the_first_frame_s(helical):
    early = _records(build_scene(helical, {"frame": 0})["files"]["structure.pdb"])
    late = _records(build_scene(helical, {"frame": 5})["files"]["structure.pdb"])
    in_a = lambda covered: {k for k in covered["H"] if k[0] == "A"}  # noqa: E731
    assert len(in_a(late)) < len(in_a(early)) / 2


@pytest.mark.parametrize("colour, expected", [
    ("chain", {"molstar_color_theme_name": "chain-id"}),
    ("spectrum", {"molstar_color_theme_name": "sequence-id"}),
    ("residue", {"molstar_color_theme_name": "residue-name"}),
    ("secondary_structure", {"molstar_color_theme_name": "secondary-structure"}),
])
def test_each_colouring_is_mol_s_theme(helical, colour, expected):
    scene = build_scene(helical, {"colour": colour})
    assert scene["of"] == "structure"
    assert _components(scene["state"])[0]["children"][0]["children"][0]["custom"] == expected


def test_element_and_monochrome_colourings(helical):
    element = build_scene(helical, {"colour": "element"})
    node = _components(element["state"])[0]["children"][0]["children"][0]
    assert node["custom"]["molstar_color_theme_name"] == "element-symbol"
    mono = build_scene(helical, {"colour": "monochrome"})
    assert _components(mono["state"])[0]["children"][0]["children"][0]["params"] == {
        "color": "#ffffff"}


def _on_the_scale(value: float, low: float, high: float) -> str:
    """The Viewer's scale (viewer-engine.js): blue, white and red, evenly."""
    stops = [(44, 123, 182), (247, 247, 247), (215, 25, 28)]
    t = min(1.0, max(0.0, (value - low) / (high - low)))
    lower = min(1, int(t * 2))
    within = t * 2 - lower
    return "#" + "".join(f"{round(a + within * (b - a)):02x}"
                         for a, b in zip(stops[lower], stops[lower + 1]))


def test_a_result_is_coloured_on_the_viewer_s_scale(helical):
    from fastmdxplora.gui.by_residue import values_by_residue

    scene = build_scene(helical, {"colour": "result:rmsf"})
    _validated(scene["state"])
    node = _components(scene["state"])[0]["children"][0]["children"][0]
    assert node["kind"] == "color_from_uri" and node["params"]["uri"] == "colours.json"
    rows = json.loads(scene["files"]["colours.json"])
    rmsf = next(p for p in values_by_residue(helical)["properties"] if p["key"] == "rmsf")
    values = {(row[0] or "", row[1], row[2] or ""): row[3] for row in rmsf["values"]}
    coloured = {(r["auth_asym_id"], r["auth_seq_id"], r["pdbx_PDB_ins_code"] or ""): r["color"]
                for r in rows}
    chained = any(row[0] for row in rmsf["values"])
    checked = 0
    for (chain, number, icode), colour in coloured.items():
        value = values.get((chain if chained else "", number, icode))
        if value is None:
            continue
        assert colour == _on_the_scale(value, float(rmsf["low"]), float(rmsf["high"]))
        checked += 1
    assert checked > 100 and len(set(coloured.values())) > 20
    assert any("blue" in note and "red" in note for note in scene["notes"])


def test_a_result_the_study_has_not_is_said(helical):
    scene = build_scene(helical, {"colour": "result:nothing"})
    assert "colours.json" not in scene["files"]
    assert any("no result 'nothing'" in note for note in scene["notes"])
    node = _components(scene["state"])[0]["children"][0]["children"][0]
    assert node["custom"] == {"molstar_color_theme_name": "chain-id"}


def test_the_named_selections_are_in_the_scene(helical):
    frames = md.load_topology(str(helical / "simulation" / "frames_topology.pdb"))
    picked = [int(i) for i in frames.select("chainid 1 and resSeq 10 to 14")]
    selections = [
        {"name": "five", "kind": "expression", "expression": "chainid 1 and resSeq 10 to 14",
         "colour": "#0072b2", "shown": True, "representation": "sticks", "labelled": True},
        {"name": "gone", "kind": "residues", "residues": [["A", 20, "", "HIS"]],
         "colour": None, "shown": False, "representation": "none", "labelled": False},
        {"name": "bad", "kind": "expression", "expression": "resSeq and and",
         "shown": True, "representation": "sticks"},
    ]
    scene = build_scene(helical, {"frame": 2}, selections)
    _validated(scene["state"])
    components = _components(scene["state"])
    polymer = components[0]
    # Residue A 20 is hidden: the molecule's own representations leave it out.
    assert {"auth_asym_id": "A", "auth_seq_id": 20} not in polymer["params"]["selector"]
    assert {"auth_asym_id": "A", "auth_seq_id": 21} in polymer["params"]["selector"]
    layers = polymer["children"][0]["children"]
    assert layers[1]["params"] == {"selector": [{"atom_index": i} for i in picked],
                                   "color": "#0072b2"}
    sticks = [c for c in components if c["params"]["selector"] ==
              [{"atom_index": i} for i in picked]]
    assert sticks and sticks[0]["children"][0]["params"]["type"] == "ball_and_stick"
    labels = [c["children"][0]["params"]["text"] for c in components
              if c.get("children") and c["children"][0]["kind"] == "label"]
    assert len(labels) == 5 and labels[0].endswith(" 10")
    assert any("'bad' could not be read" in note for note in scene["notes"])


def test_labels_are_bounded(helical, monkeypatch):
    monkeypatch.setattr("fastmdxplora.scenes.MOST_LABELS", 50)
    scene = build_scene(helical, {}, [{"name": "all", "kind": "expression",
                                       "expression": "protein", "shown": True,
                                       "representation": "none", "labelled": True}])
    labels = [c for c in _components(scene["state"])
              if c.get("children") and c["children"][0]["kind"] == "label"]
    assert len(labels) == 50 < MOST_LABELS
    assert any("first 50 residue labels" in note for note in scene["notes"])


def test_the_ligand_and_its_pocket(trypsin):
    scene = build_scene(trypsin, {"frame": 1, "pocket_cutoff": 5.0,
                                  "shown": {"ions": True, "box": True, "water": True}})
    _validated(scene["state"])
    components = _components(scene["state"])
    ligand = [c for c in components if c["params"]["selector"] == [{"auth_comp_id": "BEN"}]]
    assert ligand and scene["state"]["root"]["custom"]["fastmdxplora"]["ligands"] == ["BEN"]
    # The pocket: residues with a heavy atom within 5 angstroms of the
    # ligand's heavy atoms, in the frame written.
    text = scene["files"]["structure.pdb"]
    path = trypsin / "pocket_check.pdb"
    path.write_text(text, encoding="utf-8")
    shown = md.load_pdb(str(path))
    heavy = shown.topology.select("resname BEN and not element H")
    protein = shown.topology.select("protein and not element H")
    near = md.compute_neighbors(shown, 0.5, heavy, haystack_indices=protein)[0]
    residues = [shown.topology.atom(i).residue for i in near]
    expected = {(residue.chain.chain_id, residue.resSeq) for residue in residues}
    pocket = [c for c in components if isinstance(c["params"]["selector"], list)
              and c["params"]["selector"] and "auth_seq_id" in c["params"]["selector"][0]
              and c["children"][0]["params"].get("size_factor") == 0.16]
    found = {(e["auth_asym_id"], e["auth_seq_id"]) for e in pocket[0]["params"]["selector"]}
    assert found == expected and found
    notes = " ".join(scene["notes"])
    assert "periodic box is not part of a scene" in notes and "hold no water" in notes


def test_a_superposed_frame_is_written_superposed(trypsin):
    from fastmdxplora.gui.trajectory_frames import superposed_frames

    scene = build_scene(trypsin, {"frame": 2, "superposed": "backbone"})
    said = superposed_frames(trypsin, "backbone")
    fitted = md.load_dcd(str(trypsin / "simulation" / said["file"]),
                         top=str(trypsin / "simulation" / "frames_topology.pdb"))
    written = np.array([(float(line[30:38]), float(line[38:46]), float(line[46:54]))
                        for line in _atoms(scene["files"]["structure.pdb"])])
    assert written == pytest.approx(fitted.xyz[2] * 10, abs=1e-3)


def test_a_view_without_a_camera_frames_the_molecule_and_a_lost_frame_is_said(helical):
    scene = build_scene(helical, {"frame": 99, "publication": True})
    kinds = [n["kind"] for n in scene["state"]["root"]["children"]]
    assert "camera" not in kinds and "focus" in kinds
    assert scene["of"] == "structure" and any("no frame 99" in n for n in scene["notes"])
    canvas = next(n for n in scene["state"]["root"]["children"] if n["kind"] == "canvas")
    assert canvas["params"]["background_color"] == "white"
    assert canvas["custom"]["molstar_postprocessing"] == {"enable_outline": True,
                                                         "enable_ssao": True}


def test_a_study_with_no_structure_has_no_scene(tmp_path):
    assert build_scene(tmp_path, {})["reason"] == "The study has no structure to show."


# --------------------------------------------------------------------------
# Written with the study
# --------------------------------------------------------------------------

def test_a_scene_is_written_listed_and_read(helical, tmp_path):
    said = write_scene(helical, "first look", {"frame": 1, "camera": CAMERA})
    assert said["ok"] and said["frame"] == 1
    archive = zipfile.ZipFile(io.BytesIO(Path(said["path"]).read_bytes()))
    assert set(archive.namelist()) == {"index.mvsj", "structure.pdb"}
    state = json.loads(archive.read("index.mvsj"))
    _validated(state)
    assert state["metadata"]["title"] == "first look" and state["metadata"]["version"] == "1.8"
    assert {"name": "first look", "bytes": Path(said["path"]).stat().st_size} in \
        scenes_of(helical)["scenes"]
    assert read_scene(helical, "first look", "structure.pdb").startswith(b"HELIX")
    assert read_scene(helical, "first look", "nothing") is None
    assert read_scene(helical, "../first look") is None
    assert read_scene(helical, "missing") is None
    assert scenes_of(tmp_path)["scenes"] == []
    assert scene_bytes({"state": {}, "files": {}})[:2] == b"PK"


@pytest.mark.parametrize("name", ["", " ", "../up", "a/b", "x" * 61, ".hidden"])
def test_a_scene_s_name_is_a_file_s(helical, name):
    said = write_scene(helical, name, {})
    assert not said["ok"] and "1 to 60" in said["reason"]


def test_a_scene_that_cannot_be_made_is_said(tmp_path):
    assert write_scene(tmp_path, "x", {}) == {"ok": False,
                                             "reason": "The study has no structure to show."}
    (tmp_path / "simulation").mkdir()
    (tmp_path / "simulation" / "frames_topology.pdb").write_text("nonsense", encoding="utf-8")
    from unittest import mock

    with mock.patch("fastmdxplora.scenes.build_scene", side_effect=RuntimeError("broken")):
        assert write_scene(tmp_path, "x", {})["reason"] == "The scene could not be made: broken"


def test_the_command_line_writes_a_scene(helical, tmp_path, capsys):
    from fastmdxplora.cli.main import main
    from fastmdxplora.gui.saved_views import save_view

    save_view(helical, "late", {"camera": CAMERA, "frame": 5, "colour": "spectrum"})

    def run(*args: str) -> tuple[int, str, str]:
        code = main(["scene", *args])
        out = capsys.readouterr()
        return code, out.out, out.err

    out = tmp_path / "late.mvsx"
    code, said, _ = run(str(helical), "--view", "late", "-o", str(out))
    assert code == 0 and "(frame 5)" in said and out.is_file()
    state = json.loads(zipfile.ZipFile(out).read("index.mvsj"))
    assert state["root"]["custom"]["fastmdxplora"]["view"]["colour"] == "spectrum"
    code, said, _ = run(str(helical), "--frame", "2", "--name", "two", "--no-selections")
    assert code == 0 and "(frame 2)" in said
    code, said, _ = run(str(helical), "--frame", "99", "--name", "lost")
    assert code == 0 and "(the structure)" in said and "no frame 99" in said
    code, _, err = run(str(helical), "--view", "none")
    assert code == 2 and "no view named 'none'" in err and "late" in err
    assert run(str(tmp_path / "missing"))[0] == 2
    code, _, err = run(str(tmp_path), "--name", "x")
    assert code == 1 and "no structure to show" in err


def test_the_command_is_the_package_s(helical):
    done = subprocess.run([sys.executable, "-m", "fastmdxplora", "scene", "--help"],
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0 and "MolViewSpec" in done.stdout


@pytest.mark.parametrize("representation, types", [
    ("surface", ["cartoon", "surface"]), ("sticks", ["ball_and_stick"]),
    ("ballAndStick", ["ball_and_stick"]), ("lines", ["line"]),
    ("spacefill", ["spacefill"]), ("backbone", ["backbone"]),
])
def test_each_representation_is_the_engine_s(helical, representation, types):
    scene = build_scene(helical, {"representation": representation, "colour": "chain"})
    _validated(scene["state"])
    polymer = _components(scene["state"])[0]
    assert [r["params"]["type"] for r in polymer["children"]] == types
    if representation == "surface":
        cartoon, surface = polymer["children"]
        assert cartoon["children"][-1] == {"kind": "opacity", "params": {"opacity": 0.18}}
        assert surface["children"][0]["params"] == {"color": "#d8d8dd"}
        assert surface["children"][-1]["params"] == {"opacity": 0.78}
    if representation == "sticks":
        assert polymer["children"][0]["params"]["size_factor"] == 0.18


def test_a_pocket_without_a_ligand_and_a_part_hidden_are_said(helical):
    scene = build_scene(helical, {"frame": 1, "superposed": "pocket"},
                        [{"name": "alphas", "kind": "expression",
                          "expression": "chainid 0 and resSeq 20 and name CA", "shown": False}])
    notes = " ".join(scene["notes"])
    assert "The frames are not superposed" in notes
    assert "1 residues are hidden only in part" in notes


def test_a_result_reversed_and_a_residue_without_a_value(trypsin, monkeypatch):
    property_ = {"key": "order", "label": "S2", "unit": "", "low": 0.0, "high": 1.0,
                 "reverse": True, "absent": None,
                 "values": [[None, 16, "", 0.0], [None, 17, "", 1.0], [None, 18, "", None]]}
    monkeypatch.setattr("fastmdxplora.gui.by_residue.values_by_residue",
                        lambda root: {"properties": [property_]})
    scene = build_scene(trypsin, {"colour": "result:order"})
    rows = {(r["auth_seq_id"], r["pdbx_PDB_ins_code"]): r["color"]
            for r in json.loads(scene["files"]["colours.json"])}
    # Reversed: the low end red, the high end blue; no value, grey.
    assert rows[(16, None)] == "#d7191c" and rows[(17, None)] == "#2c7bb6"
    assert rows[(18, None)] == "#5c5c66" and rows[(19, None)] == "#5c5c66"
    # The ligand is no residue of the colouring.
    assert (1, None) not in rows and len(rows) > 200


def test_a_short_peptide_with_its_water_and_a_label_with_an_insertion_code(tmp_path):
    root = tmp_path / "short"
    (root / "setup").mkdir(parents=True)
    raw = gzip.decompress((DATA / "3PTB.pdb.gz").read_bytes()).decode()
    kept = [line for line in raw.splitlines() if line.startswith(("ATOM", "HETATM"))
            and (line[17:20] == "HOH" and int(line[22:26]) < 420
                 or line.startswith("ATOM") and 183 <= int(line[22:26]) <= 186)]
    (root / "setup" / "topology.pdb").write_text("\n".join(kept + ["END", ""]),
                                                 encoding="utf-8")
    scene = build_scene(root, {"representation": "cartoon", "shown": {"water": True}},
                        [{"name": "a", "kind": "residues", "residues": [["A", 184, "A", "GLY"]],
                          "shown": True, "representation": "none", "labelled": True}])
    _validated(scene["state"])
    components = _components(scene["state"])
    selectors = [c["params"]["selector"] for c in components]
    assert "water" in selectors
    # A ribbon needs residues to be one: a short peptide's atoms too.
    assert sum(1 for c in components if c["params"]["selector"] == "polymer") == 2
    label = [c for c in components if c.get("children")
             and c["children"][0]["kind"] == "label"][0]
    assert label["params"]["selector"] == [{"auth_asym_id": "A", "auth_seq_id": 184,
                                            "pdbx_PDB_ins_code": "A"}]
    assert label["children"][0]["params"]["text"] == "GLY 184A"


def test_frames_of_other_atoms_than_their_topology_s_are_refused(helical, tmp_path):
    import shutil

    copy = tmp_path / "copy"
    shutil.copytree(helical, copy)
    topology = copy / "simulation" / "frames_topology.pdb"
    lines = topology.read_text(encoding="utf-8").splitlines()
    first = next(i for i, line in enumerate(lines) if line.startswith("ATOM"))
    topology.write_text("\n".join(lines[:first] + lines[first + 1:]) + "\n", encoding="utf-8")
    said = write_scene(copy, "x", {"frame": 1})
    assert said["reason"] == ("The scene could not be made: The frames' coordinates are not "
                              "of their topology's atoms.")


def test_a_study_without_dssp_has_no_records(helical, monkeypatch):
    monkeypatch.setattr("fastmdxplora.gui.by_residue.secondary_structure",
                        lambda root, of: {"available": False})
    text = build_scene(helical, {})["files"]["structure.pdb"]
    assert not text.startswith(("HELIX", "SHEET"))


# --------------------------------------------------------------------------
# In the GUI, and in Mol*
# --------------------------------------------------------------------------

def test_the_server_writes_and_serves_scenes(helical, tmp_path):
    from fastmdxplora.gui.server import start_dashboard_session

    session = start_dashboard_session(output=str(helical), host="127.0.0.1", port=0)
    base = session.url.rstrip("/")
    try:
        request = urllib.request.Request(
            base + "/api/scenes", data=json.dumps({
                "name": "from the gui", "view": {"camera": CAMERA, "frame": 3},
                "ligands": ["XYZ"], "selections": False}).encode(),
            headers={"Content-Type": "application/json", "Origin": base}, method="POST")
        said = json.loads(urllib.request.urlopen(request, timeout=120).read())
        listed = json.loads(urllib.request.urlopen(base + "/api/scenes", timeout=30).read())
        index = json.loads(urllib.request.urlopen(
            base + "/scenes/from%20the%20gui/index.mvsj", timeout=30).read())
        pdb = urllib.request.urlopen(base + "/scenes/from%20the%20gui/structure.pdb",
                                     timeout=30)
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/scenes/nothing/index.mvsj", timeout=30)
        archive = urllib.request.urlopen(
            base + "/artifacts/scenes/from%20the%20gui.mvsx?download=1", timeout=30).read()
    finally:
        session.server.shutdown()
    assert said["ok"] and said["frame"] == 3
    assert "from the gui" in [s["name"] for s in listed["scenes"]]
    assert index["root"]["custom"]["fastmdxplora"]["frame"] == 3
    assert pdb.headers["Content-Type"] == "chemical/x-pdb" and archive[:2] == b"PK"
    empty = start_dashboard_session(output=str(tmp_path / "nothing"), host="127.0.0.1", port=0)
    try:
        request = urllib.request.Request(
            empty.url.rstrip("/") + "/api/scenes", data=b'{"name": "x"}',
            headers={"Content-Type": "application/json", "Origin": empty.url.rstrip("/")},
            method="POST")
        refused = json.loads(urllib.request.urlopen(request, timeout=30).read())
    finally:
        empty.server.shutdown()
    assert refused == {"ok": False, "reason": "No study is open to save it in."}


def test_a_scene_written_from_the_viewer_opens_in_mol_star_as_it_was(helical):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from fastmdxplora.gui.by_residue import secondary_structure
    from fastmdxplora.gui.server import start_dashboard_session

    state = "window.FastMDXMoleculeViewer.STATE"
    session = start_dashboard_session(output=str(helical), host="127.0.0.1", port=0)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900},
                                    accept_downloads=True)
            page.set_default_timeout(90000)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(session.url + "#viewer", wait_until="domcontentloaded")
            if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl')"):
                pytest.skip("this browser has no WebGL, so the viewer cannot render")
            page.wait_for_function(f"() => {state}.model")
            page.evaluate("async () => window.FastMDXMoleculeViewer.loadPlayback("
                          "await (await fetch('/api/frames-info')).json())")
            page.wait_for_function(f"() => {state}.mode === 'playback' && {state}.engine"
                                   ".frameCount() === 6")
            page.evaluate("() => window.dispatchEvent(new CustomEvent("
                          "'dashboard:trajectory-seek', {detail: {frame: 5}}))")
            page.wait_for_function(f"() => {state}.engine.frame() === 5")
            page.select_option("#viewer-color", "chain")
            page.wait_for_function(f"() => {state}.colorMode === 'chain'")
            before = page.evaluate(f"() => {state}.engine.cameraSnapshot()")
            hooks.tool(page, "side-saved")
            page.click("#viewer-scene-save")
            page.fill("#viewer-view-name", "late frame")
            with page.expect_download() as caught:
                page.click("#viewer-view-keep")
            downloaded = Path(caught.value.path()).read_bytes()
            page.wait_for_function("() => document.getElementById('sr-live').textContent"
                                   ".startsWith('Wrote the scene late frame')")
            # Mol*, given the scene, as any viewer built on it would be.
            loaded = page.evaluate(f"""async () => {{
                const e = {state}.engine;
                const mvs = e.lib.extensions.mvs;
                e.secondary = null;
                const url = new URL('/scenes/late%20frame/index.mvsj', location.href).href;
                const text = await (await fetch(url)).text();
                await mvs.loadMVS(e.plugin, mvs.MVSData.fromMVSJ(text),
                                  {{sourceUrl: url, sanityChecks: true, replaceExisting: true}});
                const h = e.plugin.managers.structure.hierarchy.current;
                const structure = h.structures[h.structures.length - 1];
                const camera = e.plugin.canvas3d.camera.getSnapshot();
                return {{
                    atoms: structure.cell.obj.data.elementCount,
                    themes: structure.components.map((c) => c.representations.map(
                        (r) => r.cell.transform.params.colorTheme.name)),
                    camera: {{position: Array.from(camera.position),
                               target: Array.from(camera.target)}},
                    ss: e.secondaryStructureShown(),
                }};
            }}""")
            browser.close()
    finally:
        session.server.shutdown()
    assert errors == []
    archive = zipfile.ZipFile(io.BytesIO(downloaded))
    written = json.loads(archive.read("index.mvsj"))
    assert written["root"]["custom"]["fastmdxplora"]["frame"] == 5
    frames = md.load_topology(str(helical / "simulation" / "frames_topology.pdb"))
    assert loaded["atoms"] == frames.n_atoms
    assert loaded["themes"][0] == ["chain-id"]
    assert loaded["camera"]["target"] == pytest.approx(before["target"], abs=1e-3)
    assert loaded["camera"]["position"] == pytest.approx(before["position"], abs=1e-3)
    # The cartoon is the study's DSSP of frame 5, read from the scene's records.
    said = secondary_structure(helical, "frames")
    by_chain: dict[str, str] = {}
    for row, code in zip(said["residues"], said["frames"][5]):
        by_chain[row[0]] = by_chain.get(row[0], "") + ("H" if code == "H" else
                                                        "E" if code == "E" else "C")
    assert loaded["ss"] == by_chain
