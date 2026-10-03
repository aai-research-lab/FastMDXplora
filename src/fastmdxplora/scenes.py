"""A view of a study as a scene file, in MolViewSpec.

A view in the Viewer (its camera, the frame shown, how the molecule is
represented and coloured, the parts shown, the selections named) lived in
the page and in the study's ``viewer_views.json``, which nothing else reads.
A scene is the same view written in MolViewSpec (MVS), the Mol\\* team's
format for what a molecular viewer shows: a tree of what to load and how to
show it, which any viewer built on Mol\\* opens (molstar.org, the PDB's
pages, this GUI). A scene is written as an MVSX archive: ``index.mvsj`` and
the files it names, so it opens on another machine:

- ``structure.pdb``: the atoms shown, the frame's coordinates where a frame
  is shown (superposed where the view is), with the study's DSSP as HELIX
  and SHEET records, so the cartoon is the study's and not Mol\\*'s own;
- ``colours.json``: each residue's colour, where the view colours by one of
  the study's results (RMSF and the like), on the Viewer's scale.

Atoms are named by their place in ``structure.pdb`` (MVS's ``atom_index``),
which is the Viewer's numbering, so a selection in a scene is the Viewer's
selection. What MVS cannot say (the publication look's shading, the
Viewer's own choices) is kept in the nodes' ``custom`` fields, which Mol\\*
reads and other readers leave alone; what a scene leaves out is said in
its notes.
"""

from __future__ import annotations

import io
import json
import math
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["MVS_VERSION", "SCENES_DIR", "build_scene", "read_scene", "scene_bytes",
           "scenes_of", "write_scene"]

SCENES_DIR = "scenes"
MVS_VERSION = "1.8"
MOST_LABELS = 300

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,59}$")
_DARK = "#050505"
_RESULT_STOPS = ((44, 123, 182), (247, 247, 247), (215, 25, 28))
_NO_VALUE = "#5c5c66"
_CAPS = ("ACE", "NME", "NHE", "NH2", "FOR", "NMA")
# The Viewer's names for colourings, as Mol*'s colour themes.
_THEMES = {"chain": "chain-id", "spectrum": "sequence-id", "residue": "residue-name",
           "element": "element-symbol", "secondary_structure": "secondary-structure"}
_ELEMENT = {"molstar_color_theme_name": "element-symbol",
            "molstar_color_theme_params": {"carbonColor": {"name": "element-symbol",
                                                           "params": {}}}}


def _node(kind: str, params: dict[str, Any] | None = None, children: list | None = None,
          custom: dict[str, Any] | None = None) -> dict[str, Any]:
    node: dict[str, Any] = {"kind": kind}
    if params is not None:
        node["params"] = params
    if custom:
        node["custom"] = custom
    if children:
        node["children"] = children
    return node


def _hex(rgb: tuple[int, int, int] | list[int]) -> str:
    return "#" + "".join(f"{int(c):02x}" for c in rgb)


# --------------------------------------------------------------------------
# The atoms shown
# --------------------------------------------------------------------------

def _atom_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith(("ATOM", "HETATM"))]


def _frame_lines(root: Path, view: dict[str, Any], ligands: list[str],
                 notes: list[str]) -> tuple[list[str], int] | None:
    """The frames' atoms at the frame the view shows, superposed where it
    is; None where the study has no frames to show it at."""
    from fastmdxplora.gui.trajectory_frames import (
        FRAMES_FILE,
        FRAMES_TOPOLOGY,
        frames_info,
        superposed_frames,
    )

    frame = view.get("frame")
    if not isinstance(frame, int):
        return None
    info = frames_info(root)
    if not info.get("available") or not 0 <= frame < int(info.get("n_frames_browser") or 0):
        notes.append(f"The study has no frame {frame} to show; the structure is shown.")
        return None
    simulation = root / "simulation"
    coordinates = simulation / FRAMES_FILE
    on = view.get("superposed") or "none"
    if on in ("backbone", "pocket"):
        said = superposed_frames(root, on, ligand=ligands[0] if ligands else None,
                                 cutoff_angstrom=view.get("pocket_cutoff") or 5.0)
        if said.get("ok"):
            coordinates = simulation / said["file"]
        else:
            notes.append(f"The frames are not superposed: {said.get('reason')}")
    import mdtraj as md

    from fastmdxplora.utils.native_output import suppress_native_output

    lines = _atom_lines((simulation / FRAMES_TOPOLOGY).read_text(encoding="utf-8",
                                                                 errors="replace"))
    with suppress_native_output(), md.formats.DCDTrajectoryFile(str(coordinates)) as handle:
        handle.seek(frame)
        xyz = handle.read(1)[0][0]                    # angstroms, as the DCD holds them
    if len(xyz) != len(lines):
        from fastmdxplora.analysis.loading import TrajectoryLoadError

        raise TrajectoryLoadError("The frames' coordinates are not of their topology's atoms.")
    return [f"{line[:30]}{x:8.3f}{y:8.3f}{z:8.3f}{line[54:]}"
            for line, (x, y, z) in zip(lines, xyz)], frame


def _structure_lines(root: Path, view: dict[str, Any]) -> list[str] | None:
    from fastmdxplora.gui.viewed_structure import viewer_structure

    shown = view.get("shown") or {}
    data, _ = viewer_structure(root, "structure",
                               with_solvent=bool(shown.get("water") or shown.get("ions")))
    return _atom_lines(data.decode("utf-8", errors="replace")) if data else None


def _xyz(lines: list[str]) -> Any:
    import numpy as np

    return np.array([(float(line[30:38]), float(line[38:46]), float(line[46:54]))
                     for line in lines], dtype=float)


# --------------------------------------------------------------------------
# The residues, the cartoon and what is coloured
# --------------------------------------------------------------------------

def _polymer(name: str) -> bool:
    from fastmdxplora.ligand_detection import POLYMER_RESNAMES

    return name.upper() in POLYMER_RESNAMES or name.upper() in _CAPS


def _secondary_records(root: Path, of: str, frame: int, runs: list[Any]) -> list[str]:
    """HELIX and SHEET records of the study's DSSP for the residues shown."""
    from fastmdxplora.gui.by_residue import secondary_structure

    said = secondary_structure(root, of)
    if not said.get("available"):
        return []
    codes_of_frame = said["frames"][frame if len(said["frames"]) > 1 else 0]
    code = {tuple(row[:5]): codes_of_frame[i] for i, row in enumerate(said["residues"])}
    records: list[str] = []
    run_start = None
    helices = strands = 0

    def close(end: int) -> None:
        nonlocal run_start, helices, strands
        if run_start is None:
            return
        first, last = runs[run_start], runs[end]
        kind = code.get(tuple(first[:5]))
        if kind == "H":
            # The columns of the PDB format's HELIX record.
            helices += 1
            records.append(
                f"HELIX  {helices % 1000:>3} {helices % 1000:>3} "
                f"{first[3]:>3} {first[0]:1} {first[1]:>4}{first[2]:1} "
                f"{last[3]:>3} {last[0]:1} {last[1]:>4}{last[2]:1}{1:>2}"
                f"{'':30} {end - run_start + 1:>5}")
        else:
            # And of its SHEET record, one strand to a sheet.
            strands += 1
            records.append(
                f"SHEET  {1:>3} {strands % 1000:>3}{1:>2} "
                f"{first[3]:>3} {first[0]:1}{first[1]:>4}{first[2]:1} "
                f"{last[3]:>3} {last[0]:1}{last[1]:>4}{last[2]:1}{0:>2}")
        run_start = None

    for i, run in enumerate(runs):
        mine = code.get(tuple(run[:5]))
        if run_start is not None:
            begun = runs[run_start]
            if mine != code.get(tuple(begun[:5])) or run[0] != begun[0]:
                close(i - 1)
        if run_start is None and mine in ("H", "E"):
            run_start = i
    close(len(runs) - 1)
    return records


def _residue_colours(root: Path, colour: str, runs: list[Any], notes: list[str]
                     ) -> list[dict[str, Any]] | None:
    """Each residue's colour on the Viewer's scale for one of the study's
    results: blue at the low end, white in the middle, red at the high end;
    grey without a value, or where two residues cannot be told apart."""
    from fastmdxplora.gui.by_residue import values_by_residue

    key = colour.split(":", 1)[1]
    found = next((p for p in values_by_residue(root)["properties"] if p.get("key") == key), None)
    if found is None:
        notes.append(f"The study has no result {key!r} to colour by; coloured by chain.")
        return None
    chained = any(row[0] is not None for row in found["values"])
    values = {f"{row[0] if chained else ''}|{row[1]}|{row[2] or ''}": row[3]
              for row in found["values"]}
    low = float(found["low"])
    span = float(found["high"]) - low
    absent = found.get("absent")

    def colour_of(value: Any) -> str:
        if value is None or not math.isfinite(float(value)):
            return _NO_VALUE
        t = max(0.0, min(1.0, (float(value) - low) / span)) if span > 0 else 0.0
        if found.get("reverse"):
            t = 1 - t
        scaled = t * (len(_RESULT_STOPS) - 1)
        lower = min(len(_RESULT_STOPS) - 2, int(math.floor(scaled)))
        within = scaled - lower
        return _hex([round(a + within * (b - a))
                     for a, b in zip(_RESULT_STOPS[lower], _RESULT_STOPS[lower + 1])])

    seen: dict[str, int] = {}
    for run in runs:
        name = f"{run[0] if chained else ''}|{run[1]}|{run[2]}"
        seen[name] = seen.get(name, 0) + 1
    rows = []
    for run in runs:
        if not _polymer(run[3]):
            continue
        name = f"{run[0] if chained else ''}|{run[1]}|{run[2]}"
        value = None if seen[name] > 1 else values.get(name, absent)
        rows.append({"auth_asym_id": run[0], "auth_seq_id": run[1],
                     "pdbx_PDB_ins_code": run[2] or None, "color": colour_of(value)})
    notes.append(f"Coloured by {found.get('label', key)}: blue {found['low']} to red "
                 f"{found['high']}{' ' + found['unit'] if found.get('unit') else ''}.")
    return rows


def _colour_nodes(colour: str, residue_colours: bool, layers: list[tuple[list[int], str]]
                  ) -> list[dict[str, Any]]:
    """The colours of one of the molecule's representations: the view's
    colouring, then each selection's colour over its atoms."""
    if residue_colours:
        first = _node("color_from_uri", {"uri": "colours.json", "format": "json",
                                         "schema": "auth_residue", "field_name": "color"})
    elif colour == "monochrome":
        first = _node("color", {"color": "#ffffff"})
    elif colour == "element":
        first = _node("color", {}, custom=_ELEMENT)
    else:
        first = _node("color", {}, custom={"molstar_color_theme_name":
                                           _THEMES.get(colour, "chain-id")})
    return [first] + [_node("color", {"selector": [{"atom_index": i} for i in atoms],
                                      "color": hex_colour}) for atoms, hex_colour in layers]


# --------------------------------------------------------------------------
# The scene
# --------------------------------------------------------------------------

def _camera(camera: dict[str, Any]) -> dict[str, Any]:
    """MVS's camera: its position is the Viewer's moved towards the target
    so that its distance is the one at which Mol*'s field of view would
    frame the same sphere (MVS's "field of view normalized" position)."""
    # A saved view's camera, checked: three numbers each.
    target = [float(v) for v in camera["target"]]
    position = [float(v) for v in camera["position"]]
    up = [float(v) for v in camera["up"]]
    fov = float(camera.get("fov") or math.pi / 4)
    scale = 2 * (math.tan(fov / 2) if camera.get("mode") == "orthographic"
                 else math.sin(fov / 2))
    position = [t + (p - t) * scale for p, t in zip(position, target)]
    return {"target": target, "position": position, "up": up}


def _representations(view: dict[str, Any], hydrogens: bool) -> list[dict[str, Any]]:
    """The protein's representations, as the Viewer's engine makes them."""
    name = view.get("representation") or "cartoon"
    ignore = not hydrogens
    if name == "surface":
        return [_node("representation", {"type": "cartoon"}, custom={"opacity": 0.18}),
                _node("representation", {"type": "surface", "surface_type": "molecular"},
                      custom={"opacity": 0.78, "grey": True})]
    if name in ("sticks", "ballAndStick"):
        return [_node("representation", {"type": "ball_and_stick", "ignore_hydrogens": ignore,
                                         "size_factor": 0.18 if name == "sticks" else 0.3})]
    if name == "lines":
        return [_node("representation", {"type": "line", "ignore_hydrogens": ignore})]
    if name == "spacefill":
        return [_node("representation", {"type": "spacefill", "ignore_hydrogens": ignore})]
    if name == "backbone":
        return [_node("representation", {"type": "backbone"})]
    return [_node("representation", {"type": "cartoon"})]


def _with_colours(representation: dict[str, Any], colours: list[dict[str, Any]]
                  ) -> dict[str, Any]:
    """A representation given its colours, and an opacity where it has one."""
    custom = representation.pop("custom", None) or {}
    children = ([_node("color", {"color": "#d8d8dd"})] if custom.get("grey")
                else [dict(c) for c in colours])
    if "opacity" in custom:
        children.append(_node("opacity", {"opacity": custom["opacity"]}))
    representation["children"] = children
    return representation


def _selection_shape(name: str, hydrogens: bool) -> dict[str, Any] | None:
    ignore = not hydrogens
    shapes = {"sticks": {"type": "ball_and_stick", "ignore_hydrogens": ignore, "size_factor": 0.3},
              "spheres": {"type": "spacefill", "ignore_hydrogens": ignore},
              "lines": {"type": "line", "ignore_hydrogens": ignore},
              "surface": {"type": "surface", "surface_type": "molecular"},
              "cartoon": {"type": "cartoon"}}
    return dict(shapes[name]) if name in shapes else None


def _selected_atoms(selection: dict[str, Any], runs: list[Any], topology: Any,
                    notes: list[str]) -> list[int]:
    if selection.get("kind") == "residues":
        wanted = {(r[0], r[1], r[2], r[3]) for r in selection.get("residues") or []}
        return [i for run in runs if tuple(run[:4]) in wanted for i in run[5]]
    try:
        return [int(i) for i in topology.select(selection.get("expression", ""))]
    except Exception as exc:  # noqa: BLE001 - said in the notes
        notes.append(f"Selection {selection.get('name')!r} could not be read: {exc}")
        return []


def _topology(lines: list[str]) -> Any:
    import warnings

    import mdtraj as md

    from fastmdxplora.utils.native_output import suppress_native_output

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "shown.pdb"
        path.write_text("\n".join(lines) + "\nEND\n", encoding="utf-8")
        with suppress_native_output(), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return md.load_topology(str(path))


def _pocket(xyz: Any, runs: list[Any], ligands: set[str], cutoff: float,
            lines: list[str]) -> list[Any]:
    """The polymer residues with a heavy atom within ``cutoff`` angstroms of
    a heavy atom of the ligand, as the Viewer finds them."""
    import numpy as np

    def heavy(i: int) -> bool:
        element = lines[i][76:78].strip().upper() or lines[i][12:16].strip()[:1].upper()
        return element != "H"

    ligand = [i for run in runs if run[3] in ligands for i in run[5] if heavy(i)]
    if not ligand:
        return []
    near = []
    centre = xyz[ligand]
    for run in runs:
        if not _polymer(run[3]):
            continue
        atoms = [i for i in run[5] if heavy(i)]
        if not atoms:
            continue
        gaps = np.linalg.norm(xyz[atoms][:, None, :] - centre[None, :, :], axis=2)
        if float(gaps.min()) <= cutoff:
            near.append(run)
    return near


def _residue_expression(run: Any) -> dict[str, Any]:
    expression: dict[str, Any] = {"auth_asym_id": run[0], "auth_seq_id": run[1]}
    if run[2]:
        expression["pdbx_PDB_ins_code"] = run[2]
    return expression


_ANY_CAMERA = {"position": [0, 0, 1], "target": [0, 0, 0], "up": [0, 1, 0]}


def _view(view: Any) -> dict[str, Any]:
    """The parts of a view the Viewer can set, each checked as a saved view
    is; a view may have no camera, and the scene then frames the molecule."""
    from fastmdxplora.gui.saved_views import _checked

    raw = dict(view) if isinstance(view, dict) else {}
    has_camera = _checked({"camera": raw.get("camera")}) is not None
    checked = _checked({**raw, "camera": raw["camera"] if has_camera else _ANY_CAMERA}) or {}
    if not has_camera:
        checked.pop("camera", None)
    return checked


def build_scene(root: str | Path, view: dict[str, Any] | None = None,
                selections: list[dict[str, Any]] | None = None, *,
                ligands: list[str] | None = None, title: str | None = None) -> dict[str, Any]:
    """The scene of a view of the study at ``root``: ``{"ok": True,
    "state": <MVS state>, "files": {name: text}, "notes": [...]}``, or why
    there is none.

    ``view`` is a view as the Viewer saves one (its camera, frame,
    representation, colouring, parts shown, superposition, pocket cutoff,
    look and ground); ``selections`` the selections named, as they are kept;
    ``ligands`` the ligand's residue names, found in the structure where not
    given."""
    from fastmdxplora.gui.by_residue import residue_runs

    base = Path(root)
    view = _view(view)
    notes: list[str] = []
    shown = {"protein": True, "ligand": True, "pocket": True, "water": False, "ions": False,
             "hydrogens": False, **(view.get("shown") or {})}
    lines = _structure_lines(base, view)
    if lines is None:
        return {"ok": False, "reason": "The study has no structure to show."}
    runs = residue_runs(lines)
    if ligands is None:
        from fastmdxplora.ligand_detection import detect_ligands

        ligands = list(detect_ligands((run[0], run[3], str(run[1])) for run in runs)["resnames"])
    framed = _frame_lines(base, view, ligands, notes)
    of, frame = "structure", 0
    if framed is not None:
        lines, frame = framed
        runs = residue_runs(lines)
        of = "frames"
        if shown.get("water"):
            notes.append("The frames hold no water; the scene shows the frame without it.")
    xyz = _xyz(lines)
    topology = _topology(lines)
    colour = view.get("colour") or "spectrum"
    rows = _residue_colours(base, colour, runs, notes) if colour.startswith("result:") else None
    if colour.startswith("result:") and rows is None:
        colour = "chain"

    named = [s for s in (selections or []) if isinstance(s, dict)]
    resolved = [(s, _selected_atoms(s, runs, topology, notes)) for s in named]
    hidden = {i for s, atoms in resolved if s.get("shown") is False for i in atoms}
    layers = [(atoms, s["colour"]) for s, atoms in resolved
              if atoms and s.get("shown") is not False and s.get("colour")]
    colours = _colour_nodes(colour, rows is not None, layers)
    hydrogens = bool(shown.get("hydrogens"))
    present = {run[3] for run in runs}
    ligand_names = {name for name in ligands if name in present}

    def component(selector: Any, representations: list[dict[str, Any]],
                  label: str | None = None) -> dict[str, Any]:
        children = list(representations)
        if label:
            children.append(_node("label", {"text": label}))
        return _node("component", {"selector": selector}, children)

    def residues_selector(chosen: list[Any]) -> list[dict[str, Any]]:
        return [_residue_expression(run) for run in chosen]

    def element_sticks(size: float, uniform: str | None) -> dict[str, Any]:
        params = {"type": "ball_and_stick", "ignore_hydrogens": not hydrogens,
                  "size_factor": size}
        colour_node = _node("color", {"color": uniform}) if uniform else \
            _node("color", {}, custom=_ELEMENT)
        return _node("representation", params, [colour_node])

    monochrome = colour == "monochrome"
    components: list[dict[str, Any]] = []
    polymer_runs = [run for run in runs if _polymer(run[3])]
    if shown.get("protein") and polymer_runs:
        # Hidden residues are left out of the molecule's own representations.
        kept = [run for run in polymer_runs if not set(run[5]) <= hidden]
        if len(kept) < len(polymer_runs):
            selector: Any = residues_selector(kept)
        else:
            selector = "polymer" if not (present & set(_CAPS)) else residues_selector(kept)
        if kept:
            components.append(component(selector, [_with_colours(r, colours) for r in
                                                   _representations(view, hydrogens)]))
        partly = [run for run in polymer_runs
                  if set(run[5]) & hidden and not set(run[5]) <= hidden]
        if partly:
            notes.append(f"{len(partly)} residues are hidden only in part in the Viewer; "
                         "the scene shows them whole.")
        alphas = sum(1 for run in polymer_runs
                     for i in run[5] if lines[i][12:16].strip() == "CA")
        if (view.get("representation") or "cartoon") == "cartoon" and 0 < alphas < 8:
            components.append(component(selector, [element_sticks(0.22, None)]))
    if shown.get("pocket") and ligand_names:
        pocket = _pocket(xyz, runs, ligand_names, float(view.get("pocket_cutoff") or 5.0), lines)
        if pocket:
            components.append(component(residues_selector(pocket), [
                element_sticks(0.16, "#a78bfa" if monochrome else None)]))
    if shown.get("ligand") and ligand_names:
        components.append(component([{"auth_comp_id": name} for name in sorted(ligand_names)],
                                    [element_sticks(0.35, "#63e6ff" if monochrome else None)]))
    if shown.get("water") and of == "structure":
        components.append(component("water", [_node("representation", {
            "type": "ball_and_stick", "ignore_hydrogens": not hydrogens, "size_factor": 0.2},
            [_node("color", {"color": "#4da3ff"})])]))
    if shown.get("ions"):
        components.append(component("ion", [_node("representation", {
            "type": "spacefill", "size_factor": 0.55}, [_node("color", {}, custom=_ELEMENT)])]))
    labels = 0
    for selection, atoms in resolved:
        if not atoms or selection.get("shown") is False:
            continue
        shape = _selection_shape(selection.get("representation") or "none", hydrogens)
        selector = [{"atom_index": i} for i in atoms]
        if shape:
            colour_node = (_node("color", {"color": selection["colour"]})
                           if selection.get("colour") else _node("color", {}, custom=_ELEMENT))
            components.append(component(selector, [_node("representation", shape,
                                                         [colour_node])]))
        if selection.get("labelled"):
            atom_set = set(atoms)
            for run in runs:
                if not atom_set & set(run[5]):
                    continue
                if labels >= MOST_LABELS:
                    notes.append(f"Only the first {MOST_LABELS} residue labels are in the "
                                 "scene.")
                    break
                labels += 1
                components.append(component([_residue_expression(run)], [],
                                            label=f"{run[3]} {run[1]}{run[2]}"))
    if shown.get("box"):
        notes.append("The periodic box is not part of a scene.")

    records = _secondary_records(base, of, frame, runs)
    structure = _node("structure", {"type": "model"}, components)
    tree = [_node("download", {"url": "structure.pdb"}, [
        _node("parse", {"format": "pdb"}, [structure])])]
    if view.get("camera"):
        tree.append(_node("camera", _camera(view["camera"])))
    else:
        tree.append(_node("focus", {}))
    publication = bool(view.get("publication"))
    ground = "white" if view.get("ground") == "white" or publication else _DARK
    tree.append(_node("canvas", {"background_color": ground},
                      custom={"molstar_postprocessing": {"enable_outline": True,
                                                         "enable_ssao": True}}
                      if publication else None))
    from fastmdxplora import __version__

    state = {
        "kind": "single",
        "root": _node("root", None, tree, custom={"fastmdxplora": {
            "version": __version__, "view": view, "of": of, "frame": frame if of == "frames"
            else None, "selections": named, "ligands": sorted(ligand_names)}}),
        "metadata": {"version": MVS_VERSION,
                     "timestamp": datetime.now(timezone.utc).isoformat(),
                     "title": title or f"A view of {base.name}",
                     "description": " ".join(notes) or None},
    }
    if state["metadata"]["description"] is None:
        del state["metadata"]["description"]
    files = {"structure.pdb": "\n".join(records + lines + ["END", ""])}
    if rows is not None:
        files["colours.json"] = json.dumps(rows)
    return {"ok": True, "state": state, "files": files, "notes": notes, "of": of,
            "frame": frame if of == "frames" else None}


def scene_bytes(scene: dict[str, Any]) -> bytes:
    """The scene as an MVSX archive: ``index.mvsj`` and the files it names."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.mvsj", json.dumps(scene["state"], indent=1))
        for name, text in scene["files"].items():
            archive.writestr(name, text)
    return out.getvalue()


def write_scene(root: str | Path, name: Any, view: dict[str, Any] | None = None, *,
                selections: list[dict[str, Any]] | None = None,
                ligands: list[str] | None = None) -> dict[str, Any]:
    """The scene of a view written with the study, as
    ``scenes/<name>.mvsx``, in place of one of that name."""
    name = str(name or "").strip()
    if not _NAME.match(name):
        return {"ok": False, "reason": "A scene is named in 1 to 60 letters, digits, spaces, "
                                       "dots, dashes and underscores, from a letter or digit."}
    try:
        scene = build_scene(root, view, selections, ligands=ligands, title=name)
    except Exception as exc:  # noqa: BLE001 - said, not raised
        return {"ok": False, "reason": f"The scene could not be made: {exc}"}
    if not scene.get("ok"):
        return scene
    folder = Path(root) / SCENES_DIR
    folder.mkdir(exist_ok=True)
    target = folder / f"{name}.mvsx"
    temporary = folder / f".{name}.mvsx.tmp"
    temporary.write_bytes(scene_bytes(scene))
    temporary.replace(target)
    return {"ok": True, "name": name, "path": str(target), "notes": scene["notes"],
            "of": scene["of"], "frame": scene["frame"]}


def scenes_of(root: str | Path) -> dict[str, Any]:
    """The scenes written with the study, newest first."""
    folder = Path(root) / SCENES_DIR
    found = sorted(folder.glob("*.mvsx"), key=lambda p: p.stat().st_mtime, reverse=True) \
        if folder.is_dir() else []
    return {"ok": True, "scenes": [{"name": p.stem, "bytes": p.stat().st_size}
                                   for p in found if _NAME.match(p.stem)]}


def read_scene(root: str | Path, name: Any, member: str = "index.mvsj") -> bytes | None:
    """One file of a scene written with the study, or None."""
    name = str(name or "")
    if not _NAME.match(name):
        return None
    path = Path(root) / SCENES_DIR / f"{name}.mvsx"
    try:
        with zipfile.ZipFile(path) as archive:
            if member not in archive.namelist():
                return None
            return archive.read(member)
    except (OSError, zipfile.BadZipFile):
        return None
