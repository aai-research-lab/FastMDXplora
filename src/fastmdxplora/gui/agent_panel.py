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
    try:
        proposal = propose_config(
            request, complete, phases=list(phases),
            max_cycles=int(payload.get("attempts") or 4),
            history=history or None, current_config=current,
            run_status=_run_status(runtime))
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
# The conversation, kept.
#
# The thread lived only in the browser's memory: a refresh emptied it. A
# conversation about a study is part of the study's record, and a person
# who closes the tab and comes back should find what they said and what
# came back. One file per workspace, beside the runs, owned by the server
# so it survives the browser and follows the workspace rather than one
# machine's local storage.
# ---------------------------------------------------------------------------

CONVERSATION_FILE = ".fastmdxplora_agent_conversation.json"
CONVERSATION_KEEP = 400


def conversation_path(workspace: Any) -> Path:
    return Path(workspace) / CONVERSATION_FILE


def read_conversation(workspace: Any) -> dict[str, Any]:
    """What was said, or an empty thread. A broken file is an empty thread
    too: a person should not be locked out of the Agent by a corrupt cache."""
    import json

    path = conversation_path(workspace)
    if not path.is_file():
        return {"ok": True, "entries": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": True, "entries": []}
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        entries = []
    return {"ok": True, "entries": entries[-CONVERSATION_KEEP:]}


def write_conversation(workspace: Any, entries: Any) -> dict[str, Any]:
    """Replace the thread with what the browser holds. Bounded, atomic."""
    import json
    import os
    from pathlib import Path

    if not isinstance(entries, list):
        return {"ok": False, "error": "entries must be a list"}
    clean = [e for e in entries if isinstance(e, dict) and e.get("role") in ("user", "agent")]
    clean = clean[-CONVERSATION_KEEP:]
    path = conversation_path(workspace)
    try:
        Path(workspace).mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "entries": clean}, indent=1),
                       encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        return {"ok": False, "error": f"Could not save the conversation: {exc}"}
    return {"ok": True, "entries": clean}


def clear_conversation(workspace: Any) -> dict[str, Any]:
    path = conversation_path(workspace)
    try:
        if path.exists():
            path.unlink()
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "entries": []}
