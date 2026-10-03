"""Read-only preparation evidence; inventory differences never invent causes."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from urllib.parse import quote

PROTEIN = set(
    (
        "ALA ARG ASN ASP CYS GLN GLU GLY HIS HID HIE HIP ILE LEU LYS MET "
        "PHE PRO SER THR TRP TYR VAL MSE SEC PYL CYX ASH GLH LYN"
    ).split()
)
WATER = {"HOH", "WAT", "SOL", "TIP3", "TIP3P", "TIP4P"}
IONS = {
    "NA",
    "CL",
    "K",
    "CA",
    "MG",
    "ZN",
    "FE",
    "MN",
    "CU",
    "CO",
    "NI",
    "CD",
    "BR",
    "I",
    "CS",
    "LI",
    "RB",
    "F",
}


def _file(root, relative, limit=32 * 1024 * 1024):
    if not isinstance(relative, str):
        return None
    try:
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > limit:
            return None
    except (OSError, ValueError):
        return None
    return path


def _record(root, relative):
    path = _file(root, relative, 2_000_000)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path else {}
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _inventory(path):
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ValueError("Preparation source changed during reading")
    atoms, ambiguous = {}, False
    component_counts = {}
    counts = {"protein": 0, "water": 0, "ions": 0, "other": 0, "hydrogens": 0, "total": 0}
    lines = raw.decode("utf-8", errors="replace").splitlines()
    model_count = sum(line.startswith("MODEL ") for line in lines)
    for line in lines:
        if line.startswith("ENDMDL"):
            break
        if line[:6].strip() not in {"ATOM", "HETATM"} or len(line) < 54:
            continue
        key = (
            line[21:22].strip(),
            line[22:26].strip(),
            line[26:27].strip(),
            line[17:20].strip(),
            line[12:16].strip(),
            line[16:17].strip(),
        )
        element = line[76:78].strip().upper() if len(line) >= 78 else ""
        counts["total"] += 1
        component_counts[key[3]] = component_counts.get(key[3], 0) + 1
        counts["hydrogens"] += element in {"H", "D"}
        kind = (
            "protein"
            if key[3] in PROTEIN
            else "water"
            if key[3] in WATER
            else "ions"
            if key[3] in IONS
            else "other"
        )
        counts[kind] += 1
        if key in atoms:
            ambiguous = True
        try:
            xyz = [float(line[start : start + 8]) for start in (30, 38, 46)]
        except ValueError:
            xyz = None
        if xyz and not all(math.isfinite(value) for value in xyz):
            xyz = None
        atoms[key] = {"element": element, "xyz": xyz}
    return {
        "atoms": atoms,
        "counts": counts,
        "component_counts": component_counts,
        "ambiguous": ambiguous,
        "models": model_count or int(counts["total"] > 0),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _source(root, relative, label, expected=None):
    path = _file(root, relative)
    if path is None:
        return {
            "label": label,
            "unavailable": "Saved structure is missing or exceeds the preview size limit.",
        }, None
    if path.suffix.lower() not in {".pdb", ".cif", ".mmcif", ".pdbx"}:
        return {"label": label, "unavailable": "This source format cannot be displayed."}, None
    try:
        inventory = _inventory(path) if path.suffix.lower() == ".pdb" else None
        digest = inventory["sha256"] if inventory else hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, ValueError):
        return {
            "label": label,
            "unavailable": "Saved source became unavailable or changed during reading.",
        }, None
    if expected and digest != expected:
        return {
            "label": label,
            "unavailable": "Snapshot checksum differs from the preparation record.",
        }, None
    relative = path.relative_to(root).as_posix()
    row = {
        "label": label,
        "path": relative,
        "url": "/artifacts/" + quote(relative, safe="/"),
        "format": "pdb" if inventory else "cif",
        "sha256": digest,
        "counts": inventory["counts"] if inventory else None,
        "models": inventory["models"] if inventory else None,
        "ambiguous": inventory["ambiguous"] if inventory else True,
    }
    return row, inventory


def _changes(before, after, inventories):
    if before not in inventories or after not in inventories:
        return []
    a, b = inventories[before]["atoms"], inventories[after]["atoms"]
    rows = []
    for action, keys, stage in (
        ("added", b.keys() - a.keys(), after),
        ("absent", a.keys() - b.keys(), before),
    ):
        grouped, environment = {}, {}
        for key in sorted(keys):
            if key[3] in WATER | IONS:
                summary = environment.setdefault(key[3], {"atoms": 0, "residues": set()})
                summary["atoms"] += 1
                summary["residues"].add(key[:4])
                continue
            grouped.setdefault(key[:4], []).append(key[4] + (" alt " + key[5] if key[5] else ""))
        for name, summary in environment.items():
            identity = json.dumps([before, after, action, "environment", name])
            rows.append(
                {
                    "id": hashlib.sha256(identity.encode()).hexdigest()[:20],
                    "kind": "inventory",
                    "label": f"{name}: {summary['atoms']} atoms {action}",
                    "before": before,
                    "after": after,
                    "stage": stage,
                    "details": {
                        "atom_count": summary["atoms"],
                        "residue_count": len(summary["residues"]),
                    },
                    "evidence": (
                        "Grouped water/ion atom-identity difference; counts do not establish "
                        "the chemical cause or individual ion placement."
                    ),
                }
            )
        for residue, names in grouped.items():
            chain, number, insertion, resname = residue
            identity = json.dumps([before, after, action, residue])
            rows.append(
                {
                    "id": hashlib.sha256(identity.encode()).hexdigest()[:20],
                    "kind": "inventory",
                    "label": (
                        f"{resname} {chain or '_'}:{number}{insertion}: {len(names)} atoms {action}"
                    ),
                    "before": before,
                    "after": after,
                    "stage": stage,
                    "selection": {
                        "chain": chain,
                        "resseq": number,
                        "icode": insertion,
                        "resname": resname,
                    },
                    "atoms": names,
                    "evidence": (
                        "Observed atom identities differ. Renaming, numbering changes, "
                        "mutation, assembly selection or repair may all affect matching; "
                        "the difference alone does not establish its cause."
                    ),
                }
            )
    return rows


def audit_payload(root):
    root = Path(root).resolve()
    setup = _record(root, "setup/setup_parameters.json")
    journal = _record(root, "setup/preparation_audit.json")
    recorded = (
        journal.get("version") == 1
        and isinstance(journal.get("events"), list)
        and isinstance(journal.get("sources"), dict)
    )
    sources, inventories, events = {}, {}, []
    observed_changes = 0
    observed_limited = False
    if recorded:
        for identity, row in list(journal["sources"].items())[:1000]:
            if not isinstance(identity, str) or not isinstance(row, dict):
                continue
            relative = row.get("snapshot")
            if isinstance(relative, str) and relative.startswith("setup/audit/"):
                source, inventory = _source(
                    root, relative, str(row.get("label", identity))[:200], row.get("sha256")
                )
            else:
                source, inventory = (
                    {
                        "label": str(row.get("label", identity))[:200],
                        "unavailable": str(row.get("unavailable") or "Snapshot not recorded.")[
                            :500
                        ],
                    },
                    None,
                )
            sources[identity] = source
            if inventory:
                inventories[identity] = inventory
        for row in journal["events"][:500]:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                continue
            events.append(
                {
                    **row,
                    "kind": "recorded",
                    "label": str(row.get("operation", "Recorded operation"))
                    .replace("_", " ")
                    .capitalize()[:200],
                }
            )
            # Snapshot differences are observations at a recorded stage, not
            # proof that every changed identity was caused by that operation.
            if row.get("before") and row.get("after"):
                observed = _changes(row["before"], row["after"], inventories)
                allowance = max(0, 1000 - observed_changes)
                observed_limited |= len(observed) > allowance
                for change in observed[:allowance]:
                    change["id"] = row["id"] + "-" + change["id"]
                    change["recorded_event"] = row["id"]
                    change["operation"] = row.get("operation")
                    events.append(change)
                    observed_changes += 1
    else:
        for stage, relative in (
            ("input", "setup/input.pdb"),
            ("prepared", "setup/prepared.pdb"),
            ("system", "setup/topology.pdb"),
        ):
            if stage == "system" and not _file(root, relative):
                relative = "setup/solvated.pdb"
            label = {
                "input": "Saved setup input (may already reflect selection)",
                "prepared": "Prepared solute",
                "system": "Prepared system",
            }[stage]
            source, inventory = _source(root, relative, label)
            sources[stage] = source
            if inventory:
                inventories[stage] = inventory
        for before, after in (("input", "prepared"), ("prepared", "system")):
            events.extend(_changes(before, after, inventories))
        parameters = setup.get("parameters") if isinstance(setup.get("parameters"), dict) else {}
        original = setup.get("input") if isinstance(setup.get("input"), dict) else {}
        decisions = {
            "assembly": original.get("assembly"),
            "force_field": setup.get("resolved_forcefield"),
            "protonation_settings": {
                key: parameters[key]
                for key in ("ph", "residue_states", "protonation_margin")
                if key in parameters
            },
            "heterogens": setup.get("heterogen_decisions"),
            "notes": setup.get("notes"),
        }
        for name, evidence in decisions.items():
            events.append(
                {
                    "id": name,
                    "kind": "decision",
                    "label": name.replace("_", " ").capitalize(),
                    "evidence": evidence if evidence else "Not recorded in this study.",
                }
            )
    available = [identity for identity, row in sources.items() if row.get("url")]
    forcefield = setup.get("resolved_forcefield")
    ligand_record = forcefield.get("ligand") if isinstance(forcefield, dict) else None
    ligand_name = ligand_record.get("name") if isinstance(ligand_record, dict) else None
    ligand_names = ligand_name if isinstance(ligand_name, list) else [ligand_name] if isinstance(ligand_name, str) else []
    ligand_names = {name for name in ligand_names if isinstance(name, str) and name not in PROTEIN | WATER | IONS}
    for identity, row in sources.items():
        counts = row.get("counts")
        if not isinstance(counts, dict):
            continue
        counts["ligand"] = sum(count for name, count in inventories[identity]["component_counts"].items() if name in ligand_names)
        counts["other"] -= counts["ligand"]
        row["ligand_evidence"] = "Named in setup/resolved_forcefield.ligand; residue-name classification, not chemical validation." if ligand_names else "No recorded ligand names; unmatched components remain other."
    source_events = []
    for identity in available:
        source = sources[identity]
        source_events.append(
            {
                "id": "view-" + identity,
                "kind": "source",
                "label": source["label"],
                "after": identity,
                "counts": source.get("counts"),
                "sha256": source["sha256"],
                "evidence": (
                    "Inventory of this saved structure; this derived row is not a "
                    "recorded preparation operation."
                ),
            }
        )
    events = (
        source_events
        + [row for row in events if row["kind"] != "inventory"]
        + [row for row in events if row["kind"] == "inventory"]
    )
    decisions = []
    for row in events[:1000]:
        if row["kind"] not in {"recorded", "decision"}:
            continue
        details = row.get("details")
        details = details if isinstance(details, dict) else {}
        decisions.append({"event_id": row["id"], "choice": row["label"],
                          "requested": details.get("requested_settings", details.get("requested", "Not separately recorded")),
                          "resolved": details.get("resolved_forcefield", details.get("resolved", details or row.get("evidence"))),
                          "reason": row.get("reason") or "No chemical reason recorded; inventory changes alone do not establish one.",
                          "before": row.get("before"), "after": row.get("after"),
                          "status": "Recorded operation" if row["kind"] == "recorded" else "Historical evidence",
                          "source": "setup/preparation_audit.json" if recorded else "setup/setup_parameters.json"})
    affected = []
    for row in events[:1000]:
        selection = row.get("selection")
        if not isinstance(selection, dict):
            continue
        stage = row.get("stage")
        ambiguous = not stage or inventories.get(stage, {}).get("ambiguous", True)
        operation = str(row.get("operation") or "")
        category = ("Mutation stage" if "mutat" in operation else
                    "Repair stage" if "missing" in operation or "repair" in operation else
                    "Hydrogen/state stage" if "hydrogen" in operation or "proton" in operation else
                    "Component/assembly stage" if "heterogen" in operation or "assembly" in operation else
                    "Observed inventory difference")
        affected.append({"event_id": row["id"], "selection": selection, "stage": stage,
                         "category": category, "ambiguous": ambiguous,
                         "evidence": row["evidence"], "label": row["label"]})
    warnings = (
        journal.get("warnings", [])[:30]
        if recorded and isinstance(journal.get("warnings"), list)
        else []
    )
    if not recorded and _file(root, "setup/preparation_audit.json"):
        warnings.append(
            "The saved preparation journal is invalid; only historical evidence is displayed."
        )
    return {
        "ok": True,
        "study": str(root),
        "available": bool(available),
        "recorded": bool(recorded),
        "status": journal.get("status", "historical evidence only")
        if recorded
        else "historical evidence only",
        "warnings": warnings,
        "sources": sources,
        "changes": events[:1000],
        "decisions": decisions,
        "affected_residues": affected,
        "total_changes": len(events),
        "limited": len(events) > 1000 or observed_limited,
        "ambiguous": any(row["ambiguous"] for row in inventories.values()),
        "notice": (
            "Structures show their first saved model. Atom identity matching "
            "includes chain, residue number, insertion code, residue name, "
            "atom name and alternate location. Component counts are based on "
            "residue names; ligand counts require names in the saved resolved "
            "ligand parameterization record. Other components remain unclassified. "
            "Historical differences do not establish chemical causes."
            " This audit does not certify scientific suitability."
        ),
    }


def comparison_payload(root, before, after):
    import numpy as np

    payload = audit_payload(root)
    sources = payload["sources"]
    if before not in sources or after not in sources or before == after:
        return {"ok": False, "error": "Choose two different saved stages."}
    rows = [sources[key] for key in (before, after)]
    if any(
        not row.get("path") or row.get("format") != "pdb" or row.get("ambiguous") for row in rows
    ):
        return {
            "ok": False,
            "error": "Reliable atom correspondence is unavailable; use side-by-side views.",
        }
    root = Path(root).resolve()
    paths = [_file(root, row["path"]) for row in rows]
    if not all(paths):
        return {"ok": False, "error": "A comparison source changed or became unavailable."}
    inventories = [_inventory(path) for path in paths]
    if any(inventory["sha256"] != row["sha256"] for inventory, row in zip(inventories, rows)):
        return {"ok": False, "error": "A comparison source changed during alignment."}
    atoms = [row["atoms"] for row in inventories]
    keys = sorted(atoms[0].keys() & atoms[1].keys())
    keys = [
        key
        for key in keys
        if all(
            mapping[key]["xyz"] and mapping[key]["element"] not in {"", "H", "D"}
            for mapping in atoms
        )
    ][:5000]
    if len(keys) < 3:
        return {
            "ok": False,
            "error": (
                "At least three reliably matched heavy atoms are needed for a display alignment."
            ),
        }
    a, b = [np.array([mapping[key]["xyz"] for key in keys]) for mapping in atoms]
    ac, bc = a.mean(axis=0), b.mean(axis=0)
    if min(np.linalg.matrix_rank(a - ac), np.linalg.matrix_rank(b - bc)) < 2:
        return {
            "ok": False,
            "error": "Matched atoms are collinear; a unique display alignment is unavailable.",
        }
    u, _, vt = np.linalg.svd((b - bc).T @ (a - ac))
    rotation = u @ vt
    if np.linalg.det(rotation) < 0:
        u[:, -1] *= -1
        rotation = u @ vt
    translation = ac - bc @ rotation
    return {
        "ok": True,
        "study": str(root),
        "before": before,
        "after": after,
        "rotation": rotation.tolist(),
        "translation": translation.tolist(),
        "matched_atoms": len(keys),
        "notice": (
            "Display-only least-squares alignment of exact matching heavy-atom"
            " identities (up to 5000). Coordinates remain unchanged on disk; "
            "this is not a trajectory RMSD or a preparation-quality score."
        ),
    }


def selection_evidence(root, source, selected):
    payload = audit_payload(root)
    row = payload["sources"].get(source, {})
    if not row.get("path") or row.get("format") != "pdb" or row.get("ambiguous"):
        return {
            "available": False,
            "reason": "Unambiguous residue evidence is unavailable for this saved stage.",
        }
    path = _file(Path(root).resolve(), row["path"])
    if path is None:
        return {"available": False, "reason": "The saved stage is unavailable."}
    inventory = _inventory(path)
    if inventory["sha256"] != row["sha256"]:
        return {"available": False, "reason": "The saved stage changed during verification."}
    matches = [
        (key, atom)
        for key, atom in inventory["atoms"].items()
        if key[:4]
        == (
            selected.get("chain", ""),
            str(selected.get("resseq")),
            selected.get("icode", ""),
            selected.get("resname", ""),
        )
        and (not selected.get("atom") or key[4] == selected["atom"])
        and (not selected.get("altloc") or key[5] == selected["altloc"])
    ]
    return {
        "available": bool(matches),
        "source": source,
        "label": row["label"],
        "sha256": row["sha256"],
        "selection": selected,
        "atoms": [
            {"name": key[4], "altloc": key[5], "element": atom["element"]}
            for key, atom in matches[:200]
        ],
        "limited": len(matches) > 200,
        "notice": (
            "Saved-stage atom inventory; no causal motion or chemical-state "
            "inference is established by this selection."
        ),
    }
