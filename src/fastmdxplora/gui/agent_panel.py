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

__all__ = ["model_endpoint", "propose_endpoint"]


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
        return {
            "ok": True,
            "providers": [
                {"id": name, "label": spec["label"],
                 "default_model": spec["default_model"],
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
    # The choice, not the key. A browser that never receives one cannot
    # leak one.
    return {"ok": True, "current": ModelChoice(provider, model,
                                               base_url).as_record()}


def propose_endpoint(payload: dict[str, Any]) -> dict[str, Any]:
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

    try:
        proposal = propose_config(
            request, complete, phases=list(phases),
            max_cycles=int(payload.get("attempts") or 4))
    except StudyError as exc:
        found = refusal_of(exc)
        return {"ok": False, "error": found.message, "code": found.code}

    attempts = [
        {"number": attempt.number,
         "refusal": (attempt.refusal.as_dict() if attempt.refusal else None)}
        for attempt in proposal.attempts
    ]
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
