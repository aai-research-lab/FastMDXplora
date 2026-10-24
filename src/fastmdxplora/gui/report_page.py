"""The report as a document, in the page.

The report phase writes ``report/report.md`` -- the study's methods,
results and convergence, with the sampling caveats in the prose where the
numbers are. It also writes ``not_produced.json`` for anything it could
not make and why: a PDF without WeasyPrint, say.

This turns the first into HTML for the centre column and hands the second
over as notices, so a person reads the report where they ran the study
rather than opening a file to find out what it said.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastmdxplora import record_access as _records

__all__ = ["methods_payload", "report_payload"]


def report_payload(root: Path | str, *, trace: bool = True) -> dict[str, Any]:
    """What the Report page needs, or why there is nothing to show. For a
    study of several runs, the comparison across them. ``trace``: each
    number the report states traced to its record (the Report page; the
    standalone page shows the report as written)."""
    base = Path(root)
    from fastmdxplora.simulation.pipeline import _refused

    if _records.is_file(base / "batch_manifest.json"):
        return _study_of_runs_payload(base, trace=trace)
    report_dir = base / "report"
    source = report_dir / "report.md"
    if _refused(source):
        # Not read, in a read held to the study (`record_access`): which is
        # not a study with no report yet. Named in the answer's unavailable.
        return {"ok": False, "reason": "not read",
                "html": "", "not_produced": [], "downloads": {}}
    if not _records.is_file(source):
        return {"ok": False, "reason": "no report yet",
                "html": "", "not_produced": [], "downloads": {}}

    text = _links_from_the_report(_records.read_text(source, encoding="utf-8", errors="replace"),
                                  report_dir, base)
    # The markdown library lives in the `pdf` extra, not the base
    # install, and a base install still has a report to show. With it,
    # the report is rendered; without it, the same text is shown as it
    # was written, which is legible Markdown. CI installs the base
    # package and found this: five failures where a Mac with the extra
    # had passed.
    html, rendered = render_markdown(text)

    not_produced: list[dict[str, str]] = []
    record = report_dir / "not_produced.json"
    if _records.is_file(record):
        try:
            rows = json.loads(_records.read_text(record, encoding="utf-8"))
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, dict) and row.get("artifact"):
                    not_produced.append({
                        "artifact": str(row["artifact"]),
                        "reason": str(row.get("reason") or ""),
                    })
        except (OSError, ValueError):
            pass

    # Only the downloads that exist. A button for a file that was not
    # produced is the not_produced notice's job, not a dead link's.
    downloads: dict[str, str] = {}
    for key, name in (("pdf", "report.pdf"), ("slides", "slides.pptx"),
                      ("bundle", "project_bundle.zip"),
                      ("markdown", "report.md"), ("summary", "analysis_summary.png")):
        if _records.is_file(report_dir / name):
            downloads[key] = f"/artifacts/report/{name}?download=1"

    # What made each analysis's figures, for a chip under each figure the
    # report shows, as the Analysis page has one (figure_provenance.py).
    try:
        from fastmdxplora.gui.figure_provenance import figure_provenance

        provenance = figure_provenance(base)
    except Exception:  # noqa: BLE001 - the report stands without its chips
        provenance = {}

    return {
        "ok": True,
        "not_produced": not_produced,
        "downloads": downloads,
        "generated": _generated_line(text),
        "rendered": rendered,
        "figure_provenance": provenance,
        **_traced(html, rendered, base, source, trace=trace),
    }


def methods_payload(root: Path | str, *, may_read: Any = None) -> dict[str, Any]:
    """The study's methods paragraphs, to read and to copy into a
    manuscript: as the report gives them, written from the records as they
    stand, so they are there before the report is and follow an extension.

    ``html`` is for the page; ``plain`` is for the clipboard, without the
    Markdown, since a manuscript is not Markdown. ``may_read`` is a hosted
    GUI's rule for what is read outside the study.
    """
    import re

    base = Path(root)
    if _records.is_file(base / "batch_manifest.json"):
        return {"ok": False, "reason": "a study of several runs: each run's report "
                                       "gives its own methods"}
    if not any(_records.exists(base / name) for name in ("manifest.json", "simulation", "setup")):
        return {"ok": False, "reason": "nothing has run yet"}
    from fastmdxplora.report.document import methods_prose

    try:
        prose = methods_prose(base, may_read=may_read)
    except PermissionError as exc:
        return {"ok": False, "reason": str(exc)}
    if not prose:
        return {"ok": False, "reason": "nothing recorded yet"}
    plain = re.sub(r"\*\*([^*]+)\*\*", r"\1", prose).replace("`", "")
    return {"ok": True, "html": render_markdown(prose)[0], "plain": plain}


def _links_from_the_report(text: str, report_dir: Path, base: Path) -> str:
    """Each relative link as it resolves from report/, where the report is.

    A report written before 1085 links each figure from the study's root,
    `analysis/rmsd/rmsd.png`, though it sits in report/; every study made
    with 2.5.8 is one. On the Report page each of those figures failed to
    load. A link that names nothing from report/ but names a file from the
    study's root is read as the second, and the report's file is left as it
    was written.

    A link to a file the study does not hold is shown as its words alone:
    a study shared without its standalone page (the demo is one) linked
    `dashboard.html` from its first lines, and the link went nowhere. The
    report's line naming that page goes with it.
    """
    import re
    from urllib.parse import unquote

    def held(path: str) -> bool:
        return _records.exists(report_dir / path) or _records.exists(base / path)

    def resolved(found: "re.Match[str]") -> str:
        image, words, target = found.group(1), found.group(2), found.group(3)
        rest = found.group(4) or ""
        if re.match(r"^([a-z][a-z0-9+.-]*:|/|#|\.\./)", target, re.IGNORECASE):
            return found.group(0)
        path = unquote(target.split("#")[0].split("?")[0])
        if not path or _records.exists(report_dir / path):
            return found.group(0)
        if _records.is_file(base / path):
            return f"{image}[{words}]({'../' + target}{rest})"
        return found.group(0) if image or _records.exists(base / path) else words

    lines = []
    for line in text.splitlines(keepends=True):
        named = re.fullmatch(r"\s*_Dashboard: \[[^\]]*\]\(([^)\s]+)\)_\s*", line)
        if named and not held(unquote(named.group(1))):
            continue
        lines.append(line)
    return re.sub(r'(!?)\[([^\]]*)\]\(([^)\s]+)(\s+"[^"]*")?\)', resolved, "".join(lines))


def _generated_line(text: str) -> str:
    """The `_Generated: …_` line near the top, if the report carries one."""
    for line in text.splitlines()[:8]:
        stripped = line.strip().strip("_")
        if stripped.lower().startswith("generated:"):
            return stripped[len("generated:"):].strip()
    return ""


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def render_markdown(text: str) -> tuple[str, str]:
    """Markdown to HTML, or the text as written where the library is not
    installed. One renderer for the report page and the Files tab's
    preview, so a .md file reads the same on both and there is nothing
    vendored: the browser has no Markdown of its own, and the Python
    library is the one this software already trusts for its report.
    Raw HTML in the text is shown as written, and a link goes only to the
    web, to mail or within the study: see `fastmdxplora.report.markdown_html`."""
    from fastmdxplora.report.markdown_html import markdown_as_html

    try:
        return markdown_as_html(text), "html"
    except ImportError:
        return '<pre class="report-plain">' + _escape(text) + "</pre>", "plain"


def _study_of_runs_payload(base: Path, *, trace: bool = True) -> dict[str, Any]:
    """The report of a study of several runs: the comparison the batch
    writes once every run has completed, with its figures served from
    comparison/; before that, what the completed runs say so far, and how
    many are still to come. Neither is a run's own report, which each run
    keeps under runs/<id>/report/."""
    from fastmdxplora.gui.exploration import runs_of_a_study

    runs = runs_of_a_study(base) or []
    # Read with the runs a study still extends in rounds, as their pieces so
    # far say them; counted apart, as they have not completed.
    completed = [r for r in runs if r["state"] == "completed" or r.get("so_far")]
    pending = sum(1 for r in runs if r["state"] != "completed")
    comparison = base / "comparison" / "comparison_report.md"
    if _records.is_file(comparison):
        text = _records.read_text(comparison, encoding="utf-8", errors="replace")
        html, rendered = render_markdown(text)
        downloads = {}
        for key, name in (("markdown", "comparison_report.md"),
                          ("summary", "comparison_summary.csv")):
            if _records.is_file(comparison.parent / name):
                downloads[key] = f"/artifacts/comparison/{name}?download=1"
        return {"ok": True, "not_produced": [], "downloads": downloads,
                "generated": _generated_line(text), "rendered": rendered,
                "figures_under": "comparison", "runs": len(runs), "pending": pending,
                "figure_provenance": _comparison_provenance(comparison.parent),
                **_traced(html, rendered, base, comparison, trace=trace)}
    if not completed:
        return {"ok": False, "reason": f"none of {len(runs)} runs completed yet",
                "html": "", "not_produced": [], "downloads": {},
                "runs": len(runs), "pending": pending}
    html, rendered = render_markdown(_so_far(base, runs, completed))
    return {"ok": True, "html": html, "not_produced": [], "downloads": {},
            "generated": "", "rendered": rendered, "figures_under": "comparison",
            "runs": len(runs), "pending": pending}


#: The last report traced, by study and by whether its read was held to
#: it: tracing reads every record, and the page asks again on every change
#: of the run's state. A trace read without the hold may hold a record's
#: values the hold refuses, so it is never given to a held read.
_TRACED: dict[tuple[str, bool], tuple[Any, dict[str, Any]]] = {}


def _held(base: Path) -> bool:
    """Whether this read is held to a study (`record_access`): the root of
    the study's filesystem is then outside what it may read."""
    return not _records.can_read(Path(base.absolute().anchor))


def _traced(html: str, rendered: str, base: Path, source: Path, *,
            trace: bool = True) -> dict[str, Any]:
    """The report's HTML as rendered, and as the Report page shows it:
    each number it states marked and traced to its record
    (`report_numbers.py`); and the records whose values the report's
    numbers were traced to that changed since the report was first read
    here (`_changed_since`). The standalone page and the Files tab's
    preview take the first; the standalone page asks for no trace.

    Every record is read through `record_access`: in a read held to the
    study, one linked out of it is not read, its numbers not traced to it,
    and it is named in ``unavailable`` (again when the trace is kept from
    an earlier read, which reads nothing)."""
    if rendered != "html" or not trace:
        return {"html": html, "marked": "", "numbers": [], "records_changed": []}
    from fastmdxplora.gui.report_numbers import record_files, records_of, traced_html

    def mtime(path: Path) -> int | None:
        try:
            return _records.stat(path).st_mtime_ns
        except OSError:
            return None

    listed_unread: list[str] = []
    files = record_files(base, listed_unread)
    stamps = tuple((str(p), mtime(p)) for p in files)
    signature = (html, mtime(source), stamps, tuple(sorted(set(listed_unread))))
    place = (str(base), _held(base))
    kept = _TRACED.get(place)
    if kept is not None and kept[0] == signature:
        return kept[1]
    not_read: list[str] = []
    records = records_of(base, not_read)
    marked, numbers = traced_html(html, base, not_read, records=records)
    unread = sorted(set(not_read) | set(listed_unread))
    try:
        written = _records.stat(source)
        written_as = f"{written.st_mtime_ns}:{written.st_size}"
    except OSError:
        written_as = ""
    numbers, changed = _changed_since(base, html, numbers, records, unread, written_as)
    said: dict[str, Any] = {"html": html, "marked": marked, "numbers": numbers,
                            "records_changed": changed}
    if unread:
        said["unavailable"] = unread
    _TRACED[place] = (signature, said)
    return said


#: What each report's numbers were traced to when it was first read, by
#: study and report, where no folder to keep them in could be written.
_FIRST_READ: dict[str, dict[str, Any]] = {}


def _first_read_file(key: str) -> Path:
    import os

    override = os.environ.get("FASTMDXPLORA_CACHE_DIR")
    root = Path(override) if override else Path.home() / ".cache" / "fastmdxplora"
    return root / "report_traces" / f"{key}.json"


#: The first reads kept, at most, and for at most so many days: one a
#: report read, the oldest let go.
_FIRST_READS_KEPT = 500
_FIRST_READ_DAYS = 90


def _prune(folder: Path) -> None:
    """The first reads kept in ``folder`` held to the newest
    `_FIRST_READS_KEPT`, none older than `_FIRST_READ_DAYS` days."""
    import time

    try:
        kept = sorted(((p.stat().st_mtime, p) for p in folder.glob("*.json")), reverse=True)
    except OSError:
        return
    oldest = time.time() - _FIRST_READ_DAYS * 86400
    for index, (when, path) in enumerate(kept):
        if index >= _FIRST_READS_KEPT or when < oldest:
            try:
                path.unlink()
            except OSError:
                pass


def _kept(key: str, first: dict[str, Any]) -> None:
    """A first read held in memory, the least recently used let go first."""
    _FIRST_READ.pop(key, None)
    _FIRST_READ[key] = first
    while len(_FIRST_READ) > _FIRST_READS_KEPT:
        _FIRST_READ.pop(next(iter(_FIRST_READ)))


def _used(path: Path) -> None:
    """A stored first read marked as used now (once a day at most), so the
    cache lets go of the least recently read, not the first written."""
    import os
    import time

    try:
        if time.time() - path.stat().st_mtime > 86400:
            os.utime(path)
    except OSError:
        pass


def _changed_since(base: Path, html: str, numbers: list[dict[str, Any]],
                   records: list[dict[str, Any]], unread: list[str], written_as: str = ""
                   ) -> tuple[list[dict[str, Any]], list[str]]:
    """The records changed since this report was first read here, by
    content: each number traced then is looked up again by its file and
    key, and a record whose value there is now another (or gone) changed.
    A record the run writes again unchanged after its report (its manifest,
    its resolved Config) is not one; a file's time says nothing. Kept
    outside the study (the user's cache folder), as the GUI writes nothing
    into a study it reads. Each number whose record changed is given
    ``changed``: the file, the key, and the value then and now.

    ``written_as`` is the report file as written (its time and size): a
    report written again starts a first read of its own, though its words
    came out the same, so Write it again clears what it was asked to."""
    import hashlib

    key = hashlib.sha256(f"{base.resolve()}\0{written_as}\0{html}".encode()).hexdigest()[:32]
    first = _FIRST_READ.get(key)
    if first is None:
        try:
            first = json.loads(_first_read_file(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            first = None
    if not isinstance(first, dict) or not isinstance(first.get("numbers"), dict):
        first = {"numbers": {str(i): {"file": n["record"]["file"], "key": n["record"]["key"],
                                      "value": float(n["record"]["value"])}
                             for i, n in enumerate(numbers) if n.get("traced")}}
        _kept(key, first)
        try:
            path = _first_read_file(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(first), encoding="utf-8")
            _prune(path.parent)
        except OSError:
            pass
        return numbers, []
    _kept(key, first)
    _used(_first_read_file(key))
    now = {(r["file"], r["key"]): r["value"] for r in records}
    said = [dict(n) for n in numbers]
    changed: set[str] = set()
    for index, then in first["numbers"].items():
        try:
            number = said[int(index)]
            file, field, value = then["file"], then["key"], float(then["value"])
        except (KeyError, ValueError, TypeError, IndexError):
            continue
        if file in unread:
            continue
        current = now.get((file, field))
        if current is not None and abs(current - value) <= 1e-12 * max(1.0, abs(value)):
            continue
        changed.add(file)
        number["changed"] = {"file": file, "key": field, "then": value, "now": current}
    return said, sorted(changed)


def _comparison_provenance(folder: Path) -> dict[str, dict[str, Any]]:
    """What each comparison figure was plotted from, by its file's stem, as
    the comparison recorded it (`figures.json`): the runs, the release that
    analysed each, and the release that plotted them. Empty for a comparison
    plotted before it was recorded."""
    from fastmdxplora import __version__

    try:
        record = json.loads(_records.read_text(folder / "figures.json", encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    figures = record.get("figures") if isinstance(record, dict) else None
    if not isinstance(figures, dict):
        return {}
    return {str(name): {**figure, "version": record.get("version"),
                        "host": record.get("host"), "made": record.get("made"),
                        "this_version": str(__version__)}
            for name, figure in figures.items() if isinstance(figure, dict)}


def _so_far(base: Path, runs: list[dict[str, Any]], completed: list[dict[str, Any]]) -> str:
    """A table of the means each completed run determined, one row per run and
    one column per measure with a mean, read from the runs' own findings.
    The comparison across them, with its figures, comes when the last run
    finishes.

    Each mean is given with its standard error and unit, and a mean the
    analysis said is not a measurement is marked as one. The table read an
    `uncertainty` and a `unit` that no analysis writes (its test's fixture
    wrote them), so every mean stood without its error, and a series too
    short to measure stood among them as though it were a result."""
    from fastmdxplora.batch.aggregate import read_member_findings
    from fastmdxplora.gui.report_dashboard import _format_metric_value, unit_of
    from fastmdxplora.image_note import note_of, said_short
    from fastmdxplora.statistics import with_its_error

    axes: list[str] = sorted({axis for r in runs for axis in (r.get("values") or {})})
    short = {axis: axis.split(".")[-1] for axis in axes}
    means: dict[str, dict[str, Any]] = {}
    measures: list[str] = []
    for run in completed:
        found = read_member_findings(Path(run["path"]))
        row = {}
        for analysis, findings in found.items():
            mean = findings.get("mean") if isinstance(findings, dict) else None
            if isinstance(mean, dict) and isinstance(mean.get("mean"), (int, float)):
                row[analysis] = mean
                if analysis not in measures:
                    measures.append(analysis)
        means[run["run_id"]] = row
    # Read so far between rounds where it says so; else completed.
    done = sum(1 for r in completed if not (r.get("so_far") and r.get("state") != "completed"))
    between = len(completed) - done
    # Still to run: neither completed nor read so far between rounds.
    pending = len(runs) - len(completed)
    lines = [f"# {base.name}: {done} of {len(runs)} runs completed", ""]
    if between:
        lines += [f"_{between} {'is' if between == 1 else 'are'} read as far as "
                  f"{'its' if between == 1 else 'their'} finished pieces go: the study extends "
                  f"{'it' if between == 1 else 'them'} in rounds until what it asks is "
                  "determined._", ""]
    if pending:
        lines += [f"_{pending} still to run. The comparison across all of them, with its "
                  "figures, is written when the last one finishes._", ""]
    if not measures:
        lines += ["No run has recorded a mean yet.", ""]
        return "\n".join(lines)
    units = {m: next((unit_of(m, row[m]) for row in means.values() if m in row), "")
             for m in measures}
    # Each measure by its name on the pages ("End-to-end distance"), not its
    # folder's ("end_to_end"), in the header and the notes under the table.
    from fastmdxplora.gui.runs_compared import _named

    named = {m: _named(m) for m in measures}
    head = [short[a] for a in axes] + [
        f"{named[m]} mean" + (f" ({units[m]})" if units[m] else "") for m in measures]
    lines.append("| run | " + " | ".join(head) + " |")
    lines.append("|---|" + "---|" * len(head))
    qualified = False
    noted: list[str] = []
    for run in completed:
        cells = [str((run.get("values") or {}).get(axis, "")) for axis in axes]
        for measure in measures:
            record = means[run["run_id"]].get(measure)
            if record is None:
                cells.append("")
                continue
            error = record.get("standard_error")
            cell = (with_its_error(record["mean"], error)
                    if isinstance(error, (int, float)) and not isinstance(error, bool)
                    else _format_metric_value(record["mean"]))
            if record.get("not_a_measurement"):
                cell += " \\*"
                qualified = True
            # Its image note, a mark here and said in full under the table.
            short = said_short(note_of(record))
            if short:
                cell += " \u2020"
                noted.append(f"{run['run_id']}, {named[measure]}: {short}.")
            cells.append(cell)
        lines.append(f"| {run['run_id']} | " + " | ".join(cells) + " |")
    lines += ["", "Each mean is over the frames after equilibration, with its standard error."]
    if qualified:
        lines += ["", "\\* Not determined: the analysis found the series too short, or "
                  "with too few independent samples, for its mean to be determined."]
    for line in noted:
        lines += ["", f"\u2020 {line}"]
    return "\n".join(lines) + "\n"
