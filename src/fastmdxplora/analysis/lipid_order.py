"""Lipid order: the deuterium order parameter S_CD of every acyl-chain carbon.

How ordered each carbon of each acyl chain is, as deuterium NMR measures it:
for each C-H bond, S_CD = <3 cos^2(theta) - 1> / 2, with theta its angle to
the bilayer normal, averaged over time and lipids. -S_CD is plotted against
the carbon number, one line per chain, as it conventionally is.

A fluid bilayer's profile has a plateau near the glycerol and falls towards
the chain end, where the chains are free to move; a gel's is higher and flat.
The sn-2 chain's first carbons sit lower than the sn-1 chain's, and a cis
double bond shows as a dip. Since each value compares directly with an NMR
quadrupolar splitting, a profile that is uniformly too high or too low is
the clearest sign of a force field or a temperature that does not suit the
lipid.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np
import pandas as pd

from fastmdxplora.analysis.base import Analysis
from fastmdxplora.analysis.bilayer import (
    _BOND_TOLERANCE_NM,
    _COVALENT_NM,
    _symbol,
    _wrapped,
    box_vectors,
    find_bilayer,
    leaflets,
)
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.lipids import BUILT_LIPIDS, is_lipid, is_sterol
from fastmdxplora.refusals import StudyError

__all__ = ["LipidOrder"]


@dataclass
class _Chain:
    lipid: str
    label: str
    #: carbon number (2 upward) -> (carbon position, [hydrogen positions])
    carbons: list[tuple[int, int, list[int]]]


def _bonds(xyz: np.ndarray, symbols: list[str], cell: np.ndarray | None) -> list[set[int]]:
    """Neighbours of each atom, from interatomic distance."""
    delta = xyz[:, None, :] - xyz[None, :, :]
    if cell is not None:
        inverse = np.linalg.inv(cell.T)
        frac = delta @ inverse.T
        delta = (frac - np.round(frac)) @ cell
    distance = np.sqrt((delta ** 2).sum(axis=-1))
    radius = np.array([_COVALENT_NM.get(s, 0.076) for s in symbols])
    limit = radius[:, None] + radius[None, :] + _BOND_TOLERANCE_NM
    bonded = (distance < limit) & ~np.eye(len(symbols), dtype=bool)
    hydrogen = np.array([s == "H" for s in symbols])
    bonded &= ~(hydrogen[:, None] & hydrogen[None, :])
    return [set(np.flatnonzero(row).tolist()) for row in bonded]


def _lipid_label(names: tuple[str, ...], symbols: list[str],
                 neighbours: list[set[int]]) -> str:
    """The lipid's name; a built bilayer's three-letter name made whole."""
    if len(names) == 1 and names[0] in set(BUILT_LIPIDS.values()):
        head = "C"
        for index, symbol in enumerate(symbols):
            if symbol == "N":
                carbons = sum(symbols[n] == "C" for n in neighbours[index])
                head = "C" if carbons == 4 else "E"
                break
        full = names[0] + head
        return full if full in BUILT_LIPIDS else names[0]
    return "-".join(names)


def _chains(names: tuple[str, ...], symbols: list[str],
            neighbours: list[set[int]]) -> tuple[list[_Chain], str]:
    """The acyl chains of one lipid molecule, labelled sn-1, sn-2 or N-acyl."""
    lipid = _lipid_label(names, symbols, neighbours)
    found: list[_Chain] = []

    def hydrogens(index: int) -> list[int]:
        return sorted(n for n in neighbours[index] if symbols[n] == "H")

    def heavy(index: int, symbol: str) -> list[int]:
        return sorted(n for n in neighbours[index] if symbols[n] == symbol)

    for carbonyl, symbol in enumerate(symbols):
        if symbol != "C" or hydrogens(carbonyl):
            continue
        oxygens, carbons, nitrogens = (heavy(carbonyl, "O"), heavy(carbonyl, "C"),
                                       heavy(carbonyl, "N"))
        if len(carbons) != 1:
            continue
        if len(oxygens) == 2 and not nitrogens:
            ester = [o for o in oxygens if len([n for n in neighbours[o]
                                                if symbols[n] != "H"]) == 2]
            if len(ester) != 1:
                continue
            glycerol = [n for n in neighbours[ester[0]]
                        if n != carbonyl and symbols[n] == "C"]
            if len(glycerol) != 1:
                continue
            attached = len(hydrogens(glycerol[0]))
            position = {1: "sn-2", 2: "sn-1"}.get(attached)
            if position is None:
                continue
        elif len(oxygens) == 1 and len(nitrogens) == 1:
            position = "N-acyl"
        else:
            continue
        walk: list[tuple[int, int, list[int]]] = []
        previous, current, number = carbonyl, carbons[0], 2
        while True:
            walk.append((number, current, hydrogens(current)))
            onward = [n for n in heavy(current, "C") if n != previous]
            if len(onward) != 1:
                break
            previous, current, number = current, onward[0], number + 1
        if len(walk) < 4:
            continue
        double = sum(1 for _, c, h in walk[:-1] if len(h) == 1) // 2
        found.append(_Chain(lipid, f"{position} ({len(walk) + 1}:{double})", walk))
    return found, lipid


class LipidOrder(Analysis):
    """Deuterium order parameter S_CD of every acyl-chain carbon.

    For each C-H bond of a chain carbon, the angle theta it makes with the
    bilayer normal (z)::

        S_CD = < 3 cos^2(theta) - 1 > / 2

    averaged over the carbon's hydrogens and the frames for each lipid, then
    over the lipids, with the standard error taken across lipids (each
    lipid's time average is close to an independent sample; consecutive
    frames are not). This is what a 2H NMR quadrupolar splitting reports,
    so it compares with experiment directly; -S_CD is plotted, as it is
    conventionally. A fluid bilayer's profile has a plateau near the
    glycerol and falls towards the chain end; a gel's is higher and flat.

    Chains are found from the bonds, not from atom names, so any force
    field's naming works: an acyl chain starts at a carbonyl carbon bonded
    to an ester oxygen, is sn-2 when that oxygen's glycerol carbon carries
    one hydrogen and sn-1 when it carries two, and runs along the carbon
    backbone to its end. Needs the hydrogens, which a united-atom model or a
    trajectory saved without them does not have.

    Output
    ------
    ``lipid_order.dat`` -- lipid, chain, carbon, s_cd, standard_error,
    lipids; one row per carbon of each chain of each lipid.
    """

    name = "lipid_order"
    description = "Acyl-chain order parameter"
    honours_selection = False
    requires_bilayer = True

    #: Frames at a time, to bound memory on a long run of a large bilayer.
    _CHUNK = 256

    def _molecules(self, topology: md.Topology) -> list[list[Any]]:
        """Lipid molecules as lists of residues, joined across split lipids."""
        residues = [r for r in topology.residues if is_lipid(r.name)]
        parent = {r.index: r.index for r in residues}

        def root(key: int) -> int:
            while parent[key] != key:
                parent[key] = parent[parent[key]]
                key = parent[key]
            return key

        for bond in topology.bonds:
            first, second = bond[0].residue.index, bond[1].residue.index
            if first != second and first in parent and second in parent:
                parent[root(first)] = root(second)
        groups: dict[int, list[Any]] = {}
        for residue in residues:
            groups.setdefault(root(residue.index), []).append(residue)
        return list(groups.values())

    def compute(self, traj: md.Trajectory) -> pd.DataFrame:
        # The order is taken against z, so a bilayer whose normal is not z
        # is refused here as the area and the thickness refuse it.
        leaflets(traj, find_bilayer(traj.topology))
        vectors = box_vectors(traj)
        topology = traj.topology
        templates: dict[tuple, tuple[list[_Chain], str]] = {}
        pairs: dict[tuple[str, str, int], list[tuple[int, list[int], int]]] = {}
        no_hydrogen: set[str] = set()
        no_chain: set[str] = set()
        for number, molecule in enumerate(self._molecules(topology)):
            if all(is_sterol(r.name) for r in molecule):
                continue  # a sterol has no acyl chain to order
            atoms = [a for residue in molecule for a in residue.atoms]
            key = tuple((r.name, tuple(a.name for a in r.atoms)) for r in molecule)
            if key not in templates:
                symbols = [_symbol(a) for a in atoms]
                names = tuple(r.name for r in molecule)
                if "H" not in symbols:
                    no_hydrogen.add("-".join(names))
                    templates[key] = ([], "-".join(names))
                    continue
                xyz = traj.xyz[0, [a.index for a in atoms]].astype(np.float64)
                neighbours = _bonds(xyz, symbols, vectors[0])
                templates[key] = _chains(names, symbols, neighbours)
                if not templates[key][0]:
                    no_chain.add(templates[key][1])
            chains, _ = templates[key]
            for chain in chains:
                for carbon, position, hydrogens in chain.carbons:
                    if hydrogens:
                        pairs.setdefault((chain.lipid, chain.label, carbon), []).append(
                            (atoms[position].index,
                             [atoms[h].index for h in hydrogens], number))
        if not pairs:
            if no_hydrogen:
                raise StudyError(
                    "The lipids in this trajectory have no hydrogens, and S_CD "
                    "is the order of the C-H bonds. A united-atom model, or a "
                    "trajectory saved without hydrogens, cannot report it.",
                    code="analysis.data.absent")
            raise StudyError(
                "No acyl chain was found in these lipids: no carbonyl carbon "
                "bonded to an ester or amide and to a carbon chain.",
                code="analysis.system.inapplicable")

        heights = vectors[:, 2, 2]
        rows = []
        for (lipid, label, carbon), entries in sorted(pairs.items()):
            carbons = np.array([c for c, hs, _ in entries for _ in hs])
            hydrogens = np.array([h for _, hs, _ in entries for h in hs])
            owner = np.array([m for _, hs, m in entries for _ in hs])
            total = np.zeros(len(carbons))
            for start in range(0, traj.n_frames, self._CHUNK):
                stop = min(start + self._CHUNK, traj.n_frames)
                bond = (traj.xyz[start:stop, hydrogens]
                        - traj.xyz[start:stop, carbons]).astype(np.float64)
                bond[..., 2] = _wrapped(bond[..., 2], heights[start:stop, None])
                cosine2 = bond[..., 2] ** 2 / (bond ** 2).sum(axis=-1)
                total += (1.5 * cosine2 - 0.5).sum(axis=0)
            per_bond = total / traj.n_frames
            molecules, where = np.unique(owner, return_inverse=True)
            per_lipid = (np.bincount(where, weights=per_bond)
                         / np.bincount(where))
            error = (float(per_lipid.std(ddof=1) / np.sqrt(len(per_lipid)))
                     if len(per_lipid) > 1 else float("nan"))
            rows.append({"lipid": lipid, "chain": label, "carbon": carbon,
                         "s_cd": float(per_lipid.mean()),
                         "standard_error": error, "lipids": int(len(molecules))})
        table = pd.DataFrame(rows, columns=["lipid", "chain", "carbon", "s_cd",
                                            "standard_error", "lipids"])
        self.findings["order"] = {
            "frames": int(traj.n_frames),
            "chains": sorted({f"{r['lipid']} {r['chain']}" for r in rows}),
            "normal": "z",
            "error": "standard error across lipids of each lipid's time average",
        }
        self.findings["averaged_over"] = (
            f"Averaged over all {traj.n_frames} frames. A bilayer built from a "
            "patch and a protein pushed into it relaxes over the first few "
            "nanoseconds; if the area per lipid is still drifting, the early "
            "frames are in this average too.")
        if no_chain:
            self.findings["no_chain_found"] = (
                f"No acyl chain was found in {', '.join(sorted(no_chain))}, "
                "which is left out.")
        if no_hydrogen:
            self.findings["no_hydrogens"] = (
                f"{', '.join(sorted(no_hydrogen))} has no hydrogens and is left "
                "out.")
        return table

    def plot(self, result: pd.DataFrame, ax: plt.Axes) -> None:
        for (lipid, chain), rows in result.groupby(["lipid", "chain"], sort=True):
            rows = rows.sort_values("carbon")
            ax.errorbar(rows["carbon"], -rows["s_cd"], yerr=rows["standard_error"],
                        marker="o", markersize=3, linewidth=1.2, capsize=2,
                        label=f"{lipid} {chain}")
        ax.legend(loc="best", fontsize=7.5)

    def default_xlabel(self) -> str | None:
        return "Carbon number"

    def default_ylabel(self) -> str | None:
        return "-S_CD"


register_analysis(LipidOrder.name, LipidOrder)
