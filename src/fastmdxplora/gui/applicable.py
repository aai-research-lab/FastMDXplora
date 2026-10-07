"""Which analyses a study's config leaves nothing for, and why.

The analysis phase plans its default run from what each analysis declares it
needs (`requires_ligand`, `requires_water` and the rest, read by the
orchestrator against the trajectory). The Config Builder offers the analyses
before there is a trajectory, and ticked every one, so a study of a protein
in water seemed to promise ligand contacts, bilayer thickness and a free
energy. This reads the same declarations against what the config and the
structure already say, and gives the reason in a few words. Where the form
cannot know (a trajectory brought from elsewhere, a selection written by
hand), nothing is ruled out: the run decides as it always has.
"""

from __future__ import annotations

from typing import Any

#: Each need, the reason it is not met, in the words the form shows.
SAID = {
    "umbrella": "Needs umbrella sampling",
    "metadynamics": "Needs metadynamics",
    "steered": "Needs a steered pull",
    "naming": "Runs only when chosen, with its selections named",
    "state_record": "Needs the run's state record",
    "bilayer": "No membrane in this study",
    "water": "Needs water in the frames",
    "ligand": "No ligand in this system",
    "crystallographic_bfactors": "An NMR entry has no crystallographic B-factors",
}


def _needs(cls: Any) -> list[str]:
    """What the class declares it needs, `naming` last: a pair the study
    names is met by choosing it, so any other need is the reason said."""
    needs = [attribute[len("requires_"):] for attribute in dir(cls)
             if attribute.startswith("requires_") and getattr(cls, attribute, False) is True]
    return sorted(needs, key=lambda need: need == "naming")


def _waterless(selection: Any) -> bool:
    """Whether the frames a run writes leave out every water: the default,
    `not water`, does. Any other selection is not read here."""
    said = " ".join(str(selection or "not water").lower().split())
    return said == "not water"


def not_applicable(config: dict[str, Any], facts: dict[str, Any] | None = None) -> dict[str, str]:
    """``{analysis: reason}`` for each analysis this study gives nothing to
    analyse, from its config and, where given, what its first structure
    holds (``facts``: ``models``, ``ligands`` kept)."""
    try:
        import fastmdxplora.analysis  # noqa: F401  (populates the registry)
        from fastmdxplora.analysis.orchestrator import _REGISTRY
    except Exception:  # noqa: BLE001 - the analysis stack is optional here
        return {}

    simulation = config.get("simulation") if isinstance(config.get("simulation"), dict) else {}
    setup = config.get("setup") if isinstance(config.get("setup"), dict) else {}
    analysis = config.get("analysis") if isinstance(config.get("analysis"), dict) else {}
    include = analysis.get("include")
    if isinstance(include, str):
        include = [part.strip() for part in include.split(",")]
    chosen = {str(part) for part in include or ()}
    phases = config.get("include_phase") or ["setup", "simulation", "analysis", "report"]
    simulated = "simulation" in phases
    # Frames brought from elsewhere: what they hold is not known here.
    elsewhere = bool(analysis.get("trajectory")) and not simulated

    reasons: dict[str, str] = {}
    for name, cls in _REGISTRY.items():
        for need in _needs(cls):
            missing = False
            if need in ("umbrella", "metadynamics", "steered"):
                missing = not simulation.get(need)
            elif need == "naming":
                # Left out of the default plan: a pair to analyse is the
                # study's question, so it runs where `include` names it.
                missing = name not in chosen
            elif need == "state_record":
                missing = not simulated
            elif elsewhere:
                continue
            elif need == "bilayer":
                missing = not setup.get("membrane")
            elif need == "water":
                missing = _waterless(simulation.get("save_selection"))
            elif need == "ligand" and facts is not None:
                missing = not facts.get("ligands") and not setup.get("ligand")
            elif need == "crystallographic_bfactors" and facts is not None:
                missing = int(facts.get("models") or 1) > 1
            if missing:
                reasons[name] = SAID.get(need, "Does not apply to this study")
                break
    return reasons
