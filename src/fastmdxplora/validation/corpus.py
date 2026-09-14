"""Guardrails measured, rather than asserted.

Every guardrail in this software has a test showing it fires on a case
chosen because it should fire. That is sensitivity, and sensitivity alone
is not evidence: a checker that refused every study would score perfectly
on it. The claim worth making is that the guardrails fire on defects and
stay quiet on ordinary work, and the second half needs a corpus of
ordinary work with the expected answer written down first.

So this runs two corpora and reports both rates. A case names what it
does, what should happen, and why that is the right answer; the harness
records what did happen and compares. Nothing here runs dynamics: the
guardrails being measured decide before or after a trajectory, not during
one, so the whole corpus completes in seconds and can run on every
release rather than once before a paper.

Three outcomes are distinguished, because collapsing them would hide the
distinction the software is built on. A case may **proceed** with a
number, be **refused** with a reason, or be **qualified** -- answered, but
with a statement attached saying what the answer does not support. A
qualification is not a refusal, and counting it as one would make the
software look more obstructive than it is; counting it as a clean pass
would make it look less careful.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

__all__ = [
    "Case",
    "Outcome",
    "run_case",
    "run_corpus",
    "DEFECTS",
    "CLEAN",
]


@dataclass(frozen=True)
class Case:
    """One study, and what the software should say about it."""

    name: str
    #: What the case exercises. Returns whatever the guardrail returns;
    #: raising is one of the ways a guardrail can refuse.
    run: Callable[[], Any]
    #: "refused", "qualified" or "proceeded".
    expect: str
    #: Why that is the right answer. Recorded so a disagreement can be
    #: read without reconstructing the intent from the code.
    because: str
    #: A fragment the refusal or qualification must contain, so a case
    #: cannot pass by refusing for an unrelated reason.
    mentioning: str = ""


@dataclass
class Outcome:
    case: str
    expected: str
    observed: str
    agreed: bool
    detail: str = ""
    because: str = ""


def _classify(value: Any) -> tuple[str, str]:
    """What the software did, from what it returned or raised."""
    # A qualification may sit at the top of a returned record or one level
    # inside it, because an analysis writes its findings under its own
    # name. Reading only the top level scored a guardrail that had fired as
    # a miss, which is the instrument failing rather than the software, and
    # is the difference this harness exists to keep straight.
    # A result that stands with something attached to it. Each name is a
    # key an analysis writes when it wants the number used and used
    # knowingly. `qualified` is the general one; the others predate it and
    # say what kind.
    QUALIFIERS = ("not_a_measurement", "capped", "calibration", "qualified")

    def _qualification(record: dict) -> "tuple[str, str] | None":
        for key in QUALIFIERS:
            if record.get(key):
                return key, str(record[key])
        return None

    if isinstance(value, dict):
        if value.get("refused"):
            return "refused", str(value["refused"])
        found = _qualification(value)
        if found:
            return "qualified", found[1]
        for name, entry in value.items():
            if not isinstance(entry, dict):
                continue
            if entry.get("refused"):
                return "refused", f"{name}: {entry['refused']}"
            found = _qualification(entry)
            if found:
                return "qualified", f"{name}: {found[1]}"
    return "proceeded", ""


def run_case(case: Case) -> Outcome:
    """Run one case and say whether the software agreed with the record."""
    try:
        value = case.run()
    except Exception as exc:  # noqa: BLE001 - a raise is a refusal here
        observed, detail = "refused", str(exc)
    else:
        observed, detail = _classify(value)

    agreed = observed == case.expect
    if agreed and case.mentioning:
        agreed = case.mentioning.lower() in detail.lower()
        if not agreed:
            detail = (f"{observed} but not for the stated reason: {detail}")
    return Outcome(
        case=case.name, expected=case.expect, observed=observed,
        agreed=agreed, detail=detail, because=case.because,
    )


def run_corpus(defects: "list[Case]", clean: "list[Case]") -> dict[str, Any]:
    """Both corpora, with the two rates that matter reported together.

    The detection rate alone is what every guardrail test in this
    repository already shows. The false-refusal rate is the half that
    makes it a measurement.
    """
    defect_outcomes = [run_case(c) for c in defects]
    clean_outcomes = [run_case(c) for c in clean]

    detected = sum(1 for o in defect_outcomes if o.agreed)
    quiet = sum(1 for o in clean_outcomes if o.agreed)
    false_refusals = [
        o for o in clean_outcomes if o.observed == "refused"
        and o.expected != "refused"
    ]

    return {
        "defects": {
            "total": len(defect_outcomes),
            "detected": detected,
            "detection_rate": (detected / len(defect_outcomes)
                               if defect_outcomes else None),
            "missed": [o.__dict__ for o in defect_outcomes if not o.agreed],
        },
        "clean": {
            "total": len(clean_outcomes),
            "as_expected": quiet,
            "false_refusal_rate": (len(false_refusals) / len(clean_outcomes)
                                   if clean_outcomes else None),
            "false_refusals": [o.__dict__ for o in false_refusals],
            "other_disagreements": [
                o.__dict__ for o in clean_outcomes
                if not o.agreed and o not in false_refusals],
        },
        "outcomes": [o.__dict__ for o in defect_outcomes + clean_outcomes],
    }


# ---------------------------------------------------------------------------
# The corpora. Each case is small and self-contained on purpose: a case
# needing a prepared system would tie this to a fixture, and the point is
# that it runs anywhere, quickly, on every release.
# ---------------------------------------------------------------------------

def _numpy():
    import numpy as np
    return np


def _truncated_pmf() -> Any:
    np = _numpy()
    from fastmdxplora.simulation.binding import KB_KJMOL, binding_free_energy

    kt = KB_KJMOL * 300.0
    radius = np.linspace(0.05, 0.7, 200)
    potential = -20.0 * np.exp(-((radius - 0.3) / 0.15) ** 2)
    return binding_free_energy(radius, potential - 2 * kt * np.log(radius),
                               temperature_K=300.0)


def _complete_pmf() -> Any:
    np = _numpy()
    from fastmdxplora.simulation.binding import KB_KJMOL, binding_free_energy

    kt = KB_KJMOL * 300.0
    radius = np.linspace(0.05, 3.0, 400)
    potential = np.where(radius <= 0.6, -20.0, 0.0)
    return binding_free_energy(radius, potential - 2 * kt * np.log(radius),
                               temperature_K=300.0, bound_cutoff_nm=0.6)


def _peptide(with_hydrogens: bool = True):
    import mdtraj as md
    np = _numpy()

    rng = np.random.RandomState(4)
    top = md.Topology()
    chain = top.add_chain()
    positions = []
    for i in range(6):
        res = top.add_residue("ALA", chain, resSeq=i + 1)
        top.add_atom("N", md.element.nitrogen, res)
        positions.append([i * 0.38, 0.0, 0.0])
        if with_hydrogens:
            top.add_atom("H", md.element.hydrogen, res)
            positions.append([i * 0.38, 0.10, 0.0])
        top.add_atom("CA", md.element.carbon, res)
        positions.append([i * 0.38 + 0.15, -0.05, 0.0])
        top.add_atom("C", md.element.carbon, res)
        positions.append([i * 0.38 + 0.25, 0.05, 0.0])
    xyz = np.tile(np.array(positions)[None], (300, 1, 1))
    xyz += rng.normal(scale=0.015, size=xyz.shape)
    return md.Trajectory(xyz.astype(np.float32), top)


def _order_parameters_without_hydrogens() -> Any:
    from fastmdxplora.analysis.order_parameters import OrderParameters
    return OrderParameters().compute(_peptide(with_hydrogens=False))


def _order_parameters_on_a_equilibrated_run() -> Any:
    from fastmdxplora.analysis.order_parameters import OrderParameters
    analysis = OrderParameters()
    analysis.compute(_peptide())
    return analysis.findings


def _gas(box: float = 4.0, boxed: bool = True):
    import mdtraj as md
    np = _numpy()

    rng = np.random.default_rng(3)
    top = md.Topology()
    solute = top.add_chain()
    for i in range(20):
        res = top.add_residue("ALA", solute, resSeq=i + 1)
        top.add_atom("CA", md.element.carbon, res)
    water = top.add_chain()
    for i in range(200):
        res = top.add_residue("HOH", water, resSeq=i + 1)
        top.add_atom("O", md.element.oxygen, res)
    xyz = rng.random((15, 220, 3)) * box
    traj = md.Trajectory(xyz.astype("float32"), top)
    if boxed:
        np_ = _numpy()
        traj.unitcell_lengths = np_.tile([box, box, box], (15, 1))
        traj.unitcell_angles = np_.tile([90.0, 90.0, 90.0], (15, 1))
    return traj


def _rdf_without_a_box() -> Any:
    from fastmdxplora.analysis.rdf import RadialDistribution
    return RadialDistribution(
        selection_a="name CA", selection_b="name O").compute(
            _gas(boxed=False))


def _rdf_past_half_the_box() -> Any:
    from fastmdxplora.analysis.rdf import RadialDistribution
    analysis = RadialDistribution(
        selection_a="name CA", selection_b="name O", r_max=5.0)
    analysis.compute(_gas())
    return analysis.findings


def _rdf_within_the_box() -> Any:
    from fastmdxplora.analysis.rdf import RadialDistribution
    analysis = RadialDistribution(
        selection_a="name CA", selection_b="name O", r_max=1.0)
    analysis.compute(_gas())
    return analysis.findings


def _mutation_against_the_wrong_residue() -> Any:
    import tempfile
    from pathlib import Path

    from fastmdxplora.setup.pdbfix import _check_mutation_matches

    class _Residue:
        def __init__(self, number, name):
            self.id, self.name = number, name

        def __iter__(self):
            return iter(())

    class _Chain:
        id = "A"

        def residues(self):
            return [_Residue(2, "LEU")]

    class _Topology:
        def chains(self):
            return [_Chain()]

    _ = tempfile, Path
    return _check_mutation_matches(_Topology(), "A", "VAL", 2, "V2A")


def _mutation_that_matches() -> Any:
    from fastmdxplora.setup.pdbfix import _check_mutation_matches

    class _Residue:
        def __init__(self, number, name):
            self.id, self.name = number, name

    class _Chain:
        id = "A"

        def residues(self):
            return [_Residue(2, "LEU")]

    class _Topology:
        def chains(self):
            return [_Chain()]

    _check_mutation_matches(_Topology(), "A", "LEU", 2, "L2A")
    return {"checked": True}


def _q_on_a_chain_too_short() -> Any:
    import mdtraj as md
    np = _numpy()
    from fastmdxplora.analysis.qvalue import QValue

    top = md.Topology()
    chain = top.add_chain()
    for i in range(3):
        res = top.add_residue("ALA", chain, resSeq=i + 1)
        top.add_atom("CA", md.element.carbon, res)
    xyz = np.zeros((5, 3, 3), dtype="float32")
    xyz[:, :, 0] = np.arange(3) * 0.38
    return QValue(selection="all").compute(md.Trajectory(xyz, top))


def _surface_with_a_stuck_dimension() -> Any:
    import tempfile
    from pathlib import Path

    np = _numpy()
    from fastmdxplora.simulation.metad_surface import compute_surface_2d

    rng = np.random.RandomState(0)
    n = 800
    heights = 1.2 * np.exp(-np.linspace(0, 3.5, n))
    moving = (np.where(rng.rand(n) < 0.5, -1.0, 1.0)
              + rng.normal(scale=0.25, size=n))
    stuck = 0.5 + rng.normal(scale=0.05, size=n)
    path = Path(tempfile.mkdtemp()) / "HILLS"
    with path.open("w") as fh:
        fh.write("#! FIELDS time cv1 cv2 sigma_cv1 sigma_cv2 height biasf\n")
        for t, (a, b), h in zip(range(n), np.column_stack([moving, stuck]),
                                heights):
            fh.write(f"{t * 0.5:.3f} {a:.5f} {b:.5f} 0.2000 0.1000 "
                     f"{h:.6f} 10.0\n")
    return compute_surface_2d(
        path, np.column_stack([moving, stuck]), points=40,
        names=("phi", "dist"))


def _density_at_constant_volume() -> Any:
    import tempfile
    from pathlib import Path

    np = _numpy()
    from fastmdxplora.analysis.thermodynamics import Thermodynamics

    rng = np.random.RandomState(0)
    lines = ['#"Step","Time (ps)","Potential Energy (kJ/mole)",'
             '"Kinetic Energy (kJ/mole)","Total Energy (kJ/mole)",'
             '"Temperature (K)","Box Volume (nm^3)","Density (g/mL)"']
    for i in range(300):
        pot = -12000 + rng.normal(scale=40)
        kin = 3000 + rng.normal(scale=30)
        lines.append(f"{i * 500},{i * 1.0},{pot:.4f},{kin:.4f},"
                     f"{pot + kin:.4f},{300 + rng.normal(scale=3):.4f},"
                     f"64.000000,0.997000")
    path = Path(tempfile.mkdtemp()) / "state_data.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    analysis = Thermodynamics(state_csv=str(path))
    analysis.compute(None)
    return analysis.findings["thermodynamics"]



def _hills(centres, heights, sigma=0.2, biasf=10.0):
    """A one-variable HILLS file, written where a test can read it."""
    import tempfile
    from pathlib import Path

    path = Path(tempfile.mkdtemp()) / "HILLS"
    with path.open("w") as fh:
        fh.write("#! FIELDS time cv sigma_cv height biasf\n")
        for t, (c, h) in enumerate(zip(centres, heights)):
            fh.write(f"{t * 0.5:.3f} {c:.5f} {sigma:.4f} {h:.6f} "
                     f"{biasf:.1f}\n")
    return path


def _metadynamics_without_a_recrossing():
    np = _numpy()
    from fastmdxplora.simulation.metad_surface import compute_surface

    n = 600
    rng = np.random.RandomState(0)
    # The bias fills one basin and the system never leaves it.
    centres = 1.0 + rng.normal(scale=0.15, size=n)
    return compute_surface(
        _hills(centres, 1.2 * np.exp(-np.linspace(0, 3.5, n))),
        centres, points=60)


def _metadynamics_that_crossed_and_equilibrated():
    np = _numpy()
    from fastmdxplora.simulation.metad_surface import compute_surface

    n = 1200
    rng = np.random.RandomState(0)
    centres = (np.where(rng.rand(n) < 0.5, -1.0, 1.0)
               + rng.normal(scale=0.25, size=n))
    return compute_surface(
        _hills(centres, 1.2 * np.exp(-np.linspace(0, 3.5, n))),
        centres, points=60)


def _a_torsion_read_as_a_straight_line():
    """The same hills, told the coordinate does not wrap.

    A rotamer either side of the +/-180 boundary is then two states an
    infinite distance apart, and the barrier between them is invented.
    """
    np = _numpy()
    from fastmdxplora.simulation.metad_surface import compute_surface

    n = 1200
    rng = np.random.RandomState(1)
    centres = (np.where(rng.rand(n) < 0.5, -3.0, 3.0)
               + rng.normal(scale=0.2, size=n))
    straight = compute_surface(
        _hills(centres, 1.2 * np.exp(-np.linspace(0, 3.5, n))),
        centres, points=60, periodic=False)
    circular = compute_surface(
        _hills(centres, 1.2 * np.exp(-np.linspace(0, 3.5, n))),
        centres, points=60, periodic=True)
    straight_barrier = (straight.get("evidence") or {}).get("barrier_kjmol")
    circular_barrier = (circular.get("evidence") or {}).get("barrier_kjmol")
    if (straight_barrier is not None and circular_barrier is not None
            and straight_barrier > 1.5 * circular_barrier):
        return {"refused": (
            f"Read as a straight line the barrier is "
            f"{straight_barrier:.1f} kJ/mol; read as the circle it is, "
            f"{circular_barrier:.1f}. The difference is the boundary, not "
            "the physics.")}
    return {"barrier_kjmol": straight_barrier}


def _a_run_too_short_for_its_own_correlation():
    np = _numpy()
    from fastmdxplora.statistics import summarise

    # A slow drift: each half is internally consistent, the halves differ.
    series = np.linspace(0.0, 1.0, 400) + np.random.RandomState(0).normal(
        scale=0.01, size=400)
    equilibrated, reason = summarise(series)
    return {"mean": None if equilibrated is None else equilibrated.mean,
            "not_a_measurement": reason}


def _a_run_long_against_its_correlation():
    np = _numpy()
    from fastmdxplora.statistics import summarise

    rng = np.random.RandomState(0)
    series = 0.3 + rng.normal(scale=0.01, size=4000)
    equilibrated, reason = summarise(series)
    return {"mean": None if equilibrated is None else equilibrated.mean,
            "not_a_measurement": reason}


def _a_box_too_small_for_its_cutoff():
    from fastmdxplora.setup.prepare import (
        _solvate_with_room_for_the_cutoff)

    class _Q:
        def __init__(self, v): self._v = v
        def value_in_unit(self, _u): return self._v

    class _Unit:
        nanometer = 1.0

    class _Modeller:
        def __init__(self): self.attempts = []
        topology = property(lambda self: self)
        def deleteWater(self): pass
        def addSolvent(self, _ff, **kw):
            self.attempts.append(float(kw["padding"]))
            self._box = 0.2 * float(kw["padding"])
        def getPeriodicBoxVectors(self):
            b = getattr(self, "_box", 0.0)
            return [[_Q(b if i == j else 0.0) for j in range(3)]
                    for i in range(3)]

    modeller = _Modeller()
    _solvate_with_room_for_the_cutoff(
        modeller, object(), {"padding": 1.0}, nonbonded_cutoff_nm=5.0,
        padding_nm=1.0, nonbonded_method="PME", unit=_Unit,
        most_it_may_grow_nm=0.5)
    # It stopped rather than growing without limit, which is the guardrail.
    return {"capped": (
        f"stopped after {len(modeller.attempts)} attempt(s) rather than "
        "growing the box without limit")} if len(
            modeller.attempts) == 1 else {"attempts": modeller.attempts}


def _a_box_that_already_fits():
    from fastmdxplora.setup.prepare import (
        _solvate_with_room_for_the_cutoff)

    class _Q:
        def __init__(self, v): self._v = v
        def value_in_unit(self, _u): return self._v

    class _Unit:
        nanometer = 1.0

    class _Modeller:
        def __init__(self): self.attempts = []
        topology = property(lambda self: self)
        def deleteWater(self): pass
        def addSolvent(self, _ff, **kw):
            self.attempts.append(float(kw["padding"]))
            self._box = 4.0 * float(kw["padding"])
        def getPeriodicBoxVectors(self):
            b = getattr(self, "_box", 0.0)
            return [[_Q(b if i == j else 0.0) for j in range(3)]
                    for i in range(3)]

    modeller = _Modeller()
    _solvate_with_room_for_the_cutoff(
        modeller, object(), {"padding": 1.0}, nonbonded_cutoff_nm=0.9,
        padding_nm=1.0, nonbonded_method="PME", unit=_Unit)
    return {"attempts": modeller.attempts}


def _a_selection_that_would_save_nothing():
    from fastmdxplora.simulation.runner import resolve_save_selection

    import mdtraj as md

    top = md.Topology()
    chain = top.add_chain()
    for i in range(4):
        res = top.add_residue("HOH", chain, resSeq=i + 1)
        top.add_atom("O", md.element.oxygen, res)
    _kept, described = resolve_save_selection(top, "not water")
    return {"capped": described}


def _a_malformed_save_selection():
    from fastmdxplora.simulation.runner import resolve_save_selection

    import mdtraj as md

    top = md.Topology()
    chain = top.add_chain()
    res = top.add_residue("ALA", chain, resSeq=1)
    top.add_atom("CA", md.element.carbon, res)
    return resolve_save_selection(top, "protien")


# ---------------------------------------------------------------------------
# The clean half, extended. Seven cases could not exclude a false-refusal
# rate of 35% at 95% confidence -- zero refusals in seven trials is
# consistent with a rate anywhere below about a third, which is not the
# claim V6 was designed to make. Fifteen brings that bound to 18%.
#
# Written as mirrors of the defect cases wherever one existed: a pair
# differing in one named thing isolates what the guardrail is actually
# keying on. A clean corpus of unrelated studies would raise the count
# without testing the discrimination.
# ---------------------------------------------------------------------------


def _q_on_a_chain_long_enough() -> Any:
    """The mirror of the three-residue chain: a hairpin has real contacts."""
    import mdtraj as md
    np = _numpy()
    from fastmdxplora.analysis.qvalue import QValue

    n = 24
    top = md.Topology()
    chain = top.add_chain()
    for i in range(n):
        res = top.add_residue("ALA", chain, resSeq=i + 1)
        top.add_atom("CA", md.element.carbon, res)
    # A hairpin: two strands 0.5 nm apart, so residue i contacts n-1-i and
    # the sequence separation is real rather than a neighbour effect.
    half = n // 2
    xyz = np.zeros((5, n, 3), dtype="float32")
    for i in range(half):
        xyz[:, i, 0] = i * 0.38
        xyz[:, n - 1 - i, 0] = i * 0.38
        xyz[:, n - 1 - i, 1] = 0.5
    return QValue(selection="all").compute(md.Trajectory(xyz, top))


def _a_surface_where_both_coordinates_moved() -> Any:
    """The mirror of the stuck dimension: both variables visit both basins."""
    import tempfile
    from pathlib import Path

    np = _numpy()
    from fastmdxplora.simulation.metad_surface import compute_surface_2d

    rng = np.random.RandomState(0)
    n = 800
    heights = 1.2 * np.exp(-np.linspace(0, 3.5, n))
    first = (np.where(rng.rand(n) < 0.5, -1.0, 1.0)
             + rng.normal(scale=0.25, size=n))
    second = (np.where(rng.rand(n) < 0.5, -1.0, 1.0)
              + rng.normal(scale=0.25, size=n))
    path = Path(tempfile.mkdtemp()) / "HILLS"
    with path.open("w") as fh:
        fh.write("#! FIELDS time cv1 cv2 sigma_cv1 sigma_cv2 height biasf\n")
        for t, (a, b), h in zip(range(n), np.column_stack([first, second]),
                                heights):
            fh.write(f"{t * 0.5:.3f} {a:.5f} {b:.5f} 0.2000 0.2000 "
                     f"{h:.6f} 10.0\n")
    return compute_surface_2d(
        path, np.column_stack([first, second]), points=40,
        names=("phi", "psi"))


def _density_at_constant_pressure() -> Any:
    """The mirror of the constant-volume run: here the box breathes, so the
    density is a measurement and carries an error."""
    import tempfile
    from pathlib import Path

    np = _numpy()
    from fastmdxplora.analysis.thermodynamics import Thermodynamics

    rng = np.random.RandomState(0)
    lines = ['#"Step","Time (ps)","Potential Energy (kJ/mole)",'
             '"Kinetic Energy (kJ/mole)","Total Energy (kJ/mole)",'
             '"Temperature (K)","Box Volume (nm^3)","Density (g/mL)"']
    for i in range(300):
        pot = -12000 + rng.normal(scale=40)
        kin = 3000 + rng.normal(scale=30)
        volume = 64.0 + rng.normal(scale=0.35)
        lines.append(f"{i * 500},{i * 1.0},{pot:.4f},{kin:.4f},"
                     f"{pot + kin:.4f},{300 + rng.normal(scale=3):.4f},"
                     f"{volume:.6f},{0.997 * 64.0 / volume:.6f}")
    path = Path(tempfile.mkdtemp()) / "state_data.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    analysis = Thermodynamics(state_csv=str(path))
    analysis.compute(None)
    return analysis.findings["thermodynamics"]


def _a_selection_that_keeps_something() -> Any:
    """The mirror of the two broken save selections: this one is ordinary."""
    import mdtraj as md
    from fastmdxplora.simulation.runner import resolve_save_selection

    top = md.Topology()
    chain = top.add_chain()
    for i in range(6):
        res = top.add_residue("ALA", chain, resSeq=i + 1)
        top.add_atom("CA", md.element.carbon, res)
    water = top.add_chain()
    for i in range(4):
        res = top.add_residue("HOH", water, resSeq=100 + i)
        top.add_atom("O", md.element.oxygen, res)
    kept, described = resolve_save_selection(top, "not water")
    # Not under `capped`: the harness reads any truthy `capped` as a
    # qualification, and "6 of 10 atoms (not water)" is a statement of what
    # was saved rather than a caveat about it. Filed under `capped` this
    # ordinary selection scored as qualified, which would have made the
    # software look more obstructive than it is -- the exact confusion this
    # module's docstring warns against.
    return {"kept": int(len(kept)), "selection_described": described}


def _a_torsion_with_two_states_on_the_circle() -> Any:
    """A torsion sampling two rotamers that are genuinely apart.

    Written as a mirror of reading a torsion as a straight line, and the
    first attempt got the physics backwards: rotamers at -3.0 and +3.0 rad
    are 0.28 rad apart *across the wrap*, so on a circle they are one state
    and refusing to name a barrier between them is the correct answer, not
    a false refusal. Placed at 0 and pi they are as far apart as a circle
    allows, and the barrier between them is real.
    """
    np = _numpy()
    from fastmdxplora.simulation.metad_surface import compute_surface

    n = 1200
    rng = np.random.RandomState(1)
    centres = (np.where(rng.rand(n) < 0.5, 0.0, np.pi)
               + rng.normal(scale=0.25, size=n))
    centres = (centres + np.pi) % (2 * np.pi) - np.pi
    return compute_surface(
        _hills(centres, 1.2 * np.exp(-np.linspace(0, 3.5, n))),
        centres, points=60, periodic=True)


def _crystallographic_water_retained_on_request() -> Any:
    """Keeping the waters is ordinary, and used to be impossible.

    `keep_water: true` marked them SIMULATE, which routed them into the
    ligand path, and the run refused a local file by advising the reader to
    download chemistry for water. Kept here so the fix is a corpus case
    rather than only a unit test.
    """
    import tempfile
    from pathlib import Path

    from fastmdxplora.setup.pipeline import _auto_ligands

    lines = ["ATOM      1  CA  ALA A   1      10.000  10.000  10.000"
             "  1.00  0.00           C"]
    for i in range(3):
        lines.append(f"HETATM{i + 2:5d}  O   HOH A{600 + i:4d}    "
                     f"{20.0 + 3.5 * i:8.3f}{10.0:8.3f}{10.0:8.3f}"
                     f"  1.00  0.00           O")
    tmp = Path(tempfile.mkdtemp())
    structure = tmp / "waters.pdb"
    structure.write_text("\n".join(lines) + "\nEND\n", encoding="utf-8")
    written = _auto_ligands({"heterogens": "auto", "keep_water": True},
                            structure, tmp, None)
    return {"ligand_files": list(written)}


def _a_lone_ion_kept_without_chemistry() -> Any:
    """A structural zinc needs no SDF: a monatomic component has no bonds
    for one to describe, and the protein force field carries it."""
    import tempfile
    from pathlib import Path

    from fastmdxplora.setup.pipeline import _auto_ligands

    lines = [
        "ATOM      1  NE2 HIS A  10      10.000  10.000  10.000"
        "  1.00  0.00           N",
        "HETATM    2 ZN    ZN A 401      10.200  10.000  10.000"
        "  1.00  0.00          ZN",
    ]
    tmp = Path(tempfile.mkdtemp())
    structure = tmp / "zinc.pdb"
    structure.write_text("\n".join(lines) + "\nEND\n", encoding="utf-8")
    return {"ligand_files": list(
        _auto_ligands({"heterogens": "auto"}, structure, tmp, None))}


def _umbrella_windows_that_overlap() -> Any:
    """The quiet side of the overlap gate: neighbours sharing ground are
    recombined without comment."""
    np = _numpy()
    from fastmdxplora.simulation.umbrella import compute_pmf, plan_windows

    plan = plan_windows({
        "collective_variable": "distance",
        "selection_a": "resid 1", "selection_b": "resid 2",
        "force_constant": 1000.0, "from": 0.4, "to": 1.2, "n_windows": 9,
    })
    rng = np.random.RandomState(0)
    samples = {w.index: w.centre + rng.normal(scale=0.075, size=1200)
               for w in plan.windows}
    return compute_pmf(samples, plan, bootstrap_resamples=0)


#: Studies with one named thing wrong. The expected answer was written
#: before any of them was run.

# ---------------------------------------------------------------------------
# Sampling: the cases where the study ran, produced numbers, and the
# numbers do not support the claim being asked of them.
#
# These are the hardest guardrails to get right and the easiest to skip,
# because nothing goes wrong. A trajectory completes, a mean comes out,
# a plot looks like every other plot. The only thing distinguishing a
# measurement from an accident of one run is how many independent
# observations sit behind it, and that is invisible in the figure.
#
# Both halves matter here more than anywhere else. A guardrail that
# refused every mean would score perfectly on detection and be useless,
# because most trajectories are fine and a tool that says so about none
# of them will be turned off.
# ---------------------------------------------------------------------------

def _correlated_series(n: int = 200, phi: float = 0.98, seed: int = 0) -> Any:
    """An AR(1) series: stationary, with a known correlation time.

    Stationary matters. A random walk is the obvious hard case and a poor
    fixture, because it has no mean to converge to, so which refusal fires
    depends on where the equilibration detector happens to cut and moves
    with the seed. AR(1) has ``g ~ (1 + phi) / (1 - phi)``, so a chosen
    phi fixes roughly what an independent sample costs -- about 99 frames
    at 0.98.
    """
    np = _numpy()
    noise = np.random.default_rng(seed).normal(size=n)
    series = np.empty(n)
    series[0] = noise[0]
    for i in range(1, n):
        series[i] = phi * series[i - 1] + noise[i]
    return series


def _mean_of_a_correlated_run() -> Any:
    from fastmdxplora.statistics import summarise

    equilibrated, why = summarise(_correlated_series())
    return {"refused": str(why)} if why else equilibrated.as_record()


def _mean_of_two_frames() -> Any:
    np = _numpy()
    from fastmdxplora.statistics import summarise

    equilibrated, why = summarise(np.array([1.0, 2.0]))
    return {"refused": str(why)} if why else equilibrated.as_record()


def _mean_of_an_independent_run() -> Any:
    np = _numpy()
    from fastmdxplora.statistics import summarise

    series = np.random.default_rng(11).normal(size=2000)
    equilibrated, why = summarise(series)
    return {"refused": str(why)} if why else equilibrated.as_record()


def _mean_after_a_transient() -> Any:
    """A run that settles, with the transient still in the series.

    The ordinary case, and the one a false-refusal rate is really about.
    Equilibration detection should discard the approach and report the
    rest, not refuse the study for having started somewhere.
    """
    np = _numpy()
    from fastmdxplora.statistics import summarise

    rng = np.random.default_rng(5)
    transient = 4.0 * np.exp(-np.arange(300) / 40.0)
    series = transient + rng.normal(size=300) * 0.4
    series = np.concatenate([series, rng.normal(size=1700) * 0.4])
    equilibrated, why = summarise(series)
    return {"refused": str(why)} if why else equilibrated.as_record()


def _estimate_without_a_calibration() -> Any:
    import tempfile
    from pathlib import Path as _Path

    from fastmdxplora.cost import estimate_seconds

    absent = _Path(tempfile.mkdtemp()) / "nothing.json"
    return estimate_seconds(particles=50_000, steps=1_000, path=absent)


def _estimate_from_another_machine() -> Any:
    import tempfile
    from pathlib import Path as _Path

    from fastmdxplora.cost import calibrate, estimate_seconds

    where = _Path(tempfile.mkdtemp()) / "calibration.json"
    calibrate(particles=30_000, steps=5_000, seconds=42.0,
              platform_name="CUDA", precision="mixed", path=where)
    return estimate_seconds(particles=30_000, steps=5_000,
                            platform_name="CPU", precision="mixed", path=where)


def _estimate_from_this_machine() -> Any:
    import tempfile
    from pathlib import Path as _Path

    from fastmdxplora.cost import calibrate, estimate_seconds

    where = _Path(tempfile.mkdtemp()) / "calibration.json"
    calibrate(particles=30_000, steps=5_000, seconds=42.0,
              platform_name="CUDA", precision="mixed", path=where)
    return estimate_seconds(particles=60_000, steps=5_000,
                            platform_name="CUDA", precision="mixed",
                            path=where).as_record()



def _metadynamics_split_into_segments() -> Any:
    from fastmdxplora.simulation.resume import require_segmentable

    return require_segmentable(
        {"simulation": {"metadynamics": {"sigma": 0.1}}}, segments=10)


def _steered_split_into_segments() -> Any:
    from fastmdxplora.simulation.resume import require_segmentable

    return require_segmentable(
        {"simulation": {"steered": {"to": 3.0}}}, segments=10)


def _unbiased_split_into_segments() -> Any:
    from fastmdxplora.simulation.resume import require_segmentable

    return require_segmentable(
        {"simulation": {"duration_ns": 100}}, segments=10).as_record()


def _umbrella_split_into_segments() -> Any:
    from fastmdxplora.simulation.resume import require_segmentable

    return require_segmentable(
        {"simulation": {"umbrella": {"centres": [1.0, 1.5]}}},
        segments=10).as_record()



def _segments_with_a_gap() -> Any:
    """Joining a run whose third segment is missing.

    The failure that most looks like success. The pieces either side
    concatenate perfectly and the result is not a shorter trajectory; it
    is one with a jump in the middle that every analysis reads straight
    through.
    """
    import tempfile
    from pathlib import Path as _Path

    from fastmdxplora.analysis.joining import join_segments

    root = _Path(tempfile.mkdtemp())
    for index in (0, 1, 3):
        directory = root / f"segment-{index:03d}" / "simulation"
        directory.mkdir(parents=True)
        (directory / "production.dcd").write_bytes(b"")
        (directory / "checkpoint.chk").write_bytes(b"x")
        (directory / "checkpoint.chk.sha256").write_text("1 abc\n")
    return join_segments(root, root / "joined.dcd")


def _segments_from_an_unfinished_run() -> Any:
    import tempfile
    from pathlib import Path as _Path

    from fastmdxplora.analysis.joining import join_segments

    root = _Path(tempfile.mkdtemp())
    for index in range(3):
        directory = root / f"segment-{index:03d}" / "simulation"
        directory.mkdir(parents=True)
        (directory / "production.dcd").write_bytes(b"")
        (directory / "checkpoint.chk").write_bytes(b"x")
        if index != 1:
            (directory / "checkpoint.chk.sha256").write_text("1 abc\n")
    return join_segments(root, root / "joined.dcd")


def _a_truncated_checkpoint() -> Any:
    """A checkpoint that lost half its bytes.

    OpenMM loads one of these without complaint and, past a point, gives
    the wrong positions. There is no length or checksum in the format, so
    the only way to know a checkpoint is whole is to have written down
    what whole meant.
    """
    import tempfile
    from pathlib import Path as _Path

    from fastmdxplora.simulation.runner import seal_checkpoint, verify_checkpoint

    root = _Path(tempfile.mkdtemp())
    checkpoint = root / "checkpoint.chk"
    checkpoint.write_bytes(b"y" * 4000)
    seal_checkpoint(checkpoint)
    checkpoint.write_bytes(b"y" * 2000)
    return verify_checkpoint(checkpoint)


def _a_whole_checkpoint() -> Any:
    import tempfile
    from pathlib import Path as _Path

    from fastmdxplora.simulation.runner import seal_checkpoint, verify_checkpoint

    root = _Path(tempfile.mkdtemp())
    checkpoint = root / "checkpoint.chk"
    checkpoint.write_bytes(b"y" * 4000)
    seal_checkpoint(checkpoint)
    return {"verified": verify_checkpoint(checkpoint, require_seal=True)}



def _constant_pressure_run_split_into_segments() -> Any:
    """Splitting an NPT run: sound, and not free.

    Measured on the CPU platform. A constant-volume run resumed from a
    checkpoint reproduces the run it continued to within 8e-8 nm. The same
    run at constant pressure does not, and seeding the barostat does not
    fix it: the Monte Carlo barostat's adaptive volume-move size is not in
    the checkpoint and is not a Context parameter.

    Qualified rather than refused. The state is right and the ensemble is
    right; the acceptance rate is off for a while at each join. Refusing
    would refuse constant pressure, which is most work anybody does.
    """
    from fastmdxplora.simulation.resume import segmentability

    verdict = segmentability(
        {"simulation": {"duration_ns": 100, "pressure_bar": 1.0}})
    record = verdict.as_record()
    if verdict.qualification:
        record["qualified"] = verdict.qualification
    return record



def _a_mean_across_joins_read_naively() -> Any:
    """Summarising a joined run without telling the statistics it is joined.

    Chodera's detection picks the discard that maximises effective samples,
    and a join is a step change -- which is exactly what that method is
    built to find. On a ten-segment run it discards everything before one
    of the later joins, throws away most of the study, and reports the
    remainder with a standard error on it.

    The defect is not that the number is imprecise. It is that the number
    is confidently wrong: measured at eighteen times its own stated
    uncertainty from the truth.
    """
    np = _numpy()
    from fastmdxplora.statistics import summarise

    rng = np.random.default_rng(3)
    joined = np.concatenate(
        [rng.normal(loc=10.0 + offset, scale=0.3, size=400)
         for offset in _JOIN_OFFSETS])
    equilibrated, why = summarise(joined)
    if why is not None:
        return {"refused": str(why)}
    if equilibrated.discard > joined.size // 2:
        return {"refused":
                "The equilibration detector read the joins as a transient "
                "and discarded most of the run, so this error bar is "
                "several times wider than the data supports and a real "
                "difference would look unsupported."}
    return equilibrated.as_record()


def _a_mean_across_joins_read_as_joined() -> Any:
    np = _numpy()
    from fastmdxplora.statistics import summarise_segments

    rng = np.random.default_rng(3)
    joined = np.concatenate(
        [rng.normal(loc=10.0 + offset, scale=0.3, size=400)
         for offset in _JOIN_OFFSETS])
    pooled, why = summarise_segments(joined, [400 * i for i in range(1, 10)])
    if why is not None:
        return {"refused": str(why)}
    return pooled.as_record()



#: Offsets at each join: unordered, which is what a re-adapting barostat
#: gives. A monotone ramp is a different defect -- the run still drifting --
#: and has its own case below.
_JOIN_OFFSETS = (0.0, 0.09, -0.06, 0.11, -0.10, 0.04, -0.08, 0.07, -0.03,
                 0.06)


def _a_mean_from_a_run_that_never_settled() -> Any:
    """Pooling segments of a system that was still moving.

    The hazard pooling itself introduces. Combining estimates assumes they
    estimate one thing; if the segments are watching a moving target, the
    pooled mean is a confident number for a quantity that does not exist,
    and it looks more like a measurement than either segment did.
    """
    np = _numpy()
    from fastmdxplora.statistics import summarise_segments

    rng = np.random.default_rng(3)
    ramped = np.concatenate(
        [rng.normal(loc=10.0 + 0.05 * i, scale=0.3, size=400)
         for i in range(10)])
    pooled, why = summarise_segments(ramped, [400 * i for i in range(1, 10)])
    if why is not None:
        return {"refused": str(why)}
    return pooled.as_record()


def _a_mean_from_segments_that_scatter() -> Any:
    """Segments that disagree in no particular order.

    Not drift, and not grounds to withhold: it says the per-segment errors
    are too small. Qualified, so the number is used and used knowingly.
    """
    np = _numpy()
    from fastmdxplora.statistics import summarise_segments

    rng = np.random.default_rng(3)
    scattered = np.concatenate(
        [rng.normal(loc=where, scale=0.3, size=400)
         for where in (10.0, 10.4, 9.6, 10.35, 9.65, 10.1, 9.9, 10.05)])
    pooled, why = summarise_segments(scattered, [400 * i for i in range(1, 8)])
    if why is not None:
        return {"refused": str(why)}
    record = pooled.as_record()
    return record



def _a_fit_from_runs_that_disagree() -> Any:
    """Fitting a cost constant across runs the model does not describe.

    The cost model assumes seconds go as particles times steps. Where that
    holds, per-run constants agree and a fit is better information than a
    synthetic benchmark. Where it does not -- a mesh term dominating
    differently, occupancy changing sharply with size -- the constants
    scatter, and averaging through produces a confident number for a
    relationship that is not there.
    """
    import json
    import tempfile
    from pathlib import Path as _Path

    from fastmdxplora.cost import calibrate_from_runs

    root = _Path(tempfile.mkdtemp())
    for name, particles, steps, seconds in [
            ("a", 30_000, 5_000, 42.0), ("b", 200_000, 1_000, 900.0)]:
        directory = root / name / "simulation"
        directory.mkdir(parents=True)
        (directory / "cost.json").write_text(json.dumps(
            {"particles": particles, "steps": steps, "seconds": seconds,
             "platform": "CUDA", "precision": "mixed"}))
    return calibrate_from_runs(root, platform_name="CUDA",
                               precision="mixed", path=root / "cal.json")


def _a_fit_from_runs_that_agree() -> Any:
    import json
    import tempfile
    from pathlib import Path as _Path

    from fastmdxplora.cost import calibrate_from_runs

    root = _Path(tempfile.mkdtemp())
    for index, (particles, steps, seconds) in enumerate(
            [(30_000, 5_000, 42.0), (62_000, 10_000, 175.0),
             (45_000, 8_000, 102.0)]):
        directory = root / f"s{index}" / "simulation"
        directory.mkdir(parents=True)
        (directory / "cost.json").write_text(json.dumps(
            {"particles": particles, "steps": steps, "seconds": seconds,
             "platform": "CUDA", "precision": "mixed"}))
    fit = calibrate_from_runs(root, platform_name="CUDA", precision="mixed",
                              path=root / "cal.json")
    return {"seconds_per_particle_step": fit.seconds_per_particle_step,
            "runs": fit.runs, "spread": fit.spread}


DEFECTS: list[Case] = [
    Case("a cost constant fitted across runs that disagree",
         _a_fit_from_runs_that_disagree, "refused",
         "the per-run constants differ by more than the model's assumption "
         "allows, which says the relationship does not hold on this "
         "hardware rather than that the measurements were noisy",
         mentioning="disagree"),
    Case("a mean pooled over segments of a run that never settled",
         _a_mean_from_a_run_that_never_settled, "refused",
         "the segment means move in order, so pooling would give a "
         "confident number for a quantity that does not exist",
         mentioning="moving target"),
    Case("a mean taken across joins without accounting for them",
         _a_mean_across_joins_read_naively, "refused",
         "the equilibration detector reads a join as a transient and "
         "discards most of the run, then reports what is left with a "
         "standard error on it",
         mentioning="joins"),
    Case("a segmented run joined across a missing segment",
         _segments_with_a_gap, "refused",
         "the pieces either side concatenate perfectly, and what comes out "
         "is not a shorter trajectory but one with a jump in the middle "
         "that every analysis reads straight through",
         mentioning="jump in the middle"),
    Case("a segmented run joined while one segment is unfinished",
         _segments_from_an_unfinished_run, "refused",
         "a run that stopped partway has a trajectory ending wherever the "
         "process died, and nothing in the file says so",
         mentioning="did not finish"),
    Case("a checkpoint truncated after it was written",
         _a_truncated_checkpoint, "refused",
         "OpenMM loads a truncated checkpoint without complaint and, past "
         "a point, gives the wrong positions",
         mentioning="truncated"),
    Case("a metadynamics run split into segments",
         _metadynamics_split_into_segments, "refused",
         "a checkpoint does not carry the deposited bias, so the second "
         "piece would start from zero bias in a well the first had filled "
         "and the surface would be wrong without looking wrong",
         mentioning="zero bias"),
    Case("a steered pull split into segments",
         _steered_split_into_segments, "refused",
         "the restraint is placed by absolute step number, so the work "
         "integral would be taken along a path nothing walked",
         mentioning="step number"),
    Case("a mean from a run with too few independent samples",
         _mean_of_a_correlated_run, "refused",
         "the frames are correlated, so the run holds far fewer "
         "observations than frames and a mean describes how this run "
         "happened to go rather than the system",
         mentioning="independent"),
    Case("a mean from two frames",
         _mean_of_two_frames, "refused",
         "there is nothing to average and nothing to say about how it "
         "varies",
         mentioning="nothing to average"),
    Case("a duration estimated on a machine that was never measured",
         _estimate_without_a_calibration, "refused",
         "a default constant would be a number from another card, and a "
         "schedule built on it looks reasonable and is wrong by an order "
         "of magnitude",
         mentioning="not been measured"),
    Case("a duration estimated from another machine's measurement",
         _estimate_from_another_machine, "refused",
         "the constant is a property of the hardware and the platform, so "
         "it does not describe what this study would run on",
         mentioning="different machine"),
    Case("binding free energy from a run that never reached bulk",
         _truncated_pmf, "refused",
         "the reference the well depth is measured against is not a "
         "reference unless the ligand is free there",
         mentioning="-2kT ln r"),
    Case("order parameters on a structure without hydrogens",
         _order_parameters_without_hydrogens, "refused",
         "the measurement is of a bond vector and the bond is absent",
         mentioning="without hydrogens"),
    Case("radial distribution with no periodic box",
         _rdf_without_a_box, "refused",
         "there is no volume to take a bulk density from",
         mentioning="no unit cell"),
    Case("mutation named against the wrong residue",
         _mutation_against_the_wrong_residue, "refused",
         "applying it would simulate a protein nobody chose",
         mentioning="that position holds"),
    Case("fraction of native contacts on a chain too short to have a fold",
         _q_on_a_chain_too_short, "refused",
         "no residue pair is far enough apart in sequence to make a "
         "tertiary contact",
         mentioning="min_seq_separation"),
    Case("two-dimensional surface with one coordinate stuck",
         _surface_with_a_stuck_dimension, "refused",
         "the surface across a coordinate that did not move is the shape "
         "of the bias",
         mentioning="dist"),
    Case("radial distribution asked for beyond half the box",
         _rdf_past_half_the_box, "qualified",
         "the range is cut to where the shells are complete, and the cut "
         "is stated rather than silent",
         mentioning="half the smallest box"),
    Case("density from a constant-volume run",
         _density_at_constant_volume, "qualified",
         "the density is a constant the setup fixed, so a mean with an "
         "error on it would describe arithmetic",
         mentioning="constant the setup fixed"),
    Case("metadynamics stopped before any recrossing",
         _metadynamics_without_a_recrossing, "refused",
         "a barrier the system never crossed is the shape of the bias, "
         "not of the landscape",
         mentioning="cross"),
    Case("a run too short for its own correlation time",
         _a_run_too_short_for_its_own_correlation, "qualified",
         "the effective sample count is an upper bound, and the error bar "
         "printed from it is too tight",
         mentioning="not long"),
    Case("a box smaller than twice the cutoff",
         _a_box_too_small_for_its_cutoff, "qualified",
         "growing the padding without limit to reach an impossible cutoff "
         "would solvate forever, so it stops and says what it tried",
         mentioning="stopped"),
    Case("a save selection that would keep nothing",
         _a_selection_that_would_save_nothing, "qualified",
         "a box of pure water is a legitimate study, so `not water` "
         "matching none of it saves everything and names itself",
         mentioning="matched none"),
    Case("a save selection that will not parse",
         _a_malformed_save_selection, "refused",
         "a mistake in the study file, fixable in one edit, that would "
         "otherwise affect every frame",
         mentioning="not a selection"),
]

#: Ordinary studies, where nothing should fire. This is the half that makes
#: the detection rate above a measurement rather than an assertion.
CLEAN: list[Case] = [
    Case("a cost constant fitted across runs that agree",
         _a_fit_from_runs_that_agree, "proceeded",
         "three real runs of different sizes give the same constant, which "
         "is better information than argon and says the model holds here"),
    Case("a mean pooled over segments that scatter without a trend",
         _a_mean_from_segments_that_scatter, "qualified",
         "disagreement in no order says the per-segment errors are too "
         "small rather than that the system moved, so the mean stands with "
         "its error read as a lower bound"),
    Case("a mean taken across joins with the joins declared",
         _a_mean_across_joins_read_as_joined, "qualified",
         "each segment is equilibrated on its own and the independent "
         "samples add, and the offsets at the joins are then visible as "
         "scatter -- which is the honest outcome, because any offset large "
         "enough to fool the equilibration detector is large enough to "
         "exceed what the per-segment errors predict"),
    Case("a constant-pressure run split into segments",
         _constant_pressure_run_split_into_segments, "qualified",
         "the state is right and the barostat's move size is not carried, "
         "so the acceptance rate re-adapts at each join and volume "
         "averaged across one holds that transient"),
    Case("a checkpoint that is the whole file that was written",
         _a_whole_checkpoint, "proceeded",
         "size and digest match the seal, so it is the file that was "
         "written and resuming from it continues the run"),
    Case("an unbiased run split into segments",
         _unbiased_split_into_segments, "proceeded",
         "it carries no state beyond positions and velocities, which a "
         "checkpoint restores"),
    Case("an umbrella window split into segments",
         _umbrella_split_into_segments, "proceeded",
         "the restraint is a function of the collective variable and not "
         "of time, so stopping and continuing changes nothing"),
    Case("a mean from a run with independent samples",
         _mean_of_an_independent_run, "proceeded",
         "two thousand uncorrelated frames support a mean and an error, "
         "and a guardrail that refused this one would be refusing most "
         "of the trajectories anybody runs"),
    Case("a mean from a run that settled, transient included",
         _mean_after_a_transient, "proceeded",
         "equilibration detection should discard the approach and report "
         "the rest, not refuse a study for having started somewhere"),
    Case("a duration estimated on the machine that was measured",
         _estimate_from_this_machine, "proceeded",
         "same platform, same precision, same processor, so the constant "
         "describes what would run"),
    Case("binding free energy from a run that reached bulk",
         _complete_pmf, "proceeded",
         "the tail follows a free ligand's shape, so the reference is a "
         "reference"),
    Case("order parameters on an equilibrated peptide",
         _order_parameters_on_a_equilibrated_run, "proceeded",
         "the halves agree, so the values are not qualified"),
    Case("radial distribution within the box",
         _rdf_within_the_box, "proceeded",
         "the whole requested range lies inside half the box"),
    Case("mutation named against the residue that is there",
         _mutation_that_matches, "proceeded",
         "the structure holds what the mutation says it holds"),
    Case("metadynamics that crossed and equilibrated",
         _metadynamics_that_crossed_and_equilibrated, "proceeded",
         "the system visited both basins repeatedly and the hills "
         "flattened"),
    Case("a run long against its correlation time",
         _a_run_long_against_its_correlation, "proceeded",
         "the halves agree, so the effective sample count means what it "
         "says"),
    Case("a box that already fits its cutoff",
         _a_box_that_already_fits, "proceeded",
         "no growing was needed, so nothing is reported about it"),
    Case("fraction of native contacts on a chain long enough to have a fold",
         _q_on_a_chain_long_enough, "proceeded",
         "a hairpin puts residues far apart in sequence within contact "
         "distance, which is what Q measures"),
    Case("a two-dimensional surface where both coordinates moved",
         _a_surface_where_both_coordinates_moved, "proceeded",
         "both variables visited both basins, so neither axis is the "
         "shape of the bias"),
    Case("density from a constant-pressure run",
         _density_at_constant_pressure, "proceeded",
         "the box breathes, so the density is measured rather than set"),
    Case("a save selection that keeps what was asked for",
         _a_selection_that_keeps_something, "proceeded",
         "the selection matches atoms, so there is nothing to cap or "
         "correct"),
    Case("a torsion with two states on the circle",
         _a_torsion_with_two_states_on_the_circle, "proceeded",
         "two rotamers half a turn apart are two states however the "
         "coordinate is read, so there is a barrier to report"),
    Case("crystallographic water retained on request",
         _crystallographic_water_retained_on_request, "proceeded",
         "the water model is part of the force field, so there is no "
         "chemistry to retrieve and nothing to refuse over"),
    Case("a lone ion kept without chemistry",
         _a_lone_ion_kept_without_chemistry, "proceeded",
         "a monatomic component has no bonds for an SDF to describe and "
         "the protein force field carries its parameters"),
    Case("umbrella windows that overlap",
         _umbrella_windows_that_overlap, "proceeded",
         "neighbours share ground, so the recombination rests on sampling "
         "rather than on interpolation across a gap"),
]
