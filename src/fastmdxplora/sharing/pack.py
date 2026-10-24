"""Pack a finished study as one zip another FastMDXplora opens: `--share`.

What goes in, by default, is what the study's pages read once it has
finished: the Config and the Manifest, the structures setup made, the
trajectory and the topology it is read with, the run's records, every
analysis's numbers and figures, the report, and what the person kept from
the Viewer. `--share-all` adds every other file the study keeps. Never in
it: the Agent's conversations, the record of the process that ran it, tags
and notes, the Viewer's scratch, what `--rerun` set aside, deposits and the
project bundle.

A study of several runs is packed whole, as its folder lays it out: its
Config and its record of runs, each run in `runs/<id>/`, and what it made
of them together (the table of members, the comparison, a free energy). A
prepared system a run simulated from (`setup_from`) is carried with what a
run needs to find it and start from it; one kept outside the study is
carried once, in `prepared/<name>/`, and its path said as that folder.

A study's records name its files by the path they were written at. Each
text file is packed with the study's folder said as `__FASTMDX_STUDY__`,
any other home folder's path as `__FASTMDX_HOME__` and the computer's name
as `__FASTMDX_HOST__`, and read again: an archive that still holds any of
them is not written. The same study packed with the same choices on the
same day is the same bytes.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import socket
import stat
import tempfile
import zipfile
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from fastmdxplora.sharing import HOME, HOST, STUDY, ShareRefused
from fastmdxplora.sharing import crate as packing_list

__all__ = ["DEFAULT_LICENSE", "TEXT_SUFFIXES", "chosen_files", "finished_manifest",
           "never_shared", "share_study"]

DEFAULT_LICENSE = "CC-BY-4.0"
#: Scrubbed text larger than this is written to a file until it is packed.
_HELD_IN_MEMORY = 16 * 1024 * 1024
SHARED_FROM = "shared_from.json"

#: Files whose text is read for paths and names, and written with them said
#: as placeholders. Anything else is packed as it is: the trajectory,
#: figures as pictures and NumPy arrays record no path.
TEXT_SUFFIXES = frozenset({
    ".json", ".yml", ".yaml", ".md", ".markdown", ".csv", ".tsv", ".log", ".txt", ".out",
    ".err", ".pdb", ".cif", ".mmcif", ".sdf", ".mol", ".mol2", ".dat", ".xvg", ".dx",
    ".xml", ".html", ".htm", ".svg", ".sha256", ".toml", ".ini", ".cfg"})

#: Never shared: the record of the process that ran the study (its
#: computer, boot and process number), the person's tags and notes, a copy
#: of the rest, where an archive opened here came from, the Zenodo record it
#: was shared as, and where on this computer a study fetched back from
#: another machine was sent from (`fetched.json`).
_NEVER_NAMES = frozenset({"project_bundle.zip", "study_tags.json", SHARED_FROM,
                          "shared_to.json", "fetched.json", packing_list.METADATA,
                          packing_list.PREVIEW})
#: The Agent's conversations with the person.
_NEVER_FOLDERS = ("agent/",)
#: Where a prepared system kept outside the study is carried.
PREPARED = "prepared"
#: Packed byte for byte, never read for paths: what a run finds its
#: prepared system by (`system.xml`'s SHA-256), and the states beside it.
_AS_THEY_ARE = frozenset({"system.xml", "state.xml", "integrator.xml"})
#: A record's field naming the computer it ran on.
_HOST_FIELD = re.compile(r'("(?:host|hostname)"\s*:\s*)"(?!__FASTMDX_HOST__")(?:[^"\\]|\\.)+"')
#: A record's path to its prepared system, relative to the run.
_RELATIVE_TO_RUN = re.compile(r'("relative_to_run"\s*:\s*)"(?:[^"\\]|\\.)*"')
#: What a run needs of a prepared system it simulated from: its record and
#: the system it finds it by (`system.xml`'s SHA-256), the state it starts
#: from and its topology. Carried whatever the package.
_PREPARED_NEEDS = frozenset({"system.xml", "state.xml", "topology.pdb",
                             "setup_parameters.json"})
_NEVER_PHASES = frozenset({"scratch", "previous", "deposit"})

#: Left out unless everything is asked for: what only a run needs (the
#: solvated system, the force field as OpenMM built it, the states), what
#: setup made on the way, the report's downloads and the logs.
_ONLY_ALL_NAMES = frozenset({
    "system.xml", "state.xml", "integrator.xml", "solvated.pdb", "state_final.xml",
    "state_minimized.xml", "complex_for_pka.pdb", "retained.pdb", "report.pdf",
    "slides.pptx", "dashboard.html", "simulation.log", "fastmdxplora.log",
    "checkpoint.chk.json", "checkpoint.chk.sha256"})
_ONLY_ALL_SUFFIXES = frozenset({".chk", ".mp4", ".webm"})
_MEDOID = re.compile(r"_medoid_\d+\.pdb$")

#: A path in any home folder, on any system: `/home/<name>`, `/Users/<name>`,
#: `C:\Users\<name>` (and as JSON writes it, `C:\\Users\\<name>`). A name
#: with a space in it is taken whole where a path goes on after it.
_NAME_ON = r"(?:[^/\\\n\"'`<>,;()|]{1,64}?(?=[/\\])|[^/\\\s\"'`,;)\]}<>|]+)"
_HOMES = re.compile(r"(?<![\w.-])(?:/(?:home|Users)/|[A-Za-z]:(?:\\\\|\\)Users(?:\\\\|\\))"
                    + _NAME_ON)
#: Another person's home folder as a shell names it (`~<name>/prep`).
_TILDE_HOMES = re.compile(r"(?<![\w.-])~(?=[A-Za-z_])[\w.-]+(?=[/\\])")
#: The same reached by a relative path (`../../home/<name>`), as a record of
#: where a prepared system was relative to its run says it.
_RELATIVE_HOMES = re.compile(r"(?<![\w.-])(?:\.\.(?:/|\\\\|\\))+(?:home|Users)(?:/|\\\\|\\)"
                             + _NAME_ON)
#: Where a path ends: not inside a longer name (`/data/run` is not
#: `/data/run2`, nor `/data/run.bak`); a full stop ending a sentence ends it.
_STARTS = r"(?<![\w.-])"
_ENDS = r"(?![\w-]|\.[\w])"
#: Names computers are given by default, which are words of a sentence too.
_COMMON_HOSTS = frozenset({"localhost", "ubuntu", "debian", "fedora", "centos", "server",
                           "linux", "node", "master", "main", "raspberrypi", "workstation",
                           "desktop", "laptop", "computer", "host"})


def _unread_folder(path: Path) -> tuple[Path, OSError] | None:
    """Of the folders ``path`` is in, below the nearest one that can be
    entered, the outermost, and why: the one to make readable first; none
    where the folder ``path`` is in can be. One that can be entered and not
    listed (another person's, at 711) keeps no file in it from being read."""
    found = None
    for folder in path.parents:
        try:
            os.stat(os.path.join(folder, "."))
        except OSError as exc:
            found = (folder, exc)
        else:
            break
    return found


def _record_read(path: Path, what: str, root: Path) -> bool:
    """Whether ``path``, a record of the study (``what``), is there; one
    there that cannot be read is refused, naming the outermost folder of the
    study on its way that cannot be read, or the record."""
    try:
        with path.open("rb"):
            return True
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return False
    except OSError as exc:
        where, error = _unread_folder(path) or (path, exc)
        raise ShareRefused(f"{where}{'' if where != path else f', {what},'} could not be read "
                           f"({error.strerror or error}); nothing was written. Make it "
                           "readable and share it again.",
                           code="environment.share.not_finished",
                           path=str(where)) from exc


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def finished_manifest(root: Path) -> dict[str, Any]:
    """The study's Manifest, once the study has finished; refused where it
    is running, stopped short or holds no study. For a study of several
    runs, its record of runs read as one Manifest: `kind` "batch", its
    `runs`, their systems and every run's phases, each with its `run`."""
    if _record_read(root / "batch_manifest.json", "the record of its runs", root):
        return _finished_runs(root)
    _record_read(root / "manifest.json", "its Manifest", root)
    manifest = _load(root / "manifest.json")
    if not isinstance(manifest, dict) or not manifest.get("phases"):
        raise ShareRefused(f"{root} holds no FastMDXplora study (no manifest.json).",
                           code="environment.share.not_a_study")
    try:
        from fastmdxplora.gui.telemetry import status_as_it_stands

        status = str(status_as_it_stands(root).get("status") or "").lower()
    except Exception:  # noqa: BLE001 - the Manifest still says how each phase ended
        status = ""
    if status in ("running", "starting", "paused"):
        raise ShareRefused(f"The study in {root} is still {status}; share it once it has "
                           "finished.", code="environment.share.not_finished")
    short = [str(p.get("name")) for p in manifest["phases"]
             if isinstance(p, dict) and str(p.get("status")) not in ("ok", "skipped")]
    if short or status in ("stopped", "failed", "interrupted"):
        raise ShareRefused(
            f"The study in {root} did not finish ({', '.join(short) or status}); share it "
            "once it has, after `fastmdx resume` where it stopped.",
            code="environment.share.not_finished")
    if not (root / "resolved_config.yml").is_file():
        raise ShareRefused(f"The study in {root} has no resolved_config.yml, the Config it "
                           "ran, which a shared study is opened by.",
                           code="environment.share.not_finished")
    return manifest


def _finished_runs(root: Path) -> dict[str, Any]:
    """A study of several runs, once each of its runs has finished: refused
    where one is running, stopped short or never ran."""
    batch = _load(root / "batch_manifest.json")
    batch = batch if isinstance(batch, dict) else {}
    planned = batch.get("planned") or batch.get("runs")
    planned = planned if isinstance(planned, list) else []
    ids = list(dict.fromkeys(str(entry.get("run_id")) for entry in planned or []
                             if isinstance(entry, dict) and entry.get("run_id")))
    if not ids:
        raise ShareRefused(f"{root} holds a record of runs (batch_manifest.json) that names "
                           "none.", code="environment.share.not_a_study")
    try:
        from fastmdxplora.gui.workspace import study_state

        state = study_state(root)
    except Exception:  # noqa: BLE001 - each run's Manifest still says how it ended
        state = ""
    if state == "running":
        try:
            from fastmdxplora.gui.exploration import runs_of_a_study

            going = [str(r.get("run_id") or r.get("id") or "") for r in runs_of_a_study(root)
                     or [] if r.get("state") == "running"]
        except Exception:  # noqa: BLE001 - said without the names
            going = []
        going = [g for g in going if g]
        which = (f"The run{'s' if len(going) > 1 else ''} {', '.join(going)} of the study in "
                 f"{root} {'are' if len(going) > 1 else 'is'} still going" if going
                 else f"Runs of the study in {root} are still going")
        raise ShareRefused(f"{which}; share the study once every run has finished.",
                           code="environment.share.not_finished")
    results = {str(r.get("run_id")): r for r in (batch.get("runs") if isinstance(
        batch.get("runs"), list) else []) if isinstance(r, dict)}
    named = {str(e.get("run_id")): _system_named(e.get("system"))
             for e in planned if isinstance(e, dict)}
    phases: list[dict[str, Any]] = []
    systems: list[str] = []
    for run_id in ids:
        folder = root / "runs" / run_id
        if (PurePosixPath(run_id).name != run_id or run_id.startswith(".")
                or any(mark in run_id for mark in "\\:")):
            raise ShareRefused(f"{root}'s record of runs names {run_id!r}, which is no run's "
                               "folder.", code="environment.share.not_a_study")
        if not _record_read(folder / "manifest.json", f"the Manifest of the run {run_id}", root):
            raise ShareRefused(
                f"The run {run_id} of the study in {root} never ran; run it with `fastmdx "
                f"resume {root}`, then share the study.", code="environment.share.not_finished")
        if str((results.get(run_id) or {}).get("status") or "ok") in ("error", "failed"):
            raise ShareRefused(
                f"The run {run_id} of the study in {root} ended with an error; share the study "
                f"once every run has finished, after `fastmdx resume {root}`.",
                code="environment.share.not_finished")
        try:
            member = finished_manifest(folder)
        except ShareRefused as exc:
            if exc.refusal.code != "environment.share.not_finished":
                raise
            found = _load(folder / "manifest.json")
            short = [str(p.get("name")) for p in (found.get("phases") or [])
                     if isinstance(p, dict) and str(p.get("status")) not in ("ok", "skipped")]
            raise ShareRefused(
                f"The run {run_id} of the study in {root} did not finish"
                + (f" ({', '.join(short)})" if short else "")
                + f"; share the study once every run has, after `fastmdx resume {root}`.",
                code="environment.share.not_finished") from exc
        phases += [{**phase, "run": run_id} for phase in member.get("phases") or []
                   if isinstance(phase, dict)]
        named[run_id] = named.get(run_id) or _system_named(member.get("system"))
        if named[run_id]:
            systems.append(named[run_id])
    if not (root / "resolved_config.yml").is_file():
        raise ShareRefused(f"The study in {root} has no resolved_config.yml, the Config of "
                           "all its runs, which a shared study is opened by.",
                           code="environment.share.not_finished")
    return {"kind": "batch", "version": str(batch.get("version") or ""),
            "system": ", ".join(dict.fromkeys(systems)),
            "output_dir": str(batch.get("output_dir") or ""), "runs": ids, "phases": phases,
            "systems_of_runs": named}


def _system_named(system: Any) -> str:
    """A run's system as a reader knows it: a PDB entry in capitals, a
    structure file by its name, not the path it was read from."""
    from fastmdxplora.system_id import PDB_ID

    from fastmdxplora.system_id import _SUFFIX

    text = str(system or "").strip()
    if PDB_ID.match(text):
        return text.upper()
    name = PurePosixPath(text.replace("\\", "/")).name if text else ""
    return _SUFFIX.sub("", name) or name


#: The placeholders as a Markdown file is written with them (:func:`_markdown_plain`).
_ESCAPED = {token: "\\_\\_" + token[2:-2] + "\\_\\_" for token in (HOME, HOST)}
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_MAY_PRECEDE_CODE = re.compile(r" {0,3}(#{1,6}(\s|$)|(=+|-+)[ \t]*$)")
_CODE_SPAN = re.compile(r"(`+)(?!`).*?(?<!`)\1(?!`)")


def _markdown_plain(text: str) -> str:
    """``text``, a Markdown file, with the placeholders left in it
    (`__FASTMDX_HOME__`, `__FASTMDX_HOST__`) written with their underscores
    escaped outside code, so that they show as written rather than as a
    word in bold. `__FASTMDX_STUDY__` is put back when the study is opened."""
    if HOME not in text and HOST not in text:
        return text

    def plain(piece: str) -> str:
        for token in (HOME, HOST):
            piece = piece.replace(token, _ESCAPED[token])
        return piece

    out, fence, blank, indented = [], "", True, False
    for line in text.splitlines(keepends=True):
        opened = _FENCE.match(line)
        if not fence and line.strip() and (line.startswith(("    ", "\t"))
                                           and (blank or indented)):
            # An indented block of code, after a blank line.
            indented = True
            out.append(line)
            continue
        if line.strip():
            indented = False
        # What an indented block of code may follow: a blank line, a heading
        # (`# Host`, or the `---` under one), the end of a fenced block.
        blank = not line.strip() or bool(_MAY_PRECEDE_CODE.match(line))
        if fence:
            if opened and opened.group(1)[0] == fence[0] and len(opened.group(1)) >= len(fence):
                fence, blank = "", True
            out.append(line)
            continue
        if opened:
            fence = opened.group(1)
            out.append(line)
            continue
        kept, at = [], 0
        for span in _CODE_SPAN.finditer(line):
            kept += [plain(line[at:span.start()]), span.group(0)]
            at = span.end()
        out.append("".join(kept) + plain(line[at:]))
    return "".join(out)


def _walk(root: Path, kept: Callable[[str], bool] | None = None,
          links: list[str] | None = None) -> list[str]:
    """The files in ``root``, as paths in it, in name order. A folder that
    cannot be read whole is refused where ``kept`` (by a path of a file in
    it) says its files would be shared: left out, the study would be opened
    missing them. Links, to a file or a folder, are never among them; each
    is put in ``links``."""
    found = []

    def unread(folder: str, exc: OSError) -> None:
        rel = Path(folder).relative_to(root).as_posix() if folder != str(root) else ""
        if kept is None or kept(f"{rel}/_" if rel else "_"):
            raise ShareRefused(f"{folder} could not be read ({exc.strerror or exc}); nothing "
                               "was written. Make it readable and share it again.",
                               code="environment.share.not_finished",
                               path=rel or str(root)) from exc

    def failed(exc: OSError) -> None:
        unread(str(exc.filename or root), exc)

    for folder, folders, files in os.walk(root, onerror=failed):
        folders[:] = [name for name in folders if name != "__pycache__"]
        for name in [*folders, *files]:
            path = Path(folder) / name
            try:
                mode = path.lstat().st_mode
            except OSError as exc:
                unread(folder, exc)
                continue
            rel = path.relative_to(root).as_posix()
            if stat.S_ISLNK(mode):
                if links is not None:
                    links.append(rel)
            elif stat.S_ISREG(mode) and path.suffix != ".tmp":
                found.append(rel)
    return sorted(found, key=lambda rel: PurePosixPath(rel).parts)


def _opens_with(root: Path, trajectory: str, roots: list[str]) -> str:
    """The topology a trajectory is read with, as a path in the study."""
    folder = PurePosixPath(trajectory).parent
    if folder.name == "joined":
        named = str(_load(root / folder / "joined.json").get("topology") or "")
        for old in roots:
            if named.startswith(old.rstrip("/") + "/"):
                inner = named[len(old.rstrip("/")) + 1:]
                if (root / inner).is_file():
                    return inner
    for name in ("trajectory_topology.pdb", "topology.pdb"):
        for place in (folder, PurePosixPath("simulation")):
            candidate = (place / name).as_posix()
            if (root / candidate).is_file():
                return candidate
    return ""


def chosen_files(root: Path, everything: bool = False,
                 roots: list[str] | None = None,
                 needed: frozenset[str] = frozenset(),
                 runs: frozenset[str] | None = None,
                 links: list[str] | None = None) -> list[dict[str, Any]]:
    """The study's files that go in, in name order, each with its phase,
    kind, label, and for a trajectory the topology it is read with;
    ``needed`` go in whatever the package (a prepared system a run
    simulated from); of a study of several, only the ``runs`` its record of
    runs names (not a folder of an earlier attempt left beside them)."""
    from fastmdxplora.deposit import _trajectory_parts
    from fastmdxplora.study_files import kind_of, label_of, place

    def kept(rel: str) -> bool:
        run = place(rel)[1]
        return not never_shared(rel) and (runs is None or not run or run in runs)

    found_links: list[str] = []
    walked = _walk(root, kept, found_links)
    # A link is judged as what it stands for: a folder by what would be
    # shared of it, a file as the file there would be.
    folder_links = [rel for rel in found_links if os.path.isdir(root / rel)]
    # One leading nowhere could stand for either: only where both would be
    # shared does it keep the study back.
    file_links = {rel for rel in found_links
                  if rel not in folder_links and not rel.endswith(".tmp")
                  and (os.path.exists(root / rel) or kept(f"{rel}/_"))}
    entries = []
    for rel in sorted([*walked, *file_links], key=lambda rel: PurePosixPath(rel).parts):
        if never_shared(rel):
            continue
        phase, run, inner = place(rel)
        if runs is not None and run and run not in runs:
            continue
        kind = kind_of(rel)
        entries.append({"path": rel, "phase": phase, "run": run, "kind": kind,
                        "label": label_of(inner), "opens_with": "",
                        "link": rel in file_links})
    for entry in entries:
        if entry["kind"] == "trajectory" and entry["phase"] == "simulation":
            entry["opens_with"] = _opens_with(root, entry["path"], roots or [str(root)])
    parts = _trajectory_parts(entries)
    have = {e["path"] for e in entries}
    out = []
    for entry in entries:
        path = PurePosixPath(entry["path"])
        if not everything and entry["path"] not in needed:
            if path.name in _ONLY_ALL_NAMES or path.suffix.lower() in _ONLY_ALL_SUFFIXES \
                    or _MEDOID.search(path.name) or parts.get(entry["path"]) == "segments":
                continue
            # A figure is shown from its picture; its vector twin is a download.
            if path.suffix.lower() == ".svg" and path.with_suffix(".png").as_posix() in have:
                continue
        out.append(entry)
    if links is not None:
        links += sorted([e["path"] for e in out if e["link"]]
                        + [rel for rel in folder_links if kept(f"{rel}/_")],
                        key=lambda rel: PurePosixPath(rel).parts)
    out = [e for e in out if not e["link"]]
    for entry in out:
        del entry["link"]
    return out


def never_shared(rel: str) -> bool:
    """Whether a path of a study is never in a shared one: its Agent
    conversations, the record of its process, tags and notes, a file or
    folder whose name begins with a dot, the Viewer's scratch, what
    `--rerun` set aside, deposits; a run's of a study of several alike."""
    from fastmdxplora.study_files import place

    path = PurePosixPath(rel)
    phase, _, inner = place(rel)
    if path.name in _NEVER_NAMES or rel.startswith(_NEVER_FOLDERS) \
            or inner.startswith(_NEVER_FOLDERS) \
            or any(part.startswith(".") for part in path.parts):
        return True
    return phase in _NEVER_PHASES


def _hosts(root: Path, manifest: dict[str, Any],
           elsewhere: list[Path] | None = None) -> list[str]:
    """The names of the computers the study ran on, as its records give them:
    its own, each of its runs', and those of ``elsewhere`` (the studies its
    prepared systems were made in)."""
    names = {socket.gethostname()}
    folders = [root, *(root / "runs" / run for run in manifest.get("runs") or []),
               root / "seed_pull", *(elsewhere or [])]
    phases = list(manifest.get("phases") or [])
    for folder in [root / "seed_pull", *(elsewhere or [])]:
        found = _load(folder / "manifest.json")
        phases += (found.get("phases") or []) if isinstance(found, dict) else []
    for phase in phases:
        produced = phase.get("produced_by") if isinstance(phase, dict) else None
        if isinstance(produced, dict) and produced.get("host"):
            names.add(str(produced["host"]))
    for folder in folders:
        for record in (folder / ".fastmdxplora_run.json",
                       folder / "simulation" / "live_status.json"):
            found = _load(record)
            if isinstance(found, dict):
                for key in ("host", "hostname"):
                    if found.get(key):
                        names.add(str(found[key]))
    # A study opened from a share names its computer as the placeholder.
    short = {n.split(".")[0] for n in names if not n.startswith("__FASTMDX_")}
    return sorted({n for n in short if len(n) >= 3 and n.lower() not in _COMMON_HOSTS},
                  key=len, reverse=True)


def _absolute(path: str) -> bool:
    """Whether a path is absolute on the computer that wrote it, Windows
    included: a study run on one and shared from another names both."""
    from pathlib import PurePosixPath as _Posix, PureWindowsPath

    return _Posix(path).is_absolute() or PureWindowsPath(path).is_absolute()


def _spellings(path: str) -> set[str]:
    """A path as a text file may hold it: as written, as JSON escapes its
    backslashes, and with its separators turned."""
    return {path, json.dumps(path)[1:-1], path.replace("\\", "/")}


class _Scrubber:
    """Says the study's folder, home folders and the computer's name as
    placeholders, and finds any left: one set of patterns for both, so what
    is not replaced is not then refused."""

    def __init__(self, roots: list[str], hosts: list[str],
                 carried: dict[str, tuple[str, bool]] | None = None) -> None:
        # Each folder the study's files are said by: the study's own, and
        # each prepared system carried in it, as the folder it is carried in
        # (``carried``: by path, where, and whether only that path itself
        # is said so, not what is in it). Longest first, so a folder inside
        # another is said as its own.
        said = {spelling.rstrip("/\\"): (STUDY, False) for root in roots if _absolute(root)
                for spelling in _spellings(root) if len(spelling.strip("/\\")) > 1}
        for folder, (inside, alone) in (carried or {}).items():
            for spelling in _spellings(folder):
                if ((_absolute(folder) or folder.startswith(("~/", "inputs/")))
                        and len(spelling.strip("/\\")) > 1):
                    said.setdefault(spelling.rstrip("/\\"), (f"{STUDY}/{inside}", alone))
        # A relative name (`inputs/<name>`) only where it starts a path.
        self.roots = [(re.compile((r"(?<![\w./\\-])" if r.startswith("inputs/") else _STARTS)
                                  + re.escape(r)
                                  + (r"(?=/?(?![\w/\\-]|\.[\w]))" if said[r][1] else _ENDS)),
                       said[r][0])
                      for r in sorted(said, key=len, reverse=True)]
        # The home folder as named and where a link to it leads.
        homes = {str(Path.home()).rstrip("/\\")}
        try:
            homes.add(str(Path.home().resolve()).rstrip("/\\"))
        except (OSError, RuntimeError):
            pass
        self.homes = [re.compile(_STARTS + re.escape(h) + _ENDS)
                      for h in sorted({s for home in homes for s in _spellings(home)},
                                      key=len, reverse=True)
                      if len(h.strip("/\\")) > 1]
        self.hosts = [re.compile(r"(?<![\w.-])" + re.escape(h) + r"(?:\.[\w-]+)*(?![\w-])")
                      for h in hosts]

    def __call__(self, text: str) -> tuple[str, set[str]]:
        used: set[str] = set()
        for pattern, replacement in self.roots:
            text, n = pattern.subn(replacement.replace("\\", "\\\\"), text)
            if n:
                used.add(STUDY)
        for pattern in [*self.homes, _HOMES, _RELATIVE_HOMES, _TILDE_HOMES]:
            text, n = pattern.subn(HOME, text)
            if n:
                used.add(HOME)
        for pattern in self.hosts:
            text, n = pattern.subn(HOST, text)
            if n:
                used.add(HOST)
        # A record's own field for the computer, whatever its name: one too
        # short to be told from a word (`vm`) is not replaced elsewhere.
        text, n = _HOST_FIELD.subn(lambda m: m.group(1) + json.dumps(HOST), text)
        if n:
            used.add(HOST)
        return text, used

    def left(self, text: str, *, hosts: bool = True) -> str:
        """What is still there that should not be, or ''; with ``hosts``
        false, a computer's name is not looked for."""
        for pattern in [*(p for p, _ in self.roots), *self.homes, _HOMES, _RELATIVE_HOMES,
                        _TILDE_HOMES,
                        *(self.hosts if hosts else [])]:
            found = pattern.search(text)
            if found:
                return found.group(0)
        return ""


def _title_and_summary(root: Path, manifest: dict[str, Any], scrub: _Scrubber
                       ) -> tuple[str, str]:
    demo = _load(root / "demo.json")
    system = str(manifest.get("system") or root.name)
    runs = len(manifest.get("runs") or [])
    title = str(demo.get("study") or "") if isinstance(demo, dict) else ""
    summary = ""
    try:
        report = (root / "report" / "report.md").read_text(encoding="utf-8")
    except OSError:
        report = ""
    lines = report.splitlines()
    if not title:
        title = next((line[2:].strip() for line in lines if line.startswith("# ")), "")
    if "## Summary" in lines:
        body = []
        for line in lines[lines.index("## Summary") + 1:]:
            if line.startswith("#"):
                break
            if line.strip():
                body.append(line.strip())
            elif body:
                break
        summary = " ".join(body)
    version = str(manifest.get("version") or "")
    of = (f"{system}, in one run" if runs == 1 else f"{system}, in {runs} runs" if runs
          else system)
    summary = summary or (f"A molecular dynamics study of {of}, prepared, simulated, "
                          f"analysed and reported by FastMDXplora {version}.").replace("  ", " ")
    return (scrub(title or f"A FastMDXplora study of {of}")[0], scrub(summary)[0])


def _frames_and_atoms(path: Path) -> tuple[int | None, int | None]:
    try:
        from mdtraj.formats import DCDTrajectoryFile

        from fastmdxplora.utils.native_output import suppress_native_output

        with suppress_native_output(), DCDTrajectoryFile(str(path)) as handle:
            frames = len(handle)
            xyz, _, _ = handle.read(1)
        return frames, int(xyz.shape[1])
    except Exception:  # noqa: BLE001 - said where known, left out where not
        return None, None


def _interval_ps(root: Path, trajectory: str, run: str = "") -> float | None:
    from fastmdxplora.gui.trajectory_frames import _interval_ns

    try:
        ns = _interval_ns(root / "runs" / run if run else root,
                          {"trajectory": root / trajectory})
    except Exception:  # noqa: BLE001
        return None
    return round(ns * 1000.0, 9) if ns else None


def _actions(manifest: dict[str, Any], files: list[dict[str, Any]],
             prepared_of: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Each phase that ran, of the study or of each of its runs, with what it
    read and wrote; ``prepared_of`` is each run's prepared system's topology
    where it simulated from one not its own."""
    have = {f["path"] for f in files}
    out = []
    for phase in manifest.get("phases") or []:
        if not isinstance(phase, dict) or phase.get("status") != "ok":
            continue
        name = str(phase.get("name") or "")
        run = str(phase.get("run") or "")
        at = f"runs/{run}/" if run else ""
        mine = [f for f in files if f["phase"] == name and str(f.get("run") or "") == run]
        trajectories = [f for f in files if f["role"] == "trajectory"
                        and str(f.get("run") or "") == run]
        inputs = {
            "setup": [f"{at}setup/input.pdb"],
            "simulation": [(prepared_of or {}).get(run) or f"{at}setup/topology.pdb"],
            "analysis": [p for f in trajectories for p in (f["path"], f.get("read_with") or "")],
            "report": [f"{at}analysis/analysis_manifest.json"],
        }
        out.append({
            "id": f"{run}/{name}" if run else name, "name": name, "run": run,
            "start": str(phase.get("started_at") or ""),
            "end": str(phase.get("finished_at") or ""),
            "object": [p for p in [f"{at}resolved_config.yml", *inputs.get(name, [])]
                       if p in have],
            "result": [f["path"] for f in mine]})
    return out


def _relative_put(text: str, owner: str, target: str) -> str:
    """A record's `relative_to_run` said as the way from ``owner`` to
    ``target`` in the study, both paths in it, as it will be opened: the way
    it ran named a folder of that computer (`../../home/<name>/prep`)."""
    import posixpath

    way = posixpath.relpath(target or ".", owner or ".")
    return _RELATIVE_TO_RUN.sub(lambda m: m.group(1) + json.dumps(way), text)


def _within(path: Path, folder: Path) -> bool:
    try:
        path.relative_to(folder)
    except ValueError:
        return False
    return True


def _prepared_systems(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """As :func:`_looked_for`, with the simulation phase's own warning of a
    system not found kept quiet: here one not found is refused, and says so."""
    import logging

    quiet = logging.getLogger("fastmdx.simulation")
    level = quiet.level
    quiet.setLevel(logging.ERROR)
    try:
        return _looked_for(root, manifest)
    except OSError as exc:
        where, error = (_unread_folder(Path(exc.filename)) if exc.filename else None) \
            or (Path(exc.filename or root), exc)
        raise ShareRefused(
            f"{where}, which the study's runs simulated from, could not be read "
            f"({error.strerror or error}); nothing was written. Make it readable and share "
            "it again.", code="environment.share.not_finished", path=str(where)) from exc
    finally:
        quiet.setLevel(level)


def _unreadable_system(folders: list[Path | None]) -> tuple[Path, str] | None:
    """The first `system.xml` there in ``folders`` (or their `setup/`) that
    cannot be read, and why."""
    for folder in folders:
        for place in ([folder, folder / "setup"] if folder is not None else []):
            system = place / "system.xml"
            try:
                if system.is_file():
                    with system.open("rb"):
                        pass
            except OSError as exc:
                return system, str(exc.strerror or exc)
    return None


def _looked_for(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """The prepared systems the study's runs simulated from that are not
    their own `setup/`, found as the simulation phase finds them (by where
    the run's record says, relative to the run, then as named, each by its
    content), and for a window's seed the preparation it was taken from.

    Returns `carried`, those kept outside the study, each once, with the
    folder it is carried in (`inside`, `at`) and what its paths are said as
    (`said`); `needed`, the files of those inside the study a run needs
    (:data:`_PREPARED_NEEDS`); `topology_of`, by run, the topology it
    simulated, as a path in the study; and `relative`, by packed record, the
    folder its `relative_to_run` is read from and the folder it must lead to,
    as paths in the study. A run whose system is not found, or is another
    now, is refused: shared without it, it would be opened with none."""
    from fastmdxplora.refusals import CodedError
    from fastmdxplora.simulation import pipeline
    from fastmdxplora.simulation.seeding import SEEDED_FROM

    real = root.resolve()
    outside: dict[Path, dict[str, Any]] = {}
    needed: dict[str, list[str]] = {}
    was_inside: dict[str, tuple[str, bool]] = {}
    # Each record that names a prepared system by `relative_to_run`: a
    # run's ("run"), or a seed's (by the seed), and the system it names.
    users: list[tuple[str, str, Path, Path]] = []
    topology: dict[str, Path] = {}

    def of(run: str, said: str) -> str:
        who = f"The run {run} of the study in {root}" if run else f"The study in {root}"
        return f"{who} {said}"

    def note(system: Path, names: list[Any], run: str, itself: list[Any] = ()) -> None:
        """``system`` a run simulated from, ``names`` the folders its records
        name it by, and ``itself`` the paths they record it was at: where
        the study ran, which a study moved since no longer has, and where its
        own records say its files were (a preparation copied since). One in a
        folder of the study a shared study leaves out (one whose name begins
        with a dot, a run's the record of runs does not name) is carried as
        one outside it is."""
        itself = [*itself, *_made_in(system)]
        if _within(system, real) and not _left_out(system.relative_to(real).as_posix(),
                                                    manifest.get("runs")):
            inner = system.relative_to(real).as_posix()
            for n in sorted(_PREPARED_NEEDS):
                if (system / n).exists() or (system / n).is_symlink():
                    users_of = needed.setdefault(f"{inner}/{n}", [])
                    if run not in users_of:
                        users_of.append(run)
            # Where it was when the study ran, for a study moved since: its
            # folder as a folder, a study that held it as its name alone.
            gone = [os.path.normpath(str(path)) for path in itself
                    if path and Path(str(path)).is_absolute() and Path(str(path)) != system]
            for typed, rule in _typed_from_home(names, gone, inner).items():
                was_inside.setdefault(typed, rule)
            for was in gone:
                was_inside.setdefault(was, (inner, False))
                for named in names:
                    if _full(named) is None:
                        continue
                    named = os.path.normpath(str(named))
                    below = os.path.relpath(was, named)
                    if below == ".":
                        was_inside.setdefault(named, (inner, False))
                    elif (not below.startswith("..") and inner.endswith("/" + below)
                          and below in pipeline.PREPARED_SYSTEM_LAYOUTS):
                        was_inside.setdefault(named, (inner[:-len(below) - 1], True))
            return
        entry = outside.setdefault(system, {"system": system, "names": set(), "runs": [],
                                            "was": set(), "was_named": set()})
        if run not in entry["runs"]:
            entry["runs"].append(run)
        for path in itself:
            if path and Path(str(path)).is_absolute() and Path(str(path)) != system:
                entry["was"].add(os.path.normpath(str(path)))
        entry["was_named"].update(os.path.normpath(str(n)) for n in names
                                  if _full(n) is not None)
        entry.setdefault("typed", set()).update(str(n) for n in names
                                                if str(n or "").startswith("~/"))
        # The folders it is named by: its own, or a study holding it where
        # setup lays one out (`setup/`, `shared_setup/setup/`).
        for given in names:
            given_path = _full(given)
            if given_path is None:
                continue
            try:
                spellings = (Path(os.path.normpath(given_path)), given_path.resolve())
            except (OSError, RuntimeError):
                continue
            for spelled in spellings:
                try:
                    holding = spelled.resolve()
                except (OSError, RuntimeError):
                    continue
                if any(holding / layout == system if layout else holding == system
                       for layout in pipeline.PREPARED_SYSTEM_LAYOUTS):
                    entry["names"].add(spelled)

    # Each prepared system not found here, and the runs that simulated it:
    # said at once, every run named.
    not_found: dict[str, list[str]] = {}
    # Each prepared system that is another one now, by what was found, and
    # the runs that simulated it: said at once too.
    changed: dict[str, list[str]] = {}
    found_systems: set[Path] = set()
    for run in manifest.get("runs") or [""]:
        folder = root / "runs" / run if run else root
        reference = pipeline._prepared_system_recorded(folder)
        recorded = pipeline._setup_from_recorded(folder)
        try:
            if reference is not None:
                # A name this computer cannot read (another person's `~`) is
                # left out of the lookup, as the rest find the system.
                readable = {k: v for k, v in reference.items()
                            if k not in ("given", "resolved") or _readable(v)}
                simulated = pipeline._the_system_recorded(folder, readable)
            else:
                taken = pipeline._setup_taken_from(folder)
                candidates = ([taken] if taken is not None else []) + [
                    pipeline.where_a_prepared_system_sits(_full(n))
                    for n in recorded if _full(n) is not None]
                simulated = next((c for c in candidates if (c / "system.xml").is_file()), None)
        except CodedError as exc:
            changed.setdefault(str(exc), []).append(run)
            continue
        except RuntimeError as exc:
            raise ShareRefused(
                of(run, f"names its prepared system by a path this computer cannot read "
                        f"({exc}); share it where that path is read."),
                code="environment.share.not_finished") from exc
        named = [str(v) for v in ((reference or {}).get("given"),
                                  (reference or {}).get("resolved")) if v] + recorded
        if simulated is None:
            relative = str((reference or {}).get("relative_to_run") or "")
            unread = _unreadable_system([*(_full(n) for n in named),
                                         folder / relative if relative else None])
            if unread is not None:
                raise ShareRefused(
                    of(run, f"simulated the prepared system in {unread[0].parent}, whose "
                            f"{unread[0].name} could not be read ({unread[1]}); nothing was "
                            "written. Make it readable and share it again."),
                    code="environment.share.not_finished", path=str(unread[0]))
            if reference is not None or recorded:
                # By where the system was, so runs naming it two ways are one.
                key = str((reference or {}).get("resolved") or (named[0] if named else ""))
                not_found.setdefault(key, []).append(run)
            continue
        simulated = simulated.resolve()
        found_systems.add(simulated)
        try:
            own_setup = (folder / "setup").resolve()
        except (OSError, RuntimeError):
            own_setup = None
        if simulated == own_setup:
            continue
        note(simulated, named, run,
             [(reference or {}).get("resolved"), pipeline._setup_taken_from(folder)])
        topology[run] = simulated
        users.append(("run", run, simulated, simulated))
        # A window's seed keeps no setup record: it is with the preparation
        # the seed was taken from, found as a reader finds it.
        seeded = _load(simulated / SEEDED_FROM)
        if (isinstance(seeded, dict) and seeded.get("system_xml_sha256")
                and not (simulated / "setup_parameters.json").is_file()):
            try:
                readable = {k: v for k, v in seeded.items()
                            if k not in ("given", "resolved") or _readable(v)}
                records = pipeline._the_system_recorded(simulated, readable,
                                                        verb="was seeded from")
            except CodedError as exc:
                changed.setdefault(str(exc), []).append(run)
                continue
            except RuntimeError as exc:
                raise ShareRefused(
                    of(run, f"simulated a seed whose preparation is named by a path this "
                            f"computer cannot read ({exc})."),
                    code="environment.share.not_finished") from exc
            if records is None or records.resolve() == simulated:
                raise ShareRefused(of(run, (
                    f"simulated a seed taken from the preparation named "
                    f"{seeded.get('resolved') or seeded.get('given')}, which is not found "
                    "here, so a reader of the shared study would have no setup record for "
                    "it. Put it back and share it again.")),
                    code="environment.share.not_finished")
            records = records.resolve()
            found_systems.add(records)
            note(records, [seeded.get("given"), seeded.get("resolved")], run,
                 [seeded.get("resolved")])
            users.append(("seed", run, simulated, records))

    if changed:
        said_of = "; ".join(
            f"{', '.join(r for r in runs_of if r) or 'the study'}: {why.rstrip('.')}"
            for why, runs_of in changed.items())
        users = [r for runs_of in changed.values() for r in runs_of if r]
        subject = (f"The run{'s' if len(users) > 1 else ''} {', '.join(users)} of the study in "
                   f"{root}" if users else f"The study in {root}")
        raise ShareRefused(f"{subject} simulated a prepared system that is another one now "
                           f"({said_of}).", code="environment.share.not_finished")
    if not_found:
        lines = []
        for name, runs_of in not_found.items():
            who = (f"the run{'s' if len(runs_of) > 1 else ''} {', '.join(runs_of)}"
                   if any(runs_of) else "the study")
            lines.append(f"{name or 'the one in its record'} ({who})")
        one = len(not_found) == 1
        (first_name, first_runs), = list(not_found.items())[:1]
        subject = (f"The run{'s' if len(first_runs) > 1 else ''} {', '.join(first_runs)} of the "
                   f"study in {root}" if any(first_runs) else f"The study in {root}")
        raise ShareRefused(
            (f"{subject} simulated the prepared system named "
             f"{first_name or 'in its record'}, which is not found here"
             if one else f"The study in {root} simulated prepared systems not found here: "
             + "; ".join(lines))
            + ", so a reader of the shared study would have none. Put "
            + ("it" if one else "each") + " back where the runs' records say, or beside the "
            "study as it was, and share it again.", code="environment.share.not_finished")
    # Where a system was is said as where it is now only where nothing of
    # the study, nor another system found, is there now: the root's own rule
    # says the study's paths, and a copy's record names its original's.
    studies = {os.path.normpath(str(p)) for p in (real, root.absolute(),
                                                    manifest.get("output_dir")) if p}
    systems_found = {os.path.normpath(str(system)) for system in found_systems}
    for run in manifest.get("runs") or []:
        # A run's own folder as its records give it (carried on elsewhere).
        recorded = _load(root / "runs" / run / "manifest.json")
        recorded = recorded.get("output_dir") if isinstance(recorded, dict) else None
        if isinstance(recorded, str) and _absolute(recorded):
            systems_found.add(os.path.normpath(recorded))

    def is_here(path: str) -> bool:
        # A folder of the study the archive leaves out is carried elsewhere,
        # so where it was is said as where it is carried.
        for folder in studies:
            if path == folder or path.startswith(folder.rstrip("/") + "/"):
                if not _left_out(os.path.relpath(path, folder), manifest.get("runs")):
                    return True
        return any(path == f or path.startswith(f.rstrip("/") + "/") for f in systems_found)

    for entry in outside.values():
        entry["was"] = {w for w in entry["was"] if not is_here(w)}
    for was in [w for w in was_inside if is_here(w)]:
        del was_inside[was]
    hosts = _hosts(root, manifest, [folder for entry in outside.values()
                                    for folder in (entry["system"], entry["system"].parent)])
    machines = {name.casefold() for name in hosts}
    host_said = _Scrubber([], hosts).hosts
    taken_names: set[str] = set()
    carried = []
    inside_of: dict[Path, str] = {}
    for system, entry in sorted(outside.items(), key=lambda item: str(item[0])):
        # Carried as the shallowest folder it is named by, so each name
        # finds it as it did: `setup_from: /x/prep` as `prepared/prep`, with
        # its `setup/` in it; one named by itself alone, as the study that
        # holds it where setup lays one out, so it is named by that study.
        try:
            home = Path.home().resolve()
        except (OSError, RuntimeError):
            home = Path.home()
        holding = [system.parents[len(PurePosixPath(layout).parts) - 1]
                   for layout in pipeline.PREPARED_SYSTEM_LAYOUTS
                   if layout and system.as_posix().endswith("/" + layout)]
        holding = [h for h in holding if h not in (home, *home.parents, Path(h.anchor),
                                                    real, *real.parents)]
        entry["base"] = min([*(n.resolve() for n in entry["names"]), *holding],
                            key=lambda n: len(n.parts), default=system)
        # One folder for each study carried from: its two systems (`setup/`
        # and `shared_setup/setup/`) in one, as they were.
        inside = inside_of.get(entry["base"])
        if inside is None:
            stem = re.sub(r"[^A-Za-z0-9._-]+", "-", entry["base"].name).strip("-.") or "system"
            folded = stem.casefold()
            if any(said.search(stem) for said in host_said) or any(
                    len(name) >= 3 and name in folded for name in {*_logins(), *machines}):
                # A folder named for the person (`/scratch/<login>/setup`,
                # `<login>.old`) or for the computer (`labbox07.old`,
                # `prep-labbox07`): neither name goes in the archive.
                stem = "preparation"
            name, n = stem, 2
            while name in taken_names or (root / PREPARED / name).exists():
                name, n = f"{stem}-{n}", n + 1
            taken_names.add(name)
            inside = inside_of[entry["base"]] = f"{PREPARED}/{name}"
        inner = system.relative_to(entry["base"]).as_posix()
        at = inside if inner == "." else f"{inside}/{inner}"
        # Paths in the system's folder, as the folder it is carried in; each
        # name it is given by, as that name alone (what else a study named
        # by it holds is not carried, so is not said to be).
        said = {str(system): (at, False)}
        for named in entry["names"]:
            real_name = named.resolve()
            below = real_name.relative_to(entry["base"]).as_posix()
            # The system's folder spelled through the name (a link to the
            # study it is in), as a folder; the name itself alone.
            said.setdefault(str(named / system.relative_to(real_name)), (at, False))
            if real_name != system:
                said.setdefault(str(named), (inside if below == "." else f"{inside}/{below}",
                                             True))
        # Where it was when the study ran, for a study moved since: the
        # system's folder as a folder, a study holding it as its name alone.
        was_said: dict[str, tuple[str, bool]] = dict(_typed_from_home(
            sorted(entry.get("typed") or ()), sorted(entry["was"]), at))
        for was in sorted(entry["was"]):
            was_said.setdefault(was, (at, False))
            for named in sorted(entry["was_named"]):
                if named in said or named in was_said:
                    continue
                below = os.path.relpath(was, named)
                if below == ".":
                    was_said[named] = (at, False)
                elif below == inner and not below.startswith(".."):
                    was_said[named] = (inside, True)
        # As typed under the home folder (`setup_from: ~/prep`).
        home_here = Path.home()
        try:
            homes_here = {home_here, home_here.resolve()}
        except (OSError, RuntimeError):
            homes_here = {home_here}
        for name, rule in list(said.items()):
            for base in homes_here:
                if _within(Path(name), base) and Path(name) != base:
                    said.setdefault("~/" + Path(name).relative_to(base).as_posix(), rule)
        entry.update(inside=inside, at=at, said=said, was_said=was_said)
        carried.append(entry)

    def in_crate(system: Path) -> str:
        if system in outside:
            return outside[system]["at"]
        return _in_study(system, real) or ""

    relative = {}
    for kind, run, simulated, system in users:
        if kind == "run":
            at = f"runs/{run}" if run else ""
            relative[f"{at}/simulation/simulation_parameters.json".lstrip("/")] = (
                at, in_crate(system))
        else:
            seed = in_crate(simulated)
            relative[f"{seed}/{SEEDED_FROM}"] = (seed, in_crate(system))
    return {"carried": carried, "needed": needed, "was_inside": was_inside,
            "topology_of": {run: f"{in_crate(s)}/topology.pdb" for run, s in topology.items()},
            "relative": relative}


def _made_in(system: Path) -> list[str]:
    """The folder a prepared system's own setup record says it was made in,
    where not where it is now (made in one folder, copied to another): from
    the structure setup kept beside what it made (`parameters._retained_pdb`),
    the one path it records that it wrote itself; never what it read
    (`input`, a ligand file given). None where that folder is named
    otherwise than the system's, or holds that file with other bytes here."""
    record = _load(system / "setup_parameters.json")
    parameters = record.get("parameters") if isinstance(record, dict) else None
    retained = parameters.get("_retained_pdb") if isinstance(parameters, dict) else None
    if not isinstance(retained, str) or not _absolute(retained):
        return []
    own = _base_name(retained)
    folder = retained[:len(retained) - len(own) - 1]
    if (not own or not (system / own).is_file()
            or PurePosixPath(folder.replace("\\", "/")).name != system.name
            or os.path.normpath(folder) == os.path.normpath(str(system))):
        return []
    there = Path(retained)
    try:
        if there.is_file() and (there.stat().st_size != (system / own).stat().st_size
                                or _sha256_of(there) != _sha256_of(system / own)):
            return []
    except OSError:
        return []
    return [os.path.normpath(folder) if folder.startswith("/") else folder]


def _base_name(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _typed_from_home(names: list[Any], was: list[str], inner: str
                     ) -> dict[str, tuple[str, bool]]:
    """Each name typed from a home folder (`~/prep`) that names a folder where
    the system was (``was``), on the computer it was typed on, as the folder
    it is carried in or now is (``inner``, a path in the study): the system's
    own folder as a folder, a study holding it as its name alone."""
    from fastmdxplora.simulation.pipeline import PREPARED_SYSTEM_LAYOUTS

    out: dict[str, tuple[str, bool]] = {}
    for name in names:
        typed = str(name or "")
        if not typed.startswith("~/"):
            continue
        tail = typed[2:].rstrip("/")
        for folder in was:
            for layout in PREPARED_SYSTEM_LAYOUTS:
                holder = folder if not layout else (
                    folder[:-len(layout) - 1] if folder.endswith("/" + layout) else "")
                if not holder or not (holder == tail or holder.endswith("/" + tail)):
                    continue
                if not layout:
                    out.setdefault(typed.rstrip("/"), (inner, False))
                elif inner.endswith("/" + layout):
                    out.setdefault(typed.rstrip("/"), (inner[:-len(layout) - 1], True))
    return out


def _readable(name: Any) -> bool:
    """Whether the simulation phase can look a recorded name up here: all
    but another person's home (`~otheruser/...`), which raises; a relative
    name (`inputs/prep`, a fetched study's) it reads as it does."""
    text = str(name or "")
    return not text.startswith("~") or _full(text) is not None


def _full(name: Any) -> Path | None:
    """A recorded path as a full path here, or None: not a full path, or a
    home this computer does not know (`~otheruser/prep`)."""
    try:
        path = Path(str(name or "")).expanduser()
    except RuntimeError:
        return None
    return path if name and path.is_absolute() else None


def _left_out(inner: str, runs: Any) -> bool:
    """Whether a folder of the study, as a path in it, is one a shared study
    leaves out: one whose name begins with a dot, one never shared (what
    `--rerun` set aside, a deposit, scratch), or a run's folder the record of
    runs does not name."""
    parts = PurePosixPath(inner).parts
    if any(part.startswith(".") for part in parts) or never_shared(f"{inner}/system.xml"):
        return True
    return bool(runs) and len(parts) >= 2 and parts[0] == "runs" and parts[1] not in runs


def _in_study(path: Path, real: Path) -> str | None:
    """``path`` as a path in the study, or None outside it."""
    return path.relative_to(real).as_posix() if _within(path, real) else None


def _logins() -> frozenset[str]:
    """The person's names a folder may be called by: their login and their
    home folder's name, as the disk compares them."""
    import getpass

    names = {Path.home().name}
    try:
        names.add(getpass.getuser())
    except Exception:  # noqa: BLE001 - no login name to be found
        pass
    try:
        names.add(Path.home().resolve().name)
    except (OSError, RuntimeError):
        pass
    return frozenset(n.casefold() for n in names if n)


def _fetched_names(root: Path, carried: list[dict[str, Any]]
                   ) -> dict[str, tuple[str, bool]]:
    """For a study fetched back (`fetched.json` at its top), each name the
    machine it ran on gave an input that is carried, as its carried folder."""
    record = _load(root / "fetched.json")
    if not isinstance(record, dict) or not isinstance(record.get("inputs"), dict):
        return {}
    remote = str(record.get("remote_dir") or "").rstrip("/")
    out: dict[str, tuple[str, bool]] = {}
    for name, source in sorted(record["inputs"].items()):
        here = _full(source)
        if here is None or not re.fullmatch(r"[\w.-]+", str(name)):
            continue
        try:
            here = here.resolve()
        except (OSError, RuntimeError):
            continue
        for entry in carried:
            # The input names the carried folder itself, as a folder; or a
            # folder holding it, which says only the way down to it.
            inside = entry["inside"]
            if entry["base"] == here:
                below = ""
            elif _within(entry["base"], here):
                below = "/" + entry["base"].relative_to(here).as_posix()
            elif _within(here, entry["base"]) and (_within(here, entry["system"])
                                                   or _within(entry["system"], here)):
                # The input is the system's own folder (`.../prep/setup`), or
                # one holding it (`.../prep/shared_setup`), in the folder
                # carried (`prep`); not one beside it (`.../prep/ligs`).
                below, inside = "", f"{inside}/{here.relative_to(entry['base']).as_posix()}"
            else:
                continue
            for typed in (f"inputs/{name}", f"{remote}/inputs/{name}" if remote else ""):
                if typed:
                    out.setdefault(typed + below, (inside, False))
    return out


def _carried_folders(carried: list[dict[str, Any]],
                     order: list[str] | None = None) -> list[dict[str, Any]]:
    """Each folder systems are carried in, once, with every run that
    simulated from a system in it, in the order of the record of runs."""
    folders: dict[str, list[str]] = {}
    for entry in carried:
        runs = folders.setdefault(entry["inside"], [])
        runs += [run for run in entry["runs"] if run not in runs]
    place = {run: n for n, run in enumerate(order or [])}
    return [{"at": inside, "runs": sorted(runs, key=lambda r: place.get(r, len(place)))}
            for inside, runs in folders.items()]


def _carried_files(entry: dict[str, Any], everything: bool) -> list[dict[str, Any]]:
    """The files of a prepared system kept outside the study, as it is
    carried in it: what a run needs of it (:data:`_PREPARED_NEEDS`) and the
    records and chemistry setup wrote beside it; with ``everything``, all of
    its folder."""
    from fastmdxplora.study_files import kind_of, label_of

    system: Path = entry["system"]

    def packed(rel: str) -> bool:
        path = PurePosixPath(rel)
        return not never_shared(rel) and (everything or rel in _PREPARED_NEEDS or not (
            path.name in _ONLY_ALL_NAMES or path.suffix.lower() in _ONLY_ALL_SUFFIXES))

    try:
        links = [path for path in sorted(system.rglob("*"))
                 if path.is_symlink() and "__pycache__" not in path.parts
                 and path.suffix != ".tmp" and (
                     packed(path.relative_to(system).as_posix() + "/_") if path.is_dir()
                     else packed(path.relative_to(system).as_posix()) and (
                         path.exists() or packed(path.relative_to(system).as_posix() + "/_")))]
    except OSError as exc:
        raise ShareRefused(
            f"The prepared system in {system}, which runs of the study simulated from, could "
            f"not be read whole ({exc.strerror or exc}); nothing was written. Make it "
            "readable and share it again.",
            code="environment.share.not_finished", path=str(system)) from exc
    if links:
        # A link is never packed: carried without what it leads to, the run
        # would be opened missing it.
        users = [run for run in entry["runs"] if run]
        who = (f"the run{'s' if len(users) > 1 else ''} {', '.join(users)}" if users
               else "the study")
        raise ShareRefused(
            f"The prepared system in {system}, which {who} simulated from, holds "
            f"{'a link' if len(links) == 1 else f'{len(links)} links'}, which a shared study "
            f"does not carry: {', '.join(str(p) for p in links)}. Put the files they lead to "
            "in their place (or remove one that leads nowhere), and share it again.",
            code="environment.share.not_finished", path=str(links[0]))
    out = []
    for rel in _walk(system, packed):
        path = PurePosixPath(rel)
        if never_shared(rel) or (not everything and rel not in _PREPARED_NEEDS and (
                path.name in _ONLY_ALL_NAMES or path.suffix.lower() in _ONLY_ALL_SUFFIXES)):
            continue
        out.append({"path": f"{entry['at']}/{rel}", "phase": "setup", "run": "",
                    "kind": kind_of(rel), "opens_with": "", "source": system / rel,
                    "label": f"{label_of('setup/' + rel)}, of the prepared system the runs "
                             "simulated from"})
    return out


def _zip_time(manifest: dict[str, Any]) -> tuple[int, int, int, int, int, int]:
    ends = [str(p.get("finished_at") or "") for p in manifest.get("phases") or []
            if isinstance(p, dict)]
    for end in sorted(ends, reverse=True):
        try:
            when = datetime.fromisoformat(end).astimezone(timezone.utc)
        except ValueError:
            continue
        if when.year >= 1980:
            return (when.year, when.month, when.day, when.hour, when.minute,
                    when.second - when.second % 2)
    return (1980, 1, 1, 0, 0, 0)


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for piece in iter(lambda: handle.read(1 << 20), b""):
            digest.update(piece)
    return digest.hexdigest()


def share_study(root: str | Path, to: str | Path, *, everything: bool = False,
                license_id: str = DEFAULT_LICENSE, authors: list[tuple[str, str]] | None = None,
                published: date | None = None, identifier: str = "",
                said: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Pack the finished study in ``root`` as ``to``; what was written: its
    `path`, `files`, `bytes` and `sha256`, the `crate`, and which files hold
    each placeholder (`placeholders`). A disk with no room for it refuses."""
    try:
        return _packed(root, to, everything=everything, license_id=license_id,
                       authors=authors, published=published, identifier=identifier,
                       said=said)
    except OSError as exc:
        if exc.errno not in (errno.ENOSPC, errno.EDQUOT, errno.EFBIG):
            raise
        where = Path(exc.filename).parent if exc.filename else Path(to).expanduser().parent
        spooled = Path(tempfile.gettempdir())
        if any(part.startswith("fastmdx-share-") for part in where.parts):
            # Large files are written there on their way into the archive.
            raise ShareRefused(
                f"The temporary folder {spooled}, where large files are written on their way "
                f"into the archive, has no room ({exc.strerror}); nothing was written. Make "
                "room there, or set TMPDIR to a folder with room, and share again.",
                code="environment.share.too_large", path=str(spooled)) from exc
        raise ShareRefused(
            f"The shared study could not be written to {where}: the disk there has no room "
            f"for it ({exc.strerror}); "
            + ("no archive is there now (the one packed before was taken away first, to "
               "make room). " if getattr(exc, "replaced", False) else "nothing was written. ")
            + "Make room there, or write it elsewhere.",
            code="environment.share.too_large", path=str(where)) from exc


def _packed(root: str | Path, to: str | Path, *, everything: bool, license_id: str,
            authors: list[tuple[str, str]] | None, published: date | None, identifier: str,
            said: Callable[[str], None] | None) -> dict[str, Any]:
    from fastmdxplora.study_files import STORED

    if not authors:
        raise ShareRefused("A shared study names at least one author.",
                           code="environment.share.not_finished")
    root = Path(root).expanduser()
    target = Path(to).expanduser()
    real = root.resolve()
    if real == target.resolve().parent or real in target.resolve().parents:
        raise ShareRefused(f"{target} is inside the study it packs; write it elsewhere.",
                           code="environment.path.exists", path=str(target))
    manifest = finished_manifest(root)
    demo = _load(root / "demo.json")
    roots = [str(root.absolute()), str(real), str(manifest.get("output_dir") or "")]
    if isinstance(demo, dict) and demo.get("made_in"):
        roots.append(str(demo["made_in"]))
    roots = [r for r in roots if r and _absolute(r)]
    from fastmdxplora.simulation.pipeline import FETCHED_RECORD

    # Read without a word, its runs' systems would be looked for only where
    # they were on the other machine.
    _record_read(root / FETCHED_RECORD, "where the study was fetched back from", root)
    prepared = _prepared_systems(root, manifest)
    carried = prepared["carried"]
    elsewhere = [folder for entry in carried
                 for folder in dict.fromkeys((entry["base"], entry["system"].parent))]
    # Each system's own paths before where any was when the study ran: a
    # path one system was at and another is at now is said as the one there.
    runs = manifest.get("runs")
    rules: dict[str, tuple[str, bool]] = {}
    for entry in carried:
        for name, rule in entry["said"].items():
            rules.setdefault(name, rule)
    # A study fetched back from another machine names its inputs as they
    # were named there (`inputs/<name>`, `<remote_dir>/inputs/<name>`):
    # each one carried is said as the folder it is carried in.
    for name, rule in _fetched_names(root, carried).items():
        rules.setdefault(name, rule)
    # A run's own folder as its records give it, where the record of runs
    # gives another (a run carried on elsewhere): only one only that run
    # gives, and outside the study, so no rule says another run's paths.
    given_by: dict[str, list[str]] = {}
    for run in runs or []:
        recorded = _load(root / "runs" / run / "manifest.json")
        recorded = str(recorded.get("output_dir") or "") if isinstance(recorded, dict) else ""
        if recorded and _absolute(recorded):
            given_by.setdefault(os.path.normpath(recorded), []).append(run)
    inside_roots = [os.path.normpath(r) for r in roots]
    # What a folder said as a run's must not hold, nor be in: the study, a
    # carried system; nor be the home folder or above it (every path under
    # it would be said as the run's).
    held = [*inside_roots, *(str(entry["system"]) for entry in carried)]
    homes = {str(Path.home()), str(Path.home().resolve())}

    def above(folder: str, path: str) -> bool:
        return path == folder or path.startswith(folder.rstrip("/") + "/")

    for recorded, by in sorted(given_by.items()):
        if (len(by) == 1 and not any(above(r, recorded) or above(recorded, r) for r in held)
                and not any(above(recorded, home) for home in homes)):
            rules.setdefault(recorded, (f"runs/{by[0]}", False))
    for name, rule in [*(item for entry in carried for item in entry["was_said"].items()),
                       *prepared["was_inside"].items()]:
        rules.setdefault(name, rule)
    scrub = _Scrubber(roots, _hosts(root, manifest, elsewhere), rules)
    links: list[str] = []
    entries = chosen_files(root, everything, roots, frozenset(prepared["needed"]),
                           frozenset(runs) if runs else None, links)
    from fastmdxplora.simulation.seeding import SEEDED_FROM
    from fastmdxplora.study_files import label_of

    for entry in entries:
        name = PurePosixPath(entry["path"]).name
        if entry["path"] in prepared["needed"]:
            entry["label"] = (f"{label_of('setup/' + name)}, of the prepared system the runs "
                              "simulated from")
        elif name == SEEDED_FROM:
            entry["label"] = "Which preparation this window's seed was taken from"
    have = {e["path"] for e in entries}
    missing = [(path, ", ".join(runs)) for path, runs in sorted(prepared["needed"].items())
               if path not in have]
    if missing:
        # Kept as links, which are never packed: the run would be opened
        # with none.
        folders: dict[str, tuple[list[str], list[str]]] = {}
        for path, runs_of in missing:
            names, users = folders.setdefault(str(root / PurePosixPath(path).parent), ([], []))
            names.append(PurePosixPath(path).name)
            users += [r for r in runs_of.split(", ") if r and r not in users]

        def who(users: list[str]) -> str:
            return (f"the run{'s' if len(users) > 1 else ''} {', '.join(users)}" if users
                    else "the study")

        if len(folders) == 1:
            (folder, (names, users)), = folders.items()
            subject = (f"The run{'s' if len(users) > 1 else ''} {', '.join(users)} of the "
                       f"study in {root}" if users else f"The study in {root}")
            said = (f"{subject} simulated the prepared system in {folder}, and its "
                    f"{', '.join(names)} {'is a link' if len(names) == 1 else 'are links'}")
        else:
            said = (f"The study in {root} simulated prepared systems kept as links: "
                    + "; ".join(f"in {folder} ({who(users)}), {', '.join(names)}"
                                for folder, (names, users) in folders.items()))
        raise ShareRefused(
            f"{said}, which a shared study does not carry. Put the files they lead to in "
            "their place, and share it again.", code="environment.share.not_finished",
            path=missing[0][0])
    if links:
        # Left out, the study would be opened without what they lead to.
        raise ShareRefused(
            f"The study in {root} holds {'a link' if len(links) == 1 else f'{len(links)} links'}"
            f", which a shared study does not carry: {', '.join(links)}. Put the files they "
            "lead to in their place (or remove one that leads nowhere), and share it again.",
            code="environment.share.not_finished", path=links[0])
    for entry in carried:
        entries += _carried_files(entry, everything)
    entries.sort(key=lambda e: e["path"])
    topologies = frozenset(e["opens_with"] for e in entries if e["opens_with"])

    with tempfile.TemporaryDirectory(prefix="fastmdx-share-") as spooled_in:
        spool = Path(spooled_in)
        payload: dict[str, bytes | Path] = {}
        files: list[dict[str, Any]] = []
        placeholders: dict[str, list[str]] = {STUDY: [], HOME: [], HOST: []}
        for entry in entries:
            rel = entry["path"]
            source = entry.get("source") or root / rel
            data: bytes | None = None
            if PurePosixPath(rel).suffix.lower() in TEXT_SUFFIXES:
                try:
                    raw = source.read_bytes()
                except OSError as exc:
                    raise ShareRefused(f"{source} could not be read ({exc.strerror or exc}); "
                                       "nothing was written. Make it readable and share it "
                                       "again.",
                                       code="environment.share.not_finished",
                                       path=rel) from exc
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    text = None
                if text is not None and PurePosixPath(rel).name in _AS_THEY_ARE:
                    # Packed byte for byte: a run finds its system by
                    # `system.xml`'s SHA-256. They name no path; one that
                    # did would not be packed.
                    left = scrub.left(text, hosts=False)
                    if left:
                        raise ShareRefused(
                            f"{rel} names {left!r}, and it is packed as it is; nothing was "
                            "written.", code="environment.share.unscrubbed", path=rel)
                    text = None
                if text is not None:
                    if rel in prepared["relative"]:
                        text = _relative_put(text, *prepared["relative"][rel])
                    text, used = scrub(text)
                    used |= {key for key in (HOME, HOST)
                             if key in text or _ESCAPED[key] in text}
                    if PurePosixPath(rel).suffix.lower() in (".md", ".markdown"):
                        text = _markdown_plain(text)
                    left = scrub.left(text)
                    if left:
                        raise ShareRefused(
                            f"{rel} still names {left!r} after its paths and the computer's name "
                            "were replaced; nothing was written.",
                            code="environment.share.unscrubbed", path=rel)
                    for key in used:
                        placeholders[key].append(rel)
                    data = text.encode("utf-8")
            if data is not None:
                size, digest = len(data), hashlib.sha256(data).hexdigest()
                if size > _HELD_IN_MEMORY:
                    # Written out as it is read, so a study of large tables is
                    # not held whole in memory until the zip is written.
                    spooled = spool / f"{len(payload)}.txt"
                    spooled.write_bytes(data)
                    payload[rel] = spooled
                else:
                    payload[rel] = data
            else:
                payload[rel] = source
                try:
                    size, digest = source.stat().st_size, _sha256_of(source)
                except OSError as exc:
                    raise ShareRefused(f"{source} could not be read ({exc.strerror or exc}); "
                                       "nothing was written. Make it readable and share it "
                                       "again.",
                                       code="environment.share.not_finished",
                                       path=rel) from exc
            phase = entry["phase"]
            record = {"path": rel, "name": entry["label"], "size": size, "sha256": digest,
                      "phase": {"saved": "viewer", "record": "study"}.get(phase, phase),
                      "run": entry["run"],
                      "role": packing_list.role_of(rel, phase=phase, kind=entry["kind"],
                                                   topologies=topologies)}
            if record["role"] == "trajectory":
                if not entry["opens_with"]:
                    raise ShareRefused(
                        f"{rel} has no topology beside it to be read with, so a reader of the "
                        "shared study could not open it.", code="environment.share.not_finished",
                        path=rel)
                record["read_with"] = entry["opens_with"]
                if PurePosixPath(rel).suffix.lower() == ".dcd":
                    record["frames"], record["atoms"] = _frames_and_atoms(source)
                record["interval_ps"] = _interval_ps(root, rel, entry["run"])
            files.append(record)

        title, summary = _title_and_summary(root, manifest, scrub)
        based_on = _load(root / SHARED_FROM)
        crate = packing_list.build(
            name=title, description=summary,
            published=(published or datetime.now(timezone.utc).date()).isoformat(),
            license_id=license_id or DEFAULT_LICENSE, authors=list(authors or []),
            files=files, actions=_actions(manifest, files, prepared["topology_of"]),
            runs=[{"id": run, "system": (manifest.get("systems_of_runs") or {}).get(run, "")}
                  for run in manifest.get("runs") or []],
            prepared=_carried_folders(carried, manifest.get("runs")),
            software={"version": str(manifest.get("version") or ""),
                      "url": "https://github.com/aai-research-lab/FastMDXplora"},
            package="all" if everything else "view", placeholders=placeholders,
            identifier=identifier,
            based_on=str(based_on.get("doi") or "") if isinstance(based_on, dict) else "")
        payload[packing_list.METADATA] = (json.dumps(crate, indent=1, ensure_ascii=False)
                                          + "\n").encode("utf-8")

        when = _zip_time(manifest)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        # An archive packed before (without its DOI, say) is made again: room
        # for one archive, not two, is room enough.
        replaced = target.exists()
        target.unlink(missing_ok=True)
        try:
            with zipfile.ZipFile(temporary, "w", allowZip64=True) as archive:
                for name in sorted(payload):
                    info = zipfile.ZipInfo(name, date_time=when)
                    info.create_system = 3
                    info.external_attr = 0o644 << 16
                    info.compress_type = (zipfile.ZIP_STORED
                                          if PurePosixPath(name).suffix.lower() in STORED
                                          else zipfile.ZIP_DEFLATED)
                    body = payload[name]
                    if isinstance(body, bytes):
                        archive.writestr(info, body)
                    else:
                        info.file_size = body.stat().st_size
                        with body.open("rb") as source, archive.open(info, "w") as sink:
                            for piece in iter(lambda: source.read(1 << 20), b""):
                                sink.write(piece)
            os.replace(temporary, target)
        except OSError as exc:
            exc.replaced = replaced
            raise
        finally:
            temporary.unlink(missing_ok=True)
        written = {"path": str(target), "files": len(files), "bytes": target.stat().st_size,
                   "sha256": _sha256_of(target), "crate": crate,
                   "placeholders": {k: v for k, v in placeholders.items() if v},
                   "package": "all" if everything else "view"}
        if said:
            size = (f"{written['bytes'] / 1e6:.1f} MB" if written["bytes"] >= 1e5
                    else f"{written['bytes'] / 1e3:.1f} kB")
            said(f"Shared {len(files)} files of {root} as {target} ({size}), "
                 f"SHA-256 {written['sha256']}.")
        return written
