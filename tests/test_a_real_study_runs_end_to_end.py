"""A real study, really run, on the CPU platform.

Everything about segmentation, cost recording and calibration has been
tested against stubs and synthetic series. This runs an actual solvated
peptide through the actual pipeline -- PDBFixer, a real force field, PME,
constraints, a barostat -- and checks the three things that only a real
run can establish:

  - `cost.json` is written, with the particle and step counts that
    produced the time rather than plausible-looking numbers;
  - a segment after the first resumes from its predecessor's checkpoint
    and does not silently start over;
  - `calibrate_from_runs` fits across real runs and agrees with itself;
  - a two-segment study resumes and the log says so.

The last one was left out of the first version of this file, because the
configuration that exercised it went unstable and a test that is sometimes
right is worse than none. The instability turned out to be the bug: the
config's `resume_from` never reached the runner, so the second segment
started from the pre-equilibration state without minimising. With that
fixed the run is stable and the check belongs here.

Slow by the standards of this suite -- about a minute -- and skipped
without OpenMM and PDBFixer. That cost buys the only evidence here that
the whole path works rather than that its pieces do.

The peptide is tri-alanine, written inline rather than fetched, so the
test needs no network and no data file.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

try:
    import openmm  # noqa: F401
    import pdbfixer  # noqa: F401

    HAS_BACKENDS = True
except ImportError:  # pragma: no cover
    HAS_BACKENDS = False

import pytest

TRI_ALANINE = """\
ATOM      1  N   ALA A   1      -0.677   1.230  -0.491  1.00  0.00           N
ATOM      2  CA  ALA A   1      -0.001   0.064  -1.056  1.00  0.00           C
ATOM      3  CB  ALA A   1       1.472   0.129  -0.746  1.00  0.00           C
ATOM      4  C   ALA A   1      -0.606  -1.219  -0.518  1.00  0.00           C
ATOM      5  O   ALA A   1      -1.797  -1.371  -0.301  1.00  0.00           O
ATOM      6  N   ALA A   2       0.253  -2.190  -0.290  1.00  0.00           N
ATOM      7  CA  ALA A   2      -0.152  -3.456   0.260  1.00  0.00           C
ATOM      8  CB  ALA A   2       0.229  -4.560  -0.713  1.00  0.00           C
ATOM      9  C   ALA A   2       0.564  -3.752   1.570  1.00  0.00           C
ATOM     10  O   ALA A   2       1.782  -3.694   1.687  1.00  0.00           O
ATOM     11  N   ALA A   3      -0.219  -4.078   2.590  1.00  0.00           N
ATOM     12  CA  ALA A   3       0.298  -4.393   3.910  1.00  0.00           C
ATOM     13  CB  ALA A   3      -0.844  -4.629   4.880  1.00  0.00           C
ATOM     14  C   ALA A   3       1.200  -3.283   4.420  1.00  0.00           C
ATOM     15  O   ALA A   3       0.865  -2.104   4.340  1.00  0.00           O
ATOM     16  OXT ALA A   3       2.300  -3.600   4.930  1.00  0.00           O
TER
END
"""


@pytest.mark.slow
@unittest.skipUnless(HAS_BACKENDS, "OpenMM and PDBFixer are needed")
class TestARealStudy(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import logging

        from fastmdxplora import FastMDXplora

        # `explore()` turns propagation off on the `fastmdx` logger and
        # does not turn it back on. The CLI restores it; the API does not,
        # so anything that calls explore() in-process has its logging
        # quietly changed for the rest of the session. Worth knowing about
        # outside the tests, and worth restoring here regardless --
        # TestInvokingTheCLILeavesTheSuiteAsItFoundIt guards this and
        # caught it.
        cls._propagate = logging.getLogger("fastmdx").propagate

        cls.root = pathlib.Path(tempfile.mkdtemp())
        cls.pdb = cls.root / "tri-ala.pdb"
        cls.pdb.write_text(TRI_ALANINE)
        # Padding and cutoff chosen so the cutoff is under half the box.
        # The smaller values this started with were refused, correctly, by
        # the minimum-image check -- which is itself the guardrail working
        # on a real system rather than a fixture.
        cls.config = {
            "systems": [{"id": "tri", "system": str(cls.pdb)}],
            "setup": {"ph": 7.0, "solvent_padding_nm": 1.2,
                      "nonbonded_cutoff_nm": 0.9},
            "simulation": {"platform": "CPU", "production_steps": 300,
                           "nvt_steps": 100, "npt_steps": 100,
                           "timestep_fs": 2,
                           "trajectory_interval_steps": 50},
            "include": ["setup", "simulation"],
        }
        cls.output = cls.root / "whole"
        FastMDXplora(config_data=cls.config,
                     output_dir=str(cls.output)).explore()

    @classmethod
    def tearDownClass(cls):
        import logging

        logging.getLogger("fastmdx").propagate = cls._propagate

    def cost_record(self, output):
        records = list(pathlib.Path(output).rglob("cost.json"))
        self.assertEqual(len(records), 1, "expected one cost record")
        return json.loads(records[0].read_text())

    def test_a_finished_run_records_what_it_cost(self):
        cost = self.cost_record(self.output)
        # 500 steps: 100 NVT, 100 NPT, 300 production. Counted rather than
        # assumed, because the whole point of the record is that it says
        # what actually ran.
        self.assertEqual(cost["steps"], 500)
        self.assertGreater(cost["particles"], 1000)
        self.assertGreater(cost["seconds"], 0.0)
        self.assertEqual(cost["platform"], "CPU")

    def test_the_resolved_config_names_the_steps_that_ran(self):
        """The whole chain, on a real run: the runner works the step plan
        out, the phase records it, and the resolved config carries it.

        Nothing wrote the plan down before. A study asking for a duration
        left `production_steps: null`, so replaying it derived the count
        again -- from whichever version was replaying, and from a timestep
        the file happened to state. Here the counts are known: 100 NVT,
        100 NPT, 300 production.
        """
        import yaml

        doc = yaml.safe_load(
            (self.output / "resolved_config.yml").read_text(encoding="utf-8"))
        simulation = doc["simulation"]
        self.assertEqual(simulation["nvt_steps"], 100)
        self.assertEqual(simulation["npt_steps"], 100)
        self.assertEqual(simulation["production_steps"], 300)
        self.assertIsNotNone(simulation["trajectory_interval_steps"])
        self.assertIsNotNone(simulation["pressure_bar"])

    def test_the_resolved_config_names_what_setup_chose(self):
        """`forcefield: auto` names a family whose membership is a
        property of the release, not of the study."""
        import yaml

        doc = yaml.safe_load(
            (self.output / "resolved_config.yml").read_text(encoding="utf-8"))
        self.assertTrue(doc["setup"]["force_field"],
                        "the XMLs `auto` resolved to")
        self.assertTrue(doc["setup"]["water_model"])

    def test_the_resolved_config_is_still_a_config(self):
        """Every setting a run decided, written back, and the file still
        passes the validation any config passes. A resolution written
        under a name the schema does not know would fail here."""
        from fastmdxplora.config.loader import (
            load_config_file, normalise_config, validate_config,
        )

        data = normalise_config(
            load_config_file(self.output / "resolved_config.yml"))
        validate_config(data, require_systems=True)

    def test_no_private_key_reached_the_resolved_config(self):
        """Setup injects private keys into its own parameters. They are
        not settings, and a config carrying one is refused on replay."""
        import yaml

        text = (self.output / "resolved_config.yml").read_text(encoding="utf-8")
        doc = yaml.safe_load(text)
        for phase in ("setup", "simulation", "analysis", "report"):
            for name in doc.get(phase) or {}:
                self.assertFalse(
                    name.startswith("_"),
                    f"{phase}.{name} is internal and should not be here")

    def test_the_checkpoint_is_written_and_sealed(self):
        simulation = self.output / "simulation"
        checkpoint = simulation / "checkpoint.chk"
        self.assertTrue(checkpoint.is_file())
        from fastmdxplora.simulation.runner import verify_checkpoint

        self.assertTrue(verify_checkpoint(checkpoint, require_seal=True))

    def test_the_seal_catches_a_real_truncation(self):
        from fastmdxplora.refusals import UnstableRun, refusal_of
        from fastmdxplora.simulation.runner import verify_checkpoint

        original = self.output / "simulation" / "checkpoint.chk"
        copy = self.root / "truncated.chk"
        copy.write_bytes(original.read_bytes())
        (self.root / "truncated.chk.sha256").write_text(
            (self.output / "simulation"
             / "checkpoint.chk.sha256").read_text())
        copy.write_bytes(original.read_bytes()[: -100])
        with self.assertRaises(UnstableRun) as caught:
            verify_checkpoint(copy)
        self.assertEqual(refusal_of(caught.exception).code,
                         "simulation.resume.checkpoint_truncated")

    def test_the_cost_record_calibrates_this_machine(self):
        from fastmdxplora.cost import calibrate_from_runs, estimate_seconds

        fit = calibrate_from_runs(self.output, platform_name="CPU",
                                  precision="mixed",
                                  path=self.root / "cal.json")
        self.assertEqual(fit.runs, 1)
        cost = self.cost_record(self.output)
        # The estimate for the run that produced the calibration must be
        # the run. A fit that does not reproduce its own input is fitting
        # something else.
        estimate = estimate_seconds(
            particles=cost["particles"], steps=cost["steps"],
            platform_name="CPU", precision="mixed",
            path=self.root / "cal.json")
        self.assertAlmostEqual(estimate.seconds, cost["seconds"], places=4)


    def test_a_second_segment_resumes_rather_than_starting_over(self):
        # The check the first version of this file left out. A config
        # option can validate and be ignored, and the only outward sign
        # here was a run going unstable -- which was luck. At a sensible
        # density it would have produced a plausible trajectory that was
        # not the study anybody asked for.
        import logging

        from fastmdxplora import FastMDXplora
        from fastmdxplora.simulation.resume import plan_segments

        segments = plan_segments(self.config, segments=2)
        directories = []
        records = []

        # The runner's own logger. It is "fastmdx.simulation.runner" --
        # the package name, not the distribution name. A handler on root
        # configures handlers of its own, and a handler on root saw
        # nothing -- which cost a test run to discover.
        watched = logging.getLogger("fastmdx.simulation.runner")
        handler = logging.Handler()
        handler.emit = lambda record: records.append(record.getMessage())
        watched.addHandler(handler)
        # Restored below. A test that raises a logger's level and leaves it
        # raised changes what every later test sees, and
        # TestInvokingTheCLILeavesTheSuiteAsItFoundIt exists to catch
        # exactly that -- it caught this.
        previous_level = watched.level
        watched.setLevel(logging.INFO)
        try:
            for segment in segments:
                config = dict(segment.config)
                directory = self.root / f"seg{segment.index}"
                if segment.index > 0:
                    config["include"] = ["simulation"]
                    config["simulation"] = dict(config["simulation"])
                    config["simulation"]["resume_from"] = str(
                        directories[-1] / "simulation" / "checkpoint.chk")
                    config["simulation"]["setup_from"] = str(
                        directories[0] / "setup")
                FastMDXplora(config_data=config,
                             output_dir=str(directory)).explore()
                directories.append(directory)
        finally:
            watched.removeHandler(handler)
            watched.setLevel(previous_level)
            logging.getLogger("fastmdx").propagate = self._propagate

        self.assertTrue(
            any("Resumed from" in message for message in records),
            "the second segment did not resume; `resume_from` validated "
            "and was ignored")

        # And it produced a run rather than a refusal.
        for directory in directories:
            with self.subTest(directory=directory.name):
                self.assertTrue(
                    (directory / "simulation" / "cost.json").is_file())

if __name__ == "__main__":  # pragma: no cover
    unittest.main()
