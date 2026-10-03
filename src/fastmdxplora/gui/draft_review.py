"""Read-only draft differences and short-lived review binding; no execution."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from typing import Any

_KEY = secrets.token_bytes(32)  # Process-local signing key, not provider credentials.
REVIEW_SECONDS = 600


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _binding(runtime: Any) -> dict:
    root = getattr(runtime, "active_root", None)
    if getattr(runtime, "data_stale", False):
        raise ValueError("Reload the current study before reviewing a draft.")
    record: dict = {"study": str(root) if root else None, "sources": {}}
    if root:
        base = Path(root).resolve()
        for relative in ("setup/setup_parameters.json", "simulation/simulation_parameters.json"):
            path = base / relative
            if not path.exists():
                continue
            if not path.resolve().is_relative_to(base) or path.stat().st_size > 2_000_000:
                raise ValueError("Study review evidence is unavailable or too large.")
            record["sources"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return record


def _signature(config: dict, before: dict, runtime: Any, purpose: str, expiry: int) -> str:
    message = _canonical({"config": config, "before": before, "study": _binding(runtime),
                          "purpose": purpose, "expiry": expiry})
    return hmac.new(_KEY, message, hashlib.sha256).hexdigest()


def _verify(token: Any, config: dict, before: dict, runtime: Any, purpose: str) -> None:
    if not isinstance(token, str) or len(token) > 100:
        raise ValueError("Review this exact draft before continuing.")
    try:
        stamp, signature = token.split(".", 1)
        expiry = int(stamp)
    except (ValueError, TypeError) as exc:
        raise ValueError("Review this exact draft before continuing.") from exc
    if not time.time() <= expiry <= time.time() + REVIEW_SECONDS + 1:
        raise ValueError("Draft review expired. Review the current draft again.")
    if not hmac.compare_digest(signature, _signature(config, before, runtime, purpose, expiry)):
        raise ValueError("The draft, builder or study changed. Review the differences again.")


def _differences(before: dict, after: dict) -> list[dict]:
    from fastmdxplora.config.schema import all_schemas

    changes = []
    missing = object()
    def walk(old: Any, new: Any, trail: list[str]) -> None:
        if isinstance(old, dict) and (isinstance(new, dict) or new is missing) or isinstance(new, dict) and old is missing:
            old = {} if old is missing else old
            new = {} if new is missing else new
            for key in sorted(set(old) | set(new)):
                walk(old.get(key, missing), new.get(key, missing), [*trail, key])
        elif old != new:
            name = ".".join(trail)
            schema = all_schemas().get(trail[0]) if len(trail) == 2 else None
            field = schema.get(trail[1]) if schema else None
            changes.append({"field": name,
                            "before": None if old is missing else old, "before_present": old is not missing,
                            "after": None if new is missing else new, "after_present": new is not missing,
                            "help": field.help if field else "Review this configuration change and its study scope.",
                            "default": field.default if field else None,
                            "has_schema": field is not None})
    walk(before, after, [])
    return changes


def review_endpoint(payload: dict, runtime: Any) -> dict:
    from fastmdxplora.gui.config_builder import build_config, render_config, state_from_config

    try:
        before = payload.get("builder_state")
        if not isinstance(before, dict):
            raise ValueError("Supply the current builder draft for review.")
        purpose = payload.get("purpose", "draft")
        if purpose not in ("draft", "run"):
            raise ValueError("Unknown draft review action.")
        config = build_config(before) if purpose == "run" else payload.get("config")
        if not isinstance(config, dict):
            raise ValueError("Supply a configuration to review.")
        checked = render_config(config)
        if not checked["ok"]:
            return checked
        baseline = build_config(before)
        # Short YAML intentionally omits explicit values equal to defaults.
        # Review must still show the actual value the human selected in the form.
        full_before = build_config(before, full=True)
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        for phase in PHASE_SCHEMAS:
            chosen = before.get(phase)
            values = full_before.get(phase)
            if isinstance(chosen, dict) and isinstance(values, dict):
                for name in chosen:
                    if name in values:
                        baseline.setdefault(phase, {})[name] = values[name]
        binding = _binding(runtime)
        if "study" in payload and payload["study"] != binding["study"]:
            raise ValueError("The study changed. Review the current study again.")
        if payload.get("action") == "accept":
            if payload.get("confirmed") is not True:
                raise ValueError("Confirm human review before adding the draft.")
            _verify(payload.get("review_token"), config, before, runtime, purpose)
            return state_from_config(config)
        if payload.get("action", "preview") != "preview":
            raise ValueError("Unknown draft review action.")
        expiry = int(time.time()) + REVIEW_SECONDS
        return {"ok": True, "changes": _differences({} if purpose == "run" else baseline, config), "yaml": checked["yaml"],
                "study": binding["study"], "purpose": purpose,
                "review_token": f"{expiry}.{_signature(config, before, runtime, purpose, expiry)}",
                "notice": "Validation checks configuration consistency, not scientific suitability. Missing values are not equivalent to explicit defaults. Review the full configuration before a human-controlled run."}
    except (ValueError, TypeError, OSError) as exc:
        return {"ok": False, "error": str(exc)}


def verify_run_review(payload: dict, runtime: Any) -> dict | None:
    """Agent-authored builder runs need review bound to the final exact state."""
    from fastmdxplora.gui.config_builder import build_config

    try:
        config = build_config(payload)
        if not config.get("agent"):
            return None
        if payload.get("review_confirmed") is not True:
            raise ValueError("Review the exact Agent draft before running it.")
        before = {key: value for key, value in payload.items() if key not in {"review_token", "review_confirmed"}}
        _verify(payload.get("review_token"), config, before, runtime, "run")
    except (ValueError, TypeError, OSError) as exc:
        return {"ok": False, "error": str(exc), "code": "config.option.not_permitted"}
    return None
