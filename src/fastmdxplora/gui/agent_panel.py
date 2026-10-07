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

import json
import threading
import time
from pathlib import Path
from typing import Any

__all__ = ["model_endpoint", "propose_endpoint", "run_endpoint"]


def model_endpoint(payload: dict[str, Any]) -> dict[str, Any]:
    """Read or set which AI model to ask.

    With no ``provider``, reports what is set. With one, stores the choice
    and, if a key came with it, the key.
    """
    from fastmdxplora.agent.models import (
        CACHE_FOR, PROVIDERS, ModelChoice, _takes_tools, default_model, load_choice,
        model_path, save_cache_for, save_choice,
    )

    if not payload.get("provider"):
        current = load_choice()
        # Ask the provider now, if there is already a key to ask with. The
        # list used to be fetched only when a choice was saved, so opening
        # Settings showed the written fallback and an AI model chosen from it
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
                 "default_model": default_model(name, live.get(name)),
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
            # Whether the server takes tool calls; once it has turned them
            # away, the Agent writes to it in plain text.
            "tool_use": _takes_tools() if current else None,
            # How long the instructions are kept in the provider's cache.
            "cache_for": _stored_cache_for(),
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

    model = str(payload.get("model") or default_model(provider))
    if not model:
        return {"ok": False, "error": "An AI model name is needed.",
                "code": "config.option.missing_companion"}
    cache = str(payload.get("cache_for") or "auto")
    if cache not in CACHE_FOR:
        return {"ok": False, "error": f"Keep the cache for {', '.join(CACHE_FOR)}.",
                "code": "config.option.not_permitted"}

    save_choice(ModelChoice(provider, model, base_url),
                key=str(payload.get("api_key") or ""))
    save_cache_for(cache)
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
                     runtime: Any = None, *,
                     path_for: Any = None,
                     emit: Any = None) -> dict[str, Any]:
    """A sentence in, a config out, or the refusal that stopped it.

    The attempts come back whole rather than as a count. They are the only
    visible sign that anything checked the config, and a reader watching
    the AI model correct itself learns the config language while they wait.

    The AI model may look with the software's own tools before it answers
    (:mod:`fastmdxplora.agent.tools`); what it looked at comes back as
    ``looks`` with every kind of answer, and is shown under it. ``path_for``
    is the server's rule for a path (inside the workspace, when hosted),
    which a tool reading a structure is held to as the builder is.

    With ``emit``, the reply is sent on as it is written: a ``begin`` each
    time the AI model is asked, its text in pieces, each look as it is taken.
    What comes back at the end is the same.
    """
    from fastmdxplora.agent import completion_for, propose_config
    from fastmdxplora.agent.propose import DEFAULT_ATTEMPTS
    from fastmdxplora.config.loader import ConfigError
    from fastmdxplora.refusals import StudyError, refusal_of

    request = str(payload.get("request") or "").strip()
    if not request:
        return {"ok": False, "error": "Describe the study you want.",
                "code": "config.option.missing_companion"}

    mode = str(payload.get("agent") or "assisted")
    phases = payload.get("phases") or ["setup", "simulation"]
    # What the page shows (`molecule-viewer.js` `currentViewHints`): where
    # to look, never what is so; the `current_view` tool looks it up.
    view_hints = payload.get("current_view")
    scope_error = _active_view_error(view_hints, runtime, path_for)
    if scope_error is not None:
        return {"ok": False, "error": scope_error, "code": "agent.view.changed"}
    # Where this reply's receipt is kept, taken as it is asked: a study
    # opened while the AI model answers is not where its prompts belong.
    receipts = _receipts_of(runtime)
    try:
        complete = completion_for(where="page")
    except StudyError as exc:
        # The start page's questions about the study open are answered from
        # its records where no AI model can be asked.
        from fastmdxplora.gui.records_answer import answered_from_the_records

        answered = answered_from_the_records(payload, runtime)
        if answered is not None:
            return answered
        found = refusal_of(exc)
        return {"ok": False, "error": found.message, "code": found.code}

    # The conversation, the current config and the run, so the Agent can
    # modify rather than restart, and answer rather than write. The
    # browser sends the first two; the server knows the third.
    from fastmdxplora.agent.propose import KEPT_IN_BRIEF, KEPT_WHOLE

    history = [
        {"role": str(h.get("role") or "user"), "text": str(h.get("text") or "")}
        for h in (payload.get("history") or []) if isinstance(h, dict)
    ][-(KEPT_WHOLE + KEPT_IN_BRIEF):]
    current = payload.get("current_config")
    current = str(current) if current else None
    # Files attached to this message: the browser sends what read_attachment
    # returned, name and text; the bytes never go into the transcript.
    attachments = [
        {"name": str(a.get("name") or "file"), "text": str(a.get("text") or ""),
         "truncated": bool(a.get("truncated"))}
        for a in (payload.get("attachments") or []) if isinstance(a, dict) and a.get("text")
    ][:6]
    from fastmdxplora.agent.tools import Toolbox, current_view_tool

    # Your defaults, from where this GUI puts new studies: the Agent is told
    # them and an accepted config has them filled in, as the run will.
    try:
        defaults = your_defaults(runtime)
    except ConfigError as exc:
        return {"ok": False, "error": str(exc), "code": exc.code}
    from fastmdxplora.workspace_studies import folder_of_studies

    tools = Toolbox(
        path_for=path_for,
        workspace=folder_of_studies(getattr(runtime, "exploration_root", None)),
        extra=(current_view_tool(
            view_hints,
            active_root=getattr(runtime, "active_root", None),
            path_for=path_for,
        ),),
    )
    if emit is not None:
        complete = _written_as_it_goes(complete, emit)
        _say_each_look(tools, emit)
    try:
        proposal = propose_config(
            request, complete, phases=list(phases),
            max_cycles=int(payload.get("attempts") or DEFAULT_ATTEMPTS),
            history=history or None, current_config=current,
            run_status=_run_status(runtime), attachments=attachments or None,
            tools=tools, defaults=defaults)
    except StudyError as exc:
        found = refusal_of(exc)
        return {"ok": False, "error": found.message, "code": found.code,
                "looks": [look.as_record() for look in tools.looks]}
    # What the AI model was sent is kept beside the conversation whatever
    # came of it; the reply names it by its digest, and the page reads it
    # from `/api/agent/receipt` when the person opens it.
    receipt = _kept_receipt(receipts, proposal.receipt.as_record(),
                            looks=len(proposal.looks))
    scope_error = _active_view_error(view_hints, runtime, path_for)
    if scope_error is not None:
        # The study changed while the AI model answered: its reply is about
        # a study no longer open, so it is not offered.
        return {"ok": False, "error": scope_error, "code": "agent.view.changed",
                "looks": [look.as_record() for look in proposal.looks],
                "context_receipt": receipt}
    answer = _proposal_answer(proposal, payload, runtime, request, mode)
    answer["looks"] = [look.as_record() for look in proposal.looks]
    answer["context_receipt"] = receipt
    return answer


def _stored_cache_for() -> str:
    """The cache setting as stored: auto, 1h or 5m."""
    import json

    from fastmdxplora.agent.models import CACHE_FOR, model_path

    try:
        record = json.loads(model_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "auto"
    chosen = record.get("cache_for") if isinstance(record, dict) else None
    return chosen if chosen in CACHE_FOR else "auto"


def your_defaults(runtime: Any) -> Any:
    """The fastmdx-defaults.yml that applies where this GUI puts new
    studies, read and checked, or None. Raises ConfigError for a file
    that is there and wrong, so it is said rather than ignored."""
    from fastmdxplora.config.defaults_file import defaults_for

    where = getattr(runtime, "exploration_root", None)
    return defaults_for(where) if where else None


def _written_as_it_goes(complete: Any, emit: Any) -> Any:
    """The completion, each reply sent on as the AI model writes it. One that
    cannot stream (a test's, another's) is sent on whole when it answers.

    A completion that replies by tool calls (``turn``) is wrapped the same
    way: its text sent on as it is written, and a config being written said
    as it begins, since a config comes as a call's arguments and not as
    text the page could show line by line."""
    def written(prompt: str) -> str:
        emit({"type": "begin"})
        if getattr(complete, "streams", False):
            return complete(prompt, on_text=lambda piece: emit({"type": "text", "text": piece}))
        text = complete(prompt)
        emit({"type": "text", "text": text})
        return text

    turn = getattr(complete, "turn", None)
    if callable(turn):
        def turned(system: str, messages: list[Any], tools: list[Any]) -> Any:
            emit({"type": "begin"})

            def calling(name: str) -> None:
                # A config comes as a call's arguments, not as text the page
                # could show line by line: said as a step while it is written.
                if name == "propose_config":
                    emit({"type": "step", "label": "Writing the config"})

            return turn(system, messages, tools,
                        on_text=lambda piece: emit({"type": "text", "text": piece}),
                        on_call=calling)
        written.turn = turned  # type: ignore[attr-defined]
        written.turned_away = getattr(complete, "turned_away", None)  # type: ignore[attr-defined]
    return written


def _active_view_error(view_hints: Any, runtime: Any, path_for: Any) -> str | None:
    """Reject a stale or changed study before an Agent call begins."""
    if not isinstance(view_hints, dict) or not view_hints.get("study"):
        return None
    if getattr(runtime, "data_stale", False):
        return "Reload the current study before asking the Agent about its view."
    active = getattr(runtime, "active_root", None)
    try:
        named = (path_for(view_hints["study"]) if path_for is not None
                 else view_hints["study"])
        matches = (
            active is not None and named is not None
            and Path(named).expanduser().resolve() == Path(active).expanduser().resolve()
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        matches = False
    if not matches:
        return "The current view changed. Select it again before asking the Agent."
    return None


def _say_each_look(tools: Any, emit: Any) -> None:
    """Each look said as it begins ("Looking up "trp-cage" in the PDB"), so
    the page shows what the Agent is doing, and sent on when it is taken, as
    it will be shown under the reply."""
    from fastmdxplora.agent.tools import look_said

    used = tools.use

    def use(name: str, asked: dict[str, Any]) -> Any:
        emit({"type": "looking", "label": look_said(name, asked, doing=True)})
        look = used(name, asked)
        emit({"type": "look", "look": look.as_record()})
        return look
    tools.use = use


def _proposal_answer(proposal: Any, payload: dict[str, Any], runtime: Any,
                     request: str, mode: str) -> dict[str, Any]:
    """What the browser is sent for a proposal, by what kind it is."""
    attempts = [
        {"number": attempt.number,
         "refusal": (attempt.refusal.as_dict() if attempt.refusal else None)}
        for attempt in proposal.attempts
    ]
    answer = _proposal_kind(proposal, payload, runtime, request, mode, attempts)
    # What the AI model's calls cost, where the completion reports it, and
    # how it replied: on every kind of answer, for the page and the record.
    answer["usage"] = getattr(proposal, "usage", None)
    answer["protocol"] = getattr(proposal, "protocol", "text")
    return answer


def _proposal_kind(proposal: Any, payload: dict[str, Any], runtime: Any,
                   request: str, mode: str, attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """The answer for what the proposal is: an action, an answer, a
    question, a refusal or a config."""
    import yaml
    if proposal.action:
        # An instruction. The browser carries it out through the same
        # door the button uses; the server only names it. For "stop" it
        # adds where the run is, so the confirmation can say what would
        # be lost.
        where = ""
        if proposal.action == "stop":
            where = _where_the_run_is(runtime)
        answer = {"ok": False, "action": proposal.action, "where": where,
                  "attempts": attempts}
        if proposal.action == "run":
            # Run without asking only when the person's own words said to.
            # The browser asks first unless this says it need not.
            from fastmdxplora.agent.propose import told_to_run

            answer["confirm"] = not told_to_run(request)
        if proposal.action == "run the fix":
            # The fix itself, from the study's record rather than from the
            # reply: the command and its price, for the person to confirm.
            # Always asked: it starts work on this machine.
            answer["fix"] = _first_fix(runtime)
            answer["confirm"] = True
        if proposal.action in ("analyze again", "write the report again"):
            # The study open's analyses or report run again in its folder,
            # the analyses named checked against the software's own; asked
            # with what is replaced, or said why not.
            from fastmdxplora.gui.again_view import again_fix

            said = again_fix(getattr(runtime, "active_root", None), proposal.action,
                             proposal.arguments)
            answer["fix"] = said.get("fix")
            answer["refused"] = said.get("reason")
            answer["confirm"] = True
        if proposal.action == "rerun windows":
            # The windows and values the person named, read by a strict
            # pattern and checked here against the study; the command is
            # built from its record. Asked, with the price, or said why not.
            from fastmdxplora.gui.fixes_view import windows_payload

            said = windows_payload(getattr(runtime, "active_root", None), proposal.arguments)
            answer["fix"] = said.get("fix")
            answer["refused"] = said.get("reason")
            answer["confirm"] = True
        return answer
    if proposal.answer:
        # A question was asked, not a study. A paragraph back, and under it
        # what the study recorded for each analysis the paragraph names, so
        # a number in the prose can be read against its record and figure.
        from fastmdxplora.gui.citations import cited_findings

        try:
            cites = cited_findings(proposal.answer, getattr(runtime, "active_root", None))
        except Exception:  # noqa: BLE001 - the answer stands without them
            cites = []
        answer = {"ok": False, "answer": proposal.answer, "cites": cites,
                  "attempts": attempts}
        if proposal.scene and _is_study(getattr(runtime, "active_root", None)):
            # A scene the answer proposes, written only when the person
            # presses its button (`POST /api/scenes`).
            answer["scene"] = proposal.scene
        return answer
    if proposal.question:
        # Not a failure. The request is short of something only the person
        # can supply, and the honest answer is to say what. The candidates
        # the AI model named are said with it, so a page that shows only
        # the question still shows them, and the conversation keeps them.
        choices = list(getattr(proposal, "choices", ()) or ())
        question = proposal.question + (
            "\n\nCandidates: " + "; ".join(choices) + "." if choices else "")
        return {"ok": False, "question": question, "choices": choices,
                "code": "config.option.missing_companion",
                "error": question, "attempts": attempts}
    if not proposal.accepted:
        last = proposal.refusal
        return {"ok": False, "attempts": attempts, "cycles": proposal.cycles,
                "error": last.message if last else "No config was produced.",
                "code": last.code if last else "unclassified"}

    # The mode travels with the config rather than beside it, so it reaches
    # resolved_config.yml and the manifest like any other setting.
    config = dict(proposal.config)
    config["agent"] = mode
    # And which AI model, not only that one was used. `agent: assisted` says an
    # AI model was involved; this says which, so the record identifies the
    # software rather than the category.
    from fastmdxplora.agent import load_choice

    chosen = load_choice()
    if chosen is not None:
        config["agent_model"] = f"{chosen.provider}/{chosen.model}"
    from fastmdxplora.gui.plan import plan_of

    try:
        plan = plan_of(config)
    except Exception:  # noqa: BLE001 - the config stands without its summary
        plan = []
    return {
        "ok": True,
        "cycles": proposal.cycles,
        "attempts": attempts,
        "config": config,
        "plan": plan,
        "note": getattr(proposal, "note", None),
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
    # The panel's field first; else a ceiling the config itself carries, as
    # the command line reads it, so an Agent that wrote `budget_hours` is
    # not refused for the field being empty.
    hours = 0.0
    for given in (payload.get("budget_hours"), config.get("budget_hours")):
        try:
            hours = float(given)
        except (TypeError, ValueError):
            continue
        if hours > 0:
            break

    # Required for `autonomous`, honoured in every mode. A budget stands in
    # for a human, which is why the mode with nobody watching must have
    # one -- and a ceiling is never the wrong thing to have on a study that
    # will run for days, so it is offered whether or not it is demanded.
    if mode == "autonomous" and hours <= 0:
        return _with_its_fix({
            "ok": False,
            "code": "environment.budget.absent",
            "error": ("An autonomous run is not shown to you before it "
                      "starts, so a GPU-hour budget is the only thing left "
                      "that can stop it. Give one above."),
        })
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
        return _with_its_fix(runtime.launch_from_config(None, config=config,
                                                        dashboard_url=dashboard_url))
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        from fastmdxplora.gui.config_builder import refused

        said = refused(exc)
        return {"ok": False, "error": said["refusal"]["message"],
                "code": said["refusal"]["code"], **said}


def _with_its_fix(answer: dict[str, Any]) -> dict[str, Any]:
    """A refused run with what would fix it, as the builder's refusals come.

    The builder's refusals carried their remedy (1133); the same config
    refused through the Agent's Run here came back as the refusal alone.
    A refusal without a registered code (a folder already in use) is said
    as it is: its own message names the next step.
    """
    if answer.get("ok") or answer.get("remedy") or not answer.get("code"):
        return answer
    from fastmdxplora.remedies import remedy_for

    record = answer.get("refusal") or {"code": answer["code"],
                                       "message": answer.get("error") or ""}
    try:
        answer["remedy"] = remedy_for(record, where="the config").as_record()
    except Exception:  # noqa: BLE001 - the refusal stands without its fix
        pass
    return answer


def _run_status(runtime: Any) -> str | None:
    """What the run is doing, in a few lines an AI model can read.

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
        from fastmdxplora.gui.telemetry import analyze_health, status_as_it_stands

        status = status_as_it_stands(runtime.active_root) or {}
        if status.get("stage"):
            lines.append(f"stage: {status['stage']}")
        # A record left saying the run goes on, by a run that has ended: the
        # time it had left is not said, as the page does not say it.
        ended = status.get("status") == "interrupted"
        if ended:
            lines.append("state: interrupted: the run ended without recording why; its "
                         f"last update was {status.get('last_update_timestamp') or 'not recorded'}")
        # The numbers the sidebar shows, so "how far along?" is answered
        # with a step and a time rather than "I have only the stage". The
        # Agent said exactly that while the sidebar read 334,000 of
        # 350,000 and three minutes left.
        step, total = status.get("current_step"), status.get("total_planned_steps")
        if isinstance(step, (int, float)) and isinstance(total, (int, float)) and total > 0:
            lines.append(f"step: {int(step):,} of {int(total):,} "
                         f"({100.0 * step / total:.1f}% complete)")
            elapsed = status.get("elapsed_wall_time_s")
            if isinstance(elapsed, (int, float)) and step > 0 and not ended:
                remaining = elapsed * (total / step - 1.0)
                lines.append(f"elapsed: {_hms(elapsed)}; about {_hms(remaining)} left")
        from fastmdxplora.gui.simulated_time import say_length, simulated_times

        times = simulated_times(runtime.active_root, status)
        sim_ns = status.get("simulation_time_completed_ns")
        if times["production_ns"] is not None or times["equilibrating"]:
            # Production, as the page says it: the live record's own time
            # has equilibration in it, and "0.7 ns" was read back as a
            # production length of 0.7 ns when the config said 0.5.
            done = times["production_ns"] or 0.0
            planned = times["production_planned_ns"]
            if times["pieces"] > 1:
                lines.append(f"production: {say_length(done)} in {times['pieces']} pieces")
            else:
                lines.append(f"production so far: {say_length(done)}"
                             + (f" of {say_length(planned)}" if planned else ""))
            if times["equilibrating"] and times["equilibration_ns"] is not None:
                lines.append(f"equilibrating ({times['stage']}): "
                             f"{say_length(times['equilibration_ns'])} of "
                             f"{say_length(times['equilibration_planned_ns'])}")
        elif isinstance(sim_ns, (int, float)):
            # Equilibration included; the config below is where the
            # production length lives.
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
    checks = _checks_summary(getattr(runtime, "active_root", None))
    if checks:
        lines.append("")
        lines.append(checks)
    asked = _sampling_summary(getattr(runtime, "active_root", None))
    if asked:
        lines.append("")
        lines.append(asked)
    stopped = _stopping_summary(getattr(runtime, "active_root", None))
    if stopped:
        lines.append("")
        lines.append(stopped)
    fixes = _remedies_summary(getattr(runtime, "active_root", None))
    if fixes:
        lines.append("")
        lines.append(fixes)
    return "\n".join(lines)


def _remedies_summary(root: Any) -> str:
    """What would fix each thing that stopped the study, with its command
    or config and its price here, so "why did it stop and what now?" is
    answered with a step and a cost rather than the refusal read back."""
    from fastmdxplora.gui.fixes_view import runnable, study_of

    here = study_of(root)
    if here is None:
        return ""
    try:
        from fastmdxplora.remedies import remedies_of

        found = remedies_of(here)
    except Exception:  # noqa: BLE001 - context, not load-bearing
        return ""
    if not found:
        return ""
    return ("what would fix it (within what each refusal lets be said; a value "
            "not given here is not the software's to suggest; one marked "
            "[runs here] is what `DO: run the fix` runs, the first of them):\n"
            + "\n".join(f"  {'[runs here] ' if runnable(remedy) else ''}{remedy.as_text()}"
                         for remedy in found[:8]))


def _first_fix(runtime: Any) -> dict[str, Any] | None:
    """The fix `DO: run the fix` would run: the first one this software runs
    for the study on screen, with its place in the list, or None."""
    from fastmdxplora.gui.fixes_view import fixes_payload

    try:
        found = fixes_payload(getattr(runtime, "active_root", None))
    except Exception:  # noqa: BLE001
        return None
    for fix in found.get("fixes") or []:
        if fix.get("runnable"):
            return fix
    return None


def _stopping_summary(root: Any) -> str:
    """For a study run until it knew: each round and what was decided on it,
    so "why did it stop at 12 ns?" is answered from the record."""
    if not root:
        return ""
    import json

    from fastmdxplora.simulation.stopping import RECORD

    here = Path(root)
    # A replica's own folder is runs/<id>; the record is the study's.
    for where in (here, here.parent.parent):
        try:
            record = json.loads((where / RECORD).read_text(encoding="utf-8"))
            break
        except (OSError, ValueError):
            continue
    else:
        return ""
    if not isinstance(record, dict):
        return ""
    lines = ["how long the study ran, and why (simulation.stop_when, as recorded):"]
    for number, entry in enumerate(record.get("rounds") or [], start=1):
        said = "; ".join(str(v.get("said")) for v in entry.get("verdicts") or [])
        lines.append(f"  round {number}, at {entry.get('production_ns')} ns: {said}"
                     f" -> {entry.get('decision')}"
                     + (f" by {entry['more_ns']} ns" if entry.get("more_ns") else ""))
    if record.get("said"):
        lines.append(f"  outcome: {record['said']}")
    return "\n".join(lines)


def _checks_summary(root: Any) -> str:
    """The checks the run was held to, each ticked, as the report ticks them.

    The same list an Agent's plan states before a run, so "did it pass?" is
    answered against what was promised rather than whatever the AI model
    thinks a good run looks like.
    """
    if not root:
        return ""
    try:
        from fastmdxplora.report.document import _assess_this_run

        assessed = _assess_this_run(Path(root))
    except Exception:  # noqa: BLE001 - context, not load-bearing
        return ""
    checks = (assessed or {}).get("checks") or []
    if not checks:
        return ""
    word = {True: "passed", False: "FAILED", None: "not judged"}
    return "the checks this run was held to (as the report ticks them):\n" + "\n".join(
        f"  {word[c['passed']]}: {c['said']} ({c['detail']})" for c in checks)


def _sampling_summary(root: Any) -> str:
    """How much longer the study must run for the means it withheld, what
    that takes here, and the config that does it.

    "The remedy is a longer run" is where the analyses stopped, and the
    Agent asked how much longer had only that sentence. The analyses
    record the figure; this is it for the study, with the cost at the speed
    the study ran and, where the study can be continued, the config.
    """
    if not root:
        return ""
    try:
        from fastmdxplora.simulation.sampling_ask import sampling_asked_for

        ask = sampling_asked_for(root)
    except Exception:  # noqa: BLE001 - context, not load-bearing
        return ""
    if ask is None:
        return ""
    text = f"what the withheld means need: {ask.as_text()}"
    try:
        import yaml

        from fastmdxplora.simulation.resume import continuation_of, last_segment

        possible = continuation_of(root, from_segment=last_segment(root)).possible
    except Exception:  # noqa: BLE001
        possible = False
    if possible:
        config = yaml.safe_dump(ask.config(root), sort_keys=False,
                                default_flow_style=False).strip()
        text += (" To run it, answer with this config; it extends the study in "
                 f"place and reruns the analyses:\n```yaml\n{config}\n```")
    return text


def _config_the_run_used(root: Any) -> str:
    """The active run's own config, so "the same settings as that one" has
    something to copy from.

    The Agent lost the previous study's config the moment it wrote a new
    one, and said "I have no chignolin study in this conversation" while
    the chignolin run was the active study with its resolved config on
    disk. It reads the study's config now, as it was written
    (`exploration.yml`, which a study started from the GUI or an AI app
    keeps), or else the resolved one with what nobody decided left out:
    every setting at its default, and every one left unset. The resolved
    file alone is a hundred lines of defaults and nulls (3,152 characters
    for a 40 ps study, 41 of its lines `null`), cut at 4,000 characters, so a
    study's analysis and report blocks could be lost behind them; what a
    person means by "the same settings" is what was decided.
    """
    if not root:
        return ""
    import yaml

    base = Path(root)
    for name, decided in (("exploration.yml", False), ("resolved_config.yml", True)):
        path = base / name
        if not path.is_file():
            continue
        try:
            config = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(config, dict):
            continue
        if decided:
            config = _what_was_decided(config)
        body = yaml.safe_dump(config, sort_keys=False, default_flow_style=False).strip()
        # A config that runs to pages is not something to paste into every
        # prompt.
        if len(body) > 4000:
            body = body[:4000] + "\n# \u2026 (truncated)"
        return body
    return ""


def _what_was_decided(config: dict[str, Any]) -> dict[str, Any]:
    """A resolved config without its unset settings and those at their
    defaults: what was decided, as near as the record can tell. A value
    somebody set to the default itself cannot be told from the default."""
    from fastmdxplora.config.schema import PHASE_SCHEMAS

    kept: dict[str, Any] = {}
    for key, value in config.items():
        if value is None or value == [] or value == {}:
            continue
        schema = PHASE_SCHEMAS.get(key)
        if schema is not None and isinstance(value, dict):
            defaults = {field.name: field.default for field in schema.fields}
            block = {name: setting for name, setting in value.items()
                     if setting is not None and not (name in defaults
                                                     and setting == defaults[name])}
            if block:
                kept[key] = block
            continue
        kept[key] = value
    return kept



def _results_summary(root: Any) -> str:
    """What the analyses found, in a few lines an AI model can read.

    The same numbers the Report page shows, from the same computation:
    for each analysis, the mean, its standard error, how many effective
    samples the trajectory held and how many frames were discarded as
    not yet equilibrated. "Is the RMSD converged?" is answerable from
    that -- the effective sample count against the ten a mean needs --
    and not from a figure the AI model cannot see.
    """
    if not root:
        return ""
    from pathlib import Path

    base = Path(root)
    analysis = base / "analysis"
    if not analysis.is_dir():
        return ""
    import json
    import math

    from fastmdxplora.gui.report_dashboard import unit_of
    from fastmdxplora.statistics import with_its_error
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
            if not isinstance(f, dict):
                continue
            # A series too short to measure records why and no mean, and the
            # AI model was told nothing: asked whether the RMSD had equilibrated it
            # had no number and no reason, only silence to read.
            withheld = f.get("not_a_measurement")
            # The findings key is usually "mean", and some analyses key their
            # finding by their own name; naming it again reads as a stutter
            # ("order_parameters: order_parameters mean 0.8565"). Name the
            # key only when it says something else.
            own = key in ("mean", name)
            if "mean" not in f:
                if withheld:
                    label = "" if own else f"{key} "
                    parts.append(f"{label}no mean: {withheld}")
                continue
            mean = f.get("mean")
            se = f.get("standard_error")
            n_eff = f.get("effective_samples")
            discard = f.get("discard")
            n = f.get("n_frames")
            label = "" if own else f"{key} "
            # With its unit: an RMSD of 0.013 was handed over bare, and an
            # AI model answering in Angstrom had nothing to say it was nm.
            unit = unit_of(name, f) if own else (
                f["unit"] if isinstance(f.get("unit"), str) else "")
            has_error = isinstance(se, (int, float)) and math.isfinite(se)
            # As the report and the citations under the answer give it: the
            # error to two figures and the mean to the same place. To four
            # figures each, an energy of -123,456.7 +/- 12 read -1.235e+05,
            # coarser than its own error, and differed from what the person
            # read beside it.
            if isinstance(mean, (int, float)):
                piece = f"{label}mean {with_its_error(mean, se if has_error else None)}"
            else:
                piece = f"{label}mean {mean}"
            if unit:
                piece += f" {unit}"
            if has_error:
                piece += " (s.e.)"
            if isinstance(n_eff, (int, float)):
                piece += f", {n_eff:.1f} effective samples"
                if n_eff < MINIMUM_EFFECTIVE_SAMPLES:
                    piece += " -- too few for the mean to describe the system rather than this run"
            if isinstance(discard, int) and isinstance(n, int):
                piece += f", first {discard} of {n} frames discarded as unequilibrated"
            if withheld:
                piece += f" -- not determined: {withheld}"
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
# A conversation belongs to the study it is about, and sees that study's
# context. It lives inside the study folder, at <study>/agent/conversations/, so copying a study carries the
# conversations that made it -- the record stays with the data. A
# conversation about no study in particular lives at the workspace level.
# Opening a conversation from another
# study loads that study, so the thread and the Agent's context are never
# about two different runs. A conversation that launches a run moves into
# the study it created: how a study came to be belongs with the study.
# ---------------------------------------------------------------------------

CONVERSATIONS_SUBDIR = Path("agent") / "conversations"
WORKSPACE_CONVERSATIONS_DIR = ".fastmdxplora_agent_conversations"
CONVERSATION_FILE = ".fastmdxplora_agent_conversation.json"  # pre-0047 single file
CONVERSATION_KEEP = 400
CONTEXT_RECEIPTS_DIR = Path("receipts")


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


#: The longest name a person can give a conversation.
TITLE_LENGTH = 80
#: Not given: the conversation's own place is meant.
_HERE: Any = object()


def _title_of(entries: list, named: Any = None) -> str:
    """The name the person gave it, else its first question's first line."""
    if isinstance(named, str) and named.strip():
        return named.strip()[:TITLE_LENGTH]
    for e in entries:
        if isinstance(e, dict) and e.get("role") == "user" and e.get("text"):
            text = str(e["text"]).strip().splitlines()[0]
            return text[:60] + ("\u2026" if len(text) > 60 else "")
    return "New conversation"


def _read_record(store: Path, cid: str) -> dict:
    import json

    try:
        data = json.loads((store / f"{cid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_one(store: Path, cid: str) -> list:
    entries = _read_record(store, cid).get("entries")
    return entries[-CONVERSATION_KEEP:] if isinstance(entries, list) else []


def _write_one(store: Path, cid: str, entries: list, title: Any = _HERE) -> None:
    """The conversation written whole; the name given to it kept unless
    another is given (``title``, '' for none)."""
    import json
    import os

    clean = [e for e in entries if isinstance(e, dict) and e.get("role") in ("user", "agent")]
    clean = clean[-CONVERSATION_KEEP:]
    if title is _HERE:
        title = _read_record(store, cid).get("title")
    record: dict[str, Any] = {"version": 3, "id": cid, "entries": clean}
    if isinstance(title, str) and title.strip():
        record["title"] = title.strip()[:TITLE_LENGTH]
    store.mkdir(parents=True, exist_ok=True)
    path = store / f"{cid}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _current_in(store: Path) -> str | None:
    try:
        cid = (store / "current").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return cid if (store / f"{cid}.json").is_file() else None


#: Receipts kept beside a study's conversations, the newest; each is what
#: the AI model was sent for one reply (bounded in `agent/receipt.py`).
MOST_RECEIPTS = 100
#: Where a receipt's system prompts are kept, once each by their SHA-256:
#: the same 46,000 characters for every reply, which would otherwise be
#: most of every receipt.
RECEIPT_SYSTEMS_DIR = "systems"
#: System prompts kept before those no receipt names are looked for: one a
#: release, so the receipts are read again only now and then.
MOST_UNNAMED_SYSTEMS = 5


def _digest_of(value: Any) -> str | None:
    digest = str(value or "")
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        return None
    return digest


def _receipts_of(runtime: Any) -> Path | None:
    """The folder of receipts beside the conversations shown; none where the
    runtime names neither a workspace nor a study, rather than the folder
    the process happens to be in."""
    if runtime is None:
        return None
    workspace, study = _scope(runtime)
    if study is None and getattr(runtime, "exploration_root", None) is None:
        return None
    return _store_for(workspace, study) / CONTEXT_RECEIPTS_DIR


def _written(target: Path, text: str) -> None:
    """Written whole, as bytes: the text read back is the text kept, line
    endings included, so its digest holds."""
    temporary = target.with_name(f".{target.name}.{threading.get_ident()}.tmp")
    temporary.write_bytes(text.encode("utf-8", "surrogatepass"))
    temporary.replace(target)


def _kept_receipt(store: Path | None, record: dict[str, Any], *,
                  looks: int = 0) -> dict[str, Any]:
    """Keep a reply's receipt in `store`, beside the conversation it
    belongs to, the newest `MOST_RECEIPTS`, and say it in brief: its
    digest, how many times the AI model was asked and how many looks it
    took, whether any text was cut. Saved or not, the brief is the same;
    `kept` says which. Its system prompts are kept once each, by digest."""
    import hashlib

    brief = {"sha256": record.get("sha256"), "asked": len(record.get("sent") or ()),
             "looks": int(looks), "truncated": bool(record.get("truncated")),
             "kept": False}
    digest = _digest_of(record.get("sha256"))
    if store is None or digest is None:
        return brief
    systems = store / RECEIPT_SYSTEMS_DIR
    try:
        systems.mkdir(parents=True, exist_ok=True)
        named = []
        for text in record.get("systems") or ():
            sha = hashlib.sha256(str(text).encode("utf-8", "surrogatepass")).hexdigest()
            if not (systems / f"{sha}.txt").is_file():
                _written(systems / f"{sha}.txt", str(text))
            named.append({"sha256": sha})
        _written(store / f"{digest}.json",
                 json.dumps({**record, "systems": named}, indent=1, ensure_ascii=True))
        kept = sorted(store.glob("*.json"), key=lambda path: path.stat().st_mtime_ns)
        if len(kept) > MOST_RECEIPTS:
            for old in kept[:-MOST_RECEIPTS]:
                old.unlink(missing_ok=True)
            if len(list(systems.glob("*.txt"))) > MOST_UNNAMED_SYSTEMS:
                _forget_unnamed_systems(store, kept[-MOST_RECEIPTS:])
    except OSError:
        return brief
    return {**brief, "kept": True}


def _forget_unnamed_systems(store: Path, kept: list[Path]) -> None:
    """The system prompts no kept receipt names any more."""
    named: set[str] = set()
    for path in kept:
        try:
            for entry in json.loads(path.read_text(encoding="utf-8")).get("systems") or ():
                named.add(str(entry.get("sha256")))
        except (OSError, ValueError, AttributeError):
            return  # unread, so none is known to be unnamed
    now = time.time()
    for text in (store / RECEIPT_SYSTEMS_DIR).glob("*.txt"):
        # One written in the last minute may be about to be named by a reply
        # being kept beside this one.
        try:
            if text.stem not in named and now - text.stat().st_mtime > 60:
                text.unlink(missing_ok=True)
        except OSError:
            continue  # gone already, by a reply kept beside this one


def receipt_endpoint(runtime: Any, digest: Any) -> dict[str, Any]:
    """One kept receipt of the conversations shown, by its digest, its
    system prompts read back in, and checked against that digest."""
    named = _digest_of(digest)
    if runtime is None or named is None:
        return {"ok": False, "error": "No such record of what I read."}
    store = _receipts_of(runtime)
    if store is None:
        return {"ok": False, "error": "No such record of what I read."}
    try:
        record = json.loads((store / f"{named}.json").read_text(encoding="utf-8"))
        record["systems"] = [
            (store / RECEIPT_SYSTEMS_DIR / f"{_digest_of(entry.get('sha256'))}.txt")
            .read_bytes().decode("utf-8", "surrogatepass")
            for entry in record.get("systems") or ()]
    except (OSError, ValueError, AttributeError, UnicodeDecodeError):
        return {"ok": False, "error": "This record of what I read is no "
                                      f"longer kept (the newest {MOST_RECEIPTS} are)."}
    from fastmdxplora.agent.receipt import digest_of

    if digest_of(record) != named:
        return {"ok": False, "error": "This record of what I read has "
                                      "changed since it was kept."}
    return {"ok": True, "receipt": record}


def _valid_id(cid: Any) -> bool:
    cid = str(cid or "")
    return cid.startswith("conv-") and "/" not in cid and ".." not in cid


def _study_label(study: Path) -> str:
    """The study's system ID (fastmdxplora.system_id) if the manifest
    names its system, else the folder; and, for a continuation, whose."""
    import json

    from fastmdxplora.system_id import system_id

    label = study.name
    try:
        manifest = json.loads((study / "manifest.json").read_text(encoding="utf-8"))
        system = manifest.get("system")
        if isinstance(system, dict):
            system = system.get("system")
        system = system or manifest.get("system_input")
        if system:
            label = system_id(system) or label
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


def _named_place(runtime: Any, study: Any) -> tuple[Path | None, Path] | None:
    """The study named and where its conversations are kept: ``None`` for
    the workspace's own (Chats), or a study's folder. None if that is no
    study."""
    workspace, _ = _scope(runtime)
    target = Path(study) if study else None
    if target is not None and not _is_study(target):
        return None
    return target, _store_for(workspace, target)


def write_conversation(runtime: Any, entries: Any, cid: Any = None,
                       study: Any = _HERE) -> dict[str, Any]:
    """Replace a conversation with what the browser holds: the one it names
    (``cid`` in ``study``, None for a chat of no study), else the current
    one where the GUI is. A chat of no study stays one while a study is
    open."""
    if not isinstance(entries, list):
        return {"ok": False, "error": "entries must be a list"}
    workspace, study_open = _scope(runtime)
    _migrate_single_file(workspace)
    if cid is not None and study is not _HERE:
        if not _valid_id(cid):
            return {"ok": False, "error": "No such conversation."}
        place = _named_place(runtime, study)
        if place is None:
            return {"ok": False, "error": "No such study."}
        named, store = place
        try:
            _write_one(store, str(cid), entries)
            (store / "current").write_text(str(cid), encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "error": f"Could not save the conversation: {exc}"}
        return {"ok": True, "id": str(cid), "entries": _read_one(store, str(cid)),
                "study": str(named) if named else None}
    study = study_open
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


def new_conversation(runtime: Any, study: Any = _HERE) -> dict[str, Any]:
    """A fresh thread where the GUI is, or in ``study`` (None: a chat of no
    study, whatever is open). The last one stays."""
    workspace, study_open = _scope(runtime)
    if study is _HERE:
        study, store = study_open, _store_for(workspace, study_open)
    else:
        place = _named_place(runtime, study)
        if place is None:
            return {"ok": False, "error": "No such study."}
        study, store = place
    try:
        cid = _new_id()
        _write_one(store, cid, [])
        (store / "current").write_text(cid, encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "id": cid, "entries": [], "study": str(study) if study else None}


def rename_conversation(runtime: Any, cid: Any, study: Any = None,
                        title: Any = "") -> dict[str, Any]:
    """A name of the person's own for a conversation; '' gives it back its
    first question's first line."""
    if not _valid_id(cid):
        return {"ok": False, "error": "No such conversation."}
    place = _named_place(runtime, study)
    if place is None:
        return {"ok": False, "error": "No such study."}
    _, store = place
    if not (store / f"{cid}.json").is_file():
        return {"ok": False, "error": "No such conversation."}
    text = " ".join(str(title or "").split())
    if len(text) > TITLE_LENGTH:
        return {"ok": False, "error": f"A name is at most {TITLE_LENGTH} characters."}
    try:
        _write_one(store, str(cid), _read_one(store, str(cid)), title=text)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    record = _read_record(store, str(cid))
    return {"ok": True, "id": str(cid),
            "title": _title_of(record.get("entries") or [], record.get("title"))}


def list_conversations(runtime: Any) -> dict[str, Any]:
    """Every conversation the workspace holds, grouped by study.

    The current study's come first, then each other study's, then the
    workspace's own. Newest first within each. Cheap: a few small files
    per study, read once per opening of the list.
    """
    workspace, study = _scope(runtime)
    _migrate_single_file(workspace)

    from datetime import datetime, timezone

    def rows_in(store: Path, study_path: Path | None) -> list[dict[str, Any]]:
        if not store.is_dir():
            return []
        current = _current_in(store)
        out = []
        for path in store.glob("conv-*.json"):
            cid = path.stem
            record = _read_record(store, cid)
            entries = record.get("entries") if isinstance(record.get("entries"), list) else []
            try:
                updated = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            except OSError:
                continue
            out.append({"id": cid, "title": _title_of(entries, record.get("title")),
                        "named": bool(record.get("title")), "entries": len(entries),
                        "current": cid == current,
                        "study": str(study_path) if study_path else None,
                        "study_label": _study_label(study_path) if study_path else None,
                        "started": cid[5:13] + " " + cid[14:16] + ":" + cid[16:18],
                        "updated": updated.isoformat(timespec="seconds")})
        # The one talked in last first.
        out.sort(key=lambda row: (row["updated"], row["id"]), reverse=True)
        return out

    def group(folder: Path, loaded: bool, rows: list) -> dict[str, Any]:
        return {"study": str(folder), "label": _study_label(folder), "folder": folder.name,
                "loaded": loaded, "conversations": rows,
                "updated": rows[0]["updated"] if rows else ""}

    groups: list[dict[str, Any]] = []
    if study is not None:
        groups.append(group(study, True, rows_in(_store_for(workspace, study), study)))
    others = []
    for folder in sorted(workspace.iterdir() if workspace.is_dir() else [], reverse=True):
        if study is not None and folder.resolve() == study.resolve():
            continue
        if _is_study(folder):
            rows = rows_in(folder / CONVERSATIONS_SUBDIR, folder)
            if rows:
                others.append(group(folder, False, rows))
    # The study talked about last first.
    others.sort(key=lambda g: g["updated"], reverse=True)
    groups.extend(others)
    # The chats of no study: the workspace's own.
    ws_rows = rows_in(workspace / WORKSPACE_CONVERSATIONS_DIR, None)
    groups.append({"study": None, "label": "Chats", "folder": "", "loaded": study is None,
                   "chats": True, "conversations": ws_rows,
                   "updated": ws_rows[0]["updated"] if ws_rows else ""})
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
# in any AI app, and it goes with that message as context. Per
# message, explicit, and recorded: the transcript keeps the file's name,
# path, size and digest, so the conversation stays a scientific record of
# what was looked at, without copying the bytes into it.
# ---------------------------------------------------------------------------

ATTACHABLE_SUFFIXES = frozenset({
    ".yml", ".yaml", ".json", ".log", ".md", ".txt", ".csv", ".tsv", ".dat",
    ".pdb", ".cif", ".py", ".toml", ".ini", ".cfg", ".xml", ".sdf", ".mol2",
    ".sha256",  # a checkpoint's seal: one line, a size and a digest
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
    # Line endings as reading text in Python gives them. Decoded bytes kept
    # a file's \r\n, so on Windows the same report.md rendered one way on
    # the report page, which reads it as text, and another in the preview.
    # Size and digest stay those of the file as it is on disk.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
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

    It resumes from the last segment, as the command line's extension
    does: the study's own checkpoint is where its first run stopped, and
    a config resumed from there once the study has been extended runs
    the extensions' span again.
    """
    if not root:
        return ""
    try:
        import yaml

        from fastmdxplora.simulation.resume import continuation_of, last_segment

        last = last_segment(root)
        cont = continuation_of(root, from_segment=last)
        extended = Path(last).resolve() != Path(root).resolve()
    except Exception:  # noqa: BLE001 - context, not load-bearing
        return ""
    if not cont.possible:
        if "no checkpoint" in (cont.refusal or ""):
            return ""
        return f"continuing this study: {cont.as_text()}"
    # The study itself, extended in place: its next segment runs inside
    # it, every segment is joined and the analyses rerun over the whole.
    # This offered the raw checkpoint mechanism instead, with duration_ns as
    # the amount MORE, while the prompt described the study form with
    # duration_ns as the TOTAL: two meanings of one setting, and a run that
    # was a separate, unjoined study.
    planned = cont.production_planned_ns
    short = {"simulation": {"resume_from": str(Path(root).resolve()),
                            "duration_ns": round(float(planned), 6)}}
    text = yaml.safe_dump(short, sort_keys=False, default_flow_style=False).strip()
    where = (f" It has been extended; {Path(last).name} is where it last stopped, "
             f"and the production done counts every segment."
             if extended else "")
    return (
        f"continuing this study: {cont.as_text()}.{where} To continue it, answer "
        f"with this config. It extends the study in place from where it "
        f"stopped, with no minimisation or equilibration, joins every segment "
        f"and reruns the analyses. duration_ns is the TOTAL production the "
        f"study should end with ({cont.production_done_ns:.3f} ns are done); "
        f"to say how much MORE instead, replace it with extra_ns:"
        f"\n```yaml\n{text}\n```"
    )
