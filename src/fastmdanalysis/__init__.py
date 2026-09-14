# FastMDAnalysis/src/fastmdanalysis/__init__.py
"""
FastMDAnalysis – automated MD trajectory analysis.

Documentation: https://fastmdanalysis.readthedocs.io/en/latest/

FastMDAnalysis Package Initialization

Instantiate with trajectory/topology and optional frame/atom selection,
or pass a system configuration (YAML/JSON path or dict) with the same keys
you'd use on the CLI (trajectory/topology or traj/top, frames, atoms,
include/exclude, options, output, slides, strict, stop_on_error).

All subsequent analyses (rmsd, rmsf, rg, hbonds, cluster, ss, sasa, dimred)
use the pre-loaded trajectory and default atom selection unless overridden.
"""

from __future__ import annotations

from typing import Optional, Tuple, Union, Sequence, Mapping, Any, Dict
from pathlib import Path
import logging
import warnings

# Optional dependency import to ensure availability at import time (not used directly here).
import mdtraj as md  # noqa: F401

from .analysis import rmsd, rmsf, rg, hbonds, cluster, ss, dimred, sasa, qvalue, dihedrals
from .utils import load_trajectory  # Extended utility supporting multiple files.
from .utils.logging import setup_library_logging, log_run_header  # convenient re-exports

# -----------------------------------------------------------------------------
# Package version
# -----------------------------------------------------------------------------
try:
    from importlib.metadata import version as _pkg_version, PackageNotFoundError  # type: ignore
except Exception:  # pragma: no cover
    try:  # Python <3.8 backport
        from importlib_metadata import version as _pkg_version, PackageNotFoundError  # type: ignore
    except Exception:  # pragma: no cover
        _pkg_version = None  # type: ignore
        PackageNotFoundError = Exception  # type: ignore


def _resolve_version() -> str:
    for dist_name in ("fastmdanalysis", "FastMDAnalysis"):
        try:
            if _pkg_version:
                return _pkg_version(dist_name)
        except PackageNotFoundError:
            continue
        except Exception:
            continue
    return "0+unknown"


__version__ = _resolve_version()

# -----------------------------------------------------------------------------
# Succession notice.
#
# DeprecationWarning is silent unless the importer is `__main__` or the caller
# asked for warnings, so a library that depends on this package is unaffected
# while someone running a script or a notebook is told. Pin 1.1.0 to silence it
# permanently; nothing else differs between the two releases.
# -----------------------------------------------------------------------------
warnings.warn(
    "fastmdanalysis is superseded by fastmdxplora, which has every analysis "
    "here plus system setup, production MD, enhanced sampling and free "
    "energies: pip install fastmdxplora",
    DeprecationWarning,
    stacklevel=2,
)

# -----------------------------------------------------------------------------
# Package logging: install a NullHandler so library users don't get warnings.
# -----------------------------------------------------------------------------
_pkg_logger = logging.getLogger("fastmdanalysis")
if not _pkg_logger.handlers:
    _pkg_logger.addHandler(logging.NullHandler())

# -----------------------------------------------------------------------------
# Expose analysis classes.
# -----------------------------------------------------------------------------
RMSDAnalysis = rmsd.RMSDAnalysis
RMSFAnalysis = rmsf.RMSFAnalysis
RGAnalysis = rg.RGAnalysis
HBondsAnalysis = hbonds.HBondsAnalysis
ClusterAnalysis = cluster.ClusterAnalysis
SSAnalysis = ss.SSAnalysis
DimRedAnalysis = dimred.DimRedAnalysis
SASAAnalysis = sasa.SASAAnalysis
QAnalysis = qvalue.QAnalysis
PhiAnalysis = dihedrals.PhiAnalysis
PsiAnalysis = dihedrals.PsiAnalysis
OmegaAnalysis = dihedrals.OmegaAnalysis
DihedralsAnalysis = dihedrals.DihedralsAnalysis

__all__ = [
    "__version__",
    "FastMDAnalysis",
    "RMSDAnalysis",
    "RMSFAnalysis",
    "RGAnalysis",
    "HBondsAnalysis",
    "ClusterAnalysis",
    "SSAnalysis",
    "DimRedAnalysis",
    "SASAAnalysis",
    "QAnalysis",
    "PhiAnalysis",
    "PsiAnalysis",
    "OmegaAnalysis",
    "DihedralsAnalysis",
    "load_trajectory",
    "setup_library_logging",
    "log_run_header",
]


def _normalize_frames(
    frames: Optional[
        Union[
            str,
            Sequence[Union[int, None]],
            Tuple[Optional[int], Optional[int], Optional[int]],
        ]
    ]
) -> Optional[Tuple[Optional[int], Optional[int], int]]:
    """
    Normalize (start, stop, stride) for slicing.

    Accepts:
      - None
      - a 3-tuple/list of (start, stop, stride)
      - a string "start,stop,stride" OR "start:stop:stride"
    """
    if frames is None:
        return None

    if isinstance(frames, str):
        s = frames.strip()
        parts = s.split(",") if "," in s else s.split(":")
        if len(parts) != 3:
            raise TypeError(
                "frames must be a 3-tuple/list or 'start,stop,stride' (commas or colons)."
            )
        raw = []
        for i, tok in enumerate(parts):
            tok = tok.strip()
            if i < 2 and tok.lower() in {"", "none"}:
                raw.append(None)
            else:
                raw.append(tok)
        frames = raw  # fall-through

    if not isinstance(frames, (list, tuple)) or len(frames) != 3:
        raise TypeError("frames must be None or a 3-tuple/list: (start, stop, stride)")

    start, stop, stride = frames

    def _int_or_none(x):
        if x is None:
            return None
        try:
            return int(x)
        except Exception as e:
            raise TypeError("frames elements must be int or None") from e

    start_i = _int_or_none(start)
    stop_i = _int_or_none(stop)
    stride_i = _int_or_none(stride)

    if stride_i is None or stride_i == 0:
        stride_i = 1
    if stride_i < 0:
        stride_i = -stride_i

    return (start_i, stop_i, stride_i)


def _load_system_config(system: Union[str, Path, Mapping[str, Any]]) -> Dict[str, Any]:
    """Load a YAML/JSON system config or accept a pre-built mapping. Returns a dict."""
    if isinstance(system, Mapping):
        cfg = dict(system)
    else:
        p = Path(system).expanduser()
        text = p.read_text(encoding="utf-8")
        if p.suffix.lower() in {".yml", ".yaml"}:
            try:
                import yaml  # type: ignore
            except Exception as e:  # pragma: no cover
                raise RuntimeError(
                    "PyYAML is required to read YAML system files. Install with: pip install PyYAML"
                ) from e
            cfg = yaml.safe_load(text) or {}
        else:
            import json
            cfg = json.loads(text) or {}

    if not isinstance(cfg, dict):
        raise TypeError("system config must be a mapping/object")

    # Aliases for CLI/API parity
    aliases = {
        "traj": "trajectory",
        "top": "topology",
        "selection": "atoms",
        "outdir": "output",
    }
    for src, dst in aliases.items():
        if src in cfg and dst not in cfg:
            cfg[dst] = cfg[src]

    if "trajectory" not in cfg and "traj_file" in cfg:
        cfg["trajectory"] = cfg["traj_file"]
    if "topology" not in cfg and "top_file" in cfg:
        cfg["topology"] = cfg["top_file"]

    return cfg


class FastMDAnalysis:
    """
    Main API class for MD trajectory analysis.

    You may construct with explicit traj/top or a `system` config (YAML/JSON path or dict).
    """

    def __init__(
        self,
        traj_file: Optional[Union[str, Path, Sequence[Union[str, Path]]]] = None,  # Changed to accept sequences
        top_file: Optional[Union[str, Path]] = None,
        frames: Optional[
            Union[
                str,
                Sequence[Union[int, None]],
                Tuple[Optional[int], Optional[int], Optional[int]],
            ]
        ] = None,
        atoms: Optional[str] = None,
        *,
        system: Optional[Union[str, Path, Mapping[str, Any]]] = None,
        **kwargs: Any,
    ):
        # Accept keyword aliases (trajectory/topology or traj/top)
        if traj_file is None:
            if "trajectory" in kwargs:
                traj_file = kwargs.pop("trajectory")
            elif "traj" in kwargs:
                traj_file = kwargs.pop("traj")
        if top_file is None:
            if "topology" in kwargs:
                top_file = kwargs.pop("topology")
            elif "top" in kwargs:
                top_file = kwargs.pop("top")

        # If a system file/dict is provided, use it to fill missing inputs and stash analyze defaults
        if system is not None:
            sys_cfg = _load_system_config(system)

            if traj_file is None:
                t = sys_cfg.get("trajectory")
                if isinstance(t, (list, tuple)):
                    traj_file = t[0] if t else None
                else:
                    traj_file = t

            if top_file is None:
                top_file = sys_cfg.get("topology")

            if frames is None and "frames" in sys_cfg:
                frames = sys_cfg["frames"]

            if atoms is None and "atoms" in sys_cfg:
                atoms = sys_cfg["atoms"]

            # Store analyze defaults for later .analyze() calls
            self._system_include = sys_cfg.get("include")
            self._system_exclude = sys_cfg.get("exclude")
            self._system_options = sys_cfg.get("options") or {}
            self._system_output = sys_cfg.get("output")
            self._system_slides = sys_cfg.get("slides")
            self._system_strict = bool(sys_cfg.get("strict", False))
            self._system_stop_on_error = bool(sys_cfg.get("stop_on_error", False))
            self._system_compute_stat = bool(sys_cfg.get("compute_stat", False))
            self._system_file = system if not isinstance(system, Mapping) else None
        else:
            # Ensure attributes exist even without a system config
            self._system_include = None
            self._system_exclude = None
            self._system_options = {}
            self._system_output = None
            self._system_slides = None
            self._system_strict = False
            self._system_stop_on_error = False
            self._system_compute_stat = False
            self._system_file = None

        if traj_file is None or top_file is None:
            raise ValueError(
                "Both trajectory and topology are required. Provide (traj_file, top_file) "
                "or use system=<yaml/json> containing 'trajectory' and 'topology'."
            )

        # Handle multiple trajectory files - pass the list directly to load_trajectory
        if isinstance(traj_file, (list, tuple)):
            if len(traj_file) == 0:
                raise ValueError("Empty trajectory file list provided")
            # Pass the entire list to load_trajectory which will concatenate them
            actual_traj_files = traj_file
        else:
            actual_traj_files = [traj_file]

        # Load trajectory and apply frame selection
        self.full_traj = load_trajectory(actual_traj_files, str(top_file))
        
        norm_frames = _normalize_frames(frames)
        if norm_frames is not None:
            start, stop, stride = norm_frames
            self.traj = self.full_traj[start:stop:stride]
        else:
            self.traj = self.full_traj

        # Store defaults for later analyses
        self.default_atoms = atoms

        # Optional: common output/figure dirs probed by the slides utility
        self.figdir = getattr(self, "figdir", "figures")
        self.outdir = getattr(self, "outdir", "results")

    def _get_atoms(self, specific_atoms: Optional[str]) -> Optional[str]:
        return specific_atoms if specific_atoms is not None else self.default_atoms

    # ----------------------------- Analyses -----------------------------------

    def rmsd(
        self,
        reference_frame: Optional[int] = None,
        ref: Optional[int] = None,
        atoms: Optional[str] = None,
        **kwargs,
    ):
        a = self._get_atoms(atoms)
        rf = ref if ref is not None else (reference_frame if reference_frame is not None else 0)
        analysis = RMSDAnalysis(self.traj, reference_frame=rf, atoms=a, **kwargs)
        analysis.run()
        return analysis

    def rmsf(self, atoms: Optional[str] = None, **kwargs):
        a = self._get_atoms(atoms)
        analysis = RMSFAnalysis(self.traj, atoms=a, **kwargs)
        analysis.run()
        return analysis

    def rg(self, atoms: Optional[str] = None, **kwargs):
        a = self._get_atoms(atoms)
        analysis = RGAnalysis(self.traj, atoms=a, **kwargs)
        analysis.run()
        return analysis

    def hbonds(self, atoms: Optional[str] = None, **kwargs):
        a = self._get_atoms(atoms)
        analysis = HBondsAnalysis(self.traj, atoms=a, **kwargs)
        analysis.run()
        return analysis

    def cluster(
        self,
        methods="all",
        eps: float = 0.15,
        min_samples: int = 5,
        n_clusters: Optional[int] = None,
        atoms: Optional[str] = None,
        **kwargs,
    ):
        a = self._get_atoms(atoms)
        analysis = ClusterAnalysis(
            self.traj,
            methods=methods,
            eps=eps,
            min_samples=min_samples,
            n_clusters=n_clusters,
            atoms=a,
            **kwargs,
        )
        analysis.run()
        return analysis

    def ss(self, atoms: Optional[str] = None, **kwargs):
        a = self._get_atoms(atoms)
        analysis = SSAnalysis(self.traj, atoms=a, **kwargs)
        analysis.run()
        return analysis

    def sasa(self, probe_radius: float = 0.14, atoms: Optional[str] = None, **kwargs):
        a = self._get_atoms(atoms)
        analysis = SASAAnalysis(self.traj, probe_radius=probe_radius, atoms=a, **kwargs)
        analysis.run()
        return analysis

    def dimred(self, methods="all", atoms: Optional[str] = None, **kwargs):
        a = self._get_atoms(atoms)
        analysis = DimRedAnalysis(self.traj, methods=methods, atoms=a, **kwargs)
        analysis.run()
        return analysis

    def qvalue(
        self,
        reference_frame: int = 0,
        beta_const: float = 50.0,
        lambda_const: float = 1.8,
        native_cutoff: float = 0.45,
        atoms: Optional[str] = None,
        **kwargs,
    ):
        a = self._get_atoms(atoms)
        analysis = QAnalysis(
            self.traj,
            reference_frame=reference_frame,
            beta_const=beta_const,
            lambda_const=lambda_const,
            native_cutoff=native_cutoff,
            atom_selection=a if a else None,
            **kwargs,
        )
        analysis.run()
        return analysis

    def phi(self, residues: Optional[Union[int, Sequence[int]]] = None, units: str = "degrees", **kwargs):
        analysis = PhiAnalysis(self.traj, residues=residues, units=units, **kwargs)
        analysis.run()
        return analysis

    def psi(self, residues: Optional[Union[int, Sequence[int]]] = None, units: str = "degrees", **kwargs):
        analysis = PsiAnalysis(self.traj, residues=residues, units=units, **kwargs)
        analysis.run()
        return analysis

    def omega(self, residues: Optional[Union[int, Sequence[int]]] = None, units: str = "degrees", **kwargs):
        analysis = OmegaAnalysis(self.traj, residues=residues, units=units, **kwargs)
        analysis.run()
        return analysis

    def dihedrals(self, types: Sequence[str] = ["phi", "psi", "omega"], residues: Optional[Union[int, Sequence[int]]] = None, units: str = "degrees", **kwargs):
        analysis = DihedralsAnalysis(self.traj, types=types, residues=residues, units=units, **kwargs)
        analysis.run()
        return analysis

    # ----------------------------- Unified analyze façade ----------------------

    def analyze(
        self,
        include: Optional[Sequence[str]] = None,
        exclude: Optional[Sequence[str]] = None,
        options: Optional[Mapping[str, Mapping[str, Any]]] = None,
        stop_on_error: bool = False,
        verbose: bool = True,
        slides: Optional[Union[bool, str, Path]] = None,
        output: Optional[Union[str, Path]] = None,
        strict: bool = False,
        compute_stat: bool = False,
    ):
        """
        Analyze wrapper. If arguments are omitted here, fall back to defaults
        provided via the constructor `system=` config (include/exclude/options/
        output/slides/strict/stop_on_error).
        """
        # Lazy import to avoid circular/early import issues
        try:
            from .analysis.analyze import analyze as _run  # preferred name
        except Exception:
            from .analysis.analyze import run as _run  # fallback

        if include is None:
            include = getattr(self, "_system_include", None)
        if exclude is None:
            exclude = getattr(self, "_system_exclude", None)
        if options is None:
            options = getattr(self, "_system_options", None)
        if output is None:
            output = getattr(self, "_system_output", None)
        if slides is None:
            slides = getattr(self, "_system_slides", None)
        strict = bool(strict or getattr(self, "_system_strict", False))
        stop_on_error = bool(stop_on_error or getattr(self, "_system_stop_on_error", False))
        compute_stat = bool(compute_stat or getattr(self, "_system_compute_stat", False))

        return _run(
            self,
            include=include,
            exclude=exclude,
            options=options,
            stop_on_error=stop_on_error,
            verbose=verbose,
            slides=slides,
            output=output,
            strict=strict,
            compute_stat=compute_stat,
        )