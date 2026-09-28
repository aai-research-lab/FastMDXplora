"""Records, help and docs that said something the code does not.

- `MIN_PYTHON` said 3.9 while the package requires 3.10.
- The `chains` help said the default is every chain; setup takes the
  biological assembly the file declares.
- The resume figure quoted 8e-8 nm where the code and its test measure about
  1e-9 at the same thread count.
- The config language given to a model showed no ranges, so a pH of 25 was
  proposed and refused; no section said what it was for; and `execution`
  could not be asked for at all.
"""

from __future__ import annotations

from pathlib import Path

from tests._toml import load_toml

ROOT = Path(__file__).resolve().parents[1]


def test_the_python_floor_is_the_one_the_package_declares() -> None:
    from fastmdxplora import MIN_PYTHON, python_range_string

    declared = load_toml(ROOT / "pyproject.toml")["project"]["requires-python"]
    floor = declared.split(",")[0].strip().lstrip(">=")
    assert ".".join(map(str, MIN_PYTHON)) == floor
    assert python_range_string().startswith(f"Python {floor}")


def test_the_chains_help_names_the_assembly() -> None:
    from fastmdxplora.config.schema import PHASE_SCHEMAS

    said = PHASE_SCHEMAS["setup"].get("chains").help
    assert "every chain in the structure" not in said
    assert "biological assembly" in said


def test_the_resume_figure_is_the_measured_one() -> None:
    from fastmdxplora.validation import corpus

    for text in ((ROOT / "docs" / "production.md").read_text(encoding="utf-8"),
                 Path(corpus.__file__).read_text(encoding="utf-8")):
        assert "8e-8" not in text and "8×10⁻⁸" not in text


class TestTheLanguageAModelIsShown:

    def test_a_range_is_shown_with_the_setting(self) -> None:
        from fastmdxplora.config.describe import describe_schema

        [ph] = [line for line in describe_schema(phases=["setup"]).splitlines()
                if line.startswith("  - ph ")]
        assert "from 0 to 14" in ph

    def test_and_given_as_data(self) -> None:
        from fastmdxplora.config.describe import schema_as_json

        ph = schema_as_json(phases=["setup"])["setup"]["ph"]
        assert (ph["minimum"], ph["maximum"]) == (0.0, 14.0)

    def test_each_section_says_what_it_is_for(self) -> None:
        from fastmdxplora.config.describe import describe_schema
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        text = describe_schema(phases=["setup"])
        wanted = " ".join(PHASE_SCHEMAS["setup"].description.split())
        assert f"## setup\n{wanted}" in text

    def test_execution_is_described_when_asked_for_and_not_otherwise(self) -> None:
        from fastmdxplora.config.describe import describe_schema, schema_as_json

        assert "## execution" not in describe_schema()
        asked = describe_schema(phases=["execution"])
        assert "## execution" in asked and "  - workers " in asked
        assert "devices" in schema_as_json(phases=["execution"])["execution"]
