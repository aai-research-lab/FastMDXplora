"""Write the Docker build for the image `fastmdx.def` describes.

`fastmdx.def` is the one recipe. The Apptainer image on each release page is
built from it for clusters, and the Docker image is built from it here, for
services that run FastMDXplora in containers. Two recipes kept in step by hand
would drift: one would gain a package or a check the other lacks, and a study
would give a different answer depending on which image ran it. So this file
holds no package list and no check of its own. It copies the definition's
`%post` (the whole installation, and the checks that fail the build) and its
`%test` into the build, and turns `%environment` and `%runscript` into their
Docker equivalents.

What Docker adds is what a service needs and a cluster does not: a user that
is not root, a workspace at `/workspace` that is also `HOME`, and the GUI's
port.

Usage, from the repository root::

    python container/docker_from_def.py build/docker
    docker build --build-arg FASTMDX_VERSION=2.5.8 -t fastmdx build/docker

Standard library only, so it runs on a build machine with nothing installed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFINITION = Path(__file__).with_name("fastmdx.def")

#: Where a person's studies live in the image, and its HOME, so that the
#: Agent's stored model choice sits with them on the same volume.
WORKSPACE = "/workspace"

#: The GUI's port.
PORT = 8765


class DefinitionError(ValueError):
    """The definition is not in the shape this file reads."""


def sections(definition: str) -> dict[str, str]:
    """The definition's header and its `%` sections, by name."""
    found: dict[str, str] = {}
    name = "header"
    lines: list[str] = []
    for line in definition.splitlines():
        match = re.match(r"^%(\w+)\s*$", line)
        if match:
            found[name] = "\n".join(lines).strip("\n")
            name, lines = match.group(1), []
        else:
            lines.append(line)
    found[name] = "\n".join(lines).strip("\n")
    return found


def base_image(header: str) -> str:
    """The `From:` of a `Bootstrap: docker` definition."""
    if not re.search(r"^Bootstrap:\s*docker\s*$", header, re.M):
        raise DefinitionError("the definition does not bootstrap from a Docker image")
    match = re.search(r"^From:\s*(\S+)\s*$", header, re.M)
    if not match:
        raise DefinitionError("the definition names no From: image")
    return match.group(1)


def environment(section: str) -> list[tuple[str, str]]:
    """`export NAME=value` lines, in order. Anything else is refused, since
    it would be run in Apptainer and silently dropped here."""
    pairs = []
    for raw in section.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^export\s+([A-Za-z_][A-Za-z0-9_]*)=(\S+)$", line)
        if not match:
            raise DefinitionError(f"%environment line not understood: {line!r}")
        pairs.append((match.group(1), match.group(2)))
    return pairs


def entrypoint(section: str) -> list[str]:
    """`exec program "$@"` as the image's entry point."""
    commands = [line.strip() for line in section.splitlines()
                if line.strip() and not line.strip().startswith("#")]
    match = re.match(r'^exec\s+(\S+)\s+"\$@"$', commands[0]) if len(commands) == 1 else None
    if not match:
        raise DefinitionError(f"%runscript not understood: {section!r}")
    return [match.group(1)]


def _dedent(section: str) -> str:
    lines = section.splitlines()
    indents = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
    cut = min(indents) if indents else 0
    return "\n".join(line[cut:] for line in lines) + "\n"


def dockerfile(parts: dict[str, str]) -> str:
    """The Dockerfile for these sections."""
    env_lines = "\n".join(f"ENV {name}={value}" for name, value in environment(parts["environment"]))
    entry = entrypoint(parts["runscript"])
    return f"""\
# Written by container/docker_from_def.py from container/fastmdx.def.
# Do not edit: change fastmdx.def, which the Apptainer image is built from too.
FROM {base_image(parts["header"])}

# The version to install and the CUDA it is built against, as for Apptainer.
ARG FASTMDX_VERSION
ARG CUDA_VERSION

USER root
# The definition's own default version is a placeholder the release workflow
# replaces, so a build that names none would install an old release quietly.
RUN test -n "$FASTMDX_VERSION" || {{ echo "Build with --build-arg FASTMDX_VERSION=<version>." >&2; exit 1; }}
COPY post.sh test.sh /opt/fastmdx/
# The definition's %post: the installation and the checks that fail the build.
RUN bash -eux /opt/fastmdx/post.sh

{env_lines}

# What a service needs that a cluster does not: a workspace that is also HOME,
# owned by a user that is not root.
RUN mkdir -p {WORKSPACE} && chown "$MAMBA_USER:$MAMBA_USER" {WORKSPACE}
ENV HOME={WORKSPACE}
WORKDIR {WORKSPACE}
USER $MAMBA_USER
EXPOSE {PORT}

# The definition's %test, kept in the image: bash /opt/fastmdx/test.sh
ENTRYPOINT {json.dumps(entry)}
CMD ["--help"]
"""


def write(out: Path, definition: Path = DEFINITION) -> Path:
    """Write the Dockerfile and the two scripts it copies into `out`."""
    parts = sections(definition.read_text(encoding="utf-8"))
    for needed in ("header", "post", "environment", "runscript", "test"):
        if needed not in parts:
            raise DefinitionError(f"the definition has no %{needed}")
    out.mkdir(parents=True, exist_ok=True)
    (out / "Dockerfile").write_text(dockerfile(parts), encoding="utf-8")
    (out / "post.sh").write_text(_dedent(parts["post"]), encoding="utf-8")
    (out / "test.sh").write_text("set -eu\n" + _dedent(parts["test"]), encoding="utf-8")
    return out / "Dockerfile"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path, help="Folder to write the Docker build into.")
    args = parser.parse_args(argv)
    try:
        print(write(args.out))
    except DefinitionError as exc:
        print(f"docker_from_def: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
