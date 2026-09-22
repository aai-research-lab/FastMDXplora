# Contributing to FastMDXplora

Thank you for your interest in contributing to FastMDXplora. We welcome
contributions of all kinds — bug reports, feature requests, documentation
improvements, and code.

## Getting started

```bash
# Clone the repository
git clone https://github.com/aai-research-lab/FastMDXplora.git
cd FastMDXplora

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate

# Install in editable mode with development dependencies
pip install -e ".[dev]"

# Verify the install
fastmdx --version
fastmdx info

# Verify it is *this* install: the path should be inside the checkout
python -c "import fastmdxplora; print(fastmdxplora.__file__)"

pytest
```

### The browser tests

The tests that check the GUI page itself, such as the composer's layout and
the study frame, drive a real browser through Playwright and skip when it is
not installed. To run them locally:

```bash
pip install playwright
python -m playwright install chromium
```

CI runs them on one job, Ubuntu with Python 3.11.

### If you install a released version into the same environment

Don't, unless you mean to. `pip install -U fastmdxplora` replaces an editable
install with an ordinary one, reports success, and says nothing about what it
displaced. Every `pytest` after that imports from `site-packages`, so the code
in your tree is no longer the code under test.

The symptom is not one you would connect to the cause. Tests fail with
`AttributeError` on methods that are visibly present in the file you are
reading, and the search goes to the code, which is fine. An hour has gone into
this once already.

`tests/test_the_tests_run_against_this_checkout.py` now fails first and says
so. To get back:

```bash
pip install -e ".[dev]"
```

To compare against a release without disturbing the checkout, use a separate
environment for it.

## Development workflow

1. Open an issue describing the change you'd like to make (skip this for
   trivial fixes such as typos).
2. Fork the repository and create a topic branch from `main`.
3. Make your changes. Write or update tests under `tests/`.
4. Run the test suite locally (`pytest`).
5. Run the linter. CI gates on the rules that find bugs:

   ```bash
   ruff check src tests --select F,B     # must pass; this is what CI runs
   ruff check src tests                  # everything, including the backlog
   ```

   The second reports a further 378 findings of import order and line
   length. Clearing them is welcome and is a commit of its own; please do not
   fold it into a change about something else, because a 138-file mechanical
   diff hides whatever it is carrying.
6. If you touched setup, chemistry or a guardrail, run the corpus that
   actually builds molecules:

   ```bash
   pytest -m network tests/validation
   ```

   `pytest.ini` deselects these by default because they fetch from RCSB and
   run real dynamics, so an ordinary `pytest` never touches them. They run
   nightly in CI and can be triggered by hand from the Actions tab. They are
   also where this project's defects have historically been found: the note
   at the top of `tests/validation/structures.py` records that every one
   found on 2026-08-07 came from running a real structure while the suite
   stayed green at 1,900 tests.
7. Open a pull request against `main`.

## Coding conventions

- **Python ≥ 3.10.** Use modern type hints and the standard library where possible.
- **`src/` layout.** All package code lives under `src/fastmdxplora/`.
- **Docstrings.** Public functions and classes get NumPy-style docstrings.
- **Tests required for new functionality.** Smoke tests at minimum; full
  numerical/equivalence tests for any analytical code migrated from
  FastMDXplora version 1.
- **Lazy imports for heavy optional dependencies** (OpenMM, PDBFixer,
  python-pptx, etc.).
- **Consistent output structure.** Every phase writes its outputs to
  `output_dir/<phase>/`, including a `*_parameters.json` manifest.

## Smoke campaigns before a release

A smoke campaign runs FastMDXplora over many PDB structures at once, short, to
find what breaks. Real structures are more varied than any test suite: unusual
residues, missing density, exotic cofactors, chain breaks in awkward places.
Running fifty of them for ten picoseconds each finds failures that fifty
unit tests will not.

This is for hardening a release, not for ordinary CI.

```bash
python scripts/run_pdb_smoke_campaign.py \
  --output-root runs/campaign \
  1UBQ 1L2Y 4LYT 181L 1AFO
```

---

## Choosing what to run

Positional arguments are PDB identifiers or paths to local `.pdb` / `.cif`
files. For anything more than a handful, keep them in a file:

```bash
python scripts/run_pdb_smoke_campaign.py \
  --output-root runs/campaign \
  --input-list examples/pdb_list.txt
```

One entry per line; `#` starts a comment.

A good list mixes the easy and the awkward: a small soluble protein, one with
a ligand, one with a metal, one with a nucleic acid, one with missing loops,
one that is large. The point is coverage of what breaks, not of what works.

---

## Keeping it short and bounded

The defaults are already short. The flags that matter are the ones that stop a
single structure eating the campaign:

| | |
|---|---|
| `--nvt-steps`, `--npt-steps`, `--production-steps` | shorter still |
| `--max-input-mb` | skip a structure whose file is larger |
| `--max-setup-atoms` | skip one that solvates to more atoms than this |
| `--platform` | force CPU or CUDA |
| `--no-report` | skip the reporting phase |

The two limits are what keep a campaign finishing overnight. A ribosome will
otherwise consume the time budget for everything after it.

---

## Letting it finish

```bash
python scripts/run_pdb_smoke_campaign.py \
  --output-root runs/campaign \
  --input-list examples/pdb_list.txt \
  --continue-on-error
```

`--continue-on-error` is the point of a campaign: one structure failing is a
result, not a reason to stop. `--stop-on-error` does the opposite when you are
chasing one specific failure.

---

## Reading the results

Two summaries are written to the output root:

- **`campaign_summary.csv`** — one row per structure, for sorting and
  filtering
- **`campaign_summary.json`** — the same with the detail

Each structure gets one of these:

| | |
|---|---|
| `ok` | ran through |
| `expected_limitation` | refused something it should refuse — a structure the software correctly declines |
| `validation_failed` | produced output that did not pass its own checks |
| `failed` | a phase raised |
| `skipped` | over a size limit |
| `error` | the campaign itself had a problem |

Each run's own directory is beside the summaries, so a failure can be opened
and read like any other run.

---

## What counts as a bug

**`expected_limitation` is not a bug.** A structure with an unparameterisable
cofactor, a charge that cannot be settled, or geometry the force field has no
templates for *should* be refused. A campaign with several of these is
working.

**`validation_failed` usually is.** It means a phase produced output and the
output was wrong, which is worse than refusing.

**`failed` needs reading.** A phase raising with a clear message naming what it
could not do is close to `expected_limitation`. A phase raising with a
traceback from inside a library is a bug, and the message is where to start.

The distinction is the whole reason the statuses are separate: software that
refuses cleanly and software that breaks look the same in a pass/fail count.

---

## Reporting issues

Please include:

- The output of `fastmdx info`
- The command line you ran (or the Python code snippet)
- The full error message and traceback
- A minimal reproducing example if possible

## Code of conduct

By participating in this project, you agree to abide by our
[Code of Conduct](CODE_OF_CONDUCT.md).

## License

By contributing, you agree that your contributions will be licensed under
the project's [MIT License](LICENSE).
