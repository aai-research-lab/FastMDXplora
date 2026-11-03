"""What the GUI offers to run again on the study on screen (`GET /api/again`).

The Analysis page's **Analyze again** and the Report page's **Write it
again** read it: whether each phase can run again and, where not, why;
the analyses the study ran last, to offer ticked; and the analyses this
release has, each with its title, to add one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def again_payload(root: Any) -> dict[str, Any]:
    from fastmdxplora import again
    from fastmdxplora.gui.browse import is_study

    study = Path(root) if root else None
    if study is None or not study.is_dir() or not is_study(study):
        return {"available": False, "why": "No study is open."}
    offered = again.offered(study)
    try:
        import fastmdxplora.analysis  # noqa: F401  (fills the registry)
        from fastmdxplora.analysis.describe import explain_analysis
        from fastmdxplora.analysis.orchestrator import available_analyses

        catalogue = [{"name": name, "title": explain_analysis(name).get("title") or name}
                     for name in available_analyses()]
    except Exception:  # noqa: BLE001 - the analysis stack is optional here
        catalogue = []
    return {"available": True, **offered, "catalogue": catalogue,
            "not_applicable": _not_applicable(study), "previous": again.PREVIOUS}


def _not_applicable(study: Path) -> dict[str, str]:
    """``{analysis: reason}`` for the analyses the study's recorded Config
    leaves nothing for (`applicable.not_applicable`, as New study reads
    it): a study never analysed was offered all of them ticked, a ligand's
    and a membrane's for a protein in water. Nothing where the Config is
    not read (the analysis decides, as it always has)."""
    import yaml

    from fastmdxplora import again, record_access
    from fastmdxplora.config.recorded import RECORDED
    from fastmdxplora.gui.applicable import not_applicable

    run = study
    if again._several(study):
        runs = again._runs_of(study)
        run = runs[0] if runs else study
    try:
        config = yaml.safe_load(record_access.read_text(run / RECORDED, encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return {}
    return not_applicable(config) if isinstance(config, dict) else {}


def again_fix(root: Any, action: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """What the Agent's `DO: analyze again` or `DO: write the report again`
    would run on the study on screen, for the person to confirm; or why it
    cannot. Checked here, from the study, before the person is asked: the
    analyses come from the reply's one line, read by a strict pattern."""
    from fastmdxplora import again
    from fastmdxplora.gui.browse import is_study
    from fastmdxplora.refusals import refusal_of

    study = Path(root) if root else None
    if study is None or not study.is_dir() or not is_study(study):
        return {"reason": "No study is open to run again."}
    phases = ["report"] if action == "write the report again" else ["analysis"]
    named = (arguments or {}).get("analyses")
    try:
        planned = again.plan(study, phases, named)
    except Exception as exc:  # noqa: BLE001 - said, and nothing runs
        return {"reason": refusal_of(exc).message}
    if planned.phases == ("report",):
        question = ("Write the report again from this study's records, keeping the report "
                    "there now in previous/")
    else:
        chosen = planned.analyses or tuple(again.recorded_analyses(study))
        listed = ", ".join(chosen) if chosen else "the analyses it ran last"
        question = (f"Analyze this study again with {listed}"
                    + (", writing its report again too" if planned.report_added else "")
                    + ", keeping what they replace in previous/")
    return {"fix": {"action": action, "route": "/api/again", "fix": question,
                    "command": " ".join(["fastmdx", *planned.command()]),
                    "request": {"phases": list(planned.phases),
                                "analyses": list(planned.analyses) if planned.analyses else None}}}
