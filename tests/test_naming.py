"""One rule for the name of a study's output folder."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from fastmdxplora.naming import default_output_name, system_of, system_slug

WHEN = datetime(2026, 9, 19, 2, 20, 7, tzinfo=timezone.utc)


def test_the_name_says_the_system_and_that_it_is_a_study():
    assert default_output_name("1UAO", when=WHEN) == "fastmdxplora_1UAO_study_20260919022007"


def test_no_system_is_still_a_timestamped_study():
    # Never a fixed name two runs could collide on.
    assert default_output_name(None, when=WHEN) == "fastmdxplora_study_20260919022007"
    assert default_output_name("", when=WHEN) == "fastmdxplora_study_20260919022007"


def test_a_file_is_named_by_its_stem_and_made_shell_safe():
    assert system_slug("/data/my protein v2.pdb") == "my-protein-v2"
    assert system_slug("Trp cage (1L2Y)") == "Trp-cage-1L2Y"
    assert system_slug("C:\\runs\\lys.cif") == "lys"


def test_the_slug_is_bounded():
    assert len(system_slug("x" * 200)) == 40


def test_system_of_reads_the_first_system_preferring_its_id():
    assert system_of({"systems": [{"id": "chignolin", "system": "1UAO"}]}) == "chignolin"
    assert system_of({"systems": [{"system": "1UAO"}]}) == "1UAO"
    assert system_of({"system": "1L2Y"}) == "1L2Y"
    assert system_of({}) is None
    assert system_of(None) is None


def test_the_timestamp_is_utc_and_sortable():
    name = default_output_name("x")
    assert re.fullmatch(r"fastmdxplora_x_study_\d{14}", name)


def test_no_other_copy_of_the_rule_survives():
    # Six copies in Python and one in JavaScript, two with no timestamp,
    # were how a second run could land in the first run's folder.
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "fastmdxplora"
    offenders = []
    for path in src.rglob("*.py"):
        if path.name == "naming.py":
            continue
        text = path.read_text(encoding="utf-8")
        if 'f"fastmdxplora_output_' in text or '"fastmdxplora_output"' in text:
            offenders.append(path.name)
    for path in src.rglob("*.js"):
        if '"fastmdxplora_output_"' in path.read_text(encoding="utf-8"):
            offenders.append(path.name)
    assert offenders == [], offenders
