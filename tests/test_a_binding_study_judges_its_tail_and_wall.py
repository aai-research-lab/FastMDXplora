"""A binding study's reference, judged where the study is recombined.

`BatchExplorer._maybe_build_pmf` on windows sampled exactly from a known
well, W(r) = -15 + 30 (r - 0.8)^2 kJ/mol capped at 0, under a 30 degree
cone: the tail judged against the bootstrap's resampled curves, each
resample's number worked out without judging its tail, and the cone's wall
judged over the frames up to the cutoff the integral uses (1.46 nm, well
past 1.25 times the 0.8 nm minimum).

Each study is one seeded draw (frames and bootstrap), so every test here is
deterministic. Some margins are thin on purpose: a 1 kJ/mol tilt is caught
about seven times in ten, and the flat study's 0.5 kJ/mol is about two of
its standard deviations. A change that only reorders the random stream can
fail one of them by chance; redraw before reading it as a defect.
"""

from __future__ import annotations

import json
import logging

import numpy as np
import pytest

from fastmdxplora.simulation.binding import KB_KJMOL, STANDARD_VOLUME_NM3

KT = KB_KJMOL * 300.0
GRID = np.linspace(0.3, 2.4, 42001)


def _potential(radius, tilt: float = 0.0):
    """The well, and beyond 1.75 nm a fall of `tilt` kJ/mol to 2.15 nm."""
    well = np.minimum(0.0, -15.0 + 30.0 * (radius - 0.8) ** 2)
    return well - tilt * np.clip((radius - 1.75) / 0.4, 0.0, 1.0)


def _study(tmp_path, *, tilt: float = 0.0, bite: float = 0.0, seed: int = 0,
           count: int = 3000, resamples: int = 40, monkeypatch=None,
           refuse_the_reference: bool = False, wall_at: float = 1.35,
           unread: "int | None" = None, whole: bool = False) -> dict:
    """16 windows from 0.55 to 2.05 nm at 1000 kJ/mol/nm^2, `count` frames
    each; the wall's bias is `bite` on frames within 0.05 nm of `wall_at`,
    and window `unread` writes no `cone.bias` column. `whole` returns all of
    `pmf.json`, else its `binding`."""
    from fastmdxplora.batch.explorer import BatchExplorer
    from fastmdxplora.simulation.umbrella import Cone

    config = tmp_path / "study.yml"
    config.write_text(
        "output: bindingstudy\n"
        "systems:\n  - system: 181L\n"
        "simulation:\n  umbrella:\n"
        "    collective_variable: ligand_distance\n"
        '    site_selection: "resid 84 to 121 and name CA"\n'
        "    from: 0.55\n    to: 2.05\n    n_windows: 16\n"
        "    force_constant: 1000\n", encoding="utf-8")
    explorer = BatchExplorer(config=config, output_dir=str(tmp_path / "out"))
    cone = Cone(half_angle_deg=30.0).as_record()
    rng = np.random.default_rng(seed)
    free = _potential(GRID, tilt) - 2.0 * KT * np.log(GRID)
    for spec in explorer.run_specs:
        block = spec.options["simulation"]["umbrella"]
        block["ligand_resname"] = "LIG"
        run = explorer._run_output_dir(spec) / "simulation"
        run.mkdir(parents=True)
        energy = free + 0.5 * block["force_constant"] * (
            GRID - block["centre"]) ** 2
        weight = np.exp(-(energy - energy.min()) / KT)
        where = rng.choice(GRID, size=count, p=weight / weight.sum())
        wall = np.where(np.abs(where - wall_at) < 0.05, bite, 0.0)
        if block["index"] == unread:
            run.joinpath("COLVAR").write_text(
                "#! FIELDS time d\n" + "\n".join(
                    f"{i * 0.1:.1f} {r:.5f}" for i, r in enumerate(where)),
                encoding="utf-8")
        else:
            run.joinpath("COLVAR").write_text(
                "#! FIELDS time d cone.bias\n" + "\n".join(
                    f"{i * 0.1:.1f} {r:.5f} {b:.4f}"
                    for i, (r, b) in enumerate(zip(where, wall))),
                encoding="utf-8")
        run.joinpath("umbrella_window.json").write_text(json.dumps({
            "centre": block["centre"],
            "force_constant": block["force_constant"], "cone": cone}),
            encoding="utf-8")
    if refuse_the_reference:
        from fastmdxplora.simulation import reference_state

        monkeypatch.setattr(reference_state, "check_the_reference",
                            lambda *a, **k: {"refused": "Not open.",
                                             "warnings": []})
    logging.disable(logging.WARNING)
    try:
        explorer._maybe_build_pmf(bootstrap_resamples=resamples)
    finally:
        logging.disable(logging.NOTSET)
    saved = json.loads((tmp_path / "out" / "pmf.json").read_text(
        encoding="utf-8"), parse_constant=_not_strict_json)
    return saved if whole else saved["binding"]


def _not_strict_json(token: str):
    raise AssertionError(f"pmf.json holds {token}, which strict JSON refuses")


def _exact(cutoff: float) -> float:
    """Gilson et al. 1997: -kT ln(4 pi int r^2 e^(-W/kT) dr / V0) to the
    cutoff, plus the cone's kT ln(4 pi / Omega)."""
    from fastmdxplora.simulation.umbrella import Cone

    radius = GRID[GRID <= cutoff]
    constant = 4.0 * np.pi * np.trapezoid(
        radius ** 2 * np.exp(-_potential(radius) / KT), radius)
    return (-KT * float(np.log(constant / STANDARD_VOLUME_NM3))
            + Cone(half_angle_deg=30.0).correction_kjmol(300.0))


class TestTheStudyJudgesItsReference:
    def test_a_flat_tail_gives_the_exact_answer_judged_by_the_resamples(
            self, tmp_path):
        binding = _study(tmp_path)

        assert binding["refused"] is None
        assert binding["bulk_judged_by"] == "the bootstrap's resampled curves"
        assert binding["bulk_resamples_used"] >= 30
        assert binding["delta_g_kjmol"] == pytest.approx(
            _exact(binding["bound_cutoff_nm"]), abs=0.5)
        assert binding["cone_wall_checked_up_to_nm"] == pytest.approx(
            binding["bound_cutoff_nm"])
        assert binding["bound_cutoff_nm"] > 1.25 * binding["minimum_at_nm"]

    def test_a_tail_still_falling_by_1_kjmol_is_refused_by_its_tilt(
            self, tmp_path):
        binding = _study(tmp_path, tilt=1.0)

        assert binding["delta_g_kjmol"] is None
        assert "by a tilt of" in binding["refused"]
        assert binding["bulk_tilt_kjmol"] < -0.6

    def test_a_wall_biting_inside_the_cutoff_past_1_25_times_the_minimum(
            self, tmp_path):
        """1 kJ/mol at 1.30 to 1.40 nm, inside the 1.46 nm cutoff and out of
        reach of any window centred within 1.25 times the minimum."""
        binding = _study(tmp_path, bite=1.0)

        assert binding["delta_g_kjmol"] is None
        assert "The cone's wall carried" in binding["refused"]
        assert 8 in {b["window"] for b in binding["cone_wall_bit_in"]}

    def test_few_frames_a_window_still_give_an_interval_from_every_resample(
            self, tmp_path):
        """300 frames a window: a resample's tail departs by about 1 kJ/mol
        by noise, so judged on its own almost every resample was refused
        and the interval was made of the flattest few."""
        binding = _study(tmp_path, count=300, seed=1)

        assert binding["refused"] is None
        assert binding["delta_g_resamples_without_a_number"] <= 4
        low, high = binding["delta_g_confidence_kjmol"]
        assert high - low > 1.0
        assert low <= _exact(binding["bound_cutoff_nm"]) <= high

    def test_a_reference_refusal_saves_how_the_tail_and_wall_were_judged(
            self, tmp_path, monkeypatch):
        binding = _study(tmp_path, monkeypatch=monkeypatch,
                         refuse_the_reference=True)

        assert binding["delta_g_kjmol"] is None
        assert binding["refused"] == "Not open."
        assert binding["bulk_judged_by"] == "the bootstrap's resampled curves"
        for key in ("bulk_judged", "bulk_tilt_kjmol", "bulk_tilt_allowed_kjmol",
                    "bulk_resamples_used", "cone_wall_checked_up_to_nm"):
            assert binding[key] is not None, key
        assert binding["cone_wall_not_read_in"] == []


class TestWhatARefusalSaysAndSaves:
    def test_the_wall_refusal_says_every_window_runs_again(self, tmp_path):
        """The windows of one study share one cone, so running the named
        windows again under a wider one is refused after they have run."""
        binding = _study(tmp_path, bite=1.0)

        assert binding["delta_g_kjmol"] is None
        assert "Every window then runs again under the new cone" in (
            binding["refused"])
        assert "--force-overwrite" in binding["refused"]
        assert "`--rerun-window` cannot do it" in binding["refused"]

    def test_a_wall_refusal_saves_how_the_tail_and_wall_were_judged(
            self, tmp_path):
        binding = _study(tmp_path, bite=1.0)

        assert binding["bulk_judged_by"] == "the bootstrap's resampled curves"
        for key in ("bulk_allowed_kjmol", "bulk_worst_at_nm",
                    "bulk_tilt_kjmol", "bulk_tilt_allowed_kjmol",
                    "bulk_resamples_used", "minimum_at_nm"):
            assert binding[key] is not None, key
        assert 8 in binding["cone_wall_windows"]

    def test_an_unread_window_is_saved_as_null_not_nan(self, tmp_path):
        """Window 8 (1.35 nm) samples inside the cutoff with no `cone.bias`
        column: refused, named, and saved as strict JSON."""
        binding = _study(tmp_path, unread=8)

        assert binding["delta_g_kjmol"] is None
        assert binding["cone_wall_not_read_in"] == [8]
        row = next(r for r in binding["cone_wall_judged"] if r["window"] == 8)
        assert row["bias_kjmol"] is None and row["read"] is False
        assert binding["bulk_judged_by"] == "the bootstrap's resampled curves"

    def test_an_infinite_wall_is_saved_as_null_and_said(self, tmp_path):
        binding = _study(tmp_path, bite=float("inf"))

        assert binding["delta_g_kjmol"] is None
        assert binding["cone_wall_bias_kjmol"] is None
        assert binding["cone_wall_infinite"] is True
        assert all(row["infinite"] for row in binding["cone_wall_bit_in"])

    def test_a_refused_binding_leaves_no_number_in_derived(self, tmp_path):
        saved = _study(tmp_path, tilt=1.0, whole=True)

        assert saved["binding"]["delta_g_kjmol"] is None
        assert saved["derived"]["value"] is None
        assert "binding.refused" in saved["derived"]["note"]

    def test_a_reference_refusal_saves_the_windows_and_the_allowance(
            self, tmp_path, monkeypatch):
        binding = _study(tmp_path, monkeypatch=monkeypatch,
                         refuse_the_reference=True)

        assert binding["refused"] == "Not open."
        assert binding["bulk_allowed_kjmol"] is not None
        assert binding["bulk_worst_at_nm"] is not None
        assert binding["cone_wall_windows"]
        assert binding["cone_wall_judged"]
        assert binding["cone_wall_bit_in"] == []

    def test_no_bootstrap_says_the_tilt_was_not_judged(self, tmp_path,
                                                        capsys):
        binding = _study(tmp_path, resamples=0)

        assert binding["delta_g_kjmol"] is not None
        assert binding["bulk_judged"].endswith(
            "since the study ran no bootstrap (`bootstrap_resamples: 0`)")
        assert ("The tail was judged against 0.6 kJ/mol alone and its tilt "
                "was not judged: the study ran no bootstrap "
                "(bootstrap_resamples: 0).") in capsys.readouterr().out
