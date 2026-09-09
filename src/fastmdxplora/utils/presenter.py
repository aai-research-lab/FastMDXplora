"""Structural output layer for FastMDXplora.

While :mod:`fastmdxplora.utils.logging` handles per-record log formatting
(timestamps, levels, icons), this module handles the *structural* output
that organizes a session into a human-readable narrative: the opening
banner box, per-phase headers, the analysis-phase status table, and the
closing total. The two layers are complementary — the logger keeps doing
its job, and the presenter prints its own clean lines on top.

The presenter is **silent in quiet mode**. Quiet mode is enabled by
setting ``FASTMDX_LOG_STYLE=plain`` in the environment, or by passing
``quiet=True`` to :class:`SessionPresenter` at construction. In quiet
mode the structural output disappears entirely and the user sees only
the underlying per-line log records — the right behavior for CI, log
file redirection, and grep workflows.

Design principles:
  - **Zero external dependencies.** ANSI escape codes only. No ``rich``,
    no ``colorama``.
  - **Terminal-width aware.** The banner box auto-fits ``shutil.get_terminal_size``;
    the analysis status table aligns to its widest analysis name.
  - **TTY-aware coloring.** Colors disable automatically when stdout is
    redirected (e.g., to a file) or when ``NO_COLOR`` is set.
  - **No state coupling with the logger.** The presenter writes directly
    to stdout. The file log never receives presenter output, keeping
    ``fastmdxplora.log`` audit-friendly.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from contextlib import contextmanager
from typing import IO, Iterator


# ---------------------------------------------------------------------------
# Color palette — matches the FastMDXplora logging module so the structural
# and per-line layers feel like one tool.
# ---------------------------------------------------------------------------
_C = {
    "reset":   "\x1b[0m",
    "dim":     "\x1b[2m",
    "bold":    "\x1b[1m",
    "blue":    "\x1b[38;5;33m",
    "cyan":    "\x1b[38;5;39m",
    "green":   "\x1b[38;5;76m",
    "yellow":  "\x1b[38;5;214m",
    "red":     "\x1b[38;5;196m",
    "orange":  "\x1b[38;5;208m",
    "purple":  "\x1b[38;5;99m",
    "white":   "\x1b[38;5;255m",
    "navy":    "\x1b[38;5;18m",
    "muted":   "\x1b[38;5;244m",
}


#: Where a study prepares the one system all its windows share. The banner
#: has to know the name because a preparation reports the directory it writes
#: to, which is accurate and useless to watch: it is a second of setup,
#: finished before anybody could type the command, and the windows that follow
#: it are written somewhere else entirely.
SHARED_SETUP_DIRECTORY = "shared_setup"


def _worth_watching(output: str) -> str:
    """The directory somebody watching this run should actually open.

    A run's own directory, except for the shared preparation of an umbrella
    study, where it is the study above: that is where the windows appear, and
    by the time the command is typed the preparation has finished.
    """
    # Trimmed off the text rather than rebuilt from a `Path`. Rebuilding
    # normalises the separators, so a caller who wrote `../runs/study` on
    # Windows was handed back `..\\runs\\study` -- a correct path, and not the
    # one they typed. What is wanted here is the string they will recognise.
    text = str(output)
    trimmed = text.rstrip("/\\")
    # Both separators, whichever platform is running. `os.path.split` on
    # POSIX does not treat a backslash as one, so a Windows path handed to a
    # test on Linux would go unrecognised -- and the reverse was how this was
    # found: the run directory came back with its separators swapped.
    cut = max(trimmed.rfind("/"), trimmed.rfind("\\"))
    if cut <= 0:
        return text
    head, tail = trimmed[:cut], trimmed[cut + 1:]
    if tail == SHARED_SETUP_DIRECTORY and head not in ("", "."):
        return head
    return text


def _ansi_supported(stream: IO) -> bool:
    """Return True iff we should emit ANSI escapes on this stream."""
    if os.getenv("NO_COLOR"):
        return False
    if not hasattr(stream, "isatty"):
        return False
    return bool(stream.isatty())


def _readable_duration(seconds: float) -> str:
    """A duration somebody can act on, rather than a float."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _strip_ansi(s: str) -> str:
    """Remove ANSI escape sequences. Used for width calculations."""
    out: list[str] = []
    i = 0
    while i < len(s):
        if s[i] == "\x1b" and i + 1 < len(s) and s[i + 1] == "[":
            j = s.find("m", i + 2)
            if j == -1:
                break
            i = j + 1
            continue
        out.append(s[i])
        i += 1
    return "".join(out)


#: Whether the banner draws a frame. Kept as a name rather than deleted
#: outright so the intent is visible: the values are the point, the border was
#: decoration, and decoration on the loudest element of the output competes
#: with the run it is describing.
_BOXED = False


def _visual_width(s: str) -> int:
    """Approximate displayed width (strip ANSI; count chars 1-wide).

    A full East-Asian width implementation is overkill for ASCII status
    output; this approximation is correct for the characters we actually
    emit (box-drawing, arrows, status icons, latin-1).
    """
    return len(_strip_ansi(s))


# ---------------------------------------------------------------------------
# SessionPresenter
# ---------------------------------------------------------------------------

def _wrap(text: str, width: int) -> list[str]:
    """Word-wrap ``text`` to ``width``, never splitting a word."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if len(candidate) <= width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _the_useful_part_of(name: str, reason: str | None) -> str:
    """The part of a failure message worth putting in front of somebody.

    Messages arrive as ``"<analysis>: <what went wrong>"``. The analysis
    name is already the first column of the row, so repeating it wastes the
    line. A bare exception class name is worse than nothing -- it occupies
    the space where an explanation should be while saying only that
    something raised -- so it is left out and the log keeps the traceback.
    """
    if not reason:
        return ""
    text = reason.strip()
    prefix = f"{name}:"
    if text.startswith(prefix):
        text = text[len(prefix):].strip()
    if not text or text in {"ok", name}:
        return ""
    # "KeyError" or "IndexError" alone says nothing a reader can act on.
    if text.endswith("Error") and " " not in text:
        return ""
    return text


class SessionPresenter:
    """Print structured session output to stdout.

    A single presenter instance is created by the project-level orchestrator
    at the start of a session and lives for that session. It tracks the
    current phase (for indentation) and records start times so
    :meth:`phase_end` and :meth:`done` can report elapsed durations.

    Parameters
    ----------
    stream : file-like, optional
        Where to write. Defaults to :data:`sys.stdout`.
    quiet : bool, optional
        If True, every method becomes a no-op. If omitted, quiet mode is
        auto-enabled when ``FASTMDX_LOG_STYLE=plain``. Passing
        ``quiet=False`` forces the presenter on even with that env var.
    width : int, optional
        Override the auto-detected terminal width.
    """

    # Status icons — same palette as the logger
    _STATUS_ICON = {
        "ok": ("✓", "green"),
        "error": ("✗", "red"),
        "skipped": ("·", "muted"),
        "warning": ("⚠", "yellow"),
    }

    def __init__(
        self,
        stream: IO | None = None,
        *,
        quiet: bool | None = None,
        explain: bool = True,
        width: int | None = None,
    ) -> None:
        # Held as "the stream I was given, or none" rather than as a
        # resolved object. The presenter is a singleton built the first time
        # anything prints, and `sys.stdout` is not fixed for the life of a
        # process: a caller that redirects it afterwards would otherwise keep
        # having output written to the stream it replaced -- invisible where
        # it went, and a ValueError from this class if that stream has since
        # been closed. Resolved at each write instead, so the console is
        # wherever the console is now.
        self._stream: IO | None = stream
        self._color_override: bool | None = None

        # Auto-detect quiet mode from env when not explicitly set
        if quiet is None:
            quiet = os.getenv("FASTMDX_LOG_STYLE", "").strip().lower() == "plain"
        self.quiet: bool = bool(quiet)
        #: Whether to say why each step happens. On, because a pipeline that
        #: runs silently teaches nothing and somebody's first trajectory
        #: should be one they can defend.
        self.explain: bool = bool(explain)

        # Terminal width: explicit > shutil > 80 fallback
        if width is None:
            try:
                width = shutil.get_terminal_size(fallback=(80, 24)).columns
            except OSError:
                width = 80
        self.width: int = max(40, width)  # clamp absurdly narrow terminals

        self._session_start: float | None = None
        self._phase_start: float | None = None
        self._current_phase: str | None = None
        self._welcome_shown: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    #: The wordmark, drawn rather than filled from a grid.
    #:
    #: This was three bitmap sets in a row -- a mixed-case one, then two
    #: uppercase ones -- each shadowing the last, so the mixed-case set that
    #: was already here had never once been used and the banner printed in
    #: capitals regardless.
    #:
    #: Each glyph is a rectangle: five rows of equal width. Ascenders reach
    #: row 0, x-height letters start at row 1, and `p` descends into row 4, so
    #: the name reads with the capitals it actually carries. A single
    #: character short in any row shifts every letter after it, which is what
    #: the rectangle test guards.
    _GLYPHS: dict[str, tuple[str, ...]] = {
        "F": (" ___ ",
              "| __|",
              "| _| ",
              "|_|  ",
              "     "),
        "a": ("      ",
              " __ _ ",
              "/ _` |",
              "\\__,_|",
              "      "),
        "s": ("     ",
              " ___ ",
              "(_-< ",
              "/__/ ",
              "     "),
        "t": (" _   ",
              "| |_ ",
              "| __|",
              " \\__|",
              "     "),
        "M": (" __  __ ",
              "|  \\/  |",
              "| |\\/| |",
              "|_|  |_|",
              "        "),
        "D": (" ___  ",
              "|   \\ ",
              "| |) |",
              "|___/ ",
              "      "),
        "X": ("__  __",
              "\\ \\/ /",
              " >  < ",
              "/_/\\_\\",
              "      "),
        "p": ("      ",
              " _ __ ",
              "| '_ \\",
              "| .__/",
              "|_|   "),
        "l": (" _  ",
              "| | ",
              "| | ",
              "|_| ",
              "    "),
        "o": ("     ",
              " ___ ",
              "/ _ \\",
              "\\___/",
              "     "),
        "r": ("     ",
              " _ __",
              "| '_/",
              "|_|  ",
              "     "),
    }
    _WORDMARK = "FastMDXplora"
    _TAGLINE = "Fully Automated SysTem for Molecular Dynamics eXploration"

    @property
    def stream(self) -> IO:
        """Where output goes now, not where it went at construction."""
        return self._stream if self._stream is not None else sys.stdout

    @stream.setter
    def stream(self, value: IO) -> None:
        self._stream = value

    @property
    def _color(self) -> bool:
        """Whether to colour, decided against the stream being written to.

        Derived rather than stored, for the same reason the stream is: a
        redirect changes the answer. An explicit assignment still wins, so a
        caller that forces colour on or off keeps that.
        """
        if self._color_override is not None:
            return self._color_override
        return _ansi_supported(self.stream)

    @_color.setter
    def _color(self, value: bool) -> None:
        self._color_override = bool(value)

    def welcome(
        self,
        *,
        dashboard_url: str = "http://127.0.0.1:8765",
        dashboard_enabled: bool = False,
    ) -> None:
        """Print the FastMDXplora startup identity once.

        Deliberately minimal: the wordmark, and the expansion of the name
        justified to span it exactly. Nothing else. Version, backends, and
        citation belong to ``fastmdx info``; the GUI address is printed by
        the server that actually opens the socket.

        ``dashboard_url`` and ``dashboard_enabled`` are accepted for call-site
        compatibility and intentionally unused.
        """
        if self.quiet or self._welcome_shown:
            return
        self._welcome_shown = True

        # Joined without a separator: the glyphs carry their own side bearings,
        # and a space between them opens gaps the letterforms already close.
        logo = [
            "".join(self._GLYPHS[ch][row] for ch in self._WORDMARK).rstrip()
            for row in range(5)
        ]
        block = max(len(row) for row in logo)
        pad = "  "

        self._write("")

        if self.width < block + len(pad) + 1:
            # Too narrow for the wordmark: plain text beats wrapping the
            # glyph rows into noise.
            self._write(self._c(pad + "FastMDXplora", "purple"))
            for line in _wrap(self._TAGLINE, max(20, self.width - len(pad))):
                self._write(pad + line)
            self._write("")
            return

        for row in logo:
            self._write(self._c(pad + row, "purple"))
        self._write("")

        # Spread the words so the expansion spans the wordmark exactly.
        words = self._TAGLINE.split()
        slack = block - sum(len(word) for word in words)
        gaps = len(words) - 1
        if gaps >= 1 and slack >= 0:
            base, extra = divmod(slack, gaps)
            spread = ""
            for index, word in enumerate(words):
                spread += word
                if index < gaps:
                    spread += " " * (base + (1 if index < extra else 0))
        else:
            spread = self._TAGLINE
        self._write(pad + spread)
        self._write("")

    def banner(self, **fields: str) -> None:
        import os as _os
        import sys as _sys
        import time as _time

        if self.quiet:
            return

        self._session_start = _time.monotonic()

        argv = list(_sys.argv[1:])

        def _config_on_the_command_line() -> dict:
            """The config this run was given, if it was given one.

            The banner was built entirely from command-line flags, so a run
            started with --config saw none of them and fell back to defaults:
            it announced a million production steps for a run that only
            analysed a trajectory, and the setup pH of a config that said
            something else. Reading the file is the difference between the
            banner describing the run and describing a run.

            Never raises. A banner is decoration; a run that cannot print one
            should still start.
            """
            import yaml

            for index, item in enumerate(argv):
                path = None
                if item in {"--config", "-c", "-config"} and index + 1 < len(argv):
                    path = argv[index + 1]
                elif item.startswith("--config="):
                    path = item.split("=", 1)[1]
                if not path:
                    continue
                try:
                    data = yaml.safe_load(
                        _os.fspath(path) and open(path, encoding="utf-8").read()
                    )
                except Exception:
                    return {}
                return data if isinstance(data, dict) else {}
            return {}

        from_config = _config_on_the_command_line()

        def configured(phase: str, key: str) -> str:
            """A value the config gives, as text, or empty."""
            block = from_config.get(phase)
            if not isinstance(block, dict):
                return ""
            value = block.get(key)
            return "" if value is None else str(value)

        def argv_list(*names: str) -> list[str]:
            """A multi-value option from the command line, or an empty list.

            `--include` and `--exclude` take several phases, which `arg_value`
            cannot read: it returns the first word after the flag.
            """
            for index, token in enumerate(argv):
                if token not in names:
                    continue
                values = []
                for candidate in argv[index + 1:]:
                    if candidate.startswith("-"):
                        break
                    values.append(candidate)
                return values
            return []

        _PHASE_OF_SUBCOMMAND = {
            "setup": "setup",
            "simulate": "simulation",
            "analyze": "analysis",
            "report": "report",
        }

        def subcommand_phase() -> str:
            """The one phase a phase subcommand runs, or empty for the rest.

            `fastmdx analyze` says nothing through --include, so the "where
            nothing says, show everything" fallback printed a SIMULATION block
            for a command that reads a finished trajectory: a million
            production steps and a frame interval belonging to no run, taken
            from the schema defaults. The subcommand is the most definite
            statement available about which phases happen.
            """
            for token in argv:
                if token.startswith("-"):
                    continue
                return _PHASE_OF_SUBCOMMAND.get(token, "")
            return ""

        def will_run(phase: str) -> bool:
            """Whether this run includes a phase.

            Read from the command line first, then the config. Reading only
            the config, `--include setup` printed a SIMULATION section for a
            run that stops after setup -- announcing 1,750,000 steps that were
            never going to happen.

            A phase block in a config says the phase was configured, not that
            it runs: analysis and report take their defaults and appear in no
            block, so treating a block as inclusion hid two sections of a run
            that did all four. Where nothing says, every section is shown --
            leaving one out is the worse mistake.

            A phase subcommand outranks both: `fastmdx analyze` states which
            phase runs more definitely than any flag, and says nothing through
            --include, so it used to fall to the show-everything fallback.
            """
            only = subcommand_phase()
            if only:
                return phase == only
            included = argv_list("--include") or from_config.get("include")
            if isinstance(included, list) and included:
                return phase in included
            excluded = argv_list("--exclude") or from_config.get("exclude")
            if isinstance(excluded, list) and excluded:
                return phase not in excluded
            return True

        def arg_value(*names: str, default: str = "") -> str:
            """First matching option on the command line, else ``default``.

            Each option is listed under both spellings: `fastmdx explore` uses
            the phase-prefixed form (--setup-ph), while the per-phase commands
            drop the prefix (--ph). Listing only one meant the summary showed
            defaults for every option a per-phase run had actually set.
            """
            for i, token in enumerate(argv):
                for name in names:
                    if token == name and i + 1 < len(argv):
                        return str(argv[i + 1])
                    prefix = name + "="
                    if token.startswith(prefix):
                        return token[len(prefix):]
            return default

        def arg_list(name: str, default: str = "") -> str:
            values = []
            i = 0
            while i < len(argv):
                if argv[i] == name:
                    j = i + 1
                    while j < len(argv) and not argv[j].startswith("--"):
                        values.append(argv[j])
                        j += 1
                    break
                i += 1
            return ", ".join(values) if values else default

        system = (
            str(fields.get("System") or fields.get("system") or "")
            or arg_value("-s", "-system", "--system", default="ANY_PDB_ID")
        )
        output = (
            str(fields.get("Output") or fields.get("output") or "")
            or arg_value("--output", default="output_folder")
        )

        version = str(fields.get("Version") or fields.get("version") or "")
        if not version:
            try:
                from fastmdxplora import __version__ as _fmdx_version
                version = str(_fmdx_version)
            except Exception:
                version = "unknown"

        def field_value(*keys: str, default: object = "") -> str:
            """Return a banner value from explicit fields, falling back to a default."""
            for key in keys:
                value = fields.get(key)
                if value not in (None, ""):
                    return str(value)
            return str(default)

        try:
            from fastmdxplora.setup.pipeline import DEFAULTS as _SETUP_DEFAULTS
        except Exception:
            _SETUP_DEFAULTS = {}

        try:
            from fastmdxplora.simulation.pipeline import DEFAULTS as _SIM_DEFAULTS
        except Exception:
            _SIM_DEFAULTS = {}

        platform = arg_value(
            "--simulate-platform", "--platform",
            default=field_value("Platform", "platform", default=_SIM_DEFAULTS.get("platform", "auto")),
        )
        precision = arg_value(
            "--simulate-precision", "--precision",
            default=field_value("Precision", "precision", default=_SIM_DEFAULTS.get("precision", "mixed")),
        )

        setup_ph = arg_value(
            "--setup-ph", "--ph",
            default=field_value("pH", "ph", "setup_ph", default=_SETUP_DEFAULTS.get("ph", 7.0)),
        )
        ion_conc = arg_value(
            "--setup-ion-concentration-M",
            default=field_value(
                "Ion Conc.",
                "ion_concentration_M",
                "setup_ion_concentration_M",
                default=_SETUP_DEFAULTS.get("ion_concentration_M", 0.15),
            ),
        )
        forcefield = arg_value(
            "--setup-forcefield", "--forcefield",
            default=field_value("Force Field", "forcefield", "setup_forcefield",
                                default=_SETUP_DEFAULTS.get("forcefield", "auto")),
        )
        if str(forcefield).strip().lower() == "auto":
            # "auto" tells the reader nothing about what was simulated. Show
            # what it resolves to, and say that it was chosen rather than named.
            try:
                from fastmdxplora.setup.forcefields import resolve_forcefield

                forcefield = f"{resolve_forcefield('auto').name} (auto)"
            except Exception:  # noqa: BLE001 - the banner must never fail a run
                pass

        timestep = arg_value(
            "--simulate-timestep-fs", "--timestep-fs",
            default=field_value("Timestep", "timestep_fs", "simulate_timestep_fs", default=_SIM_DEFAULTS.get("timestep_fs", 2.0)),
        )
        temperature = arg_value(
            "--simulate-temperature-K",
            default=field_value("Temperature", "temperature_K", "simulate_temperature_K", default=_SIM_DEFAULTS.get("temperature_K", 300.0)),
        )
        friction = arg_value(
            "--simulate-friction-per-ps", "--friction-per-ps",
            default=field_value("Friction", "friction_per_ps", "simulate_friction_per_ps", default=_SIM_DEFAULTS.get("friction_per_ps", 1.0)),
        )

        def maybe_int(value: str) -> int | None:
            if value == "":
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        def maybe_float(value: str) -> float | None:
            if value == "":
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        def format_steps(value: int | None) -> str:
            if value is None:
                return "default"
            return f"{int(value):,}"

        def resolve_stage_display() -> tuple[int | None, int | None, int | None, int | None]:
            """Resolve displayed stage steps from the same defaults as simulation."""
            try:
                from fastmdxplora.simulation.pipeline import DEFAULTS as _SIM_DEFAULTS
                from fastmdxplora.simulation.runner import plan_stages as _plan_stages
            except Exception:
                nvt = maybe_int(arg_value("--simulate-nvt-steps", "--nvt-steps", default=""))
                npt = maybe_int(arg_value("--simulate-npt-steps", "--npt-steps", default=""))
                prod = maybe_int(arg_value("--simulate-production-steps", "--production-steps", default=""))
                total = None if None in (nvt, npt, prod) else int(nvt) + int(npt) + int(prod)
                return nvt, npt, prod, total

            params = dict(_SIM_DEFAULTS)

            # A field the caller passed beats a command-line flag, which beats
            # the default. The caller knows what the run resolved to; argv
            # only knows what somebody typed, and a run driven by a config
            # file typed none of it.
            overrides = {
                "nvt_steps": maybe_int(field_value(
                    "nvt_steps", default=arg_value(
                        "--simulate-nvt-steps", "--nvt-steps", default=""))),
                "npt_steps": maybe_int(field_value(
                    "npt_steps", default=arg_value(
                        "--simulate-npt-steps", "--npt-steps", default=""))),
                "production_steps": maybe_int(field_value(
                    "production_steps", default=arg_value(
                        "--simulate-production-steps", "--production-steps",
                        default=""))),
                "duration_ns": maybe_float(field_value(
                    "duration_ns", default=arg_value(
                        "--simulate-duration-ns", "--duration-ns", default=""))),
                "nvt_duration_ns": maybe_float(field_value(
                    "nvt_duration_ns", default=arg_value(
                        "--simulate-nvt-duration-ns", "--nvt-duration-ns",
                        default=""))),
                "npt_duration_ns": maybe_float(field_value(
                    "npt_duration_ns", default=arg_value(
                        "--simulate-npt-duration-ns", "--npt-duration-ns",
                        default=""))),
                "timestep_fs": maybe_float(timestep),
            }
            params.update({k: v for k, v in overrides.items() if v is not None})
            plan = _plan_stages(
                duration_ns=params.get("duration_ns"),
                timestep_fs=float(params.get("timestep_fs", 2.0)),
                nvt_steps=params.get("nvt_steps"),
                npt_steps=params.get("npt_steps"),
                production_steps=params.get("production_steps"),
                nvt_duration_ns=params.get("nvt_duration_ns"),
                npt_duration_ns=params.get("npt_duration_ns"),
            )
            nvt = int(plan["nvt_steps"])
            npt = int(plan["npt_steps"])
            prod = int(plan["production_steps"])
            return nvt, npt, prod, nvt + npt + prod

        nvt_steps, npt_steps, prod_steps, total_steps = resolve_stage_display()

        def resolve_trajectory_display() -> str:
            # Fields beat the command line, and the command line beats the
            # config -- the same precedence the stage steps above use, and for
            # the same reason: a run driven by a config file typed none of it.
            # Reading argv alone, this printed a computed default while the
            # run used the config's value, so a banner promising a frame every
            # 100 steps introduced a run writing one every 250.
            raw = field_value(
                "trajectory_interval_steps",
                default=arg_value(
                    "--simulate-trajectory-interval-steps",
                    "--trajectory-interval-steps",
                    default=configured("simulation", "trajectory_interval_steps"),
                ),
            )
            explicit = maybe_int(raw)
            if explicit is not None:
                return f"save frame every {explicit:,} production steps"
            try:
                from fastmdxplora.simulation.runner import trajectory_interval_for as _trajectory_interval_for
                interval = _trajectory_interval_for(int(prod_steps or 0))
                return f"save frame every {interval:,} production steps"
            except Exception:
                return "adaptive frame saving during production"

        trajectory_display = resolve_trajectory_display()

        # Resolve the dashboard URL from an explicitly supplied field,
        # an environment variable, or the dashboard CLI flags.
        dashboard_link = str(
            fields.get("Dashboard")
            or fields.get("dashboard_url")
            or _os.environ.get("FASTMDX_DASHBOARD_URL", "")
        )

        dashboard_enabled = (
            "--dashboard" in argv
            or "--live-dashboard" in argv
            or _os.environ.get("FASTMDX_DASHBOARD_ACTIVE") == "1"
        )

        if not dashboard_link:
            dashboard_host = arg_value(
                "--dashboard-host",
                "--host",
                default="127.0.0.1",
            )
            dashboard_port = arg_value(
                "--dashboard-port",
                "--port",
                default="8765",
            )

            # 0.0.0.0 and :: are server bind addresses, not useful browser URLs.
            display_host = (
                "127.0.0.1"
                if dashboard_host in {"0.0.0.0", "::", "[::]"}
                else dashboard_host
            )

            dashboard_link = f"http://{display_host}:{dashboard_port}"
        started = _time.strftime("%Y-%m-%d %H:%M:%S")

        H = chr(0x2500)
        V = chr(0x2502)
        # One box holds every section. It is left-aligned with the startup
        # wordmark rather than centred, so the whole introduction reads as a
        # single left-hand column instead of drifting to the middle of a wide
        # terminal.
        box_width = min(112, max(72, self.width - 24))
        box_width = min(box_width, max(38, self.width - 2))
        content_w = box_width - 4
        box_prefix = "  "

        def fit(text: str, limit: int | None = None) -> str:
            text = str(text)
            available = content_w if limit is None else max(0, limit)
            if _visual_width(text) <= available:
                return text
            if available <= 3:
                return text[:available]
            return text[: max(0, available - 3)] + "..."

        # No border. A box around the settings made the banner the loudest
        # thing on the screen, and it is the least important: what a reader
        # wants from it is the handful of values, not a frame around them.
        def top(color: str) -> None:
            return

        def bottom(color: str) -> None:
            return

        def line(
            text: str = "",
            border: str = "cyan",
            text_color: str | None = None,
        ) -> None:
            raw = fit(text)
            pad = " " * max(0, content_w - _visual_width(raw))
            body = self._c(raw, text_color) if text_color else raw
            if not _BOXED:
                self._write((box_prefix + "  " + body).rstrip())
                return
            self._write(
                box_prefix
                + self._c(V, border)
                + " "
                + body
                + pad
                + " "
                + self._c(V, border)
            )

        def title(text: str, border: str) -> None:
            line(text, border, "white")
            line(H * len(text), border, border)

        def kv(
            label: str,
            value: str,
            border: str,
            *,
            label_color: str | None = None,
            value_color: str = "white",
        ) -> None:
            label_width = min(16, max(8, content_w // 3))
            label_raw = fit(label, label_width).ljust(label_width)
            value_limit = max(0, content_w - label_width - 1)
            value_raw = fit(value, value_limit)
            visible = _visual_width(label_raw) + 1 + _visual_width(value_raw)
            pad = " " * max(0, content_w - visible)
            if not _BOXED:
                self._write(
                    box_prefix
                    + "  "
                    + self._c(label_raw, label_color or border)
                    + " "
                    + self._c(value_raw, value_color)
                )
                return
            self._write(
                box_prefix
                + self._c(V, border)
                + " "
                + self._c(label_raw, label_color or border)
                + " "
                + self._c(value_raw, value_color)
                + pad
                + " "
                + self._c(V, border)
            )

        def section(text: str, border: str) -> None:
            """Start a new section inside the shared box."""
            line("", border)
            title(text, border)

        self.welcome(
            dashboard_url=dashboard_link,
            dashboard_enabled=dashboard_enabled,
        )

        top("green")
        kv("System", system, "green")
        kv("Output", output, "green")
        kv("Version", version, "green")
        kv("Started", started, "green")
        kv("Platform", f"{platform} ({precision} precision)", "green")
        # How to watch it. The banner computed this address and threw it away,
        # on the reasoning that the GUI prints its own -- true when a server
        # is running, and no help to somebody who has just started a run and
        # wants to see it. Where no server is running, the command that starts
        # one is the useful thing to print; a bare URL would point at nothing.
        if dashboard_enabled:
            kv("Watch", dashboard_link, "green")
        else:
            kv("Watch", f"fastmdx gui --output {_worth_watching(output)}", "green")
        # Only the phases this run includes. Announcing a million production
        # steps for a run that only analyses a trajectory is not a small
        # inaccuracy: it is the log saying the run did something it did not.
        if will_run("setup"):
            section("SETUP", "cyan")
            kv("pH", configured("setup", "ph") or setup_ph, "cyan")
            kv(
                "Ion Conc.",
                f"{configured('setup', 'ion_concentration_M') or ion_conc} M",
                "cyan",
            )
            kv("Force Field", configured("setup", "forcefield") or forcefield, "cyan")
        if will_run("simulation"):
            section("SIMULATION", "orange")
            kv("NVT", f"{format_steps(nvt_steps)} steps", "orange")
            kv("NPT", f"{format_steps(npt_steps)} steps", "orange")
            kv("Production", f"{format_steps(prod_steps)} steps", "orange")
            kv("Total Planned", f"{format_steps(total_steps)} steps", "orange")
            kv("Timestep", f"{timestep} fs", "orange")
            kv(
                "Temperature",
                f"{configured('simulation', 'temperature_K') or temperature} K",
                "orange",
            )
            kv("Friction", f"{friction} / ps", "orange")
            kv("DCD Frames", trajectory_display, "orange")

        # The report's title was listed here, which announced the name of a
        # document that did not exist yet and said nothing about the run. A
        # section with nothing to say is left out rather than printed empty.
        if dashboard_enabled and dashboard_link:
            section("ANALYSIS", "blue")
            # Only when a browser was actually asked for: printing the address
            # unconditionally sent people to a refused connection, since
            # `explore` does not start a server on its own.
            kv(
                "GUI",
                dashboard_link,
                "blue",
                label_color="cyan",
            )

        # Use green here to balance the cyan, orange, and blue sections above.
        # The list of output formats and the feature badges are gone. Both
        # said what this software is rather than what this run is doing, and
        # the banner is read once at the start of a run by somebody who has
        # already chosen to use it. The formats are visible in the output
        # directory afterwards, where they can be opened rather than admired.
        bottom("green")
        self._write("")

    def phase_start(self, name: str) -> None:
        """Print a phase header. Records the start time for :meth:`phase_end`."""
        if self.quiet:
            return
        self._phase_start = time.monotonic()
        self._current_phase = name
        arrow = self._c("▸", "cyan")
        label = self._c(f"Phase: {name}", "bold")
        self._write(f"{arrow} {label}")

    def phase_end(self, name: str, status: str = "ok", *, message: str | None = None) -> None:
        """Print a phase-complete summary line with elapsed time.

        Parameters
        ----------
        name : str
            The phase that just finished. Must match what was passed to
            :meth:`phase_start` for the timing to be correct.
        status : str
            ``"ok"`` | ``"error"`` | ``"skipped"`` | ``"warning"``.
        message : str, optional
            Override the default ``"{name} complete"`` text.
        """
        if self.quiet:
            return
        elapsed = (time.monotonic() - self._phase_start) if self._phase_start else 0.0
        icon, color = self._STATUS_ICON.get(status, ("·", "muted"))
        default_message = {
            "ok": f"{name} complete",
            "error": f"{name} failed",
            "skipped": f"{name} skipped",
            "warning": f"{name} completed with warnings",
        }.get(status, f"{name} complete")
        msg = message or default_message
        line = f"  {self._c(icon, color)} {msg} {self._c(f'({self._fmt_elapsed(elapsed)})', 'muted')}"
        self._write(line)
        self._write("")
        self._phase_start = None
        self._current_phase = None

    def progress(
        self,
        label: str,
        done: int,
        total: int,
        *,
        rate_ns_per_day: float | None = None,
        seconds_left: float | None = None,
    ) -> None:
        """Show how far through a stage the run is.

        Molecular dynamics is the part that takes the time, and it used to
        print the number of steps it was about to take and then nothing until
        it finished -- half an hour of a terminal that looked identical to a
        hung one.

        On a terminal this rewrites one line. Where the output is a file --
        which is where a worker's output goes when several run at once -- a
        line of carriage returns is unreadable, so it prints at intervals
        instead.
        """
        if self.quiet or total <= 0:
            return

        fraction = min(1.0, max(0.0, done / total))
        parts = [f"{fraction * 100:5.1f}%"]
        if rate_ns_per_day is not None and rate_ns_per_day > 0:
            parts.append(f"{rate_ns_per_day:.2f} ns/day")
        if seconds_left is not None and seconds_left > 0:
            parts.append(f"{_readable_duration(seconds_left)} left")
        trailer = "  ".join(parts)

        if not _ansi_supported(self.stream):
            # A file. Say something every tenth, and once at the end.
            tenth = max(1, total // 10)
            if done % tenth and done != total:
                return
            self._write(f"    {label}: {trailer}")
            return

        width = 28
        filled = int(width * fraction)
        bar = "\u2501" * filled + "\u2500" * (width - filled)
        line = (f"    {self._c(bar, 'green' if fraction < 1 else 'cyan')} "
                f"{trailer}  {label}")
        self.stream.write("\r\x1b[2K" + line)
        self.stream.flush()
        if done >= total:
            self.stream.write("\n")
            self.stream.flush()

    def step(self, message: str, *, status: str = "ok",
             explain: str | None = None) -> None:
        """Print a single indented step inside the current phase.

        Used for fine-grained progress messages within a phase, such as
        ``"Loaded input: protein.pdb"`` during setup or ``"Wrote slides.pptx"``
        during report.

        ``explain`` names an entry in :mod:`fastmdxplora.explain`, printed
        beneath the step when explanations are on. It is a key rather than the
        text itself, so an explanation cannot drift onto the wrong step and a
        step cannot quietly lose its explanation.
        """
        if self.quiet:
            return
        icon, color = self._STATUS_ICON.get(status, ("·", "muted"))
        self._write(f"  {self._c(icon, color)} {message}")

        self.explanation(explain)

    def explanation(self, key: str | None) -> None:
        """Print the explanation named by ``key``, if explanations are on.

        Separate from :meth:`step` because the simulation phase announces its
        stages through :meth:`info` -- no status icon, because "NVT
        equilibration: 250,000 steps" is not a thing that succeeded or failed
        -- and those stages are exactly where the reasons are worth having.
        The entries for minimisation, NVT, NPT and the ensemble existed and
        had nowhere to be printed from.
        """
        if not key or not self.explain or self.quiet:
            return
        from fastmdxplora.explain import explain as _lookup

        entry = _lookup(key)
        if entry is None:
            return
        self._write("")
        self._write(self._c(entry.as_text(), "muted"))
        self._write("")

    def info(self, message: str, *, explain: str | None = None) -> None:
        """Print a plain indented message (no status icon).

        Useful for things like ``"Loading trajectory... 10000 frames"``
        where a status icon would be misleading.
        """
        if self.quiet:
            return
        self._write(f"  {message}")
        self.explanation(explain)

    def analysis_table_row(
        self,
        name: str,
        status: str,
        path: str,
        elapsed: float,
        *,
        name_width: int | None = None,
        reason: str | None = None,
    ) -> None:
        """Print one row of the analysis-phase status table.

        Format::

            ▸ rmsd      ✓ ok    →  analysis/rmsd/      (0.4 s)

        The orchestrator should pass ``name_width`` set to the longest
        analysis name in the plan so all rows align.

        An analysis that fails passes ``reason``, which is printed beneath
        the row. Without it the table read ``✗ error`` and nothing else,
        while the explanation sat in the debug log: `rdf` on a default
        configuration reports *"selection_b 'water and name O' matched no
        atoms"*, which names the problem exactly, and a reader watching the
        run never saw it. The software refuses in order to be told why, and
        a refusal filed somewhere the person is not looking is a refusal
        that has not been made.
        """
        if self.quiet:
            return
        nw = name_width if name_width is not None else max(8, len(name))
        icon, color = self._STATUS_ICON.get(status, ("·", "muted"))
        arrow = self._c("▸", "cyan")
        path_arrow = self._c("→", "muted")
        elapsed_str = self._c(f"({self._fmt_elapsed(elapsed)})", "muted")
        line = (
            f"  {arrow} {name:<{nw}}  "
            f"{self._c(icon, color)} {status:<7}  "
            f"{path_arrow}  {path:<22}  {elapsed_str}"
        )
        self._write(line)
        detail = _the_useful_part_of(name, reason)
        if detail:
            for wrapped in _wrap(detail, max(40, self.width - nw - 12)):
                self._write(f"  {' ' * (nw + 4)}{self._c(wrapped, color)}")

    def done(self, *, message: str = "Done") -> None:
        """Print the closing session-total line."""
        if self.quiet:
            return
        elapsed = (time.monotonic() - self._session_start) if self._session_start else 0.0
        line = self._c(f"{message} in {self._fmt_elapsed(elapsed)}.", "bold")
        self._write(line)

    @contextmanager
    def phase(self, name: str, *, status: str = "ok") -> Iterator["SessionPresenter"]:
        """Context manager combining :meth:`phase_start` and :meth:`phase_end`.

        Yields ``self`` so users can call ``presenter.step(...)`` inside.
        On exception, the phase is closed with ``status="error"``.
        """
        self.phase_start(name)
        try:
            yield self
            self.phase_end(name, status=status)
        except Exception:
            self.phase_end(name, status="error")
            raise

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _write(self, line: str) -> None:
        self.stream.write(line + "\n")
        try:
            self.stream.flush()
        except Exception:  # noqa: BLE001 -- flush best-effort
            pass

    def _c(self, text: str, color: str) -> str:
        """Wrap text in an ANSI color if colors are enabled."""
        if not self._color or color not in _C:
            return text
        return f"{_C[color]}{text}{_C['reset']}"

    def _color_text(self, text: str, color: str) -> str:
        return self._c(text, color)

    def _top_rule(self, title: str, inner_w: int | None = None) -> str:
        """Top of the banner box: ``╭─ Title ──────────────╮``."""
        title_part = f" {title} "
        if inner_w is None:
            inner_w = max(20, len(title) + 4)
        # left segment: ╭─{title}
        left = "╭─"
        # right segment: ──╮ -- enough dashes to fill width
        # box width = 1(╭) + 1(─) + len(title_part) + dashes + 1(╮)
        # inner content width (between │ │) = inner_w; total = inner_w + 4
        dashes_count = inner_w - len(title_part)
        if dashes_count < 2:
            dashes_count = 2
        right_dashes = "─" * dashes_count
        return self._c(f"{left}{title_part}{right_dashes}╮", "dim")

    def _bottom_rule(self, inner_w: int | None = None) -> str:
        if inner_w is None:
            inner_w = 20
        return self._c(f"╰{'─' * (inner_w + 2)}╯", "dim")

    @staticmethod
    def _fmt_elapsed(seconds: float) -> str:
        """Human-friendly elapsed time: '0.4 s', '12.3 s', '1m 23s', '1h 5m'."""
        if seconds < 60:
            return f"{seconds:.1f} s"
        if seconds < 3600:
            m, s = divmod(int(seconds), 60)
            return f"{m}m {s}s"
        h, rem = divmod(int(seconds), 3600)
        m = rem // 60
        return f"{h}h {m}m"


# ---------------------------------------------------------------------------
# Module-level singleton accessor
# ---------------------------------------------------------------------------
_PRESENTER: SessionPresenter | None = None


def get_presenter() -> SessionPresenter:
    """Return the current session presenter, creating one on first access.

    The default presenter auto-detects quiet mode and terminal width from
    the environment. Callers who need different behavior should construct
    a :class:`SessionPresenter` directly.
    """
    global _PRESENTER
    if _PRESENTER is None:
        _PRESENTER = SessionPresenter()
    return _PRESENTER


def set_explain(enabled: bool) -> None:
    """Turn the step explanations on or off for the current session.

    The presenter is a singleton created before the command line has been
    read, so the setting arrives afterwards rather than through its
    constructor.
    """
    get_presenter().explain = bool(enabled)


def reset_presenter() -> None:
    """Reset the singleton. Used by tests to isolate state."""
    global _PRESENTER
    _PRESENTER = None
