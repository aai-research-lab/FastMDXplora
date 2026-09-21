"""One study, three languages: a config file, a command, a script.

The GUI describes a study and hands back a config. But the same study has two
other languages the software already speaks -- the command line and the
Python API -- and somebody who decided everything in the form should not have
to translate by hand into either. Hand translation is where a flag gets the
wrong prefix or a block the wrong name, and the run that follows is not the
study the form described.

Both renderers here are derived rather than written:

**The command consults the live parser.** The CLI's flags come from the
schema plus hand-written exceptions (``analysis.include`` is
``--analyze-analyses``, because ``--analyze-include`` would collide with the
phase selector). Re-deriving those rules here would mean maintaining them
twice and disagreeing eventually; instead the parser is asked which option
string owns each setting, so the exceptions are honoured automatically and a
new schema field is spelled correctly the day it exists. The proof is a
round trip: the rendered command, parsed by the real parser, reproduces the
config it was rendered from.

**The script is the documented API, filled in.** ``FastMDXplora(system=...,
output_dir=..., options={...}).explore()`` -- the same blocks the config
holds, in the shape ``docs/api.md`` promises.
"""

from __future__ import annotations

import shlex
from typing import Any
from fastmdxplora.refusals import CodedError

__all__ = ["cli_command", "python_script", "UntranslatableSetting"]


#: Config blocks are nouns; the CLI's phase prefixes are verbs.
_BLOCK_TO_VERB = {
    "setup": "setup",
    "simulation": "simulate",
    "analysis": "analyze",
    "report": "report",
}


class UntranslatableSetting(CodedError, ValueError):
    """A setting the command line cannot say.

    Raised rather than silently dropped: a command that omits a decided
    setting runs a different study from the one the form described, and
    looks identical doing it. The config file is the language everything
    translates from, so it is always the fallback.
    """

    default_code = "config.untranslatable"


def _settled_defaults() -> dict[str, dict[str, Any]]:
    """Per phase, the value each setting takes when nothing asks for one.

    The schema's own defaults put through the loader's normalisation,
    because normalisation is part of what "nothing asks for one" means.
    `setup.ligand_resname` declares no default and is nonetheless "LIG" in
    every config that does not set it, mirrored from `ligand_name`, which
    does declare one -- so comparing against the raw schema default says a
    value was chosen when it was only settled.

    It reads as a detail until a resolved config, which names every
    setting, is translated: the command then carries
    `--setup-ligand-resname LIG` for a protein with no ligand in it.
    """
    global _SETTLED
    if _SETTLED is None:
        from fastmdxplora.config.loader import normalise_config
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        bare = {phase: schema.defaults()
                for phase, schema in PHASE_SCHEMAS.items()}
        settled = normalise_config(bare)
        _SETTLED = {phase: dict(settled.get(phase) or {}) for phase in bare}
    return _SETTLED


#: Filled on first use; the schema does not change within a process.
_SETTLED: dict[str, dict[str, Any]] | None = None


def _worth_saying(phase: str, name: str, value: Any) -> bool:
    """Whether a setting needs stating, or the run would take it anyway.

    What lets a *full* config -- which restates every default on purpose,
    so the file is complete -- translate to a short command and a readable
    script rather than to every setting there is.
    """
    if value is None:
        return False
    settled = _settled_defaults().get(phase) or {}
    return not (name in settled and value == settled[name])


def _explore_options() -> dict[str, tuple[str, Any]]:
    """dest -> (canonical option string, the action), from the real parser."""
    from fastmdxplora.cli.main import _build_parser

    explore = _build_parser()._subparsers._group_actions[0].choices["explore"]
    table: dict[str, tuple[str, Any]] = {}
    for action in explore._actions:
        if not action.option_strings:
            continue
        # The last spelling is the long one (`-s`, `-system`, `--system`).
        table[action.dest] = (action.option_strings[-1], action)
    return table


def _flag_for(options, dest: str, value: Any) -> tuple[str, Any]:
    """The option string that sets `dest` to `value`, honouring negations.

    A zero-argument flag can only say its own constant, so False under a
    ``--setup-x`` needs its ``--setup-no-x`` twin; where no spelling exists
    the setting is untranslatable and this says so rather than dropping it.
    """
    if dest in options:
        flag, action = options[dest]
        if action.nargs == 0:
            if value == action.const:
                return flag, None
            # The other constant lives on the twin flag, found by dest+value.
            for other_flag, other in options.values():
                if other.dest == dest and other.nargs == 0 and other.const == value:
                    return other_flag, None
            raise UntranslatableSetting(
                f"The command line has no flag that sets {dest.replace('__', '.')} "
                f"to {value!r}; keep this study as a config file."
            )
        return flag, value
    raise UntranslatableSetting(
        f"The command line has no flag for {dest.replace('__', '.')}; "
        "keep this study as a config file."
    )


def _rendered(value: Any) -> list[str]:
    if isinstance(value, bool):
        # Never reached for nargs=0 flags; a valued flag taking a bool takes
        # the words the config file uses.
        return ["true" if value else "false"]
    if isinstance(value, (list, tuple)):
        return [shlex.quote(str(item)) for item in value]
    return [shlex.quote(str(value))]


def cli_command(config: dict[str, Any]) -> str:
    """The `fastmdx explore` invocation that runs this config.

    Raises :class:`UntranslatableSetting` for a config the command line cannot
    express, naming the setting -- the config file is the fallback spelling,
    and it always works.
    """
    # The dict arrives from a caller rather than through the loader, so a
    # study written with the earlier `include` is settled before the
    # command or the script is spelled out of it.
    from fastmdxplora.config.loader import canonical_phase_keys

    config = canonical_phase_keys(dict(config))
    options = _explore_options()
    parts: list[str] = ["fastmdx", "explore"]

    # A config names its inputs under `systems` so one protein and forty have
    # the same shape; the command line takes exactly one. One entry renders,
    # several do not -- a batch is a config-file study.
    system = config.get("system")
    if system is None:
        entries = config.get("systems") or []
        if len(entries) == 1 and isinstance(entries[0], dict):
            system = entries[0].get("system")
        elif len(entries) > 1:
            raise UntranslatableSetting(
                f"This study names {len(entries)} systems, and the command "
                "line takes one; a batch is a config-file study."
            )
    if system is not None:
        parts += ["--system", shlex.quote(str(system))]
    if config.get("output"):
        parts += ["--output", shlex.quote(str(config["output"]))]
    for key in ("include_phase", "exclude_phase"):
        names = config.get(key)
        flag = "--" + key.replace("_", "-")
        if names:
            parts += [flag, *(shlex.quote(str(n)) for n in names)]

    for block, verb in _BLOCK_TO_VERB.items():
        settings = config.get(block)
        if not isinstance(settings, dict):
            continue
        for name, value in settings.items():
            # A value the run would take anyway needs no saying: the
            # command's own defaults supply it, and saying it would fail on
            # the first default the CLI only knows how to negate.
            if not _worth_saying(block, name, value):
                continue
            flag, rendered_value = _flag_for(
                options, f"{verb}__{name}", value)
            parts.append(flag)
            if rendered_value is not None:
                parts += _rendered(rendered_value)
    return " ".join(parts)


def _literal(value: Any, indent: int = 8) -> str:
    """A Python literal, laid out the way a person would write it."""
    if isinstance(value, dict):
        if not value:
            return "{}"
        pad = " " * indent
        entries = ",\n".join(
            f"{pad}{key!r}: {_literal(item, indent + 4)}"
            for key, item in value.items()
        )
        return "{\n" + entries + ",\n" + " " * (indent - 4) + "}"
    return repr(value)


def python_script(config: dict[str, Any]) -> str:
    """The study as the documented Python API, ``docs/api.md``'s shape.

    Only the settings that were decided. A script is read, and a resolved
    config names every setting the run used -- rendered whole, the same
    study becomes a hundred lines of which four were chosen, and which
    four is the one thing a reader is looking for. The rest are the API's
    own defaults and passing them explicitly says nothing the call does
    not already do.
    """
    # The dict arrives from a caller rather than through the loader, so a
    # study written with the earlier `include` is settled before the
    # command or the script is spelled out of it.
    from fastmdxplora.config.loader import canonical_phase_keys

    config = canonical_phase_keys(dict(config))
    blocks = {}
    for name, settings in config.items():
        if name not in _BLOCK_TO_VERB or not isinstance(settings, dict):
            continue
        decided = {key: value for key, value in settings.items()
                   if _worth_saying(name, key, value)}
        if decided:
            blocks[name] = decided
    system = config.get("system")
    if system is None:
        entries = config.get("systems") or []
        if len(entries) == 1 and isinstance(entries[0], dict):
            system = entries[0].get("system")
    lines = [
        '"""This study, as a script.',
        "",
        "Written by the FastMDXplora GUI. The same settings, in the same",
        "blocks, as the config file -- run whichever language suits the",
        'machine in front of you."""',
        "",
        "import fastmdxplora as fastmdx",
        "",
        "study = fastmdx.FastMDXplora(",
        f"    system={system!r},",
    ]
    if config.get("output"):
        lines.append(f"    output_dir={config['output']!r},")
    if blocks:
        lines.append(f"    options={_literal(blocks)},")
    lines.append(")")
    call = "study.explore("
    arguments = []
    # The config key, the flag and the API's parameter are one name, so
    # the script says what the config says.
    for key in ("include_phase", "exclude_phase"):
        if config.get(key):
            arguments.append(f"{key}={list(config[key])!r}")
    lines += ["", f"runs = {call}{', '.join(arguments)})", ""]
    lines += ["for run in runs:", "    print(run.output_dir)", ""]
    return "\n".join(lines)
