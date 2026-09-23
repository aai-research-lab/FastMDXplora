"""Six messages that named a problem and left the reader stuck.

Each of these was correct. Each described the situation accurately and then
stopped, leaving somebody to work out the remedy -- and each cost real time
in a week of using the software on an unfamiliar machine.

A message that says what went wrong and not what to do is half a message, and
the half it leaves out is the one the reader wanted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fastmdxplora.config.loader import ConfigError, validate_config


class TestAFetchWithNoNetwork:
    """`urlopen error [Errno -3] Temporary failure in name resolution` is
    Python's words for a situation the reader can act on and cannot act on
    from that sentence. Clusters are commonly airgapped and this is the first
    thing a run does there."""

    def test_it_says_there_is_no_route_and_what_to_do(self, tmp_path, monkeypatch) -> None:
        # A machine with no route out: the refusal says so, and gives the
        # command to fetch the file from one that has.
        import urllib.request

        import pytest

        from fastmdxplora.refusals import StudyError
        from fastmdxplora.setup.pipeline import _fetch_pdb_from_rcsb

        def no_route(url, dest):
            raise OSError("[Errno -3] Temporary failure in name resolution")

        monkeypatch.setattr(urllib.request, "urlretrieve", no_route)
        with pytest.raises(StudyError) as raised:
            _fetch_pdb_from_rcsb("1UBQ", tmp_path / "1UBQ.pdb")
        said = str(raised.value)
        assert "no route to the internet" in said
        assert "curl -O https://files.rcsb.org/download/1UBQ.pdb" in said
        assert raised.value.code == "environment.service.unreachable"

    def test_the_original_error_survives(self, tmp_path, monkeypatch) -> None:
        """Whatever OSError actually said is still the evidence."""
        import urllib.request

        import pytest

        from fastmdxplora.refusals import StudyError
        from fastmdxplora.setup.pipeline import _fetch_pdb_from_rcsb

        original = OSError("[Errno 113] No route to host")

        def no_route(url, dest):
            raise original

        monkeypatch.setattr(urllib.request, "urlretrieve", no_route)
        with pytest.raises(StudyError) as raised:
            _fetch_pdb_from_rcsb("1UBQ", tmp_path / "1UBQ.pdb")
        assert "No route to host" in str(raised.value)
        assert raised.value.__cause__ is original

class TestARefusalToLookUpChemistry:
    def test_it_says_where_to_get_the_file(self) -> None:
        source = (Path("src/fastmdxplora/setup/pipeline.py")
                  .read_text(encoding="utf-8"))
        assert "files.rcsb.org/ligands/download" in source

    def test_it_says_the_coordinates_do_not_matter(self) -> None:
        """The commonest confusion after being told to supply one: an ideal
        component's pose is arbitrary and the structure's is used."""
        source = (Path("src/fastmdxplora/setup/pipeline.py")
                  .read_text(encoding="utf-8"))
        assert "coordinates are idealised and " in source


class TestALigandGivenAResidueName:
    """`ligand: HED` reported a missing file called HED. What was meant is a
    component already in the structure, which is a different setting."""

    @pytest.mark.parametrize("given", ["HED", "BNZ", "NAP", "ZN"])
    def test_a_residue_name_is_recognised(self, given: str) -> None:
        from fastmdxplora.setup.ligand import LigandError, load_ligand

        with pytest.raises(LigandError) as caught:
            load_ligand(given)
        said = str(caught.value)
        assert "looks like a residue name" in said
        assert "heterogens: auto" in said
        assert f"{given.upper()}_ideal.sdf" in said

    @pytest.mark.parametrize("given", ["missing.sdf", "a/b/ligand.mol2"])
    def test_a_real_path_gets_the_plain_message(self, given: str) -> None:
        """Somebody who gave a path and mistyped it does not need a lecture
        about a setting they used correctly."""
        from fastmdxplora.setup.ligand import LigandError, load_ligand

        with pytest.raises(LigandError) as caught:
            load_ligand(given)
        assert "looks like a residue name" not in str(caught.value)


class TestIncludeAccumulates:
    """`--include analysis --include report` ran report alone. Half the
    request dropped, and nothing said about it."""

    @staticmethod
    def _explore():
        from fastmdxplora.cli.main import _build_parser

        return _build_parser()._subparsers._group_actions[0].choices["explore"]

    def test_repeating_the_flag_adds(self) -> None:
        got = self._explore().parse_known_args(
            ["--include", "analysis", "--include", "report"])[0]
        assert got.include == ["analysis", "report"]

    def test_one_flag_with_several_values_is_the_same(self) -> None:
        """Both spellings mean what somebody typing either of them intends."""
        got = self._explore().parse_known_args(
            ["--include", "analysis", "report"])[0]
        assert got.include == ["analysis", "report"]

    def test_a_repeat_does_not_duplicate(self) -> None:
        got = self._explore().parse_known_args(
            ["--include", "report", "--include", "report"])[0]
        assert got.include == ["report"]

    def test_exclude_behaves_the_same_way(self) -> None:
        got = self._explore().parse_known_args(
            ["--exclude", "report", "--exclude", "analysis"])[0]
        assert got.exclude == ["report", "analysis"]

    def test_not_given_is_still_nothing(self) -> None:
        assert self._explore().parse_known_args([])[0].include is None


class TestAnAnalysisNameThatDoesNotExist:
    """`contacts` for `pl_contacts` validated, ran nothing, and produced a
    report missing the measurement the config asked for."""

    def test_a_misspelling_is_refused(self) -> None:
        with pytest.raises(ConfigError) as caught:
            validate_config({"analysis": {"include": ["contacts"]}})
        assert "No analysis is called 'contacts'" in str(caught.value)

    def test_the_nearest_name_is_offered(self) -> None:
        with pytest.raises(ConfigError) as caught:
            validate_config({"analysis": {"include": ["contacts"]}})
        assert "pl_contacts" in str(caught.value)

    def test_the_whole_list_is_given(self) -> None:
        """Where nothing is near, the list is what settles it."""
        with pytest.raises(ConfigError) as caught:
            validate_config({"analysis": {"include": ["nonsense"]}})
        said = str(caught.value)
        assert "rmsd" in said and "water_sites" in said

    def test_exclude_is_checked_too(self) -> None:
        with pytest.raises(ConfigError):
            validate_config({"analysis": {"exclude": ["contacts"]}})

    @pytest.mark.parametrize("names", [
        ["pl_contacts"], ["rmsd", "rg", "ss"], ["ligand_rmsd"],
    ])
    def test_real_names_pass(self, names) -> None:
        validate_config({"analysis": {"include": names}})


class TestReadingAStructureIsNotAGuiConcern:
    """It reads a file as text and returns counts, and the orchestrator needs
    it before any interface exists."""

    def test_it_lives_outside_the_gui_package(self) -> None:
        from fastmdxplora.structure_info import count_structure

        assert callable(count_structure)

    def test_the_old_path_still_works(self) -> None:
        """A rename inside this repository is not a reason to break somebody
        else's import."""
        from fastmdxplora.gui.structure_info import count_structure as moved
        from fastmdxplora.structure_info import count_structure

        assert moved is count_structure

    def test_no_test_reads_or_patches_the_shim(self) -> None:
        """Three tests broke on the move in the same way: they read the
        module's source or patched a name in it, and after the move both
        reach the re-export, which holds neither. A shim is for importers
        outside this repository; inside it, use the real module."""
        offenders = []
        here = Path(__file__).name
        for path in Path("tests").rglob("test_*.py"):
            if path.name == here:
                continue  # the probes below are this file's own text
            text = path.read_text(encoding="utf-8")
            for probe in ('patch("fastmdxplora.gui.structure_info.',
                          "import fastmdxplora.gui.structure_info as",
                          'patch("fastmdxplora.gui.ligand_detection.'):
                if probe in text:
                    offenders.append(f"{path.name}: {probe}")
        assert not offenders, offenders

    def test_nothing_in_the_package_reaches_through_the_gui(self) -> None:
        offenders = [
            str(path)
            for path in Path("src/fastmdxplora").rglob("*.py")
            if "gui/" not in str(path)
            and "from fastmdxplora.gui.structure_info" in path.read_text(
                encoding="utf-8")
        ]
        assert not offenders, offenders
