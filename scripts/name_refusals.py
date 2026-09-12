"""Insert ``code=`` into raise sites, located by AST rather than by regex.

Used to migrate the package's refusals onto
:mod:`fastmdxplora.refusals`. Kept because the migration is not finished:
roughly two hundred raise sites still say nothing about which refusal
they are, and doing the rest by hand is how a rule gets applied to the
wrong site at two in the morning.

A raise site spans lines, holds f-strings with braces and nested quotes,
and ends at a paren a regex cannot reliably find. So this parses, finds
the call's end offset exactly, and inserts there.

Matching is on a distinctive fragment of the message, checked to appear
exactly once in the file, so a rule cannot silently patch the wrong site.
Every rule must fire; a rule that matches nothing is reported and the file
is left alone.
"""
from __future__ import annotations

import ast
import pathlib
import sys


def patch(path: pathlib.Path, rules: list[tuple[str, str, str]]) -> int:
    """rules: (fragment, code, extra_kwargs). Returns sites patched."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.split("\n")

    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
            continue
        if any(k.arg == "code" for k in node.exc.keywords):
            continue
        seg = ast.get_source_segment(src, node.exc) or ""
        sites.append((node.exc, seg))

    # Resolve each rule to exactly one site.
    planned = []
    for fragment, code, extra in rules:
        hits = [(call, seg) for call, seg in sites if fragment in seg]
        if len(hits) != 1:
            print(f"  !! {path.name}: {len(hits)} sites match {fragment!r}")
            return 0
        call, _ = hits[0]
        kwargs = f', code="{code}"' + (f", {extra}" if extra else "")
        planned.append((call.end_lineno, call.end_col_offset, kwargs))

    # Apply back to front so earlier offsets stay valid.
    for lineno, col, kwargs in sorted(planned, reverse=True):
        line = lines[lineno - 1]
        close = line.rfind(")", 0, col)
        if close < 0:
            print(f"  !! {path.name}:{lineno}: no closing paren found")
            return 0
        lines[lineno - 1] = line[:close] + kwargs + line[close:]

    out = "\n".join(lines)
    ast.parse(out)  # refuse to write anything that will not parse
    path.write_text(out, encoding="utf-8")
    return len(planned)


if __name__ == "__main__":
    print("imported as a module", file=sys.stderr)
