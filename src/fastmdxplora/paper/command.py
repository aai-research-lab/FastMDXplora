"""`fastmdx config --paper`: a paper's MD studies listed, and the chosen
ones written as configs."""

from __future__ import annotations

import argparse
import sys

from fastmdxplora.refusals import CodedError

__all__ = ["config_from_paper"]

_MARK = {"as_stated": "=", "not_stated": ".", "differs": "~", "needs_you": "!",
         "not_possible": "x"}


def config_from_paper(args: argparse.Namespace) -> int:
    from fastmdxplora.paper.studies import (
        choose,
        plans_for,
        said_of,
        studies_in,
        write_configs,
    )

    def said(message: str) -> None:
        print(message, file=sys.stderr)

    try:
        paper, reading = studies_in(args.paper, list(args.paper_si or []), said=said)
    except CodedError as exc:
        print(f"fastmdx config: {exc}", file=sys.stderr)
        return 2
    plans = plans_for(reading, until_determined=bool(args.paper_until_determined))
    title = reading.get("title") or paper.title or args.paper
    print(f"{title}" + (f" (doi:{reading['doi']})" if reading.get("doi") else ""))
    print(f"Read by {reading.get('model') or 'the AI model'}; every value checked against "
          "the paper's own words.")
    if reading.get("left_out"):
        print("Too long to read whole; left out: " + ", ".join(reading["left_out"][:8]) + ".")
    print("")
    for plan in plans:
        print(said_of(plan))
    print("")
    if not args.paper_studies:
        print("Choose which to write with --paper-studies (ids, or all); each setting's "
              "source is then in the config.")
        return 0
    try:
        chosen = choose(plans, args.paper_studies)
        written = write_configs(chosen, args.config_file, force=bool(args.force))
    except CodedError as exc:
        print(f"fastmdx config: {exc}", file=sys.stderr)
        return 2
    for entry in written:
        plan = entry["plan"]
        print(f"{plan['id']}: {plan.get('label', '')}")
        for choice in plan.get("choices") or []:
            if choice["label"] in ("differs", "needs_you", "not_possible"):
                print(f"  {_MARK[choice['label']]} {choice['field']}: {choice['why']}")
        counts = {}
        for choice in plan.get("choices") or []:
            counts[choice["label"]] = counts.get(choice["label"], 0) + 1
        print("  " + ", ".join(f"{counts[label]} {label.replace('_', ' ')}"
                               for label in ("as_stated", "not_stated", "differs",
                                             "needs_you", "not_possible") if label in counts))
        if not entry.get("written"):
            print("  Not written: this software cannot run it.")
            continue
        print(f"  Wrote {entry['path']}.")
        if entry.get("refused"):
            print(f"  It is refused until completed: {entry['refused'].splitlines()[0]}")
        else:
            print(f"  Run it with:  fastmdx explore --config {entry['path']}")
    return 0
