"""Marking what nothing checked, in the one place the mark has to survive.

`agent: unvalidated` means a phase worked outside this schema. The study
is still recorded, still reproducible, still has a config and a manifest.
What it does not have is anything that checked the method.

The manifest already says which phases those were --
:func:`fastmdxplora.config.agent_modes.unchecked_phases` returns the list.
That is enough for anyone holding the directory, and it is not enough for
the case that matters. A figure ends up in a slide deck, an email, a
supervisor's folder, a paper draft. It leaves the manifest behind on the
first copy, and from then on it is a plot like any other plot.

So the mark goes on the figure. Small, in a corner, out of the way of the
data: **unvalidated** and the date. That is the one artifact whose journey
takes it away from everything that would otherwise explain it.

Three decisions worth stating, because each could reasonably have gone the
other way.

Beside `refusals.py` and `cost.py` rather than under `analysis/`, because
it is not an analysis concern: `unchecked_phases` covers every phase, and
a setup or simulation artifact from an unchecked phase needs the same
mark. It lived under `analysis/` first only because `save_figure` does,
which is a fact about where the mark is applied and not about what it is.

**Only the unchecked phases.** A trajectory from a validated simulation is
sound even when the analysis over it was not, and marking it anyway is
crying wolf. A mark that appears on everything stops being read, and then
it protects nobody. This is the whole reason `agent` is a per-phase
setting.

**It is not removable by a setting.** There is no `mark: false`. Somebody
who wants the mark gone has to rerun the work inside the schema, which is
exactly the action the mark exists to prompt. A flag would make it a
formality.

**It says "unvalidated", not "wrong".** The result may be perfectly good;
what is absent is the checking, and the reader is entitled to know which
of those they are looking at. Overstating it would be its own kind of
dishonesty, and the first person to find an unvalidated figure that turned
out fine would learn to ignore the mark.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

__all__ = [
    "MARK_TEXT",
    "mark_for",
    "stamp_figure",
    "sidecar_for",
    "write_sidecar",
]

#: What the mark says. One phrase, so a reader who has seen it once
#: recognises it anywhere, and so it is searchable.
MARK_TEXT = "unvalidated"


def mark_for(config: dict[str, Any] | None, phase: str) -> str:
    """The mark this phase's artifacts carry, or an empty string.

    Empty for a phase a person wrote, one a model drafted and the
    validator accepted, or one run autonomously within a budget -- all
    three went through the same checks, and the difference between them is
    a provenance question the manifest answers, not a warning.
    """
    if not config:
        return ""
    from fastmdxplora.config.agent_modes import unchecked_phases

    if phase not in unchecked_phases(config):
        return ""
    return f"{MARK_TEXT} · {time.strftime('%Y-%m-%d')}"


def stamp_figure(fig: Any, mark: str) -> None:
    """Put the mark in a figure's lower-left corner.

    Figure coordinates rather than axes, so it sits outside the data and
    cannot be mistaken for an annotation on it, and so it lands in the same
    place whatever the plot is.

    Lower left because that corner is empty in almost every plot this
    package draws -- a legend goes upper right by default, and an axis
    label sits below the ticks rather than under the frame. Small and in
    the annotation colour: legible, and not competing with the figure for
    attention. A mark that shouted would be cropped out by the first
    person it annoyed.

    The colour comes from `plotting.colour("ANNOTATION")` rather than being
    named here. That was not the first version -- a hardcoded hex was, and
    test_no_analysis_names_a_colour_of_its_own caught it. The rule earns
    its keep twice over: one module decides what figures look like, and
    greyscale mode now reaches the mark as well, which a literal colour
    would have quietly escaped.
    """
    if not mark:
        return
    from fastmdxplora.analysis.plotting import colour

    fig.text(
        0.005, 0.005, mark,
        ha="left", va="bottom",
        fontsize=6.5, color=colour("ANNOTATION"),
        zorder=1000,
    )


def sidecar_for(path: Path | str) -> Path:
    """Where the note beside a data file goes."""
    target = Path(path)
    return target.with_suffix(target.suffix + ".unvalidated.json")


def write_sidecar(path: Path | str, phase: str, mark: str,
                  detail: str = "") -> Path | None:
    """Leave a note beside a data file that nothing checked.

    A sidecar rather than a header comment, because the formats this
    package writes are not all commentable -- a DCD has nowhere to put a
    sentence -- and a rule that applied to some files and not others would
    be a rule nobody could rely on.

    Read by whoever has the directory, which is also who has the manifest,
    so this is belt and braces rather than the load-bearing part. The
    figure stamp is the load-bearing part.
    """
    if not mark:
        return None
    import json

    note = sidecar_for(path)
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(json.dumps({
        "unvalidated": True,
        "phase": phase,
        "written": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "what_this_means": (
            "This file came from a phase that worked outside the "
            "FastMDXplora schema, so nothing checked the method that "
            "produced it. The study is still recorded and still "
            "reproducible; what is absent is the checking, not "
            "necessarily the correctness."
        ),
        "detail": detail,
    }, indent=2), encoding="utf-8")
    return note
