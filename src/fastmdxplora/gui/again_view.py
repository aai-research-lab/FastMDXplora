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
            "previous": again.PREVIOUS}
