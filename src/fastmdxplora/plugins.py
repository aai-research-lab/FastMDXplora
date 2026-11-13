"""What separately installed packages add to FastMDXplora.

The core never imports a package that extends it. Such a package says what
it offers in its own metadata, as entry points in one of the groups below,
and this module reads them: the core asks for a group by name and is handed
what the installed packages offer in it.

Nothing is registered by importing a plug-in. Each group is read at one
place in the core, which then does the registering itself: the analyses at
the foot of :mod:`fastmdxplora.analysis`, after the core's own
(:func:`fastmdxplora.analysis.orchestrator.register_plugged_in`).

In a source checkout the project's own plug-ins are in ``packages/``, and
one that is not installed is read from there (:func:`_from_the_checkout`),
so a clone made before a module moved runs as it did.

A plug-in that fails to load is left out, named with why
(:func:`failures`, the log, and ``fastmdx info``), and the core starts
without it. A study that then asks for what it offered is refused with the
package to install (:func:`unavailable`): ``environment.plugin.missing``
where the package is not installed, ``environment.plugin.broken`` where it
is and would not load.

Groups
------
``fastmdxplora.analyses``
    The entry point's name is the analysis's name, as a study's
    ``analysis.include`` gives it; it loads to a subclass of
    :class:`fastmdxplora.analysis.base.Analysis`. The core registers the
    class under that name; the module that holds it registers nothing.
``fastmdxplora.moved_modules``
    The entry point's name is a module path the core used to have
    (``fastmdxplora.analysis.lipid_order``), its value the module that is
    now that one. An ``import`` of the old path gives the new module, so a
    script written against the old layout runs as it did.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastmdxplora.utils.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "ANALYSES",
    "MOVED_MODULES",
    "GROUPS",
    "FIRST_PARTY",
    "Failure",
    "Unavailable",
    "failures",
    "forget",
    "installed",
    "load",
    "offered",
    "report",
    "unavailable",
    "waiting",
    "find_moved_modules",
]

#: Analyses, by the name a study gives them.
ANALYSES = "fastmdxplora.analyses"
#: Modules the core used to hold, by the path they had.
MOVED_MODULES = "fastmdxplora.moved_modules"
#: Every group the core reads.
GROUPS = (ANALYSES, MOVED_MODULES)

#: What each of this project's own plug-ins offers, by group and name, and
#: the package that brings it. Names only: nothing here is imported. It is
#: how a study asking for something whose package is absent can be told
#: which package that is, since an absent package cannot say so itself.
FIRST_PARTY: dict[tuple[str, str], str] = {
    (ANALYSES, "area_per_lipid"): "fastmdxplora-membrane",
    (ANALYSES, "bilayer_thickness"): "fastmdxplora-membrane",
    (ANALYSES, "lipid_order"): "fastmdxplora-membrane",
    (MOVED_MODULES, "fastmdxplora.analysis.bilayer"): "fastmdxplora-membrane",
    (MOVED_MODULES, "fastmdxplora.analysis.area_per_lipid"): "fastmdxplora-membrane",
    (MOVED_MODULES, "fastmdxplora.analysis.bilayer_thickness"): "fastmdxplora-membrane",
    (MOVED_MODULES, "fastmdxplora.analysis.lipid_order"): "fastmdxplora-membrane",
}


@dataclass(frozen=True)
class Failure:
    """One thing a plug-in offered that could not be used."""

    group: str
    name: str
    #: The distribution that offered it, as ``pip`` names it.
    package: str
    #: Where it was to be loaded from (``package.module:object``).
    value: str
    #: Why not, in a sentence.
    reason: str

    def __str__(self) -> str:
        return (f"{self.name!r} of {self.package} ({self.value}) could not be "
                f"loaded, so it is left out: {self.reason}")


@dataclass(frozen=True)
class Unavailable:
    """Why something a study asked for is not here, and what would bring it."""

    #: ``environment.plugin.missing`` or ``environment.plugin.broken``.
    code: str
    #: The sentence a person reads.
    message: str
    #: What the refusal carries for a program: the package and the command.
    details: dict[str, Any]


_lock = threading.RLock()
_loaded: dict[str, dict[str, Any]] = {}
_owner: dict[str, dict[str, str]] = {}
_waiting: dict[str, list[Any]] = {}
_failures: list[Failure] = []
_moved_modules: dict[str, str] | None = None


def _entry_points(group: str) -> list[Any]:
    """The entry points installed packages declare in ``group``, by name;
    with those of this checkout's own packages that are not installed
    (:func:`_from_the_checkout`)."""
    from importlib.metadata import entry_points

    return sorted([*entry_points(group=group), *_from_the_checkout(group)],
                  key=lambda point: point.name)


# ---------------------------------------------------------------------------
# A source checkout whose own plug-ins were never installed
# ---------------------------------------------------------------------------
class _CheckoutPoint:
    """An entry point read from a ``pyproject.toml`` of this checkout."""

    def __init__(self, name: str, value: str, group: str, package: str) -> None:
        self.name, self.value, self.group = name, value, group
        self.dist = type("Dist", (), {"name": package})()

    def load(self) -> Any:
        module, _, attribute = self.value.partition(":")
        loaded: Any = importlib.import_module(module.strip())
        for part in filter(None, attribute.strip().split(".")):
            loaded = getattr(loaded, part)
        return loaded


def _checkout_packages() -> Path | None:
    """``packages/`` of the source checkout this module is running from,
    or ``None`` where it is an installed copy."""
    root = Path(__file__).resolve().parents[2]
    packages = root / "packages"
    if (root / "pyproject.toml").is_file() and packages.is_dir():
        return packages
    return None


#: The packages read from this checkout because they are not installed,
#: each with its folder.
_read_from_the_checkout: dict[str, str] = {}

_NAME = re.compile(r'^name\s*=\s*"([^"]+)"', re.M)
_ENTRY = re.compile(r'^\s*"?([^"=\s]+)"?\s*=\s*"([^"]+)"\s*$')


def _declared(pyproject: str, group: str) -> tuple[str, dict[str, str]]:
    """The project's name and what its ``pyproject.toml`` declares in
    ``group``. Reads the two things it needs from files this repository
    writes, so Python 3.10, which has no TOML reader, needs none."""
    name = _NAME.search(pyproject)
    declared: dict[str, str] = {}
    inside = False
    for line in pyproject.splitlines():
        if line.lstrip().startswith("["):
            inside = line.strip() == f'[project.entry-points."{group}"]'
        elif inside and (found := _ENTRY.match(line)):
            declared[found.group(1)] = found.group(2)
    return (name.group(1) if name else ""), declared


def _from_the_checkout(group: str) -> list[Any]:
    """What this checkout's own packages offer in ``group``, for each one
    that is not installed, its sources put on the path.

    A clone installed before a module moved to ``packages/`` (an editable
    install, or one run through the ``fastmdx`` launcher at its root) has
    the core and not the package the module moved to. It runs as it did:
    the package is read from where it sits in the same checkout. Installed
    (``pip install -e packages/<name>``), the installed one is used and
    this reads nothing.
    """
    from importlib.metadata import PackageNotFoundError, distribution

    packages = _checkout_packages()
    if packages is None:
        return []
    found: list[Any] = []
    for pyproject in sorted(packages.glob("*/pyproject.toml")):
        try:
            package, declared = _declared(pyproject.read_text(encoding="utf-8"), group)
        except OSError:
            continue
        if not package or not declared:
            continue
        try:
            distribution(package)
            continue
        except PackageNotFoundError:
            pass
        source = str(pyproject.parent / "src")
        if source not in sys.path:
            sys.path.append(source)
        _read_from_the_checkout[package] = str(pyproject.parent)
        found += [_CheckoutPoint(name, value, group, package)
                  for name, value in declared.items()]
    return found


def _package_of(point: Any) -> str:
    dist = getattr(point, "dist", None)
    name = getattr(dist, "name", None) if dist is not None else None
    return str(name) if name else "an installed package"


def offered(group: str) -> dict[str, str]:
    """What installed packages say they offer in ``group``, each name with
    the package that offers it. Read from metadata: nothing is imported."""
    try:
        return {point.name: _package_of(point) for point in _entry_points(group)}
    except Exception as exc:  # noqa: BLE001 - a broken install is not the core's
        logger.warning("The plug-ins of %s could not be listed: %s", group, exc)
        return {}


def _is_being_imported(value: str) -> bool:
    """Whether the module an entry point loads from is part-way through its
    own import: the plug-in was imported before the core, and importing the
    core is what brought the reading here."""
    module = sys.modules.get(str(value).partition(":")[0].strip())
    return bool(getattr(getattr(module, "__spec__", None), "_initializing", False))


def load(group: str) -> dict[str, Any]:
    """What installed packages offer in ``group``, loaded, by name; the
    installed packages are read once per process (:func:`forget` reads
    them again).

    One that fails to load is named in a warning, kept in :func:`failures`
    and left out: a plug-in that breaks does not take the core with it.
    Where two packages offer one name the first by package name is kept
    and the other is a failure.

    One whose module is being imported at this moment is neither: it waits
    (:func:`waiting`) and is loaded by a later call, once its import has
    finished. That is a script importing a plug-in's module before anything
    of the core's, which is no fault of the plug-in.
    """
    with _lock:
        if group not in _loaded:
            _loaded[group] = {}
            _owner[group] = {}
            try:
                points = _entry_points(group)
            except Exception as exc:  # noqa: BLE001 - a broken install is not the core's
                logger.warning("The plug-ins of %s could not be listed: %s", group, exc)
                points = []
            _waiting[group] = sorted(points, key=lambda p: (p.name, _package_of(p)))
        found, owner = _loaded[group], _owner[group]
        still_waiting = []
        for point in _waiting[group]:
            package = _package_of(point)
            if point.name in found:
                refuse(group, point.name, package, str(point.value),
                       f"{owner[point.name]} already offers {point.name!r}",
                       keep=True)
                continue
            if _is_being_imported(str(point.value)):
                still_waiting.append(point)
                continue
            try:
                found[point.name] = point.load()
                owner[point.name] = package
            except BaseException as exc:  # noqa: BLE001 - said, and left out
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                refuse(group, point.name, package, str(point.value),
                       f"{type(exc).__name__}: {exc}")
        _waiting[group] = still_waiting
        return dict(found)


def waiting(group: str) -> bool:
    """Whether something offered in ``group`` has yet to be loaded because
    its module was being imported when the group was read."""
    with _lock:
        return bool(_waiting.get(group))


def refuse(group: str, name: str, package: str, value: str, reason: str, *,
           keep: bool = False) -> None:
    """Record that ``name`` of ``group`` cannot be used, and say so once.
    Called here for what does not load, and by the core where what loaded
    is not what the group takes. ``keep`` leaves what another package
    loaded under that name."""
    failure = Failure(group, name, package, value, reason)
    with _lock:
        if failure in _failures:
            return
        _failures.append(failure)
        if not keep:
            _loaded.get(group, {}).pop(name, None)
    logger.warning("Plug-in %s: %s", group, failure)


def failures() -> tuple[Failure, ...]:
    """Everything a plug-in offered that could not be used, so far."""
    with _lock:
        return tuple(_failures)


def forget() -> None:
    """Read the installed packages again at the next :func:`load`. What the
    core registered from an earlier read stays registered."""
    with _lock:
        global _moved_modules
        _moved_modules = None
        _loaded.clear()
        _owner.clear()
        _waiting.clear()
        _failures.clear()


def installed() -> dict[str, dict[str, list[str]]]:
    """Each installed plug-in package, with what it offers by group. Read
    from metadata: nothing is imported."""
    packages: dict[str, dict[str, list[str]]] = {}
    for group in GROUPS:
        for name, package in offered(group).items():
            packages.setdefault(package, {}).setdefault(group, []).append(name)
    return dict(sorted(packages.items()))


def report() -> dict[str, dict[str, Any]]:
    """Each plug-in package and how it stands, for ``fastmdx info``:
    ``installed`` with its version and what it offers, ``checkout`` where
    it is not installed and is read from this source checkout, ``broken``
    where any of that would not load (with why), or ``missing`` for one of
    this project's own that is not installed (with the command that
    installs it). Says what has been read so far: a group the core has not yet
    loaded has no failures to report.
    """
    from importlib.metadata import PackageNotFoundError, version

    said: dict[str, dict[str, Any]] = {}
    for package, offers in installed().items():
        folder = _read_from_the_checkout.get(package)
        try:
            its_version = "" if folder else version(package)
        except PackageNotFoundError:
            its_version = ""
        broke = [str(failure) for failure in failures() if failure.package == package]
        said[package] = {
            "state": "broken" if broke else "checkout" if folder else "installed",
            "version": its_version,
            "offers": offers,
            "detail": (" ".join(broke) if broke
                       else f"not installed; read from {folder}" if folder else ""),
            "install": (f"pip install -e {folder}" if folder
                        else f"pip install --force-reinstall {package}" if broke else ""),
        }
    for package in sorted(set(FIRST_PARTY.values()) - set(said)):
        said[package] = {
            "state": "missing", "version": "",
            "offers": {}, "detail": "",
            "install": install_command(package),
        }
    return dict(sorted(said.items()))


def install_command(package: str) -> str:
    """The command that installs ``package``. From PyPI: the conda package
    of FastMDXplora carries its own plug-ins."""
    return f"pip install {package}"


def unavailable(group: str, name: str, *, what: str = "analysis") -> Unavailable | None:
    """Why ``name`` of ``group`` is not here, where a plug-in is the reason:
    its package is installed and would not load, or is one of this
    project's own and is not installed. ``None`` where no known package
    offers that name, and the caller's own refusal stands.
    """
    for failure in failures():
        if failure.group == group and failure.name == name:
            command = f"pip install --force-reinstall {failure.package}"
            return Unavailable(
                "environment.plugin.broken",
                f"The {what} {name!r} comes with {failure.package}, which is "
                f"installed and could not be loaded ({failure.reason}). "
                f"Reinstall it in the Python environment running FastMDXplora "
                f"with:\n\n    {command}",
                {"feature": name, "package": failure.package,
                 "install_command": command, "reason": failure.reason})
    if name in offered(group):
        return None
    package = FIRST_PARTY.get((group, name))
    if package is None:
        return None
    command = install_command(package)
    return Unavailable(
        "environment.plugin.missing",
        f"The {what} {name!r} comes with {package}, which is not installed "
        f"in the Python environment running FastMDXplora. Install it in that "
        f"same environment with:\n\n    {command}",
        {"feature": name, "package": package, "install_command": command})


# ---------------------------------------------------------------------------
# Modules the core used to hold
# ---------------------------------------------------------------------------
class _MovedModule(importlib.abc.Loader):
    """Gives the module a plug-in holds as the module of the old path."""

    def __init__(self, target: str) -> None:
        self.target = target
        self._its_own_spec: Any = None

    def create_module(self, spec: Any) -> Any:
        module = importlib.import_module(self.target)
        self._its_own_spec = getattr(module, "__spec__", None)
        return module

    def exec_module(self, module: Any) -> None:
        # The import system has by now written the old path's spec onto the
        # module, which is one module under two names: it keeps its own, so
        # it reloads and is named as what it is.
        if self._its_own_spec is not None:
            module.__spec__ = self._its_own_spec


class _MovedModules(importlib.abc.MetaPathFinder):
    """Finds ``fastmdxplora.*`` paths that are now a plug-in's modules.

    Last on ``sys.meta_path``, so it is asked only for a name nothing else
    found, and it reads the entry points then, not before.
    """

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        if not fullname.startswith("fastmdxplora."):
            return None
        moved = _moved()
        if fullname in moved:
            return importlib.machinery.ModuleSpec(fullname, _MovedModule(moved[fullname]))
        package = FIRST_PARTY.get((MOVED_MODULES, fullname))
        if package is not None:
            raise ModuleNotFoundError(
                f"{fullname} is now part of {package}, which is not installed. "
                f"Install it with: {install_command(package)}", name=fullname)
        return None


def _moved() -> dict[str, str]:
    """Each old path with the module that is now it; the installed packages
    are read for them once per process (:func:`forget` reads them again)."""
    global _moved_modules
    with _lock:
        if _moved_modules is None:
            try:
                _moved_modules = {point.name: str(point.value)
                                  for point in _entry_points(MOVED_MODULES)}
            except Exception:  # noqa: BLE001 - a broken install is not the core's
                return {}
        return _moved_modules


def find_moved_modules() -> None:
    """Have ``import`` find the modules that moved to plug-ins at the paths
    they had. Called once, as :mod:`fastmdxplora` is first imported."""
    if not any(isinstance(finder, _MovedModules) for finder in sys.meta_path):
        sys.meta_path.append(_MovedModules())
