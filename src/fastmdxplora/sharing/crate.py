"""The packing list of a shared study: RO-Crate 1.2 with FastMDXplora's profile.

`ro-crate-metadata.json` says what the archive is (a study, its title,
authors, licence and the profile it keeps) and what each file in it is: its
path in the study, its size, its SHA-256, the phase that wrote it and its
role, and for a trajectory the topology it is read with. Each phase that ran
is a `CreateAction`, as Process Run Crate describes a run of a program. The
rules are given in full in `docs/sharing.md`; :func:`check` holds an archive
to them before anything in it is opened.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from fastmdxplora.sharing import PROFILE, PROFILE_VERSION, ShareRefused

__all__ = ["CRATE", "CRATE_SPEC", "METADATA", "PREVIEW", "PROCESS_RUN", "ROLES",
           "build", "check", "encoding_of", "role_of"]

METADATA = "ro-crate-metadata.json"
#: An HTML page a crate may carry to be read in a browser; never a study file.
PREVIEW = "ro-crate-preview.html"
CRATE_SPEC = "https://w3id.org/ro/crate/1.2"
CRATE = [
    f"{CRATE_SPEC}/context",
    {"sha256": "https://w3id.org/ro/terms/workflow-run#sha256",
     "fmx": "https://w3id.org/fastmdxplora/terms#"},
]
PROCESS_RUN = "https://w3id.org/ro/wfrun/process/0.5"

#: What a file of a study is: `fmx:role`.
ROLES = ("config", "manifest", "record", "structure", "ligand", "system", "state",
         "checkpoint", "trajectory", "topology", "series", "analysis", "figure", "report",
         "log", "view")

_FORMATS = {
    ".pdb": "chemical/x-pdb", ".cif": "chemical/x-cif", ".mmcif": "chemical/x-mmcif",
    ".sdf": "chemical/x-mdl-sdfile", ".mol": "chemical/x-mdl-molfile",
    ".mol2": "chemical/x-mol2", ".json": "application/json", ".yml": "application/yaml",
    ".yaml": "application/yaml", ".md": "text/markdown", ".csv": "text/csv",
    ".tsv": "text/tab-separated-values", ".dat": "text/plain", ".txt": "text/plain",
    ".log": "text/plain", ".dx": "text/plain", ".sha256": "text/plain",
    ".png": "image/png", ".svg": "image/svg+xml", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".gif": "image/gif", ".pdf": "application/pdf",
    ".html": "text/html", ".xml": "application/xml", ".mvsx": "application/zip",
    ".mp4": "video/mp4", ".webm": "video/webm",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

_SERIES = frozenset({"energy.csv", "equilibration_energy.csv", "live_metrics.csv"})
_RECORDS_IN_ANALYSIS = frozenset({"analysis_manifest.json", "options.json"})


def encoding_of(path: str) -> str:
    """A file's media type, from its name; `application/octet-stream` for a
    binary format with none of its own (DCD, XTC, NumPy)."""
    return _FORMATS.get(PurePosixPath(path).suffix.lower(), "application/octet-stream")


def role_of(path: str, *, phase: str, kind: str, topologies: frozenset[str]) -> str:
    """A file's `fmx:role`, from where the study keeps it and what it holds;
    ``topologies`` are the files a trajectory is read with."""
    name = PurePosixPath(path).name
    suffix = PurePosixPath(path).suffix.lower()
    if name == "resolved_config.yml":
        return "config"
    if name == "manifest.json" and "/" not in path.strip("/"):
        return "manifest"
    if path in topologies:
        return "topology"
    if phase == "saved":
        return "view"
    if kind == "trajectory":
        return "trajectory"
    if suffix == ".chk":
        return "checkpoint"
    if kind == "state":
        return "state"
    if name in ("system.xml", "integrator.xml"):
        return "system"
    if suffix in (".sdf", ".mol", ".mol2"):
        return "ligand"
    if kind == "structure":
        return "view" if suffix == ".mvsx" else "structure"
    if name in _SERIES:
        return "series"
    if kind == "log":
        return "log"
    if phase == "report":
        return "figure" if kind == "figure" else "report"
    if kind == "figure":
        return "figure"
    if phase == "analysis":
        return "record" if name in _RECORDS_IN_ANALYSIS else "analysis"
    if kind == "data":
        return "series"
    return "record"


def build(*, name: str, description: str, published: str, license_id: str,
          authors: list[tuple[str, str]], files: list[dict[str, Any]],
          actions: list[dict[str, Any]], software: dict[str, Any], package: str,
          placeholders: dict[str, list[str]], identifier: str = "",
          based_on: str = "") -> dict[str, Any]:
    """The packing list, as JSON. ``files`` are the study's files, each with
    its `path`, `name`, `size`, `sha256`, `phase` and `role`, and for a
    trajectory `read_with`, `frames`, `atoms`, `interval_ps` where known;
    ``actions`` each phase that ran, with its `name`, `start`, `end`,
    `object` and `result` (paths)."""
    graph: list[dict[str, Any]] = [
        {"@id": METADATA, "@type": "CreativeWork",
         "conformsTo": {"@id": CRATE_SPEC}, "about": {"@id": "./"}},
    ]
    people = []
    for index, (person, orcid) in enumerate(authors, 1):
        key = f"https://orcid.org/{orcid}" if orcid else f"#author-{index}"
        people.append({"@id": key, "@type": "Person", "name": person})
    licence = f"https://spdx.org/licenses/{license_id}"
    root: dict[str, Any] = {
        "@id": "./", "@type": "Dataset", "name": name, "description": description,
        "datePublished": published, "license": {"@id": licence},
        "author": [{"@id": p["@id"]} for p in people],
        "conformsTo": [{"@id": PROFILE}, {"@id": PROCESS_RUN}],
        "mainEntity": {"@id": "resolved_config.yml"},
        "fmx:package": package,
        "fmx:placeholders": [{"fmx:placeholder": key, "fmx:in": [{"@id": p} for p in held]}
                             for key, held in sorted(placeholders.items()) if held],
        "hasPart": [{"@id": f["path"]} for f in files],
        "mentions": [{"@id": f"#{a['name']}"} for a in actions],
    }
    if identifier:
        root["identifier"] = identifier
    if based_on:
        root["isBasedOn"] = {"@id": based_on}
    graph.append(root)
    for f in files:
        entity: dict[str, Any] = {
            "@id": f["path"], "@type": "File", "name": f["name"],
            "encodingFormat": encoding_of(f["path"]), "contentSize": str(f["size"]),
            "sha256": f["sha256"], "fmx:phase": f["phase"], "fmx:role": f["role"]}
        if f.get("read_with"):
            entity["fmx:readWith"] = {"@id": f["read_with"]}
        for key, term in (("frames", "fmx:frames"), ("atoms", "fmx:atoms"),
                          ("interval_ps", "fmx:savingIntervalPs")):
            if f.get(key) is not None:
                entity[term] = f[key]
        graph.append(entity)
    graph.extend(people)
    graph.append({"@id": licence, "@type": "CreativeWork", "name": license_id,
                  "identifier": license_id})
    graph.append({"@id": "#fastmdxplora", "@type": "SoftwareApplication",
                  "name": "FastMDXplora", **software})
    for a in actions:
        action: dict[str, Any] = {
            "@id": f"#{a['name']}", "@type": "CreateAction",
            "name": a["name"].capitalize(), "instrument": {"@id": "#fastmdxplora"},
            "object": [{"@id": p} for p in a["object"]],
            "result": [{"@id": p} for p in a["result"]]}
        if a.get("start"):
            action["startTime"] = a["start"]
        if a.get("end"):
            action["endTime"] = a["end"]
        graph.append(action)
    for key, label in ((PROFILE, "A FastMDXplora study"),
                       (PROCESS_RUN, "Process Run Crate")):
        graph.append({"@id": key, "@type": ["CreativeWork", "Profile"], "name": label,
                      "version": PROFILE_VERSION if key == PROFILE else "0.5"})
    return {"@context": CRATE, "@graph": graph}


def _ids(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    return [str(item.get("@id")) for item in items if isinstance(item, dict) and item.get("@id")]


def _version_of(profile: str) -> tuple[int, int] | None:
    stem = "https://w3id.org/fastmdxplora/study/"
    if not profile.startswith(stem):
        return None
    try:
        major, minor = profile[len(stem):].split(".")
        return int(major), int(minor)
    except ValueError:
        return None


def check(data: Any) -> dict[str, dict[str, Any]]:
    """The files a packing list names, by path, each with its size and
    SHA-256, once the list keeps RO-Crate 1.2 and the rules of a version of
    the profile this release reads (`docs/sharing.md`); refused
    (`environment.share.not_a_study`) where it does not, and
    (`environment.share.unsafe`) where a file's name leads outside the
    archive."""
    graph = data.get("@graph") if isinstance(data, dict) else None
    if not isinstance(graph, list):
        raise ShareRefused("This archive is not a shared FastMDXplora study: its packing "
                           "list has no @graph.", code="environment.share.not_a_study")
    by_id = {str(e.get("@id")): e for e in graph if isinstance(e, dict) and e.get("@id")}
    descriptor = by_id.get(METADATA, {})
    if CRATE_SPEC not in _ids(descriptor.get("conformsTo")):
        raise ShareRefused(f"This archive is not a shared FastMDXplora study: its packing "
                           f"list is not RO-Crate 1.2 ({CRATE_SPEC}).",
                           code="environment.share.not_a_study")
    about = _ids(descriptor.get("about"))
    root = by_id.get(about[0] if about else "./", {})
    known = [v for v in (_version_of(p) for p in _ids(root.get("conformsTo"))) if v]
    if not known:
        raise ShareRefused(f"This archive is not a shared FastMDXplora study: it does not keep "
                           f"the study profile ({PROFILE}).", code="environment.share.not_a_study")
    ours = int(PROFILE_VERSION.split(".")[0])
    if all(major != ours for major, _ in known):
        major, minor = max(known)
        raise ShareRefused(f"This archive keeps version {major}.{minor} of the FastMDXplora "
                           f"study profile, and this release reads version {ours}; a newer "
                           "FastMDXplora opens it.", code="environment.share.not_a_study")
    missing = [key for key in ("name", "datePublished", "license", "author")
               if not root.get(key)]
    if "resolved_config.yml" not in _ids(root.get("mainEntity")):
        missing.append("mainEntity (resolved_config.yml)")
    if missing:
        raise ShareRefused(f"This archive is not a shared FastMDXplora study: its root has no "
                           f"{', '.join(missing)}.", code="environment.share.not_a_study")
    files: dict[str, dict[str, Any]] = {}
    for path in _ids(root.get("hasPart")):
        pure = PurePosixPath(path)
        if (not path or pure.is_absolute() or "://" in path or ".." in pure.parts
                or "\\" in path or path in (METADATA, PREVIEW)):
            raise ShareRefused(f"This archive names a file outside itself, {path!r}.",
                               code="environment.share.unsafe")
        entity = by_id.get(path)
        types = entity.get("@type") if isinstance(entity, dict) else None
        if "File" not in (types if isinstance(types, list) else [types]):
            continue
        digest = str(entity.get("sha256") or "").lower()
        try:
            size = int(entity.get("contentSize"))
        except (TypeError, ValueError):
            size = -1
        if size < 0 or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ShareRefused(f"This archive is not a shared FastMDXplora study: {path} has no "
                               "size or SHA-256.", code="environment.share.not_a_study")
        if entity.get("fmx:role") == "trajectory" and not _ids(entity.get("fmx:readWith")):
            raise ShareRefused(f"This archive is not a shared FastMDXplora study: the "
                               f"trajectory {path} names no topology to be read with.",
                               code="environment.share.not_a_study")
        files[path] = {"size": size, "sha256": digest}
    if "resolved_config.yml" not in files:
        raise ShareRefused("This archive is not a shared FastMDXplora study: it lists no "
                           "Config, resolved_config.yml.", code="environment.share.not_a_study")
    return files
