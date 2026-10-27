"""Problems already recorded by a study, without running or changing it."""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any, Callable

from fastmdxplora.gui.analysis_overview import overview_of
from fastmdxplora.gui.series import NAME
from fastmdxplora.gui.telemetry import (
    EVENTS_FILE,
    METRICS_FILE,
    STATUS_FILE,
    TEMPERATURE_EXPLANATION,
    TEMPERATURE_REMEDY,
    _GOING_STATUSES,
    _pieces_of,
    analyze_health,
    read_metrics,
    status_as_it_stands,
)
from fastmdxplora.gui.workspace import study_key
from fastmdxplora.record_access import (
    confined_records,
    exists,
    is_dir,
    is_file,
    read_text,
    stat,
)
from fastmdxplora.refusals import code_for
from fastmdxplora.remedies import remedy_for


_LOG = logging.getLogger(__name__)

#: Said under "What would fix it" where the records hold no remedy.
NO_REMEDY = "No remedy is recorded."

#: The run's narration, where its warnings are written (`TelemetryWriter.event`).
EVENTS = f"simulation/{EVENTS_FILE}"

#: The study's own log (`orchestrator.py` attaches it to every run).
LOG = "fastmdxplora.log"

#: What the report could not produce, with its refusal code (`report/run.py`).
NOT_PRODUCED = "report/not_produced.json"

#: The report, whose convergence checks say whether each observable
#: equilibrated (`report/document.py`, `report/convergence.py`).
REPORT = "report/report.md"

#: Said, not counted as unavailable, of a run still going.
WRITTEN_AT_THE_END = ("manifest.json is written when the run ends; its refusals and "
                      "phase results are read from it then.")

#: Said of an open folder with no record in it at all.
NO_RECORDS_YET = ("No records yet. A study's problems are read from the records its "
                  "run writes, and this folder holds none so far.")

#: The refusals of a run that ended where it can be carried on.
_STOP_CODES = frozenset({"simulation.run.stopped", "simulation.run.interrupted"})

#: A phase status as the Overview says it ("Setup failed").
_PHASE_SAID = {"error": "failed", "failed": "failed", "stopped": "stopped",
               "interrupted": "interrupted"}

#: The warning the orchestrator writes beside a stop (`orchestrator.py`).
_PHASE_STOPPED = re.compile(r"\w+ phase stopped")

#: The warning logged when a stop is asked for (`runner._StopRequests`,
#: `batch/explorer.py`): part of the stop, not a problem of its own.
_STOP_HEARD = re.compile(
    r"(?:\w+ received|Stop asked from the GUI): (production will stop|each run in progress ends)")

#: A warning line of the study's log, in either of its formats
#: (`utils/logging.py`: `_PlainISOFormatter`, `_PrettyFormatter`).
_LOG_RECORD = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d{3})? - (?P<plain>[A-Z]+) - "
    r"|\d{2}:\d{2}:\d{2} \S (?P<pretty>[A-Z]+) +)(?P<message>.*)$")

#: The mark the study's log gets where a run starts (`utils/logging.py`,
#: `_mark_a_new_run`).
_NEW_RUN = "=== new run "

#: Where a run starts a phase (`orchestrator._run_phase`).
_PHASE_STARTS = re.compile(r"--> Phase '(?P<phase>\w+)' starting")

#: What the telemetry's own explanations say would fix a run, where one does.
_HEALTH_REMEDIES = {TEMPERATURE_EXPLANATION: TEMPERATURE_REMEDY}


def admitted_for_problems(root: Path) -> Callable[[Path], bool]:
    """The study's public files, and the run's own process record.

    The process record (`RUN_PROCESS_FILE`) is hidden, so the public rule
    refuses it, and without it a run whose record says it is going cannot
    be told from one whose process is gone. Only that file is admitted, in
    the study or in one of its pieces (`segment-*`), where
    `how_the_run_ended` reads it, and only when asked for by its own name:
    a result linked to it is refused, or its pid, command and host would be
    read as that result.
    """
    from fastmdxplora import record_access
    from fastmdxplora.gui.server import _private, _public_records
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    public = _public_records(root)

    def admitted(path: Path) -> bool:
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            return False
        if parts and parts[-1] == RUN_PROCESS_FILE and (
                len(parts) == 1
                or (len(parts) == 2 and parts[0].startswith("segment-")
                    and not _private(parts[:1]))):
            return True
        if not public(path):
            return False
        try:
            # Where it leads, as the held read found it (`record_access`).
            return record_access.resolved(path).name != RUN_PROCESS_FILE
        except (OSError, RuntimeError):
            return False

    return admitted


def problems_payload(root: str | Path, *,
                     artifacts: list[dict[str, Any]] | None = None,
                     admit_evidence: bool = False) -> dict[str, Any]:
    """Read existing records within the open study's public file boundary.

    Explicit ``artifacts`` further restricts evidence controls. The route
    may instead admit just the candidates used by this panel, without
    recursively listing the study. Missing records remain unknown, and
    ``read`` and ``unavailable`` name the records read and those that
    could not be.
    """
    root = Path(root).resolve()
    key = study_key(root)
    if not root.is_dir():
        return {"ok": True, "study": key, "state": "gone", "items": [], "available": False,
                "read": [], "unavailable": [],
                "notes": ["The study's folder is no longer there, so its records "
                          "cannot be read."]}
    try:
        with confined_records(root, admitted_for_problems(root)) as scope:
            answer = _read_problems(root, artifacts=artifacts, admit_evidence=admit_evidence)
    except OSError:
        _LOG.debug("Could not open the study's folder for reading", exc_info=True)
        return {"ok": True, "study": key, "state": "unreadable", "items": [],
                "available": False, "read": [], "unavailable": [],
                "notes": ["The study's folder could not be opened for reading, so its "
                          "problems are unknown."]}
    if scope.denied:
        # Even if a legacy reader caught the I/O refusal and used a
        # fallback, never present that fallback as a checked aggregate.
        return {"ok": True, "study": key, "items": [], "available": False,
                "read": [], "unavailable": sorted(scope.denied),
                "notes": ["Problems were not read because a source or link could not be "
                          "confined to the study's allowed files: "
                          + ", ".join(sorted(scope.denied)) + "."]}
    return answer


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _more_sampling(shortfall: Any) -> str:
    """How much more a withheld mean asked for, in its own record's words
    (`statistics.Shortfall`, rebuilt from `Shortfall.as_record`); empty
    where it recorded none."""
    if not isinstance(shortfall, dict):
        return ""
    from fastmdxplora.statistics import Shortfall

    target = _number(shortfall.get("target_independent"))
    have = _number(shortfall.get("independent"))
    inefficiency = _number(shortfall.get("statistical_inefficiency"))
    frames = _number(shortfall.get("more_frames"))
    more_ns = _number(shortfall.get("more_ns"))
    if (target is None or target <= 0 or have is None or inefficiency is None
            or frames is None or frames <= 0 or frames != int(frames)):
        return ""
    return str(Shortfall(target=target, have=have, inefficiency=inefficiency,
                         more_frames=int(frames), more_ns=more_ns,
                         resolved=not shortfall.get("lower_bound")))


def _quantity_record(findings: dict[str, Any], key: str) -> dict[str, Any] | None:
    """The record a quantity of the Overview was read from (`overview_of`)."""
    found = findings.get(key)
    if found is None and "." in key:
        group, inner = key.split(".", 1)
        found = findings.get(group, {}).get(inner) if isinstance(
            findings.get(group), dict) else None
    return found if isinstance(found, dict) else None


def _plain(message: Any) -> str:
    """A recorded warning as a person reads it: without the runner's own
    "warning:" before it (`runner.py`), starting with a capital."""
    said = str(message or "").strip()
    if said.lower().startswith("warning:"):
        said = said[len("warning:"):].strip()
    return said[:1].upper() + said[1:]


def _event_warnings(text: str) -> list[str]:
    """The warnings of one narration (`TelemetryWriter.event` writes each
    as its time, level and message, separated by tabs). A line with no tab
    goes with the event above it, as `read_events` reads it."""
    events: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            events.append((parts[1].strip().lower(), [parts[2]]))
        elif events and line.strip():
            events[-1][1].append(line)
    return ["\n".join(lines) for level, lines in events if level == "warning"]


def _log_warnings(text: str) -> list[str]:
    """The WARNING records of the study's log that its records still stand
    for, each with any lines that continue it, up to the next record or a
    new run's mark.

    Every run appends to the log after its mark (`_NEW_RUN`), and a run
    that does a phase again replaces it and every phase after it
    (`replaced.make_way`). So a warning written in a phase that a later run
    started again, or after one, is of records since replaced. What a run
    logs before its first phase goes with that phase, as setup's padding
    warning goes with setup. A run that started no phase replaced nothing,
    and its warnings go only once a later run sets the study up again.
    """
    from fastmdxplora.replaced import ORDER

    # Per run, its parts: the phase each was written in, as its place in
    # ORDER (None before any phase), and its warnings.
    runs: list[list[list[Any]]] = [[[None, []]]]
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith(_NEW_RUN):
            runs.append([[None, []]])
            current = None
            continue
        matched = _LOG_RECORD.match(line)
        if matched:
            started = _PHASE_STARTS.match(matched.group("message"))
            if started and started.group("phase") in ORDER:
                rank = ORDER.index(started.group("phase"))
                if runs[-1][-1][0] is None:
                    runs[-1][-1][0] = rank
                else:
                    runs[-1].append([rank, []])
                current = None
                continue
            level = matched.group("plain") or matched.group("pretty")
            current = [matched.group("message")] if level == "WARNING" else None
            if current is not None:
                runs[-1][-1][1].append(current)
        elif line.startswith("==="):
            current = None
        elif current is not None and line.strip():
            current.append(line)
    found: list[list[str]] = []
    for index, parts in enumerate(runs):
        # The earliest phase any later run started again.
        later = [rank for run in runs[index + 1:] for rank, _ in run if rank is not None]
        for rank, warnings in parts:
            if not later or min(later) > (rank or 0):
                found.extend(warnings)
    return ["\n".join(lines) for lines in found]


def _read_problems(root: Path, *, artifacts: list[dict[str, Any]] | None,
                   admit_evidence: bool) -> dict[str, Any]:
    notes: list[str] = []
    items: list[dict[str, str]] = []
    read: list[str] = []
    unavailable: list[str] = []
    #: Records that exist and could not be read, not only ones not there.
    troubled = False
    allowed = {entry.get("path") for entry in artifacts or []
               if isinstance(entry, dict) and isinstance(entry.get("path"), str)}
    available = False

    def add(identifier: str, title: str, reason: str, fix: str, *,
            code: str = "", files: tuple[str, ...] = ()) -> dict[str, str]:
        item = {"id": identifier, "title": title, "reason": reason, "fix": fix or NO_REMEDY}
        if code:
            item["code"] = code
        for path in files:
            if (path in allowed or (artifacts is None and admit_evidence)) and is_file(root / path):
                item["file"] = path
                break
        items.append(item)
        return item

    def unknown(relative: str, note: str, *, missing: bool = False) -> None:
        nonlocal troubled
        troubled = troubled or not missing
        if relative not in unavailable:
            unavailable.append(relative)
        notes.append(note)

    def text_of(relative: str, missing: str = "") -> str | None:
        try:
            text = read_text(root / relative, encoding="utf-8", errors="replace")
        except FileNotFoundError:
            if missing:
                unknown(relative, f"{relative} is not there; {missing}", missing=True)
            return None
        except OSError:
            unknown(relative, f"{relative} could not be read; its problems are unknown.")
            return None
        if relative not in read:
            read.append(relative)
        return text

    def record(relative: str, missing: str = "") -> dict[str, Any] | None:
        try:
            value = json.loads(read_text(root / relative, encoding="utf-8"))
        except FileNotFoundError:
            if missing:
                unknown(relative, f"{relative} is not there; {missing}", missing=True)
            return None
        except (OSError, ValueError):
            unknown(relative, f"{relative} could not be read; its problems are unknown.")
            return None
        if not isinstance(value, dict) or not value:
            unknown(relative, f"{relative} has no usable record; its problems are unknown.")
            return None
        if relative not in read:
            read.append(relative)
        return value

    def listed(text: str) -> bool:
        return any(_plain(item["reason"]) == text for item in items)

    # The run as it stands, as the Overview and the sidebar say it: a run
    # whose record says it is going but whose process is gone, or silent
    # for a day, ended; a study carried on in pieces (`segment-001/`) is
    # where its newest piece is. A stop or an interruption is a problem
    # only while this says so.
    status: dict[str, Any] = {}
    try:
        status = status_as_it_stands(root, include_campaign=False)
        pieces = _pieces_of(root)
    except Exception:  # noqa: BLE001 - partial records must not hide the other problems
        _LOG.debug("Could not read recorded run health", exc_info=True)
        unknown(f"simulation/{STATUS_FILE}",
                "Run health could not be read; its problems are unknown.")
        pieces = []
    now = str(status.get("status") or "").lower()
    #: The stop's own words, wherever they were written: listed as the stop
    #: while the run stands stopped, and not at all once it is carried on.
    stop_words: set[str] = set()

    def stop_said(*texts: Any) -> None:
        stop_words.update(_plain(text) for text in texts if isinstance(text, str) and text.strip())

    def own_to_a_stop(text: str) -> bool:
        return (text in stop_words or bool(_PHASE_STOPPED.fullmatch(text))
                or bool(_STOP_HEARD.match(text)))

    if now in _GOING_STATUSES and not exists(root / "manifest.json"):
        manifest = None
        notes.append(WRITTEN_AT_THE_END)
    else:
        manifest = record("manifest.json", "it is written when a run ends, so refusals and "
                                           "phase results are unknown.")
    phases = manifest.get("phases") if manifest else None
    if manifest is not None and (not isinstance(phases, list) or not phases):
        unknown("manifest.json", "The manifest has no readable phase records.")
    for index, phase in enumerate(phases if isinstance(phases, list) else []):
        if not isinstance(phase, dict) or not phase.get("status"):
            unknown("manifest.json", "A manifest phase has no readable status.")
            continue
        available = True
        refusal = phase.get("refusal")
        code = str(refusal.get("code") or "") if isinstance(refusal, dict) else ""
        said = str(phase["status"]).lower()
        carried = phase.get("carried_on_from")
        if isinstance(carried, dict):
            # A stop that was carried on and joined (`resume._production_carried_on`).
            earlier = carried.get("refusal")
            stop_said(carried.get("message"),
                      earlier.get("message") if isinstance(earlier, dict) else None)
        if code in _STOP_CODES or said in {"stopped", "interrupted"}:
            stop_said(phase.get("message"),
                      refusal.get("message") if isinstance(refusal, dict) else None)
            if now and (now not in {"stopped", "interrupted"} or status.get("piece")):
                # The run as it stands is going again, has finished, or
                # ended again in the piece that carries it on: this stop is
                # not its state, and the run's health below says what is.
                continue
        failed = said in _PHASE_SAID
        if not failed and not refusal:
            continue
        name = str(phase.get("name") or phase.get("phase") or "Study")
        reason = str(phase.get("message") or "No reason was recorded.")
        fix = NO_REMEDY
        if isinstance(refusal, dict):
            reason = str(refusal.get("message") or phase.get("message") or reason)
            try:
                # A retry depends on whether the run wrote a checkpoint, so
                # its remedy is asked of the study (read, never priced).
                # Otherwise the registry alone answers, without a sampling
                # estimate or a Config built from the study.
                retry = code_for(code).retryable
                fix = (remedy_for(refusal, study=root, price=False) if retry
                       else remedy_for(refusal)).fix
            except Exception:  # noqa: BLE001 - retain the refusal when its remedy cannot be read
                _LOG.debug("Could not read a recorded phase remedy", exc_info=True)
                notes.append(f"The remedy for {name} could not be read.")
        elif refusal is not None:
            notes.append(f"The refusal record for {name} could not be read.")
        word = (code.rsplit(".", 1)[-1] if code in _STOP_CODES
                else _PHASE_SAID.get(said, "refused" if refusal else said))
        add(f"phase:{index}", f"{name.capitalize()} {word}", reason, fix,
            code=code, files=("manifest.json",))

    raw_status = record(f"simulation/{STATUS_FILE}", "the run's health is unknown.")
    if raw_status is not None and str(raw_status.get("status") or "").lower() in {
            "stopped", "interrupted"}:
        stop_said(raw_status.get("latest_error"))
    #: Where the run's health is read from: the newest piece, where the
    #: study stands as that piece's record says (`telemetry._as_one_run`).
    health_in = "simulation"
    for piece in pieces:
        # Each piece the study was carried on in keeps its own record.
        own = record(f"{piece.name}/simulation/{STATUS_FILE}")
        if own is not None and str(own.get("status") or "").lower() in {"stopped", "interrupted"}:
            stop_said(own.get("latest_error"))
        if (own is not None and piece.name == status.get("piece")
                and own.get("last_update_timestamp") == status.get("last_update_timestamp")
                and str(status.get("stage") or "").lower() == "production"):
            health_in = f"{piece.name}/simulation"
    try:
        metrics = read_metrics(root)
        for folder in [root, *pieces]:
            relative = (folder / "simulation" / METRICS_FILE).relative_to(root).as_posix()
            if is_file(root / relative):
                read.append(relative)
        health = analyze_health(status, metrics)
        state = health.get("state")
        if now:
            available = True
        elif raw_status is not None:
            unknown(f"simulation/{STATUS_FILE}", "The telemetry record has no run status.")
        if state in {"warning", "failed", "stopped", "interrupted"}:
            message = str(health.get("message") or "No reason was recorded.")
            explanation = str(health.get("explanation") or "")
            reason, fix = message, _HEALTH_REMEDIES.get(explanation, NO_REMEDY)
            if state in {"stopped", "interrupted"}:
                stop_said(message)
                if status.get("stage") in {"analysis", "report"}:
                    fix = "Use Analyze again or Write it again on the Overview for the phase that stopped."
                else:
                    # Checkpoint-aware wording, without pricing an action
                    # that the read-only panel never offers to run.
                    fix = remedy_for({"code": f"simulation.run.{state}", "message": message},
                                     study=root, price=False).fix
            elif explanation and explanation not in _HEALTH_REMEDIES:
                # What the telemetry says of it is why, not what fixes it.
                reason = f"{message} {explanation}"
            duplicate = next((item for item in items if
                item["reason"].strip() == message.strip()
                or (state in {"stopped", "interrupted"}
                    and item.get("code") == f"simulation.run.{state}")), None)
            if duplicate is not None and state in {"stopped", "interrupted"}:
                duplicate["fix"] = fix
            if duplicate is None:
                add(f"health:{state}", str(health.get("headline") or f"Run {state}"),
                    reason, fix, files=(f"{health_in}/{STATUS_FILE}", f"{health_in}/{METRICS_FILE}"))
        elif state == "unknown" and raw_status is not None:
            notes.append(str(health.get("message") or "Run health is unavailable."))
    except Exception:  # noqa: BLE001 - partial records must not hide the other problems
        _LOG.debug("Could not read recorded run health", exc_info=True)
        unknown(f"simulation/{STATUS_FILE}",
                "Run health could not be read; its problems are unknown.")

    # Each warning the run wrote in its narration (runner.py and
    # orchestrator.py write them with `TelemetryWriter.event`), once, from
    # the study and from each piece it was carried on in.
    ran = raw_status is not None or any(
        isinstance(phase, dict) and phase.get("name") == "simulation"
        for phase in (phases if isinstance(phases, list) else []))
    warned: set[str] = set()
    for folder in [root, *pieces]:
        relative = (folder / "simulation" / EVENTS_FILE).relative_to(root).as_posix()
        text = text_of(relative, "the run's warnings are unknown."
                       if folder != root or ran else "")
        for message in _event_warnings(text or ""):
            said = _plain(message)
            if not said or said in warned:
                continue
            warned.add(said)
            if own_to_a_stop(said) or listed(said):
                continue
            add(f"warning:{len(warned)}", "Recorded run warning", said, NO_REMEDY,
                files=(relative,))

    # The warnings the study's log holds (setup growing the padding, for
    # one), each once, and none the narration already said.
    logged: set[str] = set()
    for folder in [root, *pieces]:
        relative = (folder / LOG).relative_to(root).as_posix()
        text = text_of(relative, "the warnings it would hold are unknown."
                       if folder != root or ran or manifest is not None else "")
        for message in _log_warnings(text or ""):
            said = _plain(message)
            if not said or said in logged or said in warned:
                continue
            logged.add(said)
            if own_to_a_stop(said) or listed(said):
                continue
            add(f"log:{len(logged)}", "Warning in the study's log", said, NO_REMEDY,
                files=(relative,))

    _report_refusals(root, add, unknown, read)
    if _not_equilibrated(root, phases, add, unknown, notes, read):
        available = True

    analysis_expected = is_dir(root / "analysis") or any(
        isinstance(phase, dict) and str(phase.get("name") or "") == "analysis"
        for phase in (phases if isinstance(phases, list) else []))
    analysis = record("analysis/analysis_manifest.json",
                      "a failed analysis would not be known." if analysis_expected else "")
    results = analysis.get("results") if analysis else None
    invalid_names = isinstance(results, dict) and any(
        not NAME.fullmatch(name) for name in results)
    if analysis is not None and (not isinstance(results, dict) or invalid_names):
        unknown("analysis/analysis_manifest.json",
                "The analysis manifest has unreadable result entries.")
    elif isinstance(results, dict) and any(not isinstance(result, dict)
                                          or not result.get("status")
                                          for result in results.values()):
        unknown("analysis/analysis_manifest.json",
                "The analysis manifest has unreadable result entries.")
    try:
        # Do not follow an analysis name outside its folder, even in an old
        # or hand-edited manifest. The existing reader assumes valid names.
        overview = {"rows": []} if invalid_names else overview_of(root, include_time=False, include_data=False)
        rows = overview.get("rows") or []
        if not rows:
            notes.append("No analysis results are available to check.")
        for row in rows:
            name, title = row["analysis"], row["title"]
            options = f"analysis/{name}/options.json"
            found = record(options)
            if found is not None and not isinstance(found.get("findings"), dict):
                unknown(options, f"{title} has no readable findings record.")
                found = None
            if found is not None and "mean" in found["findings"] and not isinstance(
                    found["findings"]["mean"], dict):
                unknown(options, f"{title} has an unreadable mean record in {options}; "
                                 "its problems are unknown.")
            if found is None and row.get("status") not in {"failed", "skipped", "running"}:
                unknown(options, f"{title} has no readable findings record.")
            available = available or found is not None or row.get("status") == "failed"
            if row.get("status") == "failed":
                add(f"analysis:{name}:failed", f"{title} failed",
                    str(row.get("message") or "No reason was recorded."),
                    NO_REMEDY, files=("analysis/analysis_manifest.json", options))
            for quantity in row.get("quantities") or []:
                earlier = bool(quantity.get("judged_earlier"))
                if quantity.get("determined") and not earlier:
                    continue
                label = quantity.get("label") or title
                named = title if label == title else f"{title} / {label}"
                if earlier:
                    add(f"analysis:{name}:{quantity['key']}", f"{named}: earlier rules",
                        "This mean was judged by earlier rules; its recorded verdict "
                        "has not been checked under the current rules.",
                        "Analyze again to apply the current rules.", files=(options,))
                    continue
                why = str(quantity.get("why") or "No reason was recorded.")
                own_record = _quantity_record(found["findings"], str(quantity["key"])) \
                    if found is not None else None
                fix = ""
                if own_record is not None and str(own_record.get("not_a_measurement") or "") == why:
                    # The record's own reason, so its own remedy: how much
                    # more sampling it asked for, where it asked.
                    fix = _more_sampling(own_record.get("shortfall"))
                # The same in every frame is said as the Overview says it,
                # not as a value missing.
                verdict = (str(quantity.get("said")) if quantity.get("constant")
                           and quantity.get("said") else "not determined")
                add(f"analysis:{name}:{quantity['key']}", f"{named}: {verdict}",
                    why, fix, files=(options,))
    except Exception:  # noqa: BLE001 - an unreadable analysis must not erase run problems
        _LOG.debug("Could not read recorded analysis results", exc_info=True)
        unknown("analysis", "Some analysis results could not be read; their problems "
                            "are unknown.")

    campaign = is_file(root / "batch_manifest.json")
    if campaign:
        notes.append("This list covers the open folder's records. Open each campaign run "
                     "to check its health, refusals and analysis results.")
    if not read and not items and not troubled and not campaign:
        # A folder a study is about to be in, or one just begun: nothing
        # written yet is not a record missing.
        return {"ok": True, "study": study_key(root), "state": "empty", "items": [],
                "notes": [NO_RECORDS_YET], "available": False, "read": [],
                "unavailable": []}
    if not available:
        notes.append("No usable run or analysis records are available.")
    return {"ok": True, "study": study_key(root), "items": items,
            "notes": list(dict.fromkeys(notes)), "available": available,
            "read": list(dict.fromkeys(read)), "unavailable": unavailable}


def _report_refusals(root: Path, add: Callable[..., Any], unknown: Callable[..., None],
                     read: list[str]) -> None:
    """Each format the report could not produce, with its refusal code and
    words (`report/run.py` writes them only when one was not produced, and
    removes the record once every format is)."""
    try:
        value = json.loads(read_text(root / NOT_PRODUCED, encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, ValueError):
        unknown(NOT_PRODUCED, f"{NOT_PRODUCED} could not be read; what the report could "
                              "not produce is unknown.")
        return
    entries = value if isinstance(value, list) else None
    if not entries or not all(isinstance(entry, dict) and entry.get("code")
                              and entry.get("reason") for entry in entries):
        unknown(NOT_PRODUCED, f"{NOT_PRODUCED} has no usable record; what the report could "
                              "not produce is unknown.")
        return
    read.append(NOT_PRODUCED)
    for index, entry in enumerate(entries):
        code, reason = str(entry["code"]), str(entry["reason"])
        artifact = str(entry.get("artifact") or "A report format")
        try:
            fix = remedy_for({"code": code, "message": reason}).fix
        except Exception:  # noqa: BLE001 - the refusal is listed without a remedy
            _LOG.debug("Could not read a report refusal's remedy", exc_info=True)
            fix = NO_REMEDY
        add(f"report:not-produced:{index}", f"Report: {artifact} not produced", reason,
            fix, code=code, files=(NOT_PRODUCED,))


def _not_equilibrated(root: Path, phases: Any, add: Callable[..., Any],
                      unknown: Callable[..., None], notes: list[str],
                      read: list[str]) -> bool:
    """The report's own verdict that an observable has not equilibrated.

    The report holds each run to `report.convergence.CHECKS`, judged by
    `assess_run` over the analyses' records when the report was written,
    and writes the verdicts as a table in ``report/report.md``. That table
    is the only record of them, so it is read as written: a check failed
    there is listed with the observables it names, and nothing is judged
    again here. Returns whether the report's checks were read.
    """
    from fastmdxplora.report.convergence import CHECKS

    asked = any(isinstance(phase, dict) and phase.get("name") == "report"
                and "report.md" in (phase.get("artifacts") or [])
                for phase in (phases if isinstance(phases, list) else []))
    try:
        text = read_text(root / REPORT, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        if asked:
            unknown(REPORT, f"{REPORT} is not there; whether each observable equilibrated "
                            "is unknown.", missing=True)
        return False
    except OSError:
        unknown(REPORT, f"{REPORT} could not be read; whether each observable "
                        "equilibrated is unknown.")
        return False
    try:
        written = stat(root / REPORT).st_mtime
        analysed = stat(root / "analysis" / "analysis_manifest.json").st_mtime
    except OSError:
        analysed = None
    if analysed is not None and analysed > written:
        notes.append(f"{REPORT} was written before the analyses were last run, so its "
                     "convergence checks are not read as current. Write the report "
                     "again to check them.")
        return False
    read.append(REPORT)
    said = next(check for key, check, _short in CHECKS if key == "equilibrated")
    row = f"| {said[0].upper()}{said[1:]} | **failed** | "
    detail = next((line[len(row):].rstrip().rstrip("|").strip()
                   for line in text.splitlines() if line.startswith(row)), None)
    if detail is None:
        return True
    moving = detail.partition(": ")[2] if detail.startswith("still moving: ") else ""
    # The report's own sentence for it, where it wrote one.
    reason = next((line[2:].strip() for line in text.splitlines()
                   if line.startswith("- Still moving in one direction: ")),
                  f"The report's check that {said} failed: {detail}.")
    add("report:not-equilibrated", "Not equilibrated" + (f": {moving}" if moving else ""),
        reason, NO_REMEDY, files=(REPORT,))
    return True
