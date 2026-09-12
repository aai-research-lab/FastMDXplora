"""A fifth reader of the schema, and it must not be a fifth truth.

The GUI form, the CLI flags, the Python API and validation are generated
from one declaration, so none of them can drift. This adds a description
for something that has not read the docs -- a language model asked to
write a config -- and it earns the same guarantee only if it is generated
from that same declaration and never hand-maintained beside it.

So what is asserted here is not that the description reads well. It is
that the description and validation cannot disagree: every setting named
is one validation accepts, every setting validation accepts is named, and
every enumerated value offered is one the schema permits.

That property is what makes the natural language interface safe to have.
The standing failure of such interfaces since the seventies is the
plausible query -- well formed, semantically wrong, silently answered --
and the fix has never been a cleverer parser. It is a target language
narrow enough to refuse. A description that could name a setting
validation rejects would put the hole straight back.
"""

from __future__ import annotations

import json
import unittest

from fastmdxplora.config.describe import (
    describe_field,
    describe_schema,
    schema_as_json,
)
from fastmdxplora.config.loader import ConfigError, validate_config
from fastmdxplora.config.schema import PHASE_SCHEMAS, TOP_LEVEL
from fastmdxplora.refusals import refusal_of


class TestTheDescriptionMatchesWhatValidationAccepts(unittest.TestCase):

    def test_every_described_setting_is_accepted(self):
        # The direction that matters most. A description naming something
        # validation refuses sends a caller to write a study that cannot
        # run, and the refusal it gets will look like the software's fault.
        document = schema_as_json()
        for phase, block in document.items():
            if phase == "top_level":
                continue
            for name, entry in block.items():
                with self.subTest(phase=phase, setting=name):
                    value = _a_legal_value(entry)
                    if value is _NO_VALUE:
                        continue
                    try:
                        validate_config({phase: {name: value}})
                    except ConfigError as exc:
                        refusal = refusal_of(exc)
                        # A companion requirement is a legitimate refusal of
                        # a setting that nonetheless exists. An unknown
                        # option is not.
                        self.assertNotEqual(
                            refusal.code, "config.option.unknown",
                            f"{phase}.{name} is described and not accepted",
                        )

    def test_every_accepted_setting_is_described(self):
        # The other direction. A setting validation accepts and the
        # description omits is one a caller cannot discover, which quietly
        # makes part of the software unreachable from this interface.
        document = schema_as_json()
        for phase, schema in PHASE_SCHEMAS.items():
            described = set(document.get(phase, {}))
            declared = {field.name for field in schema.fields}
            with self.subTest(phase=phase):
                self.assertEqual(
                    declared - described, set(),
                    f"{phase}: declared and not described",
                )

    def test_every_offered_value_is_permitted(self):
        # An enum in the description that the loader would refuse is the
        # worst of the three: it reads as authoritative and it is wrong
        # about the one thing a caller would not think to check.
        for phase, block in schema_as_json().items():
            if phase == "top_level":
                continue
            for name, entry in block.items():
                for value in entry.get("enum", []):
                    with self.subTest(phase=phase, setting=name, value=value):
                        try:
                            validate_config({phase: {name: value}})
                        except ConfigError as exc:
                            self.assertNotEqual(
                                refusal_of(exc).code,
                                "config.option.not_permitted",
                                f"{phase}.{name} offers {value!r} and refuses it",
                            )

    def test_the_defaults_shown_are_the_defaults_declared(self):
        document = schema_as_json()
        for phase, schema in PHASE_SCHEMAS.items():
            block = document.get(phase, {})
            for field in schema.fields:
                if field.default is None:
                    continue
                with self.subTest(phase=phase, setting=field.name):
                    self.assertEqual(
                        block[field.name].get("default"), field.default)


class TestTheDescriptionIsUsable(unittest.TestCase):

    def test_it_carries_the_help_that_explains_the_refusals(self):
        # The help is the most valuable thing in the registry here. A model
        # told that setup refuses rather than embedding a protein sideways
        # will not propose a membrane study without the orientation check;
        # one given only the field's name will, and then be refused.
        text = describe_schema(phases=["setup"])
        self.assertIn("membrane_orientation_checked", text)
        self.assertIn("xy plane", text)

    def test_terse_is_much_shorter_and_still_complete(self):
        full = describe_schema()
        terse = describe_schema(verbose=False)
        self.assertLess(len(terse), len(full) / 2)
        for phase, schema in PHASE_SCHEMAS.items():
            for field in schema.fields:
                with self.subTest(setting=field.name):
                    self.assertIn(field.name, terse)

    def test_a_caller_can_ask_for_one_phase(self):
        only_setup = describe_schema(phases=["setup"])
        self.assertIn("## setup", only_setup)
        self.assertNotIn("## analysis", only_setup)

    def test_the_json_form_is_serialisable(self):
        json.dumps(schema_as_json())

    def test_a_field_line_names_its_choices(self):
        field = next(f for f in PHASE_SCHEMAS["setup"].fields
                     if f.name == "box_shape")
        line = describe_field(field)
        for choice in field.choices:
            self.assertIn(str(choice), line)


#: Sentinel for a setting no legal value can be guessed for.
_NO_VALUE = object()


def _a_legal_value(entry: dict):
    """Something the schema should accept for this setting.

    Only used to check that a described setting is not refused as unknown,
    so it need only be of the right type. A setting whose type gives no
    obvious specimen is skipped rather than guessed at.
    """
    if "enum" in entry and entry["enum"]:
        return entry["enum"][0]
    if "default" in entry:
        return entry["default"]
    return {
        "true/false": True,
        "whole number": 1,
        "number": 1.0,
        "text": "x",
        "list": [],
        "mapping": {},
    }.get(entry.get("type"), _NO_VALUE)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
