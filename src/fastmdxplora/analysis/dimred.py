"""Dimensionality reduction.

Projects the high-dimensional configuration space of an MD trajectory
down to two or three dimensions for visualization. Four methods -- PCA,
MDS, t-SNE and UMAP -- which answer different questions: PCA finds the
directions of largest variance in the coordinates, while MDS finds an
arrangement preserving the distances between frames, and that distance is
RMSD.

  - **PCA** (default): principal component analysis on the aligned
    Cartesian coordinates. Linear, fast, decomposes the variance into
    orthogonal collective modes. Standard in MD analysis.
  - **t-SNE**: t-distributed stochastic neighbor embedding. Non-linear,
    preserves local neighborhood structure, useful for visualizing
    metastable basins. Stochastic — set ``random_state`` for reproducibility.
  - **MDS**: classical (Torgerson) multidimensional scaling of the
    pairwise RMSD matrix: the leading eigenvectors of its double-centred
    squared distances, an arrangement whose distances match the RMSDs
    exactly where they are Euclidean, and approximately otherwise.
  - **UMAP** (optional): uniform manifold approximation and projection.
    Non-linear and generally faster than t-SNE; how much more of the
    global structure it keeps is debated, so distances between its
    clusters are not to be read as RMSDs. Requires the optional
    ``umap-learn`` package.

Every frame is projected by default, the equilibration from the starting
structure included; ``start`` begins later, and the record says how many
frames were projected and whether the equilibration the RMSD detects is
among them (:mod:`fastmdxplora.analysis.starting_frame`).

Each method produces one 2-D scatter ``dimred_<method>.png`` colored
by frame index (the "trajectory trace" visualization), plus a data file
``dimred_<method>.dat`` with the projected coordinates.

References
----------
Amadei, A.; Linssen, A. B. M.; Berendsen, H. J. C. *Proteins* **1993**, 17, 412 (PCA).
van der Maaten, L.; Hinton, G. *J. Mach. Learn. Res.* **2008**, 9, 2579 (t-SNE).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import matplotlib.pyplot as plt
import mdtraj as md
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.manifold import TSNE

from fastmdxplora.protein_names import ALPHA_CARBONS
from fastmdxplora.analysis.base import Analysis, AnalysisResult, superposed
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.analysis.plotting import (
    close_figures_opened_since, figures_open, match_colorbar_font, new_figure, save_figure)
from fastmdxplora.analysis.rmsd import refuse_too_few_to_align
from fastmdxplora.analysis.starting_frame import first_frame, start_as_given
from fastmdxplora.refusals import StudyError
from fastmdxplora.refusals import BackendUnavailable
from fastmdxplora.utils.logging import get_logger

logger = get_logger("analysis.dimred")


VALID_METHODS = ("pca", "mds", "tsne", "umap")


class DimRed(Analysis):
    """Dimensionality reduction on the trajectory.

    Parameters
    ----------
    methods : sequence of str, default ``("pca",)``
        Which methods to run. Choices: ``"pca"``, ``"mds"``, ``"tsne"``,
        ``"umap"``.
    n_components : int, default 2
        Dimensionality of the embedding. For visualization keep at 2 (or 3).
    perplexity : float, default 30.0
        t-SNE perplexity parameter. Roughly the effective number of
        neighbors each point is balanced against; 5-50 is typical. It must
        stay below the number of frames, so the value used is
        min(perplexity, max(1, (n_frames - 1) / 3)) and is recorded.
    n_neighbors : int, default 15
        UMAP neighborhood size.
    min_dist : float, default 0.1
        UMAP minimum distance between embedded points.
    random_state : int, default 42
        Random seed for stochastic methods (t-SNE, UMAP).
    landscape_bins : int, default 40
        Bins along each of PC 1 and PC 2 for the free-energy landscape
        written beside the PCA projection. Fewer bins smooth it and more
        resolve it, at the cost of more empty bins on a short run.
    start : float or "equilibrated", default 0
        Where in the trajectory the projection begins, in ns. At 0 every frame is
        projected, the equilibration from the starting structure included,
        as before; a relaxation can then take a principal component of its
        own. A time in ns begins at the first frame at or after it, and
        ``"equilibrated"`` at the end of the equilibration Chodera's method
        detects in the RMSD of the selected atoms from the first frame. How
        many frames were projected, from where, and whether the equilibration
        is among them is recorded under ``findings.frames`` either way.
    selection : str, optional
        MDTraj atom selection used to flatten coordinates. Defaults to
        ``"protein and name CA"`` (CA-only is a standard featurization for protein
        DimRed).
    **kwargs
        Standard base-class options.

    Output
    ------
    Per method, in ``<output_dir>/dimred/``:
      - ``dimred_<method>.dat`` — CSV with frame + component columns.
      - ``dimred_<method>.png`` — 2-D scatter colored by frame index.
    With PCA, also ``dimred_pca_landscape.npz`` and
    ``dimred_pca_landscape.png``: the free energy -kT ln P over PC 1 and
    PC 2 (see :func:`free_energy_landscape`), in kJ/mol at the study's
    temperature where one is recorded and in units of kT where none is.
    """

    name = "dimred"
    description = "Dimensionality reduction"
    default_selection = ALPHA_CARBONS
    #: A superposition needs three atoms to be defined. Without this,
    #: MDTraj returns identity rotations and the frames are compared
    #: unaligned -- a real run clustered a capped alanine and found one
    #: distinct cluster where five were asked for, and reported ok.
    min_atoms_to_align = 3

    def __init__(
        self,
        *,
        methods: Sequence[str] = ("pca",),
        n_components: int = 2,
        perplexity: float = 30.0,
        n_neighbors: int = 15,
        min_dist: float = 0.1,
        random_state: int = 42,
        landscape_bins: int = 40,
        start: float | str = 0.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        methods = list(methods) if methods else ["pca"]
        methods = [m.lower() for m in methods]
        unknown = [m for m in methods if m not in VALID_METHODS]
        if unknown:
            raise StudyError(
                f"Unknown dimred method(s): {unknown}. Valid: {VALID_METHODS}"
            , code="analysis.option.not_permitted")
        self.methods: list[str] = methods
        self.n_components: int = int(n_components)
        self.perplexity: float = float(perplexity)
        self.n_neighbors: int = int(n_neighbors)
        self.min_dist: float = float(min_dist)
        self.random_state: int = int(random_state)
        self.landscape_bins: int = int(landscape_bins)
        if self.landscape_bins < 2:
            raise StudyError(
                f"`landscape_bins` must be at least 2 along each component; got "
                f"{landscape_bins!r}.", code="analysis.option.out_of_range")
        self.options.update(
            methods=self.methods,
            n_components=self.n_components,
            perplexity=self.perplexity,
            n_neighbors=self.n_neighbors,
            min_dist=self.min_dist,
            random_state=self.random_state,
            landscape_bins=self.landscape_bins,
        )
        self.start: float | str | None = start_as_given(start)
        if self.start is not None:
            self.options["start"] = self.start

    def compute(self, traj: md.Trajectory) -> dict[str, np.ndarray]:
        """Each method's projection (:meth:`_projected`), with BLAS and
        OpenMP held to one thread under an address-space limit, as
        clustering's are (:func:`~fastmdxplora.analysis.cluster.threads_held_to_one`):
        an OpenBLAS thread whose buffer could not be mapped there retried for
        ever."""
        from fastmdxplora.analysis.cluster import threads_held_to_one

        with threads_held_to_one(self.findings):
            return self._projected(traj)

    def _projected(self, traj: md.Trajectory) -> dict[str, np.ndarray]:
        """Run all requested DimRed methods.

        Returns
        -------
        dict
            Maps method name → (n_frames, n_components) embedding array.
        """
        atom_idx = self.select_atoms(traj)
        # A superposition needs three atoms (on one, every frame aligns
        # onto the first exactly): said with the selection that works, not
        # as "nothing to decompose".
        refuse_too_few_to_align(self, traj, atom_idx)
        first, record = first_frame(traj, atom_idx, self.start)
        self.findings["frames"] = record
        self._first_frame = first
        if first:
            traj = traj[first:]

        # Superpose the trajectory onto frame 0 using the selected atoms,
        # then flatten each frame's coordinates into a feature vector.
        # This is the standard "Cartesian PCA" featurization.
        aligned = superposed(traj, frame=0, atom_indices=atom_idx)
        coords = aligned.xyz[:, atom_idx, :].reshape(traj.n_frames, -1)

        # Mean-center: each feature should have zero mean before linear
        # decomposition. (PCA does this internally, but it's also needed
        # for t-SNE/UMAP to be scale-invariant.)
        coords = coords - coords.mean(axis=0)

        # A structure that does not move has no variance to decompose. PCA
        # divides the variance of each component by the total, which is zero
        # here, so the ratios came out NaN and the figure was labelled
        # "PC 1 (nan%)" over a scatter of coincident points. The other methods
        # fare no better: there are no neighbourhoods to preserve among points
        # that are all the same point.
        if not np.any(coords.var(axis=0) > 0):
            raise StudyError(
                "There is nothing to decompose: once aligned, every frame of "
                "the selected atoms is identical, so the coordinates have no "
                "variance. A single repeated structure, a minimisation written "
                "as a trajectory, or a selection whose atoms happen not to "
                "move will do this. Widen the selection, or analyse a "
                "trajectory that samples."
            , code="analysis.sampling.no_variance")

        # The memory read once, before any method runs: each method planned
        # on its own (`_methods_that_fit`), and one that does not fit
        # refused, the others run and saved, the refusal recorded; the
        # last left refused is the analysis's refusal.
        methods = _methods_that_fit(self, traj.n_frames)
        self._methods_run = methods

        results: dict[str, np.ndarray] = {}
        for method in list(methods):  # a method may leave it as it runs
            # Where the memory runs out all the same, the method is refused
            # with what to set and the rest saved: t-SNE's neighbours at
            # 24,000 frames ended the analysis, and PCA's projection, computed
            # first, was lost with it.
            try:
                embedding = self._projection(method, traj, coords, aligned, atom_idx)
            except (MemoryError, OSError) as error:
                from fastmdxplora.analysis.cluster import ran_out

                if not ran_out(error):
                    raise
                # As it ran: what made the plan short is not known here, and
                # "the memory left fell" was said where nothing had.
                self._refused(methods, method, StudyError(
                    f"{NAMED[method]} of {traj.n_frames:,} frames ran out of memory as "
                    f"it ran ({error}), though the check planned it would fit. "
                    f"{_INSTEAD[method]}"
                    f"{'or l' if _INSTEAD[method] else 'L'}oad every second "
                    "frame for the analyses (`analysis: {stride: 2}`, which every "
                    "analysis then reads).", code="analysis.memory.matrix_too_large",
                    frames=traj.n_frames, stride=2), ran=True)
                continue
            results[method] = np.asarray(embedding, dtype=np.float64)
        # What each refusal says was run beside it, as it ended.
        for refused in self.findings.get("methods_refused", {}).values():
            refused["kept"] = list(results)
        if "mds_refused" in self.findings:
            self.findings["mds_refused"]["kept"] = list(results)

        return results

    def _projection(self, method: str, traj: md.Trajectory, coords: np.ndarray,
                    aligned: md.Trajectory, atom_idx: np.ndarray) -> np.ndarray:
        """One method's projection of the frames."""
        if method == "pca":
            model = PCA(n_components=self.n_components)
            embedding = model.fit_transform(coords)
            # Stash variance ratios for the plot annotation
            self._explained_variance = getattr(model, "explained_variance_ratio_", None)
            # The motions themselves, kept beside the projections: the
            # projections alone say when the study moved, not how.
            self._modes = {
                "mean": aligned.xyz[:, atom_idx, :].mean(axis=0).astype(np.float64),
                "vectors": np.asarray(model.components_, dtype=np.float64).reshape(
                    len(model.components_), len(atom_idx), 3),
                "variance": np.asarray(model.explained_variance_, dtype=np.float64),
                "ratio": np.asarray(model.explained_variance_ratio_, dtype=np.float64),
                "atoms": np.asarray(atom_idx, dtype=np.int64),
                "frames": np.int64(traj.n_frames),
            }
            return embedding
        if method == "mds":
            # Classical MDS on the pairwise RMSD between frames, which is
            # what version 1 offered and what a reader comparing against
            # it needs. It answers a different question from PCA: PCA
            # finds the directions of largest variance in the
            # coordinates, while this finds an arrangement in which the
            # distances between frames are preserved as closely as
            # possible -- and the distance between two frames is their
            # RMSD, which is what a structural biologist means by how
            # different two conformations are. PCA's variance shares stay
            # where PCA put them: MDS has none of its own, and clearing the
            # one attribute both shared took "(98.5%)" off the PCA axes
            # whenever MDS ran after it.
            distances = _pairwise_rmsd(traj, self.select_atoms(traj))
            try:
                return _classical_mds(distances, self.n_components)
            finally:
                del distances
        if method == "tsne":
            p = _tsne_perplexity(self.perplexity, traj.n_frames)
            self.findings.setdefault("tsne", {})["perplexity_used"] = float(p)
            model = TSNE(
                n_components=self.n_components,
                perplexity=p,
                random_state=self.random_state,
                init="pca",
                learning_rate="auto",
            )
            return model.fit_transform(coords)
        try:
            import umap  # type: ignore[import-not-found]
        except ImportError as exc:
            raise BackendUnavailable(
                "UMAP requested but the umap-learn package is not "
                "installed. Install it with: pip install umap-learn"
            , code="environment.backend.missing") from exc
        model = umap.UMAP(
            n_components=self.n_components,
            n_neighbors=min(self.n_neighbors, traj.n_frames - 1),
            min_dist=self.min_dist,
            random_state=self.random_state,
        )
        return model.fit_transform(coords)

    def _mds_note(self) -> str:
        """The methods refused, said on the other projections' figures, one
        short sentence each (:func:`refused_notes`)."""
        return " ".join(refused_notes(self.findings.get("methods_refused", {})))

    def _refused(self, methods: list[str], method: str, refused: StudyError,
                 ran: bool = False, stride: int | None = None,
                 need_from: str = "method") -> None:
        """``method`` taken out of ``methods``, its refusal recorded and logged,
        the other methods' projections saved; the last method left, the
        refusal is the analysis's. ``ran``: refused as it ran out, not
        before. MDS's is recorded as ``mds_refused`` as well, as the report
        reads it."""
        if methods == [method]:
            self._refused_before_it_ran = not ran
            raise refused
        methods.remove(method)
        kept = list(methods)
        # What set its need: what the method makes, or the figures planned
        # beside it (`need_from`), as its short note says.
        record = {"code": "analysis.memory.matrix_too_large", "said": str(refused),
                  "kept": kept, "before_it_ran": not ran, "stride_that_fits": stride,
                  "need_from": need_from}
        self.findings.setdefault("methods_refused", {})[method] = record
        if method == "mds":
            self.findings["mds_refused"] = {key: record[key]
                                            for key in ("code", "said", "kept")}
        logger.warning("%s The other methods asked for (%s) %s.", refused,
                       ", ".join(NAMED[each] for each in kept),
                       "are saved" if ran else "still run")

    _refused_before_it_ran = False

    # --------------------------------------------------------------
    # Override run() so each method gets its own output pair.
    # --------------------------------------------------------------
    def run(self, traj: md.Trajectory) -> AnalysisResult:
        from datetime import datetime, timezone

        # Its figures are closed if it fails part-way, as base.run's are.
        opened = figures_open()
        started = datetime.now(timezone.utc).isoformat()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        options_path = self._write_options_manifest()

        try:
            self.result = self.compute(traj)
            # Written again, as the base class does, so what the projection
            # found out about its own run is kept beside what it was told.
            options_path = self._write_options_manifest()
            artifacts: list[Path] = [options_path]
            for method, embedding in self.result.items():
                # Data file
                data_path = self.output_dir / f"dimred_{method}.dat"
                # The frame in the trajectory given, so a projection begun
                # later than the first frame says which frames it holds.
                cols = {"frame": self._first_frame + np.arange(len(embedding))}
                for i in range(embedding.shape[1]):
                    cols[f"component_{i + 1}"] = embedding[:, i]
                pd.DataFrame(cols).to_csv(data_path, index=False)
                artifacts.append(data_path)

                # Figure
                fig_path = self.output_dir / f"dimred_{method}.png"

                def scatter(method: str = method, embedding: np.ndarray = embedding,
                            path: Path = fig_path) -> None:
                    fig, ax = new_figure(
                        title=f"{self.figure_title()} ({method.upper()})",
                        figsize=self._user_figsize,
                    )
                    _plot_dimred_scatter(
                        ax, embedding, method,
                        self._explained_variance if method == "pca" else None)
                    if self.findings.get("methods_refused"):
                        # Said on the figure too, below it: one looked at
                        # alone, or opened from the card, said nothing of it.
                        fig.text(0.5, -0.02, self._mds_note(), ha="center", va="top",
                                 fontsize=7, wrap=True)
                    save_figure(fig, path)

                artifacts.extend(self._plotted(fig_path, scatter))

            if "pca" in self.result and self._modes is not None:
                # Each motion as a unit vector over the atoms (nm per nm of
                # projection), its variance (nm^2), its share of the total,
                # the mean structure fitted to the first frame (nm), and the
                # atoms, numbered in the trajectory's topology.
                modes_path = self.output_dir / "dimred_pca_modes.npz"
                np.savez(modes_path, **self._modes)
                artifacts.append(modes_path)
            if "pca" in self.result and self.result["pca"].shape[1] >= 2:
                artifacts.extend(self._write_landscape(self.result["pca"]))
                # Once more, so the landscape's record is kept with the rest.
                self._write_options_manifest()
            finished = datetime.now(timezone.utc).isoformat()
            primary = next(iter(self.result))
            return AnalysisResult(
                name=self.name,
                status="ok",
                data=self.result,
                output_dir=self.output_dir,
                figure_path=self.output_dir / f"dimred_{primary}.png",
                data_path=self.output_dir / f"dimred_{primary}.dat",
                options_path=options_path,
                artifacts=artifacts,
                message=f"{self.name}: ok ({', '.join(self.result)})" + (
                    "; " + " and ".join(NAMED[method] for method in
                                        self.findings["methods_refused"])
                    + " refused for memory, the rest run and saved"
                    if self.findings.get("methods_refused") else "") + (
                    "; not plotted, the memory ran out: "
                    + ", ".join(self.findings["figures_not_plotted"])
                    if self.findings.get("figures_not_plotted") else ""),
                started_at=started,
                finished_at=finished,
            )
        except Exception as exc:  # noqa: BLE001
            close_figures_opened_since(opened)
            finished = datetime.now(timezone.utc).isoformat()
            code = getattr(exc, "code", None)
            if code and self.findings:
                # The memory it checked, and the refusal's numbers with it,
                # as clustering keeps them, and whether it was refused before
                # it ran.
                self.findings["refused"] = {"code": code, "said": str(exc),
                                            "before_it_ran": self._refused_before_it_ran}
                try:
                    options_path = self._write_options_manifest()
                except Exception:  # noqa: BLE001 -- the refusal matters more
                    pass
            return AnalysisResult(
                name=self.name,
                status="error",
                output_dir=self.output_dir,
                options_path=options_path,
                message=f"{self.name}: {exc}",
                started_at=started,
                finished_at=finished,
            )

    def _write_landscape(self, embedding: np.ndarray) -> list[Path]:
        """The free-energy landscape on PC 1 and PC 2, as data and a figure.

        ``dimred_pca_landscape.npz`` holds ``free_energy`` (bins along PC 1
        by bins along PC 2, NaN where no frame fell), ``edges_pc1`` and
        ``edges_pc2`` (nm), ``density`` (nm^-2), ``counts``, ``unit``
        (``"kJ/mol"`` or ``"kT"``) and ``temperature_K`` (NaN where none
        was found).
        """
        temperature, source = _study_temperature(self.output_dir)
        landscape = free_energy_landscape(
            embedding[:, 0], embedding[:, 1], bins=self.landscape_bins,
            temperature_K=temperature)
        n_empty = int(np.isnan(landscape["free_energy"]).sum())
        record: dict[str, Any] = {
            "unit": landscape["unit"],
            "temperature_K": temperature,
            "bins": self.landscape_bins,
            "empty_bins": n_empty,
            "frames": int(embedding.shape[0]),
        }
        if temperature is None:
            record["said"] = (
                "No study temperature was found beside these frames "
                "(simulation/simulation_parameters.json), so the landscape is "
                "-ln P in units of kT, with no value in kJ/mol given.")
        else:
            record["temperature_from"] = source
            record["said"] = (
                f"-kT ln P at the study's {temperature:g} K, in kJ/mol, its "
                "lowest bin set to zero.")
        biased = _biasing_method(self.output_dir)
        if biased:
            record["biased_by"] = biased
            record["said"] += (
                f" The run was biased by {biased}, so this is the landscape of "
                "the biased ensemble, not of the unbiased system.")
        self.findings["landscape"] = record

        data_path = self.output_dir / "dimred_pca_landscape.npz"
        np.savez(
            data_path,
            free_energy=landscape["free_energy"],
            edges_pc1=landscape["edges_x"], edges_pc2=landscape["edges_y"],
            density=landscape["density"], counts=landscape["counts"],
            unit=np.array(landscape["unit"]),
            temperature_K=np.float64(np.nan if temperature is None else temperature))
        figure_path = self.output_dir / "dimred_pca_landscape.png"

        def plotted() -> None:
            fig, ax = new_figure(title="Free-energy landscape (PCA)",
                                 figsize=self._user_figsize)
            _plot_landscape(ax, landscape, self._explained_variance)
            save_figure(fig, figure_path)

        return [data_path, *self._plotted(figure_path, plotted)]

    def _plotted(self, path: Path, plot: Any) -> list[Path]:
        """The figure ``plot`` saves at ``path``, and its SVG; where the
        memory runs out plotting it, none, said in the record
        (``figures_not_plotted``) with what to set, and the other figures and
        files still saved, as clustering's are."""
        from fastmdxplora.analysis.cluster import figure_more_said, ran_out

        opened = figures_open()
        try:
            plot()
        except (MemoryError, OSError) as error:
            if not ran_out(error):
                raise
            close_figures_opened_since(opened)
            for leftover in (path, path.with_suffix(".svg")):
                leftover.unlink(missing_ok=True)
            # A scatter's and the landscape's canvas are the same at any
            # stride: "Set `analysis: {stride: 2}`" was said, and did not help.
            said = (f"{path.name} was not plotted: the memory ran out plotting it "
                    f"({error}). Its data is saved. "
                    + figure_more_said(self.findings.get("memory", {})))
            self.findings.setdefault("figures_not_plotted", {})[path.name] = said
            logger.warning(said)
            return []
        return [path] + ([path.with_suffix(".svg")]
                         if path.with_suffix(".svg").is_file() else [])

    # Required by the ABC; used only when a caller draws onto their own axes.
    def plot(self, result: dict[str, np.ndarray], ax: plt.Axes) -> None:
        primary = next(iter(result))
        _plot_dimred_scatter(
            ax, result[primary], primary,
            self._explained_variance if primary == "pca" else None,
        )

    _explained_variance: np.ndarray | None = None
    _modes: dict[str, np.ndarray] | None = None
    _first_frame: int = 0


def free_energy_landscape(x: np.ndarray, y: np.ndarray, *, bins: int = 40,
                          temperature_K: float | None = None) -> dict[str, Any]:
    """The free energy over two coordinates, from how often each bin is visited.

    G(x, y) = -kT ln P(x, y), with P the histogram normalised over the bin
    area (so the sum of P times the area of each bin is one) and k
    Boltzmann's constant in kJ/mol/K. Bins no frame visited have no free
    energy and are NaN rather than infinite. The lowest bin is set to zero:
    only differences are defined. Without a temperature the result is
    G/kT = -ln P, in units of kT, and ``unit`` says so.
    """
    from fastmdxplora.analysis.reweight import KB_KJ_PER_MOL_K

    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    counts, edges_x, edges_y = np.histogram2d(x, y, bins=int(bins))
    area = np.outer(np.diff(edges_x), np.diff(edges_y))
    density = counts / (counts.sum() * area)
    with np.errstate(divide="ignore"):
        reduced = np.where(counts > 0, -np.log(np.where(density > 0, density, 1.0)), np.nan)
    reduced -= np.nanmin(reduced)
    if temperature_K is None:
        energy, unit = reduced, "kT"
    else:
        energy, unit = KB_KJ_PER_MOL_K * float(temperature_K) * reduced, "kJ/mol"
    return {"free_energy": energy, "edges_x": edges_x, "edges_y": edges_y,
            "density": density, "counts": counts.astype(np.int64), "unit": unit}


def _study_temperature(output_dir: Path) -> tuple[float | None, str | None]:
    """The production temperature the study recorded, and where, or None.

    Read from ``simulation/simulation_parameters.json`` beside the analysis
    folder, where the reweighting reads it. A trajectory carries no
    temperature of its own.
    """
    from fastmdxplora.analysis.reweighted_averages import _find, _temperature

    temperature, found = _temperature(Path(output_dir))
    if not found:
        return None, None
    path = _find(Path(output_dir), "simulation_parameters.json")
    return float(temperature), (str(path) if path is not None else None)


def _biasing_method(output_dir: Path) -> str | None:
    from fastmdxplora.analysis.reweighted_averages import biasing_method

    try:
        return biasing_method(Path(output_dir))
    except Exception:  # noqa: BLE001 - a note, not a result
        return None


def _plot_landscape(ax: plt.Axes, landscape: dict[str, Any],
                    explained_variance: np.ndarray | None) -> None:
    """The landscape as a map over PC 1 and PC 2, empty bins left blank."""
    energy = np.ma.masked_invalid(landscape["free_energy"])
    mesh = ax.pcolormesh(landscape["edges_x"], landscape["edges_y"], energy.T,
                         cmap="viridis", shading="flat")
    bar = ax.figure.colorbar(mesh, ax=ax, shrink=0.85)
    bar.set_label(f"Free energy ({landscape['unit']})")
    match_colorbar_font(bar, ax)
    if explained_variance is not None and len(explained_variance) >= 2:
        ax.set_xlabel(f"PC 1 ({explained_variance[0] * 100:.1f}%), nm")
        ax.set_ylabel(f"PC 2 ({explained_variance[1] * 100:.1f}%), nm")
    else:
        ax.set_xlabel("PC 1 (nm)")
        ax.set_ylabel("PC 2 (nm)")


def _tsne_perplexity(asked: float, n_frames: int) -> float:
    """The perplexity t-SNE is given: min(asked, max(1, (n - 1) / 3)).

    scikit-learn refuses a perplexity at or above the number of samples.
    The earlier rule, min(asked, max(5, n / 4)), let the floor of 5 win on
    five frames or fewer, and t-SNE then refused to run at all. A third of
    the other frames keeps it below n with room for the neighbourhood to
    mean something, and 1 is the smallest perplexity that does.
    """
    return float(min(float(asked), max(1.0, (int(n_frames) - 1) / 3.0)))


def _plot_dimred_scatter(
    ax: plt.Axes,
    embedding: np.ndarray,
    method: str,
    explained_variance: np.ndarray | None,
) -> None:
    """2-D scatter of the embedding, colored by frame index ("trajectory trace")."""
    if embedding.shape[1] < 2:
        # 1-D fallback: just plot vs frame
        ax.plot(embedding[:, 0])
        ax.set_xlabel("Frame")
        ax.set_ylabel("Component 1")
        return

    frames = np.arange(embedding.shape[0])
    sc = ax.scatter(
        embedding[:, 0],
        embedding[:, 1],
        c=frames,
        cmap="viridis",
        s=10,
        edgecolor="none",
    )
    cbar = ax.figure.colorbar(sc, ax=ax, shrink=0.85)
    cbar.set_label("Frame")

    if method == "pca" and explained_variance is not None and len(explained_variance) >= 2:
        ax.set_xlabel(f"PC 1 ({explained_variance[0] * 100:.1f}%)")
        ax.set_ylabel(f"PC 2 ({explained_variance[1] * 100:.1f}%)")
    else:
        ax.set_xlabel(f"{method.upper()} 1")
        ax.set_ylabel(f"{method.upper()} 2")


register_analysis(DimRed.name, DimRed)


#: The methods as the docs name them.
NAMED = {"pca": "PCA", "mds": "MDS", "tsne": "t-SNE", "umap": "UMAP"}
#: What each method makes that grows with the frames, as its refusal says it.
_WHAT = {"pca": "its copy of the coordinates",
         "mds": "its frame-by-frame RMSD matrix",
         "tsne": "its lists of each frame's nearest neighbours and their affinities",
         "umap": "its lists of each frame's nearest neighbours"}
#: What a refusal of each method offers in its place.
_INSTEAD = {"pca": "",
            "mds": "PCA, t-SNE and UMAP make no such matrix (`methods: [pca]`); ",
            "tsne": "PCA keeps no neighbours (`methods: [pca]`); ",
            "umap": "PCA keeps no neighbours (`methods: [pca]`); "}

#: The address space the projections map past what the process holds once
#: the libraries are mapped (:func:`fastmdxplora.analysis.cluster.libraries_mapped`),
#: in bytes: once, for each scatter and for PCA's landscape (each figure at
#: 300 dpi, PNG and SVG). The figures are plotted once every method has run
#: and freed what it made, so the larger of the figures and the method
#: running is planned on (:func:`address_space_needed`). Measured as the
#: growth of ``VmPeak`` from the check (BLAS on one thread), tri-alanine's
#: 500 frames, in ``fastmdx analyze`` with nothing run before: t-SNE alone
#: 10.7 MiB, PCA alone 21.2, PCA, MDS and t-SNE 32.1; in a new process
#: through the Python API, 0.6, 13.8 and 43.4; after the RMSD and the
#: clustering, PCA, MDS and t-SNE 17.4. Planned on as the most of these
#: (16, 26 and 46 MiB), each run at the room so planned and 0.5 MiB ended
#: with every figure plotted in ``fastmdx analyze``. Planned at 8, 10 and
#: 14 MiB (18, 32 and 52), PCA was refused under ``ulimit -v`` 590000 after
#: the clustering and all three ran there; and as a constant (130 MiB), the
#: libraries' share was counted twice.
ADDRESS_BYTES = 6 * 2**20
SCATTER_BYTES = 10 * 2**20
LANDSCAPE_BYTES = 10 * 2**20

#: How much more a process that ran nothing before maps for the
#: projections than one where the RMSD and the clustering ran first, in
#: bytes (43.4 against 17.4 MiB, PCA, MDS and t-SNE of 500 frames): the
#: margin the plan keeps, said with a refusal under ``ulimit -v``.
MARGIN_BYTES = 26 * 2**20

#: What t-SNE makes for each frame's each neighbour, in bytes: scikit-learn
#: keeps 3 x perplexity + 1 neighbours of every frame, their distances and
#: indices, and the joint probabilities it makes of them in sparse matrices,
#: symmetrised. Measured as the growth of ``VmPeak`` from the check in a new
#: process, t-SNE alone at perplexity 30 (91 neighbours): 34 MiB at 5,000
#: frames, 67 at 10,000, 99 at 15,000, about 75 bytes a neighbour; at 24,000
#: frames, under a room planned as a constant, its neighbours' indices
#: failed to allocate.
TSNE_BYTES_PER_NEIGHBOUR = 80


def refused_notes(refused: dict[str, Any]) -> list[str]:
    """One short sentence for each method refused for memory
    (``findings.methods_refused``), as a projection's figure and card say
    it: refused before it ran, by what set its need (``need_from``), with
    the stride that fits where one does; or stopped as it ran. The whole
    refusal, with the room and where it was read, is in the record: the
    card carried it, two paragraphs under a figure, the cgroup's path in
    each."""
    from fastmdxplora.analysis.cluster import _ordinal

    notes = []
    for method, record in refused.items():
        record = record if isinstance(record, dict) else {}
        name = NAMED.get(str(method), str(method))
        stride = record.get("stride_that_fits")
        if record.get("before_it_ran", True) is False:
            notes.append(f"{name} ran out of memory as it ran and was stopped.")
            continue
        what = ("the figures planned beside it" if record.get("need_from") == "figures"
                else _WHAT.get(str(method), "what it makes"))
        notes.append(f"{name} was not run: {what} did not fit in the memory this process "
                     "can take" + (f"; every {_ordinal(stride)} frame (analysis: "
                                   f"{{stride: {stride}}}) fits it." if stride else "."))
    return notes


def mds_memory_needed(n: int) -> int:
    """The peak bytes, of order n squared, MDS of ``n`` frames makes: the
    pairwise RMSD matrix in float64 (8 n^2) and its doubly centred square,
    whose leading eigenvectors are found in place (8 n^2), as clustering's
    k-means makes them (:func:`fastmdxplora.analysis.cluster.memory_needed`)."""
    return int(16 * float(n) * float(n))


def method_memory_needed(method: str, n: int, perplexity: float = 30.0) -> int:
    """The peak bytes ``method`` makes of ``n`` frames past the coordinates
    every method reads: MDS's matrix (:func:`mds_memory_needed`); t-SNE's
    neighbours (:data:`TSNE_BYTES_PER_NEIGHBOUR` for each of its
    3 x perplexity + 1 a frame); none for PCA, whose copy of the coordinates
    is no more than the two made before the check and freed (the heap they
    left holds it), or for UMAP, which is not measured here."""
    if method == "mds":
        return mds_memory_needed(n)
    if method == "tsne":
        neighbours = min(max(int(n) - 1, 0),
                         int(3 * _tsne_perplexity(perplexity, n) + 1))
        return int(TSNE_BYTES_PER_NEIGHBOUR * neighbours * int(n))
    return 0


def figures_needed(methods: Sequence[str]) -> int:
    """The address space the projections' figures map, in bytes: a scatter
    of each method and PCA's landscape."""
    return SCATTER_BYTES * len(methods) + (LANDSCAPE_BYTES if "pca" in methods else 0)


def address_space_needed(method: str, methods: Sequence[str], n: int,
                         perplexity: float = 30.0) -> int:
    """The address space ``method`` of ``n`` frames maps at its peak, run
    beside ``methods``: :data:`ADDRESS_BYTES`, and the larger of what it
    makes (:func:`method_memory_needed`) and the figures plotted after
    every method has run (:func:`figures_needed`), and what the libraries
    would map where the check left them unmapped
    (:func:`fastmdxplora.analysis.cluster.libraries_owed`)."""
    from fastmdxplora.analysis.cluster import libraries_owed

    return ADDRESS_BYTES + max(method_memory_needed(method, n, perplexity),
                               figures_needed(methods)) + libraries_owed()


def _methods_that_fit(self: "DimRed", n: int) -> list[str]:
    """The methods asked for that fit in the memory this process can take,
    read once for the analysis
    (:func:`fastmdxplora.analysis.cluster.memory_available`): each planned on
    its own, the memory it makes (:func:`method_memory_needed`) in the
    resident memory's share and its address space
    (:func:`address_space_needed`) in what an address-space limit
    (``ulimit -v``) leaves. One that does not fit is refused with the stride
    that fits where one does, and the others still run (:meth:`DimRed._refused`);
    the last left refused, the analysis is. A constant planned for every
    method refused PCA where it fitted, and allowed t-SNE where its
    neighbours did not fit. Recorded either way (``findings.memory``)."""
    from fastmdxplora.analysis.cluster import (
        ADDRESS_SHARE,
        MEMORY_SHARE,
        _ordinal,
        address_room,
        margin_said,
        memory_available,
        more_said,
        need_and_room_said,
        resident_room,
    )

    available, read_from = memory_available()
    address, address_from = address_room(available, read_from)
    available, read_from = resident_room(available, read_from)
    methods = list(self.methods)
    memory = self.findings.setdefault("memory", {})
    memory.update({"frames": n, "bytes_available": available,
                   "available_read_from": read_from, "share_usable": MEMORY_SHARE,
                   "bytes_address_space": address, "address_share_usable": ADDRESS_SHARE,
                   "methods": {}})
    from fastmdxplora.analysis.cluster import libraries_owed

    if address is not None and libraries_owed():
        # Not warmed up: the room was under what the libraries map.
        memory["bytes_libraries_not_mapped"] = libraries_owed()

    def short(method: str, kept: Sequence[str], m: int) -> list[tuple[int, int, str, bool]]:
        found = []
        made = method_memory_needed(method, m, self.perplexity)
        if available is not None and made > MEMORY_SHARE * available:
            found.append((made, available, read_from, False))
        mapped = address_space_needed(method, kept, m, self.perplexity)
        if address is not None and mapped > ADDRESS_SHARE * address:
            found.append((mapped, address, address_from, True))
        return sorted(found, key=lambda each: each[0] / max(each[1], 1)
                      / (ADDRESS_SHARE if each[3] else MEMORY_SHARE))

    while True:
        for method in methods:
            memory["methods"][method] = {
                "bytes_needed": method_memory_needed(method, n, self.perplexity),
                "bytes_address_needed": (address_space_needed(method, methods, n,
                                                              self.perplexity)
                                         if address is not None else None)}
        over = [method for method in methods if short(method, methods, n)]
        if not over:
            return methods
        # One at a time, the one that makes the most first (PCA last): with
        # it refused, the figures left may let the others fit.
        method = max(over, key=lambda each: (
            method_memory_needed(each, n, self.perplexity), each != "pca"))
        need, room, room_from, mapped = short(method, methods, n)[-1]
        stride = 2
        while stride < n and short(method, methods, -(-n // stride)):
            stride += 1
        fits = not short(method, methods, -(-n // stride))
        if fits:
            memory["stride_that_fits"] = stride
            fewer = (f"or load every {_ordinal(stride)} frame for the analyses "
                     f"(`analysis: {{stride: {stride}}}`, which every analysis then "
                     f"reads), {-(-n // stride):,} frames, which fits")
        else:
            fewer = (f"no stride fits it. Run it {more_said(need, room, mapped, room_from)}, "
                     f"or leave `{'dimred' if methods == [method] else method}` out "
                     f"of {'the analyses' if methods == [method] else '`methods`'}")
        # What sets the need, named alone: "for its frame-by-frame RMSD
        # matrix, or for the 4 figures" was said where the matrix was 4 MB
        # of 54.5 and the figures the rest.
        figures = len(methods) + (1 if "pca" in methods else 0)
        by_figures = mapped and figures_needed(methods) >= method_memory_needed(
            method, n, self.perplexity)
        made = (f"for the {figures} figure{'s' if figures > 1 else ''} plotted once every "
                "method has run" if by_figures else f"for {_WHAT[method]}")
        # Every size in one unit, the share computed from the room as said.
        self._refused(methods, method, StudyError(
            f"{NAMED[method]} of {n:,} frames takes about "
            + need_and_room_said(need, room, room_from, mapped, "an analysis",
                                 made=made)
            + "." + (margin_said(MARGIN_BYTES) if mapped else "")
            + f" {(_INSTEAD[method] + fewer)[:1].upper()}"
            f"{(_INSTEAD[method] + fewer)[1:]}. Read as {room_from}.",
            code="analysis.memory.matrix_too_large", frames=n,
            bytes_needed=need, bytes_available=room,
            stride=stride if fits else None),
            stride=stride if fits else None,
            need_from="figures" if by_figures else "method")


def _pairwise_rmsd(traj: Any, atom_indices: Any = None) -> np.ndarray:
    """RMSD between every pair of frames, each pair superposed optimally.

    The same measure clustering uses, so the two agree about how different
    two conformations are -- which was the intent and was not the case.
    Clustering's copy of this helper takes an atom selection and this one did
    not, so MDS measured every atom in the system while every other method in
    the module measured the selection. On a solvated trajectory that is a few
    hundred protein atoms against tens of thousands of diffusing waters: the
    between-state contrast fell from 32x to 1x on a two-state test system,
    and the same `DimRed` call resolved the transition with PCA and hid it
    with MDS.

    It went unnoticed because `default_selection` is "name CA", so the
    orchestrator leaves the selection alone and never narrows the trajectory
    on this analysis's behalf, and because the test that covers MDS uses a
    solvent-free peptide, where every atom *is* the selection.
    """
    # Clustering's, which symmetrises in place: `0.5 * (d + d.T)` made two
    # more matrices.
    from fastmdxplora.analysis.cluster import _pairwise_rmsd as rows_in_place

    atoms = np.arange(traj.n_atoms) if atom_indices is None else np.asarray(atom_indices)
    return rows_in_place(traj, atoms)


def _classical_mds(distances: np.ndarray, n_components: int) -> np.ndarray:
    """Classical multidimensional scaling on a distance matrix.

    B = -0.5 * H * D^2 * H with H = I - 1/n; eigendecompose and take
    U * sqrt(lambda). Shared in spirit with the clustering module's copy,
    kept separate because that one embeds for k-means and this one is the
    result being reported.
    """
    # Clustering's, which centres in place and finds only the leading
    # eigenpairs: the centring matrix written out, its two products and a
    # full eigendecomposition made five more matrices. Negative eigenvalues
    # (pairwise RMSD need not be exactly Euclidean) are clipped there too.
    from fastmdxplora.analysis.cluster import _classical_mds as in_place

    return in_place(np.asarray(distances, dtype=np.float64), n_components)
