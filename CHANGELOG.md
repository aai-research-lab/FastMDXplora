# Changelog
All notable changes to this project will be documented in this file.

This format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]
### Added
- **Fraction of Native Contacts (Q-Value) Analysis**: New `qvalue` module implementing the Best-Hummer-Eaton Q metric for measuring protein folding state and native structure retention.
  - User-configurable parameters: `reference_frame`, `beta_const`, `lambda_const`, `native_cutoff`
  - Automatic native contact identification and metadata reporting
  - Integration with multi-analysis orchestrator and slide generation
  - Full CLI support with `fastmda qvalue` command
- New features since `v1.0.0`.
### Changed
- Behavior/CLI/API updates.
### Fixed
- Bug fixes and documentation corrections.


## [1.1.1] - 2026-09-14
### Changed
- **Superseded by [FastMDXplora](https://pypi.org/project/fastmdxplora/).** This
  is the final release of `fastmdanalysis`. The package description, README and
  `fastmda --help` now name the successor, and importing the package raises a
  `DeprecationWarning`.
- No analysis code changed. Results from 1.1.1 are identical to 1.1.0, and
  1.1.0 remains installable for reproducing published work.


## [1.0.0] - <2025-11-02>
### Added
- **First stable release of FastMDAnalysis**
- Version header in log files.

## [0.0.3] - <2025-10-27>
### Added
- **First public release of FastMDAnalysis** (non-testing).
- Command-line workflow: `fastmda analyze --traj <traj> --top <top> --include rmsd rg cluster
- Core analyses: RMSD, Rg, RMSF, hydrogen bonds, secondary structure, SASA, clustering, dimensionality reduction.
- Publication-quality figure generation. Automatic slide generation.
- Initial documentation/README.
- Packaging and metadata for citation (`CITATION.cff`) and licensing.

---

[Unreleased]: https://github.com/aai-research-lab/FastMDAnalysis/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/aai-research-lab/FastMDAnalysis/compare/v1.0.0
[0.0.3]: https://github.com/aai-research-lab/FastMDAnalysis/releases/tag/v0.0.3
