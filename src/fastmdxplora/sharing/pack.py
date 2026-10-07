"""Pack a finished study as one zip another FastMDXplora opens: `--share`.

What goes in, by default, is what the study's pages read once it has
finished: the Config and the Manifest, the structures setup made, the
trajectory and the topology it is read with, the run's records, every
analysis's numbers and figures, the report, and what the person kept from
the Viewer. `--share-all` adds every other file the study keeps. Never in
it: the Agent's conversations, the record of the process that ran it, tags
and notes, the Viewer's scratch, what `--rerun` set aside, deposits and the
project bundle.

A study's records name its files by the path they were written at. Each
text file is packed with the study's folder said as `__FASTMDX_STUDY__`,
any other home folder's path as `__FASTMDX_HOME__` and the computer's name
as `__FASTMDX_HOST__`, and read again: an archive that still holds any of
them is not written. The same study packed with the same choices on the
same day is the same bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
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
#: of the rest, where an archive opened here came from, and the Zenodo
#: record it was shared as.
_NEVER_NAMES = frozenset({"project_bundle.zip", "study_tags.json", SHARED_FROM,
                          "shared_to.json", packing_list.METADATA, packing_list.PREVIEW})
#: The Agent's conversations with the person.
_NEVER_FOLDERS = ("agent/",)
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
#: Where a path ends: not inside a longer name (`/data/run` is not
#: `/data/run2`, nor `/data/run.bak`); a full stop ending a sentence ends it.
_STARTS = r"(?<![\w.-])"
_ENDS = r"(?![\w-]|\.[\w])"
#: Names computers are given by default, which are words of a sentence too.
_COMMON_HOSTS = frozenset({"localhost", "ubuntu", "debian", "fedora", "centos", "server",
                           "linux", "node", "master", "main", "raspberrypi", "workstation",
                           "desktop", "laptop", "computer", "host"})


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def finished_manifest(root: Path) -> dict[str, Any]:
    """The study's Manifest, once the study has finished; refused where it
    is running, stopped short or holds no study."""
    if (root / "batch_manifest.json").is_file():
        raise ShareRefused(
            f"{root} is a study of several runs; each run is shared on its own: pass "
            "the folder of one, under runs/.", code="environment.share.not_finished")
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


def _walk(root: Path) -> list[str]:
    found = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file() or path.suffix == ".tmp" \
                or "__pycache__" in path.parts:
            continue
        found.append(path.relative_to(root).as_posix())
    return found


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
                 roots: list[str] | None = None) -> list[dict[str, Any]]:
    """The study's files that go in, in name order, each with its phase,
    kind, label, and for a trajectory the topology it is read with."""
    from fastmdxplora.deposit import _trajectory_parts
    from fastmdxplora.study_files import kind_of, label_of, place

    entries = []
    for rel in _walk(root):
        if never_shared(rel):
            continue
        phase, run, inner = place(rel)
        kind = kind_of(rel)
        entries.append({"path": rel, "phase": phase, "run": run, "kind": kind,
                        "label": label_of(inner), "opens_with": ""})
    for entry in entries:
        if entry["kind"] == "trajectory" and entry["phase"] == "simulation":
            entry["opens_with"] = _opens_with(root, entry["path"], roots or [str(root)])
    parts = _trajectory_parts(entries)
    have = {e["path"] for e in entries}
    out = []
    for entry in entries:
        path = PurePosixPath(entry["path"])
        if not everything:
            if path.name in _ONLY_ALL_NAMES or path.suffix.lower() in _ONLY_ALL_SUFFIXES \
                    or _MEDOID.search(path.name) or parts.get(entry["path"]) == "segments":
                continue
            # A figure is shown from its picture; its vector twin is a download.
            if path.suffix.lower() == ".svg" and path.with_suffix(".png").as_posix() in have:
                continue
        out.append(entry)
    return out


def never_shared(rel: str) -> bool:
    """Whether a path of a study is never in a shared one: its Agent
    conversations, the record of its process, tags and notes, a file or
    folder whose name begins with a dot, the Viewer's scratch, what
    `--rerun` set aside, deposits, a study of several runs' own record."""
    from fastmdxplora.study_files import place

    path = PurePosixPath(rel)
    if path.name in _NEVER_NAMES or rel.startswith(_NEVER_FOLDERS) \
            or any(part.startswith(".") for part in path.parts) \
            or path.name == "batch_manifest.json":
        return True
    return place(rel)[0] in _NEVER_PHASES


def _hosts(root: Path, manifest: dict[str, Any]) -> list[str]:
    """The names of the computers the study ran on, as its records give them."""
    names = {socket.gethostname()}
    for phase in manifest.get("phases") or []:
        produced = phase.get("produced_by") if isinstance(phase, dict) else None
        if isinstance(produced, dict) and produced.get("host"):
            names.add(str(produced["host"]))
    for record in (root / ".fastmdxplora_run.json", root / "simulation" / "live_status.json"):
        found = _load(record)
        if isinstance(found, dict):
            for key in ("host", "hostname"):
                if found.get(key):
                    names.add(str(found[key]))
    short = {n.split(".")[0] for n in names}
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

    def __init__(self, roots: list[str], hosts: list[str]) -> None:
        spelled = {spelling.rstrip("/\\") for root in roots if _absolute(root)
                   for spelling in _spellings(root) if len(spelling.strip("/\\")) > 1}
        self.roots = [re.compile(_STARTS + re.escape(r) + _ENDS)
                      for r in sorted(spelled, key=len, reverse=True)]
        home = str(Path.home()).rstrip("/\\")
        self.homes = [re.compile(_STARTS + re.escape(h) + _ENDS)
                      for h in sorted(_spellings(home), key=len, reverse=True)
                      if len(h.strip("/\\")) > 1]
        self.hosts = [re.compile(r"(?<![\w.-])" + re.escape(h) + r"(?:\.[\w-]+)*(?![\w-])")
                      for h in hosts]

    def __call__(self, text: str) -> tuple[str, set[str]]:
        used: set[str] = set()
        for pattern in self.roots:
            text, n = pattern.subn(STUDY, text)
            if n:
                used.add(STUDY)
        for pattern in [*self.homes, _HOMES]:
            text, n = pattern.subn(HOME, text)
            if n:
                used.add(HOME)
        for pattern in self.hosts:
            text, n = pattern.subn(HOST, text)
            if n:
                used.add(HOST)
        return text, used

    def left(self, text: str) -> str:
        """What is still there that should not be, or ''."""
        for pattern in [*self.roots, *self.homes, _HOMES, *self.hosts]:
            found = pattern.search(text)
            if found:
                return found.group(0)
        return ""


def _title_and_summary(root: Path, manifest: dict[str, Any], scrub: _Scrubber
                       ) -> tuple[str, str]:
    demo = _load(root / "demo.json")
    system = str(manifest.get("system") or root.name)
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
    summary = summary or (f"A molecular dynamics study of {system}, prepared, simulated, "
                          f"analysed and reported by FastMDXplora {version}.").replace("  ", " ")
    return (scrub(title or f"A FastMDXplora study of {system}")[0], scrub(summary)[0])


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


def _interval_ps(root: Path, trajectory: str) -> float | None:
    from fastmdxplora.gui.trajectory_frames import _interval_ns

    try:
        ns = _interval_ns(root, {"trajectory": root / trajectory})
    except Exception:  # noqa: BLE001
        return None
    return round(ns * 1000.0, 9) if ns else None


def _actions(manifest: dict[str, Any], files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    have = {f["path"] for f in files}
    trajectories = [f for f in files if f["role"] == "trajectory"]
    inputs = {
        "setup": ["setup/input.pdb"],
        "simulation": ["setup/topology.pdb"],
        "analysis": [p for f in trajectories for p in (f["path"], f.get("read_with") or "")],
        "report": ["analysis/analysis_manifest.json"],
    }
    out = []
    for phase in manifest.get("phases") or []:
        if not isinstance(phase, dict) or phase.get("status") != "ok":
            continue
        name = str(phase.get("name") or "")
        out.append({
            "name": name, "start": str(phase.get("started_at") or ""),
            "end": str(phase.get("finished_at") or ""),
            "object": [p for p in ["resolved_config.yml", *inputs.get(name, [])] if p in have],
            "result": [f["path"] for f in files if f["phase"] == name]})
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
    each placeholder (`placeholders`)."""
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
    scrub = _Scrubber([r for r in roots if r], _hosts(root, manifest))
    entries = chosen_files(root, everything, [r for r in roots if r])
    topologies = frozenset(e["opens_with"] for e in entries if e["opens_with"])

    with tempfile.TemporaryDirectory(prefix="fastmdx-share-") as spooled_in:
        spool = Path(spooled_in)
        payload: dict[str, bytes | Path] = {}
        files: list[dict[str, Any]] = []
        placeholders: dict[str, list[str]] = {STUDY: [], HOME: [], HOST: []}
        for entry in entries:
            rel = entry["path"]
            source = root / rel
            data: bytes | None = None
            if PurePosixPath(rel).suffix.lower() in TEXT_SUFFIXES:
                raw = source.read_bytes()
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    text = None
                if text is not None:
                    text, used = scrub(text)
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
                size, digest = source.stat().st_size, _sha256_of(source)
            phase = entry["phase"]
            record = {"path": rel, "name": entry["label"], "size": size, "sha256": digest,
                      "phase": {"saved": "viewer", "record": "study"}.get(phase, phase),
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
                record["interval_ps"] = _interval_ps(root, rel)
            files.append(record)

        title, summary = _title_and_summary(root, manifest, scrub)
        based_on = _load(root / SHARED_FROM)
        crate = packing_list.build(
            name=title, description=summary,
            published=(published or datetime.now(timezone.utc).date()).isoformat(),
            license_id=license_id or DEFAULT_LICENSE, authors=list(authors or []),
            files=files, actions=_actions(manifest, files),
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
        finally:
            temporary.unlink(missing_ok=True)
        written = {"path": str(target), "files": len(files), "bytes": target.stat().st_size,
                   "sha256": _sha256_of(target), "crate": crate,
                   "placeholders": {k: v for k, v in placeholders.items() if v},
                   "package": "all" if everything else "view"}
        if said:
            said(f"Shared {len(files)} files of {root} as {target} "
                 f"({written['bytes'] / 1e6:.1f} MB), SHA-256 {written['sha256']}.")
        return written
