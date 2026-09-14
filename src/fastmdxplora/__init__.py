"""FastMDXplora: Fully Automated SysTem for Molecular Dynamics eXploration.

FastMDXplora is a project-level orchestrator for end-to-end molecular dynamics
studies. A single object coordinates four phases — setup, simulation, analysis,
and reporting — and a single CLI invocation can take a protein structure from
input to publication-quality deliverable.

The orchestrator pattern follows a phase-based design (Aina & Kwan,
J. Comput. Chem. 2026, DOI: 10.1002/jcc.70350) from the trajectory level to
the project level. FastMDXplora holds shared
state, knows its registered phases, applies intelligent defaults, validates
options, and consolidates outputs.

Quick start
-----------

Python:

    from fastmdxplora import FastMDXplora

    fmdx = FastMDXplora(system="protein.pdb")
    fmdx.explore()

CLI:

    fastmdx explore -system protein.pdb
    fastmdx xplore -pdb-id 1L2Y

Subpackages
-----------

    fastmdxplora.setup        System preparation (fix, solvate, ionize)
    fastmdxplora.simulation   MD simulation (minimize, NVT, NPT, production)
    fastmdxplora.analysis     Trajectory analysis
    fastmdxplora.report       Slide decks, structured reports, project bundles
"""

from __future__ import annotations

try:
    from fastmdxplora._version import version as __version__
except ImportError:
    __version__ = "0.1.0"

__author__ = "Adekunle Aina, Derrick Kwan"
__license__ = "MIT"
__expansion__ = "Fully Automated SysTem for Molecular Dynamics eXploration"
__citation__ = (
    "Aina, A.; Kwan, D. FastMDAnalysis: Software for Automated Analysis of "
    "Molecular Dynamics Trajectories. J. Comput. Chem. 2026, 47, e70350. "
    "DOI: 10.1002/jcc.70350"
)
__doi__ = "10.1002/jcc.70350"

#: The same reference as a BibTeX entry. Beside the prose one so the two
#: cannot describe different papers: there were two copies of this, in the
#: report and in the GUI, and a third was very nearly added before a test
#: pointed out that the page was storing what it should have been given.
__bibtex__ = """@article{aina2026fastmd,
  author  = {Aina, Adekunle and Kwan, Derrick},
  title   = {FastMDAnalysis: Software for Automated Analysis of Molecular
             Dynamics Trajectories},
  journal = {Journal of Computational Chemistry},
  volume  = {47},
  number  = {8},
  pages   = {e70350},
  year    = {2026},
  doi     = {10.1002/jcc.70350},
}"""

# Canonical Python version range for the full conda install (setup +
# simulation stages). MAX_PYTHON is the *exclusive* upper bound, so 3.13.x
# is the highest supported, matching `pyproject.toml`'s
# ``requires-python = ">=3.9, <3.14"``. Update all three (this file,
# pyproject, and environment.yml) together when
# the OpenMM / PDBFixer compatibility window moves.
MIN_PYTHON: tuple[int, int] = (3, 9)
MAX_PYTHON: tuple[int, int] = (3, 14)


def python_range_string() -> str:
    """Human-readable supported Python range, e.g. ``"Python 3.9-3.13"``."""
    return (
        f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}"
        f"\u2013{MAX_PYTHON[0]}.{MAX_PYTHON[1] - 1}"
    )


from fastmdxplora.orchestrator import FastMDXplora

# Expose the analysis-level orchestrator for users who want it directly.
# Lazy: only imported on first access, so the heavy analysis deps (MDTraj,
# matplotlib, scikit-learn) are not loaded by `import fastmdxplora` for
# users who only call the simulation or report phases.
def __getattr__(name: str):
    if name == "AnalysisOrchestrator":
        from fastmdxplora.analysis import AnalysisOrchestrator

        return AnalysisOrchestrator
    raise AttributeError(name)


from fastmdxplora.refusals import Refusal, refusal_of

__all__ = [
    "FastMDXplora",
    "AnalysisOrchestrator",
    "Refusal",
    "refusal_of",
    "MIN_PYTHON",
    "MAX_PYTHON",
    "python_range_string",
    "__version__",
    "__author__",
    "__license__",
    "__expansion__",
    "__citation__",
    "__doi__",
]
