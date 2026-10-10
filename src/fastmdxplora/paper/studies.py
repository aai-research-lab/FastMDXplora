"""From a paper to configs: what the command line, the GUI, the Agent and
an AI app share.

:func:`studies_in` reads a paper and the studies FastMDXplora finds in it;
:func:`plans_for` writes each as a plan with its config; :func:`write_configs`
puts the chosen ones on disk, each checked by the validator that checks any
config. A study that cannot run is never written; one that needs something
from you is written so that it is refused until you supply it.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastmdxplora.paper import PaperRefused
from fastmdxplora.paper.text import PaperText

__all__ = ["studies_in", "plans_for", "choose", "write_configs", "config_text", "said_of",
           "length_said", "the_ai_model", "STATE_SAID"]

STATE_SAID = {
    "ready": "ready",
    "with_differences": "runs, with differences",
    "needs_you": "needs you",
    "cannot_run": "cannot run here",
}


def the_ai_model() -> tuple[Callable[[str], str], str]:
    """The completion of the AI model chosen with `fastmdx agent model`, and
    its name as ``provider/model`` for the record."""
    from fastmdxplora.agent.models import completion_for, load_choice

    choice = load_choice()
    complete = completion_for(choice)
    name = f"{choice.provider}/{choice.model}" if choice is not None else ""
    return complete, name


def studies_in(source: str, si: list[str] | tuple[str, ...] = (), *,
               complete: Callable[[str], str] | None = None, model: str = "",
               said: Callable[[str], None] | None = None,
               use_kept: bool = True) -> tuple[PaperText, dict[str, Any]]:
    """The paper ``source`` names and its MD studies as FastMDXplora reads
    them, checked (:func:`fastmdxplora.paper.extract.read_studies`). Without
    ``complete``, the AI model chosen with `fastmdx agent model`."""
    from fastmdxplora.paper.extract import read_studies
    from fastmdxplora.paper.fetch import open_paper

    paper = open_paper(source, si, said=said)
    if complete is None:
        complete, model = the_ai_model()
    reading = read_studies(paper, complete, model=model, said=said, use_kept=use_kept)
    if not reading.get("studies"):
        raise PaperRefused(
            f"FastMDXplora found no MD study in {paper.title or source}. If it has one, "
            "its methods may be in a supporting information not given: add it with "
            "--paper-si FILE.", code="environment.paper.unreadable")
    return paper, reading


def plans_for(reading: dict[str, Any], *, until_determined: bool = False,
              output_root: str | None = None) -> list[dict[str, Any]]:
    """Each study of a reading as a plan with its config (:func:`mapping.plan_study`)."""
    from fastmdxplora.paper.mapping import openmm_files, plan_study, slug

    files = openmm_files()
    plans = []
    for study in reading.get("studies") or []:
        output = None
        if output_root:
            output = str(Path(output_root) / slug(f"{study.get('id')}-{study.get('label')}", 40))
        plans.append(plan_study(study, reading, until_determined=until_determined,
                                files=files, output=output))
    return plans


def choose(plans: list[dict[str, Any]], asked: str | list[str]) -> list[dict[str, Any]]:
    """The plans ``asked`` names: ``all``, or ids and numbers as ``S1,S3`` or
    ``1 3``. Refused where one names no study."""
    words = asked if isinstance(asked, list) else re.split(r"[\s,;]+", str(asked or ""))
    words = [str(word).strip() for word in words if str(word).strip()]
    if not words:
        return []
    if any(word.lower() == "all" for word in words):
        return list(plans)
    by_id = {str(plan["id"]).upper(): plan for plan in plans}
    chosen = []
    for word in words:
        key = word.upper()
        key = f"S{key}" if key.isdigit() else key
        if key not in by_id:
            raise PaperRefused(
                f"The paper has no study {word}: its studies are "
                + ", ".join(str(plan["id"]) for plan in plans) + ", or all.",
                code="config.option.not_permitted")
        if by_id[key] not in chosen:
            chosen.append(by_id[key])
    return chosen


def length_said(plan: dict[str, Any]) -> str:
    production = (((plan.get("config") or {}).get("simulation") or {}).get("duration_ns"))
    stop = (((plan.get("config") or {}).get("simulation") or {}).get("stop_when"))
    replicas = int(plan.get("replicas") or 1)
    if stop:
        return f"until determined, at most {replicas} x {_ns(stop.get('max_duration_ns'))}"
    if not isinstance(production, (int, float)):
        return "length not stated"
    return (f"{replicas} x {_ns(production)}" if replicas > 1 else _ns(production))


def _ns(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "?"
    return f"{number / 1000:g} µs" if number >= 1000 else f"{number:g} ns"


def said_of(plan: dict[str, Any]) -> str:
    """One line for a study: its id, label, method, length and state."""
    method = plan.get("method") or "plain"
    method_said = "" if method == "plain" else f", {method.replace('_', ' ')}"
    return (f"{plan['id']}  {plan.get('label', '')}{method_said}  "
            f"[{length_said(plan)}]  {STATE_SAID[plan['state']]}")


def write_configs(plans: list[dict[str, Any]], file: str | Path, *,
                  force: bool = False) -> list[dict[str, Any]]:
    """The chosen plans written as configs: one to ``file``, several beside
    it as ``<stem>-<study>.yml``. Each written is checked by the validator
    (``refused`` says why where it is). A plan that cannot run is skipped
    (``written`` False)."""
    import yaml

    from fastmdxplora.config.loader import ConfigError, validate_config
    from fastmdxplora.paper.mapping import distinct, slug

    target = Path(file).expanduser()
    runnable = [plan for plan in plans if plan.get("config") is not None]
    names = iter(distinct([slug(str(plan["id"]), 8) for plan in runnable]))
    out = []
    for plan in plans:
        if plan.get("config") is None:
            out.append({"plan": plan, "written": False, "path": None, "refused": None})
            continue
        name = next(names)
        path = target if len(runnable) == 1 else target.with_name(
            f"{target.stem}-{name}{target.suffix or '.yml'}")
        if path.exists() and not force:
            raise PaperRefused(
                f"{path} already exists. Use --force-overwrite, or -f to choose another "
                "file.", code="environment.path.exists", path=str(path))
        out.append({"plan": plan, "path": path})
    for entry in out:
        if entry.get("written") is False:
            continue
        plan = entry["plan"]
        text = config_text(plan)
        entry["path"].parent.mkdir(parents=True, exist_ok=True)
        entry["path"].write_text(text, encoding="utf-8")
        entry["written"] = True
        try:
            validate_config(yaml.safe_load(text), require_systems=True)
            entry["refused"] = None
        except ConfigError as exc:
            entry["refused"] = str(exc)
    return out


def config_text(plan: dict[str, Any]) -> str:
    """A plan's config as the YAML file it is written as, headed by what it
    is and what it still needs."""
    import yaml

    text = _header(plan) + yaml.dump(plan["config"], Dumper=_Plain, sort_keys=False,
                                     allow_unicode=True, width=88)
    # The header is comments made from the paper's and the AI model's words:
    # read back, the file must be the config and nothing more.
    if yaml.safe_load(text) != plan["config"]:
        raise PaperRefused("The config written from this study did not read back as itself, "
                           "so it was not written.", code="environment.paper.unreadable")
    return text


def _one_line(text: Any) -> str:
    """``text`` on one line: a comment made of the paper's words must not end
    early, or what follows would be read as settings."""
    return re.sub(r"[\s\x00-\x1f\x7f-\x9f\u2028\u2029]+", " ", str(text)).strip()


def _plain_dumper() -> Any:
    import yaml

    class Plain(yaml.SafeDumper):
        """YAML a person reads: a value written twice is written twice,
        not as an anchor and an alias."""

        def ignore_aliases(self, data: Any) -> bool:
            return True

    return Plain


_Plain = _plain_dumper()


def _header(plan: dict[str, Any]) -> str:
    paper = (plan.get("config") or {}).get("paper") or {}
    lines = [f"# {_one_line(plan['id'])}: {_one_line(plan.get('label', ''))}",
             f"# Written from {_one_line(paper.get('title') or 'a paper')}"
             + (f" (doi:{_one_line(paper['doi'])})" if paper.get("doi") else "")
             + (f", as read by {_one_line(paper['read_by'])}" if paper.get("read_by") else "")
             + ".",
             f"# State: {STATE_SAID[plan['state']]}. Each setting's reason is in `decisions`;"
             " how each came from the paper is in `paper.choices`."]
    needs = [c for c in plan.get("choices") or [] if c.get("label") == "needs_you"]
    if needs:
        lines.append("# Before it runs:")
        for choice in needs:
            lines.extend(f"#   {line}" for line in _wrap(f"- {_one_line(choice['why'])}", 84))
    return "\n".join(lines) + "\n"


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(text, width) or [text]
