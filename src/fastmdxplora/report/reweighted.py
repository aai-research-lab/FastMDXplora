"""The averages of a biased run, said in the order they should be read.

The analyses of a metadynamics trajectory each report a mean, and every one
of those means is an average over a distribution the bias flattened on
purpose. The reweighted value is the measurement of the system; the raw one
is a property of the sampling. So the reweighted value leads here and the raw
one follows it, rather than the arrangement the rest of the report would
naturally produce, where the corrected number appears in a section of its own
and the uncorrected one sits under every analysis heading above it.

What the number rests on is printed beside it and not in a footnote: the
weight-concentration effective frames (Kish's count, which says how evenly the
weight is spread) and the independent samples (that count divided by the
statistical inefficiency, since consecutive frames are correlated). A
reweighted mean over five hundred frames whose weight sits in seven of them
is a mean over seven, and 2445 such frames correlated over 91 frames are about
27 independent samples. Neither should be readable apart from the number.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

#: Below this the correction is reported with the caution stated in the same
#: breath as the number, rather than only in the notes underneath.
THIN_EFFECTIVE_FRAMES = 50.0

#: The same caution, on the independent samples where the record has them:
#: the count below which `statistics.summarise` withholds a mean's error.
THIN_INDEPENDENT_SAMPLES = 10.0


def _record_path(project_root: Path) -> Path:
    return (Path(project_root) / "analysis" / "reweighted"
            / "reweighted_averages.json")


def load_reweighted(project_root: Path) -> dict[str, Any] | None:
    """What the analysis phase computed, or None where no bias applied.

    Absent for plain MD, for a steered pull and for an umbrella window, and
    that absence is not a gap: none of those has a set of weights that
    recovers an equilibrium average, which the methods text already says.
    """
    import json

    path = _record_path(project_root)
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict):
        return None
    # A run whose only reweightable result is a clustering has populations
    # and no means. A biased run that cannot be reweighted at all has
    # neither, and is the case that most needs saying.
    if (not record.get("quantities") and not record.get("populations")
            and record.get("applies", True)):
        return None
    return record


def _number(value: Any, digits: int = 4) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number != number:  # nan
        return "—"
    return f"{number:.{digits}g}"


def _with_spread(mean: Any, spread: Any, error: Any = None) -> str:
    """A mean, its standard error where one was computed, and the standard
    deviation of what it averages, said as one.

    The spread is never put after a ``±``: a ``±`` after a mean is read as
    its error, and the spread is the width of the reweighted distribution,
    which does not shrink with sampling. The ``±`` is the paired block
    bootstrap's standard error, where the analysis computed one.
    """
    text = _number(mean)
    try:
        number, err = float(mean), float(error)
    except (TypeError, ValueError):
        number, err = float("nan"), float("nan")
    if math.isfinite(number) and math.isfinite(err) and err >= 0.0:
        from fastmdxplora.statistics import with_its_error

        text = with_its_error(number, err)
    spread_text = _number(spread, 2)
    if text == "—" or spread_text == "—":
        return text
    return f"{text} (s.d. {spread_text})"


def _count(value: Any) -> str:
    """A count of frames or samples: whole from ten up, one decimal below,
    so 2445 does not print as 2.45e+03."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float("nan")
    if not math.isfinite(number):
        return _number(None)
    return f"{number:.0f}" if abs(number) >= 10 else f"{number:.1f}"


def _rests_on(record: dict[str, Any]) -> str:
    """What a reweighted mean rests on, in the words the record allows."""
    ess = record.get("effective_sample_size")
    frames = record.get("n_frames")
    said = f"{_count(ess)} weight-concentration effective frames of {frames}"
    independent = record.get("independent_samples")
    if isinstance(independent, (int, float)):
        said += f", about {_count(independent)} independent samples"
    return said


def reweighted_line(record: dict[str, Any] | None, analysis: str) -> str | None:
    """One line for an analysis's own section, leading with the correction.

    A reader who goes straight to the RMSD heading should not have to find
    another section to learn that the number above it is an average over a
    biased ensemble.
    """
    if not record:
        return None
    for item in record.get("quantities") or []:
        if item.get("analysis") != analysis:
            continue
        withheld = item.get("not_a_measurement")
        return (
            f"**Reweighted mean: {_with_spread(item.get('reweighted_mean'), item.get('reweighted_std'), item.get('reweighted_standard_error'))}**"
            f" — the equilibrium average, recovered from the deposited bias on "
            f"{_rests_on(record)}. "
            + (f"No error is given: {withheld} " if withheld else "")
            + f"The average over the biased frames themselves is "
            f"{_number(item.get('raw_mean'))}"
            f" ({_signed(item.get('shift_percent'))})."
        )
    return None


def _fraction(value: Any) -> str:
    """A population as a percentage, which is how occupancies are read."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number != number:
        return "—"
    return f"{100.0 * number:.1f}%"


def _percent(value: Any) -> str:
    """Signed, because the direction the bias moved a number is the point."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number != number:
        return "—"
    return f"{number:+.1f}%"


def _signed(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "change not available"
    if number != number:
        return "change not available"
    return f"{number:+.1f}% from reweighting"


def reweighted_section(project_root: Path,
                       record: dict[str, Any] | None = None, *,
                       report_dir: Path | None = None) -> str:
    """The corrected averages as a table, with what they rest on."""
    if record is None:
        record = load_reweighted(project_root)
    if not record:
        return ""

    # A biased run whose bias cannot be undone. There is no corrected number
    # to lead with, so what leads is the fact that these are not measurements
    # of the unbiased system -- which is the more important of the two things
    # this section exists to say.
    if not record.get("applies", True):
        return "\n".join([
            "### These averages are of a biased ensemble", "",
            record.get("reason", ""), "",
            "They are reported because they describe what the run did, and "
            "they are labelled because reported plainly they would read as "
            "properties of the system.", "",
        ])

    ess = record.get("effective_sample_size")
    converged = bool(record.get("converged", record.get("settled")))

    lines = ["### Averages after reweighting", ""]
    lines.append(
        "Production was biased, so the trajectory is not a Boltzmann "
        "ensemble and an average over its frames is not an estimate for the "
        "unbiased system. Each frame is weighted by exp((V − c(t))/RT), where V is the "
        "bias it was actually sampled under and c(t) is the Tiwary–Parrinello "
        "offset, which recovers the unbiased average. **The reweighted column "
        "is the result; the biased column is shown so the size of the "
        "correction is visible.**")
    lines.append("")

    caution = ""
    independent = record.get("independent_samples")
    if ((isinstance(ess, (int, float)) and ess < THIN_EFFECTIVE_FRAMES)
            or (isinstance(independent, (int, float))
                and independent < THIN_INDEPENDENT_SAMPLES)):
        caution = (
            " These averages therefore rest on very few independent frames, "
            "and they and the standard deviations beside them are "
            "correspondingly uncertain.")
    explained = ""
    if isinstance(independent, (int, float)):
        explained = (
            " The first count says how evenly the weight is spread and treats "
            "every frame as independent; consecutive frames are correlated, "
            "so the second, the first divided by the statistical inefficiency "
            "of the collective variable, is what the averages rest on.")
    lines.append(
        f"The weights carry {_rests_on(record)}." + explained + caution)
    if not converged:
        lines.append("")
        lines.append(
            "_The bias had not converged when the run ended, so these are "
            "approximate._")
    lines.append("")

    lines.append("| Quantity | Reweighted (equilibrium) | Biased trajectory | Change |")
    lines.append("| --- | --- | --- | --- |")
    withheld: list[str] = []
    for item in record.get("quantities") or []:
        lines.append(
            f"| {item.get('label', item.get('analysis', '—'))} "
            f"| {_with_spread(item.get('reweighted_mean'), item.get('reweighted_std'), item.get('reweighted_standard_error'))} "
            f"| {_with_spread(item.get('raw_mean'), item.get('raw_std'))} "
            f"| {_percent(item.get('shift_percent'))} |")
        if item.get("not_a_measurement"):
            label = item.get("label") or item.get("analysis") or "a quantity"
            withheld.append(f"{label}: {item['not_a_measurement']}")
    lines.append("")
    if any(item.get("reweighted_standard_error") is not None
           for item in record.get("quantities") or []):
        lines.append(
            "The ± is the standard error of the reweighted mean from a "
            "paired block bootstrap over values and weights; the s.d. is the "
            "width of the reweighted distribution.")
        lines.append("")
    for line in withheld:
        lines.append(f"- _No error on {line}_")
    if withheld:
        lines.append("")

    figure = (Path(project_root) / "analysis" / "reweighted"
              / "reweighted_averages.png")
    if figure.is_file():
        # Relative to the page it is written into, which is report/.
        where = os.path.relpath(figure, report_dir or Path(project_root) / "report")
        lines.append(f"![Effect of reweighting]({Path(where).as_posix()})")
        lines.append("")

    warnings = record.get("warnings") or []
    if warnings:
        for warning in warnings:
            lines.append(f"- _{warning}_")
        lines.append("")

    occupancies = record.get("populations") or []
    if occupancies:
        lines.append("#### State populations after reweighting")
        lines.append("")
        lines.append(
            "How often each conformational state was really visited. A "
            "population is a weighted count, so it corrects exactly as a "
            "mean does — and it is the quantity a bias distorts most, since "
            "escaping a well is what the bias is for.")
        lines.append("")
        for entry in occupancies:
            method = entry.get("method") or ""
            heading = (f"{entry.get('analysis', 'cluster')} — {method}"
                       if method else str(entry.get("analysis", "cluster")))
            lines.append(f"**{heading}**")
            lines.append("")
            lines.append("| State | Reweighted | Biased trajectory | Change |")
            lines.append("| --- | --- | --- | --- |")
            for state in entry.get("states") or []:
                lines.append(
                    f"| {state.get('label')} "
                    f"| {_fraction(state.get('reweighted_fraction'))} "
                    f"| {_fraction(state.get('raw_fraction'))} "
                    f"| {_percent(state.get('shift_percent'))} |")
            lines.append("")
        caveat = record.get("populations_caveat")
        if caveat:
            lines.append(f"_{caveat}_")
            lines.append("")

    # Said plainly rather than left to be inferred from the table's absence.
    lines.append(
        "_The dimensionality reduction is not reweighted: a projection is "
        "not an average, and the embedding it produces is one of the biased "
        "ensemble._")
    lines.append("")
    return "\n".join(lines)
