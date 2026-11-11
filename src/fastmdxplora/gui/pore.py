"""The pore the `pore_profile` analysis found, for the Viewer.

The analysis (analysis/pore_profile.py) leaves the pore's radius at each
height along its axis, frame by frame, and the centre of the widest sphere
at each. Here that is read for the page: the mean radius against height with
the range over the frames and each frame played's own profile (that of the
analysed frame nearest it), and the mean pore as a surface, the union of the
mean spheres, placed on the first frame played by fitting the atoms that
line it in the first frame analysed onto the same atoms there.

Lengths are sent in angstroms, as the Viewer measures.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from fastmdxplora.gui.occupancy import (GRID_ANGSTROM, _dx_text, _load_json,
                                        onto_the_first_frame_played)

#: The records the pore is read from, beside the frames played.
RECORDS = ("analysis/pore_profile/options.json", "analysis/pore_profile/pore_radius.dat",
           "analysis/pore_profile/pore_path.npz")

NOT_RUN = ("The study has no pore profile: the `pore_profile` analysis was not run. It "
           "runs where a study names it in `analysis.include`.")


def _read(out: Path) -> tuple[dict[str, Any] | None, Any, str]:
    """The analysis's findings and its path (heights, radii and centres),
    with the planes its profile was followed over; or None and why not."""
    folder = out / "analysis" / "pore_profile"
    record = _load_json(folder / "options.json")
    path = folder / "pore_path.npz"
    if not record or not path.is_file():
        return None, None, NOT_RUN
    findings = record.get("findings") or {}
    pore = findings.get("pore")
    if not isinstance(pore, dict):
        return None, None, "The pore profile's record does not say where the pore is."
    try:
        with np.load(path, allow_pickle=False) as held:
            found = {name: np.asarray(held[name], dtype=float)
                     for name in ("height_nm", "radius_nm", "centre_nm")}
    except (OSError, ValueError, KeyError):
        return None, None, "The pore's path could not be read."
    heights = found["height_nm"]
    if (found["radius_nm"].ndim != 2 or found["radius_nm"].shape[1] != len(heights)
            or found["centre_nm"].shape != found["radius_nm"].shape + (3,)):
        return None, None, "The pore's path is not of the shape the analysis writes."
    found["kept"] = ((heights >= float(pore["from_nm"]) - 1e-6)
                     & (heights <= float(pore["to_nm"]) + 1e-6))
    return {**record, "findings": findings}, found, ""


def _rounded(values: Any, places: int = 2) -> list[float | None]:
    return [None if not np.isfinite(v) else round(float(v), places) for v in values]


def pore_profile(root: str | Path) -> dict[str, Any]:
    """The pore's radius against height: its mean and range over the frames
    analysed, and each frame played's own; or why there is none."""
    import warnings

    from fastmdxplora.analysis.pore_profile import (ONE_WATER_WIDE_NM,
                                                    TOO_NARROW_FOR_WATER_NM)
    from fastmdxplora.gui.series import analysed_axis, of_the_played_trajectory

    out = Path(root)
    record, found, reason = _read(out)
    if record is None:
        return {"ok": False, "reason": reason}
    findings = record["findings"]
    kept = found["kept"]
    radius = found["radius_nm"][:, kept] * 10.0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        mean = np.nanmean(radius, axis=0)
        low, high = np.nanmin(radius, axis=0), np.nanmax(radius, axis=0)
    narrowest = findings.get("narrowest") or {}
    lined = [f"{one['residue']} (chain {int(one['chain']) + 1})"
             for one in (narrowest.get("lined_by") or [])[:6]
             if isinstance(one, dict) and one.get("residue")]
    at = float(narrowest.get("mean_radius_nm") or 0.0) * 10.0
    open_share = float((findings.get("open_to_water") or {}).get("share_of_frames") or 0.0)
    said = (f"The widest sphere among the atoms of `{record.get('selection') or 'protein'}` "
            f"at each height along {(findings.get('axis') or {}).get('along', 'the axis')}, "
            f"over {int(findings['pore'].get('frames_with_a_pore', len(radius))):,} frames. "
            f"Narrowest on average {at:.2f} Å, at {float(narrowest.get('height_nm') or 0) * 10:.1f} Å"
            + (f", lined by {', '.join(lined)}" if lined else "")
            + f". Wide enough for a water ({TOO_NARROW_FOR_WATER_NM * 10:.2f} Å) in "
            f"{open_share:.0%} of frames, which is room for one and not conduction.")
    payload: dict[str, Any] = {
        "ok": True, "said": said,
        "heights": _rounded(found["height_nm"][kept] * 10.0),
        "mean": _rounded(mean), "low": _rounded(low), "high": _rounded(high),
        "narrow": round(TOO_NARROW_FOR_WATER_NM * 10.0, 2),
        "one_water": round(ONE_WATER_WIDE_NM * 10.0, 2),
        "narrowest": {"height": round(float(narrowest.get("height_nm") or 0) * 10.0, 2),
                      "radius": round(at, 2), "lined_by": lined},
        "profiles": None,
    }
    # Each frame played takes the profile of the analysed frame nearest it,
    # where the frames analysed are the trajectory played.
    index = _load_json(out / "simulation" / "frames_index.json")
    played = [int(i) for i in index.get("frame_indices") or []]
    if played and index.get("available") and of_the_played_trajectory(out):
        analysed = np.asarray(analysed_axis(out, len(radius))[0])
        nearest = np.abs(np.asarray(played)[:, None] - analysed[None, :]).argmin(axis=1)
        payload["profiles"] = [_rounded(radius[k]) for k in nearest]
    return payload


def pore_surface(root: str | Path) -> tuple[str | None, str]:
    """The mean pore as an OpenDX map on the first frame played: at each
    point, how far inside the nearest mean sphere it is, in angstroms (the
    pore's surface is where that is zero); or None and why not."""
    import warnings

    from fastmdxplora.gui.series import of_the_played_trajectory

    out = Path(root)
    record, found, reason = _read(out)
    if record is None:
        return None, reason
    if not of_the_played_trajectory(out):
        return None, "The pore was found in a trajectory other than the one played."
    into, fitted = onto_the_first_frame_played(
        out, str(record.get("selection") or "protein"), "pore")
    if into is None:
        return None, str(fitted)
    kept = found["kept"]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        radii = np.nanmean(found["radius_nm"][:, kept], axis=0) * 10.0
        centres = np.nanmean(found["centre_nm"][:, kept], axis=0)
    there = np.isfinite(radii) & np.isfinite(centres).all(axis=1) & (radii > 0)
    if not there.any():
        return None, "The pore has no radius to show."
    radii, centres = radii[there], into(centres[there]) * 10.0
    margin = 2 * GRID_ANGSTROM
    low = (centres - radii[:, None]).min(axis=0) - margin
    high = (centres + radii[:, None]).max(axis=0) + margin
    counts = np.ceil((high - low) / GRID_ANGSTROM).astype(int) + 1
    axes = [low[k] + GRID_ANGSTROM * np.arange(counts[k]) for k in range(3)]
    grid = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 3)
    inside = np.full(len(grid), -np.inf)
    for centre, wide in zip(centres, radii):
        inside = np.maximum(inside, wide - np.linalg.norm(grid - centre, axis=1))
    # Far from the pore the depth says nothing the surface needs.
    values = np.clip(inside, -2.0, None).reshape(counts)
    return _dx_text(values, low, GRID_ANGSTROM), ""
