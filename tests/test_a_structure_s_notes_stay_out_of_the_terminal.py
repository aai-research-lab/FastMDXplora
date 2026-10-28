"""MDTraj's notes on reading a structure stay out of the terminal.

`fastmdx gui` printed MDTraj's `UserWarning: Unlikely unit cell vectors
detected in PDB file likely resulting from a dummy CRYST1 record` under
"Press Ctrl+C to stop." for a structure whose CRYST1 is the 1 A placeholder
(NMR entries such as 1L2Y carry it): MDTraj discards that cell and moves no
atom. Three routes (a residue clicked, the ruler over every frame, the
contacts placed on the frames) read the topology with no guard, the others
were quiet only because their stderr went nowhere. The GUI now ignores
MDTraj's two notes on reading a structure, by their message and for
MDTraj's own modules, from its start and for good
(`keep_notes_on_reading_quiet`), and its loads go through
`topology_quietly` and `pdb_quietly`, which add the filters where they are
missing. Set and put back around each load instead, the filters were put
back by one server thread while another was reading, and that thread's note
was printed.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

md = pytest.importorskip("mdtraj")

from fastmdxplora.utils.native_output import pdb_quietly, topology_quietly  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CELL_NOTE = "Unlikely unit cell vectors"
RESIDUE_NOTE = "two consecutive residues with same number"
PLACEHOLDER = "CRYST1    1.000    1.000    1.000  90.00  90.00  90.00 P 1           1"


def _atom(serial: int, name: str, resname: str, resseq: int, x: float) -> str:
    return (f"ATOM  {serial:5d} {(' ' + name).ljust(4)} {resname:>3} A{resseq:4d}    "
            f"{x:8.3f}{0.0:8.3f}{0.0:8.3f}{1.0:6.2f}{0.0:6.2f}          "
            f"{name[0]:>2}")


def _pdb(path: Path, *, cell: bool = True, alike: bool = False) -> Path:
    """Two residues of two atoms, with the placeholder cell and, ``alike``,
    both numbered 1."""
    lines = [PLACEHOLDER] if cell else []
    lines += [_atom(1, "N", "ALA", 1, 0.0), _atom(2, "CA", "ALA", 1, 1.5),
              _atom(3, "N", "GLY", 1 if alike else 2, 3.0),
              _atom(4, "CA", "GLY", 1 if alike else 2, 4.5), "END"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _notes(caught: list[warnings.WarningMessage]) -> list[str]:
    return [str(one.message) for one in caught
            if CELL_NOTE in str(one.message) or RESIDUE_NOTE in str(one.message)]


def test_the_placeholder_note_is_real(tmp_path):
    """MDTraj itself says both notes on these files: the tests below would
    pass for nothing if it did not."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        md.load_topology(str(_pdb(tmp_path / "cell.pdb")))
        md.load_topology(str(_pdb(tmp_path / "alike.pdb", cell=False, alike=True)))
    said = _notes(caught)
    assert any(CELL_NOTE in one for one in said), said
    assert any(RESIDUE_NOTE in one for one in said), said


@pytest.mark.parametrize("frames", [False, True])
def test_a_structure_is_read_without_its_notes(tmp_path, frames):
    path = _pdb(tmp_path / "both.pdb", alike=True)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        read = pdb_quietly(path) if frames else topology_quietly(path)
    assert _notes(caught) == []
    topology = read.topology if frames else read
    assert topology.n_atoms == 4
    assert [r.name for r in topology.residues] == ["ALA", "GLY"]
    if frames:
        # The cell is still discarded as MDTraj discards it.
        assert read.unitcell_lengths is None
        assert read.xyz.shape == (1, 4, 3)


def _said_by_mdtraj(*messages: str):
    """A load that warns as MDTraj's own reader does, from its module."""
    space = {"__name__": "mdtraj.formats.pdb.pdbfile", "warnings": warnings}
    exec("def load(path):\n"  # noqa: S102 - a module of MDTraj's name, for its warnings
         "    for message in MESSAGES:\n"
         "        warnings.warn(message, UserWarning, stacklevel=1)\n"
         "    return 'read'\n", space)
    space["MESSAGES"] = messages
    return space["load"]


def test_any_other_warning_still_shows(tmp_path, monkeypatch):
    monkeypatch.setattr(md, "load_topology", _said_by_mdtraj(
        "Unlikely unit cell vectors detected in PDB file likely resulting "
        "from a dummy CRYST1 record. Discarding unit cell vectors.",
        "something else about this file"))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert topology_quietly(tmp_path / "x.pdb") == "read"
        # The same words from anywhere but MDTraj are not its note.
        warnings.warn("Unlikely unit cell vectors detected in PDB file likely resulting "
                      "from a dummy CRYST1 record", UserWarning, stacklevel=2)
    assert [str(one.message) for one in caught] == [
        "something else about this file",
        "Unlikely unit cell vectors detected in PDB file likely resulting "
        "from a dummy CRYST1 record"]


def test_an_error_still_raises(tmp_path):
    with pytest.raises(OSError):
        topology_quietly(tmp_path / "missing.pdb")


def _stderr(path: Path, code: str) -> str:
    """What a process of its own prints, warnings shown as Python shows them."""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        [str(ROOT / "src"), str(ROOT), os.environ.get("PYTHONPATH", "")])}
    done = subprocess.run([sys.executable, "-c", code, str(path)],
                          capture_output=True, text=True, env=env, timeout=300, check=True)
    return done.stderr


def test_the_terminal_shows_nothing(tmp_path):
    """MDTraj alone prints both notes to stderr, the helpers neither."""
    path = _pdb(tmp_path / "both.pdb", alike=True)
    alone = _stderr(path, "import sys, mdtraj; mdtraj.load_topology(sys.argv[1])")
    assert CELL_NOTE in alone and RESIDUE_NOTE in alone, alone
    quietly = _stderr(path, "import sys\n"
                      "from fastmdxplora.utils.native_output import pdb_quietly, topology_quietly\n"
                      "topology_quietly(sys.argv[1]); pdb_quietly(sys.argv[1])")
    assert CELL_NOTE not in quietly and RESIDUE_NOTE not in quietly, quietly


# Two server threads reading at once: A starts, B starts, A ends while B
# is still reading, then B ends; then a scene's structure (scenes.py) read
# beside a load the same way; then a warning MDTraj does not give.
_TWO_AT_ONCE = """
import sys, threading, time, warnings
import mdtraj as md
from fastmdxplora import scenes
from fastmdxplora.utils.native_output import topology_quietly
real = md.load_topology
waits = iter([0.4, 0.8, 0.4, 0.8])
md.load_topology = lambda path, **kw: (time.sleep(next(waits)), real(path, **kw))[1]
lines = [line for line in open(sys.argv[1]).read().splitlines() if line.startswith("ATOM")]
for first, then in ((topology_quietly, (sys.argv[1],)), (scenes._topology, (lines,))):
    a = threading.Thread(target=first, args=then); a.start(); time.sleep(0.1)
    b = threading.Thread(target=topology_quietly, args=(sys.argv[1],)); b.start()
    a.join(); b.join()
warnings.warn("A warning a person needs", UserWarning, stacklevel=1)
"""


def test_two_structures_read_at_once_say_no_note(tmp_path):
    said = _stderr(_pdb(tmp_path / "both.pdb", alike=True), _TWO_AT_ONCE)
    assert CELL_NOTE not in said and RESIDUE_NOTE not in said, said
    assert "A warning a person needs" in said, said


def test_another_warning_shows_once_as_python_shows_it(tmp_path):
    """Read three times, a structure's other warning is shown once, as
    MDTraj alone shows it: the filters are not set again on each read."""
    path = _pdb(tmp_path / "charged.pdb")
    text = path.read_text(encoding="utf-8").splitlines()
    # The first atom's charge cannot be read.
    text[1] = text[1][:78] + "X?"
    path.write_text("\n".join(text) + "\n", encoding="utf-8")
    alone = _stderr(path, "import sys, mdtraj\n"
                    "for _ in range(3): mdtraj.load_topology(sys.argv[1])")
    quietly = _stderr(path, "import sys\n"
                      "from fastmdxplora.utils.native_output import topology_quietly\n"
                      "for _ in range(3): topology_quietly(sys.argv[1])")
    said = "UserWarning: Could not parse charge information"
    assert alone.count(said) == 1, alone
    assert quietly.count(said) == 1, quietly


def test_the_gui_keeps_the_notes_quiet_from_its_start(tmp_path):
    """MDTraj read straight, as the frames are (`md.load_dcd(...,
    top=frames_topology.pdb)`), after the GUI has started."""
    said = _stderr(_pdb(tmp_path / "both.pdb", alike=True), "\n".join([
        "import sys, threading, mdtraj",
        "from fastmdxplora.gui.server import start_dashboard_session",
        f"session = start_dashboard_session(output={str(tmp_path / 'study')!r},"
        " host='127.0.0.1', port=0)",
        "reading = threading.Thread(target=mdtraj.load_topology, args=(sys.argv[1],))",
        "reading.start(); reading.join(); session.server.shutdown()"]))
    assert CELL_NOTE not in said and RESIDUE_NOTE not in said, said


@pytest.fixture
def study(tmp_path):
    """A study whose analyses read a structure with both notes: its saved
    topology, read held to the study (`series.topology_held`), as a study
    with no analysis manifest reads it."""
    root = tmp_path / "study"
    (root / "simulation").mkdir(parents=True)
    where = _pdb(root / "simulation" / "trajectory_topology.pdb", alike=True)
    return root, where


def test_a_residue_clicked_says_no_note(study):
    from fastmdxplora.gui.selection import selection_for

    root, _ = study
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        answer = selection_for(root, chain="A", resseq=1, resname="GLY", atom="CA")
    assert answer["ok"] is True, answer
    assert _notes(caught) == []


def test_the_ruler_over_every_frame_says_no_note(study):
    from fastmdxplora.gui.measure import over_frames

    root, _ = study
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        answer = over_frames(root, "resname ALA and name CA", "resname GLY and name CA")
    # Both atoms were found in the structure; the study has no trajectory.
    assert answer == {"ok": False,
                      "reason": "This study has no trajectory to compute it over."}
    assert _notes(caught) == []


def test_the_contacts_placed_on_the_frames_say_no_note(study):
    from fastmdxplora.gui.interactions_over_frames import _placed

    root, where = study
    (root / "simulation" / "frames_index.json").write_text(json.dumps(
        {"available": True, "source_topology": str(where), "shown": "not water"}),
        encoding="utf-8")
    pair = {"protein_atom": 1, "protein_atom_name": "CA",
            "ligand_atom": 3, "ligand_atom_name": "CA"}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert _placed(root, [pair]) == [[1, 3]]
    assert _notes(caught) == []


def test_a_live_frame_and_a_viewer_selection_say_no_note(tmp_path):
    from fastmdxplora.gui.live_frames import _bonded_topology
    from fastmdxplora.gui.viewer_selections import _topology

    text = _pdb(tmp_path / "both.pdb", alike=True).read_text(encoding="utf-8")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert _bonded_topology(text).n_atoms == 4
        assert _topology(text.encode(), ("notes", 0, len(text))).n_atoms == 4
    assert _notes(caught) == []


def test_a_viewer_selection_lets_another_warning_show(tmp_path, monkeypatch):
    """It ignored every warning; now only MDTraj's two notes."""
    from fastmdxplora.gui.viewer_selections import _topology

    monkeypatch.setattr(md, "load_topology", _said_by_mdtraj("something else about this file"))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert _topology(b"END\n", ("another", 0, 4)) == "read"
    assert [str(one.message) for one in caught] == ["something else about this file"]


_READERS = {"load", "load_topology", "load_pdb", "load_frame"}


def _straight_reads(path: Path) -> list[str]:
    """Where MDTraj reads a structure in ``path`` other than through the
    helpers, however it is named."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {"mdtraj"}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {one.asname or one.name for one in node.names if one.name == "mdtraj"}
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("mdtraj"):
            found += [f"{path.name}:{node.lineno}" for one in node.names
                      if one.name in _READERS or one.name == "PDBTrajectoryFile"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "PDBTrajectoryFile":
            found.append(f"{path.name}:{node.lineno}")
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and isinstance(node.func.value, ast.Name) and node.func.value.id in names
              and node.func.attr in _READERS):
            found.append(f"{path.name}:{node.lineno}")
    return found


def test_every_gui_structure_load_goes_through_the_helpers(tmp_path):
    sources = sorted((ROOT / "src" / "fastmdxplora" / "gui").rglob("*.py"))
    found = [one for path in [*sources, ROOT / "src" / "fastmdxplora" / "scenes.py"]
             for one in _straight_reads(path)]
    assert found == []
    # The forms it looks for, each found.
    odd = tmp_path / "odd.py"
    odd.write_text("import mdtraj as mt\nfrom mdtraj import load_pdb\n"
                   "mt.load('a.pdb')\nmt.load_frame('a.pdb', 0)\n"
                   "mt.formats.PDBTrajectoryFile('a.pdb')\n", encoding="utf-8")
    assert sorted(_straight_reads(odd)) == ["odd.py:2", "odd.py:3", "odd.py:4", "odd.py:5"]


def _charged(path: Path) -> Path:
    """A structure whose first atom's charge cannot be read."""
    path = _pdb(path)
    text = path.read_text(encoding="utf-8").splitlines()
    text[1] = text[1][:78] + "X?"
    path.write_text("\n".join(text) + "\n", encoding="utf-8")
    return path


def test_the_gui_s_reads_show_another_warning(tmp_path):
    """Read inside the redirect of C's output, a structure's other warning
    went to /dev/null, and Python, counting it as said, never said it."""
    path = _charged(tmp_path / "charged.pdb")
    # Each in a process of its own: Python says one warning once.
    for read in ("from fastmdxplora.gui.live_frames import _bonded_topology\n"
                 "_bonded_topology(open(sys.argv[1]).read())",
                 "from fastmdxplora.gui.viewer_selections import _topology\n"
                 "text = open(sys.argv[1]).read()\n"
                 "_topology(text.encode(), ('charged', 0, len(text)))"):
        said = _stderr(path, "import sys\n" + read)
        assert "Could not parse charge information" in said, (read, said)


# Two threads in the redirect at once: A in, B in, A out, B out.
_REDIRECTED_AT_ONCE = """
import os, threading, time
from fastmdxplora.utils.native_output import suppress_native_output
def quiet(wait):
    with suppress_native_output():
        os.write(2, b"inside\\n")
        time.sleep(wait)
a = threading.Thread(target=quiet, args=(0.4,)); a.start(); time.sleep(0.1)
b = threading.Thread(target=quiet, args=(0.8,)); b.start()
a.join(); b.join()
os.write(2, b"the terminal again\\n")
"""


def test_two_redirects_at_once_leave_the_terminal_as_it_was(tmp_path):
    """The second saved the first's /dev/null as the terminal and put it
    back last: nothing written after reached the terminal again."""
    said = _stderr(tmp_path, _REDIRECTED_AT_ONCE)
    assert said == "the terminal again\n", said


def test_a_scene_s_structure_lets_another_warning_show(monkeypatch):
    """Read with every warning ignored, as the viewer's selections were."""
    from fastmdxplora import scenes

    monkeypatch.setattr(md, "load_topology", _said_by_mdtraj("something else about this file"))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert scenes._topology(["END"]) == "read"
    assert [str(one.message) for one in caught] == ["something else about this file"]


def test_a_filter_kept_as_text_is_read_as_one(tmp_path):
    """A filter put in the list with its message as text (as Python's own
    '__main__' one is) stopped the helpers, and the GUI with them."""
    with warnings.catch_warnings():
        warnings.filters.insert(0, ("ignore", "some exact message", UserWarning, "somemodule", 0))
        assert topology_quietly(_pdb(tmp_path / "both.pdb", alike=True)).n_atoms == 4
