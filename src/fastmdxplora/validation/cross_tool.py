#!/usr/bin/env python3
"""Compare a FastMDXplora run against independent reference implementations.

Moved here from the scratch directory it was written in. Ten rounds of
hardening against real cluster output are institutional knowledge with
nowhere to live: each round was an interoperability fact discovered the
expensive way, and a script that only exists on one laptop cannot carry
them into the next release. What it learned, and why each lesson is in the
code rather than in somebody's memory:

  * cluster-absolute paths in a manifest do not resolve on the machine that
    reads it, so artifacts are re-rooted before the conventional layout is
    tried as a fallback, and the fallback announces itself;
  * the interaction table is `.dat`, its comments start with `#`, and its
    numbers may be in scientific notation, so header detection cannot lean
    on any of the three;
  * stored frames carry molecules split across the periodic boundary, and a
    reference tool reading raw coordinates sees a different system: healing
    with template bonds is required, and distance-guessed bonds cannot do
    it because they cannot bridge the gap they are asked to heal;
  * before healing, the two tools appeared to agree at several residues,
    which was two errors cancelling.

Requires ProLIF and MDAnalysis, which are not dependencies of the package:
this measures agreement with tools the software does not ship.

This is the executable half of the paper's Results 6: the same trajectory,
read by tools that share no code with FastMDXplora, with the deltas
published whatever they are. Three subcommands:

  interactions RUN_DIR   ProLIF fingerprint vs pl_interactions occupancies
  observables  RUN_DIR   MDAnalysis RMSD/Rg vs the run's own rmsd/rg tables
  negative     RUN_DIR   both tools must agree the cavity is quiet

Artifacts are discovered through the run's own manifest.json wherever
possible -- the provenance index is the contract, not guessed filenames.
Where a name assumption remains, it is printed before use so the first
cluster-side failure is legible rather than mysterious.

Pre-registered tolerances (decided before any comparison was run; see
BENCHMARK_PROTOCOL.md for the argument):

  HARMONIZED_OCCUPANCY_TOL_PP = 5.0   percentage points, per residue-kind,
                                      when geometric criteria are harmonized
  OBSERVABLE_TOL_NM           = 1e-3  max |delta| for RMSD and Rg traces
  NEGATIVE_OCCUPANCY_MAX_PCT  = 5.0   any cavity residue, any kind, both tools

ProLIF-at-its-defaults is also reported, WITHOUT a tolerance: those
differences are findings about criterion choices in the ecosystem, not
failures of either tool.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from fastmdxplora.refusals import StudyError
from fastmdxplora.refusals import BackendUnavailable

HARMONIZED_OCCUPANCY_TOL_PP = 5.0
OBSERVABLE_TOL_NM = 1e-3
NEGATIVE_OCCUPANCY_MAX_PCT = 5.0

# ProLIF interaction class -> the comparable FastMDXplora kind family.
# Water bridges are deliberately absent: ProLIF's require explicit
# water-mediated setup and are compared qualitatively in the protocol,
# not numerically here.
PROLIF_TO_FAMILY = {
    "Hydrophobic": "hydrophobic",
    "HBDonor": "hbond",
    "HBAcceptor": "hbond",
    "Cationic": "salt_bridge",
    "Anionic": "salt_bridge",
    "PiStacking": "pi_stacking",
    "FaceToFace": "pi_stacking",
    "EdgeToFace": "pi_stacking",
    "XBDonor": "halogen",
    "XBAcceptor": "halogen",
    "MetalDonor": "metal",
    "MetalAcceptor": "metal",
}

# FastMDXplora's kind strings normalized into the same families by substring, so the
# comparison does not depend on exact spelling of e.g.
# pi_stacking_face_to_face vs pi_stacking.

def _reference_tools():
    """The tools this measures agreement against, or how to get them.

    Deliberately not dependencies of the package. The point of a
    cross-tool comparison is that the reference shares no code with what
    it checks, and shipping it would make the two travel together and
    drift together. Imported here so the module loads without them and
    only the comparison itself asks.
    """
    try:
        import MDAnalysis as mda
        import prolif as plf
    except ImportError as exc:  # pragma: no cover - depends on the env
        raise BackendUnavailable(
            "The cross-tool comparison needs MDAnalysis and ProLIF, which "
            "are not dependencies of FastMDXplora: they are the independent "
            "implementations it measures agreement against. Install them "
            "with `pip install \"fastmdxplora[validation]\"` or `conda "
            "install -c conda-forge mdanalysis prolif`."
        , code="environment.backend.missing") from exc
    return mda, plf

def our_kind_family(kind: str) -> str | None:
    k = kind.lower()
    for needle, family in (("hbond", "hbond"), ("hydrogen", "hbond"),
                           ("salt", "salt_bridge"), ("ionic", "salt_bridge"),
                           ("stack", "pi_stacking"), ("halogen", "halogen"),
                           ("metal", "metal"), ("hydrophob", "hydrophobic")):
        if needle in k:
            return family
    return None  # water bridges and anything novel: excluded, listed once


# Harmonized geometric criteria. The source of truth for FastMDXplora's criteria is
# tests/test_what_holds_the_ligand_is_measured.py, where every published
# threshold is pinned as a test. CONFIRM these against that file before
# trusting a harmonized run; they are parameters to ProLIF's classes.
HARMONIZED_PARAMETERS = {
    # FastMDXplora's native hydrophobic rule is 0.40 nm (4.0 A). Keep the
    # independent tool on the same geometric cutoff for the judged table.
    "Hydrophobic": {"distance": 4.0},
    "Cationic": {"distance": 4.5},
    "Anionic": {"distance": 4.5},
}


RES_RE = __import__("re").compile(r"([A-Z]{2,3})\s*(\d+)")

def residue_label(raw: str) -> str:
    """ASP189 out of whatever decoration a tool wears -- 'ASP 189 A',
    'ASP189.A', 'ASP189-OD1' -- so the two tools' tables can join. The
    protocol predicted the one-sided-rows failure this prevents."""
    m = RES_RE.search(str(raw).upper())
    return f"{m.group(1)}{m.group(2)}" if m else str(raw).strip()


def load_manifest(run_dir: Path) -> dict:
    p = run_dir / "manifest.json"
    if not p.exists():
        sys.exit(f"no manifest.json under {run_dir} -- is this a run directory?")
    return json.loads(p.read_text())


def find_artifact(run_dir: Path, manifest: dict, *needles: str) -> Path | None:
    """Find one artifact whose recorded path contains all needles.

    A manifest written on a cluster records that machine's absolute paths,
    which exist nowhere on the laptop the run directory was ferried to --
    the first real run failed discovery for exactly this. So an absolute
    path is re-rooted under the local run directory by everything after
    the run directory's own name in it, and a relative one is tried both
    as given and re-rooted the same way.
    """
    def walk(node):
        if isinstance(node, str):
            yield node
        elif isinstance(node, dict):
            for v in node.values():
                yield from walk(v)
        elif isinstance(node, list):
            for v in node:
                yield from walk(v)

    def candidates(recorded: str):
        p = Path(recorded)
        if not p.is_absolute():
            yield run_dir / p
        else:
            yield p
        if run_dir.name in p.parts:
            i = len(p.parts) - 1 - p.parts[::-1].index(run_dir.name)
            yield run_dir.joinpath(*p.parts[i + 1:])

    for rec in walk(manifest):
        low = rec.lower()
        if all(n in low for n in needles):
            for cand in candidates(rec):
                if cand.exists():
                    return cand
    return None


def trajectory_and_topology(run_dir: Path, manifest: dict) -> tuple[Path, Path]:
    # A run whose trajectory holds a subset writes the matching topology
    # beside it; taking the prepared system's instead is an atom-count
    # mismatch, and this reads runs ferried from a cluster where that
    # failure arrives far from its cause.
    # The trajectory holds the solvated system, so the topology must be the
    # solvated one -- topology.pdb -- or MDAnalysis refuses on atom count.
    traj = (find_artifact(run_dir, manifest, ".dcd")
            or find_artifact(run_dir, manifest, "trajectory"))
    top = (find_artifact(run_dir, manifest, "topology", ".pdb")
           or find_artifact(run_dir, manifest, "solvated", ".pdb"))
    # The run tree's own layout is the fallback the manifest cannot break.
    if not traj:
        conventional = run_dir / "simulation" / "production.dcd"
        if conventional.exists():
            traj = conventional
            print("[fallback] trajectory taken from the conventional layout")
    if not top:
        # The topology beside the trajectory first, then the prepared
        # system's. A run whose `save_selection` left the solvent out
        # writes trajectory_topology.pdb, and reading its trajectory
        # against setup's topology is an atom-count mismatch that
        # MDAnalysis refuses -- far from its cause, on a run ferried from
        # a cluster.
        for conventional, note in (
            (run_dir / "simulation" / "trajectory_topology.pdb",
             "topology taken from the one saved with the trajectory"),
            (run_dir / "setup" / "topology.pdb",
             "topology taken from the conventional layout"),
        ):
            if conventional.exists():
                top = conventional
                print(f"[fallback] {note}")
                break
    if not traj or not top:
        sys.exit(f"could not locate trajectory/topology "
                 f"(traj={traj}, top={top}); inspect manifest.json")
    print(f"[discovered] trajectory: {traj.name}   topology: {top.name}")
    return traj, top


def _configured_ligand_resname(run_dir: Path) -> str | None:
    """The ligand this run actually prepared, or None.

    The setup record is asked first, and it is the only one that answers
    the question being asked. `setup.ligand_name` has a default -- "LIG" --
    so once `resolved_config.yml` names every setting the run used, every
    apo protein's config carries it too. Reading the config alone would
    hand back "LIG" for a run with no ligand in it, and the comparison
    would go looking for a residue that is not there.

    `setup_parameters.json` records a ligand only where one was
    parameterised, which is exactly the distinction the config cannot make
    between a value chosen and a value defaulted.
    """
    import json

    setup = run_dir / "setup" / "setup_parameters.json"
    try:
        data = json.loads(setup.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    ligand = (data.get("resolved_forcefield") or {}).get("ligand")
    if ligand and ligand.get("name"):
        return str(ligand["name"])

    # Older runs, and runs whose setup phase did not write a record, fall
    # back to the config -- but only where it says something a default
    # would not have said.
    cfg = run_dir / "resolved_config.yml"
    if cfg.exists():
        import yaml

        from fastmdxplora.config.schema import PHASE_SCHEMAS

        config = yaml.safe_load(cfg.read_text()) or {}
        name = (config.get("setup") or {}).get("ligand_name")
        field = PHASE_SCHEMAS["setup"].get("ligand_name")
        defaulted = field is not None and name == field.default
        if name and not defaulted:
            return str(name)

    return None


def ligand_resname(run_dir: Path) -> str:
    name = _configured_ligand_resname(run_dir)
    if name:
        return name
    sys.exit("ligand_name not found in resolved_config.yml; pass --ligand")


def topology_has_resname(topology: Path, resname: str) -> bool:
    """Return whether a PDB topology contains the requested residue name."""
    target = str(resname).upper()
    for line in topology.read_text(errors="replace").splitlines():
        if line.startswith(("ATOM  ", "HETATM")):
            if line[17:20].strip().upper() == target:
                return True
    return False


def unmeasured_interaction_families(run_dir: Path) -> set[str]:
    """Read interaction kinds that native analysis explicitly refused.

    A refused kind is not a zero-occupancy measurement. It must be excluded
    from a cross-tool numeric comparison until both tools measure the same
    claim.
    """
    options = run_dir / "analysis" / "pl_interactions" / "options.json"
    if not options.is_file():
        return set()
    try:
        data = json.loads(options.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    refused = ((data.get("findings") or {}).get("not_measured") or {})
    families = set()
    for kind in refused:
        families.add(our_kind_family(kind) or str(kind))
    return families


def healed_trajectory(run_dir: Path, traj: Path, top: Path) -> Path:  # pragma: no cover - needs ProLIF/MDAnalysis and a finished run
    """Make molecules whole once, with bonds that come from chemistry.

    The stored frames carry the protein split across the periodic
    boundary. MDAnalysis's unwrap cannot heal it: its bonds were guessed
    from distances, and no guesser bridges the 60 A gap that is the very
    thing needing healing. mdtraj builds protein bonds from residue
    templates -- which is why this package's own analyses were immune --
    so it heals here, once, cached beside the run.
    """
    healed = run_dir / "benchmark_healed.dcd"
    if not healed.exists():
        import mdtraj as md

        from fastmdxplora.analysis.loading import image_whole
        print("[healing] imaging molecules whole via mdtraj "
              "(one-time, cached)...")
        t = md.load(str(traj), top=str(top))
        image_whole(t, inplace=True)
        t.save_dcd(str(healed))
    else:
        print(f"[healing] using cached {healed.name}")
    return healed


class _ReimageLigand:
    """Move the ligand to the protein's triclinic minimum-image copy."""

    def __init__(self, ligand, protein):
        self.ligand = ligand
        self.protein = protein

    def __call__(self, ts):
        import numpy as np
        from MDAnalysis.lib.mdamath import triclinic_vectors

        vectors = triclinic_vectors(ts.dimensions)
        if not np.any(vectors):
            return ts
        ligand_center = self.ligand.positions.mean(axis=0)
        protein_center = self.protein.positions.mean(axis=0)
        delta = ligand_center - protein_center
        fractional = np.linalg.solve(vectors.T, delta)
        minimum_image = (fractional - np.rint(fractional)) @ vectors
        self.ligand.positions += minimum_image - delta
        return ts


def prolif_occupancy(traj: Path, top: Path, resname: str,
                     harmonized: bool) -> dict[tuple[str, str], float]:  # pragma: no cover - needs ProLIF/MDAnalysis and a finished run
    mda, plf = _reference_tools()
    # The OpenMM topology PDB carries no CONECT records, and ProLIF's
    # RDKit conversion needs bonds -- but only on the protein and ligand.
    # Guessing over the whole solvated system asks for vdW radii the
    # guesser does not have for Na and Cl (round three died there) and
    # spends its time on fifteen thousand waters nobody will fingerprint.
    # So bonds are guessed per selection. AtomGroups, not Molecules, go to
    # run(): a Molecule snapshots frame-0 coordinates, an AtomGroup
    # follows the trajectory.
    u = mda.Universe(str(top), str(traj))
    lig = u.select_atoms(f"resname {resname}")
    prot = u.select_atoms("protein")
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        prot.guess_bonds()
        lig.guess_bonds()
    if len(lig) == 0:
        sys.exit(f"no atoms with resname {resname} in the topology")
    # Molecule healing makes each molecule whole, but it does not guarantee
    # that the ligand and protein are in the same periodic image. Native
    # FastMDXplora distances use the minimum image; apply the same triclinic
    # reimaging before ProLIF sees the AtomGroups.
    u.trajectory.add_transformations(_ReimageLigand(lig, prot))
    kinds = sorted(set(PROLIF_TO_FAMILY))
    params = HARMONIZED_PARAMETERS if harmonized else {}
    fp = plf.Fingerprint(
        interactions=[k for k in kinds if k in plf.Fingerprint.list_available()],
        parameters={k: v for k, v in params.items()})
    fp.run(u.trajectory, lig, prot)
    df = fp.to_dataframe()
    n_frames = len(df)
    # Family merge is boolean-or per frame: a frame counts for "hbond" if
    # HBDonor OR HBAcceptor fired; summing member columns would overcount
    # frames where both did.
    import collections
    fam_cols = collections.defaultdict(list)
    for col in df.columns:
        _, res, inter = col
        family = PROLIF_TO_FAMILY.get(inter)
        if family:
            fam_cols[(residue_label(res), family)].append(col)
    occ: dict[tuple[str, str], float] = {}
    for key, cols in fam_cols.items():
        fired = df[cols].any(axis=1)
        occ[key] = 100.0 * fired.sum() / n_frames
    print(f"[prolif] {n_frames} frames, {len(occ)} residue-family pairs "
          f"({'harmonized' if harmonized else 'ProLIF defaults'})")
    return occ


def our_occupancy(run_dir: Path, manifest: dict) -> dict[tuple[str, str], float]:
    """Read pl_interactions output and reduce to per-(residue, family)
    occupancy. Tries the structured JSON first, then the contacts table."""
    adir = run_dir / "analysis" / "pl_interactions"
    candidates = []
    # The exact residue table first, where the run wrote one. It holds the
    # union of each residue's atom pairs' frames, which is the quantity
    # being compared; the pair table beside it can only bound that union
    # from both sides, and a bracket against another tool's point is a
    # comparison of different things. Alphabetically the pair table sorts
    # first, so the order is stated rather than left to the glob.
    exact = adir / "pl_interactions_by_residue.dat"
    if exact.is_file():
        candidates.append(exact)
    if adir.is_dir():
        candidates += sorted(adir.glob("*.json")) + sorted(adir.glob("*.dat")) \
                    + sorted(adir.glob("*.csv"))
    for suffix in (".json", ".dat", ".csv"):
        via = find_artifact(run_dir, manifest, "pl_interactions", suffix)
        if via and via not in candidates:
            candidates.append(via)
    for path in candidates:
        if path.name == "options.json":
            continue
        print(f"[trying] {path.relative_to(run_dir)}")
        if path.suffix == ".json":
            occ = _occupancy_from_records(json.loads(path.read_text()))
        elif path.name == "pl_interactions_by_residue.dat":
            occ = _occupancy_from_residue_table(path)
        else:
            occ = _occupancy_from_csv(path)
        if occ:
            return occ
    listing = sorted(p.name for p in adir.iterdir()) if adir.is_dir() else "no directory"
    sys.exit(f"no readable pl_interactions occupancy; analysis dir holds: {listing}")


def _occupancy_from_residue_table(path: Path) -> dict[tuple[str, str], float]:
    """Per-residue occupancy as a number, from the exact union table.

    One row per residue and interaction kind, carrying the frames in which
    any of that residue's atom pairs was in contact. No bracket: the union
    was taken while the per-frame masks were still in hand, which is the
    only place it can be taken at all.
    """
    import csv as _csv

    out: dict[tuple[str, str], float] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in _csv.DictReader(handle):
            family = our_kind_family(row.get("kind") or "")
            residue = residue_label(row.get("residue") or "")
            if not family or not residue:
                continue
            try:
                out[(residue, family)] = 100.0 * float(row["occupancy"])
            except (KeyError, TypeError, ValueError):
                continue
    if out:
        print(f"[fastmdx] exact residue table: {len(out)} residue-family "
              "occupancies, taken as the union of each residue's pairs")
    return out


def _occupancy_from_records(data) -> dict[tuple[str, str], float]:
    """Handle the plausible shapes: a dict with per-contact records under a
    key, or a list of records with frame/kind/protein_atom-or-residue."""
    records = None
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        for key in ("contacts", "records", "interactions", "results"):
            if isinstance(data.get(key), list):
                records = data[key]
                break
    if not records:
        return {}
    frames = set()
    hits: dict[tuple[str, str], set] = {}
    for r in records:
        if not isinstance(r, dict) or "frame" not in r:
            return {}
        frames.add(r["frame"])
        fam = our_kind_family(str(r.get("kind", "")))
        res = residue_label(r.get("residue") or r.get("protein_residue")
                            or r.get("protein_atom", ""))
        if fam and res:
            hits.setdefault((res, fam), set()).add(r["frame"])
    n = len(frames)
    if n == 0:
        return {}
    print(f"[fastmdx] {n} frames with recorded contacts")
    return {k: 100.0 * len(v) / n for k, v in hits.items()}


def _occupancy_from_csv(path: Path) -> dict[tuple[str, str], float]:
    import csv
    frames, hits = set(), {}
    with open(path) as fh:
        first = fh.readline()
        fh.seek(0)
        if "," in first:
            reader = csv.DictReader(fh)
        else:
            # Whitespace-delimited table: split it ourselves into dicts.
            header = first.split()
            reader = (dict(zip(header, line.split()))
                      for line in fh.readlines()[1:] if line.strip())
        rows = list(reader)
        if rows and "occupancy" in rows[0] and "frame" not in rows[0]:
            # The run's pl_interactions.dat is already aggregated: one row
            # per (kind, atom pair) carrying frames_present/frames_total
            # and occupancy. Reading it as per-frame records collapsed
            # everything onto one phantom frame and printed 100.0 for
            # every pair that existed at all -- the first deltas table's
            # entire fastmdx-side column. Per residue and family this takes the
            # MAX over atom pairs: the union of pair frame-sets is not
            # recoverable from this table, so this is a lower bound on
            # the residue-level occupancy, exact wherever one pair
            # dominates -- which the hbond rows show it usually does.
            lo: dict[tuple[str, str], float] = {}
            by_patom: dict[tuple[str, str], dict[str, float]] = {}
            for row in rows:
                fam = our_kind_family(row.get("kind") or "")
                res = residue_label(row.get("residue")
                                    or row.get("protein_atom") or "")
                if fam and res:
                    pct = 100.0 * float(row["occupancy"])
                    key = (res, fam)
                    lo[key] = max(lo.get(key, 0.0), pct)
                    # Ceiling v2: pairs sharing a protein atom are the
                    # ligand's ring carbons touching it in the same
                    # frames -- summing them convicted a passing negative
                    # control at 24.8% when the union was ~3% (LEU118,
                    # 50 episodes over 62 frames). Same-atom pairs take
                    # their max; distinct protein atoms, which can fire
                    # in different frames, sum.
                    pa = str(row.get("protein_atom", "?"))
                    g = by_patom.setdefault(key, {})
                    g[pa] = max(g.get(pa, 0.0), pct)
            occ = {k: (lo[k], min(100.0, sum(by_patom[k].values())))
                   for k in lo}
            print(f"[fastmdx] aggregated table: {len(rows)} pair rows -> "
                  f"{len(occ)} residue-family brackets "
                  f"[max-over-pairs, capped-sum]")
            return occ
        for row in rows:
            frames.add(row.get("frame"))
            fam = our_kind_family(row.get("kind") or "")
            res = residue_label(row.get("residue")
                                or row.get("protein_residue")
                                or row.get("protein_atom") or "")
            if fam and res:
                hits.setdefault((res, fam), set()).add(row.get("frame"))
    n = len(frames)
    if n == 0:
        sys.exit(f"{path.name}: no frames parsed")
    occ = {k: 100.0 * len(v) / n for k, v in hits.items()}
    print(f"[fastmdx] {n} frames, {len(occ)} residue-family pairs; "
          f"sample keys: {sorted(occ)[:4]}")
    return occ


def cmd_interactions(args):  # pragma: no cover - needs ProLIF/MDAnalysis and a finished run
    run_dir = Path(args.run_dir)
    manifest = load_manifest(run_dir)
    traj, top = trajectory_and_topology(run_dir, manifest)
    traj = healed_trajectory(run_dir, traj, top)
    resname = args.ligand or ligand_resname(run_dir)
    ours = our_occupancy(run_dir, manifest)
    excluded = unmeasured_interaction_families(run_dir)
    if excluded:
        print("[excluded] native interaction families not measured: "
              f"{', '.join(sorted(excluded))}")
    for harmonized in ((True, False) if args.both else (args.harmonized,)):
        ref = prolif_occupancy(traj, top, resname, harmonized)
        label = "harmonized" if harmonized else "defaults"
        rows, worst = [], 0.0
        for key in sorted(set(ours) | set(ref)):
            if key[1] in excluded:
                continue
            got = ours.get(key, (0.0, 0.0))
            a_lo, a_hi = got if isinstance(got, tuple) else (got, got)
            b = ref.get(key, 0.0)
            # Zero inside the bracket; outside, the signed distance to the
            # nearer bound. The table cannot say where in [lo, hi] the true
            # residue-level occupancy sits, so agreement anywhere inside is
            # agreement, and only escaping the interval is a delta.
            d = 0.0 if a_lo <= b <= a_hi else \
                (a_lo - b if b < a_lo else a_hi - b)
            worst = max(worst, abs(d))
            rows.append((key[0], key[1], a_lo, a_hi, b, d))
        out = run_dir / f"benchmark_interactions_{label}.csv"
        with open(out, "w") as fh:
            fh.write("residue,family,ours_lo_pct,ours_hi_pct,"
                     "prolif_pct,delta_pp\n")
            for r in rows:
                fh.write(f"{r[0]},{r[1]},{r[2]:.1f},{r[3]:.1f},"
                         f"{r[4]:.1f},{r[5]:+.1f}\n")
        print(f"[written] {out}")
        if harmonized:
            verdict = ("WITHIN" if worst <= HARMONIZED_OCCUPANCY_TOL_PP
                       else "EXCEEDS")
            print(f"[verdict] worst |delta| = {worst:.1f} pp -> {verdict} "
                  f"the pre-registered {HARMONIZED_OCCUPANCY_TOL_PP} pp")
        else:
            print(f"[report] worst |delta| at ProLIF defaults = {worst:.1f} pp "
                  f"(reported, not judged: criterion differences are findings)")


def cmd_observables(args):  # pragma: no cover - needs ProLIF/MDAnalysis and a finished run
    run_dir = Path(args.run_dir)
    manifest = load_manifest(run_dir)
    traj, top = trajectory_and_topology(run_dir, manifest)
    mda, _ = _reference_tools()
    import numpy as np
    from MDAnalysis.analysis import rms
    for name in ("rmsd", "rg"):
        opt = run_dir / "analysis" / name / "options.json"
        if opt.exists():
            print(f"[options] {name}: {opt.read_text().strip()}")
    u = mda.Universe(str(top), str(traj))
    ref = mda.Universe(str(top), str(traj))
    ref.trajectory[0]
    R = rms.RMSD(u, ref, select="name CA").run()
    rmsd_nm = R.results.rmsd[:, 2] / 10.0  # A -> nm to match our units
    rg_sel = "protein"
    opt = run_dir / "analysis" / "rg" / "options.json"
    if opt.exists():
        rec = json.loads(opt.read_text()).get("selection")
        if rec:
            rg_sel = rec
            print(f"[rg] using the run's recorded selection: {rg_sel!r}")
    rg_group = u.select_atoms(rg_sel)
    rg_nm = np.array([rg_group.radius_of_gyration() / 10.0
                      for _ in u.trajectory])
    for name, mine in (("rmsd", rmsd_nm), ("rg", rg_nm)):
        art = run_dir / "analysis" / name / f"{name}.dat"
        if not art.exists():
            art = (find_artifact(run_dir, manifest, name, ".dat")
                   or find_artifact(run_dir, manifest, name, ".csv"))
        if not art:
            print(f"[skip] no {name} artifact found")
            continue
        print(f"[discovered] {name}: {art}")
        theirs = _numeric_column(art, prefer=name)
        n = min(len(mine), len(theirs))
        worst = float(abs(mine[:n] - theirs[:n]).max())
        verdict = "WITHIN" if worst <= OBSERVABLE_TOL_NM else "EXCEEDS"
        print(f"[{name}] frames={n} max|delta|={worst:.2e} nm -> {verdict} "
              f"{OBSERVABLE_TOL_NM} nm")


def _numeric_column(path: Path, prefer: str):
    """Header-aware table read. The run's .dat headers are '#'-prefixed,
    which genfromtxt eats as a comment -- promoting the first data row to
    column names and dropping a frame, which is how round three printed a
    number as a column name. So the header is read by hand."""
    import numpy as np
    lines = [l for l in open(path) if l.strip()]

    # The delimiter is read off a data row, never off the header. Sniffing
    # the first line broke the moment `save_data` began writing a `#` note
    # naming the layout: that note contains a comma, so a whitespace file
    # was split on commas, `names` came back wider than the array, and the
    # column lookup raised IndexError -- for `sasa` but not `ligand_rmsd`,
    # whose preferred name happened to match the first token. A message
    # that must never contain a comma is a trap for whoever edits it next.
    body_lines = [l for l in lines if not l.lstrip().startswith("#")]
    if not body_lines:
        raise StudyError(f"{path} holds no data rows", code="analysis.data.absent")
    delim = "," if "," in body_lines[0] else None

    # A `#` line describes the file; it is not the column header. Where one
    # is present the header, if any, is the first row that does not parse
    # as numbers.
    first = body_lines[0]
    header = first.lstrip("#").strip()
    tokens = ([t.strip() for t in header.split(",")] if delim
              else header.split())
    def _floats(ts):
        try:
            [float(t) for t in ts]
            return True
        except ValueError:
            return False
    # A header is a line that does not parse as numbers. "Contains a
    # letter" was the previous test, and scientific notation's `e` passed
    # it -- promoting a headerless file's first row to column names.
    has_header = not _floats(tokens)
    names = tokens if has_header else [f"col{i}" for i in range(len(tokens))]
    body = body_lines[1:] if has_header else body_lines
    data = np.array([[float(v) for v in
                      (l.split(",") if delim else l.split())]
                     for l in body])
    col = next((n for n in names if prefer in n.lower()),
               next((n for n in names if "nm" in n.lower()), names[-1]))
    print(f"[column] {path.name}: using '{col}' of {names}, "
          f"{len(body)} rows")
    return data[:, names.index(col)]


def cavity_contacts(ours: dict, ref: dict, cavity) -> list[tuple[str, str, float, str]]:
    """The contacts that fail a negative control: a cavity residue held above
    NEGATIVE_OCCUPANCY_MAX_PCT, by either tool.

    FastMDXplora is judged on the FLOOR: the measured max-pair occupancy. The
    ceiling once convicted a passing control on the summed flicker of
    correlated ring-carbon grazes; a graze-union artifact must not fail a
    negative. Its own name for the judgment, apart from the command that
    needs ProLIF and a finished run, so the rule can be run where they are
    not.
    """
    judged = [(res, fam, (v[0] if isinstance(v, tuple) else v), "fastmdx")
              for (res, fam), v in ours.items()]
    judged += [(res, fam, v, "prolif") for (res, fam), v in ref.items()]
    bad = []
    for res, fam, pct, tool in judged:
        resnum = "".join(ch for ch in res if ch.isdigit())
        if resnum in cavity and pct > NEGATIVE_OCCUPANCY_MAX_PCT:
            bad.append((res, fam, pct, tool))
    return bad


def cmd_negative(args):  # pragma: no cover - needs ProLIF/MDAnalysis and a finished run
    run_dir = Path(args.run_dir)
    manifest = load_manifest(run_dir)
    cavity = {c.strip() for c in args.cavity.split(",")}
    traj, top = trajectory_and_topology(run_dir, manifest)
    resname = args.ligand or _configured_ligand_resname(run_dir)
    if not resname:
        sys.exit("ligand_name not found in resolved_config.yml; pass --ligand")

    # An explicitly ligand-free negative control has no valid native
    # pl_interactions table and cannot be sent through the ligand-contact
    # comparison path. Its preregistered claim is that the ligand was removed;
    # verify that claim directly from the topology instead of manufacturing a
    # zero interaction table.
    if not topology_has_resname(top, resname):
        evidence = run_dir / "benchmark_negative_control.json"
        evidence.write_text(json.dumps({
            "ligand_resname": resname,
            "present_in_topology": False,
            "cavity_residues": sorted(cavity, key=int),
            "status": "ligand-free control verified",
        }, indent=2) + "\n", encoding="utf-8")
        print(f"[control] no {resname} residue in trajectory topology")
        print(f"[written] {evidence}")
        print("[verdict] negative control holds by explicit ligand removal; "
              "ligand-contact occupancy is not applicable")
        return

    ours = our_occupancy(run_dir, manifest)
    traj = healed_trajectory(run_dir, traj, top)
    ref = prolif_occupancy(traj, top, resname, harmonized=True)
    # Both bounds and both tools are printed either way; the verdict is
    # cavity_contacts's, which judges FastMDXplora on its floor.
    print("cavity report (fastmdx [lo, hi] | prolif):")
    for resnum in sorted(cavity, key=int):
        for (res, fam), v in sorted(ours.items()):
            if "".join(c for c in res if c.isdigit()) == resnum:
                a_lo, a_hi = v if isinstance(v, tuple) else (v, v)
                b = ref.get((res, fam), 0.0)
                print(f"  {res:>7} {fam:<12} fastmdx [{a_lo:4.1f}, "
                      f"{a_hi:5.1f}] | prolif {b:4.1f}")
    bad = cavity_contacts(ours, ref, cavity)
    if bad:
        print("[verdict] NEGATIVE CONTROL FAILED -- persistent cavity "
              "contacts found:")
        for res, fam, pct, tool in bad:
            print(f"  {res} {fam}: {pct:.1f}% ({tool})")
        sys.exit(1)
    print(f"[verdict] negative control holds in BOTH tools: no cavity "
          f"residue ({', '.join(sorted(cavity))}) exceeds "
          f"{NEGATIVE_OCCUPANCY_MAX_PCT}% in any interaction family")


def cmd_probe(args):  # pragma: no cover - needs ProLIF/MDAnalysis and a finished run
    """Dump both tools' evidence for one residue: FastMDXplora's pair rows from the
    aggregated table, and ProLIF's per-frame detections over a sample of
    frames. Built for VAL213, useful for any row that escapes its bracket."""
    run_dir = Path(args.run_dir)
    manifest = load_manifest(run_dir)
    target = residue_label(args.residue)
    print(f"=== fastmdx: pair rows for {target} ===")
    dat = run_dir / "analysis" / "pl_interactions" / "pl_interactions.dat"
    pair = None
    for line in open(dat):
        if target in residue_label(line.split(",")[-1]) or target in line:
            print("  " + line.rstrip())
            if pair is None and not line.startswith("kind"):
                f = line.split(",")
                pair = (int(f[1]), int(f[2]))
    traj, top = trajectory_and_topology(run_dir, manifest)
    traj = healed_trajectory(run_dir, traj, top)
    resname = args.ligand or ligand_resname(run_dir)
    mda, plf = _reference_tools()
    u = mda.Universe(str(top), str(traj))
    lig = u.select_atoms(f"resname {resname}")
    prot = u.select_atoms("protein")
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        prot.guess_bonds(); lig.guess_bonds()
    fp = plf.Fingerprint(interactions=["Hydrophobic"], count=True)
    step = max(1, len(u.trajectory) // args.frames)
    fp.run(u.trajectory[::step], lig, prot, progress=False)
    print(f"=== prolif: Hydrophobic detections near {target} over "
          f"{args.frames} sampled frames ===")
    seen = 0
    for i, frame_ifp in enumerate(fp.ifp.values()):
        for (l, p), det in frame_ifp.items():
            if residue_label(str(p)) == target:
                print(f"  frame#{i}: {l} -- {p}: {det}")
                seen += 1
    print(f"=== prolif residue keys near {target} (from this run) ===")
    keys = set()
    for frame_ifp in fp.ifp.values():
        for (_, p) in frame_ifp:
            keys.add(str(p))
    tgt_num = int("".join(c for c in target if c.isdigit()))
    near_keys = sorted(k for k in keys
                       if any(str(n) in k for n in range(tgt_num - 4,
                                                          tgt_num + 5)))
    print(f"  {near_keys or 'no keys in the 210-216 window at all'}")
    print(f"=== single-residue generate: ligand vs {target} alone ===")
    lone = prot.select_atoms(f"resid {tgt_num}")
    try:
        res = fp.generate(plf.Molecule.from_mda(lig),
                          plf.Molecule.from_mda(lone))
        print(f"  {dict(res) or 'EMPTY -- the lone residue does not fire'}")
    except Exception as e:
        print(f"  ERROR {type(e).__name__}: {e}")
    if not seen:
        print("  none in ProLIF's trajectory run. Geometry of the FastMDXplora top pair:")
    if pair:
        import numpy as np
        from MDAnalysis.lib.distances import calc_bonds
        u.trajectory[0]
        a, b = u.atoms[pair[0]], u.atoms[pair[1]]
        raw = float(np.linalg.norm(a.position - b.position))
        mi = float(calc_bonds(a.position[None, :], b.position[None, :],
                              box=u.dimensions)[0])
        print(f"  fastmdx pair: lig atom {pair[0]} ({a.name} "
              f"{a.resname}{a.resid}) -- prot atom {pair[1]} "
              f"({b.name} {b.resname}{b.resid})")
        print(f"  frame 0: raw distance {raw:.2f} A | "
              f"minimum-image distance {mi:.2f} A | "
              f"box {u.dimensions[:3].round(1)}")
        if raw > 2 * mi:
            print("  VERDICT: the pair is split across the periodic "
                  "boundary in the stored coordinates. The FastMDXplora analysis "
                  "measures minimum-image; a tool reading raw Cartesian "
                  "positions sees a different neighbourhood.")


def main():  # pragma: no cover - needs ProLIF/MDAnalysis and a finished run
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("interactions")
    p.add_argument("run_dir")
    p.add_argument("--ligand")
    p.add_argument("--harmonized", action="store_true")
    p.add_argument("--both", action="store_true",
                   help="run defaults AND harmonized, write both tables")
    p.set_defaults(func=cmd_interactions)
    p = sub.add_parser("observables")
    p.add_argument("run_dir")
    p.set_defaults(func=cmd_observables)
    p = sub.add_parser("probe")
    p.add_argument("run_dir")
    p.add_argument("--residue", required=True)
    p.add_argument("--ligand")
    p.add_argument("--frames", type=int, default=50)
    p.set_defaults(func=cmd_probe)
    p = sub.add_parser("negative")
    p.add_argument("run_dir")
    p.add_argument("--cavity", default="99,102,111,118",
                   help="comma-separated residue numbers (default: T4 L99A)")
    p.add_argument("--ligand")
    p.set_defaults(func=cmd_negative)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
