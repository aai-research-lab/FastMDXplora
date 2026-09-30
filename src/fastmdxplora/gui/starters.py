"""Studies to start from, in the builder.

A first study begins at an empty form and four questions. Most people start
from one of a few kinds of study, and the examples page (docs/examples.md)
has a recipe for each; the builder now offers them as starting points, each
a complete Config the validator accepts, with a line on what it is for and
what to change first. Loaded, the builder holds it like any other Config:
the preview, the plan and the time here follow.

Each starter names a real deposited structure so it runs as it stands; the
structure is the first thing to change.
"""

from __future__ import annotations

import copy
from typing import Any

__all__ = ["STARTERS", "starters_payload"]

#: The starters, in the order they are offered. Each Config is checked by
#: the validator in the test suite, and each follows a recipe on the
#: examples page.
STARTERS: tuple[dict[str, Any], ...] = (
    {
        "id": "protein",
        "title": "A protein in water",
        "what": "Prepare, simulate and analyse one protein: the study everything "
                "else is a variation of.",
        "change": "The structure, and the length: 10 ns shows a protein settling, "
                  "not its slower motions.",
        "config": {
            "systems": [{"system": "1UBQ"}],
            "simulation": {"duration_ns": 10},
        },
    },
    {
        "id": "ligand",
        "title": "A protein and its ligand",
        "what": "The ligand in the structure is parameterised (OpenFF, AM1-BCC "
                "charges) and the protein-ligand analyses are added.",
        "change": "The structure and `setup.ligand_name`; for a ligand the "
                  "structure does not hold, supply its chemistry as a file.",
        "config": {
            "systems": [{"system": "181L"}],
            "setup": {"forcefield": "amber-openff", "ligand_name": "BNZ"},
            "simulation": {"duration_ns": 10},
        },
    },
    {
        "id": "membrane",
        "title": "A membrane protein",
        "what": "Oriented on the membrane normal, embedded in POPC, held while "
                "the lipids pack; the bilayer's area per lipid, thickness and "
                "order computed.",
        "change": "The structure and the lipid. An OPM file keeps its own frame.",
        "config": {
            "systems": [{"system": "1AFO"}],
            "setup": {"forcefield": "amber14", "membrane": "POPC",
                      "membrane_orient": True},
            "simulation": {"duration_ns": 20,
                           "restrain": "protein and not element H"},
        },
    },
    {
        "id": "determined",
        "title": "Run until a quantity is determined",
        "what": "Three replicas extended in pieces until the RMSD's mean is known "
                "to 0.01 nm and the replicas agree, with a ceiling.",
        "change": "The quantities and the errors asked, and the ceiling.",
        "config": {
            "systems": [{"system": "1UAO"}],
            "sweep": {"simulation.random_seed": [1, 2, 3]},
            "simulation": {
                "duration_ns": 5,
                "stop_when": {"measures": [{"analysis": "rmsd", "standard_error": 0.01}],
                              "max_duration_ns": 50},
            },
        },
    },
    {
        "id": "umbrella",
        "title": "A free energy along a distance",
        "what": "Umbrella sampling of a ligand's distance from its site: one "
                "prepared system, a window per position, the profile recombined "
                "with its uncertainty.",
        "change": "The range, the windows and the force constant, sized from a "
                  "pull; the study says where its windows do not overlap.",
        "config": {
            "systems": [{"system": "181L"}],
            "setup": {"forcefield": "amber-openff", "ligand_name": "BNZ"},
            "simulation": {
                "duration_ns": 5,
                "umbrella": {"collective_variable": "ligand_distance",
                             "site_selection": "resSeq 84 to 121 and name CA",
                             "from": 0.3, "to": 1.5, "n_windows": 13,
                             "force_constant": 2000},
            },
        },
    },
)


def starters_payload() -> list[dict[str, Any]]:
    """The starters as the builder draws them: each with its plan, as the
    Agent's plan says a Config, so the tile says what will run."""
    from fastmdxplora.gui.plan import plan_of

    offered = []
    for starter in STARTERS:
        entry = copy.deepcopy(starter)
        try:
            entry["plan"] = plan_of(entry["config"])
        except Exception:  # noqa: BLE001 - the starter stands without its plan
            entry["plan"] = []
        offered.append(entry)
    return offered
