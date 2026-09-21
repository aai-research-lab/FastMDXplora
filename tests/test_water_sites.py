"""Waters that stay put, and the difference between a site and a molecule.

Most water in a simulation is bulk. Some positions are occupied throughout --
a water wedged between a ligand and a backbone carbonyl, bridging a hydrogen
bond neither could make alone. Displacing one costs entropy or gains affinity,
so which kind it is matters.
"""

from __future__ import annotations

import numpy as np
import pytest


def _system(n_waters=12):
    import mdtraj as md

    top = md.Topology()
    chain = top.add_chain()
    residue = top.add_residue("ALA", chain, resSeq=1)
    for name in ("N", "CA", "C", "O"):
        top.add_atom(name, md.element.carbon, residue)
    waters = top.add_chain()
    for index in range(n_waters):
        water = top.add_residue("HOH", waters, resSeq=100 + index)
        for name, element in (("O", md.element.oxygen),
                              ("H1", md.element.hydrogen),
                              ("H2", md.element.hydrogen)):
            top.add_atom(name, element, water)
    return top


def _trajectory(top, occupant_of_frame, n_frames=60, seed=0):
    """A trajectory where `occupant_of_frame(f)` says which water sits in the
    site that frame, and the rest are bulk."""
    import mdtraj as md

    rng = np.random.RandomState(seed)
    n_waters = (top.n_atoms - 4) // 3
    xyz = np.zeros((n_frames, top.n_atoms, 3), dtype=np.float32)
    for frame in range(n_frames):
        xyz[frame, :4] = [[0, 0, 0], [0.15, 0, 0], [0.3, 0, 0], [0.3, 0.12, 0]]
        occupant = occupant_of_frame(frame)
        for index in range(n_waters):
            base = 4 + index * 3
            if index == occupant:
                point = np.array([0.15, 0.30, 0.0]) + rng.normal(scale=0.02, size=3)
            else:
                point = rng.uniform(-3, 3, size=3)
            xyz[frame, base] = point
            xyz[frame, base + 1] = point + [0.01, 0, 0]
            xyz[frame, base + 2] = point + [0, 0.01, 0]

    traj = md.Trajectory(xyz=xyz, topology=top)
    traj.unitcell_lengths = np.tile([8.0, 8.0, 8.0], (n_frames, 1))
    traj.unitcell_angles = np.tile([90.0, 90.0, 90.0], (n_frames, 1))
    return traj


def _sites(traj, tmp_path, **kwargs):
    from fastmdxplora.analysis.water_sites import WaterSites

    return WaterSites(output_dir=str(tmp_path), site_selection="name CA",
                      cutoff_nm=0.6, **kwargs).compute(traj)


class TestTellingASiteFromAMolecule:
    """A cluster occupied in ninety per cent of frames is either one water
    that stayed or a position many waters passed through. Those are different
    findings with different consequences, and occupancy alone cannot tell
    them apart.
    """

    def test_one_water_that_stays_is_reported_as_bound(self, tmp_path) -> None:
        found = _sites(_trajectory(_system(), lambda frame: 0), tmp_path)
        assert len(found) == 1
        row = found.iloc[0]
        assert row["n_distinct_waters"] == 1
        assert "bound" in row["interpretation"]

    def test_many_waters_passing_through_is_reported_as_a_position(
            self, tmp_path) -> None:
        """Same occupancy, different finding: nothing is held there."""
        found = _sites(
            _trajectory(_system(), lambda frame: (frame // 5) % 12), tmp_path)
        assert len(found) == 1
        row = found.iloc[0]
        assert row["n_distinct_waters"] > 1
        assert "passed through" in row["interpretation"]

    def test_the_two_cases_share_an_occupancy(self, tmp_path) -> None:
        """Which is the point. If occupancy separated them there would be
        nothing to distinguish."""
        bound = _sites(_trajectory(_system(), lambda f: 0), tmp_path)
        passing = _sites(
            _trajectory(_system(), lambda f: (f // 5) % 12), tmp_path)
        assert bound.iloc[0]["occupancy"] == passing.iloc[0]["occupancy"]
        assert bound.iloc[0]["interpretation"] != passing.iloc[0]["interpretation"]

    def test_how_long_the_longest_stay_was(self, tmp_path) -> None:
        found = _sites(
            _trajectory(_system(), lambda f: (f // 5) % 12), tmp_path)
        assert found.iloc[0]["longest_stay_frames"] == 5


class TestBulkIsNotASite:
    """Bulk water clusters beautifully and means nothing: with enough frames
    every position in the box has been occupied."""

    def test_wandering_water_produces_no_site(self, tmp_path) -> None:
        found = _sites(
            _trajectory(_system(), lambda frame: -1), tmp_path)
        assert found.empty

    def test_the_cutoff_is_what_makes_it_about_the_protein(self) -> None:
        import inspect

        from fastmdxplora.analysis import water_sites

        assert "cutoff_nm" in inspect.signature(
            water_sites.WaterSites.__init__).parameters
        assert "bulk" in inspect.getdoc(water_sites).lower()


class TestWhatItRefuses:
    def test_a_system_with_no_water_is_refused(self, tmp_path) -> None:
        import mdtraj as md

        from fastmdxplora.analysis.water_sites import WaterSites

        top = md.Topology()
        residue = top.add_residue("ALA", top.add_chain(), resSeq=1)
        for name in ("N", "CA", "C", "O"):
            top.add_atom(name, md.element.carbon, residue)
        traj = md.Trajectory(xyz=np.zeros((2, 4, 3), dtype=np.float32),
                             topology=top)

        with pytest.raises(ValueError, match="No water was found"):
            WaterSites(output_dir=str(tmp_path)).compute(traj)

    def test_a_site_selection_matching_nothing_is_refused(self, tmp_path) -> None:
        from fastmdxplora.analysis.water_sites import WaterSites

        traj = _trajectory(_system(), lambda frame: 0, n_frames=5)
        with pytest.raises(ValueError, match="matched no atoms"):
            WaterSites(output_dir=str(tmp_path),
                       site_selection="resname NOPE").compute(traj)

    def test_nothing_near_the_site_says_so(self, tmp_path) -> None:
        """Rather than returning an empty table that looks like a finding."""
        from fastmdxplora.analysis.water_sites import WaterSites

        analysis = WaterSites(output_dir=str(tmp_path),
                              site_selection="name CA", cutoff_nm=0.01)
        found = analysis.compute(_trajectory(_system(), lambda f: 0))
        assert found.empty
        assert "not_found" in analysis.findings


class TestItIsRegistered:
    def test_it_is_an_analysis_like_any_other(self) -> None:
        import fastmdxplora.analysis  # noqa: F401
        from fastmdxplora.analysis.orchestrator import (
            available_analyses,
            get_analysis_class,
        )

        assert "water_sites" in available_analyses()
        assert get_analysis_class("water_sites") is not None

    def test_it_does_not_take_a_general_selection(self) -> None:
        """It works out its own atoms from the site selection, so a general
        one has nothing to apply to."""
        from fastmdxplora.analysis.water_sites import WaterSites

        assert WaterSites.honours_selection is False

    def test_it_records_how_the_site_was_chosen(self, tmp_path) -> None:
        from fastmdxplora.analysis.water_sites import WaterSites

        analysis = WaterSites(output_dir=str(tmp_path), site_selection="name CA")
        analysis.compute(_trajectory(_system(), lambda f: 0, n_frames=10))
        assert analysis.findings["site"]["selection"] == "name CA"
        assert analysis.findings["site"]["chosen_because"] == "given"


def test_finding_nothing_is_a_finding_not_a_crash(tmp_path) -> None:
    """Water came near the site and no position was held often enough. The
    first version built the empty result a different way from the full one, so
    it had no columns and raised when sorted -- turning "nothing persists"
    into a traceback.
    """
    from fastmdxplora.analysis.water_sites import WaterSites

    analysis = WaterSites(output_dir=str(tmp_path), site_selection="name CA",
                          cutoff_nm=0.6, minimum_occupancy=0.9)
    # Occupied in half the frames, which is below the bar: the first version
    # of this test put a water there every frame, so occupancy was 1.0 and a
    # site was found -- the test did not exercise the path it was written for.
    found = analysis.compute(
        _trajectory(_system(), lambda frame: 0 if frame % 2 else -1,
                    n_frames=30))

    assert list(found.columns) == list(WaterSites.COLUMNS), (
        "an empty result should have the same shape as a full one"
    )
    assert "not_found" in analysis.findings


def test_both_empty_paths_produce_the_same_shape(tmp_path) -> None:
    """There are two ways to find nothing -- no water came near at all, and
    water came near but nothing was held. They were built separately, which is
    how one of them lost its columns. Each is reached here, and known to be
    reached by the reason it gives."""
    from fastmdxplora.analysis.water_sites import WaterSites

    never_near = WaterSites(output_dir=str(tmp_path / "a"), site_selection="name CA",
                            cutoff_nm=0.01)
    none_near = never_near.compute(_trajectory(_system(), lambda frame: -1, n_frames=30))
    assert never_near.findings["not_found"].startswith("No water came within")

    not_held = WaterSites(output_dir=str(tmp_path / "b"), site_selection="name CA",
                          cutoff_nm=0.6, minimum_occupancy=0.9)
    passing = not_held.compute(
        _trajectory(_system(), lambda frame: 0 if frame % 2 else -1, n_frames=30))
    assert not_held.findings["not_found"].startswith("Water came near")

    assert list(none_near.columns) == list(passing.columns) == list(WaterSites.COLUMNS)

def test_it_says_when_it_chose_the_site_itself(tmp_path) -> None:
    """"auto" is a value, not an absence. A truthiness check called it
    "given", so the record claimed the user had chosen what the software had
    -- which is the kind of provenance error that is invisible afterwards.
    """
    from fastmdxplora.analysis.water_sites import WaterSites

    traj = _trajectory(_system(), lambda frame: 0, n_frames=10)

    automatic = WaterSites(output_dir=str(tmp_path / "a"), cutoff_nm=0.6)
    automatic.compute(traj)
    assert automatic.findings["site"]["chosen_because"].startswith("auto")

    given = WaterSites(output_dir=str(tmp_path / "b"),
                       site_selection="name CA", cutoff_nm=0.6)
    given.compute(traj)
    assert given.findings["site"]["chosen_because"] == "given"


def _shell(top, n_frames=80, seed=3, with_pinned=False):
    """A hydration shell around the protein -- the case a real run produced
    and a constructed one did not."""
    import mdtraj as md

    rng = np.random.RandomState(seed)
    n_waters = (top.n_atoms - 4) // 3
    xyz = np.zeros((n_frames, top.n_atoms, 3), dtype=np.float32)
    for frame in range(n_frames):
        xyz[frame, :4] = [[0, 0, 0], [0.15, 0, 0], [0.3, 0, 0], [0.3, 0.12, 0]]
        for index in range(n_waters):
            base = 4 + index * 3
            if with_pinned and index == 0:
                # Clear of the shell, so it is a site rather than part of one.
                point = np.array([0.15, 0.18, 0.0]) + rng.normal(scale=0.015, size=3)
            else:
                direction = rng.normal(size=3)
                direction /= np.linalg.norm(direction)
                point = np.array([0.15, 0, 0]) + direction * rng.uniform(0.30, 0.45)
            xyz[frame, base] = point
            xyz[frame, base + 1] = point + [0.01, 0, 0]
            xyz[frame, base + 2] = point + [0, 0.01, 0]
    traj = md.Trajectory(xyz=xyz, topology=top)
    traj.unitcell_lengths = np.tile([8.0, 8.0, 8.0], (n_frames, 1))
    traj.unitcell_angles = np.tile([90.0, 90.0, 90.0], (n_frames, 1))
    return traj


class TestAHydrationShellIsNotASite:
    """On ubiquitin the first shell chained into a single cluster: forty-eight
    thousand positions and eight hundred and fifty-four distinct waters,
    reported as one site occupied in every frame and described as "mostly one
    molecule".

    Clustering links neighbours through neighbours, so a surface becomes one
    object. A site is a position; it has to be compact.
    """

    def test_a_shell_produces_no_site(self, tmp_path) -> None:
        from fastmdxplora.analysis.water_sites import WaterSites

        analysis = WaterSites(output_dir=str(tmp_path), site_selection="name CA",
                              cutoff_nm=0.5)
        found = analysis.compute(_shell(_system(n_waters=60)))
        assert found.empty

    def test_and_says_it_rejected_a_surface_rather_than_finding_nothing(
            self, tmp_path) -> None:
        """Those are different answers: the second says the settings may be
        wrong for this system, not that the system has no sites."""
        from fastmdxplora.analysis.water_sites import WaterSites

        analysis = WaterSites(output_dir=str(tmp_path), site_selection="name CA",
                              cutoff_nm=0.5)
        analysis.compute(_shell(_system(n_waters=60)))

        assert analysis.findings["rejected_as_too_spread_out"] >= 1
        assert "surface hydration" in analysis.findings["not_found"]
        assert "site_selection" in analysis.findings["not_found"], (
            "and what to do about it"
        )


    def test_the_method_cannot_separate_a_site_inside_a_shell(self, tmp_path) -> None:
        """Which is a limit worth stating rather than working around.

        Clustering links neighbours through neighbours, so a water held
        against the protein is chained to the shell around it whenever the two
        are within an eps of each other. No threshold separates them: the
        answer is a narrower scope, not a cleverer cutoff.
        """
        from fastmdxplora.analysis.water_sites import WaterSites

        analysis = WaterSites(output_dir=str(tmp_path), site_selection="name CA",
                              cutoff_nm=0.5)
        found = analysis.compute(
            _shell(_system(n_waters=60), with_pinned=True))

        # The pinned water is real and is not separable at this scope.
        assert found.empty
        assert analysis.findings["rejected_as_too_spread_out"] >= 1


def test_one_long_stay_does_not_make_a_cluster_one_molecule(tmp_path) -> None:
    """Eight hundred and fifty-four waters were described as "mostly one
    molecule, exchanging occasionally", because one of them happened to have a
    run of a hundred frames. Dominance has to be measured over the whole
    cluster, not from the longest run in it.

    Here one water never leaves, and three others crowd in beside it every
    frame, taken in turn from the rest: the longest run there could be, and a
    quarter of the observations. That is a favoured position."""
    from fastmdxplora.analysis.water_sites import WaterSites

    n_waters, n_frames = 40, 60
    top = _system(n_waters)
    rng = np.random.RandomState(7)
    xyz = np.zeros((n_frames, top.n_atoms, 3), dtype=np.float32)
    for frame in range(n_frames):
        xyz[frame, :4] = [[0, 0, 0], [0.15, 0, 0], [0.3, 0, 0], [0.3, 0.12, 0]]
        crowd = {0} | {1 + (3 * frame + k) % (n_waters - 1) for k in range(3)}
        for index in range(n_waters):
            point = (np.array([0.15, 0.30, 0.0]) + rng.normal(scale=0.02, size=3)
                     if index in crowd else rng.uniform(-3, 3, size=3))
            base = 4 + index * 3
            xyz[frame, base:base + 3] = [point, point + [0.01, 0, 0], point + [0, 0.01, 0]]
    import mdtraj as md
    traj = md.Trajectory(xyz=xyz, topology=top)
    traj.unitcell_lengths = np.tile([8.0, 8.0, 8.0], (n_frames, 1))
    traj.unitcell_angles = np.tile([90.0, 90.0, 90.0], (n_frames, 1))

    found = WaterSites(output_dir=str(tmp_path), site_selection="name CA",
                       cutoff_nm=0.6, minimum_occupancy=0.5).compute(traj)
    assert len(found) >= 1
    described = list(found["interpretation"])
    assert not any(d.startswith("mostly one molecule") for d in described), described
    assert any(d.startswith("a favoured position") for d in described), described

class TestARunTooShortToShowResidence:
    """Ubiquitin over ten picoseconds produced a site occupied in every frame
    and called it "one molecule, bound".

    Water on a protein surface exchanges on ten to a hundred picoseconds. A
    site fully occupied through ten of them shows that no water left, not that
    one is held -- and every site in such a run looks the same. That is a
    claim about residence the run cannot support.
    """

    @staticmethod
    def _pinned(duration_ps, n_frames=100):
        import mdtraj as md

        top = md.Topology()
        chain = top.add_chain()
        residue = top.add_residue("ALA", chain, resSeq=1)
        for name in ("N", "CA", "C", "O"):
            top.add_atom(name, md.element.carbon, residue)
        waters = top.add_chain()
        water = top.add_residue("HOH", waters, resSeq=100)
        for name, element in (("O", md.element.oxygen),
                              ("H1", md.element.hydrogen),
                              ("H2", md.element.hydrogen)):
            top.add_atom(name, element, water)

        rng = np.random.RandomState(0)
        xyz = np.zeros((n_frames, top.n_atoms, 3), dtype=np.float32)
        for frame in range(n_frames):
            xyz[frame, :4] = [[0, 0, 0], [0.15, 0, 0], [0.3, 0, 0], [0.3, 0.12, 0]]
            point = np.array([0.15, 0.30, 0.0]) + rng.normal(scale=0.02, size=3)
            xyz[frame, 4] = point
            xyz[frame, 5] = point + [0.01, 0, 0]
            xyz[frame, 6] = point + [0, 0.01, 0]

        traj = md.Trajectory(xyz=xyz, topology=top,
                             time=np.linspace(0, duration_ps, n_frames))
        traj.unitcell_lengths = np.tile([8.0, 8.0, 8.0], (n_frames, 1))
        traj.unitcell_angles = np.tile([90.0, 90.0, 90.0], (n_frames, 1))
        return traj

    def _findings(self, duration_ps, tmp_path):
        from fastmdxplora.analysis.water_sites import WaterSites

        analysis = WaterSites(output_dir=str(tmp_path), site_selection="name CA",
                              cutoff_nm=0.6)
        analysis.compute(self._pinned(duration_ps))
        return analysis.findings

    def test_ten_picoseconds_carries_the_caveat(self, tmp_path) -> None:
        findings = self._findings(10.0, tmp_path)
        assert "too_short_for_residence" in findings
        assert "no water left" in findings["too_short_for_residence"]

    def test_five_nanoseconds_does_not(self, tmp_path) -> None:
        findings = self._findings(5000.0, tmp_path)
        assert "too_short_for_residence" not in findings

    def test_the_duration_is_recorded_either_way(self, tmp_path) -> None:
        """So a reader can weigh a residence claim without rerunning."""
        assert self._findings(10.0, tmp_path)["duration_ps"] == pytest.approx(10.0)
        assert self._findings(
            5000.0, tmp_path)["duration_ps"] == pytest.approx(5000.0)
