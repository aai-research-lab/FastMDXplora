"""Which analyses a study leaves nothing for, read from its config.

The Config Builder ticked every analysis, so a protein in water seemed to
promise ligand contacts, bilayer thickness and a free energy. The same
declarations the orchestrator plans from (`requires_*`) are read against
the config and, once read, the structure. An analysis of a pair the study
names runs where `include` names it, whatever selections are filled in: a
form that waited for the selections before letting it be chosen could not
write a config that ran it.
"""

from __future__ import annotations

import unittest


def _reasons(config, facts=None):
    from fastmdxplora.gui.applicable import not_applicable

    return not_applicable(config, facts)


class TestFromTheConfig(unittest.TestCase):
    def test_a_protein_in_water_has_no_bilayer_and_no_free_energy(self):
        reasons = _reasons({"systems": [{"system": "1L2Y"}]})
        self.assertEqual(reasons.get("area_per_lipid"), "No membrane in this study")
        self.assertNotIn("rmsd", reasons)

    def test_a_membrane_gives_the_bilayer_analyses(self):
        reasons = _reasons({"systems": [{"system": "1L2Y"}], "setup": {"membrane": "POPC"}})
        self.assertNotIn("area_per_lipid", reasons)

    def test_a_pair_runs_where_it_is_chosen(self):
        alone = _reasons({"systems": [{"system": "1L2Y"}]})
        self.assertIn("pair_distance", alone)
        chosen = _reasons({"systems": [{"system": "1L2Y"}],
                           "analysis": {"include": "rmsd, pair_distance"}})
        self.assertNotIn("pair_distance", chosen)
        self.assertIn("coordination_number", chosen)

    def test_a_pair_chosen_still_says_what_else_it_needs(self):
        # Counting round a pair needs water in the frames; choosing it does
        # not give it any.
        reasons = _reasons({"systems": [{"system": "1L2Y"}],
                            "analysis": {"include": "coordination_number"}})
        self.assertEqual(reasons.get("coordination_number"), "Needs water in the frames")
        alone = _reasons({"systems": [{"system": "1L2Y"}]})
        self.assertEqual(alone.get("coordination_number"), "Needs water in the frames")

    def test_frames_from_elsewhere_rule_nothing_out_they_might_hold(self):
        reasons = _reasons({"systems": [{"system": "x.pdb"}], "include_phase": ["analysis"],
                            "analysis": {"trajectory": "x.xtc", "topology": "x.pdb"}})
        self.assertNotIn("area_per_lipid", reasons)


class TestFromTheStructure(unittest.TestCase):
    def test_an_nmr_ensemble_has_no_crystallographic_bfactors(self):
        reasons = _reasons({"systems": [{"system": "x.pdb"}]}, {"models": 20, "ligands": []})
        self.assertIn("bfactor_comparison", reasons)

    def test_a_ligand_is_looked_for_only_once_the_structure_is_read(self):
        unread = _reasons({"systems": [{"system": "x.pdb"}]})
        read = _reasons({"systems": [{"system": "x.pdb"}]}, {"models": 1, "ligands": []})
        ligand = [name for name, why in read.items() if why == "No ligand in this system"]
        self.assertTrue(ligand)
        self.assertFalse([name for name in ligand if name in unread])


if __name__ == "__main__":
    unittest.main()
