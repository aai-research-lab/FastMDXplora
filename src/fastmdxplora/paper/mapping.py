"""A study read from a paper, written as a config this software runs.

Each setting the paper states is set as it states it, with the paper's
words as its reason (the config's ``decisions``). Each choice is said with
one of five words (:data:`LABELS`):

``as_stated``     set as the paper states it
``not_stated``    the paper does not say; this software's own value is used,
                  or one the paper's other settings imply, with why
``differs``       the paper states it, and it is done here otherwise (OpenMM
                  thermostats by Langevin dynamics; a GROMACS force switch is
                  a potential switch here), with how
``needs_you``     what the paper gives cannot be set from its words alone:
                  a structure from a model, a method's collective variables
``not_possible``  this software cannot run it (a force field OpenMM does not
                  ship, coarse-grained models, free-energy perturbation)

A study is ``ready`` when every choice is as stated or not stated, ``with
differences`` when some differ, ``needs you`` when something must be
supplied before it runs, and ``cannot run`` when anything is not possible;
a study that cannot run gets no config. A config that needs you is written
so that it is refused until it is completed: its structure is a file that
is not there, or its method's block is empty.
"""

from __future__ import annotations

import bisect
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable

from fastmdxplora.paper.quotes import squash

__all__ = ["LABELS", "plan_study", "method_said_by", "ensemble_said_by",
           "openmm_files", "STRUCTURE_TO_GIVE", "slug"]

LABELS = ("as_stated", "not_stated", "differs", "needs_you", "not_possible")

#: Where a study's structure goes when the paper names none this can fetch:
#: a file that is not there, so the config is refused until it is given.
STRUCTURE_TO_GIVE = "GIVE-THE-STARTING-STRUCTURE.pdb"


@dataclass
class Choice:
    field: str
    label: str
    why: str
    setting: str = ""
    value: Any = None
    quote: str = ""
    where: str = ""

    def as_record(self) -> dict[str, Any]:
        out: dict[str, Any] = {"field": self.field, "label": self.label, "why": self.why}
        if self.setting:
            out["setting"] = self.setting
            out["value"] = self.value
        if self.quote:
            out["quote"] = self.quote
            out["where"] = self.where
        return out


def slug(text: str, most: int = 40) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return (out[:most].rstrip("-") or "study")


def distinct(names: list[str]) -> list[str]:
    """``names`` with each repeat numbered (``s1``, ``s1-2``), so no two
    files written from one paper are one file."""
    seen: dict[str, int] = {}
    out = []
    taken = set(names)
    for name in names:
        seen[name] = seen.get(name, 0) + 1
        if seen[name] == 1:
            out.append(name)
            continue
        number = seen[name]
        while f"{name}-{number}" in taken:
            number += 1
        taken.add(f"{name}-{number}")
        out.append(f"{name}-{number}")
    return out


# ---------------------------------------------------------------------------
# What OpenMM ships
# ---------------------------------------------------------------------------
def openmm_files() -> set[str] | None:
    """The force-field files this installation's OpenMM ships, by name
    under its data folder (``amber19/protein.ff19SB.xml``), or None where
    OpenMM is not installed and so cannot be asked."""
    try:
        import os

        import openmm.app as app
    except Exception:  # noqa: BLE001 - absent or broken: unknown
        return None
    root = os.path.join(os.path.dirname(app.__file__), "data")
    found: set[str] = set()
    for folder, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith(".xml"):
                found.add(os.path.relpath(os.path.join(folder, name), root).replace(os.sep, "/"))
    return found


#: Force fields by what the paper calls them. Each: the name said, the
#: pattern its reduced spelling matches (:func:`~fastmdxplora.paper.quotes.squash`),
#: the file, the family whose water files go with it, the OpenMM release
#: that first ships it, or None where OpenMM ships none.
_PROTEIN = (
    ("ff14SB only side chains", r"14sbonlysc", None, "", ""),
    ("ff99SB*-ILDN", r"99sb\*ildn|99sbstarildn", None, "", ""),
    ("ff99SB-disp", r"99sbdisp", None, "", ""),
    ("ff99SBnmr2", r"99sbnmr2", None, "", ""),
    ("ff99SBnmr1", r"99sbnmr1?", "amber99sbnmr.xml", "legacy", "8.0"),
    ("ff99SB-ILDN", r"99sbildn", "amber99sbildn.xml", "legacy", "8.0"),
    ("ff99SB", r"99sb", "amber99sb.xml", "legacy", "8.0"),
    ("ff14SB", r"14sb|amber14(?!sb)", "amber14/protein.ff14SB.xml", "amber14", "8.0"),
    ("ff19SB", r"19sb|amber19", "amber19/protein.ff19SB.xml", "amber19", "8.3"),
    ("ff15ipq", r"15ipq", "amber14/protein.ff15ipq.xml", "amber14", "8.0"),
    ("ff03w", r"03w", None, "", ""),
    ("ff03*", r"03\*|03star", None, "", ""),
    ("ff03", r"ff03|amber03", "amber03.xml", "legacy", "8.0"),
    ("ff10", r"ff10|amber10", "amber10.xml", "legacy", "8.0"),
    ("ff96", r"ff96|amber96", "amber96.xml", "legacy", "8.0"),
    ("AMBER-FB15", r"fb15", "amberfb15.xml", "legacy", "8.0"),
    ("DES-Amber", r"desamber", None, "", ""),
    ("CHARMM36m", r"charmm36m|c36m", "charmm36_2024.xml", "charmm36_2024", "8.3"),
    ("CHARMM36", r"charmm36|c36", "charmm36.xml", "charmm36", "8.0"),
    ("CHARMM22*", r"charmm22\*|c22\*|charmm22star", None, "", ""),
    ("CHARMM27", r"charmm27|c27", None, "", ""),
    ("CHARMM22", r"charmm22|c22", None, "", ""),
    ("OPLS", r"opls", None, "", ""),
    ("GROMOS", r"gromos|43a1|53a6|54a7", None, "", ""),
    ("Martini", r"martini", None, "", ""),
    ("Drude", r"drude|polariz|polaris", None, "", ""),
    ("AMOEBA", r"amoeba", None, "", ""),
)
_NUCLEIC = (
    ("bsc0", r"bsc0|parmbsc0", None, "", ""),
    ("Tumuc1", r"tumuc", None, "", ""),
    ("OL21", r"ol21", "amber19/DNA.OL21.xml", "amber19", "8.3"),
    ("OL15", r"ol15", "amber14/DNA.OL15.xml", "amber14", "8.0"),
    ("bsc1", r"bsc1", "amber14/DNA.bsc1.xml", "amber14", "8.0"),
    ("OL3", r"ol3|chiol3", "amber14/RNA.OL3.xml", "amber14", "8.0"),
    ("CHARMM36", r"charmm36|c36", "charmm36.xml", "charmm36", "8.0"),
)
_LIPID = (
    ("Lipid14", r"lipid14", None, "", ""),
    ("Lipid17", r"lipid17", "amber14/lipid17.xml", "amber14", "8.0"),
    ("Lipid21", r"lipid21", "amber19/lipid21.xml", "amber19", "8.4"),
    ("CHARMM36", r"charmm36|c36", "charmm36.xml", "charmm36", "8.0"),
    ("Slipids", r"slipid", None, "", ""),
    ("Berger", r"berger", None, "", ""),
    ("GROMOS", r"gromos|43a1|53a6|54a7", None, "", ""),
    ("Martini", r"martini", None, "", ""),
)
#: Water models: name, pattern, the file's stem in each family's folder,
#: and the geometry OpenMM's Modeller places it with.
_WATER = (
    ("CHARMM TIP3P", r"charmmtip3p|mtip3p|tips3p|tip3p\(charmm|charmmmodified", "water", "tip3p"),
    ("TIP3P-FB", r"tip3pfb", "tip3pfb", "tip3p"),
    ("TIP4P-FB", r"tip4pfb", "tip4pfb", "tip4pew"),
    ("TIP4P-Ew", r"tip4pew", "tip4pew", "tip4pew"),
    ("TIP4P/2005", r"tip4p/?2005", "tip4p2005", "tip4pew"),
    ("TIP4P-D", r"tip4pd", None, ""),
    ("TIP5P", r"tip5p", "tip5p", "tip5p"),
    ("OPC3", r"opc3", "opc3", "tip3p"),
    ("OPC", r"opc", "opc", "tip4pew"),
    ("SPC/E", r"spc/?e", "spce", "spce"),
    ("SPC", r"spc", None, ""),
    ("TIP4P", r"tip4p", None, ""),
    ("TIP3P", r"tip3p", "tip3p", "tip3p"),
)
#: Which water files each family's folder has.
_WATERS_IN = {
    "amber14": {"tip3p", "tip3pfb", "tip4pew", "tip4pfb", "spce", "opc", "opc3"},
    "amber19": {"tip3p", "tip3pfb", "tip4pew", "tip4pfb", "spce", "opc", "opc3"},
    "legacy": {"tip3p", "tip3pfb", "tip4pew", "tip4pfb", "tip5p", "spce", "opc", "opc3"},
    "charmm36": {"water", "spce", "tip4pew", "tip4p2005", "tip5p"},
    "charmm36_2024": {"water", "spce", "tip4pew", "tip4p2005", "tip5p"},
}


def _match(table: tuple[tuple[Any, ...], ...], said: str) -> tuple[Any, ...] | None:
    reduced = squash(said)[0]
    for entry in table:
        if re.search(entry[1], reduced):
            return entry
    return None


def _water_file(family: str, stem: str) -> str:
    if family == "legacy":
        return f"{stem}.xml"
    return f"{family}/{stem}.xml"


# ---------------------------------------------------------------------------
# What a name says
# ---------------------------------------------------------------------------
_KINDS_OF: dict[str, tuple[tuple[str, str], ...]] = {
    "thermostat": (("Langevin", r"langevin|stochasticdynamics|baoab|\bsd\b"),
                   ("Nose-Hoover", r"nosehoover|nose|hoover"),
                   ("v-rescale", r"vrescale|velocityrescal|bussi|canonicalsampling"),
                   ("Berendsen", r"berendsen"), ("Andersen", r"andersen")),
    "barostat": (("Monte Carlo", r"montecarlo|mcbarostat"),
                 ("Parrinello-Rahman", r"parrinello|rahman"),
                 ("Berendsen", r"berendsen"), ("Langevin piston", r"langevinpiston|piston"),
                 ("C-rescale", r"crescale|stochasticcellrescal"),
                 ("MTK", r"mttk|martyna|tuckerman")),
    "box_shape": (("octahedron", r"octahedr"), ("dodecahedron", r"dodecahedr"),
                  ("cube", r"cub(e|ic)"), ("rectangular", r"rectang|orthorhomb|tetragonal"),
                  ("hexagonal", r"hexagon")),
    "electrostatics": (("PME", r"pme|particlemesh|smoothparticle"),
                       ("Gaussian split Ewald", r"gaussiansplit|gse"),
                       ("Ewald", r"ewald"), ("reaction field", r"reactionfield"),
                       ("cutoff", r"cutoff|cutoff")),
    "constraints": (("all bonds", r"allbond|everybond|allcovalent"),
                    ("bonds to hydrogen", r"hydrogen|hbond|xh"),
                    ("SHAKE", r"shake"), ("LINCS", r"lincs"), ("SETTLE", r"settle"),
                    ("RATTLE", r"rattle"), ("M-SHAKE", r"mshake")),
    "ensemble": (("NPT", r"npt|isothermalisobaric|constantpressure|npat"),
                 ("NVT", r"nvt|constantvolume|canonical"), ("NVE", r"nve|microcanonical")),
    "ions": (("Na+", r"\bna\b|na\+|sodium|nacl"), ("K+", r"\bk\b|k\+|potassium|kcl"),
             ("Cl-", r"\bcl\b|cl-|chloride|nacl|kcl"), ("Mg2+", r"magnesium|mg2|mgcl"),
             ("Ca2+", r"calcium|ca2|cacl"), ("Zn2+", r"zinc|zn2")),
    "engine": (("GROMACS", r"gromacs"), ("AMBER", r"amber|pmemd|sander"),
               ("NAMD", r"namd"), ("OpenMM", r"openmm"), ("CHARMM", r"charmm"),
               ("Desmond", r"desmond"), ("ACEMD", r"acemd"), ("LAMMPS", r"lammps"),
               ("Anton", r"anton"), ("GENESIS", r"genesis")),
}


def recognized(field: str, text: str) -> set[str] | None:
    """The things of their kind ``text`` names for ``field``: the force
    fields, water models, thermostats, box shapes and the rest it says,
    by one name each; None for a field that is a description rather than
    a choice among names (the system, how it was minimised)."""
    reduced = squash(str(text))[0]
    tables = {"protein_forcefield": _PROTEIN, "nucleic_forcefield": _NUCLEIC,
              "lipid_forcefield": _LIPID, "water_model": _WATER}
    found: set[str] = set()
    if field in tables:
        rest = reduced
        for entry in tables[field]:
            match = re.search(entry[1], rest)
            while match:
                found.add(entry[0])
                rest = rest[:match.start()] + "|" + rest[match.end():]
                match = re.search(entry[1], rest)
        return found
    if field in _KINDS_OF:
        words = str(text).lower()
        for name, pattern in _KINDS_OF[field]:
            if re.search(pattern, reduced) or re.search(pattern, words):
                found.add(name)
        return found
    if field == "membrane":
        upper = str(text).upper()
        found = set(re.findall(r"\b([DP][OPMLSAEY]P[CEGSA])\b", upper))
        if re.search(r"chol", str(text), re.IGNORECASE):
            found.add("CHOL")
        return found
    return None


# ---------------------------------------------------------------------------
# Words that say a method or an ensemble
# ---------------------------------------------------------------------------
_METHOD_WORDS = {
    "plain": r"md|moleculardynamics|simulation|unbiased|conventional|classical|equilibrium",
    "umbrella": r"umbrella|wham",
    "metadynamics": r"metadynamics|metad|opes|ondtheflyprobability|funnel",
    "replica_exchange": r"replicaexchange|remd|parallel\s*tempering|biasexchange|"
                        r"simulatedtempering|hrex|rest2|solutetempering",
    "free_energy": r"freeenergyperturbation|fep|alchemical|thermodynamicintegration|"
                   r"absolutebinding|decoupl",
    "accelerated": r"acceleratedmd|acceleratedmolecular|gamd|gaussianaccelerated",
    "steered": r"steered|smd|pull|targetedmd|tmd",
    "qm_mm": r"qm/?mm|quantummechanic|dftb|dft",
    "coarse_grained": r"coarsegrain|martini|cg",
    "implicit_solvent": r"implicit|generalizedborn|gbsa|obc|gbn",
    "milestoning": r"milestoning|mmvt|seekr|weightedensemble",
    "other": r".",
}


def method_said_by(method: str, words: str) -> bool:
    """Whether ``words`` say the method an AI model named for a study."""
    pattern = _METHOD_WORDS.get(str(method).strip().lower())
    return bool(pattern) and bool(re.search(pattern, squash(words)[0]))


def ensemble_said_by(ensemble: str, words: str) -> bool:
    reduced = squash(words)[0]
    said = squash(ensemble)[0]
    if said in reduced:
        return True
    if said == "npt":
        return bool(re.search(r"npt|constantpressure|isothermalisobaric|isobaric", reduced))
    if said == "nvt":
        return bool(re.search(r"nvt|constantvolume|canonical", reduced))
    return False


def _method_of(said: str) -> str:
    for method in ("replica_exchange", "free_energy", "milestoning", "qm_mm",
                   "coarse_grained", "implicit_solvent", "accelerated", "metadynamics",
                   "umbrella", "steered", "plain"):
        if str(said).strip().lower() == method:
            return method
    reduced = squash(said)[0]
    for method in ("replica_exchange", "free_energy", "milestoning", "qm_mm",
                   "coarse_grained", "implicit_solvent", "accelerated", "metadynamics",
                   "umbrella", "steered"):
        if re.search(_METHOD_WORDS[method], reduced):
            return method
    return "plain" if re.search(_METHOD_WORDS["plain"], reduced) else "other"


#: Names, in a method's details, of methods this does not run. The details
#: of plain MD name pressures, analyses and hardware, so nothing shorter or
#: looser. The AI model's word for the method decides first; these names
#: are a second look, and a name it misses leaves that word to decide.
_DETAIL_WORDS = {
    "replica_exchange": r"bias[-\s]*exchange|replica[-\s]*exchange"
                        r"|\b(?:[thm]|re|ph)?-?remds?\d{0,3}\b"
                        r"|(?-i:\b(?:T-?)?REX\d{0,3}\b)|\bre-md\b|parallel[-\s]*tempering"
                        r"|\bpt-?(?:wte|metad)|\bbe-?meta|solute[-\s]*tempering"
                        r"|(?-i:\b[gG]?REST[23]?\d{0,3}\b)|\bg?rest[23]\d{0,3}\b|\bh-?rex\d{0,3}\b"
                        r"|\breus\d{0,3}\b|simulated[-\s]*tempering|temperature[-\s]*exchange"
                        r"|hamiltonian[-\s]*(?:replica[-\s]*)?exchange"
                        r"|(?-i:\bPT\b)\s+(?:simulations?|md|runs?|with)"
                        r"|expanded[-\s]*ensemble\s+(?:simulations?|sampling|md|method|runs?)",
    "free_energy": r"free[-\s]*energy[-\s]*perturbation|\bfeps?\d{0,3}\b|alchemical|annihilat"
                   r"|thermodynamic[-\s]*integration|(?-i:\bTI\d{0,3}\b)|\b[ar]bfe\b|\bties\b"
                   r"|(?:lambda|\u03bb)[-\s]*(?:windows?|values?|states?)"
                   r"|non[-\s]?equilibrium\s+(?:switch\w*|transitions?)|double[-\s]*decoupl"
                   r"|decoupl\w*\s+(?:of\s+)?(?:the\s+|each\s+)?(?:ligand|solute|inhibitor)"
                   r"|(?:ligand|solute|electrostatics?|van\s+der\s+waals|lennard[-\s]jones"
                   r"|interactions?)(?:'s)?\s+(?:\w+\s+)?(?:was\s+|were\s+)?decoupl"
                   r"|decoupling\s+(?:simulations?|calculations?)",
    "accelerated": r"(?<!gpu-)(?<!gpu\s)(?<!cuda-)(?<!cuda\s)(?<!hardware-)"
                   r"accelerated[-\s]*(?:md|molecular)|\b(?:li|pep|lig)?gamds?\d{0,3}\b"
                   r"|(?-i:\baMD\d{0,3}\b)|dual[-\s]*boost|boost(?:ed|ing)?\s+potential"
                   r"|\biamd\s*=",
    "qm_mm": r"\bqm(?: ?[-/:\\|] ?| ?\([^()]{1,30}\) ?/? ?| )?mm\d{0,3}\b|\bqm\s+and\s+mm\b"
             r"|\bqm[-\s]+(?:region|atoms|subsystem|zone|layer|part)"
             r"|quantum[-\s]*mechanic(?:s|al)? ?[-/] ?molecular|quantum[-\s]*mechanically"
             r"|quantum[-\s]*mechanical\s+(?:\(qm\)\s+)?(?:region|treatment|subsystem|atoms|zone)"
             r"|\boniom\b|\b(?:dft|pm[367]|am1|xtb|gfn\d?-?xtb|nnp|ml|ani(?:-\w+)?|b3lyp) ?/ ?mm\b"
             r"|\b(?:scc-)?dftb\d?\b|car[-\s]*parrinello|\bcpmd\b|\bbomd\b|\baimd\b"
             r"|ab[-\s]*initio\s+(?:md|molecular)|born[-\s]*oppenheimer\s+(?:md|molecular)",
    "coarse_grained": r"\bcg[-\s]?(?:md|simulations?|models?)\b|\bmartini(?:\s*\d+(?:\.\d+)*)?\b"
                      r"|\bsirah\b|\bg[o\u014d][-\s]+(?:like\s+)?(?:models?|potentials?)\b|\bsmog\d?\b"
                      r"|\bunres\b|\bawsem\b|\boxdna\b|\boxrna\b|\bcalvados\b|\bhyres\b"
                      r"|\bdpd\b|dissipative\s+particle",
    "implicit_solvent": r"implicit[-\s]*(?:solv|water|membrane)|generali[sz]ed[-\s]*born"
                        r"|(?<!mm/)(?<!mm-)\bgb/?sa\b|\bgb-?obc|\bgbn2?\b|\bgb-?neck|\bobc\d?\b"
                        r"|\bgbsw\b|\bgbmv\d?\b|\bgb-?hct\b|\bigb\s*=?\s*[1-8]\b"
                        r"|(?:(?<!\d\s)|(?<=[^\W\d_]\d\s)|(?<=[^\W\d_]\d\d\s))\bgb\s+"
                        r"(?:model|implicit|solva?t|solvent|md|simulations?)"
                        r"|onufriev[-\s]*bashford[-\s]*case|\beef1\b|\bimm1\b|\babsinth\b"
                        r"|solvent\s+was\s+(?:modell?ed|treated)\s+implicitly|pbsa\s+solvent",
    "milestoning": r"milestoning|\bmmvt\b|\bseekr|weighted[-\s]*ensemble|\bwestpa\d{0,3}\b"
                   r"|\bwexplore\d{0,3}\b|(?-i:\bWE\b)\s+simulations?|walkers?\s+(?:per|in\s+each)\s+bin",
}

#: Words that may name such a method, or may not: "quantum mechanical"
#: (QM/MM, or a ligand's charges), "coarse-grained" (a model, or an
#: analysis), "decoupled", "in vacuo", "free energy", "AMD" (accelerated MD,
#: or the hardware), "accelerated". Each puts the study to the person, with
#: no exception read from the words around it: none refuses the study, and
#: none lets it run unread. Runnable methods that need their own settings
#: (metadynamics, umbrella sampling, steered MD) are put to the person too
#: where the AI model's word for the method is plain MD. Only "GPU-" or
#: "CUDA-accelerated", "AMD" before a GPU or processor's name, and the
#: phrases of ``_PLAIN_PHRASES`` are not read as such words.
_LOOSE_WORDS = (
    r"quantum[-\s]*mechanic|coarse[-\s]*grain|decoupl|\bin\s+vacuo\b|\bvacuum\b|gas[-\s]*phase"
    r"|(?:absolute|relative|binding|hydration|solvation)\s+free[-\s]*energ|\bpmf\b"
    r"|(?-i:\bAMD\b)(?![-\s]+(?:gpus?|radeon|instinct|epyc|ryzen|threadripper|opteron"
    r"|mi\d{2,4}[ax]?|hardware|rocm|hip|processors?|cpus?)\b)"
    r"|\bgb\b"
    r"|dielectric|(?-i:\bM?BAR\b)|lambda[-\s]*dynamics|\bboost"
    r"|(?-i:\bRest\d?\b)|semi[-\s]*empirical|\bdft\b|metadynamics|\bmetad\b|\bopes\b|umbrella|steered|\bsmd\b"
    r"|\bplumed\b|collective\s+variable|\bwham\b|\bexchang|\bswap|continuum|without\s+(?:any\s+)?"
    r"(?:water|solvent)|solvent[-\s]free|soft[-\s]?core|(?:turned|switched)\s+off|\blambda\b|\u03bb"
    r"|\bwalkers?\b|\belnedyn|\bbeads?\b|[\w()+*,-]{1,40}\s*[/\\|]\s*(?:mm|amber|charmm)\b|\bxtb\b"
    r"|\bpm[367]\b|\bigamd|(?-i:\bWE\b)|poisson[-\s]*boltzmann|(?<!gpu-)(?<!gpu\s)(?<!cuda-)(?<!cuda\s)"
    r"(?<!hardware-)\baccelerat|\bg[o\u014d][-\s]+(?:like|model)"
    r"|thermodynamic[-\s]+cycle|string[-\s]+method|temperature\s+string|constant[-\s]*ph\b"
    r"|\bc?phmd\b|brownian[-\s]+dynamic|\bbd\s+(?:simulations?|runs?|trajector)|\bbrowndye|\be?abf\b"
    r"|adaptive[-\s]+bias|\btamd\b|hyper[-\s]*dynamics|parallel[-\s]+replica\s+dynamics|\bparrep\b"
    r"|\bd-?afed\b|adiabatic\s+free[-\s]*energy|enhanced[-\s]*sampling|(?<!\d\s)(?<!\d)\bmbar\b"
    r"|\bbiosimspace\b|\bcrooks|jarzynski|zwanzig"
    r"|free[-\s]*energ\w*\s+(?:calculations?|computations?|estimat\w*)"
    r"|\bpymbar|\bpmx\b|\bopen-?fe\b|open\s+free\s+energy|\bsomd\d?\b|\bawh\b"
    r"|targeted[-\s]+(?:md|molecular)|bennett(?:['\u2019]s)?[-\s]+acceptance|\balchemlyb\b"
    r"|\bperses\b|(?-i:\bYANK\b)|\bq[-\s]?ligfep\b"
    r"|\bsumd\b|supervised[-\s]+(?:md|molecular)|\bgc(?:nc)?mc\b|grand[-\s]+canonical"
    r"|(?:\u03bc|\bmu)[-\s]?vt\b"
    r"|adaptive[-\s]+sampling"
    r"|\brest\s+(?:simulations?|md|runs?|replicas?|protocol|scheme)\b")

#: Plain phrases holding one of those words, named one by one (a fixed list,
#: no word read around them), each of which can name nothing else: a
#: wavelength of visible or ultraviolet light, weighted ensemble's capital
#: letters before an acknowledgement in capitals, gigabytes of storage or
#: memory, a drug "targeted" at MDM2 or MDMX. A loose word inside one of
#: these is no sign; another loose word in the same sentence still is.
#: Phrases that could stand beside or inside another method's description
#: (a force field before "/AMBER", which may be a QM/MM label's MM half; an
#: exchange of water, which may be a grand canonical move; a gas-phase
#: optimisation, which may be of the whole system; a run accelerated on
#: GPUs) are not on it, and ask.
_PLAIN_PHRASES = re.compile(
    r"(?:\blambda|(?<!\w)\u03bb)(?:[\s_]?(?:max|em|ex|exc|abs))?\s*(?:(?:=|of|:)\s*)?"
    r"[1-9]\d\d(?:\.\d+)?\s*nm\b"
    r"|(?-i:\bWE\s+(?:THANK|THANKS|ACKNOWLEDGE|GRATEFULLY|ARE\s+GRATEFUL)\b)"
    r"|(?<![\d.])\bgb\s+(?:of\s+)?(?:storage|memory|ram|vram|disk)\b|(?<=\d)\s?(?-i:GB/s)\b"
    r"|\btargeted\s+mdm[2x]\b", re.IGNORECASE)

#: Words in a sentence that make a method's name in it something other than
#: what the study did, or might: a denial, a comparison, another work, a
#: run it started from, an exception. Where any is in the name's sentence,
#: the person reads the words and decides; where none is, the study is that
#: method.
_QUALIFIED = re.compile(
    r"\b(?:no|not|non|none|without|never|neither|nor|instead|rather|unlike|compared|comparison"
    r"|versus|vs|contrast\w*|opposed|controls?|previous\w*|earlier|prior|published|reported"
    r"|literature|start\w*|taken|seeded|derived|except\w*|apart|but|only|whereas|e\.g"
    r"|such\s+as|alternative\w*|avoid\w*|cannot|unable|impossible)\b", re.IGNORECASE)

#: MM/GBSA or MM/PBSA: an implicit-solvent or free-energy name in its
#: sentence may be the rescoring's or the run's, so it is put to the person.
_RESCORING = re.compile(r"\bmm\s*[-/]?\s*[gp]bsa\b|\bmmpbsa|\bmmgbsa", re.IGNORECASE)

#: What a run of such a method does, named or not.
_SIGNS = {
    "replica_exchange": re.compile(
        r"\breplicas?\b[^.;]{0,80}\b(?:exchang|swap)|\b(?:exchang|swap)\w*\b[^.;]{0,80}"
        r"\b(?:replicas?|copies|temperatures)\b"
        r"|\breplicas?\b[^.;]{0,40}\b[2-9]\d\d(?:\.\d+)?(?:\s*K)?\s*(?:-|to|~|\u2192|and)\s*[2-9]\d\d(?:\.\d+)?\s*K\b"
        r"|\b[2-9]\d\d(?:\.\d+)?(?:\s*K)?\s*(?:-|to|~|\u2192)\s*[2-9]\d\d(?:\.\d+)?\s*K\b[^.;]{0,40}\breplicas?\b"
        r"|\breplica\s+temperatures\b|\btemperature\s+ladder\b"
        r"|\bmetropolis\b[^.;]{0,80}\b(?:temperatures|replicas?|exchang|swap|neighbou?ring|adjacent)"
        r"|\b(?:temperatures|replicas?|exchang\w*|swaps?)\b[^.;]{0,80}\bmetropolis\b", re.IGNORECASE),
    "accelerated": re.compile(r"\bboost(?:s|ed|ing)?\b[^.;]{0,40}\b(?:potential|added|applied"
                              r"|dihedral|kcal)|\bsigma0|\u03c30", re.IGNORECASE),
}

#: Dashes, slashes and spacing that PDF text gives in place of the plain
#: ones; soft hyphens are dropped.
_PLAIN_TEXT = str.maketrans({"\u2236": ":", "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
                             "\u2027": "-", "\u1806": "-",
                             "\u2014": "-", "\u2212": "-", "\u2043": "-", "\u2215": "/",
                             "\u2044": "/", "\u00ad": None,
                             "\u00a0": " ", "\u2009": " ", "\u202f": " "})
#: Soft hyphens, or marks printed for one, with any invisible characters
#: beside them, between a letter and a line's end or a space before a letter.
_INVISIBLE = ("\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff"
              "\ufff9-\ufffb\U000e0001\U000e0020-\U000e007f")
_SOFT_BREAK = re.compile(r"(?:(?<=[^\W\d_])|(?<=[^\W\d_][\u0300-\u036f]))"
                         r"[" + _INVISIBLE + r"\u00ad\u2027\u1806]*"
                         r"[\u00ad\u2027\u1806][" + _INVISIBLE + r"]*(?=\s+[" + _INVISIBLE
                         + r"]*[^\W\d_])")
#: Cyrillic, Greek and small-capital letters drawn as Latin ones ("Replica
#: \u0435xchange" with a Cyrillic e), read as the Latin letter they look like,
#: and slashes and bars drawn as other symbols as "\\", "|" and "/". Greek
#: letters with a meaning of their own here (lambda, sigma, mu, nu) are left
#: as they are; iota, kappa and chi are read as i, k and x, and Lisu and
#: Cherokee capitals as the Latin capitals they look like.
_LOOK_ALIKES = str.maketrans(
    "\u0410\u0412\u0421\u0415\u041d\u0406\u0408\u041a\u041c\u041e\u0420\u0405\u0422\u0425\u0423"
    "\u0430\u0441\u0435\u0456\u0458\u043e\u0440\u0455\u0445\u0443\u0501\u051b\u051d\u04bb\u04cf"
    "\u0391\u0392\u0395\u0396\u0397\u0399\u039a\u039c\u039d\u039f\u03a1\u03a4\u03a5\u03a7\u03bf"
    "\u1d00\u0299\u1d04\u1d05\u1d07\u0262\u029c\u026a\u1d0a\u1d0b\u029f\u1d0d\u0274\u1d0f\u1d18\u0280"
    "\u1d1b\u1d1c\u1d20\u1d21\u028f\u1d22\ua730\ua731"
    "\u0451\u0401\u03b9\u03ba\u03c7"
    "\ua4d0\ua4d1\ua4d2\ua4d3\ua4d4\ua4d6\ua4d7\ua4d9\ua4da\ua4dc\ua4dd\ua4df\ua4e0"
    "\ua4e1\ua4e2\ua4e3\ua4e6\ua4e7\ua4ea\ua4eb\ua4ec\ua4ee\ua4f0\ua4f2\ua4f3\ua4f4"
    "\u13aa\u13f4\u13df\u13a0\u13ac\u13c0\u13bb\u13ab\u13e6\u13de\u13b7\u13e2\u13a1\u13da"
    "\u13d4\u13d9\u13b3\u13c3\u13a2\u13d2\u13d5\u13f3\u13a9\u13bd\u051c\u04c0\u04ba"
    "\u2216\u29f5\u2223\u29f8\u2571\u2502\u2503\u01c0",
    "ABCEHIJKMOPSTXY" "aceijopsxydqwhl" "ABEZHIKMNOPTYXo" "ABCDEGHIJKLMNOPRTUVWYZFS" "eEikx"
    "BPdDTGKJCZFMN" "LSRVHWXYAEIOU"
    "ABCDEGHJKLMPRS" "WVWZTRSGYYWIH"
    "\\\\|//|||")


def _plain_text(words: str, join: bool = True, look_alikes: bool = False,
                breaks: list[int] | None = None) -> str:
    """The paper's words with PDF typography made plain: trade marks and
    modifier letters as spaces, superscript citation digits as spaces,
    ligatures and wide forms (NFKC), accents dropped, dashes and slashes as
    "-" and "/", soft hyphens and invisible characters dropped, newlines as
    spaces. With ``look_alikes``, letters and slashes that only look Latin
    are read as the Latin ones (method names; a membrane's words keep them,
    so a word in another script still needs the person). With ``join``, a
    word broken by a hyphen at a line's end ("ex-\n change") is joined; the
    plan reads the words both ways. ``breaks``, where given, gets where
    each soft hyphen before a line's end or a space was."""
    import unicodedata

    words = str(words).replace("\r\n", "\n").replace("\r", "\n")
    if breaks is not None:
        words = _SOFT_BREAK.sub("\ue000", words)
    if look_alikes:
        words = words.translate(_LOOK_ALIKES)
    words = re.sub("[\u2122\u00ae\u2120\u00a9\u00aa\u00ba\u2070\u00b9\u00b2\u00b3\u2074-\u2079]+",
                   " ", words)
    words = "".join(" " if unicodedata.category(ch) == "Lm" else ch for ch in words)
    words = unicodedata.normalize("NFKC", words).translate(_PLAIN_TEXT)
    words = "".join(ch for ch in unicodedata.normalize("NFKD", words)
                    if not unicodedata.combining(ch) and unicodedata.category(ch) != "Cf")
    if look_alikes:  # once more: an accent may have hidden the letter ("\u0451", "\u03af")
        words = words.translate(_LOOK_ALIKES)
    if join:
        words = re.sub(r"(?<=[A-Za-z])-[ \t]*\n\s*(?=[A-Za-z])", "", words)
        words = re.sub(r"(?<=[A-Za-z])- (?=[A-Za-z])", "", words)
    words = re.sub(r"\n\s*", " ", words)
    if breaks is not None:
        breaks += [mark.start() - n for n, mark in enumerate(re.finditer("\ue000", words))]
        words = words.replace("\ue000", "")
    return words.replace("_", " ")


def _methods_in_details(words: str) -> tuple[str | None, list[tuple[str, str]]]:
    """The method a study's details say it is, where that is one this does
    not run and its name stands with nothing that qualifies it in its
    sentence; and each name or word found otherwise, with its sentence:
    ``"mentioned"`` (a name beside words that may deny it or give it
    elsewhere) or ``"signs"`` (a word that may mean such a method, or a run
    of one described without its name). The plan asks the person to
    confirm both: it does not guess what such words mean. A name read with
    line-end hyphens joined makes the study that method, and so does one
    read as written before a
    hyphen and one of a fixed list of words (``_WHOLE_BEFORE``: "QM/MM-\n
    based", "REMD- and MD-based"), where joining breaks a whole name. Any
    other name read only as written ("proper-\nties" reads TIES) may be a
    piece of a broken word, so it puts the study to the person, as does a
    name read only where a soft hyphen at a line's end is joined, or a name
    of capitals glued to a word ("REMDsimulations")."""
    breaks: list[int] = []
    joined = _plain_text(words, join=True, look_alikes=True, breaks=breaks)
    text_breaks: list[int] = []
    text = _plain_text(words, join=False, look_alikes=True, breaks=text_breaks)
    softly = re.sub(_SOFT_BREAK.pattern + r"\s+[" + _INVISIBLE + "]*", "", str(words))
    soft = _plain_text(softly, join=True, look_alikes=True)
    if softly != str(words) and _qualifiers(soft) - _qualifiers(joined):
        # A word that qualifies a name, read only with a soft hyphen at a
        # line's end joined ("with" and "out"): no name makes the study a method.
        aside = []
        for reading in (joined, text, soft):
            aside += [item for item in _read_details(reading, lambda name: False)[1]
                      if item not in aside]
        return None, aside + [item for item in _glued(joined) if item not in aside]
    method, aside = _read_details(joined, lambda name: _whole_by(joined, name, breaks))
    if method:
        return method, aside
    method, found = _read_details(text, lambda name: _whole_by(text, name, text_breaks) and bool(
        _WHOLE_BEFORE.match(text, name.end()) or re.search(r"-\s", name.group(0))
        or (_PREFIXED.search(text, 0, name.start())
            and re.search(r"remd|rex|exchange", name.group(0), re.IGNORECASE))))
    if not method and softly != str(words):
        found += _read_details(soft, lambda name: False)[1]
    if not method:
        found += _glued(joined)
    seen = set(aside)
    for item in found:
        if item not in seen:
            seen.add(item)
            aside.append(item)
    return method, aside


def _whole_by(words: str, name: re.Match[str], breaks: list[int]) -> bool:
    """Whether ``name`` is whole beside the soft hyphens at ``breaks``: one
    just after it may break a longer word ("REMD\u00ad\nsettings"), unless a word
    of a fixed list follows ("REMD\u00ad\nsimulations"); one just before it
    always may ("proper\u00ad\nties")."""
    for at in breaks:
        if at < name.start() and not words[at:name.start()].strip():
            return False
        if at >= name.end() and not words[name.end():at].strip() and not _FOLLOWED.match(
                words, at):
            return False
    return True


def _qualifiers(words: str) -> Counter[str]:
    """The words in ``words`` that qualify a method's name or are MM/GBSA."""
    return Counter(re.sub(r"\s+", "", word.group(0).lower())
                   for pattern in (_QUALIFIED, _RESCORING) for word in pattern.finditer(words))


def _glued(words: str) -> list[tuple[str, str]]:
    return [(_short(_sentence_of(words, glued).strip(), 160), "mentioned")
            for glued in _GLUED.finditer(words)]


#: Names of capitals glued to a word or a number, before or after
#: ("REMDsimulations", "processorsREMD", "32REMD", "REMDSimulations"),
#: which no word boundary finds.
_GLUED_NAMES = (r"(?:REMD|REST2?|HREX|REX|GaMD|aMD|FEP|TIES|QM/MM|QMMM|ONIOM|CPMD|DFTB|MARTINI"
                r"|SIRAH|UNRES|GBSA|MMVT|WESTPA)")
#: Names glued to a word in any case ("remdsimulations", "qm/mmregion").
_GLUED_ANY_CASE = r"(?i:remd|gamd|qm/?mm|westpa|mmvt|martini|oniom|hrex|obc2|gbn2)"
_GLUED = re.compile(r"(?<=[a-z0-9])" + _GLUED_NAMES
                    + "|" + _GLUED_NAMES + r"(?=[a-z0-9]|[A-Z][a-z])"
                    + r"|(?<=[^\W_])" + _GLUED_ANY_CASE + "|" + _GLUED_ANY_CASE + r"(?=[^\W_])")


#: Words after a hyphen that show the name before it was whole ("QM/MM-
#: based", "REMD- and MD-based"), not a piece of a word broken there
#: ("reus-\nable", "unres-\ntrained").
_FOLLOWERS = (r"(?:based|like|type|style|and|or|driven|guided|biased|derived|enhanced|generated"
              r"|sampling|simulations?|runs?|mds?|trajector(?:y|ies)|models?|protocols?"
              r"|methods?|schemes?|calculations?|force)\b")
_WHOLE_BEFORE = re.compile(r"-\s+" + _FOLLOWERS, re.IGNORECASE)
_FOLLOWED = re.compile(r"\s+" + _FOLLOWERS, re.IGNORECASE)
#: Words before a hyphen at a line's end that make a whole name of the one
#: after it ("Hamiltonian-\nREMD", "pH-\nREMD").
_PREFIXED = re.compile(r"\b(?:hamiltonian|temperature|solute|replica|reservoir|ph|h|t)-\s+\Z",
                       re.IGNORECASE)


def _read_details(words: str, whole: Callable[[re.Match[str]], bool] | None = None
                  ) -> tuple[str | None, list[tuple[str, str]]]:
    """``whole``, where given, says whether an unqualified name found is
    whole; one that is not is put to the person."""
    found = sorted(((match, method) for method, pattern in _DETAIL_WORDS.items()
                    for match in re.finditer(pattern, words, re.IGNORECASE)),
                   key=lambda pair: pair[0].start())
    aside: list[tuple[str, str]] = []
    for match, method in found:
        sentence = _sentence_of(words, match)
        # Read with any break in it joined too: "with- out", "MM/ GBSA".
        sentence += " " + re.sub(r"(?<=[A-Za-z])\s*-\s+(?=[A-Za-z])|(?<=/)\s+", "", sentence)
        if _QUALIFIED.search(sentence) or (
                method in ("implicit_solvent", "free_energy") and _RESCORING.search(sentence)) or (
                whole is not None and not whole(match)):
            aside.append((_short(_sentence_of(words, match).strip(), 160), "mentioned"))
        else:
            return method, aside
    unnamed = list(words)
    for match, _method in found:  # the names themselves are not signs
        unnamed[match.start():match.end()] = " " * (match.end() - match.start())
    unnamed_text = "".join(unnamed)
    for signs in _SIGNS.values():
        sign = signs.search(unnamed_text)
        if sign:
            aside.append((sign.group(0).strip(), "signs"))
    plain = [(phrase.start(), phrase.end()) for phrase in _PLAIN_PHRASES.finditer(unnamed_text)]
    starts = [start for start, _end in plain]
    for loose in re.finditer(_LOOSE_WORDS, unnamed_text, re.IGNORECASE):
        inside = bisect.bisect_right(starts, loose.start()) - 1
        if inside >= 0 and loose.end() <= plain[inside][1]:
            continue
        aside.append((_short(_sentence_of(unnamed_text, loose).strip(), 160), "signs"))
    return None, aside


def _sentence_of(words: str, match: re.Match[str]) -> str:
    start = max(0, match.start() - 160)
    cuts = [cut.end() for cut in re.finditer(r"[.;]\s|:\s", words[start:match.start()])]
    head = words[start + (cuts[-1] if cuts else 0):match.start()]
    return head + match.group(0) + re.split(r"[.;]\s", words[match.end():match.end() + 200])[0]


def _method_in_details(words: str) -> str | None:
    return _methods_in_details(words)[0]


#: The kinds of replica exchange, and of methods read as one, named as the
#: paper names them where it names one kind alone.
_EXCHANGE_KINDS = (
    (r"simulated[-\s]*tempering", "simulated tempering"),
    (r"bias[-\s]*exchange", "bias-exchange metadynamics"),
    (r"(?:replica[-\s]*exchange\s+(?:with\s+)?)?solute[-\s]*tempering(?:\s+\(?rest2?\)?)?"
     r"|(?-i:\b[gG]?REST\b)|\bg?rest2\b", "replica exchange with solute tempering"),
    (r"\bh-?rex\b|\bh-?remd\b|hamiltonian[-\s]*replica[-\s]*exchange",
     "Hamiltonian replica exchange"),
)
_ANY_EXCHANGE = re.compile(r"replica[-\s]*exchange|\b[th]?-?remd\b|parallel[-\s]*tempering"
                           r"|\bh-?rex\b", re.IGNORECASE)


def _cannot(method: str, words: str) -> str:
    """What the study is, for a method this does not run: the kind of
    replica exchange the paper names, where it names that kind and no
    other exchange at all."""
    if method == "replica_exchange":
        words = _plain_text(words, look_alikes=True)
        kinds = [(pattern, name) for pattern, name in _EXCHANGE_KINDS
                 if re.search(pattern, words, re.IGNORECASE)]
        if len(kinds) == 1:
            rest = re.sub(kinds[0][0], " ", words, flags=re.IGNORECASE)
            if not _ANY_EXCHANGE.search(rest):
                return f"{kinds[0][1]}, which this software does not run"
    return _CANNOT[method]


_CANNOT = {
    "replica_exchange": "replica exchange, which this software does not run",
    "free_energy": "alchemical free-energy calculation, which this software does not run",
    "accelerated": "accelerated MD, which this software does not run",
    "qm_mm": "QM/MM, which this software does not run",
    "coarse_grained": "a coarse-grained model, which this software does not run",
    "implicit_solvent": "implicit solvent, where this software simulates explicit water",
    "milestoning": "milestoning or weighted ensemble, built from many short runs, "
                   "which this software does not run as one study",
}


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------
class _Plan:
    def __init__(self, study: dict[str, Any], source: str) -> None:
        self.study = study
        self.fields: dict[str, Any] = study.get("fields") or {}
        self.choices: list[Choice] = []
        self.config: dict[str, Any] = {"setup": {}, "simulation": {}}
        self.decisions: dict[str, Any] = {}
        self.source = source

    # -- reading -----------------------------------------------------------
    def stated(self, name: str) -> dict[str, Any] | None:
        record = self.fields.get(name)
        return record if isinstance(record, dict) and record.get("status") == "stated" else None

    def status(self, name: str) -> str:
        record = self.fields.get(name)
        return str(record.get("status")) if isinstance(record, dict) else "not_stated"

    # -- writing -----------------------------------------------------------
    def set(self, setting: str, value: Any, field: str, label: str, why: str,
            record: dict[str, Any] | None = None) -> None:
        phase, _, name = setting.partition(".")
        if name:
            self.config.setdefault(phase, {})[name] = value
        else:
            self.config[setting] = value
        quote = (record or {}).get("quote", "")
        where = (record or {}).get("where", "")
        self.choices.append(Choice(field, label, why, setting, value, quote, where))
        reason = why
        if quote:
            reason = f'{why} The paper ({where}): "{_short(quote)}"'
        self.decisions[setting] = {"why": reason, "source": self.source}

    def note(self, field: str, label: str, why: str,
             record: dict[str, Any] | None = None, setting: str = "") -> None:
        """A choice that sets nothing: said, and where the person sets it
        (``setting``) when it needs them."""
        self.choices.append(Choice(field, label, why, setting, None,
                                   quote=(record or {}).get("quote", ""),
                                   where=(record or {}).get("where", "")))


def _short(text: str, most: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text if len(text) <= most else text[: most - 1].rstrip() + "…"


#: What each way a setting was not read says, where it is said.
_UNCHECKED = {
    "not_found": "The AI model gave words for it the paper does not contain, so it "
                 "is not used:",
    "unread": "The paper's words the AI model gave do not hold the value it gave, so it "
              "is not used:",
    "by_reference": "The paper gives it by reference to another paper:",
    "in_si": "The paper gives it in its supporting information, which was not read "
             "(give it with `--paper-si`):",
}

#: The settings a study always has, which a study the paper is silent on
#: takes from this software: the field, where it is set, what is used, and
#: whether a value the paper gives and this could not check needs the person.
_ALWAYS = (
    ("temperature", "simulation.temperature_K", "300 K", True),
    ("pressure", "simulation.pressure_bar", "1 bar", False),
    ("timestep", "simulation.timestep_fs", "a 2 fs step", True),
    ("box_shape", "setup.box_shape", "a rhombic dodecahedron", False),
    ("padding", "setup.solvent_padding_nm", "1.0 nm of water to the nearest image", False),
    ("salt_concentration", "setup.ion_concentration_M", "0.15 M of NaCl", False),
    ("constraints", "setup.constraints", "bonds to hydrogen constrained", False),
    ("electrostatics", "setup.nonbonded_method", "particle-mesh Ewald", False),
    ("ensemble", "simulation.ensemble", "production at constant pressure", False),
    ("thermostat", "simulation.integrator", "Langevin dynamics", False),
    ("nvt_equilibration", "simulation.nvt_duration_ns", "500 ps of NVT equilibration", False),
    ("npt_equilibration", "simulation.npt_duration_ns", "1 ns of NPT equilibration", False),
    ("water_model", "", "TIP3P", True),
    ("replicas", "sweep", "one run", True),
)


def _unstated(plan: _Plan) -> None:
    """Each setting a study always has that nothing above has said: this
    software's value, said to be, and where the paper gave one this could
    not check, said so; a temperature, step or water model so given needs
    the person."""
    said = {choice.field for choice in plan.choices}
    setup = plan.config.get("setup") or {}
    charmm = "charmm" in str(setup.get("forcefield") or "") + str(setup.get("force_field") or "")
    for field, setting, used, needed in _ALWAYS:
        if field in said or plan.status(field) == "stated":
            continue
        if field == "water_model":
            used = "CHARMM's TIP3P" if charmm else "TIP3P"
        if field == "box_shape" and plan.stated("membrane"):
            plan.note(field, "not_stated", "Not stated: a bilayer's box is rectangular here.")
            continue
        status = plan.status(field)
        if status in _UNCHECKED and needed:
            plan.note(field, "needs_you",
                      f"{_UNCHECKED[status]} set it from the paper"
                      + (f" in `{setting}`" if setting else "")
                      + f" (this software's is {used}).",
                      plan.fields.get(field), setting=setting)
        elif status in _UNCHECKED:
            plan.note(field, "not_stated", f"{_UNCHECKED[status]} this software's {used} is "
                      "used.", plan.fields.get(field), setting=setting)
        else:
            plan.note(field, "not_stated", f"Not stated: this software's {used} is used.",
                      setting=setting)


def plan_study(study: dict[str, Any], reading: dict[str, Any], *,
               until_determined: bool = False, files: set[str] | None = None,
               output: str | None = None) -> dict[str, Any]:
    """What a study read from a paper becomes: ``choices`` (each a
    :class:`Choice` as a record), ``state`` (``ready``, ``with_differences``,
    ``needs_you``, ``cannot_run``), ``config`` (None where it cannot run),
    and ``simulated_ns``, the production it asks for over every replica.

    ``files`` is what this OpenMM ships (:func:`openmm_files`), or None
    where that cannot be known. ``until_determined`` adds a stopping rule
    to the paper's results with an error, the paper's production its
    ceiling."""
    source = reading.get("doi") and f"doi:{reading['doi']}" or "the paper"
    plan = _Plan(study, source)
    _structure(plan)
    _force_field(plan, files)
    _box_and_ions(plan)
    _physics(plan)
    _protocol(plan)
    method = _method(plan)
    _unstated(plan)
    state = _state_of(plan)

    production = (plan.config.get("simulation") or {}).get("duration_ns")
    replicas = int((plan.stated("replicas") or {}).get("value") or 1)
    simulated = float(production) * replicas if isinstance(production, (int, float)) else None

    config = None
    if state != "cannot_run":
        config = _config(plan, study, reading, method, output)
        if until_determined:
            _until_determined(plan, config, study)
            state = _state_of(plan)
            needs = [choice.why for choice in plan.choices if choice.label == "needs_you"]
            if needs:
                config["paper"]["needs"] = needs
    return {
        "id": study.get("id"),
        "label": study.get("label"),
        "state": state,
        "method": method,
        "choices": [choice.as_record() for choice in plan.choices],
        "config": config,
        "simulated_ns": simulated,
        "replicas": replicas,
        "claims": [c for c in study.get("claims") or [] if c.get("status") == "stated"],
    }


def _state_of(plan: _Plan) -> str:
    labels = {choice.label for choice in plan.choices}
    if "not_possible" in labels:
        return "cannot_run"
    if "needs_you" in labels:
        return "needs_you"
    if "differs" in labels:
        return "with_differences"
    return "ready"


def _structure(plan: _Plan) -> None:
    pdb = plan.stated("pdb_id")
    origin = plan.stated("structure_source")
    code = ""
    if pdb:
        found = re.findall(r"\b([0-9][A-Za-z0-9]{3})\b", str(pdb.get("value")))
        code = found[0].upper() if found else ""
    origin_text = str((origin or {}).get("value") or "") + " " + str((origin or {}).get("quote") or "")
    if origin and code and (code.lower() in origin_text.lower()
                            or re.search(r"crystal|x-ray|nmr|deposited|pdb", origin_text, re.I)) \
            and not re.search(r"model|alphafold|dock|homolog|built|predict|snapshot|previous",
                              origin_text, re.I):
        # The entry itself, perhaps with mutations or protonation made in
        # it, which are their own settings.
        origin = None
    if code and not origin:
        plan.set("systems", [{"system": code}], "pdb_id", "as_stated",
                 f"The structure is the PDB entry {code}, fetched from the RCSB.", pdb)
    elif code and origin:
        plan.set("systems", [{"system": code}], "structure_source", "needs_you",
                 f"The paper names the PDB entry {code}, and also that its starting "
                 f"structure was {origin.get('value')}: give that structure's file "
                 "if it is not the entry as deposited.", origin)
    elif origin:
        plan.set("systems", [{"system": STRUCTURE_TO_GIVE}], "structure_source", "needs_you",
                 f"The starting structure was {origin.get('value')}, which is not a "
                 "PDB entry this can fetch: give its file (the paper's deposit, if "
                 "it has one).", origin)
    else:
        plan.set("systems", [{"system": STRUCTURE_TO_GIVE}], "pdb_id", "needs_you",
                 "The paper names no PDB entry for this study: give its starting "
                 "structure.")
    chains = plan.stated("chains")
    if chains:
        letters = sorted({c for value in _as_list(chains.get("value"))
                          for c in re.findall(r"\b([A-Za-z])\b", str(value))})
        if letters:
            plan.set("setup.chains", letters, "chains", "as_stated",
                     f"The chains simulated: {', '.join(letters)}.", chains)
        else:
            plan.note("chains", "needs_you",
                      f"The paper says which chains as \"{chains.get('value')}\"; "
                      "name them by letter in `setup.chains`.", chains, setting="setup.chains")
    mutations = plan.stated("mutations")
    if mutations:
        from fastmdxplora.paper.extract import mutation_forms

        written = []
        for value in _as_list(mutations.get("value")):
            forms = sorted(form for form in mutation_forms(str(value))
                           if re.fullmatch(r"[A-Z]\d+[A-Z]", form))
            if forms:
                written.append(forms[0])
        if written:
            plan.set("setup.mutations", written, "mutations", "as_stated",
                     f"The mutations made: {', '.join(written)}. Each side chain is "
                     "placed by setup, not taken from a structure.", mutations)
        else:
            plan.note("mutations", "needs_you",
                      f"The mutations \"{mutations.get('value')}\" are not written as "
                      "point substitutions this can make: write them in "
                      "`setup.mutations`.", mutations, setting="setup.mutations")
    ligands = plan.stated("ligands")
    small = _ligands_of(ligands)
    if ligands and not small:
        plan.note("ligands", "as_stated",
                  f"{', '.join(map(str, _as_list(ligands.get('value'))))}: no small molecule "
                  "to parameterise. Ions bound in the entry are kept by setup.", ligands)
    elif ligands:
        if code and not origin:
            plan.note("ligands", "as_stated",
                      f"Kept beside the protein: {', '.join(small)}. "
                      "Setup keeps what the entry holds and can parameterise; its plan "
                      "says which, and is worth reading against this list.", ligands)
        else:
            plan.note("ligands", "needs_you",
                      f"The study holds {', '.join(small)}, "
                      "and its structure is not an entry this can fetch them from: "
                      "give each ligand's file (`setup.ligand`) with the structure.",
                      ligands, setting="setup.ligand")
    membrane = plan.stated("membrane")
    if membrane:
        from fastmdxplora.config.schema import PHASE_SCHEMAS

        allowed = next(f.choices for f in PHASE_SCHEMAS["setup"].fields if f.name == "membrane")
        said = str(membrane.get("value"))
        names = recognized("membrane", said) or set()
        # Cholesterol, not the choline of a phosphatidylcholine, and not
        # said to be absent ("cholesterol-free", "without cholesterol").
        mentions = list(re.finditer(r"chol(?!ine)\w*", said, re.IGNORECASE))
        if all(_lipid_absent(said, mention) for mention in mentions):
            names.discard("CHOL")
        lipids = names & set(allowed)
        # Another lipid named in a way the pattern does not read (PIP2,
        # sphingomyelin) makes a mixture as surely as one it does.
        others = (names - set(allowed)) | _other_lipids(said, names)
        for match in re.finditer(r"sphingo|gangliosid|cardiolipin|phosphoinositid|\bpip"
                                 r"|mixture|mixed|\d\s*:\s*\d", said, re.IGNORECASE):
            # A ratio stays a mixture even "per leaflet": "418:22 per leaflet"
            # gives the composition as surely as "95:5".
            if not _lipid_absent(said, match):
                others.add("mixture")
        if not names:
            plan.note("membrane", "needs_you",
                      f"The bilayer is {said}, which names no lipid this can build: set "
                      f"`setup.membrane` to one of {', '.join(allowed)}.", membrane,
                      setting="setup.membrane")
        elif len(lipids) == 1 and not others:
            lipid = lipids.pop()
            plan.set("setup.membrane", lipid, "membrane", "as_stated",
                     f"A bilayer of {lipid}.", membrane)
            left = _unexplained(said, lipid)
            if left:
                plan.note("membrane", "needs_you",
                          f"The bilayer is \"{_short(said, 160)}\"; this builds {lipid} alone, "
                          f"and does not read {', '.join(left[:6])}: confirm the bilayer is "
                          f"{lipid} alone, or set `setup.membrane`.", membrane,
                          setting="setup.membrane")
        else:
            plan.note("membrane", "not_possible",
                      f"The bilayer is {said}; this software builds a bilayer of one "
                      f"lipid, of {', '.join(allowed)}.", membrane)
    copies = plan.stated("copies")
    if copies and int(copies.get("value") or 1) > 1:
        plan.note("copies", "needs_you",
                  f"The box holds {copies.get('value')} copies; setup simulates the "
                  "structure as its biological assembly, so give a structure with "
                  "every copy placed if the entry's assembly is not it.", copies,
                  setting="systems")


def _force_field(plan: _Plan, files: set[str] | None) -> None:
    protein = plan.stated("protein_forcefield")
    nucleic = plan.stated("nucleic_forcefield")
    lipid = plan.stated("lipid_forcefield")
    water = plan.stated("water_model")
    ligand_ff = plan.stated("ligand_forcefield")
    has_ligand = bool(_ligands_of(plan.stated("ligands")))
    if not any((protein, nucleic, lipid, water)):
        said = plan.status("protein_forcefield")
        if said in _UNCHECKED:
            plan.note("protein_forcefield", "needs_you",
                      f"{_UNCHECKED[said]} the force field: choose it from the paper in "
                      "`setup.forcefield` (this software's own is ff14SB with TIP3P).",
                      plan.fields.get("protein_forcefield"), setting="setup.forcefield")
            return
        plan.note("protein_forcefield", "not_stated",
                  "The paper names no force field: this software's own is used "
                  "(ff14SB with TIP3P water, and OpenFF Sage for ligands).")
        return
    parts = []
    for role, record, table in (("protein", protein, _PROTEIN), ("nucleic", nucleic, _NUCLEIC),
                                ("lipid", lipid, _LIPID)):
        if not record:
            continue
        entry = _match(table, str(record.get("value")))
        name = f"{role}_forcefield"
        if entry is None:
            plan.note(name, "needs_you",
                      f"{record.get('value')} is not a force field this software "
                      "knows by name: choose the nearest it has, or give its OpenMM "
                      "files in `setup.force_field`.", record)
            return
        if entry[2] is None:
            plan.note(name, "not_possible",
                      f"{entry[0]}, a polarizable force field, which this software does "
                      "not run." if entry[0] in ("Drude", "AMOEBA") else
                      f"{entry[0]}, which OpenMM does not ship.", record)
            return
        parts.append((role, entry, record))
    if any(role == "protein" and entry[3] == "legacy" for role, entry, _ in parts) \
            and any(role == "nucleic" for role, _entry, _ in parts):
        legacy = next(entry for role, entry, _ in parts if role == "protein")
        plan.note("nucleic_forcefield", "not_possible",
                  f"{legacy[0]} beside a nucleic-acid force field: OpenMM's {legacy[2]} "
                  "carries its own nucleic-acid parameters, so the two cannot be loaded "
                  "together.", nucleic)
        return
    families = {entry[3] for _role, entry, _record in parts}
    if families and families <= {"charmm36", "charmm36_2024"}:
        # One CHARMM file carries protein, nucleic acids and lipids alike;
        # CHARMM36m's is the 2024 one, which carries all three too.
        newest = "charmm36_2024" if "charmm36_2024" in families else "charmm36"
        chosen = next(entry for _role, entry, _record in parts if entry[3] == newest)
        parts = [(role, chosen if role == parts[0][0] else entry, record)
                 for role, entry, record in parts]
        families = {newest}
    if len(families - {"amber14", "amber19", "legacy"}) and len(families) > 1:
        plan.note("protein_forcefield", "not_possible",
                  "The force fields named come from different families ("
                  + ", ".join(entry[0] for _r, entry, _rec in parts) + "), which are "
                  "not combined here.")
        return
    family = parts[0][1][3] if parts else "amber14"
    if "amber19" in families:
        family = "amber19"
    water_entry = _match(_WATER, str(water.get("value"))) if water else None
    if water and water_entry is None:
        plan.note("water_model", "needs_you",
                  f"{water.get('value')} is not a water model this software knows.", water)
        return
    if water_entry is not None and water_entry[2] is None:
        plan.note("water_model", "not_possible",
                  f"{water_entry[0]}, which OpenMM does not ship.", water)
        return
    stem = water_entry[2] if water_entry else ("water" if family.startswith("charmm") else "tip3p")
    if family.startswith("charmm") and stem == "tip3p":
        stem = "water"  # CHARMM's TIP3P is its own, with hydrogens' Lennard-Jones
    if not family.startswith("charmm") and stem == "water":
        plan.note("water_model", "not_possible",
                  "CHARMM's modified TIP3P beside an AMBER force field, which OpenMM "
                  "does not ship as a pair.", water)
        return
    if stem not in _WATERS_IN.get(family, set()):
        plan.note("water_model", "not_possible",
                  f"{water_entry[0] if water_entry else stem} with "
                  f"{parts[0][1][0] if parts else 'this force field'}, a pair OpenMM "
                  "does not ship.", water)
        return
    geometry = water_entry[3] if water_entry else "tip3p"
    names = [entry[0] for _role, entry, _record in parts]
    said_water = water_entry[0] if water_entry else "TIP3P"
    if stem == "water":
        said_water = "CHARMM's TIP3P"

    if has_ligand:
        # A ligand is parameterised here only through `amber-openff`:
        # AMBER14's files (ff14SB, OL15, OL3, Lipid17), TIP3P and a
        # small-molecule force field.
        if families - {"amber14", "amber19", "legacy"}:
            plan.note("protein_forcefield", "not_possible",
                      f"{', '.join(names)} with a ligand: a ligand is parameterised here only "
                      "beside AMBER's ff14SB.", protein or nucleic or lipid)
            return
        for role, entry, record in parts:
            if role != "protein" and entry[0] not in ("OL15", "OL3", "Lipid17"):
                plan.note(f"{role}_forcefield", "differs",
                          f"AMBER14's {'OL15 and OL3' if role == 'nucleic' else 'Lipid17'} "
                          f"in place of the paper's {entry[0]}: beside a ligand this "
                          "software's force field carries AMBER14's files.", record)
        protein_entry = next((e for r, e, _ in parts if r == "protein"), None)
        if protein_entry is None:
            plan.set("setup.forcefield", "amber-openff", "protein_forcefield", "not_stated",
                     "The paper names no protein force field: ff14SB is used, with the "
                     "ligand parameterised beside it.")
        elif protein_entry[0] not in ("ff14SB",):
            if protein_entry[3] in ("legacy", "amber14", "amber19"):
                plan.set("setup.forcefield", "amber-openff", "protein_forcefield", "differs",
                         f"ff14SB in place of the paper's {protein_entry[0]}: a ligand is "
                         "parameterised here only beside ff14SB (`amber-openff`). Both are "
                         "AMBER protein force fields, and ff14SB was refitted from ff99SB.",
                         protein)
            else:
                plan.note("protein_forcefield", "not_possible",
                          f"{protein_entry[0]} with a ligand: a ligand is parameterised here "
                          "only beside ff14SB.", protein)
                return
        else:
            plan.set("setup.forcefield", "amber-openff", "protein_forcefield", "as_stated",
                     "ff14SB for the protein, with the ligand parameterised beside it.",
                     protein)
        if said_water != "TIP3P":
            plan.note("water_model", "differs",
                      f"TIP3P in place of the paper's {said_water}: beside a ligand this "
                      "software's force field carries TIP3P.", water)
        elif water:
            plan.note("water_model", "as_stated", "TIP3P.", water)
        _ligand_forcefield(plan, ligand_ff)
        return

    named = None
    if (set(names) <= {"ff14SB", "OL15", "OL3", "Lipid17"} and said_water == "TIP3P"):
        named = "amber14"
    elif set(names) <= {"CHARMM36"} and names and stem == "water":
        named = "charmm36"
    elif names == ["AMBER-FB15"] and said_water == "TIP3P":
        named = "amber-fb15"
    if named:
        label_why = {"amber14": "ff14SB (AMBER14's files, with OL15 DNA, OL3 RNA and Lipid17) "
                                "with TIP3P.",
                     "charmm36": "CHARMM36 with CHARMM's TIP3P.",
                     "amber-fb15": "AMBER-FB15 with TIP3P."}[named]
        if protein or nucleic or lipid:
            plan.set("setup.forcefield", named, "protein_forcefield", "as_stated", label_why,
                     protein or nucleic or lipid)
        else:
            plan.set("setup.forcefield", named, "protein_forcefield", "not_stated",
                     f"The paper names its water model only: {label_why}")
        if water:
            plan.note("water_model", "as_stated", f"{said_water}.", water)
        return

    xmls = list(dict.fromkeys(entry[2] for _role, entry, _record in parts))
    if family.startswith("charmm"):
        xmls = [f"{family}.xml"]
    elif not protein:
        # No protein force field named: the software's own for any protein,
        # said so, beside what the paper names.
        xmls.insert(0, "amber14/protein.ff14SB.xml")
        if parts:
            plan.note("protein_forcefield", "not_stated",
                      "The paper names no protein force field: ff14SB is used for any "
                      "protein.")
    xmls.append(_water_file(family, stem))
    if plan.stated("membrane") and not lipid and not family.startswith("charmm"):
        # A bilayer needs lipid parameters, which an AMBER list names only in
        # AMBER14's bundle: Lipid17 beside the paper's, said so.
        if family != "amber14":
            xmls.insert(len(xmls) - 1, "amber14/lipid17.xml")
        plan.note("lipid_forcefield", "not_stated",
                  "The paper names no lipid force field: Lipid17 is used for the bilayer"
                  + (f", which was fitted with TIP3P water, not {said_water}."
                     if said_water != "TIP3P" else "."))
    missing = sorted(x for x in xmls if files is not None and x not in files)
    newest = max(([entry[4] for _r, entry, _rec in parts if entry[4]] or ["8.0"]),
                 key=lambda v: tuple(int(p) for p in v.split(".")))
    if missing:
        plan.set("setup.force_field", xmls, "protein_forcefield", "needs_you",
                 f"{', '.join(names)} with {said_water}: this installation's OpenMM does "
                 f"not have {', '.join(missing)}; OpenMM {newest} or later does.",
                 protein or nucleic or lipid)
    else:
        why = f"{', '.join(names or ['ff14SB'])} with {said_water}, from the files OpenMM ships."
        if files is None and newest != "8.0":
            why += f" Needs OpenMM {newest} or later."
        plan.set("setup.force_field", xmls, "protein_forcefield",
                 "as_stated" if (protein or nucleic or lipid) else "not_stated", why,
                 protein or nucleic or lipid)
    if geometry != "tip3p":
        plan.set("setup.water_model", geometry, "water_model", "as_stated",
                 f"{said_water}, placed with OpenMM's {geometry} geometry.", water)
    elif water:
        plan.note("water_model", "as_stated", f"{said_water}.", water)
    # A list of files carries no cutoff of its own, and setup's fallback
    # switches: each family's own scheme is written where the paper is
    # silent, and said to be.
    cutoff = plan.stated("cutoff")
    if family.startswith("charmm") and not plan.stated("switch"):
        if not cutoff:
            plan.set("setup.nonbonded_cutoff_nm", 1.2, "cutoff", "not_stated",
                     "Not stated: CHARMM36's own cutoff, 1.2 nm, switched from 1.0 nm.")
        at = round(float(cutoff["value"]) - 0.2, 6) if cutoff else 1.0
        plan.set("setup.use_switching_function", True, "switch", "not_stated",
                 "Not stated: CHARMM36 is developed with switching.")
        plan.set("setup.switch_distance_nm", at, "switch", "not_stated",
                 f"Not stated: switched from {at:g} nm, 0.2 nm inside the cutoff, as "
                 "CHARMM36 is.")
    elif not family.startswith("charmm"):
        if not plan.stated("switch"):
            plan.set("setup.use_switching_function", False, "switch", "not_stated",
                     "Not stated: AMBER force fields are run with a plain cutoff, never "
                     "switched.")
        if not cutoff:
            plan.set("setup.nonbonded_cutoff_nm", 1.0, "cutoff", "not_stated",
                     "Not stated: a 1.0 nm cutoff, as AMBER force fields are run here.")


def _ligand_forcefield(plan: _Plan, record: dict[str, Any] | None) -> None:
    if not record:
        plan.note("ligand_forcefield", "not_stated",
                  "The paper does not say how the ligand was parameterised: OpenFF "
                  "Sage 2.2.1 is used.")
        return
    reduced = squash(str(record.get("value")))[0]
    charges = plan.stated("ligand_charges")
    if re.search(r"gaff2|gaff\-?2", reduced):
        plan.set("setup.ligand_forcefield", "gaff-2.11", "ligand_forcefield", "as_stated",
                 "GAFF2 (2.11), with AM1-BCC charges.", record)
    elif "gaff" in reduced:
        plan.set("setup.ligand_forcefield", "gaff-1.81", "ligand_forcefield", "as_stated",
                 "GAFF (1.81), with AM1-BCC charges.", record)
    elif "openff" in reduced or "sage" in reduced or "parsley" in reduced:
        version = re.search(r"(\d\.\d(?:\.\d)?)", str(record.get("value")))
        name = f"openff-{version.group(1)}" if version else "openff-2.2.1"
        plan.set("setup.ligand_forcefield", name, "ligand_forcefield", "as_stated",
                 f"OpenFF ({name}).", record)
        return
    else:
        plan.note("ligand_forcefield", "differs",
                  f"OpenFF Sage 2.2.1 in place of the paper's {record.get('value')}, which "
                  "this software does not parameterise with.", record)
        return
    if charges and re.search(r"resp|hf", squash(str(charges.get("value")))[0]):
        plan.note("ligand_charges", "differs",
                  "AM1-BCC charges in place of the paper's RESP, which needs a quantum "
                  "calculation this software does not run.", charges)


def _box_and_ions(plan: _Plan) -> None:
    shape = plan.stated("box_shape")
    if shape:
        reduced = squash(str(shape.get("value")))[0]
        if "octahedr" in reduced:
            plan.set("setup.box_shape", "octahedron", "box_shape", "as_stated",
                     "A truncated octahedron.", shape)
        elif "dodecahedr" in reduced:
            plan.set("setup.box_shape", "dodecahedron", "box_shape", "as_stated",
                     "A rhombic dodecahedron.", shape)
        elif "cub" in reduced:
            plan.set("setup.box_shape", "cube", "box_shape", "as_stated", "A cube.", shape)
        elif plan.stated("membrane"):
            plan.note("box_shape", "as_stated",
                      f"{shape.get('value')}: a bilayer's box is rectangular here too.", shape)
        else:
            plan.note("box_shape", "differs",
                      f"The paper's box is {shape.get('value')}; this software makes a "
                      "cube, a dodecahedron or an octahedron, and the dodecahedron is "
                      "used.", shape)
    padding = plan.stated("padding")
    if padding:
        to_edge = float(padding["value"])
        plan.set("setup.solvent_padding_nm", round(2 * to_edge, 6), "padding", "as_stated",
                 f"{to_edge:g} nm from the solute to the box's edge, as papers measure "
                 f"it, is {2 * to_edge:g} nm to the nearest periodic image, as padding is "
                 "measured here: the same box.", padding)
    salt = plan.stated("salt_concentration")
    neutralized = plan.stated("neutralized")
    if salt:
        plan.set("setup.ion_concentration_M", float(salt["value"]), "salt_concentration",
                 "as_stated", f"{float(salt['value']):g} M of salt.", salt)
    elif neutralized and neutralized.get("value"):
        plan.set("setup.ion_concentration_M", 0.0, "salt_concentration", "not_stated",
                 "The paper names only the ions that neutralised it, so no salt is "
                 "added beyond them. Set a concentration if salt was added.", neutralized)
    if neutralized and neutralized.get("value") is False:
        plan.set("setup.neutralize", False, "neutralized", "as_stated",
                 "Not neutralised by counter-ions: the net charge is offset by PME's "
                 "uniform background.", neutralized)
    ions = plan.stated("ions")
    if ions:
        said = " ".join(map(str, _as_list(ions.get("value"))))
        reduced = said.lower()
        positive = ("K+" if re.search(r"\bk\b|k\+|potassium|kcl", reduced) else
                    "Na+" if re.search(r"\bna\b|na\+|sodium|nacl", reduced) else None)
        negative = "Cl-" if re.search(r"\bcl\b|cl-|chloride|kcl|nacl", reduced) else None
        if positive:
            plan.set("setup.ion_positive", positive, "ions", "as_stated", f"{positive}.", ions)
        if negative:
            plan.set("setup.ion_negative", negative, "ions", "as_stated", f"{negative}.", ions)
        if not positive and not negative:
            plan.note("ions", "differs",
                      f"The paper's ions ({said}) are not salt this software adds; Na+ "
                      "and Cl- are used, and ions bound in the structure are kept by "
                      "setup.", ions)
    protonation = plan.stated("protonation")
    if protonation:
        ph = re.search(r"\bpH\s*(?:of\s*)?(\d+(?:\.\d+)?)", str(protonation.get("quote", "")))
        if ph:
            plan.set("setup.ph", float(ph.group(1)), "protonation", "as_stated",
                     f"Protonation states for pH {ph.group(1)}.", protonation)
        else:
            plan.note("protonation", "needs_you",
                      f"The paper sets protonation as \"{_short(protonation.get('value'), 120)}\": "
                      "set each residue's state in `setup.residue_states`, or accept "
                      "setup's states at pH 7.4.", protonation, setting="setup.residue_states")


def _physics(plan: _Plan) -> None:
    temperature = plan.stated("temperature")
    if temperature:
        value = float(temperature["value"])
        plan.set("simulation.temperature_K", value, "temperature", "as_stated",
                 f"{value:g} K.", temperature)
        plan.config["setup"]["temperature_K"] = value
    pressure = plan.stated("pressure")
    if pressure:
        plan.set("simulation.pressure_bar", round(float(pressure["value"]), 6), "pressure",
                 "as_stated", f"{float(pressure['value']):g} bar.", pressure)
    ensemble = plan.stated("ensemble")
    if ensemble:
        reduced = squash(str(ensemble.get("value")))[0]
        if "nvt" in reduced or "canonical" in reduced or "constantvolume" in reduced:
            plan.set("simulation.ensemble", "nvt", "ensemble", "as_stated",
                     "Production at constant volume, after equilibration at constant "
                     "pressure.", ensemble)
        elif "npt" in reduced or "isobaric" in reduced or "constantpressure" in reduced:
            plan.set("simulation.ensemble", "npt", "ensemble", "as_stated",
                     "Production at constant pressure.", ensemble)
    thermostat = plan.stated("thermostat")
    if thermostat:
        reduced = squash(str(thermostat.get("value")))[0]
        if "langevin" in reduced or "stochastic" in reduced or reduced in ("sd", "baoab"):
            plan.set("simulation.integrator", "langevin_middle", "thermostat", "as_stated",
                     "Langevin dynamics (OpenMM's LangevinMiddle integrator).", thermostat)
        else:
            plan.note("thermostat", "differs",
                      f"Langevin dynamics in place of the paper's {thermostat.get('value')}: "
                      "OpenMM's runner here keeps temperature by Langevin dynamics, which "
                      "samples the same canonical distribution"
                      + (" (Berendsen's does not, quite)" if "berendsen" in reduced else "")
                      + ".", thermostat)
    barostat = plan.stated("barostat")
    if barostat:
        reduced = squash(str(barostat.get("value")))[0]
        if "montecarlo" in reduced or reduced == "mc":
            plan.note("barostat", "as_stated", "A Monte Carlo barostat.", barostat)
        else:
            plan.note("barostat", "differs",
                      f"A Monte Carlo barostat in place of the paper's {barostat.get('value')}, "
                      "as OpenMM keeps pressure; it samples the same isobaric "
                      "distribution" + (" (Berendsen's does not, quite)" if "berendsen" in reduced
                                        else "") + ".", barostat)
    timestep = plan.stated("timestep")
    if timestep:
        plan.set("simulation.timestep_fs", float(timestep["value"]), "timestep", "as_stated",
                 f"{float(timestep['value']):g} fs.", timestep)
    constraints = plan.stated("constraints")
    if constraints:
        reduced = squash(str(constraints.get("value")) + " " + str(constraints.get("quote")))[0]
        if re.search(r"allbonds|allbond|everybond", reduced):
            plan.set("setup.constraints", "AllBonds", "constraints", "as_stated",
                     "Every bond constrained.", constraints)
        elif re.search(r"hydrogen|hbond|tohydrogen|xh|h\-?bonds", reduced):
            plan.set("setup.constraints", "HBonds", "constraints", "as_stated",
                     "Bonds to hydrogen constrained (by OpenMM's own algorithm, whatever "
                     "the paper's program called it).", constraints)
        elif re.search(r"shake|lincs|settle|rattle", reduced):
            plan.note("constraints", "not_stated",
                      "The paper names its constraint algorithm, not which bonds: bonds "
                      "to hydrogen are constrained, as is usual with it.", constraints)
        elif re.search(r"none|noconstraint|flexible", reduced):
            plan.set("setup.constraints", "None", "constraints", "as_stated",
                     "No bonds constrained.", constraints)
    hmass = plan.stated("hydrogen_mass")
    step = plan.stated("timestep")
    if hmass:
        plan.set("setup.hydrogen_mass_amu", float(hmass["value"]), "hydrogen_mass", "as_stated",
                 f"Hydrogen mass repartitioned to {float(hmass['value']):g} amu.", hmass)
    elif step and float(step["value"]) >= 3.0:
        plan.note("hydrogen_mass", "needs_you",
                  f"A {float(step['value']):g} fs step needs hydrogen mass repartitioning, "
                  "and the paper does not say the mass: set `setup.hydrogen_mass_amu` "
                  "(AMBER's is 3.024 amu).", setting="setup.hydrogen_mass_amu")
    cutoff = plan.stated("cutoff")
    if cutoff:
        plan.set("setup.nonbonded_cutoff_nm", float(cutoff["value"]), "cutoff", "as_stated",
                 f"{float(cutoff['value']):g} nm.", cutoff)
    switch = plan.stated("switch")
    if switch:
        force = "force" in squash(str(switch.get("value")) + str(switch.get("quote")))[0]
        plan.set("setup.use_switching_function", True, "switch",
                 "differs" if force else "as_stated",
                 "A switch on the potential: OpenMM switches the van der Waals potential, "
                 "not the force as GROMACS's force-switch does." if force else
                 "Van der Waals switched.", switch)
        plan.set("setup.switch_distance_nm", float(switch["value"]), "switch",
                 "differs" if force else "as_stated",
                 f"Switched from {float(switch['value']):g} nm.", switch)
    electrostatics = plan.stated("electrostatics")
    if electrostatics:
        reduced = squash(str(electrostatics.get("value")))[0]
        if "pme" in reduced or "particlemesh" in reduced or "spme" in reduced:
            plan.set("setup.nonbonded_method", "PME", "electrostatics", "as_stated",
                     "Particle-mesh Ewald.", electrostatics)
        elif "gaussiansplit" in reduced or "gse" in reduced:
            plan.set("setup.nonbonded_method", "PME", "electrostatics", "differs",
                     "Particle-mesh Ewald in place of Gaussian split Ewald: both sum "
                     "the same long-range electrostatics.", electrostatics)
        elif "ewald" in reduced:
            plan.set("setup.nonbonded_method", "Ewald", "electrostatics", "as_stated",
                     "Ewald summation.", electrostatics)
        else:
            plan.set("setup.nonbonded_method", "PME", "electrostatics", "differs",
                     f"Particle-mesh Ewald in place of the paper's {electrostatics.get('value')}, "
                     "which this software does not do.", electrostatics)


def _protocol(plan: _Plan) -> None:
    for field, setting in (("nvt_equilibration", "simulation.nvt_duration_ns"),
                           ("npt_equilibration", "simulation.npt_duration_ns")):
        record = plan.stated(field)
        if record:
            plan.set(setting, float(record["value"]), field, "as_stated",
                     f"{float(record['value']):g} ns.", record)
    restraints = plan.stated("equilibration_restraints")
    if restraints:
        reduced = squash(str(restraints.get("value")))[0]
        selection = ("protein and backbone" if "backbone" in reduced else
                     "protein and name CA" if re.search(r"calpha|cα|ca\b|alphacarbon", reduced) else
                     "protein and not element H" if re.search(r"heavy|nonhydrogen", reduced)
                     else None)
        if selection:
            plan.set("simulation.restrain", selection, "equilibration_restraints", "differs",
                     f"{selection} held during equilibration, released in this software's "
                     "steps (1000, 500, 100, 0 kJ/mol/nm²), which may not be the paper's.",
                     restraints)
        else:
            plan.note("equilibration_restraints", "differs",
                      f"The paper restrained {restraints.get('value')}; equilibration here "
                      "follows this software's own steps.", restraints)
    production = plan.stated("production")
    if production:
        plan.set("simulation.duration_ns", float(production["value"]), "production",
                 "as_stated", f"{float(production['value']):g} ns of production"
                 + (" per replica." if plan.stated("replicas") else "."), production)
    else:
        record = plan.fields.get("production")
        plan.note("production", "needs_you",
                  "The paper does not state this study's production in words this can "
                  "read: set `simulation.duration_ns`."
                  + (f' (It says: "{_short(record.get("quote"), 120)}")'
                     if isinstance(record, dict) and record.get("quote") else ""),
                  setting="simulation.duration_ns")
    replicas = plan.stated("replicas")
    if replicas and int(replicas["value"]) > 1:
        plan.note("replicas", "as_stated",
                  f"{int(replicas['value'])} replicas, each from its own random seed.",
                  replicas)
    engine = plan.stated("engine")
    if engine:
        said = str(engine.get("value"))
        if "openmm" in said.lower():
            plan.note("engine", "as_stated", "OpenMM, as here.", engine)
        else:
            plan.note("engine", "differs",
                      f"Run here in OpenMM; the paper used {said}.", engine)


#: The most places quoted in one question about a study's method details.
_MOST_QUOTED = 12


def _method(plan: _Plan) -> str:
    record = plan.stated("method")
    method = _method_of(str(record.get("value"))) if record else "plain"
    details = plan.stated("method_details")
    aside: list[tuple[str, str]] = []
    if details and method not in _CANNOT:
        # The details may name a method the AI model's word does not: one
        # named with nothing that qualifies it makes the study that method;
        # one named or described otherwise is put to the person.
        words = str(details.get("value")) + ". " + str(details.get("quote") or "")
        said, aside = _methods_in_details(words)
        if said:
            method = said
            record = details
    if method in _CANNOT:
        words = " ".join(str((record or {}).get(key) or "") for key in ("value", "quote"))
        plan.note("method", "not_possible", f"The study is {_cannot(method, words)}.", record)
        return method
    if aside:
        planned = {"plain": "plain MD", "other": "the method the paper states"}.get(
            method, method.replace("_", " "))
        said = [f'"{words}"' for words in dict.fromkeys(name for name, _kind in aside)]
        if len(said) > _MOST_QUOTED:
            said = said[:_MOST_QUOTED] + [f"and {len(said) - _MOST_QUOTED} more places"]
        plan.note("method_details", "needs_you",
                  f"Planned as {planned}. Its details say " + "; ".join(said)
                  + ", which names or describes a method this software does not run, or "
                  "may: read them, and confirm the study itself is not that method before "
                  "it runs.", details)
    if method in ("umbrella", "metadynamics", "steered"):
        block = {"umbrella": "umbrella", "metadynamics": "metadynamics",
                 "steered": "steered"}[method]
        plan.note("method", "needs_you",
                  f"The study is {method.replace('_', ' ')} sampling: write its collective "
                  f"variable and settings in `simulation.{block}` from the paper's"
                  + (f' "{_short(details.get("value"), 160)}"' if details else " methods")
                  + ".", record, setting=f"simulation.{block}")
    elif method == "other":
        plan.note("method", "needs_you",
                  f"The study's method is {record.get('value') if record else 'not plain MD'}, "
                  "which this does not recognise: read the paper's methods before running.",
                  record)
    return method


def _config(plan: _Plan, study: dict[str, Any], reading: dict[str, Any], method: str,
            output: str | None) -> dict[str, Any]:
    config: dict[str, Any] = {}
    systems = plan.config.pop("systems", None) or [{"system": STRUCTURE_TO_GIVE}]
    entry = dict(systems[0])
    entry["id"] = slug(f"{study.get('id')}-{study.get('label')}", 32)
    config["systems"] = [entry]
    title = reading.get("title") or "the paper"
    config["output"] = output or f"runs/{slug(title, 28)}-{slug(str(study.get('id')), 8)}"
    for phase in ("setup", "simulation"):
        if plan.config.get(phase):
            config[phase] = dict(plan.config[phase])
    replicas = plan.stated("replicas")
    if replicas and int(replicas["value"]) > 1:
        config["sweep"] = {"simulation.random_seed": list(range(1, int(replicas["value"]) + 1))}
        plan.decisions["sweep"] = {
            "why": f"{int(replicas['value'])} replicas, each from its own random seed. "
                   f'The paper ({replicas.get("where")}): "{_short(replicas.get("quote"))}"',
            "source": plan.source}
    config["report"] = {"title": f"{study.get('label')}: reproducing {_short(title, 90)}"}
    decisions = dict(plan.decisions)
    if decisions:
        config["decisions"] = decisions
    config["paper"] = {
        "doi": reading.get("doi") or None,
        "title": title,
        "study": study.get("id"),
        "label": study.get("label"),
        "read_by": reading.get("model") or None,
        "read": reading.get("made") or None,
        "paper_sha256": reading.get("paper_sha256") or None,
        "choices": [choice.as_record() for choice in plan.choices],
        "claims": [_claim_record(claim) for claim in study.get("claims") or []
                   if claim.get("status") == "stated"],
        # What must be supplied before it runs: the validator refuses the
        # study while any is left, so none is forgotten.
        "needs": [choice.why for choice in plan.choices if choice.label == "needs_you"] or None,
    }
    config["paper"] = {key: value for key, value in config["paper"].items() if value is not None}
    return config


def _claim_record(claim: dict[str, Any]) -> dict[str, Any]:
    keep = ("quantity", "analysis", "what", "value", "error", "error_kind", "n", "unit",
            "quote", "where")
    return {key: claim[key] for key in keep if claim.get(key) not in (None, "")}


def _until_determined(plan: _Plan, config: dict[str, Any], study: dict[str, Any]) -> None:
    """A stopping rule from the paper's results that carry an error: each to
    the paper's own standard error, in the analysis's unit; the paper's
    production the ceiling, and a fifth of it the first piece."""
    from fastmdxplora.paper.reproduction import paper_standard_error, to_unit_of

    simulation = config.setdefault("simulation", {})
    ceiling = simulation.get("duration_ns")
    if not isinstance(ceiling, (int, float)) or ceiling <= 0:
        plan.note("production", "needs_you",
                  "Running until the results are determined needs the paper's production "
                  "as its ceiling, which it does not state.")
        return
    measures = []
    seen = set()
    for claim in config.get("paper", {}).get("claims", []):
        analysis = claim.get("analysis")
        if analysis not in _JUDGEABLE or analysis in seen:
            continue
        error = paper_standard_error(claim)
        if error is None:
            continue
        converted = to_unit_of(error, claim.get("unit") or "", analysis)
        if converted is None or converted <= 0:
            continue
        seen.add(analysis)
        measures.append({"analysis": analysis, "standard_error": round(converted, 6)})
    if not measures:
        plan.note("production", "not_stated",
                  "No result the paper reports carries an error this can stop at, so the "
                  "study runs the paper's full length.")
        return
    simulation["stop_when"] = {"measures": measures, "max_duration_ns": float(ceiling)}
    if not config.get("sweep"):
        simulation["stop_when"]["independent_starts"] = "not_required"
    simulation["duration_ns"] = round(max(float(ceiling) / 5.0, 1.0), 3)
    plan.choices.append(Choice(
        "production", "differs",
        "Run until " + ", ".join(m["analysis"] for m in measures) + " is determined to "
        "the paper's own error, at most the paper's "
        f"{float(ceiling):g} ns: a shorter run where that is enough.",
        "simulation.stop_when", simulation["stop_when"]))
    config["paper"]["choices"] = [choice.as_record() for choice in plan.choices]


#: The analyses a stopping rule can judge: each records one mean.
_JUDGEABLE = ("rmsd", "rg", "sasa", "end_to_end", "ligand_rmsd", "area_per_lipid",
              "bilayer_thickness", "hbonds")


#: Entries of a study's ligands that are no small molecule to parameterise:
#: ions, water, and saying there is none.
_NOT_A_LIGAND = re.compile(
    r"^\s*(?:none|no\b|n/a|apo\b|removed|without)|\bions?\b|\bwaters?\b|\bsolvent\b"
    r"|^\s*(?:na|k|cl|mg|ca|zn|mn|fe|cu|co|ni|cd|li|cs|rb|sr|ba|br|i|f)\s*\d*\s*[+-]*\s*$"
    r"|^\s*(?:sodium|potassium|chloride|magnesium|calcium|zinc|manganese|iron|copper|"
    r"cobalt|nickel|cadmium|lithium|caesium|cesium)\b", re.IGNORECASE)


def _lipid_absent(said: str, match: re.Match[str]) -> bool:
    """Whether the lipid named at ``match`` is said to be absent: "SM-free",
    "cholesterol-free", "without cholesterol", "no PIP2"."""
    after = said[match.end():match.end() + 24]
    before = said[max(0, match.start() - 24):match.start()]
    rest = after[re.match(r"\w*", after).end():]
    if re.match(r"-(?:free|depleted|deficient|lacking)\b"
                r"|\s+(?:were\s+|are\s+|was\s+|is\s+)?(?:absent|excluded|omitted)\b",
                rest, re.IGNORECASE):
        return True
    return bool(re.search(r"\b(?:no|without|free\s+of|devoid\s+of|lacking|absence\s+of)"
                          r"\s+(?:any\s+)?$", before, re.IGNORECASE))


def _chloride(said: str, match: re.Match[str]) -> bool:
    """Whether "CL" at ``match`` is chloride, not cardiolipin: a charge sign
    after it, then "ions" or "counterions", words that it neutralises, or
    beside Na+ or K+ ("Na+/CL-", "(Na+, CL-)"); never with a share of a
    bilayer before or after it ("20% CL-", "POPC/CL-", "CL- 20%") or called
    a lipid ("CL- lipids")."""
    before = said[max(0, match.start() - 24):match.start()]
    after = said[match.end():match.end() + 30]
    if re.search(r"(?:%|\bpercent|\bmol)\s*$", before, re.IGNORECASE) \
            or re.search(r"\b[A-Z]{2,5}\d?\s*[/:]\s*$", before):
        return False
    if not re.match(r"\s*[-−⁻]", after):
        return False
    rest = re.sub(r"^\s*[-−⁻]+", "", after)
    if re.match(r"\s*(?:\(?\s*\d|lipids?\b|\(\s*\d)", rest, re.IGNORECASE):
        return False
    return bool(re.match(r"\s*(?:counter[-\s]?)?ions?\b|\s*(?:(?:was|were)\s+added\s+)?to\s+neutrali",
                         rest, re.IGNORECASE)
                or (re.search(r"\b(?:na|k)\s*[+\u207a]\s*(?:/|,|and)?\s*$", before, re.IGNORECASE)
                    and re.match(r"\s*(?:\)|$)", rest))
                or re.search(r"neutrali[sz]ed\s+(?:by|with)\s*$", before, re.IGNORECASE))


#: Acyl chains by the letter a lipid's abbreviation gives them.
_ACYL = {"palmitoyl": "P", "oleoyl": "O", "myristoyl": "M", "lauroyl": "L", "stearoyl": "S",
         "arachidonoyl": "A", "erucoyl": "E", "palmitoleoyl": "Y", "linoleoyl": "Li",
         "phytanoyl": "Ph", "docosahexaenoyl": "DH"}
_ACYL_WORDS = re.compile(r"(di)?(" + "|".join(sorted(_ACYL, key=len, reverse=True)) + r")",
                         re.IGNORECASE)
#: Words that may stand between a lipid's abbreviation and its class written
#: out without naming another lipid.
_PLAIN_LIPID_WORDS = {"", "a", "an", "the", "sn", "glycero", "lipid", "lipids", "bilayer",
                      "membrane", "zwitterionic", "l", "d", "1", "2", "3"}


def _chains_of(name: str) -> str:
    """The acyl letters of an abbreviation: "POPC" gives "PO", "DPPC" "PP"."""
    head = name[:-2]
    return head[1:] * 2 if head.startswith("D") and len(head) <= 3 else head


def _same_lipid(words: str, name: str) -> bool:
    """Whether ``words`` (between a lipid's abbreviation and its class
    written out) say nothing beyond the lipid ``name``: no share, no other
    word, and acyl chains, where given, its own."""
    chains = ""
    for acyl in _ACYL_WORDS.finditer(words):
        letter = _ACYL[acyl.group(2).lower()]
        chains += letter * (2 if acyl.group(1) else 1)
    stripped = _ACYL_WORDS.sub(" ", words)
    tokens = set(re.split(r"[\s,()\-:'′]+", stripped.lower()))
    if not tokens <= _PLAIN_LIPID_WORDS:
        return False
    return not chains or chains.upper() == _chains_of(name).upper()


def _names_the_lipid(said: str, match: re.Match[str], names: set[str]) -> bool:
    """Whether the class written out at ``match`` ("phosphatidylcholine",
    "glycero-3-phosphocholine") is the lipid read itself, beside its own
    abbreviation and nothing else: "POPC (phosphatidylcholine)", "POPC
    (1-palmitoyl-2-oleoyl-sn-glycero-3-phosphocholine)", "phosphatidylcholine
    (POPC)", "POPC, a zwitterionic phosphatidylcholine", or its headgroups."""
    word_end = match.end() + re.match(r"\w*", said[match.end():]).end()
    after = said[word_end:word_end + 30]
    if re.match(r"[-\s]*head[-\s]*groups?\b", after, re.IGNORECASE):
        return True
    prefix = re.search(r"[\w,'′-]*$", said[max(0, match.start() - 80):match.start()]).group(0)
    for name in names:
        if re.match(r"\s*\(\s*" + re.escape(name) + r"\s*\)", after) \
                and _same_lipid(prefix, name):
            return True
        before = said[max(0, match.start() - 90):match.start()]
        found = list(re.finditer(r"\b" + re.escape(name) + r"\b", before))
        if found and _same_lipid(before[found[-1].end():], name) \
                and re.match(r"\s*(?:[),.;]|$|\s+(?:bilayer|membrane|lipids?)\b)", after):
            return True
    return False


#: Words a description of a one-lipid bilayer may hold besides the lipid.
_PLAIN_BILAYER = set("""
a an the of with and in on at for by per each x to as from into its
bilayer bilayers membrane membranes lipid lipids pure single component symmetric symmetrical
hydrated fully solvated leaflet leaflets both total
molecules molecule water waters tip3p tip4p tip4p-ew spc spc/e opc ions ion nacl kcl mm m
mol mmol na k cl chloride sodium potassium counterions counterion counter neutralizing
neutralising neutralize neutralise neutralized neutralised neutral salt physiological
concentration built using generated prepared constructed charmm gui charmm-gui builder
insane packmol area apl nm a2 nm2 patch size square rectangular box dimensions dimension
containing comprising composed consisting made zwitterionic model slab planar flat
lamellar liquid disordered phase fluid ld temperature k approximately about around
protein peptide embedded inserted oriented placed orientation opm ppm centered centred
sn glycero phospho phosphatidyl lipid21 lipid17 charmm36 slipids amber ew
headgroup headgroups head group groups were was added add buffer hepes tris mops
""".split())

#: A class written out, by the headgroup letter of a lipid's abbreviation.
_CLASS_WORDS = {"C": r"phosphatidyl[-\s]?choline|phospho[-\s]?choline|pc",
                "E": r"phosphatidyl[-\s]?ethanolamine|phospho[-\s]?ethanolamine|pe",
                "G": r"phosphatidyl[-\s]?glycerol|phospho[-\s]?glycerol|pg",
                "S": r"phosphatidyl[-\s]?serine|phospho[-\s]?(?:l-)?serine|ps",
                "A": r"phosphatidic\s+acid|phosphate|pa"}


#: Water and ions after a count, with what may follow them ("6000 waters",
#: "20 Na+ ions", "40 waters per lipid").
_SALT_WORDS = (r"(?:waters?|(?:tip3p|tip4p|spc|opc)\s+waters?|ions?|counterions?|nacl|na\+?"
               r"|cl-?\s*ions?|kcl|sodium|potassium|chloride)"
               r"(?:\s+(?:ions?|molecules|per\s+lipid))?")


#: A word's dash before a number ("leaflet-60"), but not one of the fixed
#: versions of a
#: force field, water model, tool or a lipid's chemical name ("CHARMM-36",
#: "TIP-3P", "glycero-3").
_WORD_DASH = re.compile(
    r"\b(?!(?:charmm-(?:19|22|27|36)|lipid-(?:11|14|17|21)|amber-(?:9[4-9]|0\d|1\d|2\d)"
    r"|tip-[345]p?|tip[345]p-(?:2005|2018)|gaff-[12]|slipids-20[0-2]\d|opls-20[0-2]\d|opc-3"
    r"|martini-[23]|ff-(?:9\d|0\d|1\d)|gromos-(?:43|45|53|54)|sn-[123]|glycero-3"
    r"|(?:gui|packmol|insane|opm|ppm)-[1-3])(?!\d))([^\W\d_][^\W_]*)-(?=\d)")


def _unexplained(said: str, lipid: str) -> list[str]:
    """The words of a membrane's description that say more than a bilayer
    of ``lipid``: what is not the lipid, its own name written out, numbers
    of lipids, ions, water or the box. A share other than 100% is one."""
    text = re.sub(r"-{2,}", "-", _plain_text(said, join=False).lower())
    chains = "".join(_ACYL[acyl.group(2).lower()] * (2 if acyl.group(1) else 1)
                     for acyl in _ACYL_WORDS.finditer(text))
    if chains and chains.upper() != _chains_of(lipid).upper():
        return [acyl.group(0) for acyl in _ACYL_WORDS.finditer(text)]
    # A chain's place goes with it ("1-palmitoyl").
    text = re.sub(r"(?:\b[123]-)?(?:" + _ACYL_WORDS.pattern + ")", " ", text)
    # A count after a word's dash is a count ("per leaflet-60", "POPC-60"), and
    # so is one before "-lipid" ("128-lipid").
    text = re.sub(r"\b(\d+)-(?=lipids?\b)", r"\1 ", _WORD_DASH.sub(r"\1 ", text))
    name = r"\b" + re.escape(lipid.lower()) + r"s?\b"
    named = re.sub(name, " lipids ", text)
    text = re.sub(name, " ", text)
    text = re.sub(r"\b(?:" + _CLASS_WORDS.get(lipid[-1], "(?!)") + r")\b", " ", text)
    unit = r"(?! ?(?:mm|um|nm|ns|ps|bar|atm|m(?= (?:nacl|kcl|salt)\b))\b)"
    left = []
    # A share written as a fraction or a ratio ("70/30", "3 to 1", "0.7", "7:3").
    left += re.findall(r"(?<![\d.])\d+(?:\.\d+)?\s*[/:]\s*\d+|\b\d+\s+to\s+\d+\b|\u2030", text)
    left += re.findall(r"(?<![\d.])0?\.\d+(?![\d])(?!\s*(?:m|mm|nm|ns|k)\b)", text)
    # "96,32" or "70-30", but not a decimal comma or a range with its unit ("0,15 M", "1-1.5 nm").
    left += re.findall(r"(?<![\d.,])\d+,\d+(?:\.\d+)?(?!\.?\d)" + unit
                       + r"|(?<![\d.])\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?(?!\.?\d)" + unit, text)
    # Two counts not of a unit ("96 POPC and 32", "(96, 32)", "2 x 64 and 2 x 16"): more
    # than one lipid. "2 x 64" is 64 in each leaflet; a count beside a
    # per-leaflet count must be it or twice it, and a "2 x" count must be it.
    per_two = (r"(?<![\w.-])(?<![x\u00d7*]\s)2\s*[x\u00d7*]\s*(?=([1-9]\d{0,4})(?!\d)(?![.,]\d)"
               r"(?!\s*[x\u00d7*])(?!\s*(?:mm|m|mol|k|nm|nm2|a2|a|ns|ps|us|fs|bar|atm|deg|c)\b))")
    doubled = set(re.findall(per_two, text))
    text = re.sub(per_two, " ", text)
    # The pressure this software keeps, in a few fixed phrases ("at 1 bar",
    # "pressure of 1 atm"); any other pressure is still read.
    text = re.sub(r"(?:\bpressure\s+of|\bat)\s+1(?:(?:\.0+)?\s*(?:bar|atm)|\.01325\s*bar)\b"
                  r"(?:\s+pressure\b)?", " ", text)
    # A decimal not of a unit, nor a box's side, nor a pH ("70.5 and 29.5").
    left += re.findall(r"(?<![\d.])(?<![x\u00d7*])(?<![x\u00d7*]\s)(?<!ph)(?<!ph\s)"
                       r"\d+\.\d+(?!\.?\d)(?!\s*(?:[x\u00d7*%]|(?:mm|m|k|nm|nm2|a2|a|ns|ps|us|fs"
                       r"|bar|atm|deg|c|g|mg|ml|l|waters?|ions?)\b))", text)
    # Counts of water and ions are not of lipids, said as a whole phrase
    # ("6000 waters,", "20 Na+ ions and"); a water model's name is a lipid's
    # too ("SPC"), so only before "water" is it water. A count of lipids per
    # leaflet is the per-leaflet count, unless it ends a list ("64 and 64
    # lipids per leaflet"). A dash before a count not glued to a word is no
    # range ("64 per leaflet -60").
    counts = [count.group(0) for count in re.finditer(
        r"(?<![\w.])(?<![\w.]-)\d+(?![\w%])(?!-\w)(?![.,]\d)"
        r"(?!-?\s*(?:mm|m|k|nm|nm2|a2|a|ns|ps|us|fs|bar|atm|deg|c|per\s+leaflet"
        r"|in\s+each\s+leaflet|/\s*leaflet)(?![\w-])"
        r"|\s*" + _SALT_WORDS + r"\s*(?=[,.;:)]|and\b|$))", text)
        if not re.match(r"-?\s*lipids(?:\s+(?:per|in\s+each)\s+|\s*/\s*)leaflet",
                         text[count.end():])
        or re.search(r"\d(?:,|\s*\b(?:and|or))?\s*$", text[:count.start()])]
    # The same word before anything else ("32 Na+ lipids", "32 sodium POPC") is
    # no such phrase.
    counts += [salt.group(1) for salt in re.finditer(r"(?<![\w.-])(\d+)\s*" + _SALT_WORDS, named)
               if not re.match(r"\s*(?:[,.;:)]|and\b|$)", named[salt.end():])]
    per_leaflet = set(re.findall(r"(?<![\w.])(?<![\w.]-)(\d+)\s*-?\s*(?:lipids\s*)?"
                                 r"(?:(?:per|in\s+each)\s+|/\s*)leaflet", text))
    # Twice the per-leaflet count only where it is said to be the total.
    totals = set(re.findall(r"(?<![\w.-])(\d+)-?\s+(?:lipids?|(?:lipid\s+)?molecules|in\s+total"
                            r"|total)\b|\b(?:total(?:\s+of|:)?|lipids)\s+(\d+)\b", named))
    totals = {count for pair in totals for count in pair if count}
    leaf = min(per_leaflet, default="")
    twice = str(2 * int(leaf)) if 0 < len(leaf) < 7 else ""
    apart = bool(leaf) and any(
        count not in ((leaf, twice) if count in totals and count not in doubled else (leaf,))
        for count in counts)
    # Several bilayers ("bilayers of 128 lipids each") are not one.
    several = bool(re.search(r"\b(?:bilayers|membranes)\b", text)) and bool(counts or doubled)
    if len(counts) > 1 or len(per_leaflet) > 1 or apart or several:
        left += counts + sorted(per_leaflet | doubled)
    for token in re.findall(r"(?<![\d.])\d+(?:\.\d+)?\s*(?:mol\s*|w/w\s*|wt\s*)?%"
                            r"|[^\W\d_][^\W_]*", text):
        if token.endswith("%"):
            if float(re.match(r"[\d.]+", token).group(0)) != 100:
                left.append(token)
        elif not token.isascii() or (token not in _PLAIN_BILAYER
                                     and not re.fullmatch(r"[0-9]+", token)):
            left.append(token)
    return left


def _other_lipids(said: str, names: set[str]) -> set[str]:
    """Lipids beside those read by the phospholipid pattern (``names``):
    named by an abbreviation it does not read, written out, or as a sterol,
    an extract or a detergent; never one said to be absent, chloride ("CL-
    ions"), or the lipid read written out beside its abbreviation."""
    classes = {f"P{name[-1]}" for name in names if re.fullmatch(r"[DP][OPMLSAEY]P[CEGSA]", name)}
    found = set()
    for match in re.finditer(r"\b[A-Z][A-Za-z0-9]{1,4}\b", said):
        token = match.group(0)
        another = (re.fullmatch(r"(?:D|[POMLSAEY])(?:[POMLSAEYD]|Li|Ph|DH|H)P[CEGSAI]", token)
                   and token not in names and token not in _BUFFERS
                   and not re.match(r"\s+(?:receptors?|channels?|proteins?|transporters?"
                                    r"|kinases?|domains?)\b", said[match.end():], re.IGNORECASE))
        if (token not in _OTHER_LIPIDS and not another) or _lipid_absent(said, match):
            continue
        if token == "CL" and _chloride(said, match):
            continue
        if token in classes and re.match(r"[-\s]*head[-\s]*groups?\b", said[match.end():],
                                         re.IGNORECASE):
            continue
        found.add(token)
    heads = {"choline": "PC", "ethanolamine": "PE", "glycerol": "PG", "serine": "PS",
             "inositol": "PI"}
    for match in re.finditer(
            r"phosphatid(?:yl[-\s]?(?P<head>choline|ethanolamine|glycerol|serine|inositol)"
            r"|(?P<acid>ic\s+acid))"
            r"|(?:glycero[-\s]?3[-\s]?)?phospho[-\s]?(?:[ld][-\s])?(?P<head2>choline"
            r"|ethanolamine|glycerol|serine|inositol)\b"
            r"|\bptdins|ergosterol|lanosterol|desmosterol|ceramide|\blyso|plasmalogen"
            r"|\b(?:phyto|sito|stigma)?sterols?\b|hopanoid|diacylglycerol|glycolipid"
            r"|lipopolysaccharide|\blipid\s+a\b|lipid\s+extract|polar\s+lipid|fatty\s+acids?"
            r"|\b(?:oleic|palmitic|stearic|linoleic|myristic|arachidonic)\s+acid|\bpeg(?:ylated)?\b"
            r"|triolein|triglyceride|sulfatide|\bgalcer\b|\bbmp\b|bis\(monoacylglycero\)"
            r"|detergents?\b|micelles?\b|\bddm\b|\bdpc\b|\bdotap\b|\bdotma\b|\bddab\b"
            r"|asolectin|total\s+lipids?|triton|octyl[-\s]?glucoside|squalene|tocopherol"
            r"|\blipid\s+(?:ii|iv)\b|\bnta\b|\b(?:binary|ternary|quaternary|two-component"
            r"|three-component)\b|\bof\s+the\s+lipid\b"

            r"|\b(?:anionic|cationic|charged|saturated|unsaturated|bacterial|pufa|other|acidic"
            r"|neutral|minor|ethanolamine|serine)\s+"
            r"(?:phospho)?lipids?\b|\b(?:mostly|mainly|predominantly|primarily|largely)\s+"
            r"(?:of\s+)?(?:(?-i:[A-Z]{2,5}\d?)\b|lipids?|phospho)|\b(?-i:[A-Z]{2,5}\d?)-rich\b"
            r"|\brich\s+in\s+(?:(?-i:[A-Z]{2,5})|lipid|chol|sterol)|\b(?:egg|soy|brain|liver|heart)(?:\s*-\s*|\s*)p[cegsi]\b",
            said, re.IGNORECASE):
        head = (heads.get((match.group("head") or match.group("head2") or "").lower())
                or ("PA" if match.group("acid") else match.group(0).upper()))
        if head in classes and _names_the_lipid(said, match, names):
            continue
        if not _lipid_absent(said, match):
            found.add(head)
    return found


#: Buffers whose names read like a phospholipid's.
_BUFFERS = {"MOPS", "TAPS", "CAPS", "MOPSO", "TAPSO", "CAPSO"}

#: Lipids named by an abbreviation the phospholipid pattern does not read,
#: each making a bilayer a mixture.
_OTHER_LIPIDS = {"PIP", "PIP2", "PIP3", "PI4P", "PI", "SM", "SSM", "PSM", "CHL1", "CHL", "CL",
                 "TOCL", "TOCL1", "TOCL2", "TMCL", "TMCL1", "TMCL2", "LPE", "LPG", "BMP",
                 "CHS", "LPA", "LPI", "Cer", "Gb3", "S1P", "DOGS", "LDAO", "SDS", "DHA", "DOG",
                 "CL1", "CL2", "GM1", "GM3", "LPS", "DAG", "CER", "ERG", "PC", "PE", "PG",
                 "PS", "PA", "LPC"}


def _ligands_of(record: dict[str, Any] | None) -> list[str]:
    """The small molecules among what a study's ligands name: ions, water
    and "none" set aside, which are no ligand to parameterise."""
    if not record:
        return []
    return [str(item).strip() for item in _as_list(record.get("value"))
            if str(item).strip() and not _NOT_A_LIGAND.search(str(item))]


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return [value] if value not in (None, "") else []
