# Molecular dynamics feature campaign

This campaign tracks the sixteen gaps identified against established
simulation, analysis and molecular-viewing tools. An implemented feature is
not automatically independently validated. Each needs matched inputs,
settings and comparisons before numerical agreement can be claimed.

| Feature | Current implementation | Outstanding validation or scope |
|---|---|---|
| Diffusion | `diffusion`: periodic paths, MSD and diffusion coefficient | Compare matched frames and selections with an established MSD tool |
| Dynamic cross-correlation | `cross_correlation` | Compare the same alignment and atom choices with cpptraj or Bio3D |
| Helix geometry | `helix_geometry` | Independent comparison of tilt and local axes |
| MM/GBSA | `mmgbsa`, using the prepared force field | Independent endpoint-energy comparison; this is not a binding free energy |
| Residue networks | `residue_network` | Compare a matched contact network; a path alone establishes no signalling mechanism |
| Experimental observables | `nmr_observables`: J-couplings and NOE averages | SAXS and chemical shifts remain outside the implementation |
| Markov models | `markov_model`: tICA, microstates and transition estimates | Independent comparison, Chapman–Kolmogorov tests, coarse graining and Bayesian uncertainty |
| Nucleic acid geometry | `nucleic_acid` | Compare matched base pairing and reference frames with 3DNA |
| Water thermodynamics | `gist` | Independent grid comparison and sampling/convergence checks |
| Publication pictures | Viewer traced lighting | GPU portability and browser compatibility; the original manual check is not an automated test |
| Replica exchange | REST2 and temperature exchange; `replica_mixing` | Representative CPU/GPU systems, observable convergence and independent comparison |
| Alchemical free energies | Pending | Define supported transformations, endpoint/charge corrections and reference benchmarks before implementation |
| Machine-learned potentials | Pending | Define supported models/elements, ML region boundaries and model provenance before implementation |
| Mixed/asymmetric membranes | Pending | Builder integration, leaflet composition checks and pressure-coupling validation |
| Martini coarse graining | Pending | Mapping, topology/force-field provenance and independent coarse-grained benchmarks |
| Constant-pH sampling | Pending | Specify the protonation model, detailed-balance checks and titration benchmarks |

The implementation checks are in `tests/`, including
`test_replicas_cross_what_one_copy_cannot.py` and
`test_replica_exchange_keeps_its_ensemble_and_limits.py`. Synthetic checks
and short integration runs establish specific software behaviours; they do
not establish force-field accuracy or production-scale convergence.

## Next validation stage

Start with BPTI/6PTI and record the exact prepared system, selection, force
field, constraints, timestep, ladder, seeds and software versions. Compare
REST2 at scale one with the original Hamiltonian; compare equilibrium
observables with independent ordinary runs. Inspect acceptance, round trips
and observable convergence separately. A high swap acceptance rate does not
by itself show useful sampling.

Before launching on aailab01, inspect the live hardware, scheduler, storage
and environment. Run a short pilot to measure throughput and peak memory,
then choose replica count and production length from those measurements.
The cost counter includes all replicas and their sampler equilibration;
its ordinary-MD calibration does not measure exchange, cross-energy,
minimisation or storage overhead. No GPU validation or aailab01 throughput
is claimed by these laptop checks.

The remaining simulation features should each have an explicit supported
scope, a small verified implementation and an independent numerical
benchmark. They remain pending until those checks exist.
