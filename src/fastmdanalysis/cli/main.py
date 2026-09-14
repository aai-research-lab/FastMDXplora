# src/fastmdanalysis/cli/main.py
from __future__ import annotations

import sys
import argparse

from ._common import (
    make_common_parser,
    setup_logging,
    parse_frames,
    build_instance,
    expand_trajectory_args,
    normalize_topology_arg,
)
from . import analyze as analyze_cmd
from . import simple as simple_cmd
from ..utils.logging import log_run_header


def _build_parser() -> argparse.ArgumentParser:
    common = make_common_parser()
    parser = argparse.ArgumentParser(
        description="FastMDAnalysis: Fast Automated MD Trajectory Analysis",
        epilog=(
            "Superseded by FastMDXplora: pip install fastmdxplora\n"
            "Docs: https://fastmdanalysis.readthedocs.io/en/latest/"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[common],
    )
    subparsers = parser.add_subparsers(dest="command", help="Analysis type", required=True)

    # Register subcommands
    analyze_cmd.register(subparsers, common)
    simple_cmd.register_simple(subparsers, common)

    return parser


def _normalize_argv(argv: list[str]) -> list[str]:
    """
    Lightweight normalization to support user-friendly '-ref' (single hyphen) for RMSD.
    Maps:
      - '-ref'      -> '--reference-frame'
      - '-ref=VAL'  -> '--reference-frame=VAL'
    """
    out: list[str] = []
    for tok in argv:
        if tok == "-ref":
            out.append("--reference-frame")
        elif tok.startswith("-ref="):
            out.append("--reference-frame=" + tok.split("=", 1)[1])
        else:
            out.append(tok)
    return out


def main() -> None:
    parser = _build_parser()
    argv = _normalize_argv(sys.argv[1:])
    args = parser.parse_args(argv)

    # -------------------------------------------------------------------------
    # Special path: analyze with a system file/dict drives construction directly.
    # We instantiate FastMDAnalysis(system=...) first, then configure logging
    # using CLI output override or YAML-provided output, and dispatch.
    # -------------------------------------------------------------------------
    if args.command == "analyze" and getattr(args, "system", None):
        try:
            from .. import FastMDAnalysis  # constructor now accepts system= (path or dict)

            # CLI values (frames/atoms) override YAML when provided, so pass them through.
            # We intentionally do NOT normalize trajectory/topology here; the constructor
            # uses the system config to fill them (and accepts alias keys).
            fastmda = FastMDAnalysis(
                system=args.system,
                frames=getattr(args, "frames", None),
                atoms=getattr(args, "atoms", None),
            )
        except SystemExit:
            raise
        except Exception as e:
            # No logger yet; print a concise error and exit.
            sys.stderr.write(f"[fastmda] Failed to initialize from system file: {e}\n")
            sys.exit(1)

        # Choose output directory: CLI override > YAML (stored on instance) > default
        output_dir = (
            getattr(args, "output", None)
            or getattr(fastmda, "_system_output", None)
            or f"{args.command}_output"
        )
        logger = setup_logging(output_dir, getattr(args, "verbose", False), args.command)

        # Emit version/runtime header for provenance (best-effort)
        try:
            log_run_header(logger)
        except Exception:
            pass

        logger.info("Running 'analyze' with system configuration: %s", getattr(fastmda, "_system_file", args.system))

        # Handler defaults are registered by the subcommand; fall back to module handle().
        handler = getattr(args, "_handler", None) or analyze_cmd.handle
        handler(args, fastmda, logger)
        return

    # -------------------------------------------------------------------------
    # Default path (no system file): keep existing normalization + construction.
    # -------------------------------------------------------------------------
    # Output dir per command
    output_dir = args.output if getattr(args, "output", None) else f"{args.command}_output"
    logger = setup_logging(output_dir, getattr(args, "verbose", False), args.command)

    # Emit version/runtime header for provenance
    try:
        log_run_header(logger)
    except Exception:
        # Never fail the CLI due to logging
        pass

    logger.info("Parsed arguments: %s", args)

    # Normalize IO args centrally (Option A)
    try:
        # expand space/comma/glob and validate existence
        trajs = expand_trajectory_args(args.trajectory)
        top = normalize_topology_arg(args.topology)
        # write back normalized values so handlers can use them if needed
        args.trajectory = trajs
        args.topology = top
    except SystemExit:
        # clear error message already formed in helper
        raise
    except Exception as e:
        logger.error("Invalid input paths: %s", e)
        sys.exit(2)

    # Shared init
    frames = parse_frames(getattr(args, "frames", None))
    atoms = getattr(args, "atoms", None)

    try:
        fastmda = build_instance(trajs, top, frames=frames, atoms=atoms)
    except SystemExit:
        raise
    except Exception as e:
        logger.error("Error initializing FastMDAnalysis: %s", e)
        sys.exit(1)

    # Dispatch to the registered handler
    handler = getattr(args, "_handler", None)
    if handler is None:
        # Fallbacks for safety if a subcommand forgot to register _handler
        if args.command == "analyze":
            handler = analyze_cmd.handle
        elif args.command == "simple":
            handler = simple_cmd.handle  # type: ignore[attr-defined]
        else:
            parser.error("No handler registered for the selected subcommand.")
    handler(args, fastmda, logger)
