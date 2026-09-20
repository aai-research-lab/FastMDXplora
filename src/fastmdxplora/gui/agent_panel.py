"""The browser's two calls into the agent.

Both are thin, and deliberately so. The GUI is one more door onto
:func:`fastmdxplora.agent.propose_config` -- the same function the CLI
calls and the same one a notebook imports -- so nothing here decides
whether a config is acceptable. The validator does that, as it does for a
config somebody typed by hand.

The key is accepted here and handed to
:func:`fastmdxplora.agent.save_choice`, which puts it in a file of its
own. It is never echoed back to the browser, never written into a config,
and never logged. Server-side rather than in the page because a browser
cannot hold a secret: anything the page keeps is readable by anything else
the page runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["model_endpoint", "propose_endpoint", "run_endpoint"]


def model_endpoint(payload: dict[str, Any]) -> dict[str, Any]:
    """Read or set which model to ask.

    With no ``provider``, reports what is set. With one, stores the choice
    and, if a key came with it, the key.
    """
    from fastmdxplora.agent.models import (
        PROVIDERS, ModelChoice, load_choice, model_path, save_choice,
    )

    if not payload.get("provider"):
        current = load_choice()
        # Ask the provider now, if there is already a key to ask with. The
        # list used to be fetched only when a choice was saved, so opening
        # Settings showed the written fallback and a model chosen from it
        # could 404 -- which is how `claude-opus-4-1` reached somebody.
        live: dict[str, list] = {}
        if current is not None:
            from fastmdxplora.agent.models import list_models

            try:
                found = list(list_models(current))
            except Exception:  # noqa: BLE001 - a fallback list still works
                found = []
            if found:
                live[current.provider] = found
        return {
            "ok": True,
            "providers": [
                {"id": name, "label": spec["label"],
                 "default_model": spec["default_model"],
                 "models": live.get(name) or list(spec.get("models") or ()),
                 "environment_variable": spec["env"],
                 "needs_url": not spec["url"],
                 "examples": [
                     {"label": label, "url": url, "model": model}
                     for label, url, model in spec.get("examples", ())
                 ]}
                for name, spec in PROVIDERS.items()
            ],
            # What is set, never the key.
            "current": current.as_record() if current else None,
            "stored_at": str(model_path()),
        }

    provider = str(payload["provider"])
    if provider not in PROVIDERS:
        return {"ok": False,
                "error": f"No provider called {provider!r}.",
                "code": "config.option.not_permitted"}

    base_url = str(payload.get("base_url") or "")
    if not PROVIDERS[provider]["url"] and not base_url:
        return {"ok": False,
                "error": ("An OpenAI-compatible server needs a base URL, "
                          "such as http://localhost:11434/v1 for Ollama."),
                "code": "config.option.missing_companion"}

    model = str(payload.get("model") or PROVIDERS[provider]["default_model"])
    if not model:
        return {"ok": False, "error": "A model name is needed.",
                "code": "config.option.missing_companion"}

    save_choice(ModelChoice(provider, model, base_url),
                key=str(payload.get("api_key") or ""))
    # Ask the provider what it actually has, now that there is a key to ask
    # with. The written list is what to show before this can be answered.
    from fastmdxplora.agent.models import list_models

    try:
        offered = list(list_models(ModelChoice(provider, model, base_url)))
    except Exception:  # noqa: BLE001 - a stale list beats a broken save
        offered = list(PROVIDERS[provider].get("models") or ())
    # The choice, not the key. A browser that never receives one cannot
    # leak one.
    return {"ok": True, "models": offered,
            "current": ModelChoice(provider, model, base_url).as_record()}


def propose_endpoint(payload: dict[str, Any],
                     runtime: Any = None) -> dict[str, Any]:
    """A sentence in, a config out, or the refusal that stopped it.

    The attempts come back whole rather than as a count. They are the only
    visible sign that anything checked the config, and a reader watching
    the model correct itself learns the config language while they wait.
    """
    import yaml

    from fastmdxplora.agent import completion_for, propose_config
    from fastmdxplora.refusals import StudyError, refusal_of

    request = str(payload.get("request") or "").strip()
    if not request:
        return {"ok": False, "error": "Describe the study you want.",
                "code": "config.option.missing_companion"}

    mode = str(payload.get("agent") or "assisted")
    phases = payload.get("phases") or ["setup", "simulation"]
    try:
        complete = completion_for()
    except StudyError as exc:
        found = refusal_of(exc)
        return {"ok": False, "error": found.message, "code": found.code}

    # The conversation, the current config and the run, so the Agent can
    # modify rather than restart, and answer rather than write. The
    # browser sends the first two; the server knows the third.
    history = [
        {"role": str(h.get("role") or "user"), "text": str(h.get("text") or "")}
        for h in (payload.get("history") or []) if isinstance(h, dict)
    ][-12:]
    current = payload.get("current_config")
    current = str(current) if current else None
    # Files attached to this message: the browser sends what read_attachment
    # returned, name and text; the bytes never go into the transcript.
    attachments = [
        {"name": str(a.get("name") or "file"), "text": str(a.get("text") or ""),
         "truncated": bool(a.get("truncated"))}
        for a in (payload.get("attachments") or []) if isinstance(a, dict) and a.get("text")
    ][:6]
    try:
        proposal = propose_config(
            request, complete, phases=list(phases),
            max_cycles=int(payload.get("attempts") or 4),
            history=history or None, current_config=current,
            run_status=_run_status(runtime), attachments=attachments or None)
    except StudyError as exc:
        found = refusal_of(exc)
        return {"ok": False, "error": found.message, "code": found.code}

    attempts = [
        {"number": attempt.number,
         "refusal": (attempt.refusal.as_dict() if attempt.refusal else None)}
        for attempt in proposal.attempts
    ]
    if proposal.action:
        # An instruction. The browser carries it out through the same
        # door the button uses; the server only names it. For "stop" it
        # adds where the run is, so the confirmation can say what would
        # be lost.
        where = ""
        if proposal.action == "stop":
            where = _where_the_run_is(runtime)
        return {"ok": False, "action": proposal.action, "where": where,
                "attempts": attempts}
    if proposal.answer:
        # A question was asked, not a study. A paragraph back.
        return {"ok": False, "answer": proposal.answer, "attempts": attempts}
    if proposal.question:
        # Not a failure. The request is short of something only the person
        # can supply, and the honest answer is to say what.
        return {"ok": False, "question": proposal.question,
                "code": "config.option.missing_companion",
                "error": proposal.question, "attempts": attempts}
    if not proposal.accepted:
        last = proposal.refusal
        return {"ok": False, "attempts": attempts, "cycles": proposal.cycles,
                "error": last.message if last else "No config was produced.",
                "code": last.code if last else "unclassified"}

    # The mode travels with the config rather than beside it, so it reaches
    # resolved_config.yml and the manifest like any other setting.
    config = dict(proposal.config)
    config["agent"] = mode
    # And which model, not only that one was used. `agent: assisted` says a
    # model was involved; this says which, so the record identifies the
    # software rather than the category.
    from fastmdxplora.agent import load_choice

    chosen = load_choice()
    if chosen is not None:
        config["agent_model"] = f"{chosen.provider}/{chosen.model}"
    return {
        "ok": True,
        "cycles": proposal.cycles,
        "attempts": attempts,
        "config": config,
        "yaml": yaml.safe_dump(config, sort_keys=False),
    }


def run_endpoint(payload: dict[str, Any], runtime: Any,
                 *, dashboard_url: str | None = None) -> dict[str, Any]:
    """Start the study the panel just drafted, on this machine.

    The GUI runs on the user's own hardware, so there is no reason a mode
    that runs should be a command-line-only workflow. `assisted` still
    loads into the form, because the point of that mode is that a person
    reads it first; the other two start here.

    `autonomous` needs a budget, for the same reason the CLI does: it runs
    without being shown to anybody, so a ceiling is the only thing left
    that can stop it. The check happens after setup, where the solvated
    particle count -- and so the cost -- is first known.

    Through `launch_from_config`, which is the GUI's own door for running
    what a config describes rather than what a form was wired for. Nothing
    here is a second way of starting a study.
    """
    config = payload.get("config")
    if not isinstance(config, dict) or not config:
        return {"ok": False, "code": "config.option.missing_companion",
                "error": "No config to run. Write one first."}

    mode = str(config.get("agent") or "assisted")
    try:
        hours = float(payload.get("budget_hours"))
    except (TypeError, ValueError):
        hours = 0.0

    # Required for `autonomous`, honoured in every mode. A budget stands in
    # for a human, which is why the mode with nobody watching must have
    # one -- and a ceiling is never the wrong thing to have on a study that
    # will run for days, so it is offered whether or not it is demanded.
    if mode == "autonomous" and hours <= 0:
        return {
            "ok": False,
            "code": "environment.budget.absent",
            "error": ("An autonomous run is not shown to you before it "
                      "starts, so a GPU-hour budget is the only thing left "
                      "that can stop it. Give one above."),
        }
    if hours > 0:
        # Carried on the config so it reaches resolved_config.yml and the
        # manifest, rather than living only in this request.
        config = dict(config)
        config["budget_hours"] = hours

    # The config goes to the launch as itself. It used to be translated
    # into the builder's form state first, and the translation and the
    # builder disagreed about where phase settings lived, so every run
    # from here ran with defaults. One source of truth: the config the
    # Agent wrote is the config that launches, rendered by the same
    # render_config the form's own path ends in.
    if payload.get("output_dir"):
        config = dict(config)
        config["output"] = str(payload["output_dir"])
    try:
        return runtime.launch_from_config(None, config=config,
                                          dashboard_url=dashboard_url)
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        from fastmdxplora.refusals import refusal_of

        found = refusal_of(exc)
        return {"ok": False, "error": found.message, "code": found.code}


def _run_status(runtime: Any) -> str | None:
    """What the run is doing, in a few lines a model can read.

    So "why did it stop?" can be answered from what happened rather than
    from a guess. Stage, status, the last error if there was one, the
    health verdict if there is one. Nothing the sidebar does not already
    show a person.
    """
    if runtime is None or not hasattr(runtime, "snapshot"):
        return None
    try:
        snap = runtime.snapshot() or {}
    except Exception:  # noqa: BLE001 - context, not load-bearing
        return None
    if not snap.get("active_run"):
        return "No run is active."
    lines = [f"status: {snap.get('status') or 'unknown'}"]
    if snap.get("process_running"):
        lines.append("the process is running")
    if snap.get("error"):
        lines.append(f"last error: {str(snap['error'])[:400]}")
    try:
        from fastmdxplora.gui.telemetry import analyze_health, read_status

        status = read_status(runtime.active_root) or {}
        if status.get("stage"):
            lines.append(f"stage: {status['stage']}")
        # The numbers the sidebar shows, so "how far along?" is answered
        # with a step and a time rather than "I have only the stage". The
        # Agent said exactly that while the sidebar read 334,000 of
        # 350,000 and three minutes left.
        step, total = status.get("current_step"), status.get("total_planned_steps")
        if isinstance(step, (int, float)) and isinstance(total, (int, float)) and total > 0:
            lines.append(f"step: {int(step):,} of {int(total):,} "
                         f"({100.0 * step / total:.1f}% complete)")
            elapsed = status.get("elapsed_wall_time_s")
            if isinstance(elapsed, (int, float)) and step > 0:
                remaining = elapsed * (total / step - 1.0)
                lines.append(f"elapsed: {_hms(elapsed)}; about {_hms(remaining)} left")
        sim_ns = status.get("simulation_time_completed_ns")
        if isinstance(sim_ns, (int, float)):
            # Equilibration included. "0.7 ns" here was read back as a
            # production length of 0.7 ns when the config said 0.5; the
            # config below is where the production length lives.
            lines.append(f"simulated so far, equilibration included: {sim_ns:.3f} ns")
        speed = status.get("ns_per_day") or status.get("speed")
        if isinstance(speed, (int, float)) and speed > 0:
            lines.append(f"speed: {speed:.2f} ns/day")
        health = analyze_health(status, [])
        if health.get("state"):
            lines.append(f"health: {health['state']}"
                         + (f" -- {health['message']}" if health.get("message") else ""))
    except Exception:  # noqa: BLE001
        pass
    used = _config_the_run_used(getattr(runtime, "active_root", None))
    if used:
        lines.append("")
        lines.append("the config this run used (the short form, as written):")
        lines.append(used)
    cont = _continuation_summary(getattr(runtime, "active_root", None))
    if cont:
        lines.append("")
        lines.append(cont)
    results = _results_summary(getattr(runtime, "active_root", None))
    if results:
        lines.append("")
        lines.append(results)
    return "\n".join(lines)


def _config_the_run_used(root: Any) -> str:
    """The active run's own config, so "the same settings as that one" has
    something to copy from.

    The Agent lost the previous study's config the moment it wrote a new
    one, and said "I have no chignolin study in this conversation" while
    the chignolin run was the active study with its resolved config on
    disk. It reads that file now. The short form, not the full dump:
    the full one is a hundred lines of defaults, and what a person means
    by "the same settings" is what was decided.
    """
    if not root:
        return ""
    path = Path(root) / "resolved_config.yml"
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    # Drop the header comments and cap the length; a config that runs to
    # pages is not something to paste into every prompt.
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    body = body.strip()
    if len(body) > 4000:
        body = body[:4000] + "\n# … (truncated)"
    return body



def _results_summary(root: Any) -> str:
    """What the analyses found, in a few lines a model can read.

    The same numbers the Report page shows, from the same computation:
    for each analysis, the mean, its standard error, how many effective
    samples the trajectory held and how many frames were discarded as
    not yet equilibrated. "Is the RMSD converged?" is answerable from
    that -- the effective sample count against the ten a mean needs --
    and not from a figure the model cannot see.
    """
    if not root:
        return ""
    from pathlib import Path

    base = Path(root)
    analysis = base / "analysis"
    if not analysis.is_dir():
        return ""
    import json

    from fastmdxplora.statistics import MINIMUM_EFFECTIVE_SAMPLES

    rows: list[str] = []
    for options in sorted(analysis.glob("*/options.json")):
        try:
            data = json.loads(options.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        name = str(data.get("analysis") or options.parent.name)
        findings = data.get("findings") or {}
        if not isinstance(findings, dict):
            continue
        parts: list[str] = []
        for key, f in findings.items():
            if not isinstance(f, dict) or "mean" not in f:
                continue
            mean = f.get("mean")
            se = f.get("standard_error")
            n_eff = f.get("effective_samples")
            discard = f.get("discard")
            n = f.get("n_frames")
            # The findings key is usually "mean"; naming it twice reads as
            # a stutter. Name the key only when it says something else.
            label = "" if key == "mean" else f"{key} "
            piece = (f"{label}mean {mean:.4g}" if isinstance(mean, (int, float))
                     else f"{label}mean {mean}")
            if isinstance(se, (int, float)):
                piece += f" \u00b1 {se:.2g} (s.e.)"
            if isinstance(n_eff, (int, float)):
                piece += f", {n_eff:.1f} effective samples"
                if n_eff < MINIMUM_EFFECTIVE_SAMPLES:
                    piece += " -- too few for the mean to describe the system rather than this run"
            if isinstance(discard, int) and isinstance(n, int):
                piece += f", first {discard} of {n} frames discarded as unequilibrated"
            parts.append(piece)
        if parts:
            rows.append(f"{name}: " + "; ".join(parts))
    if not rows:
        return ""
    return "what the analyses found:\n" + "\n".join(f"  {r}" for r in rows[:20])


def _where_the_run_is(runtime: Any) -> str:
    """"production step 16,000" -- what a stop would throw away."""
    if runtime is None or not getattr(runtime, "active_root", None):
        return ""
    try:
        from fastmdxplora.gui.telemetry import read_status

        status = read_status(runtime.active_root) or {}
    except Exception:  # noqa: BLE001
        return ""
    stage = status.get("stage") or ""
    step = status.get("step") or status.get("current_step")
    if stage and isinstance(step, (int, float)):
        return f"{stage} step {int(step):,}"
    return str(stage)


def _hms(seconds: float) -> str:
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m" if h else f"{m}m {s}s"


# ---------------------------------------------------------------------------
# Conversations belong to studies.
#
# The model is the one Claude's users already know: a chat belongs to a
# project, and every chat in a project sees the project's context. Here a
# study is the project. A conversation lives inside the study folder it is
# about, at <study>/agent/conversations/, so copying a study carries the
# conversations that made it -- the record stays with the data. A
# conversation about no study in particular lives at the workspace level,
# as a chat outside any project does. Opening a conversation from another
# study loads that study, so the thread and the Agent's context are never
# about two different runs. A conversation that launches a run moves into
# the study it created: how a study came to be belongs with the study.
# ---------------------------------------------------------------------------

CONVERSATIONS_SUBDIR = Path("agent") / "conversations"
WORKSPACE_CONVERSATIONS_DIR = ".fastmdxplora_agent_conversations"
CONVERSATION_FILE = ".fastmdxplora_agent_conversation.json"  # pre-0047 single file
CONVERSATION_KEEP = 400


def _is_study(path: Any) -> bool:
    """A folder FastMDXplora wrote: a manifest or a phase directory."""
    if not path:
        return False
    p = Path(path)
    return p.is_dir() and any((p / m).exists()
                              for m in ("manifest.json", "simulation", "analysis", "report", "setup"))


def _store_for(workspace: Any, study: Any) -> Path:
    """Where a scope's conversations are kept."""
    if study is not None and _is_study(study):
        return Path(study) / CONVERSATIONS_SUBDIR
    return Path(workspace) / WORKSPACE_CONVERSATIONS_DIR


def _scope(runtime: Any) -> tuple[Path, Path | None]:
    """(workspace, study-or-None) for the runtime's current view."""
    workspace = Path(getattr(runtime, "exploration_root", None) or ".")
    study = getattr(runtime, "active_root", None)
    return workspace, (Path(study) if study and _is_study(study) else None)


def _migrate_single_file(workspace: Any) -> None:
    """The one-file thread from before 0047 becomes a workspace conversation."""
    import json
    import os

    old = Path(workspace) / CONVERSATION_FILE
    if not old.is_file():
        return
    store = Path(workspace) / WORKSPACE_CONVERSATIONS_DIR
    store.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(old.read_text(encoding="utf-8"))
        entries = data.get("entries") if isinstance(data, dict) else []
    except (OSError, ValueError):
        entries = []
    cid = _new_id()
    _write_one(store, cid, entries or [])
    (store / "current").write_text(cid, encoding="utf-8")
    try:
        os.replace(old, old.with_suffix(".json.migrated"))
    except OSError:
        pass


def _new_id() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("conv-%Y%m%d-%H%M%S-%f")


def _title_of(entries: list) -> str:
    for e in entries:
        if isinstance(e, dict) and e.get("role") == "user" and e.get("text"):
            text = str(e["text"]).strip().splitlines()[0]
            return text[:60] + ("\u2026" if len(text) > 60 else "")
    return "New conversation"


def _read_one(store: Path, cid: str) -> list:
    import json

    try:
        data = json.loads((store / f"{cid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    return entries[-CONVERSATION_KEEP:] if isinstance(entries, list) else []


def _write_one(store: Path, cid: str, entries: list) -> None:
    import json
    import os

    clean = [e for e in entries if isinstance(e, dict) and e.get("role") in ("user", "agent")]
    clean = clean[-CONVERSATION_KEEP:]
    store.mkdir(parents=True, exist_ok=True)
    path = store / f"{cid}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 3, "id": cid, "entries": clean}, indent=1),
                   encoding="utf-8")
    os.replace(tmp, path)


def _current_in(store: Path) -> str | None:
    try:
        cid = (store / "current").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return cid if (store / f"{cid}.json").is_file() else None


def _valid_id(cid: Any) -> bool:
    cid = str(cid or "")
    return cid.startswith("conv-") and "/" not in cid and ".." not in cid


def _study_label(study: Path) -> str:
    """The study's system name if the manifest has it, else the folder;
    and, for a continuation, whose."""
    import json

    label = study.name
    try:
        manifest = json.loads((study / "manifest.json").read_text(encoding="utf-8"))
        system = (manifest.get("system") or {}).get("system") or manifest.get("system_input")
        if system:
            label = str(Path(str(system)).stem)
    except (OSError, ValueError, AttributeError):
        pass
    from fastmdxplora.gui.browse import continuation_of

    cont = continuation_of(study)
    if cont:
        label += f" (continues {Path(str(cont['study'])).name})"
    return label


# ---- the API the endpoints call, all scoped by the runtime's view ---------

def read_conversation(runtime: Any) -> dict[str, Any]:
    """The current conversation in the current scope, or an empty one."""
    workspace, study = _scope(runtime)
    _migrate_single_file(workspace)
    store = _store_for(workspace, study)
    cid = _current_in(store)
    scope = {"study": str(study) if study else None,
             "study_label": _study_label(study) if study else None}
    if cid is None:
        return {"ok": True, "id": None, "entries": [], **scope}
    return {"ok": True, "id": cid, "entries": _read_one(store, cid), **scope}


def write_conversation(runtime: Any, entries: Any) -> dict[str, Any]:
    """Replace the current conversation with what the browser holds."""
    if not isinstance(entries, list):
        return {"ok": False, "error": "entries must be a list"}
    workspace, study = _scope(runtime)
    _migrate_single_file(workspace)
    store = _store_for(workspace, study)
    try:
        cid = _current_in(store)
        if cid is None:
            cid = _new_id()
            store.mkdir(parents=True, exist_ok=True)
            (store / "current").write_text(cid, encoding="utf-8")
        _write_one(store, cid, entries)
    except OSError as exc:
        return {"ok": False, "error": f"Could not save the conversation: {exc}"}
    return {"ok": True, "id": cid, "entries": _read_one(store, cid),
            "study": str(study) if study else None}


def new_conversation(runtime: Any) -> dict[str, Any]:
    """A fresh thread in the current scope. The last one stays."""
    workspace, study = _scope(runtime)
    store = _store_for(workspace, study)
    try:
        cid = _new_id()
        _write_one(store, cid, [])
        (store / "current").write_text(cid, encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "id": cid, "entries": []}


def list_conversations(runtime: Any) -> dict[str, Any]:
    """Every conversation the workspace holds, grouped by study.

    The current study's come first, then each other study's, then the
    workspace's own. Newest first within each. Cheap: a few small files
    per study, read once per opening of the list.
    """
    workspace, study = _scope(runtime)
    _migrate_single_file(workspace)

    def rows_in(store: Path, study_path: Path | None) -> list[dict[str, Any]]:
        if not store.is_dir():
            return []
        current = _current_in(store)
        out = []
        for path in sorted(store.glob("conv-*.json"), reverse=True):
            cid = path.stem
            entries = _read_one(store, cid)
            out.append({"id": cid, "title": _title_of(entries), "entries": len(entries),
                        "current": cid == current,
                        "study": str(study_path) if study_path else None,
                        "study_label": _study_label(study_path) if study_path else None,
                        "started": cid[5:13] + " " + cid[14:16] + ":" + cid[16:18]})
        return out

    groups: list[dict[str, Any]] = []
    if study is not None:
        groups.append({"study": str(study), "label": _study_label(study), "loaded": True,
                       "conversations": rows_in(_store_for(workspace, study), study)})
    for folder in sorted(workspace.iterdir() if workspace.is_dir() else [], reverse=True):
        if study is not None and folder.resolve() == study.resolve():
            continue
        if _is_study(folder):
            rows = rows_in(folder / CONVERSATIONS_SUBDIR, folder)
            if rows:
                groups.append({"study": str(folder), "label": _study_label(folder),
                               "loaded": False, "conversations": rows})
    ws_rows = rows_in(workspace / WORKSPACE_CONVERSATIONS_DIR, None)
    if ws_rows or study is None:
        groups.append({"study": None, "label": "No study", "loaded": study is None,
                       "conversations": ws_rows})
    return {"ok": True, "groups": groups}


def open_conversation(runtime: Any, cid: Any, study: Any = None) -> dict[str, Any]:
    """Make a conversation current. If it belongs to another study, load
    that study first, so the thread and the Agent's context agree."""
    if not _valid_id(cid):
        return {"ok": False, "error": "No such conversation."}
    workspace, current_study = _scope(runtime)
    target = Path(study) if study else None
    if target is not None and not _is_study(target):
        return {"ok": False, "error": "No such study."}
    if target is not None and (current_study is None or target.resolve() != current_study.resolve()):
        switched = runtime.switch_to(target) if hasattr(runtime, "switch_to") else {"ok": False}
        if not switched.get("ok"):
            return {"ok": False, "error": switched.get("error") or "Could not load that study."}
    store = _store_for(workspace, target)
    if not (store / f"{cid}.json").is_file():
        return {"ok": False, "error": "No such conversation."}
    (store / "current").write_text(str(cid), encoding="utf-8")
    return {"ok": True, "id": str(cid), "entries": _read_one(store, str(cid)),
            "study": str(target) if target else None, "loaded_study": target is not None}


def attach_conversation(runtime: Any, study: Any, cid: Any = None,
                        from_study: Any = None) -> dict[str, Any]:
    """Move a conversation into the study it just launched.

    The browser names the conversation and where it is now. It has to:
    by the time this runs the launch has already switched the loaded
    study to the new one, so "the current conversation in scope" is the
    new study's, which has none -- the first version looked there, moved
    nothing, and the next save started a fresh thread in the new study
    from whatever the browser held. With no id given, fall back to the
    current conversation of the given source scope.
    """
    import os

    target = Path(study) if study else None
    if target is None or not target.is_dir():
        return {"ok": False, "error": "No such study."}
    workspace, _ = _scope(runtime)
    source_study = Path(from_study) if from_study else None
    if source_study is not None and not _is_study(source_study):
        return {"ok": False, "error": "No such source study."}
    source = _store_for(workspace, source_study)
    if cid is not None and not _valid_id(cid):
        return {"ok": False, "error": "No such conversation."}
    cid = str(cid) if cid else _current_in(source)
    if cid is None or not (source / f"{cid}.json").is_file():
        return {"ok": True, "moved": False}
    dest = target / CONVERSATIONS_SUBDIR
    if source.resolve() == dest.resolve():
        return {"ok": True, "moved": False, "id": cid, "study": str(target)}
    try:
        dest.mkdir(parents=True, exist_ok=True)
        os.replace(source / f"{cid}.json", dest / f"{cid}.json")
        (dest / "current").write_text(cid, encoding="utf-8")
        if _current_in(source) is None:
            (source / "current").unlink(missing_ok=True)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "moved": True, "id": cid, "study": str(target)}


def delete_conversation(runtime: Any, cid: Any, study: Any = None) -> dict[str, Any]:
    """Delete one conversation, wherever it is. Asked for, per
    conversation, never a side effect of anything else."""
    if not _valid_id(cid):
        return {"ok": False, "error": "No such conversation."}
    workspace, _ = _scope(runtime)
    target = Path(study) if study else None
    store = _store_for(workspace, target)
    path = store / f"{cid}.json"
    if not path.is_file():
        return {"ok": False, "error": "No such conversation."}
    try:
        path.unlink()
        if _current_in(store) is None:
            (store / "current").unlink(missing_ok=True)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


def clear_conversation(runtime: Any) -> dict[str, Any]:
    """The old endpoint: delete the current conversation in scope."""
    workspace, study = _scope(runtime)
    store = _store_for(workspace, study)
    cid = _current_in(store)
    if cid is None:
        return {"ok": True, "entries": []}
    answer = delete_conversation(runtime, cid, str(study) if study else None)
    answer["entries"] = []
    return answer


# ---------------------------------------------------------------------------
# Attaching a file to a message.
#
# The Agent reads what the run status hands it and nothing else, by
# design. When a person wants it to see a file -- a log, a manifest, a
# config from another study -- they attach it to a message, as one does
# in any assistant, and it goes with that message as context. Per
# message, explicit, and recorded: the transcript keeps the file's name,
# path, size and digest, so the conversation stays a scientific record of
# what was looked at, without copying the bytes into it.
# ---------------------------------------------------------------------------

ATTACHABLE_SUFFIXES = frozenset({
    ".yml", ".yaml", ".json", ".log", ".md", ".txt", ".csv", ".tsv", ".dat",
    ".pdb", ".cif", ".py", ".toml", ".ini", ".cfg", ".xml", ".sdf", ".mol2",
})
ATTACH_LIMIT_BYTES = 200_000
ATTACH_KEEP_EACH_END = 80_000


PREVIEW_LIMIT_BYTES = 200_000
PREVIEW_KEEP_EACH_END = 80_000


def read_text_file(path: Any, *, within: Any = None,
                   limit: int = PREVIEW_LIMIT_BYTES) -> dict[str, Any]:
    """A text file, with what can be shown of it, and what it is.

    One reader for the Agent's attachments and the Files tab's preview,
    over one list of types: a file you can attach is a file you can read.
    ``within`` confines the read to a folder, for the preview, which
    takes its path from a URL; the attachment picker is the person's own
    choice and needs no fence beyond the type.
    """
    import hashlib

    file = Path(str(path or "")).expanduser()
    if not str(path or "").strip():
        return {"ok": False, "error": "No file given."}
    if within is not None:
        root = Path(str(within)).expanduser().resolve()
        try:
            resolved = file.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            return {"ok": False, "error": "That file is outside this study."}
        file = resolved
    if not file.is_file():
        return {"ok": False, "error": f"No such file: {file}"}
    if file.suffix.lower() not in ATTACHABLE_SUFFIXES:
        return {"ok": False,
                "error": f"{file.name} is not a text file. It can be downloaded "
                         "or opened, but not read here."}
    try:
        raw = file.read_bytes()
    except OSError as exc:
        return {"ok": False, "error": f"Could not read {file.name}: {exc}"}
    digest = hashlib.sha256(raw).hexdigest()[:12]
    truncated = False
    keep = min(PREVIEW_KEEP_EACH_END, max(1, limit // 2))
    if len(raw) > limit:
        head = raw[:keep].decode("utf-8", "replace")
        tail = raw[-keep:].decode("utf-8", "replace")
        dropped = len(raw) - 2 * keep
        text = (head + f"\n\n[\u2026 {dropped:,} bytes from the middle of the file "
                       f"not shown \u2026]\n\n" + tail)
        truncated = True
    else:
        text = raw.decode("utf-8", "replace")
    if "\x00" in text[:4000]:
        return {"ok": False, "error": f"{file.name} looks binary; this reads text."}
    return {"ok": True, "name": file.name, "path": str(file),
            "suffix": file.suffix.lower().lstrip("."),
            "size": len(raw), "sha256": digest, "text": text,
            "truncated": truncated, "lines": text.count("\n") + 1}


def read_attachment(path: Any) -> dict[str, Any]:
    """A text file the person chose, for the Agent to read.

    The same reader the Files tab uses, over the same list of types, so a
    file you can attach is a file you can read and adding a type adds it
    in both places.
    """
    return read_text_file(path, limit=ATTACH_LIMIT_BYTES)


def _continuation_summary(root: Any) -> str:
    """Whether and how this study can be continued, with the ready-made
    config, so "continue to 0.5 ns" is answered from the record.

    The Agent used to write resume_from by hand -- leaving minimisation
    and equilibration on, which the runner now refuses -- and ask for the
    equilibration lengths it needed for the arithmetic. The planner has
    them, and the config it makes is the one to hand back.
    """
    if not root:
        return ""
    try:
        import yaml

        from fastmdxplora.simulation.resume import continuation_of

        cont = continuation_of(root)
    except Exception:  # noqa: BLE001 - context, not load-bearing
        return ""
    if not cont.possible:
        if "no checkpoint" in (cont.refusal or ""):
            return ""
        return f"continuing this study: {cont.as_text()}"
    short = dict(cont.config)
    text = yaml.safe_dump(short, sort_keys=False, default_flow_style=False).strip()
    return (
        f"continuing this study: {cont.as_text()}. To continue it, use this "
        f"config as the base and set simulation.duration_ns to how much MORE "
        f"production is wanted (the remainder of the plan is filled in); "
        f"for a total, subtract {cont.production_done_ns:.3f} ns already done. "
        f"Do not turn minimisation or equilibration back on:\n```yaml\n{text}\n```"
    )
