"""The config-language guide describes every key a study may have.

An AI app given `fastmdxplora://guide/config-language` was told "a key not
listed here is refused", and `systems` and `sweep` were not listed: the
guide is generated from the schema's fields, and those two have a shape of
their own and are not fields. Writing from the guide alone, it wrote
`system:`, which is refused, and had no way to write a study that runs.
Found running `fastmdx mcp` from an AI app (2026-10-01).
"""

from __future__ import annotations

import pytest

from fastmdxplora.config.describe import describe_schema
from fastmdxplora.config.schema import BATCH_KEY_HELP, TOP_LEVEL_KEYS


def _guide() -> str:
    from fastmdxplora.mcp.content import _guide_config

    return _guide_config()


def test_every_key_of_a_study_is_in_the_guide():
    guide = _guide()
    missing = [key for key in sorted(TOP_LEVEL_KEYS - {"execution"})
               if f"- {key} " not in guide and f"- {key}\n" not in guide
               and f"## {key}" not in guide]
    assert missing == []


def test_the_agent_reads_the_same_text():
    described = describe_schema()
    for key, said in BATCH_KEY_HELP.items():
        assert " ".join(said.split()) in described, key
    assert "There is no top-level `system` key" in described


def test_a_study_written_from_the_guide_is_accepted():
    from fastmdxplora.config.loader import validate_config

    guide = _guide()
    assert "`systems: [{system: 1L2Y, id: trpcage}]`" in guide
    validate_config({"systems": [{"system": "1L2Y", "id": "trpcage"}],
                     "sweep": {"simulation.temperature_K": [300, 310]},
                     "simulation": {"duration_ns": 1}}, require_systems=True)


def test_the_key_the_guide_says_is_not_there_is_refused():
    from fastmdxplora.config.loader import validate_config

    with pytest.raises(Exception) as refused:
        validate_config({"system": "1L2Y"})
    assert getattr(refused.value, "code", "") == "config.option.unknown"
