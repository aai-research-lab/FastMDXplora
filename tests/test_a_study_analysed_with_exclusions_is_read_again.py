"""A study analysed with `--exclude-analyses` is read again from its record.

Its `resolved_config.yml` held the analyses left out (`exclude`) and, beside
them, the plan they resolved to (`include`), and the loader refuses the two
together: `fastmdx report`, and `fastmdx analyze --rerun --analyses rg`, on
the same folder stopped with "the settings ... recorded cannot be used". The
record now holds the person's choice alone, the plan being in the analysis
manifest; and a record written with both is read with the plan that ran.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from test_a_phase_command_is_explore_with_one_phase import _analysed, _made  # noqa: E402

from fastmdxplora.analysis.orchestrator import available_analyses  # noqa: E402
from fastmdxplora.cli.main import main  # noqa: E402

#: Everything but the radius of gyration and the RMSD, left out by name.
LEFT_OUT = [name for name in available_analyses() if name not in ("rg", "rmsd")]


def _analysis_block(root: Path) -> dict:
    return yaml.safe_load((root / "resolved_config.yml").read_text())["analysis"]


def test_the_record_holds_what_was_left_out_and_not_the_plan_beside_it(tmp_path):
    root = _analysed(tmp_path / "study")
    assert main(["analyze", "--output", str(root), "--rerun",
                 "--exclude-analyses", *LEFT_OUT]) == 0
    assert _made(root) == ["rmsd", "rg"]
    block = _analysis_block(root)
    assert block["exclude"] == LEFT_OUT and not block.get("include")
    # The plan that ran is in the analysis manifest.
    manifest = json.loads((root / "analysis" / "analysis_manifest.json").read_text())
    assert manifest["resolved"]["include"] == ["rmsd", "rg"]

    # The study is reported, and analysed again with analyses named, from it.
    assert main(["report", "--output", str(root), "--rerun"]) == 0
    assert main(["analyze", "--output", str(root), "--rerun", "--analyses", "rg"]) == 0
    assert _made(root) == ["rg"]
    block = _analysis_block(root)
    assert block["include"] == ["rg"] and not block.get("exclude")


def test_a_record_written_with_both_is_read_with_the_plan_that_ran(tmp_path):
    root = _analysed(tmp_path / "study", analyses=("rmsd", "rg"))
    record = yaml.safe_load((root / "resolved_config.yml").read_text())
    record["analysis"]["include"] = ["rmsd", "rg"]
    record["analysis"]["exclude"] = LEFT_OUT
    (root / "resolved_config.yml").write_text(yaml.safe_dump(record))

    assert main(["report", "--output", str(root), "--rerun"]) == 0
    assert main(["analyze", "--output", str(root), "--rerun"]) == 0
    assert _made(root) == ["rmsd", "rg"]


def test_a_record_written_with_both_is_said_in_the_log(caplog):
    """Read as its plan, a record written with both dropped the person's
    exclusion of the image check on the next run, and nothing said so."""
    import logging

    from fastmdxplora.config.loader import validate_config

    record = {"analysis": {"include": ["rmsd", "rg"], "exclude": ["image_check", "cluster"]}}
    # On the package's logger itself, which may not pass records to the
    # root once the command line owns the console.
    logger = logging.getLogger("fastmdx")
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="fastmdx"):
            validate_config(record, as_record=True)
    finally:
        logger.removeHandler(caplog.handler)
    assert record["analysis"] == {"include": ["rmsd", "rg"]}
    # Once, or twice where the root passes it to the same handler too.
    said = {r.getMessage() for r in caplog.records if r.name == "fastmdx.config"}
    assert said == {"The study's record lists the analyses that ran and those left out "
                    "(image_check, cluster); it is read as the analyses that ran, which the "
                    "image check joins, its exclusion not kept."}
