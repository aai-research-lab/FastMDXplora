"""Optional-backend checks and beginner-facing installation guidance."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from fastmdxplora.refusals import CodedError


@dataclass(frozen=True)
class MissingDependency:
    """A backend package that is required for a requested workflow."""

    label: str
    module: str
    package: str


CHEMISTRY_DEPENDENCIES = (
    MissingDependency("OpenMM", "openmm.app", "openmm"),
    MissingDependency("PDBFixer", "pdbfixer", "pdbfixer"),
)


def missing_dependencies(*, include_analysis: bool = False) -> list[MissingDependency]:
    """Return chemistry/analysis packages unavailable in this interpreter."""
    required = list(CHEMISTRY_DEPENDENCIES)
    if include_analysis:
        required.append(MissingDependency("MDTraj", "mdtraj", "mdtraj"))
    missing: list[MissingDependency] = []
    for dependency in required:
        try:
            importlib.import_module(dependency.module)
        except Exception:  # noqa: BLE001 - broken partial installs are missing too
            missing.append(dependency)
    return missing


def install_command(missing: list[MissingDependency]) -> str:
    """Return the recommended command for the missing packages."""
    packages = " ".join(dict.fromkeys(item.package for item in missing))
    return f"conda install -c conda-forge {packages}"


def dependency_error_message(missing: list[MissingDependency]) -> str:
    """Return one actionable message shared by CLI, API, and dashboard."""
    names = [item.label for item in missing]
    labels = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    they, them = ("it is", "it") if len(names) == 1 else ("they are", "them")
    command = install_command(missing)
    return (
        f"This workflow needs {labels}, but {they} not installed in the "
        f"Python environment running FastMDXplora. Install {them} in that same "
        f"environment with:\n\n    {command}\n\n"
        "Then restart the terminal or dashboard and try again. "
        "To analyse a trajectory you already have, run only "
        "`--include-phase analysis report`."
    )


class MissingBackendError(CodedError, RuntimeError):
    """Raised when a requested phase cannot run without an optional backend."""

    default_code = "environment.backend.missing"

    def __init__(self, missing: list[MissingDependency]) -> None:
        self.missing = tuple(missing)
        # The install command is already computed for the message. Carrying
        # it as a detail as well means a caller that can act on it does not
        # have to find it inside a paragraph of prose written for a person.
        packages = list(dict.fromkeys(dep.package for dep in missing))
        super().__init__(
            dependency_error_message(missing),
            packages=packages,
            install_command=install_command(missing),
        )
