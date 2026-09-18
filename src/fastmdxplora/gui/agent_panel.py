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

    # launch_from_config takes the builder's form state and builds a config
    # from it. The first version handed it {"config": ...}, a key nothing
    # reads, and it built an empty config: the study started, the CLI
    # refused it for naming no system, and the button said Running while
    # nothing ran. The tests passed because their stub runtime echoed the
    # "config" key back -- they tested the assumption, not the function.
    # The config goes through the same mapping the builder uses to load
    # one, so what launches is what the Agent wrote.
    from fastmdxplora.gui.config_builder import state_from_config

    mapped = state_from_config(config)
    if not mapped.get("ok"):
        return {"ok": False, "code": "config.option.invalid",
                "error": mapped.get("error") or "The config could not be prepared to run."}
    state = dict(mapped["state"])
    if payload.get("output_dir"):
        state["output"] = str(payload["output_dir"])
    try:
        return runtime.launch_from_config(state, dashboard_url=dashboard_url)
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
        health = analyze_health(status, [])
        if health.get("state"):
            lines.append(f"health: {health['state']}"
                         + (f" -- {health['message']}" if health.get("message") else ""))
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(lines)
