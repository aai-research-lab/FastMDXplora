"""Configuration file support for FastMDXplora.

A single config can capture an entire study — or a whole campaign of
many systems and a parameter sweep. The canonical input is a
``systems:`` list (always a list, even for one system). The same file
drives the CLI and the Python API:

  CLI:    fastmdx explore --config study.yml
  Python: BatchExplorer(config="study.yml").run()

Public API
----------
- :func:`validate_config` -- strict schema validation (raises ConfigError)
- :func:`load_config_file` -- parse a YAML config to a dict
- :func:`phase_options` -- extract per-phase option blocks
- :func:`generate_template` -- the ``fastmdx init-config`` template
- :func:`write_resolved_config` -- the reproducibility dump
- :class:`ConfigError` -- raised on any config problem
- :data:`PHASE_SCHEMAS` -- the schema registry (single source of truth)
"""

from fastmdxplora.config.generate import generate_template, write_resolved_config
from fastmdxplora.config.agent_modes import (
    AgentModes,
    resolve_agent_modes,
    unchecked_phases,
)
from fastmdxplora.config.describe import (
    describe_field,
    describe_schema,
    schema_as_json,
)
from fastmdxplora.config.loader import (
    ConfigError,
    STUDY_LEVEL_KEYS,
    load_config_file,
    phase_options,
    study_options,
    validate_config,
)
from fastmdxplora.config.schema import PHASE_SCHEMAS, PhaseSchema

__all__ = [
    "AgentModes",
    "resolve_agent_modes",
    "unchecked_phases",
    "describe_schema",
    "describe_field",
    "schema_as_json",
    "ConfigError",
    "PHASE_SCHEMAS",
    "PhaseSchema",
    "generate_template",
    "load_config_file",
    "phase_options",
    "STUDY_LEVEL_KEYS",
    "study_options",
    "validate_config",
    "write_resolved_config",
]
