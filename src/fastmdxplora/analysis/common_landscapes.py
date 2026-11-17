"""One reference-fitted Cartesian PCA and one occupancy grid for ensembles."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import mdtraj as md
import numpy as np
import sklearn
from sklearn.decomposition import PCA

from fastmdxplora.analysis.base import (
    IMAGING_FAILED,
    AnalysisResult,
    _whole_if_broken,
    superposed,
)
from fastmdxplora.analysis.loading import _frames_in, load_trajectory
from fastmdxplora.analysis.plotting import (
    close_figures_opened_since,
    figures_open,
    new_figure,
    save_figure,
)
from fastmdxplora.refusals import StudyError


def _refuse(message: str, code: str = "analysis.option.inapplicable") -> None:
    raise StudyError(message, code=code)


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _label(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 80 or any(
        not c.isprintable() for c in value
    ):
        _refuse(f"{where} needs a unique nonempty printable label of at most 80 characters.")
    return value


def _validated(raw: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(raw, dict) or set(raw) != {"reference", "targets"}:
        _refuse("`comparison` must contain exactly `reference` and `targets`.")
    reference = raw["reference"]
    if not isinstance(reference, dict) or set(reference) - {"label", "temperature_K", "sampling"}:
        _refuse("Comparison reference accepts label, temperature_K and sampling only.")
    reference = dict(reference)
    reference["label"] = _label(reference.get("label"), "Comparison reference")
    targets = raw["targets"]
    if not isinstance(targets, list) or not 1 <= len(targets) <= 3:
        _refuse("Comparison needs one to three target trajectory mappings.")
    ensembles = [reference]
    labels = {reference["label"]}
    normalized = []
    for i, target in enumerate(targets, 1):
        if not isinstance(target, dict) or set(target) - {
            "label", "trajectory", "topology", "first", "last", "stride",
            "temperature_K", "sampling",
        }:
            _refuse(f"Comparison target {i} has unknown keys or is not a mapping.")
        target = dict(target)
        target["label"] = _label(target.get("label"), f"Comparison target {i}")
        if target["label"] in labels:
            _refuse(f"Comparison label {target['label']!r} is duplicated.")
        labels.add(target["label"])
        for key in ("trajectory", "topology"):
            value = target.get(key)
            if not isinstance(value, str) or not value.strip() or any(c in value for c in "*?[]"):
                _refuse(f"Comparison target {target['label']!r} needs one {key} filename.")
        for key, default, minimum in (("first", 0, 0), ("last", None, 0), ("stride", 1, 1)):
            value = target.get(key, default)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < minimum
            ):
                _refuse(f"Comparison target {target['label']!r} has invalid {key}={value!r}.")
            target[key] = value
        normalized.append(target)
        ensembles.append(target)
    for item in ensembles:
        temperature = item.get("temperature_K")
        if temperature is not None and (not _finite_number(temperature) or temperature <= 0):
            _refuse(f"Comparison ensemble {item['label']!r} needs temperature_K > 0.")
        item["sampling"] = item.get("sampling", "unknown")
        if not isinstance(item["sampling"], str) or item["sampling"] not in {
            "unknown", "unbiased", "biased"
        }:
            _refuse(
                f"Comparison ensemble {item['label']!r} sampling must be "
                "unknown, unbiased or biased."
            )
    return reference, normalized


def normalize_comparison(raw: Any) -> dict[str, Any]:
    """Validate options once and fill the documented defaults for the manifest."""
    reference, targets = _validated(raw)
    reference.setdefault("sampling", "unknown")
    return {"reference": reference, "targets": targets}


def _atom_keys(topology: md.Topology, indices: np.ndarray) -> list[tuple[Any, ...]]:
    keys = []
    for index in indices:
        atom = topology.atom(int(index))
        residue = atom.residue
        chain = residue.chain
        keys.append((chain.index, str(getattr(chain, "chain_id", "") or ""),
                     residue.index, residue.resSeq, residue.name, atom.name,
                     atom.element.symbol if atom.element else ""))
    if len(set(keys)) != len(keys):
        _refuse("Selected atoms have duplicate identity keys; use an unambiguous selection.")
    return keys


def _check_geometry(xyz: np.ndarray, label: str) -> None:
    eps = np.finfo(np.float32).eps * 10
    for frame, points in enumerate(xyz):
        centered = np.asarray(points, dtype=np.float64) - np.mean(points, axis=0)
        singular = np.linalg.svd(centered, compute_uv=False)
        if len(singular) < 2 or singular[0] == 0 or singular[1] <= eps * singular[0]:
            _refuse(f"Ensemble {label!r} frame {frame} has collinear alignment atoms; "
                    "select at least three noncollinear atoms.")


def _file_facts(path: Path) -> tuple[dict[str, Any], tuple[int, int, int]]:
    info = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return ({"path": str(path), "size_bytes": info.st_size, "sha256": digest.hexdigest()},
            (info.st_size, info.st_mtime_ns, info.st_ino))


def _same_file(path: Path, before: tuple[int, int, int], digest: str) -> None:
    current, signature = _file_facts(path)
    if signature != before or current["sha256"] != digest:
        _refuse(f"Input {path} changed while the comparison was running.")


def _memory_preflight(reference: md.Trajectory, n_frames: int, n_atoms: int,
                      selected_atoms: int, bins: int, target: str,
                      analysis: Any, retained_frames: int | None = None) -> dict[str, Any]:
    from fastmdxplora.analysis.cluster import address_room, memory_available, resident_room

    available, read_from = memory_available()
    resident, resident_from = resident_room(available, read_from)
    address, address_from = address_room(available, read_from)
    n_ref = reference.n_frames
    n_ens = len(analysis.comparison["targets"]) + 1
    largest_xyz = max(n_frames * n_atoms, reference.n_frames * reference.n_atoms)
    full_target = largest_xyz * 3 * 8 * 6
    ref_flat = n_ref * selected_atoms * 3 * 8 * 4
    svd = min(n_ref, selected_atoms * 3) ** 2 * 8
    retained = (n_ref if retained_frames is None else retained_frames) * 2 * 8
    grids = n_ens * bins * bins * (8 * 3 + 8)
    canvas_height = 6.0 if n_ens <= 2 else 9.0
    canvas = 10 * canvas_height * 300 * 300 * 4
    reference_storage = reference.n_frames * reference.n_atoms * 3 * 4 * 2
    need = (full_target + reference_storage
            + ref_flat + svd + retained + grids + canvas)
    findings = {"estimate_bytes": int(need), "available_bytes": available,
                "available_read_from": read_from, "resident_room_bytes": resident,
                "resident_read_from": resident_from, "address_room_bytes": address,
                "address_read_from": address_from, "estimate_known": available is not None}
    analysis.findings["memory"] = findings
    for room, label in ((resident, "resident"), (address, "address-space")):
        if room is not None and need > room:
            _refuse(f"Comparison target {target!r} needs an estimated {need:,} bytes, "
                    f"above the {room:,}-byte {label} room. Reduce the input or stride.",
                    "analysis.memory.matrix_too_large")
    return findings


def _hash_arrays(*arrays: np.ndarray) -> str:
    digest = hashlib.sha256()
    for array in arrays:
        digest.update(np.ascontiguousarray(array).tobytes())
    return digest.hexdigest()


def compute_comparison(analysis: Any, reference: md.Trajectory) -> dict[str, Any]:
    """Compute a common reference-trained PCA and joint occupancy grid."""
    from fastmdxplora.analysis.dimred import free_energy_landscape

    ref_meta, targets = _validated(analysis.comparison)
    ref_idx = analysis.select_atoms(reference)
    if len(ref_idx) < 3 or reference.n_frames < 3:
        _refuse("Reference needs at least three selected atoms and three retained frames.")
    memory_checks = [_memory_preflight(reference, reference.n_frames, reference.n_atoms,
        len(ref_idx), analysis.landscape_bins, "reference", analysis)]
    reference, whole_note = _whole_if_broken(reference)
    if getattr(reference, IMAGING_FAILED, False):
        _refuse("Reference imaging failed for a periodic input; reload it with load_trajectory().")
    ref_idx = analysis.select_atoms(reference)
    ref_keys = _atom_keys(reference.topology, ref_idx)
    _check_geometry(reference.xyz[:, ref_idx], ref_meta["label"])
    ref_aligned = superposed(reference, frame=0, atom_indices=ref_idx)
    x_ref = np.asarray(ref_aligned.xyz[:, ref_idx, :], dtype=np.float64).reshape(
        reference.n_frames, -1)
    if not np.isfinite(x_ref).all():
        _refuse("Reference coordinates contain nonfinite values.")
    model = PCA(n_components=2, svd_solver="full").fit(x_ref)
    tolerance = (np.finfo(np.float64).eps
                 * max(float(model.explained_variance_[0]), np.finfo(float).tiny) * 100)
    if model.explained_variance_[1] <= tolerance:
        _refuse("Reference PCA has zero variance on PC2; provide a nondegenerate reference "
                "ensemble.")
    mean = np.asarray(model.mean_, dtype=np.float64)
    components = np.asarray(model.components_, dtype=np.float64)
    projections = [(x_ref - mean) @ components.T]
    labels = [ref_meta["label"]]
    selections: list[np.ndarray | None] = [None]
    files: list[dict[str, Any]] = []
    residuals = [0.0]
    source_frames: list[np.ndarray | None] = [None]
    original_ref = np.asarray(reference.xyz[:, ref_idx, :]).copy()
    if whole_note:
        analysis.findings["made_whole"] = whole_note
    for target in targets:
        label = target["label"]
        traj_path = Path(target["trajectory"]).expanduser().resolve()
        top_path = Path(target["topology"]).expanduser().resolve()
        if not traj_path.is_file() or not top_path.is_file():
            _refuse(f"Comparison target {label!r} trajectory/topology file is missing.")
        trajectory_facts, traj_sig = _file_facts(traj_path)
        topology_facts, top_sig = _file_facts(top_path)
        topology = md.load_topology(str(top_path))
        n_atoms = topology.n_atoms
        n_total = _frames_in(traj_path, top_path, load=False)
        if n_total is None:
            _refuse(f"Cannot safely count frames in target {label!r} without loading it whole; "
                    "convert it to an indexed format such as XTC, TRR, DCD or NetCDF.")
        stride = target["stride"]
        first = target["first"]
        loaded_frames = len(range(0, n_total, stride))
        stop = target["last"] if target["last"] is not None else loaded_frames
        selected_source = np.arange(0, n_total, stride, dtype=np.int64)[first:stop]
        if not len(selected_source):
            _refuse(f"Target {label!r} selection retains no frames.")
        memory_checks.append(_memory_preflight(
            reference, loaded_frames, n_atoms, len(ref_idx),
            analysis.landscape_bins, label, analysis,
            retained_frames=sum(len(points) for points in projections) + len(selected_source)))
        loaded = load_trajectory(traj_path, top_path, first=first,
                                  last=target["last"], stride=stride)
        if getattr(loaded, IMAGING_FAILED, False):
            _refuse(f"Target {label!r} imaging failed for a periodic input.")
        if loaded.n_frames != len(selected_source):
            _refuse(f"Target {label!r} loader returned an unexpected retained frame count.")
        if loaded.topology.n_atoms != n_atoms:
            _refuse(f"Target {label!r} topology changed during loading.")
        idx = analysis.select_atoms(loaded)
        keys = _atom_keys(loaded.topology, idx)
        if keys != ref_keys:
            _refuse(f"Target {label!r} ordered selected atom identities differ from the reference; "
                    "use the same selection and topology numbering.")
        _check_geometry(loaded.xyz[:, idx], label)
        aligned = superposed(loaded, frame=0, atom_indices=idx,
                             reference=reference[0], ref_atom_indices=ref_idx)
        x_target = np.asarray(aligned.xyz[:, idx, :], dtype=np.float64).reshape(loaded.n_frames, -1)
        if not np.isfinite(x_target).all():
            _refuse(f"Target {label!r} coordinates contain nonfinite values.")
        projected = (x_target - mean) @ components.T
        reconstructed = mean + projected @ components
        residuals.append(float(np.mean(np.square(x_target - reconstructed))))
        projections.append(projected)
        labels.append(label)
        selections.append({"first": first, "last": target["last"], "stride": stride})
        source_frames.append(selected_source)
        files.append({"label": label, "trajectory": trajectory_facts,
                      "topology": topology_facts, "selection": selections[-1],
                      "selected_indices": [int(value) for value in idx],
                      "projected_frames": int(loaded.n_frames)})
        _same_file(traj_path, traj_sig, trajectory_facts["sha256"])
        _same_file(top_path, top_sig, topology_facts["sha256"])
        del x_target, projected, loaded, aligned
    if not np.array_equal(original_ref, reference.xyz[:, ref_idx, :]):
        _refuse("Reference trajectory changed during alignment.")
    union = np.concatenate(projections, axis=0)
    _, edges_x, edges_y = np.histogram2d(union[:, 0], union[:, 1], bins=analysis.landscape_bins)
    landscapes = [free_energy_landscape(points[:, 0], points[:, 1], edges=(edges_x, edges_y))
                  for points in projections]
    metadata = json.dumps({"schema_version": 1, "reference_frame": 0, "units": "nm",
                           "fit_solver": "full", "atom_keys": ref_keys}, sort_keys=True)
    model_id = hashlib.sha256(metadata.encode() + mean.tobytes() + components.tobytes()).hexdigest()
    reference_hash = hashlib.sha256()
    reference_hash.update(np.ascontiguousarray(reference.xyz[:, ref_idx]).tobytes())
    reference_hash.update(np.ascontiguousarray(ref_idx).tobytes())
    reference_hash.update(json.dumps(ref_keys, separators=(",", ":")).encode())
    reference_digest = reference_hash.hexdigest()
    temps = [item.get("temperature_K") for item in [ref_meta, *targets]]
    known_temps = {float(value) for value in temps if value is not None}
    qualifications = ["Empirical sampling; equilibrium and convergence are not established."]
    if len(known_temps) > 1:
        qualifications.append("Ensembles have different user-declared temperatures.")
    if any(item["sampling"] == "biased" for item in [ref_meta, *targets]):
        qualifications.append("Biased occupancy; no reweighting was applied.")
    if any(item["sampling"] == "unknown" for item in [ref_meta, *targets]):
        qualifications.append("Sampling state is unknown for one or more ensembles.")
    qualifications.append("Target motion outside reference PC1 and PC2 is not displayed.")
    qualifications.append("Each panel's zero is its own minimum, not a common absolute zero.")
    analysis.findings["comparison"] = {
        "schema_version": 1, "labels": labels, "model_id": model_id,
        "reference_selected_coordinates_sha256": reference_digest,
        "reference_raw_trajectory": "unknown for in-memory input",
        "selected_indices": [int(value) for value in ref_idx],
        "atom_keys": [list(key) for key in ref_keys], "reference_frames": reference.n_frames,
        "projected_frames": [len(points) for points in projections],
        "histogrammed_frames": [int(grid["counts"].sum()) for grid in landscapes],
        "bins": analysis.landscape_bins,
        "union_ranges_nm": [[float(union[:, i].min()), float(union[:, i].max())] for i in range(2)],
        "edges_sha256": _hash_arrays(edges_x, edges_y), "target_sources": files,
        "target_residual_mean_squared_nm2": residuals[1:],
        "temperature_K": temps,
        "temperature_source": ["user" if t is not None else "unknown" for t in temps],
        "sampling": [item["sampling"] for item in [ref_meta, *targets]],
        "sampling_source": analysis._comparison_sampling_sources,
        "qualifications": qualifications,
        "landscape_convention": (
            "Per ensemble: density = counts / (that ensemble's frames x bin area); value = "
            "-ln(density) minus that ensemble's own minimum over occupied bins. Panels share "
            "the grid and colour range, not an absolute zero."),
        "solver": "sklearn PCA(svd_solver='full')",
        "tolerance_pc2": tolerance, "alignment_tolerance": "10 * float32 epsilon, scale-relative",
        "software": {"numpy": np.__version__, "mdtraj": md.__version__,
                     "scikit_learn": sklearn.__version__},
        "memory": memory_checks,
        "reference_source_frames": "unknown",
        "reference_source_frames_reason": (
            "The orchestrator emits its input-selection manifest after the analysis returns; "
            "it does not pass source-frame metadata to DimRed."),
    }
    return {"labels": np.asarray(labels, dtype="U80"), "projections": projections,
            "landscapes": landscapes, "mean": mean, "components": components,
            "variance": np.asarray(model.explained_variance_, dtype=np.float64),
            "ratio": np.asarray(model.explained_variance_ratio_, dtype=np.float64),
            "alignment_xyz": np.asarray(reference.xyz[0, ref_idx], dtype=np.float64),
            "selected_indices": np.asarray(ref_idx, dtype=np.int64),
            "atom_keys": np.asarray(
                [json.dumps(key, separators=(",", ":")) for key in ref_keys], dtype="U256"),
            "edges_x": edges_x, "edges_y": edges_y, "residuals": np.asarray(residuals),
            "source_frames": source_frames, "selections": selections,
            "temperature_K": np.asarray([np.nan if t is None else t for t in temps]),
            "sampling": np.asarray(
                [item["sampling"] for item in [ref_meta, *targets]], dtype="U16"),
            "model_id": model_id}


_OUTPUTS = ("common_pca_model.npz", "common_pca_projections.dat", "common_pca_landscapes.npz",
            "common_pca_landscapes.png", "common_pca_landscapes.svg")


def run_comparison(analysis: Any, traj: md.Trajectory) -> AnalysisResult:
    """Write one comparative run without disturbing default PCA artifacts."""
    started = datetime.now(timezone.utc).isoformat()
    opened = figures_open()
    analysis.output_dir.mkdir(parents=True, exist_ok=True)
    for name in _OUTPUTS:
        (analysis.output_dir / name).unlink(missing_ok=True)
    for key in ("comparison", "memory", "made_whole", "refused", "figures_not_plotted"):
        analysis.findings.pop(key, None)
    options_path = analysis._write_options_manifest()
    try:
        analysis.result = analysis.compute(traj)
        details = analysis.result
        model_path = analysis.output_dir / "common_pca_model.npz"
        np.savez(model_path, schema_version=np.int64(1),
                 alignment_xyz=details["alignment_xyz"], mean=details["mean"].reshape(-1, 3),
                 components=details["components"].reshape(2, -1, 3),
                 variance=details["variance"], ratio=details["ratio"],
                 selected_indices=details["selected_indices"], atom_keys=details["atom_keys"],
                 reference_frame=np.int64(0), units=np.asarray("nm"),
                 fit_solver=np.asarray("full"), model_id=np.asarray(details["model_id"]))
        projections_path = analysis.output_dir / "common_pca_projections.dat"
        with projections_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(("ensemble", "label", "retained_frame", "source_frame",
                             "component_1_nm", "component_2_nm"))
            for ensemble, (label, points, source) in enumerate(zip(
                details["labels"], details["projections"], details["source_frames"]
            )):
                for i, row in enumerate(points):
                    writer.writerow((ensemble, label, i,
                                     "" if source is None else int(source[i]),
                                     float(row[0]), float(row[1])))  # repr round-trips
        grids = details["landscapes"]
        counts = np.stack([grid["counts"] for grid in grids]).astype(np.int64)
        density = np.stack([grid["density"] for grid in grids]).astype(np.float64)
        probability = counts / counts.sum(axis=(1, 2), keepdims=True)
        reduced = np.stack([grid["free_energy"] for grid in grids]).astype(np.float64)
        landscapes_path = analysis.output_dir / "common_pca_landscapes.npz"
        np.savez(landscapes_path, schema_version=np.int64(1), edges_pc1=details["edges_x"],
                 edges_pc2=details["edges_y"], counts=counts, probability=probability,
                 density=density, reduced_occupancy=reduced, labels=details["labels"],
                 temperatures_K=details["temperature_K"], sampling=details["sampling"],
                 unit=np.asarray("dimensionless"))
        figure_path = analysis.output_dir / "common_pca_landscapes.png"

        def plot() -> None:
            nrows = 1 if len(grids) <= 2 else 2
            fig, placeholder = new_figure(figsize=(10.0, 6.0 if nrows == 1 else 9.0))
            fig.delaxes(placeholder)
            layout = fig.add_gridspec(nrows + 2, 2, height_ratios=[1] * nrows + [0.07, 0.2],
                                      hspace=0.32 if nrows == 1 else 0.45,
                                      top=None if nrows == 1 else 0.92)
            fig.suptitle("Common PCA empirical occupancy landscapes", y=0.99)
            axes = [fig.add_subplot(layout[row, col])
                    for row in range(nrows) for col in range(2)]
            vmax = max(float(np.nanmax(grid)) for grid in reduced)
            image = None
            for i, ax in enumerate(axes):
                if i >= len(grids):
                    fig.delaxes(ax)
                    continue
                image = ax.pcolormesh(details["edges_x"], details["edges_y"],
                                      reduced[i].T, cmap="viridis", vmin=0, vmax=vmax,
                                      shading="flat")
                temp = details["temperature_K"][i]
                temperature = "unknown T" if np.isnan(temp) else f"{temp:g} K"
                sampling = str(details["sampling"][i])
                ax.set_title(f"{details['labels'][i]} (N={len(details['projections'][i])}; "
                             f"{temperature}; {sampling})", parse_math=False)
                ax.set_xlabel("PC 1 (nm)")
                ax.set_ylabel("PC 2 (nm)")
                ax.set_xlim(float(details["edges_x"][0]), float(details["edges_x"][-1]))
                ax.set_ylim(float(details["edges_y"][0]), float(details["edges_y"][-1]))
            if image is not None:
                bar_axis = fig.add_subplot(layout[nrows, :])
                bar = fig.colorbar(image, cax=bar_axis, orientation="horizontal")
                bar.set_label("Reduced empirical occupancy landscape (dimensionless)")
            note = (f"Reference-fitted model {details['model_id'][:12]}. "
                    + " ".join(analysis.findings["comparison"]["qualifications"]))
            note_axis = fig.add_subplot(layout[nrows + 1, :])
            note_axis.set_axis_off()
            note_axis.text(0.5, 0.5, textwrap.fill(note, width=140),
                           ha="center", va="center", fontsize=8)
            save_figure(fig, figure_path)

        try:
            plotted = analysis._plotted(figure_path, plot, cells=analysis.landscape_bins)
        except Exception as error:  # numerical artifacts remain useful without this figure
            close_figures_opened_since(opened)
            for leftover in (figure_path, figure_path.with_suffix(".svg")):
                leftover.unlink(missing_ok=True)
            analysis.findings["comparison"]["figure_status"] = (
                f"unavailable: {type(error).__name__}: {error}")
            plotted = []
        else:
            analysis.findings["comparison"]["figure_status"] = (
                "available" if plotted else "unavailable: memory refusal")
        options_path = analysis._write_options_manifest()
        artifacts = [options_path, model_path, projections_path, landscapes_path, *plotted]
        message = "dimred: common PCA comparison complete"
        if not plotted:
            message += "; numerical artifacts saved, comparison figure unavailable"
        return AnalysisResult(name="dimred", status="ok", data=analysis.result,
                              output_dir=analysis.output_dir,
                              figure_path=figure_path if plotted else None,
                              data_path=projections_path, options_path=options_path,
                              artifacts=artifacts, message=message, started_at=started,
                              finished_at=datetime.now(timezone.utc).isoformat())
    except Exception as exc:  # noqa: BLE001 - an analysis refusal is a recorded result
        close_figures_opened_since(opened)
        analysis.findings.pop("comparison", None)
        for name in _OUTPUTS:
            try:
                (analysis.output_dir / name).unlink(missing_ok=True)
            except OSError:  # the original error is what the result reports
                pass
        code = getattr(exc, "code", None)
        if code:
            analysis.findings["refused"] = {"code": code, "said": str(exc)}
        try:
            options_path = analysis._write_options_manifest()
        except Exception:  # noqa: BLE001
            pass
        return AnalysisResult(name="dimred", status="error", output_dir=analysis.output_dir,
                              options_path=options_path, message=f"dimred: {exc}",
                              started_at=started,
                              finished_at=datetime.now(timezone.utc).isoformat())
