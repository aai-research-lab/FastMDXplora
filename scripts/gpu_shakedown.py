"""Answer the three things only a real card can answer.

Everything about cost and segmentation in FastMDXplora has been checked on
a CPU with twenty argon atoms and a tri-alanine peptide. That was enough to
find real bugs -- a `resume_from` that validated and was ignored, a
checkpoint truncation OpenMM does not catch -- and it is not enough to
trust on a solvated protein at production size.

Three questions, one campaign:

**Does the cost model hold here?** It assumes seconds go as particles times
steps. Real runs of different sizes should agree about the constant to
within a few tens of per cent. `fit.spread` near 1 means it holds; past 3
the fit refuses, because averaging through a disagreement that wide would
give a confident number for a relationship that is not there.

**Does a segment actually resume?** A config field that validated and was
silently ignored is the bug this software exists to prevent, and it shipped
here. The evidence on CPU is a log line and a tri-alanine peptide. On a
real system with PME, constraints and a barostat, the question is open.

**What does a join cost under pressure?** Measured on CPU: constant volume
resumes to within 8e-8 nm, constant pressure does not, because the Monte
Carlo barostat's adaptive move size is not in the checkpoint. The mechanism
is known. The magnitude at production size is not, and it decides whether
that is a footnote or a constraint on how short a segment can usefully be.

Usage::

    # rehearse it on a CPU first, in a couple of minutes
    python scripts/gpu_shakedown.py 1UBQ.pdb --platform CPU --ns 0.01 \
        --nvt-steps 200 --npt-steps 200 --trajectory-interval 100

    # then the real thing
    python scripts/gpu_shakedown.py 1UBQ.pdb --ns 4 --segments 4

Roughly an hour on a modern card for the defaults. It runs the same study
twice -- once whole, once in segments -- because the comparison is the
measurement.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")


def parse(argv=None):
    p = argparse.ArgumentParser(
        description="Run the same study whole and in segments, and report "
                    "what only a real card can tell you.")
    p.add_argument("system", help="A PDB file or a PDB identifier.")
    p.add_argument("--ns", type=float, default=4.0,
                   help="Production nanoseconds, total (default: 4).")
    p.add_argument("--segments", type=int, default=4,
                   help="How many pieces to split it into (default: 4).")
    p.add_argument("--trajectory-interval", type=int, default=5_000,
                   help="Steps between written frames (default: 5,000).")
    p.add_argument("--output", default="shakedown",
                   help="Where to write (default: ./shakedown).")
    p.add_argument("--platform", default="CUDA",
                   help="OpenMM platform (default: CUDA).")
    p.add_argument("--precision", default="mixed")
    p.add_argument("--nvt-steps", type=int, default=25_000,
                   help="NVT equilibration steps (default: 25,000). Drop "
                        "these to a few hundred to rehearse the script on "
                        "a CPU before spending card time on it.")
    p.add_argument("--npt-steps", type=int, default=50_000,
                   help="NPT equilibration steps (default: 50,000).")
    p.add_argument("--pressure-bar", type=float, default=1.0,
                   help="Barostat pressure. 0 runs at constant volume, "
                        "which is the control for question three.")
    return p.parse_args(argv)


def study(args, *, production_ns):
    config = {
        "systems": [{"id": "shakedown", "system": args.system}],
        "setup": {"ph": 7.4, "solvent_padding_nm": 1.2,
                  "nonbonded_cutoff_nm": 0.9},
        "simulation": {
            "platform": args.platform,
            "precision": args.precision,
            "duration_ns": production_ns,
            "timestep_fs": 2,
            "nvt_steps": args.nvt_steps,
            "npt_steps": args.npt_steps,
            "trajectory_interval_steps": args.trajectory_interval,
        },
    }
    if args.pressure_bar:
        config["simulation"]["pressure_bar"] = args.pressure_bar
    return config


def run(config, output):
    from fastmdxplora import FastMDXplora

    started = time.time()
    FastMDXplora(config_data=config, output_dir=str(output)).explore()
    return time.time() - started


def main(argv=None) -> int:
    args = parse(argv)
    from fastmdxplora.cost import calibrate_from_runs, measure_this_machine
    from fastmdxplora.refusals import StudyError, refusal_of
    from fastmdxplora.simulation.resume import plan_segments, segmentability

    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    findings: dict = {"platform": args.platform, "precision": args.precision}

    print("=" * 66)
    print("1. Measuring this machine")
    print("=" * 66)
    calibration = measure_this_machine(platform_name=args.platform,
                                       precision=args.precision)
    print(f"   k = {calibration.seconds_per_particle_step:.3e} "
          f"s per particle-step, from argon")
    findings["argon_k"] = calibration.seconds_per_particle_step

    verdict = segmentability(study(args, production_ns=args.ns))
    print(f"\n   segmentable: {verdict.allowed} ({verdict.method})")
    if verdict.qualification:
        print(f"   qualified: {verdict.qualification[:150]}")
    findings["qualification"] = verdict.qualification

    print()
    print("=" * 66)
    print(f"2. The same study whole: {args.ns} ns")
    print("=" * 66)
    whole = root / "whole"
    seconds = run(study(args, production_ns=args.ns), whole)
    print(f"   {seconds / 60:.1f} minutes")

    print()
    print("=" * 66)
    print(f"3. The same study in {args.segments} segments")
    print("=" * 66)
    pieces = plan_segments(study(args, production_ns=args.ns),
                           segments=args.segments)
    resumed = 0
    directories = []
    for piece in pieces:
        where = root / f"seg{piece.index}"
        config = dict(piece.config)
        if piece.index > 0:
            config["include"] = ["simulation"]
            simulation = dict(config["simulation"])
            simulation["resume_from"] = str(
                directories[-1] / "simulation" / "checkpoint.chk")
            simulation["setup_from"] = str(directories[0] / "setup")
            config["simulation"] = simulation
        run(config, where)
        directories.append(where)
        # The log says "Resumed from" when the checkpoint was loaded. Read
        # it from the file rather than trusting the absence of an error: a
        # segment that silently started over also finishes without one.
        log = (where / "fastmdxplora.log")
        if piece.index > 0 and log.is_file():
            if "Resumed from" in log.read_text(encoding="utf-8", errors="replace"):
                resumed += 1
                print(f"   segment {piece.index}: resumed")
            else:
                print(f"   segment {piece.index}: DID NOT RESUME")
    findings["segments_that_resumed"] = resumed
    findings["segments_after_the_first"] = len(pieces) - 1

    print()
    print("=" * 66)
    print("4. What the runs say about this machine")
    print("=" * 66)
    try:
        fit = calibrate_from_runs(root, platform_name=args.platform,
                                  precision=args.precision, save=False)
        print(f"   fitted from {fit.runs} runs: "
              f"k = {fit.seconds_per_particle_step:.3e}, "
              f"spread = {fit.spread:.2f}")
        # Computed before the f-string: a line break inside a replacement
        # field needs Python 3.12, and this has to run on 3.9.
        off_by = (abs(fit.seconds_per_particle_step - findings["argon_k"])
                  / fit.seconds_per_particle_step)
        print(f"   argon was out by {off_by:.0%}")
        findings["fit_k"] = fit.seconds_per_particle_step
        findings["fit_spread"] = fit.spread
        findings["fit_runs"] = fit.runs
    except StudyError as exc:
        found = refusal_of(exc)
        print(f"   REFUSED: {found.code}")
        print(f"   {found.message[:200]}")
        findings["fit_refusal"] = found.as_dict()

    print()
    print("=" * 66)
    print("5. What a join cost")
    print("=" * 66)
    _report_the_join(root, directories, findings)

    (root / "shakedown.json").write_text(json.dumps(findings, indent=2),
                                         encoding="utf-8")
    print(f"\nWritten to {root / 'shakedown.json'}")
    return 0


def _report_the_join(root, directories, findings) -> None:
    """Volume either side of the first join, if a barostat was on.

    The barostat's adaptive move size is not in the checkpoint, so it
    restarts at its default and re-adapts. That shows up in the volume: a
    stretch of settling after the join that is not there in the run that
    went through. How long it lasts, and how far it wanders, is the number
    this whole campaign exists to get.
    """
    import csv

    def volumes(directory):
        energy = directory / "simulation" / "energy.csv"
        if not energy.is_file():
            return []
        with energy.open() as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            return []
        key = next((k for k in rows[0] if "Volume" in k), None)
        if key is None:
            return []
        return [float(r[key]) for r in rows if r.get(key)]

    before = volumes(directories[0])
    after = volumes(directories[1]) if len(directories) > 1 else []
    whole = volumes(root / "whole")
    if not (before and after and whole):
        print("   No volume column found -- constant volume, or the "
              "reporter did not write one. Nothing to measure here.")
        return

    import statistics

    settled = statistics.fmean(whole[len(whole) // 2:])
    spread = statistics.pstdev(whole[len(whole) // 2:]) or 1.0
    # How many frames after the join sit outside the band the unsplit run
    # settled into. Zero means the join cost nothing measurable.
    outside = 0
    for value in after:
        if abs(value - settled) > 2 * spread:
            outside += 1
        else:
            break
    print(f"   unsplit run settles at {settled:.0f} nm^3 (sd {spread:.1f})")
    print(f"   after the join, {outside} of {len(after)} frames sit outside "
          f"2 sd of that")
    if outside == 0:
        print("   -> the join cost nothing measurable at this size")
    else:
        print(f"   -> about {outside} frames of settling; discard that much "
              "either side when averaging volume across a join")
    findings["frames_settling_after_the_join"] = outside
    findings["frames_in_the_segment"] = len(after)


if __name__ == "__main__":
    raise SystemExit(main())
