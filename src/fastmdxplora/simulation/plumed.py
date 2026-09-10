"""PLUMED enhanced-sampling integration for the simulation phase.

PLUMED (https://www.plumed.org) adds collective-variable-based biasing and
analysis to an MD run — metadynamics, umbrella sampling, steered MD, and
free-energy methods. FastMDXplora wires it in optionally: when a PLUMED
script is supplied, its biasing forces are added to the OpenMM ``System``
before the simulation context is built, and PLUMED's output files (COLVAR,
HILLS, etc.) are redirected into the run's output directory.

The biasing is applied to the production stage only — equilibration (NVT/NPT)
runs unbiased, matching the standard enhanced-sampling protocol of
equilibrating the system before turning on the bias. The biasing force is
added to the OpenMM ``System`` just before production and the context is
reinitialized so it takes effect.

This integration is optional and degrades gracefully: without the
``openmm-plumed`` package installed, enabling PLUMED raises a clear,
actionable error rather than failing obscurely.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastmdxplora.utils.logging import get_logger

logger = get_logger("simulation.plumed")


class PlumedError(RuntimeError):
    """Raised for PLUMED configuration or environment problems."""


def _import_plumed():
    """Import openmm-plumed's PlumedForce, with an actionable error if absent."""
    try:
        from openmmplumed import PlumedForce
    except ImportError as exc:  # pragma: no cover - exercised only without the dep
        raise PlumedError(
            "PLUMED enhanced sampling requires the 'openmm-plumed' package, "
            "which is not installed. Install it with:\n"
            "    conda install -c conda-forge openmm-plumed\n"
            "or disable PLUMED (simulation.plumed.enabled = false)."
        ) from exc
    return PlumedForce


def load_plumed_script(script: str | Path) -> str:
    """Resolve a PLUMED script given either an inline script or a file path.

    If ``script`` names an existing file, its contents are read; otherwise it
    is treated as the literal PLUMED script text (so a short script can be
    given inline in the config).
    """
    text = str(script)
    # Heuristic: a path-like single line that exists on disk -> read the file.
    candidate = Path(text.strip()) if "\n" not in text else None
    if candidate is not None and candidate.exists():
        return candidate.read_text(encoding="utf-8")
    if candidate is not None and (candidate.suffix in {".dat", ".plumed", ".txt"}
                                  or "/" in text or "\\" in text):
        # Looked like a path but doesn't exist — fail clearly rather than
        # silently treating a typo'd path as an (invalid) inline script.
        raise PlumedError(f"PLUMED script file not found: {candidate}")
    return text


def adjust_plumed_output_paths(script: str, output_dir: Path) -> str:
    """Redirect PLUMED ``FILE=`` outputs into the run's output directory.

    PLUMED scripts write COLVAR/HILLS/etc. to whatever ``FILE=`` names; we
    rewrite those to live under ``output_dir`` (keeping only the basename) so
    a run's PLUMED outputs land with its other artifacts, using forward
    slashes for cross-platform correctness.
    """
    lines = []
    for line in script.splitlines():
        if "FILE=" in line:
            match = re.search(r"FILE=(\S+)", line)
            if match:
                filename = Path(match.group(1)).name
                new_path = (output_dir / filename).as_posix()
                line = line.replace(match.group(1), new_path)
        lines.append(line)
    return "\n".join(lines)


def add_plumed_force(
    omm: dict,
    system: Any,
    plumed_config: dict[str, Any],
    output_dir: Path,
    *,
    resolved_name: str = "plumed.dat",
) -> Any | None:
    """Add a PLUMED biasing force to ``system`` if PLUMED is enabled.

    Parameters
    ----------
    omm : dict
        The OpenMM handle dict (unused directly, accepted for symmetry with
        other force helpers and possible future use).
    system : openmm.System
        The System to which the PlumedForce is added. The caller adds this
        just before the production stage and reinitializes the context, so
        equilibration runs unbiased.
    plumed_config : dict
        The ``simulation.plumed`` config block. Recognized keys:
          - ``enabled`` (bool): master switch (default False).
          - ``script`` (str): inline PLUMED script text, or a path to a
            ``.dat``/``.plumed`` file.
    output_dir : pathlib.Path
        Directory for PLUMED output files (COLVAR, HILLS, ...).
    resolved_name : str
        What to call the copy of the resolved script saved beside the run.
        A run that attaches twice -- an umbrella window, held through
        equilibration with one script and switched to another at production
        -- would otherwise have the second overwrite the first, and the
        record of what actually ran during equilibration would be the
        production script.

    Returns
    -------
    The PlumedForce object if added, else ``None`` (PLUMED disabled).
    """
    if not plumed_config or not plumed_config.get("enabled", False):
        return None

    script_spec = plumed_config.get("script")
    if not script_spec:
        raise PlumedError(
            "simulation.plumed.enabled is true but no 'script' was provided. "
            "Supply a PLUMED script (inline text or a path to a .dat file)."
        )

    PlumedForce = _import_plumed()

    script = load_plumed_script(script_spec)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    script = adjust_plumed_output_paths(script, output_dir)

    force = PlumedForce(script)
    system.addForce(force)

    # Save the resolved script alongside the run for reproducibility.
    resolved = output_dir / resolved_name
    resolved.write_text(script, encoding="utf-8")
    # Said by the caller, not here. This runs inside a block that redirects
    # the file descriptors to catch PLUMED's own forty lines of setup, and a
    # message logged in there goes into the capture file with them -- so the
    # one line saying the bias was attached would vanish from the terminal.
    return force
