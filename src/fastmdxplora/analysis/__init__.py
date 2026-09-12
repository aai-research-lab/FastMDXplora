"""Trajectory analysis phase.

This package implements FastMDXplora's analysis capability. The
analysis-level orchestrator (:class:`AnalysisOrchestrator`) coordinates
individual analysis modules, each a subclass of
:class:`~fastmdxplora.analysis.base.Analysis`.

The orchestrator architecture mirrors the design described in Aina & Kwan,
*J. Comput. Chem.* 2026 (DOI: 10.1002/jcc.70350), promoted from the trajectory
level to slot inside the project-level FastMDXplora pipeline.

Public API
----------

Top-level classes:

  :class:`Analysis`               -- abstract base class for analyses
  :class:`AnalysisResult`         -- result container returned by ``run()``
  :class:`AnalysisOrchestrator`   -- the analysis-level orchestrator

Helpers:

  :func:`load_trajectory`         -- load one or more trajectory files
  :func:`available_analyses`      -- list registered analysis names

Module-level constant:

  ``AVAILABLE_ANALYSES``          -- tuple of registered analysis names
                                     (lazy: reads the registry on access)

Examples
--------

Direct use of the analysis layer::

    from fastmdxplora.analysis import AnalysisOrchestrator

    ao = AnalysisOrchestrator("traj.dcd", topology="top.pdb")
    results = ao.run(include=["rmsd", "rg"])
"""

from __future__ import annotations

# Force a non-interactive matplotlib backend before any submodule imports
# pyplot. FastMDXplora's analysis figures are always written to files, never
# shown, and MD typically runs on headless machines (CI, HPC clusters,
# servers) with no display. Without this, matplotlib tries an interactive
# backend (e.g. Tk) and crashes ("Can't find a usable init.tcl"). Set once,
# here, since this package is imported before any analysis module's pyplot.
import matplotlib as _matplotlib

_matplotlib.use("Agg")

from fastmdxplora.analysis.base import Analysis, AnalysisResult
from fastmdxplora.analysis.loading import TrajectoryLoadError, load_trajectory
from fastmdxplora.analysis.orchestrator import (
    AnalysisOrchestrator,
    available_analyses,
    get_analysis_class,
    register_analysis,
)

# Concrete analyses register themselves at import time. Each module
# imported below adds its class to the registry. The order determines the
# canonical execution order when ``include`` / ``exclude`` are not passed.
from fastmdxplora.analysis import pmf as _pmf  # noqa: F401, E402
from fastmdxplora.analysis import metad_surface as _metad_surface  # noqa: F401, E402
from fastmdxplora.analysis import steered_work as _steered_work  # noqa: F401, E402
from fastmdxplora.analysis import rmsd as _rmsd  # noqa: F401, E402
from fastmdxplora.analysis import rmsf as _rmsf  # noqa: F401, E402
from fastmdxplora.analysis import rg as _rg  # noqa: F401, E402
from fastmdxplora.analysis import hbonds as _hbonds  # noqa: F401, E402
from fastmdxplora.analysis import ss as _ss  # noqa: F401, E402
from fastmdxplora.analysis import sasa as _sasa  # noqa: F401, E402
from fastmdxplora.analysis import dihedrals as _dihedrals  # noqa: F401, E402
from fastmdxplora.analysis import qvalue as _qvalue  # noqa: F401, E402
from fastmdxplora.analysis import (  # noqa: F401, E402
    order_parameters as _order_parameters,
)
from fastmdxplora.analysis import (  # noqa: F401, E402
    bfactor_comparison as _bfactor_comparison,
)
from fastmdxplora.analysis import (  # noqa: F401, E402
    thermodynamics as _thermodynamics,
)
from fastmdxplora.analysis import rdf as _rdf  # noqa: F401, E402
from fastmdxplora.analysis import cluster as _cluster  # noqa: F401, E402
from fastmdxplora.analysis import dimred as _dimred  # noqa: F401, E402
# Needs an explicitly solvated system, so it is not ligand-gated -- a water
# site around a protein is a finding whether or not anything is bound.
from fastmdxplora.analysis import water_sites as _water_sites  # noqa: F401, E402
# Ligand-aware analyses (run automatically only when a ligand is present).
from fastmdxplora.analysis import ligand_rmsd as _ligand_rmsd  # noqa: F401, E402
from fastmdxplora.analysis import ligand_rmsf as _ligand_rmsf  # noqa: F401, E402
from fastmdxplora.analysis import contacts as _contacts  # noqa: F401, E402
from fastmdxplora.analysis import pl_hbonds as _pl_hbonds  # noqa: F401, E402
from fastmdxplora.analysis import pl_interactions as _pl_interactions  # noqa: F401, E402


def __getattr__(name: str):
    """Lazy module-level attribute for the registry view."""
    if name == "AVAILABLE_ANALYSES":
        return available_analyses()
    raise AttributeError(name)


# Re-export the project-level adapter (``run``) so the FastMDXplora
# orchestrator's lazy-import path keeps working unchanged.
from fastmdxplora.analysis.analyze import run  # noqa: E402

__all__ = [
    "Analysis",
    "AnalysisResult",
    "AnalysisOrchestrator",
    "TrajectoryLoadError",
    "available_analyses",
    "get_analysis_class",
    "load_trajectory",
    "register_analysis",
    "run",
]

from fastmdxplora.analysis.joining import join_segments, survey_segments  # noqa: E402,F401
