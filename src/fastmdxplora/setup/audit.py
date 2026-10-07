"""Optional observations of setup operations.

The recorder is controlled by the validated setup configuration. It never
chooses a scientific setting, and a capture failure never replaces a setup
result or exception.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from fastmdxplora.utils.logging import get_logger

logger = get_logger("setup.audit")
MAX_SNAPSHOT = 16 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024


def residue_identity(residue):
    return {
        "chain": str(residue.chain.id),
        "chain_index": residue.chain.index,
        "resname": str(residue.name),
        "resseq": str(residue.id),
        "icode": str(getattr(residue, "insertionCode", "")),
    }


class CaptureStopped(Exception):
    """One capture given up (a limit reached, a file that moved): said as a
    warning in the record and the log by `_safe`, never raised past the
    recorder, so it is no refusal of the study."""


class PreparationRecorder:
    """Record bounded, immutable observations for one setup invocation."""

    def __init__(self, directory, *, enabled: bool = True):
        self.enabled = bool(enabled)
        self.root = Path(directory)
        self.previous = None
        self.bytes = 0
        self.cleared = False
        self.run_id = uuid.uuid4().hex
        self.record = {
            "version": 1,
            "run_id": self.run_id,
            "status": "recording",
            "events": [],
            "sources": {},
            "warnings": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "notice": (
                "Observed operations and saved choices. This record does not "
                "certify preparation quality. Hydrogen counts do not independently "
                "establish chemical protonation states."
            ),
        }

    def _warning(self, operation, exc):
        message = (
            f"Preparation audit capture incomplete at {operation} "
            f"({type(exc).__name__}). Scientific preparation continues under "
            "its existing rules."
        )
        self.record["status"] = "incomplete"
        if len(self.record["warnings"]) < 30:
            self.record["warnings"].append(message)
        logger.warning(message)

    def _folder(self):
        folder = self.root / "audit"
        if not self.cleared:
            # One preparation's snapshots in a setup folder: those of a
            # preparation this one replaces go with the record that named them.
            if folder.is_symlink() or folder.is_file():
                folder.unlink()
            elif folder.is_dir():
                shutil.rmtree(folder)
            self.cleared = True
        if not folder.resolve().is_relative_to(self.root.resolve()):
            raise CaptureStopped("Audit snapshots must stay inside setup")
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _save(self):
        self._folder()
        raw = json.dumps(self.record, ensure_ascii=False, indent=2).encode("utf-8")
        if len(raw) > 2_000_000:
            raise CaptureStopped("Audit record size limit")
        descriptor, name = tempfile.mkstemp(prefix=".preparation-audit-", dir=self.root)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.root / "preparation_audit.json")
        finally:
            Path(name).unlink(missing_ok=True)

    def _safe(self, operation, capture):
        if not self.enabled:
            return None
        try:
            result = capture()
            self._save()
            return result
        except Exception as exc:  # noqa: BLE001 - observations cannot alter setup
            self._warning(operation, exc)
            try:
                self._save()
            except Exception:  # noqa: BLE001 - retain the in-memory warning
                pass
            return None

    def _source(self, label, raw, *, format="pdb", counts=None):
        identity = f"source-{len(self.record['sources']) + 1:04d}"
        row = {
            "label": label,
            "format": format,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "counts": counts,
            "snapshot": None,
        }
        if len(raw) <= MAX_SNAPSHOT and self.bytes + len(raw) <= MAX_TOTAL:
            suffix = ".cif" if format == "mmcif" else ".pdb" if format == "pdb" else ".txt"
            snapshot = self._folder() / (identity + suffix)
            if snapshot.exists():
                raise CaptureStopped("Existing audit snapshot would be overwritten")
            with snapshot.open("xb") as stream:
                stream.write(raw)
            self.bytes += len(raw)
            row["snapshot"] = snapshot.relative_to(self.root.parent).as_posix()
        else:
            row["unavailable"] = "Snapshot storage limit. Checksum and counts remain recorded."
            self._warning(label, ValueError("snapshot size limit"))
        self.record["sources"][identity] = row
        return identity

    def _event(self, operation, after=None, details=None, *, reason=None):
        if len(self.record["events"]) >= 500:
            raise CaptureStopped("Audit event count limit")
        raw = json.dumps(details or {}, default=str, ensure_ascii=False)
        if len(raw.encode("utf-8")) > 200_000:
            details = {"unavailable": "Operation details exceed the record limit."}
            self._warning(operation, ValueError("details size limit"))
        else:
            details = json.loads(raw)
        identity = f"event-{len(self.record['events']) + 1:04d}"
        self.record["events"].append(
            {
                "id": identity,
                "order": len(self.record["events"]),
                "operation": operation,
                "before": self.previous,
                "after": after,
                "details": details,
                "reason": reason,
                "evidence": "Recorded at the existing preparation operation.",
            }
        )
        if after:
            self.previous = after
        return identity

    def capture_file(self, path, operation, *, details=None, reason=None):
        def capture():
            source = Path(path)
            before = source.stat()
            if before.st_size > MAX_SNAPSHOT:
                digest = hashlib.sha256()
                with source.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                identity = f"source-{len(self.record['sources']) + 1:04d}"
                self.record["sources"][identity] = {
                    "label": operation,
                    "sha256": digest.hexdigest(),
                    "bytes": before.st_size,
                    "snapshot": None,
                    "unavailable": (
                        "Source exceeds snapshot limit. Structural comparison is unavailable."
                    ),
                }
                self._warning(operation, ValueError("snapshot size limit"))
            else:
                raw = source.read_bytes()
                format = "mmcif" if source.suffix.lower() in {".cif", ".mmcif", ".pdbx"} else "pdb"
                identity = self._source(operation, raw, format=format)
            after = source.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise CaptureStopped("Source changed during capture")
            return self._event(operation, identity, details, reason=reason)

        return self._safe(operation, capture)

    def observe(self, operation, topology, positions, details=None):
        def capture():
            from openmm.app import PDBFile

            atoms = list(topology.atoms())
            counts = {
                "atoms": len(atoms),
                "residues": sum(1 for _ in topology.residues()),
                "bonds": sum(1 for _ in topology.bonds()),
                "hydrogens": sum(
                    getattr(atom.element, "symbol", "") == "H" for atom in atoms
                ),
            }
            counts["heavy_atoms"] = counts["atoms"] - counts["hydrogens"]
            if counts["atoms"] > 100_000:
                source = f"source-{len(self.record['sources']) + 1:04d}"
                self.record["sources"][source] = {
                    "label": operation,
                    "format": "pdb",
                    "snapshot": None,
                    "counts": counts,
                    "unavailable": "Topology exceeds snapshot atom limit. Counts remain recorded.",
                }
                self._warning(operation, ValueError("snapshot atom limit"))
            else:
                buffer = io.StringIO()
                PDBFile.writeFile(topology, positions, buffer, keepIds=True)
                source = self._source(
                    operation, buffer.getvalue().encode("utf-8"), counts=counts
                )
            for package in ("openmm", "pdbfixer"):
                try:
                    self.record.setdefault("backends", {})[package] = version(package)
                except PackageNotFoundError:
                    self.record.setdefault("backends", {})[package] = "Version unavailable"
            return self._event(operation, source, details)

        return self._safe(operation, capture)

    def stopped(self, exc):
        """The preparation stopped before its record was written: said, so
        the record never reads as still recording."""
        def capture():
            self.record["status"] = "stopped"
            self.record["stopped_by"] = str(getattr(exc, "code", None)
                                            or type(exc).__name__)

        return self._safe("stopped", capture)

    def decision(self, operation, details, *, reason=None):
        return self._safe(
            operation,
            lambda: self._event(operation, details=details, reason=reason),
        )

    def manifest(self, manifest):
        def capture():
            fields = {
                key: manifest.get(key)
                for key in (
                    "input",
                    "resolved_forcefield",
                    "heterogen_decisions",
                    "random_seed",
                    "box",
                    "resolved",
                    "notes",
                )
            }
            params = manifest.get("parameters") or {}
            fields["requested_settings"] = {
                key: params.get(key)
                for key in (
                    "ph",
                    "residue_states",
                    "mutations",
                    "mutation_chain",
                    "replace_nonstandard_residues",
                    "build_missing_termini",
                    "heterogens",
                    "keep_heterogens",
                    "keep_water",
                    "ligand_forcefield",
                    "ligand_net_charge",
                )
            }
            self._event(
                "recorded_setup_choices",
                details=fields,
                reason=(
                    "Existing setup_parameters.json values. Recording a choice "
                    "does not prove it completed."
                ),
            )
            if self.record["status"] != "incomplete":
                self.record["status"] = (
                    "complete"
                    if any(
                        event["operation"] == "prepared_system"
                        for event in self.record["events"]
                    )
                    else "partial"
                )

        return self._safe("recorded_setup_choices", capture)


def observe_fixer(recorder, operation, fixer, details=None):
    """Guard audit setup so backend failures retain their existing behavior."""
    if not isinstance(recorder, PreparationRecorder) or not recorder.enabled:
        return
    try:
        observed = details() if callable(details) else details
        observed = dict(observed or {})
        observed["requested_platform"] = (
            fixer.platform.getName()
            if fixer.platform is not None
            else "OpenMM automatic selection; actual device is not recorded here"
        )
        recorder.observe(operation, fixer.topology, fixer.positions, observed)
    except Exception as exc:  # noqa: BLE001 - audit cannot replace setup behavior
        recorder._warning(operation, exc)
