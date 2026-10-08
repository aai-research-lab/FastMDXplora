"""What a study reproducing a paper determined, beside what the paper reports.

A config written from a paper carries the results the paper reports for
that study (``paper.claims``), fixed before the study ran. Once it has run,
each is set beside the mean this study's analysis recorded, in one unit,
with a verdict that says only what the numbers allow:

``agrees``           within twice the combined error of the two means
``disagrees``        further apart than that, by so many combined errors
``within_spread``    the paper gives a standard deviation over its frames or
                     runs without their number, so not the error of its
                     mean: this mean is inside that spread
``outside_spread``   outside it
``no_error``         the paper gives no error: the difference is said, and
                     no verdict
``not_determined``   this study's mean was not determined (too few
                     independent samples), and why
``not_compared``     there is no mean here to set beside it (an analysis
                     that did not run, a quantity this does not compute, a
                     unit that does not convert)

A study of several replicas is compared by the mean of its replicas' means,
whose error is their spread over the square root of their number. What a
paper's quantity covered (which atoms, which reference, which part of the
run) is said beside it, since an RMSD from the crystal structure over Cα is
not one from the first frame over the backbone.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

__all__ = ["compare_with_paper", "paper_standard_error", "to_unit_of", "convert",
           "reproduction_lines", "VERDICTS", "SAID"]

VERDICTS = ("agrees", "disagrees", "within_spread", "outside_spread", "no_error",
            "not_determined", "not_compared")

SAID = {
    "agrees": "agrees",
    "disagrees": "disagrees",
    "within_spread": "within the paper's spread",
    "outside_spread": "outside the paper's spread",
    "no_error": "the paper gives no error",
    "not_determined": "not determined here",
    "not_compared": "not compared",
}

#: Each unit as a dimension and a factor to that dimension's base.
_UNITS = {
    "nm": ("length", 1.0), "a": ("length", 0.1), "å": ("length", 0.1),
    "angstrom": ("length", 0.1), "angstroms": ("length", 0.1), "pm": ("length", 1e-3),
    "nm2": ("area", 1.0), "nm^2": ("area", 1.0), "nm²": ("area", 1.0),
    "a2": ("area", 0.01), "å2": ("area", 0.01), "a^2": ("area", 0.01),
    "å^2": ("area", 0.01), "å²": ("area", 0.01), "a²": ("area", 0.01),
    "%": ("fraction", 0.01), "percent": ("fraction", 0.01), "": ("fraction", 1.0),
    "fraction": ("fraction", 1.0),
    "kcal/mol": ("energy", 4.184), "kj/mol": ("energy", 1.0),
}

#: The quantity of an analysis a paper's result is set beside: its main
#: mean, or a named quantity it recorded.
_QUANTITY_KEYS = {
    "secondary_structure": {"helix": "helix_fraction", "strand": "strand_fraction",
                            "sheet": "strand_fraction", "beta": "strand_fraction"},
}

#: Analyses whose one mean a result can be set beside.
_COMPARABLE = ("rmsd", "rg", "sasa", "end_to_end", "ligand_rmsd", "area_per_lipid",
               "bilayer_thickness", "hbonds", "ss", "pair_distance")


def _unit(text: str) -> tuple[str, float] | None:
    from fastmdxplora.paper.values import plain

    key = plain(text or "").strip().lower().replace(" ", "").replace("·", "")
    return _UNITS.get(key)


def convert(value: float, from_unit: str, to_unit: str) -> float | None:
    """``value`` in ``from_unit`` as ``to_unit``, or None where the two are
    not the same kind of quantity."""
    a, b = _unit(from_unit), _unit(to_unit)
    if a is None or b is None or a[0] != b[0]:
        return None
    return value * a[1] / b[1]


def _analysis_unit(analysis: str) -> str:
    from fastmdxplora.gui.report_dashboard import unit_of

    return unit_of(analysis)


def to_unit_of(value: float, unit: str, analysis: str) -> float | None:
    """A paper's value in ``unit`` in the unit ``analysis`` records."""
    return convert(value, unit, _analysis_unit(analysis))


def paper_standard_error(claim: dict[str, Any]) -> float | None:
    """The standard error of a paper's mean, where its ``±`` gives one: a
    standard error as it is, a standard deviation over ``n`` runs or blocks
    divided by the root of ``n``, a 95% interval's half-width by 1.96."""
    error = claim.get("error")
    if not isinstance(error, (int, float)) or not math.isfinite(error) or error <= 0:
        return None
    kind = claim.get("error_kind")
    if kind == "standard_error":
        return float(error)
    if kind == "confidence_95":
        return float(error) / 1.96
    n = claim.get("n")
    if kind == "standard_deviation" and isinstance(n, int) and n > 1:
        return float(error) / math.sqrt(n)
    return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _paper_of(root: Path) -> dict[str, Any] | None:
    import yaml

    candidates = [root / "resolved_config.yml"]
    runs = root / "runs"
    if runs.is_dir():
        candidates += sorted(runs.glob("*/resolved_config.yml"))
    for path in candidates:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        paper = data.get("paper") if isinstance(data, dict) else None
        if isinstance(paper, dict):
            return paper
    return None


def _members(root: Path) -> list[Path]:
    manifest = _read_json(root / "batch_manifest.json")
    if not manifest:
        return [root]
    from fastmdxplora.batch.aggregate import member_directory

    return [member_directory(root, run) for run in manifest.get("runs", [])
            if run.get("status") == "ok"]


def _ours(member: Path, analysis: str, key: str | None) -> dict[str, Any] | None:
    from fastmdxplora.gui.analysis_overview import overview_of

    for row in overview_of(member).get("rows", []):
        if row.get("analysis") != analysis:
            continue
        for quantity in row.get("quantities") or []:
            if (key is None and quantity.get("key") == "mean") or quantity.get("key") == key:
                return quantity
        return {"why": f"{analysis} recorded no {key or 'mean'}."}
    return None


def compare_with_paper(root: str | Path) -> dict[str, Any] | None:
    """Each result the paper reports for this study, with this study's mean
    and the verdict, or None where the study was not written from a paper.
    ``{"paper": {...}, "runs": N, "rows": [...]}``."""
    root = Path(root)
    paper = _paper_of(root)
    if paper is None:
        return None
    members = _members(root)
    rows = [_row(claim, members) for claim in paper.get("claims") or []
            if isinstance(claim, dict)]
    return {"paper": {key: paper.get(key) for key in ("doi", "title", "study", "label")},
            "runs": len(members), "rows": rows}


def _key_for(claim: dict[str, Any]) -> tuple[str, str | None] | None:
    analysis = str(claim.get("analysis") or "")
    quantity = str(claim.get("quantity") or "")
    if quantity == "secondary_structure":
        what = str(claim.get("what") or "").lower()
        for word, key in _QUANTITY_KEYS["secondary_structure"].items():
            if word in what:
                return "ss", key
        return None
    if analysis not in _COMPARABLE:
        return None
    return analysis, None


def _row(claim: dict[str, Any], members: list[Path]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "quantity": claim.get("quantity"), "what": claim.get("what") or "",
        "paper_value": claim.get("value"), "paper_error": claim.get("error"),
        "paper_error_kind": claim.get("error_kind") or "unstated",
        "paper_n": claim.get("n"), "paper_unit": claim.get("unit") or "",
        "where": claim.get("where") or "",
    }
    found = _key_for(claim)
    if found is None:
        return {**out, "verdict": "not_compared",
                "why": "This software computes no single mean of this quantity to set beside it."}
    analysis, key = found
    ours = [_ours(member, analysis, key) for member in members]
    if any(quantity is None for quantity in ours):
        return {**out, "verdict": "not_compared", "analysis": analysis,
                "why": f"The {analysis} analysis did not run here."}
    unit = str(ours[0].get("unit") or "")
    out.update(analysis=analysis, unit=unit)
    value = claim.get("value")
    if not isinstance(value, (int, float)):
        return {**out, "verdict": "not_compared", "why": "The paper's value is not a number."}
    if not str(claim.get("unit") or "").strip() and claim.get("quantity") == "secondary_structure":
        # A share written without its unit: above one, it is a percentage.
        paper_value = float(value) / (100.0 if float(value) > 1.0 else 1.0)
    else:
        paper_value = convert(float(value), str(claim.get("unit") or ""), unit)
    if paper_value is None:
        return {**out, "verdict": "not_compared",
                "why": f"The paper's unit ({claim.get('unit') or 'none given'}) does not "
                       f"convert to this analysis's ({unit or 'none'})."}
    scale = paper_value / float(value) if float(value) else 1.0
    out["paper_value_here"] = paper_value
    undetermined = [q for q in ours if not q.get("determined")]
    if undetermined:
        return {**out, "verdict": "not_determined",
                "why": str(undetermined[0].get("why") or "Not determined.")}
    means = [float(q["value"]) for q in ours]
    if len(means) > 1:
        mean = sum(means) / len(means)
        spread = math.sqrt(sum((m - mean) ** 2 for m in means) / (len(means) - 1))
        error = spread / math.sqrt(len(means))
        # Never smaller than what one run says of itself.
        error = max(error, max(float(q["error"]) for q in ours) / math.sqrt(len(means)))
    else:
        mean, error = means[0], float(ours[0]["error"])
    out.update(value=mean, error=error, runs=len(means))
    difference = mean - paper_value
    out["difference"] = difference
    paper_se = paper_standard_error(claim)
    if paper_se is not None:
        combined = math.sqrt(error ** 2 + (paper_se * abs(scale)) ** 2)
        apart = abs(difference) / combined if combined > 0 else math.inf
        out["combined_errors_apart"] = apart
        out["verdict"] = "agrees" if apart <= 2.0 else "disagrees"
        return out
    error_value = claim.get("error")
    if isinstance(error_value, (int, float)) and error_value > 0 and \
            claim.get("error_kind") in ("standard_deviation", "unstated", "range"):
        spread = float(error_value) * abs(scale)
        out["verdict"] = ("within_spread" if abs(difference) <= spread + 2.0 * error
                          else "outside_spread")
        out["why"] = ("The paper's ± is a spread, not the error of its mean"
                      + (" (it does not say over how many runs)" if not claim.get("n") else "")
                      + ", so the two are not compared as means.")
        return out
    out["verdict"] = "no_error"
    out["why"] = "The paper gives no error for this value, so no verdict is given."
    return out


def _number(value: Any, error: Any = None) -> str:
    from fastmdxplora.statistics import with_its_error

    if not isinstance(value, (int, float)):
        return "-"
    if isinstance(error, (int, float)) and error > 0:
        return with_its_error(float(value), float(error))
    return f"{float(value):.4g}"


def _as_written(value: Any, error: Any = None) -> str:
    """A paper's number as the paper wrote it (its own places, not this
    software's rounding)."""
    if not isinstance(value, (int, float)):
        return "-"
    said = f"{float(value):g}"
    if isinstance(error, (int, float)) and error > 0:
        said += f" ± {float(error):g}"
    return said


def reproduction_lines(root: str | Path) -> list[str]:
    """The report's section comparing this study with the paper it was
    written from, as Markdown lines; none where it was not."""
    from fastmdxplora.report.document import _md_text

    compared = compare_with_paper(root)
    if compared is None:
        return []
    paper = compared["paper"]
    title = paper.get("title") or "the paper"
    cited = f"{_md_text(title, limit=200)}" + (
        f" (doi:{_md_text(paper['doi'], limit=120)})" if paper.get("doi") else "")
    lines = ["## Reproducing the paper", "",
             f"This study was written from {cited}, its study "
             f"{_md_text(paper.get('study') or '', limit=20)} "
             f"({_md_text(paper.get('label') or '', limit=160)}). What the paper reports "
             "for it was kept in the config before the study ran, and is set beside "
             "what this study determined"
             + (f", the mean of its {compared['runs']} runs" if compared["runs"] > 1 else "")
             + ". Two means agree where they are within twice their combined error.", ""]
    rows = compared["rows"]
    if not rows:
        lines.append("_The paper reports no number for this study in its text or tables "
                     "that could be kept, so there is nothing to compare._")
        return lines
    lines += ["| Quantity | The paper | Here | Verdict |", "|---|---|---|---|"]
    notes = []
    for row in rows:
        what = _md_text(f"{row['quantity'].replace('_', ' ')}: {row['what']}".strip(": "),
                        limit=160)
        paper_said = (_as_written(row["paper_value"], row.get("paper_error"))
                      + (f" {_md_text(row['paper_unit'], limit=20)}" if row.get("paper_unit") else ""))
        if row.get("paper_error") and row.get("paper_error_kind") not in ("standard_error",):
            paper_said += f" ({row['paper_error_kind'].replace('_', ' ')}"
            paper_said += f", n = {row['paper_n']})" if row.get("paper_n") else ")"
        here = (_number(row["value"], row.get("error")) + (f" {row['unit']}" if row.get("unit") else "")
                if "value" in row else "-")
        verdict = SAID[row["verdict"]]
        if row["verdict"] in ("agrees", "disagrees") and "combined_errors_apart" in row:
            verdict += f" ({row['combined_errors_apart']:.1f} combined errors apart)"
        lines.append(f"| {what} | {paper_said} | {here} | {verdict} |")
        if row.get("why") and row["verdict"] not in ("agrees", "disagrees"):
            notes.append(f"- {what}: {_md_text(row['why'], limit=300)}")
    lines.append("")
    if notes:
        lines += notes + [""]
    lines.append("_A quantity is computed here over this software's own atoms and from "
                 "its own reference (an RMSD from the first frame over the alpha "
                 "carbons, by default); where the paper's description says otherwise, the "
                 "two are not the same quantity, whatever the verdict._")
    return lines
