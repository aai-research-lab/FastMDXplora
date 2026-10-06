#!/usr/bin/env python
"""Package a finished study as FastMDXplora's demo study.

The demo is trypsin with benzamidine (3PTB), run from
``src/fastmdxplora/demo/3ptb.yml`` on a GPU (10 ns, a frame every 100 ps,
no water saved). This copies what the GUI shows of it into
``src/fastmdxplora/demo/3ptb/``: the records, the prepared structure, the
live record, the production's frames, the analyses and the report, leaving
out what only a run needs (the solvated system, the force field's XML, the
checkpoints) and any other file over the size given. It writes
``demo.json``: the release that packaged it, the folder it was made in
(each path the records name there is made the copy's when a person opens
the demo, ``fastmdxplora.demo.copy_demo``), when, and how many frames.

Usage
-----
    python scripts/make_demo.py <the finished study>
    python scripts/make_demo.py <the finished study> --out <folder> --most-kb 1024

It refuses a study that has not finished each phase, or one whose
production holds more than 100 frames (the analyses number their frames as
the trajectory does, so frames are not dropped here: run it as the config
says instead).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "fastmdxplora" / "demo" / "3ptb"
MOST_FRAMES = 100
#: Files a run needs and the GUI does not show, left out whatever their size.
LEFT_OUT = {"system.xml", "state.xml", "solvated.pdb", "integrator.xml"}
LEFT_OUT_SUFFIXES = {".chk", ".nc", ".xtc"}
#: Kept whatever their size: the frames the Viewer plays, the structure they
#: are played on, and the system as simulated, which the Viewer renders
#: first and reads the ligand from (`prepared.pdb` is the protein alone).
KEPT = {"simulation/production.dcd", "simulation/trajectory_topology.pdb",
        "setup/topology.pdb"}
#: What a reader of the demo reads: left out for its size, the demo would be
#: missing a figure or a page, so packaging stops and names it.
SHOWN = ("analysis/", "report/")
PHASES = ("setup", "simulation", "analysis", "report")


def frames_in(dcd: Path) -> int:
    from mdtraj.formats import DCDTrajectoryFile

    with DCDTrajectoryFile(str(dcd)) as found:
        return len(found)


def finished(study: Path) -> list[str]:
    """What is not there for a demo, or nothing."""
    wrong = []
    try:
        manifest = json.loads((study / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["no manifest.json"]
    done = {p.get("name"): p.get("status") for p in manifest.get("phases") or []
            if isinstance(p, dict)}
    for phase in PHASES:
        if done.get(phase) not in {"ok", "completed", "complete"}:
            wrong.append(f"the {phase} phase is {done.get(phase) or 'not recorded'}")
    for needed in sorted(KEPT - {"setup/topology.pdb"}):
        if not (study / needed).is_file():
            wrong.append(f"no {needed}")
    return wrong


def package(study: Path, out: Path, most_bytes: int) -> dict:
    study = study.resolve()
    wrong = finished(study)
    if wrong:
        raise SystemExit("Not a finished study: " + "; ".join(wrong) + ".")
    frames = frames_in(study / "simulation" / "production.dcd")
    if frames > MOST_FRAMES:
        raise SystemExit(f"The production holds {frames} frames; the demo keeps at most "
                         f"{MOST_FRAMES}. Run it as src/fastmdxplora/demo/3ptb.yml says.")
    if out.exists():
        shutil.rmtree(out)
    kept, left = [], []
    for path in sorted(study.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(study).as_posix()
        size = path.stat().st_size
        if relative in KEPT or not (
                path.name in LEFT_OUT or path.suffix in LEFT_OUT_SUFFIXES
                or relative.startswith("previous/") or size > most_bytes):
            target = out / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            kept.append((relative, size))
        else:
            left.append((relative, size))
    missing = [name for name, size in left
               if name.startswith(SHOWN) and size > most_bytes]
    if missing:
        shutil.rmtree(out)
        raise SystemExit("These are shown by the GUI and are over the size given; raise "
                         "--most-kb or make them smaller: " + ", ".join(missing))
    from fastmdxplora import __version__

    record = {
        "study": "3PTB, trypsin with benzamidine",
        "config": "3ptb.yml",
        "made_in": str(study),
        "packaged": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "packaged_by": f"FastMDXplora {__version__}",
        "frames": frames,
        "bytes": sum(size for _, size in kept),
        "left_out": [name for name, _ in left],
    }
    (out / "demo.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("study", type=Path, help="The finished study's folder.")
    parser.add_argument("--out", type=Path, default=OUT, help=f"Where to write it (default {OUT}).")
    parser.add_argument("--most-kb", type=int, default=1024,
                        help="Leave out any other file larger than this (default 1024 KB).")
    args = parser.parse_args(argv)
    record = package(args.study, args.out, args.most_kb * 1024)
    print(f"Wrote {args.out}: {record['frames']} frames, {record['bytes'] / 1e6:.1f} MB.")
    if record["left_out"]:
        print("Left out: " + ", ".join(record["left_out"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
