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
  - **UMAP** (optional): uniform manifold approximation and projection.
    Non-linear, generally faster than t-SNE, also preserves global
    structure better. Requires the optional ``umap-learn`` package.

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

from fastmdxplora.analysis.base import Analysis, AnalysisResult, superposed
from fastmdxplora.analysis.orchestrator import register_analysis
from fastmdxplora.analysis.plotting import new_figure, save_figure
from fastmdxplora.refusals import StudyError
from fastmdxplora.refusals import BackendUnavailable


VALID_METHODS = ("pca", "mds", "tsne", "umap")


class DimRed(Analysis):
    """Dimensionality reduction on the trajectory.

    Parameters
    ----------
    methods : sequence of str, default ``("pca",)``
        Which methods to run. Choices: ``"pca"``, ``"tsne"``, ``"umap"``.
    n_components : int, default 2
        Dimensionality of the embedding. For visualization keep at 2 (or 3).
    perplexity : float, default 30.0
        t-SNE perplexity parameter. Roughly the effective number of
        neighbors each point is balanced against; 5-50 is typical.
    n_neighbors : int, default 15
        UMAP neighborhood size.
    min_dist : float, default 0.1
        UMAP minimum distance between embedded points.
    random_state : int, default 42
        Random seed for stochastic methods (t-SNE, UMAP).
    selection : str, optional
        MDTraj atom selection used to flatten coordinates. Defaults to
        ``"name CA"`` (CA-only is a standard featurization for protein
        DimRed).
    **kwargs
        Standard base-class options.

    Output
    ------
    Per method, in ``<output_dir>/dimred/``:
      - ``dimred_<method>.dat`` — CSV with frame + component columns.
      - ``dimred_<method>.png`` — 2-D scatter colored by frame index.
    """

    name = "dimred"
    description = "Dimensionality reduction"
    default_selection = "name CA"
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
        self.options.update(
            methods=self.methods,
            n_components=self.n_components,
            perplexity=self.perplexity,
            n_neighbors=self.n_neighbors,
            min_dist=self.min_dist,
            random_state=self.random_state,
        )

    def compute(self, traj: md.Trajectory) -> dict[str, np.ndarray]:
        """Run all requested DimRed methods.

        Returns
        -------
        dict
            Maps method name → (n_frames, n_components) embedding array.
        """
        atom_idx = self.select_atoms(traj)

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

        results: dict[str, np.ndarray] = {}
        for method in self.methods:
            if method == "pca":
                model = PCA(n_components=self.n_components)
                embedding = model.fit_transform(coords)
                # Stash variance ratios for the plot annotation
                self._explained_variance = getattr(model, "explained_variance_ratio_", None)
            elif method == "mds":
                # Metric MDS on the pairwise RMSD between frames, which is
                # what version 1 offered and what a reader comparing against
                # it needs. It answers a different question from PCA: PCA
                # finds the directions of largest variance in the
                # coordinates, while this finds an arrangement in which the
                # distances between frames are preserved as closely as
                # possible -- and the distance between two frames is their
                # RMSD, which is what a structural biologist means by how
                # different two conformations are.
                distances = _pairwise_rmsd(traj, self.select_atoms(traj))
                embedding = _classical_mds(distances, self.n_components)
                self._explained_variance = None
            elif method == "tsne":
                # t-SNE requires perplexity < n_samples
                p = min(self.perplexity, max(5.0, traj.n_frames / 4))
                model = TSNE(
                    n_components=self.n_components,
                    perplexity=p,
                    random_state=self.random_state,
                    init="pca",
                    learning_rate="auto",
                )
                embedding = model.fit_transform(coords)
            elif method == "umap":
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
                embedding = model.fit_transform(coords)
            results[method] = np.asarray(embedding, dtype=np.float64)

        return results

    # --------------------------------------------------------------
    # Override run() so each method gets its own output pair.
    # --------------------------------------------------------------
    def run(self, traj: md.Trajectory) -> AnalysisResult:
        from datetime import datetime, timezone

        started = datetime.now(timezone.utc).isoformat()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        options_path = self._write_options_manifest()

        try:
            self.result = self.compute(traj)
            artifacts: list[Path] = [options_path]
            for method, embedding in self.result.items():
                # Data file
                data_path = self.output_dir / f"dimred_{method}.dat"
                cols = {"frame": np.arange(len(embedding))}
                for i in range(embedding.shape[1]):
                    cols[f"component_{i + 1}"] = embedding[:, i]
                pd.DataFrame(cols).to_csv(data_path, index=False)
                artifacts.append(data_path)

                # Figure
                fig_path = self.output_dir / f"dimred_{method}.png"
                fig, ax = new_figure(
                    title=f"{self.figure_title()} ({method.upper()})",
                    figsize=self._user_figsize,
                )
                _plot_dimred_scatter(
                    ax, embedding, method, self._explained_variance if method == "pca" else None
                )
                save_figure(fig, fig_path)
                artifacts.append(fig_path)
                svg_path = fig_path.with_suffix(".svg")
                if svg_path.is_file():
                    artifacts.append(svg_path)

            finished = datetime.now(timezone.utc).isoformat()
            primary = self.methods[0]
            return AnalysisResult(
                name=self.name,
                status="ok",
                data=self.result,
                output_dir=self.output_dir,
                figure_path=self.output_dir / f"dimred_{primary}.png",
                data_path=self.output_dir / f"dimred_{primary}.dat",
                options_path=options_path,
                artifacts=artifacts,
                message=f"{self.name}: ok ({', '.join(self.methods)})",
                started_at=started,
                finished_at=finished,
            )
        except Exception as exc:  # noqa: BLE001
            finished = datetime.now(timezone.utc).isoformat()
            return AnalysisResult(
                name=self.name,
                status="error",
                output_dir=self.output_dir,
                options_path=options_path,
                message=f"{self.name}: {exc}",
                started_at=started,
                finished_at=finished,
            )

    # Required by the ABC; used only when a caller draws onto their own axes.
    def plot(self, result: dict[str, np.ndarray], ax: plt.Axes) -> None:
        primary = next(iter(result))
        _plot_dimred_scatter(
            ax, result[primary], primary,
            self._explained_variance if primary == "pca" else None,
        )

    _explained_variance: np.ndarray | None = None


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
    import mdtraj as md

    n = traj.n_frames
    distances = np.zeros((n, n), dtype=np.float64)
    for index in range(n):
        distances[index] = md.rmsd(traj, traj, index,
                                   atom_indices=atom_indices)
    # md.rmsd is not exactly symmetric to floating point, and an asymmetric
    # matrix would give MDS a slightly complex eigenspectrum.
    return 0.5 * (distances + distances.T)


def _classical_mds(distances: np.ndarray, n_components: int) -> np.ndarray:
    """Classical multidimensional scaling on a distance matrix.

    B = -0.5 * H * D^2 * H with H = I - 1/n; eigendecompose and take
    U * sqrt(lambda). Shared in spirit with the clustering module's copy,
    kept separate because that one embeds for k-means and this one is the
    result being reported.
    """
    squared = np.asarray(distances, dtype=np.float64) ** 2
    n = squared.shape[0]
    centring = np.eye(n) - np.ones((n, n)) / n
    gram = -0.5 * centring @ squared @ centring
    values, vectors = np.linalg.eigh(gram)
    order = np.argsort(values)[::-1][:n_components]
    # Negative eigenvalues mean the distances are not exactly Euclidean,
    # which pairwise RMSD need not be. Clipping is the standard remedy.
    kept = np.clip(values[order], 0.0, None)
    return vectors[:, order] * np.sqrt(kept)
