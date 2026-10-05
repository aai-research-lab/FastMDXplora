"""Residue names that are water, in every form the package meets.

One set, so an analysis does not find water under one name and miss it
under another. `water_sites` kept HOH, WAT, TIP, TIP3, SOL and H2O and
missed TIP4, TIP2, OH2, HHO and OHH, which MDTraj's own `water` selection
takes; `bilayer` and the analysis orchestrator kept sets of their own.

The union of MDTraj's water residue names (`mdtraj.core.residue_names`,
copied rather than imported because the name there is private) with those
force fields and preparation tools write: TIP5P's TIP5, SPC's SPC, GROMACS'
T3P, T4P and T5P, TIP3P spelled out, and heavy water's DOD. Imports nothing,
so any module may use it.
"""

from __future__ import annotations

__all__ = ["WATER_RESIDUES"]

#: Upper-case residue names of water.
WATER_RESIDUES: frozenset[str] = frozenset({
    # MDTraj's `water`.
    "HOH", "WAT", "TIP", "TIP2", "TIP3", "TIP4", "SOL", "H2O", "OH2", "HHO", "OHH",
    # Written by force fields and preparation tools.
    "TIP5", "TIP3P", "SPC", "T3P", "T4P", "T5P", "DOD",
})
