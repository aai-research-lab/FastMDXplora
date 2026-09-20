"""Enhanced-sampling runs the pipeline has to be able to complete.

The structure corpus beside this asks whether a molecule was built correctly.
This asks the same question of a method: given a config, does a run produce
the result the method exists for -- a free energy, a surface, a work curve --
drawn and recorded rather than left as a file nobody reads.

What is asserted is mechanics, not physics. That umbrella sampling recovers
a minimum at 0.738 nm against an unstrained 0.73, and that metadynamics puts
its minimum in the beta region at +2.57 rad, was established by hand against
known chemistry; those runs took an hour each and their agreement is recorded
in the project's history rather than re-derived here. What can be checked in
minutes is that windows overlap, that hills deposit and shrink, that work
accumulates, and that each method's result reaches a figure.

Runs are cut to the shortest that still produce a result. A suite nobody runs
because it takes an hour catches nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The tripeptide every case uses: Ala-Gly-Ala, fourteen atoms.
#:
#: Small enough that five umbrella windows finish in three minutes, and short
#: enough that several analyses correctly do not apply -- which is the half
#: of the gating that eleven well-behaved proteins cannot test. `qvalue`
#: measures the fraction of native tertiary contacts retained, and a
#: tripeptide has no fold; it was reported as a failed analysis until a real
#: run on this system showed it.
TRIPEPTIDE = """\
ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N
ATOM      2  CA  ALA A   1       1.458   0.000   0.000  1.00  0.00           C
ATOM      3  C   ALA A   1       2.009   1.420   0.000  1.00  0.00           C
ATOM      4  O   ALA A   1       1.251   2.390   0.000  1.00  0.00           O
ATOM      5  CB  ALA A   1       1.988  -0.773  -1.199  1.00  0.00           C
ATOM      6  N   GLY A   2       3.332   1.549   0.000  1.00  0.00           N
ATOM      7  CA  GLY A   2       3.972   2.849   0.000  1.00  0.00           C
ATOM      8  C   GLY A   2       5.486   2.705   0.000  1.00  0.00           C
ATOM      9  O   GLY A   2       6.008   1.593   0.000  1.00  0.00           O
ATOM     10  N   ALA A   3       6.191   3.820   0.000  1.00  0.00           N
ATOM     11  CA  ALA A   3       7.645   3.844   0.000  1.00  0.00           C
ATOM     12  C   ALA A   3       8.212   5.255   0.000  1.00  0.00           C
ATOM     13  O   ALA A   3       7.454   6.225   0.000  1.00  0.00           O
ATOM     14  CB  ALA A   3       8.175   3.071  -1.199  1.00  0.00           C
TER
END
"""

#: Settings every case shares, and why each is what it is.
#:
#: `solvent_padding_nm: 1.2` because 0.8 gave a box the minimum-image check
#: refused on a solute this small. `npt_steps: 0` because a barostat needs
#: enough water for pressure fluctuations to average out, and a tripeptide in
#: 1.45 nm has a couple of hundred molecules -- the box collapsed below twice
#: the cutoff. Neither is a limitation of the software; both are what a
#: fourteen-atom solute means.
COMMON_SETUP: dict[str, Any] = {
    "solvent_padding_nm": 1.2,
    "ion_concentration_M": 0.0,
}
COMMON_SIMULATION: dict[str, Any] = {
    "nvt_steps": 1000,
    "npt_steps": 0,
    "telemetry_interval": 500,
}

#: The end-to-end distance, as MDTraj counts residues: from zero.
#:
#: `resid 3` -- the natural thing to write for a tripeptide numbered 1 to 3
#: in its own PDB -- matched no atoms and failed a window after the shared
#: system had been built.
ENDS = {
    "selection_a": "resid 0 and name CA",
    "selection_b": "resid 2 and name CA",
}


@dataclass(frozen=True)
class Method:
    """A run to make, and what its result has to look like."""

    name: str
    description: str
    config: dict[str, Any]

    #: Files that must exist under the run, relative to its output directory.
    produces: tuple[str, ...] = ()

    #: The analysis whose figure must appear, if the method has one.
    figure: str | None = None

    #: How many runs a study of this shape makes. Five for umbrella windows,
    #: one for anything else -- which changes where the outputs land.
    runs: int = 1

    notes: str = ""


def _config(**simulation: Any) -> dict[str, Any]:
    block = dict(COMMON_SIMULATION)
    block.update(simulation)
    return {
        "systems": [{"id": "tripeptide", "system": "tripeptide.pdb"}],
        "setup": dict(COMMON_SETUP),
        "simulation": block,
    }


METHODS: tuple[Method, ...] = (
    Method(
        name="umbrella",
        description="five windows along the end-to-end distance",
        runs=5,
        config=_config(
            # The geometry was never the problem: four windows 0.05 nm
            # apart at 2,000 kJ/mol/nm^2 are 1.4 spreads from each other and
            # their distributions overlap by about 0.48, forty-eight times
            # the threshold. What was missing was independent samples. The
            # restrained distance decorrelates in about 2/friction, so at
            # 1/ps and 8 ps of production each window held roughly four --
            # and two windows that truly overlap by 0.48 read as disjoint a
            # quarter of the time from four samples each. The overlap test
            # was measuring the sample count rather than the windows.
            #
            # Friction is the lever, not length. It sets how fast the
            # coordinate decorrelates and not what it samples: the
            # equilibrium distribution under Langevin dynamics does not
            # depend on it, so the free energy is untouched and only the
            # kinetics change. At 5/ps -- still far below the 37/ps where
            # this mode would become overdamped and start slowing again --
            # the correlation time is 0.4 ps, and 16 ps of production is
            # forty independent samples per window, at which a disjoint pair
            # did not occur once in 20,000 trials. 8,000 steps over five runs
            # is 40,000, the ceiling this suite sets for itself.
            friction_per_ps=5.0,
            production_steps=8000,
            trajectory_interval_steps=100,
            umbrella={
                "collective_variable": "distance",
                **ENDS,
                # Spacing and stiffness are one decision, not two. A window
                # held at force constant k has a spread of sqrt(RT/k): at
                # 10,000 kJ/mol/nm^2 that is 0.016 nm, so windows 0.1 nm
                # apart are six spreads from each other and share nothing.
                # Raising the stiffness to stop them sliding while widening
                # the spacing moved them in opposite directions, and the
                # study refused for the opposite reason to the one being
                # fixed.
                #
                # 2,000 kJ/mol/nm^2 gives 0.035 nm, so 0.05 nm spacing is
                # about one and a half spreads: neighbours share ground
                # without being so soft that they slide together.
                "from": 0.68,
                "to": 0.83,
                "n_windows": 4,
                "force_constant": 2000.0,
                "minimum_overlap": 0.01,
                "minimum_samples": 20,
            },
        ),
        produces=("pmf.json", "free_energy/pmf/pmf.png"),
        notes=(
            "The windows are the study; the free energy is written beside "
            "them rather than inside any one of them, which is why it is "
            "drawn where the comparison report is written."
        ),
    ),
    Method(
        name="metadynamics",
        description="hills along a backbone torsion",
        config=_config(
            production_steps=8000,
            trajectory_interval_steps=200,
            metadynamics={
                "collective_variable": "torsion",
                # psi of the first residue: exactly four atoms, in order.
                "selection": "(resid 0 and name N CA C) or (resid 1 and name N)",
                # Radians. A torsion is periodic, so no walls are needed --
                # unlike a distance, which has nowhere to stop and pulled the
                # peptide apart until walls were added.
                "sigma": 0.2,
                "height_kjmol": 2.0,
                "pace_steps": 100,
                "bias_factor": 15.0,
            },
        ),
        produces=("simulation/HILLS", "simulation/metadynamics_surface.json"),
        figure="metad_surface",
        notes=(
            "Short enough that the surface will be refused as unsettled, "
            "which is the point: a provisional surface is drawn and labelled "
            "rather than withheld, and that is the behaviour worth pinning."
        ),
    ),
    Method(
        name="steered",
        description="a pull along the end-to-end distance",
        config=_config(
            production_steps=4000,
            trajectory_interval_steps=100,
            steered={
                "collective_variable": "distance",
                **ENDS,
                "from": 0.73,
                "to": 0.95,
                "steps": 4000,
                "force_constant": 2000.0,
            },
        ),
        produces=("simulation/steered_work.json",),
        figure="steered_work",
        notes=(
            "The work varies between identical runs -- 67.5 kJ/mol once and "
            "26.9 another -- which is the dissipation, and why one pull is a "
            "pathway rather than a free energy."
        ),
    ),
)


#: Analyses that must not run on a tripeptide, and why.
#:
#: The gating is the half of the analysis layer that a corpus of real
#: proteins cannot test, because on a real protein everything applies.
INAPPLICABLE = {
    "qvalue": (
        "measures the fraction of native tertiary contacts retained, and a "
        "three-residue peptide has no pair of residues far enough apart in "
        "sequence to make one"
    ),
}


def names() -> tuple[str, ...]:
    return tuple(method.name for method in METHODS)
