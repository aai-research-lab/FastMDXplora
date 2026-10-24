"""How a mean's image note is said, read from its record: one reader for the
report, every page that shows the mean, and the Agent.

The image check (:mod:`fastmdxplora.analysis.image_check`) finds how close
the solute came to its own periodic images in each frame, and every mean of
those frames carries a note of it where the solute came within the run's
cutoff of its image in the frames the mean uses (``solute_image`` on the
mean's record, its numbers; the analysis's ``findings.solute_image``, its
paragraph). The note does not change the mean's verdict: a mean determined
is still determined, its error kept, and the note is said beside it.

The report said the note and the Overview, the Analysis page's table and the
Agent did not, so one mean read "may be biased" on one page and "Determined"
alone on the next. Every reader now reads the note here, from the record:
:func:`note_of` (the mean's numbers), :func:`said_short` (beside the
verdict, "may be biased: in 54 of 270 frames used (20%) the solute was
within 1 nm of its own image"), :func:`paragraph_of` (the analysis's
paragraph, one click or one look away), :func:`replicas_said` (beside the
replicas' mean of their means), :func:`finding_said` (the image check's own
finding, in a sentence, for its row and its card), :func:`longer_run_said`
(beside the advice of a longer run, for a run in contact with its image in
every frame) and :func:`determined_said` (beside means all determined, for
such a run).

Light on purpose: no analysis code is imported, so a page reads it without
loading the trajectory tools.
"""

from __future__ import annotations

from typing import Any

#: What a note's count is of, as it says it: the trajectory's frames a mean
#: uses, the energy rows a thermodynamic mean averages (each read at the
#: trajectory frame nearest its time), or, for an analysis without a main
#: mean, the frames its means use.
COUNTED_OVER = {
    "frames": "frames used",
    "energy rows": "energy rows used",
    "means' frames": "frames its means use",
}


def share_said(part: int, whole: int) -> str:
    """A share of frames as a percentage, never rounded to 0% or 100%."""
    value = 100.0 * part / whole if whole else 0.0
    if 0 < value < 1:
        return f"{value:.2g}%"
    if 99 < value < 100:
        return f"{value:.1f}%"
    return f"{value:.0f}%"


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def note_of(record: Any) -> dict[str, Any] | None:
    """The note a mean record (or an analysis's findings, or a reweighted
    average) carries: its ``solute_image`` where the solute came within the
    cutoff of its image in some of the frames it uses; else None. The image
    check's own finding is not a note (it has no ``frames_used``)."""
    note = record.get("solute_image") if isinstance(record, dict) else None
    if not isinstance(note, dict):
        return None
    used, under = _count(note.get("frames_used")), _count(note.get("frames_under_cutoff"))
    if not (used and under and used > 0 and under > 0):
        return None
    return note


def said_short(note: dict[str, Any] | None) -> str:
    """The note beside a mean's verdict, in a line: "may be biased: in 54
    of 270 frames used (20%) the solute was within 1 nm of its own image".
    An empty string where there is no note."""
    if not note_of({"solute_image": note}):
        return ""
    assert note is not None
    used, under = int(note["frames_used"]), int(note["frames_under_cutoff"])
    cutoff = note.get("cutoff_nm")
    within = (f"within {float(cutoff):g} nm of" if isinstance(cutoff, (int, float))
              and not isinstance(cutoff, bool) else "within the cutoff of")
    over = str(note.get("counted_over") or "frames")
    noun = COUNTED_OVER.get(over, "frames used")
    share = share_said(under, used)
    # The solute was within, in so many frames: frames do not come within.
    return (f"may be biased: in {under:,} of {used:,} {noun} ({share}) the solute "
            f"was {within} its own image")


def replicas_said(notes: Any) -> str:
    """The note beside the replicas' mean of their means, from its members'
    notes (one per replica whose mean it averages, None where a replica's
    mean has none): how many of them may be biased, said short ("may be
    biased: in 2 of the 3 replicas the solute was within 1 nm of its own
    image, in frames their means use"). Replicas share the box, so a bias in
    their means is in the mean of them. An empty string where none is
    noted."""
    members = list(notes or [])
    found = [note for note in members if note_of({"solute_image": note})]
    if not found:
        return ""
    # The cutoff where every noted replica says the same one.
    cutoffs = {float(note["cutoff_nm"])
               if isinstance(note.get("cutoff_nm"), (int, float))
               and not isinstance(note.get("cutoff_nm"), bool) else None for note in found}
    within = (f"within {next(iter(cutoffs)):g} nm of" if len(cutoffs) == 1 and None not in cutoffs
              else "within the cutoff of")
    which = (f"each of the {len(members)} replicas" if len(found) == len(members)
             else f"{len(found)} of the {len(members)} replicas")
    uses = "its mean uses" if len(found) == 1 else "their means use"
    return (f"may be biased: in {which} the solute was {within} its own image, "
            f"in frames {uses}")


def paragraph_of(findings: Any) -> str:
    """The analysis's own paragraph of its note (``findings.solute_image``,
    counted over the frames its main mean uses, or its means'), or of the
    image check's finding; an empty string where there is none."""
    image = findings.get("solute_image") if isinstance(findings, dict) else None
    if not isinstance(image, dict):
        return ""
    # Read on a page as plain text: the report's code marks are left out.
    return str(image.get("said") or "").replace("`", "")


#: What lifts the bias of a solute in contact with its image in every frame,
#: and what does not: said beside any advice of a longer run or replicas.
_WHAT_LIFTS_IT = ("a longer run in the same box, like replicas in it, samples the same "
                  "contact, and a larger box, in a new study, is what lifts it.")


def _in_contact_throughout(finding: Any) -> bool:
    """Whether the image check found the solute in contact with its own
    image (under 0.5 nm) in every frame it read."""
    if not isinstance(finding, dict) or "frames_used" in finding:
        return False
    frames, close = _count(finding.get("frames")), _count(finding.get("frames_under_close"))
    return bool(frames) and close == frames


def longer_run_said(finding: Any) -> str:
    """Where the image check found the solute in contact with its own image
    (under 0.5 nm) in every frame it read, that a longer run, which may
    determine the means, does not lift that bias, and what does; an empty
    string otherwise. One sentence for the report, the Overview's lead and
    the Agent's answers, which advised only a longer run."""
    if not _in_contact_throughout(finding):
        return ""
    return ("A longer run may determine the means; it would not lift the bias of the "
            "solute's own image, in contact with it in every frame: " + _WHAT_LIFTS_IT)


def determined_said(finding: Any, means: int | None = None) -> str:
    """For a run whose means are all determined, where the image check found
    the solute in contact with its own image in every frame, that they may
    still be biased by it, and what lifts that; an empty string otherwise.
    Beside "All 2 means are determined" on the Overview and "By its records,
    yes" in the Agent's answers, which said nothing more. ``means`` is how
    many there are, for one to be said "its mean"."""
    if not _in_contact_throughout(finding):
        return ""
    subject = "Its mean" if means == 1 else "Its means"
    return (f"{subject} may still be biased by the solute's own image, in contact with it "
            "in every frame: " + _WHAT_LIFTS_IT)


def recorded_finding(root: Any) -> dict[str, Any] | None:
    """The image check's finding as a run recorded it (its
    ``analysis/image_check/options.json``, read through `record_access`);
    None where there is none, or it cannot be read."""
    import json
    from pathlib import Path

    from fastmdxplora import record_access

    try:
        record = json.loads(record_access.read_text(
            Path(root) / "analysis" / "image_check" / "options.json", encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    found = (record.get("findings") or {}).get("solute_image") if isinstance(record, dict) \
        else None
    return found if isinstance(found, dict) else None


def paragraph_beside(note: dict[str, Any] | None, findings: Any) -> str:
    """The analysis's paragraph (:func:`paragraph_of`) beside one of its
    means' notes: as it is where it counts what the mean's note counts, else
    said as the analysis's, its counts not this mean's (a sub-mean beside
    the main mean, a thermodynamic mean beside the union of the rows). An
    empty string where there is no paragraph."""
    paragraph = paragraph_of(findings)
    if not paragraph or not note:
        return paragraph
    own = note_of(findings)
    counts = ("frames_used", "frames_under_cutoff", "counted_over")
    if own is not None and all(own.get(key) == note.get(key) for key in counts):
        return paragraph
    return "Of the analysis as a whole, not this mean alone: " + paragraph


def finding_said(finding: Any) -> str:
    """The image check's finding in a sentence, its first: how close the
    solute came to its own image, and in how many frames it was within the
    cutoff and in contact. For the check's own row and card; the rest of the
    paragraph (the closest frame and atoms, the cutoff's source, the solute
    and how it was grouped) is one click away."""
    if not isinstance(finding, dict) or "frames_used" in finding:
        return ""
    said = " ".join(str(finding.get("said") or "").replace("`", "").split())
    cut = said.find(". ")
    return said[:cut + 1] if cut > 0 else said
