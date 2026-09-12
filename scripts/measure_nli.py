"""Run the natural-language evaluation against a real model.

    ANTHROPIC_API_KEY=... python scripts/measure_nli.py
    ANTHROPIC_API_KEY=... python scripts/measure_nli.py --terse

No client library: one HTTPS POST, so this runs wherever the package does
and adds no dependency to a package that should not gain one for a script.

Point it elsewhere by editing `complete` -- it takes a prompt and returns
text, and nothing above it knows or cares what answered.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

MODEL = os.environ.get("FASTMDX_EVAL_MODEL", "claude-sonnet-4-6")


def complete(prompt: str) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit(
            "Set ANTHROPIC_API_KEY. This measures what a model does with "
            "the generated schema description, so it needs a model.")
    request = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        method="POST",
        data=json.dumps({
            "model": MODEL,
            "max_tokens": 2000,
            "messages": [{"role": "user", "content": prompt}],
        }).encode(),
        headers={"content-type": "application/json",
                 "anthropic-version": "2023-06-01",
                 "x-api-key": key})
    with urllib.request.urlopen(request, timeout=120) as response:
        body = json.loads(response.read())
    return "".join(block.get("text", "") for block in body.get("content", []))


def main() -> int:
    sys.path.insert(0, "src")
    from fastmdxplora.agent.evaluate import measure

    verbose = "--terse" not in sys.argv
    report = measure(complete, verbose_schema=verbose)
    print(f"model {MODEL}, schema description "
          f"{'with' if verbose else 'without'} help text")
    print(report)
    if "--json" in sys.argv:
        print(json.dumps(report.as_record(), indent=2))
    # Correct, not merely valid. A config that validates and asks for the
    # wrong temperature is not a pass.
    return 0 if report.correct == len(report.outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
