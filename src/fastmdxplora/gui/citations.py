"""What an Agent's answer drew on, as the study recorded it.

An answer about a finished study quotes numbers the analyses wrote, and a
reader had no way to check one short of finding the figure and reading its
caption. The analyses an answer names are listed under it, each with the mean
its analysis recorded and a link to its figure on the Analysis page, so the
number in the prose can be read against the record and the figure behind it.

Found by what the answer says, not by what the model claims to have used: an
analysis is cited when the answer names it and the study holds it. The value
shown is the record's, whatever the answer said.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

__all__ = ["cited_findings"]

#: How an answer may name each analysis, beside its folder's own name. Words
#: a person would use; none so common that ordinary prose names it by
#: accident ("thickness" alone, "contacts" alone and "Q" are left out).
NAMES: dict[str, tuple[str, ...]] = {
    "rmsd": ("RMSD", "root-mean-square deviation", "root mean square deviation"),
    "rmsf": ("RMSF", "root-mean-square fluctuation", "root mean square fluctuation"),
    "rg": ("Rg", "R_g", "radius of gyration"),
    "hbonds": ("hydrogen bonds", "hydrogen bond", "H-bonds", "H-bond"),
    "ss": ("secondary structure", "DSSP"),
    "sasa": ("SASA", "solvent-accessible surface", "solvent accessible surface"),
    "dihedrals": ("dihedrals", "Ramachandran"),
    "qvalue": ("Q-value", "Q value", "fraction of native contacts", "native contacts"),
    "cluster": ("clusters", "clustering"),
    "dimred": ("PCA", "principal component", "dimensionality reduction"),
    "ligand_rmsd": ("ligand RMSD", "ligand pose RMSD"),
    "ligand_rmsf": ("ligand RMSF",),
    "end_to_end": ("end-to-end distance", "end to end distance"),
    "pair_distance": ("pair distance", "centre-of-mass distance",
                      "center-of-mass distance", "closest approach"),
    "area_per_lipid": ("area per lipid", "APL"),
    "bilayer_thickness": ("bilayer thickness", "D_PP"),
    "lipid_order": ("order parameter", "order parameters", "S_CD"),
    "moments_of_inertia": ("moment of inertia", "moments of inertia"),
    "coordination_number": ("coordination number",),
    "rdf": ("RDF", "radial distribution"),
}

#: What each is called on its chip; otherwise its Analysis page heading.
LABELS: dict[str, str] = {
    "rg": "Rg", "hbonds": "Hydrogen bonds", "sasa": "SASA", "qvalue": "Q",
    "ss": "Secondary structure", "end_to_end": "End-to-end distance",
    "pair_distance": "Pair distance", "area_per_lipid": "Area per lipid",
    "bilayer_thickness": "Bilayer thickness", "lipid_order": "Order parameters",
    "moments_of_inertia": "Moments of inertia", "rdf": "RDF",
    "coordination_number": "Coordination number",
}

#: At most this many under one answer; an answer naming more is a survey,
#: and the Analysis page is the list.
MOST = 8

_FOLDER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def cited_findings(answer: str, root: Any) -> list[dict[str, Any]]:
    """The analyses ``answer`` names that the study at ``root`` holds, in
    the order the answer first names them.

    Each is ``{"analysis", "label", "value", "withheld"}``: ``value`` is the
    recorded mean as its caption gives it ("0.0130 ± 0.0021 nm"), or empty
    for an analysis with no mean (a per-residue profile, a map); ``withheld``
    is the analysis's reason where its mean is not a measurement.
    """
    if not answer or not root:
        return []
    analysis = Path(root) / "analysis"
    if not analysis.is_dir():
        return []
    held = {folder.name: folder for folder in analysis.iterdir()
            if folder.is_dir() and _FOLDER.match(folder.name) and _has_a_result(folder)}
    if not held:
        return []

    # Longest names first, each blanked once found, so "ligand RMSD" is the
    # ligand's and does not also cite the protein's RMSD; "the RMSD" said
    # elsewhere in the same answer still does.
    text = answer
    found: dict[str, int] = {}
    candidates = sorted(
        ((name, spoken) for name in held
         for spoken in (*NAMES.get(name, ()), name, name.replace("_", " "))),
        key=lambda pair: -len(pair[1]))
    for name, spoken in candidates:
        pattern = re.compile(r"(?<![\w-])" + re.escape(spoken) + r"(?![\w-])",
                             0 if _is_an_acronym(spoken) else re.IGNORECASE)
        for match in pattern.finditer(text):
            found[name] = min(found.get(name, match.start()), match.start())
        text = pattern.sub(lambda m: " " * len(m.group(0)), text)

    cites = []
    for name in sorted(found, key=found.__getitem__)[:MOST]:
        mean = _recorded_mean(held[name])
        cites.append({"analysis": name, "label": _label(name),
                      "value": _value(name, mean), "withheld": _withheld(mean)})
    return cites


def _is_an_acronym(spoken: str) -> bool:
    """RMSD, Rg, APL: matched as written, so "rg" in a path is not Rg."""
    return len(spoken) <= 5 and spoken[:1].isupper() and " " not in spoken


def _has_a_result(folder: Path) -> bool:
    return (folder / "options.json").is_file() or any(folder.glob("*.png"))


def _recorded_mean(folder: Path) -> dict[str, Any] | None:
    try:
        record = json.loads((folder / "options.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    found = (record.get("findings") or {}).get("mean") if isinstance(record, dict) else None
    return found if isinstance(found, dict) else None


def _label(name: str) -> str:
    from fastmdxplora.gui.report_dashboard import ANALYSIS_SECTION_BY_FOLDER

    return LABELS.get(name) or ANALYSIS_SECTION_BY_FOLDER.get(name) or name.replace("_", " ")


def _value(name: str, mean: dict[str, Any] | None) -> str:
    """The recorded mean, to the precision its error allows, with its unit."""
    from fastmdxplora.gui.report_dashboard import (
        _finite,
        _format_metric_value,
        _with_its_error,
        unit_of,
    )

    if mean is None:
        return ""
    value = mean.get("mean")
    if not _finite(value):
        return "no mean" if mean.get("not_a_measurement") else ""
    unit = unit_of(name, mean)
    unit = f" {unit}" if unit else ""
    error = mean.get("standard_error")
    if _finite(error) and error > 0 and not mean.get("not_a_measurement"):
        return f"{_with_its_error(value, error)}{unit}"
    return f"{_format_metric_value(value)}{unit}"


def _withheld(mean: dict[str, Any] | None) -> str:
    reason = (mean or {}).get("not_a_measurement")
    return str(reason) if reason else ""
