"""A paper written for the tests, and an AI model that reads it as told.

The paper is made up, so nothing in the tests is a publisher's text: a JATS
article whose methods state some settings and leave others out, with a table
of results, as an open-access archive serves one. ``scripted`` answers the
three questions :mod:`fastmdxplora.paper.extract` asks with what it is given,
counting how often it is asked.
"""

from __future__ import annotations

import json
import zlib
from typing import Any

TITLE = "Dynamics of a test protein and its L50A mutant"
DOI = "10.9999/test.paper.0001"

METHODS = (
    "The starting structure was the crystal structure of ubiquitin (PDB ID: 1UBQ). "
    "The L50A mutant was built from it in silico. "
    "Each protein was described by the AMBER ff14SB force field and solvated with "
    "TIP3P water in a truncated octahedron, leaving at least 10 Å between the "
    "protein and the box edge; Na+ and Cl- ions were added to 150 mM NaCl. "
    "Simulations were run with GROMACS 2022 at 300 K, kept by the v-rescale "
    "thermostat, and 1 bar, kept by the Parrinello-Rahman barostat, with a 2 fs "
    "time step, bonds to hydrogen constrained with LINCS, a 1.0 nm cutoff and "
    "particle mesh Ewald electrostatics. After minimization, the systems were "
    "equilibrated for 1 ns in the NVT ensemble and 1 ns in the NPT ensemble. "
    "Production consisted of three independent 100-ns simulations of each system "
    "in the NPT ensemble."
)

RESULTS_TABLE = (
    "<table-wrap id='t1'><label>Table 1</label><caption><p>Means over the three "
    "replicas (nm)</p></caption><table><thead><tr><th>System</th><th>RMSD (nm)</th>"
    "<th>Rg (nm)</th></tr></thead><tbody><tr><td>Wild type</td><td>0.12 ± 0.01</td>"
    "<td>1.18 ± 0.02</td></tr><tr><td>L50A</td><td>0.15 ± 0.02</td><td>1.19 ± 0.02"
    "</td></tr></tbody></table><table-wrap-foot><p>Errors are standard errors of the "
    "mean over three replicas.</p></table-wrap-foot></table-wrap>"
)


def jats(methods: str = METHODS, *, table: str = RESULTS_TABLE) -> bytes:
    """The paper as JATS XML."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink">
<front><article-meta>
<article-id pub-id-type="doi">{DOI}</article-id>
<title-group><article-title>{TITLE}</article-title></title-group>
<permissions><license xlink:href="https://creativecommons.org/licenses/by/4.0/"><p>CC BY</p></license></permissions>
<abstract><p>We simulated a test protein and one mutant.</p></abstract>
</article-meta></front>
<body>
<sec><title>Introduction</title><p>A protein is studied.</p></sec>
<sec><title>Methods</title>
<sec><title>Molecular dynamics simulations</title><p>{methods}</p></sec>
</sec>
<sec><title>Results</title><p>The root-mean-square deviation of the wild type was
0.12 ± 0.01 nm (Table 1).</p>{table}</sec>
<sec><title>References</title><ref-list><ref><mixed-citation>1. Someone A, Other B. A paper. J Test. 2020;1:1-2.</mixed-citation></ref></ref-list></sec>
</body>
</article>""".encode("utf-8")


def pdf(pages: list[list[str]]) -> bytes:
    """A PDF of ``pages``, each a list of lines, in Helvetica: enough for a
    text extractor to read, made without any PDF library."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    kids = []
    page_ids = []
    for lines in pages:
        ops = ["BT", "/F1 11 Tf", "14 TL", "72 760 Td"]
        for line in lines:
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({escaped}) Tj T*")
        ops.append("ET")
        stream = zlib.compress("\n".join(ops).encode("cp1252"))
        content = add(b"<< /Length " + str(len(stream)).encode() + b" /Filter /FlateDecode >>\nstream\n"
                      + stream + b"\nendstream")
        page_ids.append(content)
    pages_id = len(objects) + len(page_ids) + 1
    for content in page_ids:
        kids.append(add(f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
                        f"/Resources << /Font << /F1 {font} 0 R >> >> /Contents {content} 0 R >>"
                        .encode()))
    add(f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
        .encode())
    catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n"
            .encode())
    return bytes(out)


def field(value: Any, quote: str, unit: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"value": value, "quote": quote}
    if unit:
        out["unit"] = unit
    return out


#: What a careful AI model reads from :func:`jats`.
STUDIES = [
    {"id": "S1", "label": "Ubiquitin, wild type", "protocol": "P1",
     "fields": {"pdb_id": field("1UBQ", "the crystal structure of ubiquitin (PDB ID: 1UBQ)"),
                "replicas": field(3, "Production consisted of three independent 100-ns simulations"),
                "production": field(100, "three independent 100-ns simulations", "ns")}},
    {"id": "S2", "label": "Ubiquitin L50A", "protocol": "P1",
     "fields": {"pdb_id": field("1UBQ", "the crystal structure of ubiquitin (PDB ID: 1UBQ)"),
                "mutations": field(["L50A"], "The L50A mutant was built from it in silico"),
                "replicas": field(3, "Production consisted of three independent 100-ns simulations"),
                "production": field(100, "three independent 100-ns simulations", "ns")}},
]

PROTOCOL = {
    "protein_forcefield": field("ff14SB", "described by the AMBER ff14SB force field"),
    "water_model": field("TIP3P", "solvated with TIP3P water"),
    "box_shape": field("truncated octahedron", "in a truncated octahedron"),
    "padding": field(10, "leaving at least 10 Å between the protein and the box edge", "Å"),
    "salt_concentration": field(150, "added to 150 mM NaCl", "mM"),
    "ions": field(["Na+", "Cl-"], "Na+ and Cl- ions were added to 150 mM NaCl"),
    "temperature": field(300, "Simulations were run with GROMACS 2022 at 300 K", "K"),
    "pressure": field(1, "and 1 bar, kept by the Parrinello-Rahman barostat", "bar"),
    "thermostat": field("v-rescale", "kept by the v-rescale thermostat"),
    "barostat": field("Parrinello-Rahman", "kept by the Parrinello-Rahman barostat"),
    "timestep": field(2, "with a 2 fs time step", "fs"),
    "constraints": field("bonds to hydrogen (LINCS)", "bonds to hydrogen constrained with LINCS"),
    "cutoff": field(1.0, "a 1.0 nm cutoff", "nm"),
    "electrostatics": field("PME", "particle mesh Ewald electrostatics"),
    "nvt_equilibration": field(1, "equilibrated for 1 ns in the NVT ensemble", "ns"),
    "npt_equilibration": field(1, "and 1 ns in the NPT ensemble", "ns"),
    "ensemble": field("NPT", "of each system in the NPT ensemble"),
    "engine": field("GROMACS 2022", "Simulations were run with GROMACS 2022"),
}

CLAIMS = [
    {"study": "S1", "quantity": "rmsd", "what": "backbone", "value": 0.12, "error": 0.01,
     "error_kind": "standard_error", "n": 3, "unit": "nm",
     "quote": "Wild type | 0.12 ± 0.01 | 1.18 ± 0.02"},
    {"study": "S1", "quantity": "radius_of_gyration", "what": "protein", "value": 1.18,
     "error": 0.02, "error_kind": "standard_error", "n": 3, "unit": "nm",
     "quote": "Wild type | 0.12 ± 0.01 | 1.18 ± 0.02"},
    {"study": "S2", "quantity": "rmsd", "what": "backbone", "value": 0.15, "error": 0.02,
     "error_kind": "standard_error", "n": 3, "unit": "nm",
     "quote": "L50A | 0.15 ± 0.02 | 1.19 ± 0.02"},
]


class scripted:
    """An AI model that answers each question with what it is given, and
    counts the questions."""

    def __init__(self, studies: list[dict[str, Any]] | None = None,
                 protocol: dict[str, Any] | None = None,
                 claims: list[dict[str, Any]] | None = None) -> None:
        self.studies = STUDIES if studies is None else studies
        self.protocol = PROTOCOL if protocol is None else protocol
        self.claims = CLAIMS if claims is None else claims
        self.asked: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.asked.append(prompt)
        if "list the molecular dynamics (MD) studies" in prompt:
            return "Here it is:\n```json\n" + json.dumps({
                "title": TITLE, "studies": self.studies,
                "protocols": [{"id": "P1", "label": "every study"}], "more": False}) + "\n```"
        if "simulation settings of one of its MD protocols" in prompt:
            return json.dumps({"fields": self.protocol})
        if "numerical results" in prompt:
            return json.dumps({"claims": self.claims, "more": False})
        return "{}"
