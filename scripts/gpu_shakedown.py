"""Answer the three things only a real card can answer.

Everything about cost and segmentation in FastMDXplora has been checked on
a CPU with twenty argon atoms and a tri-alanine peptide. That was enough to
find real bugs -- a `resume_from` that validated and was ignored, a
checkpoint truncation OpenMM does not catch -- and it is not enough to
trust on a solvated protein at production size.

Three questions, one campaign:

**Does the cost model hold here?** It assumes seconds go as particles times
steps. Real runs of different sizes should agree about the constant to
within a few tens of per cent. Every run here is one system at one size,
differing only in step count, so a tight fit confirms that cost is linear
in steps and says nothing about the particle half of the model: that needs
a second, clearly different-sized system. `fit.spread` near 1 means it holds; past 3
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
        --nvt-steps 200 --npt-steps 200 --trajectory-interval 100 \
        --state-interval 100

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
    p.add_argument("--state-interval", type=int, default=None,
                   help="Steps between rows of the energy record, which is "
                        "where the volume across a join is read (default: "
                        "the study's own, 1,000). A segment shorter than "
                        "this writes no rows at all.")
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
                   help="Barostat pressure. 0 runs production at constant "
                        "volume, after the NPT equilibration has set the "
                        "density: the control for question three.")
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
    if args.state_interval:
        config["simulation"]["state_interval_steps"] = args.state_interval
    if args.pressure_bar:
        config["simulation"]["pressure_bar"] = args.pressure_bar
    else:
        # Said, not left to the default. Leaving the pressure out gave the
        # default of 1 bar, and with NPT equilibration steps production
        # stayed at constant pressure: the constant-volume control ran the
        # same ensemble as the run it was the control for.
        config["simulation"]["ensemble"] = "nvt"
    return config


class StudyFailed(Exception):
    """A study the shakedown ran did not finish: which phase, and why."""

    def __init__(self, phase: str, why: str, output: Path):
        super().__init__(f"{phase}: {why}")
        self.phase, self.why, self.output = phase, why, output


def run(config, output):
    """Run a study and return its wall time, or raise StudyFailed.

    What explore() returned was thrown away, so a study whose phases failed
    printed its minutes like any other and the script went on, measuring
    runs that never happened and explaining the missing results as physics.
    """
    from fastmdxplora import FastMDXplora

    started = time.time()
    results = FastMDXplora(config_data=config, output_dir=str(output)).explore()
    seconds = time.time() - started
    for result in results or []:
        for phase in getattr(result, "phases", []) or []:
            if getattr(phase, "status", "") == "error":
                raise StudyFailed(phase.name, phase.message or result.message, Path(output))
        if getattr(result, "status", "") == "error":
            raise StudyFailed("the study", result.message, Path(output))
    return seconds


def main(argv=None) -> int:
    args = parse(argv)
    from fastmdxplora.cost import measure_this_machine
    from fastmdxplora.refusals import StudyError, refusal_of
    from fastmdxplora.simulation.resume import segmentability

    root = Path(args.output)
    # Before anything is measured. A killed run leaves results behind, the
    # runner refuses to overwrite them -- correctly -- and without this the
    # refusal arrives after the machine has been benchmarked and a study
    # started. Found by killing a run twice.
    if root.exists() and any(root.iterdir()):
        print(f"{root} already holds something. Remove it or choose "
              "another --output; this needs an empty directory so the runs "
              "it compares are all its own.")
        return 1
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

    try:
        return _run_the_studies(args, root, findings)
    except (StudyFailed, StudyError) as failed:
        # Stopped at the first failure: everything after it would measure
        # runs that did not happen.
        phase = getattr(failed, "phase", "the study")
        why = getattr(failed, "why", None) or refusal_of(failed).message
        where = getattr(failed, "output", root)
        print(f"\n   FAILED in {phase}: {why[:400]}")
        print(f"   The run's own log: {Path(where) / 'fastmdxplora.log'}")
        print("   Stopped here; nothing after this would be a measurement.")
        findings["failed"] = {"phase": phase, "why": why, "output": str(where)}
        (root / "shakedown.json").write_text(json.dumps(findings, indent=2),
                                             encoding="utf-8")
        return 1


def _run_the_studies(args, root, findings) -> int:
    from fastmdxplora.cost import calibrate_from_runs
    from fastmdxplora.refusals import StudyError, refusal_of
    from fastmdxplora.simulation.resume import plan_segments

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
        if piece.index > 0:
            if not log.is_file():
                # Said rather than passed over: a segment with no log is
                # one nothing can be said about, not one that resumed.
                print(f"   segment {piece.index}: wrote no log, so whether it "
                      "resumed is unknown")
            elif "Resumed from" in log.read_text(encoding="utf-8", errors="replace"):
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
        # As a ratio, with its consequence. "Out by 62%" hid which way and
        # how far: argon at 0.38 of the measured constant is an estimate
        # 2.6 times too low. Computed before the f-string, which cannot
        # hold a line break inside a replacement field before Python 3.12.
        ratio = findings["argon_k"] / fit.seconds_per_particle_step
        print(f"   argon predicted {ratio:.2f}x the cost these runs measured")
        if ratio < 0.8:
            print(f"   an estimate from argon alone would be {1 / ratio:.1f} times too low")
        elif ratio > 1.25:
            print(f"   an estimate from argon alone would be {ratio:.1f} times too high")
        findings["argon_over_fit"] = ratio
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
    """Volume either side of every join, if a barostat was on.

    The barostat's adaptive move size is not in the checkpoint, so it
    restarts at its default and re-adapts. That shows up in the volume: a
    stretch of settling after the join that is not there in the run that
    went through. How long it lasts, and how far it wanders, is the number
    this whole campaign exists to get. Each segment is measured against the
    one before it: four segments are three joins, and reading only the first
    reported a third of what was run.
    """
    import csv
    import statistics

    def volumes(directory):
        """What the energy record holds: ("absent", None) where there is
        none, ("empty", None) where it has no rows, ("constant volume", the
        volumes or None) where the volume never changes or is not recorded,
        and ("ok", the volumes) otherwise. Different findings, and reading an
        empty record as constant volume blamed the physics for a reporting
        interval. The reporter writes a volume column at constant volume too,
        holding one value; its spread of zero was replaced by 1.0 and printed
        as a standard deviation."""
        energy = directory / "simulation" / "energy.csv"
        if not energy.is_file():
            return "absent", None
        with energy.open() as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            return "empty", None
        key = next((k for k in rows[0] if "Volume" in k), None)
        if key is None:
            return "constant volume", None
        values = [float(r[key]) for r in rows if r.get(key)]
        if len(set(values)) == 1:
            return "constant volume", values
        return "ok", values

    def particles(directory):
        import json as _json

        record = directory / "setup" / "setup_parameters.json"
        try:
            return int(_json.loads(record.read_text())["n_atoms_solvated"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    if len(directories) < 2:
        print("   One segment only, so there is no join to measure.")
        return
    joins = findings.setdefault("joins", [])
    for number, (earlier, later) in enumerate(zip(directories, directories[1:]), start=1):
        name = f"join {number} ({earlier.name} to {later.name})"
        (said_before, before), (said_after, after) = volumes(earlier), volumes(later)
        records = {earlier.name: said_before, later.name: said_after}
        absent = [d for d, said in records.items() if said == "absent"]
        empty = [d for d, said in records.items() if said == "empty"]
        if absent:
            # Not "constant volume": a run that wrote no energy record did
            # not get as far as production, which is a different finding.
            print(f"   {name}: no energy record in {', '.join(absent)}: that run did "
                  "not reach production. Nothing to measure here.")
            joins.append({"join": number, "not_measured": "no energy record"})
            continue
        if empty:
            print(f"   {name}: the energy record of {', '.join(empty)} has no rows: "
                  "its production is shorter than the state interval. Give the "
                  "segments more production steps, or a shorter --state-interval.")
            joins.append({"join": number, "not_measured": "no energy rows"})
            continue
        if "constant volume" in records.values():
            if before and after and before[-1] != after[0]:
                # Held constant either side and different across the join:
                # the continuation did not start from the box it was given.
                print(f"   {name}: the box changed across the join, from "
                      f"{before[-1]:.4f} to {after[0]:.4f} nm^3, with the volume "
                      "held constant on each side. The segment did not continue "
                      "from the box it was given.")
                joins.append({"join": number, "box_changed": [before[-1], after[0]]})
                continue
            held = f", held at {before[-1]:.3f} nm^3 on both sides" if before else ""
            print(f"   {name}: production at constant volume{held}. Nothing moves "
                  "across the join to measure.")
            joins.append({"join": number, "not_measured": "constant volume"})
            continue
        if len(after) < 5:
            print(f"   {name}: only {len(after)} frame(s) after the join. Too few to "
                  "say anything; give the segments more production steps or a "
                  "shorter --state-interval.")
            joins.append({"join": number, "frames_after_the_join": len(after)})
            continue
        # Against the segment before, not against the unsplit run.
        #
        # The first version compared the segment after the join with the
        # unsplit run, and the two are independently solvated: 13,241 atoms
        # against 13,406 in one rehearsal, about 1.2% more water, which at
        # 132 nm^3 is three standard deviations of volume before any barostat
        # has done anything. It reported nine frames of settling that were
        # mostly a different amount of water.
        #
        # Consecutive segments are the same system -- one solvation, one
        # topology, a checkpoint between them -- so the only thing that can
        # move the volume across the join is the barostat re-adapting. A
        # later segment has no setup of its own -- it runs with
        # `include: ["simulation"]` and `setup_from` pointing at segment 0 --
        # so the absence of its setup record is the evidence that the two
        # share a solvation, not evidence that they differ.
        first, second = particles(earlier), particles(later)
        if first is not None and second is not None and first != second:
            print(f"   {name}: the segments were solvated separately ({first:,} "
                  f"atoms against {second:,}), so a volume difference across the "
                  "join is not a transient. Nothing comparable here.")
            joins.append({"join": number, "segments_share_a_solvation": False})
            continue
        if first is not None:
            findings["particles"] = first
        settled = statistics.fmean(before[len(before) // 2:])
        spread = statistics.pstdev(before[len(before) // 2:]) or 1.0
        outside = 0
        for value in after:
            if abs(value - settled) > 2 * spread:
                outside += 1
            else:
                break
        print(f"   {name}: {earlier.name} settles at {settled:.1f} nm^3 (sd {spread:.2f}); "
              f"the first {outside} of {len(after)} frames after the join sit "
              "outside 2 sd of that")
        if outside == 0:
            print("     -> the join cost nothing measurable at this size")
        else:
            print(f"     -> about {outside} frames of settling; discard that much "
                  "after a join when averaging volume across one")
        joins.append({"join": number, "settled_nm3": settled, "sd_nm3": spread,
                      "frames_settling": outside, "frames_in_the_segment": len(after)})


if __name__ == "__main__":
    raise SystemExit(main())
