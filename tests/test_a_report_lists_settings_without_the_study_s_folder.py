"""A report lists the settings a phase was given, not where the study ran.

Setup records what it worked out for itself under names beginning with an
underscore: the repair's arguments, the file the retained ions went to,
each an absolute path. The report listed them all, and the demo study's
Report page printed the Workstation's home folder in its methods three
times. They are left out, the seed setup drew is said on `random_seed`, and
a file inside the study is named from the study's folder.
"""

from __future__ import annotations


def test_internal_settings_are_left_out_and_paths_named_from_the_study(tmp_path):
    from fastmdxplora.report.document import _settings_listed

    root = tmp_path / "3ptb-demo"
    root.mkdir()
    lines = _settings_listed({
        "ligand": [str(root / "setup" / "ligands" / "BEN.sdf")],
        "fixed_pdb": None,
        "random_seed": None,
        "_random_seed": 1909657446,
        "_retained_pdb": str(root / "setup" / "retained.pdb"),
        "_repaired": {"complex": str(root / "setup" / "complex_for_pka.pdb")},
    }, root)
    said = "\n".join(lines)
    assert str(tmp_path) not in said
    assert "**\\_" not in said and "retained" not in said
    assert "- **ligand**: `['setup/ligands/BEN.sdf']`" in lines
    assert "- **random\\_seed**: `1909657446` (drawn, as none was given)" in lines
    assert "- **fixed\\_pdb**: `None`" in lines


def test_a_seed_given_is_said_as_given(tmp_path):
    from fastmdxplora.report.document import _settings_listed

    lines = _settings_listed({"random_seed": 7, "_random_seed": 7}, tmp_path)
    assert lines == ["- **random\\_seed**: `7`"]


def test_a_path_outside_the_study_is_left_as_it_is(tmp_path):
    from fastmdxplora.report.document import _settings_listed

    elsewhere = str(tmp_path / "other" / "BEN.sdf")
    lines = _settings_listed({"ligand": elsewhere}, tmp_path / "study")
    assert lines == [f"- **ligand**: `{elsewhere}`"]
