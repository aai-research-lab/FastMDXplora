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
from contextlib import nullcontext
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
        return {"ok": False, "question": question, "asked": proposal.question,
                "choices": choices,
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
    from fastmdxplora.gui.plan import changes_between, plan_of, sourced

    try:
        # Each line with where its value came from (`decisions`).
        plan = sourced(plan_of(config), config)
    except Exception:  # noqa: BLE001 - the config stands without its summary
        plan = []
    # What changed from the version before, the config the page sent as the
    # current one: an edit is shown as its change.
    try:
        before = yaml.safe_load(str(payload.get("current_config") or "")) or None
        changes = changes_between(before, config) if isinstance(before, dict) else None
    except Exception:  # noqa: BLE001 - a version stands without its change
        changes = None
    return {
        "ok": True,
        "cycles": proposal.cycles,
        "attempts": attempts,
        "config": config,
        "plan": plan,
        "changes": changes,
        "note": getattr(proposal, "note", None),
        "yaml": yaml.safe_dump(config, sort_keys=False),
    }


#: One writer at a time: a page's save, a move into a run's folder, a
#: deletion. Each reads the conversation and writes it whole.
_CONVERSATIONS = threading.RLock()
_NOTHING_HELD = nullcontext()


def _one_writer(write: Any) -> Any:
    import functools

    @functools.wraps(write)
    def held(*args: Any, **kwargs: Any) -> Any:
        with _CONVERSATIONS:
            return write(*args, **kwargs)

    return held


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
    # The conversation that started the run goes with it, moved here as the
    # run starts: moved by the page afterwards, it raced the page's own
    # saves and its switch to the new study, and a slow disk lost the
    # thread (tenth review, 10-08). The writers' lock is held from before
    # the launch, which switches the GUI to the run's folder, until the
    # move: a reload or another tab in between read an empty thread there
    # and began a second one (sixteenth review, 10-08).
    moving = payload.get("conversation")
    moving = moving if isinstance(moving, dict) and moving.get("id") else None
    with _CONVERSATIONS if moving else _NOTHING_HELD:
        try:
            started = _with_its_fix(runtime.launch_from_config(None, config=config,
                                                               dashboard_url=dashboard_url))
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            from fastmdxplora.gui.config_builder import refused

            said = refused(exc)
            return {"ok": False, "error": said["refusal"]["message"],
                    "code": said["refusal"]["code"], **said}
        if moving and started.get("ok") and started.get("output"):
            cid = str(moving["id"])
            workspace, _ = _scope(runtime)
            # From where it lives, whatever place the page last heard of
            # (fifteenth review, 10-08: a stale tab's run left it behind).
            place = _where_it_lives(workspace, cid, runtime)
            source = (str(place[0]) if place[0] else None) if place else moving.get("study")
            started["conversation"] = attach_conversation(
                runtime, started["output"], cid, source)
            # The page saved the run's entry before it asked for the run;
            # where it went is written into that entry here, so a reload
            # before the page hears keeps the run (twelfth review, 10-08).
            _say_where_it_ran(runtime, cid, moving.get("run"), str(started["output"]))
    return started


def _say_where_it_ran(runtime: Any, cid: str, eid: Any, output: str) -> None:
    from datetime import datetime, timezone

    if not eid:
        return
    workspace, _ = _scope(runtime)
    place = _where_it_lives(workspace, cid, runtime)
    if place is None:
        return
    _, store = place
    try:
        entries = _read_one(store, cid)
        for entry in entries:
            if isinstance(entry, dict) and entry.get("eid") == eid:
                entry["output"] = output
                entry.setdefault("started", datetime.now(timezone.utc).isoformat())
                _write_one(store, cid, entries)
                return
    except OSError:
        return


#: How a run that has ended is said at the head of its summary.
_ENDED_AS = {"completed": "The run ended: completed", "failed": "The run failed",
             "stopped": "The run was stopped", "interrupted": "The run was interrupted"}


#: A run's own record saying it is going.
_GOING = ("running", "starting", "paused")
#: How long whether a folder is run is kept before it is asked again: the
#: page asks at each of its polls while a run awaits its summary, and the
#: answer reads a process's command line (PowerShell on Windows, `ps` on
#: macOS).
GOING_ASKED_FOR_S = 10.0
_GOING_ASKED: dict[Path, tuple[float, bool]] = {}


def _going_here(root: Path, runtime: Any) -> bool:
    """Whether a live process is known to run the folder, though not as
    this server's own: one in the workspace's list of runs started there
    (`runs_here`, written by every Run of the GUI and an AI app), or the
    run's own record at its top (a study of several runs keeps one there
    too). A run started on another machine is not. Kept for
    `GOING_ASKED_FOR_S`."""
    target = root.resolve()
    now = time.monotonic()
    kept = _GOING_ASKED.get(target)
    if kept is not None and now - kept[0] < GOING_ASKED_FOR_S:
        return kept[1]
    going = _asked_going(target, runtime)
    _GOING_ASKED[target] = (now, going)
    return going


def _asked_going(target: Path, runtime: Any) -> bool:
    from fastmdxplora import runs_here
    from fastmdxplora.orchestrator import RUN_PROCESS_FILE

    try:
        folders = list(runtime._rule_folders()) if hasattr(runtime, "_rule_folders") else []
    except Exception:  # noqa: BLE001 - no list to read
        folders = []
    for folder in folders:
        for run in runs_here._started_here(Path(folder).resolve()):
            here = Path(str(run.get("folder") or ""))
            if (here.is_absolute() and here.resolve() == target
                    and runs_here._going(run.get("pid"), here, run.get("argv"), run)):
                return True
    try:
        record = json.loads((target / RUN_PROCESS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(record, dict) and runs_here._going(
        record.get("pid"), target, record.get("argv"), record)


def run_summary_endpoint(payload: dict[str, Any], runtime: Any, *,
                         path_for: Any = None) -> dict[str, Any]:
    """What a run the Agent started found, once it has ended, written from
    the study's records (`records_answer`), so it costs no tokens: how it
    ended and how long it took, what it found with each error, whether it
    ran long enough, and what would strengthen it. ``ended`` false while
    the run is still going.

    In order: a folder not there is no study (the page asks no more); this
    server's process for it, alive, or another known to run it
    (`_going_here`), is a run going; then no process runs it, and what its
    records say is how it ended. A record still saying it is going is
    believed only of a run not this server's (on another machine): this
    server's own has exited (third to sixth reviews, 10-07)."""
    from fastmdxplora.gui.exploration import runs_of_a_study
    from fastmdxplora.gui.records_answer import MARK, answer_from_the_records
    from fastmdxplora.gui.simulated_time import phases_wall_seconds
    from fastmdxplora.gui.telemetry import status_as_it_stands

    given = str((payload or {}).get("study") or "").strip()
    try:
        named = path_for(given) if path_for is not None else (Path(given) if given else None)
    except Exception:  # noqa: BLE001 - outside the workspace, said as no study
        named = None
    root = Path(named).expanduser() if named else None
    if root is None or not root.is_dir():
        return {"ok": False, "error": "No study there."}
    running = getattr(runtime, "running_root", None)
    process = getattr(runtime, "process", None)
    ours = (running is not None and process is not None
            and Path(running).resolve() == root.resolve())
    if ours and process.poll() is None:
        return {"ok": True, "ended": False}
    if _going_here(root, runtime):
        # Another process runs it: not this server's, or after this
        # server's ended (`fastmdx resume` carrying it on, eighth review,
        # 10-08).
        return {"ok": True, "ended": False}
    # No process runs it from here on.
    stopped = root.resolve() in (getattr(runtime, "stopped_roots", None) or ())
    code = process.poll() if ours else None

    def recorded(folder: Path) -> str:
        return str((status_as_it_stands(folder) or {}).get("status") or "").lower()

    def said(question: str) -> str:
        text = answer_from_the_records(root, question) or ""
        return text[len(MARK):].strip() if text.startswith(MARK) else text.strip()

    def ended_as(status: str) -> str:
        """Stopped here, or ended with nothing to say how: said so."""
        if stopped and status in ("", "interrupted", "failed", *_GOING):
            return "stopped"
        if status in _GOING or not status:
            # Nothing recorded of how it ended: this server's process says,
            # where it was this server's; otherwise it is not known.
            if code is None:
                return ""
            return "interrupted" if code < 0 else "failed" if code else ""
        return status

    runs = runs_of_a_study(root)
    if runs:
        if not ours and any(recorded(Path(r["path"])) in _GOING
                            for r in runs if Path(r["path"]).is_dir()):
            # A run inside it says it is going: on another machine.
            return {"ok": True, "ended": False}
        results = _results_of(root)
        done = failed = prepared = 0
        for r in runs:
            result = results.get(str(r.get("run_id"))) or {}
            if (result.get("status") == "skipped"
                    and str(result.get("message") or "").startswith(_prepared_once())):
                prepared += 1
                done += 1
            elif r.get("state") == "completed":
                done += 1
            elif r.get("state") == "failed" and recorded(Path(r["path"])) != "stopped":
                failed += 1
        unfinished = len(runs) - done - failed
        # How it ended follows its runs: Stop pressed once every run had
        # finished changes nothing, and a study halted by a failure failed
        # (seventh review, 10-08).
        if not unfinished:
            status = "completed" if not failed else "failed" if not done else "ended"
        else:
            status = "stopped" if stopped else "failed" if failed else "interrupted"
        head = "The runs ended: " + ", ".join(
            f"{n} {word}" for n, word in ((done, "completed"), (failed, "failed"),
                                          (unfinished, "did not finish")) if n)
        if prepared == len(runs):
            # Asked only to prepare: not "completed" above its records'
            # word that no run was run (tenth review, 10-08).
            head = f"The study was prepared once, for its {prepared} windows"
        return {"ok": True, "ended": True, "status": status, "head": head,
                "found": said("found"), "long_enough": "", "strengthen": ""}
    if not _is_study(root):
        # It ended before it wrote a record of the study. One forgotten by
        # this server (another folder opened, or a restart) that wrote
        # nothing cannot have finished.
        status = ended_as("") or ("ended" if ours else "interrupted")
        return {"ok": True, "ended": True, "status": status,
                "head": _ENDED_AS.get(status, "The run ended"),
                "found": "It wrote no records to read.", "long_enough": "",
                "strengthen": ""}
    status = recorded(root)
    if status in _GOING and not ours:
        # Going, by its own record, though no process here runs it: a run
        # on another machine (review, 10-07).
        return {"ok": True, "ended": False}
    status = ended_as(status) or "ended"
    seconds = phases_wall_seconds(root)
    head = _ENDED_AS.get(status, "The run ended")
    if seconds:
        head += (" in " if status == "completed" else " after ") + _hms(seconds)
    if (root / "batch_manifest.json").is_file():
        # A study of several runs is answered in one paragraph, the same for
        # every question: said once.
        return {"ok": True, "ended": True, "status": status, "head": head,
                "found": said("found"), "long_enough": "", "strengthen": ""}
    return {"ok": True, "ended": True, "status": status, "head": head,
            "found": said("found"), "long_enough": said("long_enough"),
            "strengthen": said("strengthen")}


def _prepared_once() -> str:
    from fastmdxplora.batch.explorer import PREPARED_ONCE

    return PREPARED_ONCE


def _results_of(root: Path) -> dict[str, dict[str, Any]]:
    """Each run's result as a study of several runs' manifest records it."""
    try:
        manifest = json.loads((root / "batch_manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    runs = manifest.get("runs") if isinstance(manifest, dict) else None
    return {str(r.get("run_id")): r for r in runs or () if isinstance(r, dict)}


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


def _holds_conversations(path: Any) -> bool:
    """A study, or a folder a run the Agent started was given that has
    written nothing else yet: its conversation was moved there (eighth
    review, 10-08: Stop in the first second left such a folder, and the
    thread could be neither saved nor found)."""
    return bool(path) and (_is_study(path) or (Path(path) / CONVERSATIONS_SUBDIR).is_dir())


def _store_for(workspace: Any, study: Any) -> Path:
    """Where a scope's conversations are kept."""
    if study is not None and _holds_conversations(study):
        return Path(study) / CONVERSATIONS_SUBDIR
    return Path(workspace) / WORKSPACE_CONVERSATIONS_DIR


def _scope(runtime: Any) -> tuple[Path, Path | None]:
    """(workspace, study-or-None) for the runtime's current view."""
    workspace = Path(getattr(runtime, "exploration_root", None) or ".")
    study = getattr(runtime, "active_root", None)
    return workspace, (Path(study) if study and _holds_conversations(study) else None)


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


#: How many cut entries a conversation remembers, so a tab that still holds
#: one does not bring it back.
CUT_KEPT = 2000


def _with_eids(cid: str, entries: list) -> list:
    """Entries saved before each carried an ``eid`` given one by where it
    stands, the same for every tab that reads them (fifteenth and sixteenth
    reviews, 10-08: each tab gave its own, and two tabs kept two copies)."""
    return [dict(e, eid=f"{cid}-{i}") if isinstance(e, dict) and not e.get("eid") else e
            for i, e in enumerate(entries)]


def _read_one(store: Path, cid: str) -> list:
    entries = _read_record(store, cid).get("entries")
    return entries[-CONVERSATION_KEEP:] if isinstance(entries, list) else []


def _write_one(store: Path, cid: str, entries: list, title: Any = _HERE,
               cut: Any = _HERE) -> None:
    """The conversation written whole; the name given to it kept unless
    another is given (``title``, '' for none), and so the entries cut from
    it (``cut``, their eids)."""
    import json
    import os

    clean = [e for e in entries if isinstance(e, dict) and e.get("role") in ("user", "agent")]
    clean = clean[-CONVERSATION_KEEP:]
    if title is _HERE or cut is _HERE:
        was = _read_record(store, cid)
        title = was.get("title") if title is _HERE else title
        cut = was.get("cut") if cut is _HERE else cut
    record: dict[str, Any] = {"version": 3, "id": cid, "entries": clean}
    if isinstance(title, str) and title.strip():
        record["title"] = title.strip()[:TITLE_LENGTH]
    if isinstance(cut, list) and cut:
        record["cut"] = [str(e) for e in cut][-CUT_KEPT:]
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

@_one_writer
def read_conversation(runtime: Any) -> dict[str, Any]:
    """The current conversation in the current scope, or an empty one. Read
    under the writers' lock, so never in the middle of a run's move
    (sixteenth review, 10-08)."""
    workspace, study = _scope(runtime)
    _migrate_single_file(workspace)
    store = _store_for(workspace, study)
    cid = _current_in(store)
    scope = {"study": str(study) if study else None,
             "study_label": _study_label(study) if study else None}
    if cid is None:
        return {"ok": True, "id": None, "entries": [], **scope}
    return {"ok": True, "id": cid, "entries": _with_eids(cid, _read_one(store, cid)), **scope}


def _named_place(runtime: Any, study: Any) -> tuple[Path | None, Path] | None:
    """The study named and where its conversations are kept: ``None`` for
    the workspace's own (Chats), or a study's folder. None if that is no
    study."""
    workspace, _ = _scope(runtime)
    target = Path(study) if study else None
    if target is not None and not _holds_conversations(target):
        return None
    return target, _store_for(workspace, target)




#: The conversation made for each unnamed thread of a page, by the key the
#: page gave it: its first save and the one sent as the page closes are one
#: conversation (fifteenth and sixteenth reviews, 10-08). Kept in the store
#: in the workspace's own store (``MADE_FOR_FILE``), so a save sent again
#: after the GUI restarted is still that one (twentieth review, 10-08).
_MADE_FOR: dict[str, str] = {}
MADE_FOR_FILE = "made_for.json"

#: Said in a conversation kept after it was deleted elsewhere, from what a
#: closing page sent (twentieth review, 10-08).
KEPT_AFTER_DELETE = ("This conversation was deleted in another window. "
                     "What was said after is kept here, as a new conversation.")


#: Kept in the workspace's own store: each conversation deleted, with the
#: entries it held, so a closing page's save sent again later does not
#: bring it back (twenty-first review, 10-08); and where each conversation
#: a run took with it went, so one in a folder outside the workspace is
#: found once the GUI holds another (twenty-first review, 10-08).
DELETED_FILE = "deleted.json"
MOVED_FILE = "moved.json"
INDEX_KEPT = 500


def _index(workspace: Any, name: str) -> dict:
    import json

    try:
        known = json.loads((Path(workspace) / WORKSPACE_CONVERSATIONS_DIR / name).read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return known if isinstance(known, dict) else {}


def _remember(workspace: Any, name: str, cid: str, value: Any) -> None:
    import json

    known = _index(workspace, name)
    known.pop(str(cid), None)
    known[str(cid)] = value
    store = Path(workspace) / WORKSPACE_CONVERSATIONS_DIR
    try:
        store.mkdir(parents=True, exist_ok=True)
        _written(store / name, json.dumps(dict(list(known.items())[-INDEX_KEPT:])))
    except OSError:
        pass


def _remember_key(runtime: Any, key: Any, cid: str) -> None:
    if not key:
        return
    _MADE_FOR[str(key)] = cid
    workspace, _ = _scope(runtime)
    _remember(workspace, MADE_FOR_FILE, str(key), cid)


def _made_for(runtime: Any, key: Any) -> str | None:
    """The conversation already made for a page's key, if any: kept
    somewhere, or deleted since (then a save for it is told it is gone)."""
    if not key:
        return None
    workspace, _ = _scope(runtime)
    cid = _MADE_FOR.get(str(key)) or _index(workspace, MADE_FOR_FILE).get(str(key))
    if not isinstance(cid, str) or not _valid_id(cid):
        return None
    # A key's conversation deleted since is that one still, and gone: a
    # save sent again does not make it anew (twenty-first review, 10-08).
    if _where_it_lives(workspace, cid, runtime) is None and \
            cid not in _index(workspace, DELETED_FILE):
        return None
    _MADE_FOR[str(key)] = cid
    return cid


def _gone(workspace: Any, cid: Any) -> dict[str, Any]:
    """A save's or a /new's answer for a deleted conversation, with the
    eids it held: they went with it, and a page does not take them for what
    the person said after (twenty-fourth review, 10-08)."""
    held = _index(workspace, DELETED_FILE).get(str(cid)) or []
    return {"ok": False, "error": "No such conversation.", "gone": True,
            "held": [str(e) for e in held] if isinstance(held, list) else []}


def merge_conversation(runtime: Any, entries: Any, seen: Any, cid: Any = None,
                       study: Any = _HERE, *, key: Any = None, current: bool = True,
                       append: bool = False, dropped: Any = None, keep: Any = None,
                       keep_current: bool = False) -> dict[str, Any]:
    """A page's save, merged into what is kept (tenth to sixteenth reviews,
    10-08: the page wrote its whole list, and a reload, a second tab, a
    delete or a run's move racing it lost entries).

    Each entry carries an ``eid``. ``seen`` names every entry the page has
    held. Kept: the page's entries in its order, each over what was kept
    (a field only the server set, such as where a run went, stays); then
    every kept entry the page never held (another tab's). An entry the page
    held and no longer has was cut: it goes, and is remembered as cut, so a
    tab that still holds it does not bring it back. ``append`` (a page
    closing) only adds and updates, and cuts only what it names as
    ``dropped``. Named, the conversation is
    written where it now lives, and one that lives nowhere was deleted: not
    written again. Unnamed, a new one is made for the page's ``key`` (the
    same one for every save of that thread), never written over the one
    current where the GUI is. ``current`` false leaves which conversation
    is current alone (a thread no longer on screen). ``keep``, a key, sent
    by a closing page holding what the person said: if the conversation
    was deleted elsewhere, what it sent is kept as a new one, made for that
    key, where it was (current if ``keep_current``)."""
    if not isinstance(entries, list) or not isinstance(seen, list):
        return {"ok": False, "error": "entries and seen must be lists"}
    with _CONVERSATIONS:
        workspace, study_open = _scope(runtime)
        _migrate_single_file(workspace)
        if cid is None and key:
            made = _made_for(runtime, key)
            if made is not None:
                cid, study = made, _HERE
        if cid is None:
            # Made where the thread was begun, if it named a place (a chat
            # of no study while a study is open; nineteenth review, 10-08),
            # else where the GUI is.
            place = None if study is _HERE else _named_place(runtime, study)
            made_in, store = place or (study_open, _store_for(workspace, study_open))
            cid = _new_id()
            try:
                _write_one(store, cid, entries)
                if current:
                    (store / "current").write_text(cid, encoding="utf-8")
            except OSError as exc:
                return {"ok": False, "error": f"Could not save the conversation: {exc}"}
            _remember_key(runtime, key, cid)
            return {"ok": True, "id": cid, "entries": _with_eids(cid, _read_one(store, cid)),
                    "study": str(made_in) if made_in else None}
        if not _valid_id(cid):
            return {"ok": False, "error": "No such conversation."}
        place = (study_open, _store_for(workspace, study_open)) if study is _HERE \
            else _named_place(runtime, study)
        if place is None or not (place[1] / f"{cid}.json").is_file():
            place = _where_it_lives(workspace, str(cid), runtime)
        if place is None and append and keep:
            # Deleted elsewhere while the person went on in it: what the
            # closing page sent is kept, once, as a new conversation
            # (twentieth review, 10-08).
            # Only what the deleted conversation never held: what it held
            # was deleted with it (twenty-first review, 10-08).
            held = set(map(str, _index(workspace, DELETED_FILE).get(str(cid)) or ()))
            said = [e for e in entries if isinstance(e, dict)
                    and e.get("eid") not in set(map(str, dropped or ()))
                    and str(e.get("eid")) not in held]
            if not any(e.get("role") == "user" or e.get("action") == "run" for e in said):
                return _gone(workspace, cid)
            if not any(e.get("text") == KEPT_AFTER_DELETE for e in said):
                said.append({"eid": f"kept-{keep}", "role": "agent", "kind": "note",
                             "text": KEPT_AFTER_DELETE})
            return merge_conversation(runtime, said, [], None, study, key=keep,
                                      current=keep_current, append=True)
        if place is None:
            return _gone(workspace, cid)
        named, store = place
        record = _read_record(store, str(cid))
        kept = _with_eids(str(cid), _read_one(store, str(cid)))
        cut = [str(e) for e in record.get("cut") or ()]
        if append:
            # A closing page's cuts too (a retry with its save waiting).
            cut += [str(e) for e in (dropped or ()) if str(e) not in cut]
            merged = [e for e in _added(kept, entries, set(cut))
                      if not (isinstance(e, dict) and e.get("eid") in set(cut))]
        else:
            held = {e.get("eid") for e in entries if isinstance(e, dict)}
            cut += [e for e in map(str, seen) if e not in held and e not in cut]
            merged = _merged(kept, entries, set(map(str, seen)), set(cut))
        try:
            _write_one(store, str(cid), merged, cut=cut)
            if current:
                (store / "current").write_text(str(cid), encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "error": f"Could not save the conversation: {exc}"}
        # What of the page's is cut here, so the page tells a cut (another
        # tab's retry) from the oldest entries past the length kept
        # (nineteenth review, 10-08: every save drew the thread again).
        held_here = {str(e) for e in seen} | {
            str(e.get("eid")) for e in entries if isinstance(e, dict)}
        return {"ok": True, "id": str(cid),
                "entries": _with_eids(str(cid), _read_one(store, str(cid))),
                "study": str(named) if named else None,
                "cut": [e for e in cut if e in held_here]}


def _merged(kept: list, page: list, seen: set[str], cut: set[str]) -> list:
    by_eid = {e["eid"]: e for e in kept if isinstance(e, dict) and e.get("eid")}
    out, held = [], set()
    for entry in page:
        if not isinstance(entry, dict) or entry.get("eid") in cut:
            continue
        eid = entry.get("eid")
        if eid in held:
            # One entry, though the page held it twice (a summary another
            # tab kept too; nineteenth review, 10-08).
            continue
        if eid:
            held.add(eid)
        out.append({**by_eid[eid], **entry} if eid in by_eid else entry)
    out += [e for e in kept if isinstance(e, dict) and e.get("eid")
            and e["eid"] not in held and e["eid"] not in seen and e["eid"] not in cut]
    return out


def _added(kept: list, page: list, cut: set[str]) -> list:
    """A closing page's entries: each updates its kept self, or is added."""
    out = list(kept)
    at = {e.get("eid"): i for i, e in enumerate(out) if isinstance(e, dict)}
    for entry in page:
        if not isinstance(entry, dict) or not entry.get("eid") or entry["eid"] in cut:
            continue
        if entry["eid"] in at:
            out[at[entry["eid"]]] = {**out[at[entry["eid"]]], **entry}
        else:
            at[entry["eid"]] = len(out)
            out.append(entry)
    return out


@_one_writer
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
        if not (store / f"{cid}.json").is_file():
            # Moved since the page last heard (a run started from another
            # tab took it with it): written where it lives, and said, so the
            # page follows (twelfth review, 10-08: two copies under one id).
            named, store = _where_it_lives(workspace, str(cid), runtime) or (named, store)
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


def _where_it_lives(workspace: Path, cid: str,
                    runtime: Any = None) -> tuple[Path | None, Path] | None:
    """The place a conversation is kept: the workspace's Chats, a folder
    the GUI holds (a run's, which may be outside the workspace), or a folder
    in the workspace holding it. None if nowhere."""
    chats = Path(workspace) / WORKSPACE_CONVERSATIONS_DIR
    if (chats / f"{cid}.json").is_file():
        return None, chats
    held = [getattr(runtime, name, None) for name in ("running_root", "active_root")]
    folders = [Path(f) for f in held if f]
    folders += list(Path(workspace).iterdir()) if Path(workspace).is_dir() else []
    for folder in folders:
        store = folder / CONVERSATIONS_SUBDIR
        if (store / f"{cid}.json").is_file():
            return folder, store
    went = _index(workspace, MOVED_FILE).get(str(cid))
    if isinstance(went, str) and (Path(went) / CONVERSATIONS_SUBDIR / f"{cid}.json").is_file():
        return Path(went), Path(went) / CONVERSATIONS_SUBDIR
    return None


@_one_writer
def new_conversation(runtime: Any, study: Any = _HERE, *, key: Any = None) -> dict[str, Any]:
    """A fresh thread where the GUI is, or in ``study`` (None: a chat of no
    study, whatever is open). The last one stays. ``key``, the page's for
    the thread, names it for a save sent before this answer is heard (a
    page closing; eighteenth review, 10-08); made already by such a save,
    it is that one."""
    made = _made_for(runtime, key)
    if made is not None:
        workspace, _ = _scope(runtime)
        place = _where_it_lives(workspace, made, runtime)
        if place is not None:
            return {"ok": True, "id": made, "entries": _with_eids(made, _read_one(place[1], made)),
                    "study": str(place[0]) if place[0] else None}
        # The key's conversation was deleted: so is this thread. A new one
        # made for the key would take a closing page's save sent again
        # (twenty-second review, 10-08).
        return _gone(workspace, made)
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
    _remember_key(runtime, key, cid)
    return {"ok": True, "id": cid, "entries": [], "study": str(study) if study else None}


@_one_writer
def rename_conversation(runtime: Any, cid: Any, study: Any = None,
                        title: Any = "") -> dict[str, Any]:
    """A name of the person's own for a conversation; '' gives it back its
    first question's first line."""
    if not _valid_id(cid):
        return {"ok": False, "error": "No such conversation."}
    place = _named_place(runtime, study)
    if place is None:
        return {"ok": False, "error": "No such study."}
    if not (place[1] / f"{cid}.json").is_file():
        # Moved since the list was drawn (a run took it with it): named
        # where it lives (seventeenth review, 10-08).
        workspace, _ = _scope(runtime)
        place = _where_it_lives(workspace, str(cid), runtime)
    if place is None:
        return {"ok": False, "error": "No such conversation."}
    _, store = place
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
        if _holds_conversations(folder):
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


@_one_writer
def open_conversation(runtime: Any, cid: Any, study: Any = None) -> dict[str, Any]:
    """Make a conversation current. If it belongs to another study, load
    that study first, so the thread and the Agent's context agree."""
    if not _valid_id(cid):
        return {"ok": False, "error": "No such conversation."}
    workspace, current_study = _scope(runtime)
    target = Path(study) if study else None
    if target is not None and not _holds_conversations(target):
        return {"ok": False, "error": "No such study."}
    if not (_store_for(workspace, target) / f"{cid}.json").is_file():
        # Moved since the list was drawn (a run took it with it): opened
        # where it lives, as naming and deleting it do (nineteenth review,
        # 10-08).
        place = _where_it_lives(workspace, str(cid), runtime)
        if place is None:
            return {"ok": False, "error": "No such conversation."}
        target = place[0]
    # A folder that is no study yet (a run that wrote nothing) holds its
    # conversation; it is opened, and the GUI stays where it is.
    from fastmdxplora.gui.browse import is_study

    # What the GUI can load, as its switch judges it: a study of several
    # runs too (ninth and tenth reviews, 10-08).
    loads = target is not None and is_study(target)
    if loads and (current_study is None or target.resolve() != current_study.resolve()):
        switched = runtime.switch_to(target) if hasattr(runtime, "switch_to") else {"ok": False}
        if not switched.get("ok"):
            return {"ok": False, "error": switched.get("error") or "Could not load that study."}
    store = _store_for(workspace, target)
    if not (store / f"{cid}.json").is_file():
        return {"ok": False, "error": "No such conversation."}
    (store / "current").write_text(str(cid), encoding="utf-8")
    return {"ok": True, "id": str(cid), "entries": _with_eids(str(cid), _read_one(store, str(cid))),
            "study": str(target) if target else None, "loaded_study": loads}


@_one_writer
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
    if source_study is not None and not _holds_conversations(source_study):
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
        _remember(workspace, MOVED_FILE, cid, str(target))
        (dest / "current").write_text(cid, encoding="utf-8")
        if _current_in(source) is None:
            (source / "current").unlink(missing_ok=True)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "moved": True, "id": cid, "study": str(target)}


@_one_writer
def delete_conversation(runtime: Any, cid: Any, study: Any = None, *,
                        key: Any = None) -> dict[str, Any]:
    """Delete one conversation, wherever it is. Asked for, per
    conversation, never a side effect of anything else. ``key``, the
    page's for the thread it has on screen: the answer says whether that
    thread is this conversation (``on_screen``), though the page has not
    yet heard its name (twenty-third review, 10-08)."""
    if not _valid_id(cid):
        return {"ok": False, "error": "No such conversation."}
    workspace, _ = _scope(runtime)
    target = Path(study) if study else None
    store = _store_for(workspace, target)
    path = store / f"{cid}.json"
    if not path.is_file():
        # Moved since the list was drawn (a run took it with it): deleted
        # where it lives (sixteenth review, 10-08).
        place = _where_it_lives(workspace, str(cid), runtime)
        if place is None:
            return {"ok": False, "error": "No such conversation."}
        store = place[1]
        path = store / f"{cid}.json"
    held = [e.get("eid") for e in _with_eids(str(cid), _read_one(store, str(cid)))
            if isinstance(e, dict) and e.get("eid")]
    try:
        path.unlink()
        _remember(workspace, DELETED_FILE, str(cid), held)
        if _current_in(store) is None:
            (store / "current").unlink(missing_ok=True)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    made = (_MADE_FOR.get(str(key)) or _index(workspace, MADE_FOR_FILE).get(str(key))) \
        if key else None
    return {"ok": True, "on_screen": made == str(cid)}


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
